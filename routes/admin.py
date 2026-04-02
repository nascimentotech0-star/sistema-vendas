from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from models import (db, User, Attendance, AttendanceBreak, OvertimeRequest,
                    Client, Sale, Renewal, PAYMENT_METHODS, DAYS_AT_RISK,
                    AttendantGoal, CommissionPayment, PriceItem,
                    AbsenceRecord, SalaryPayment, AuditLog)
from audit import log_action
from notify import notify, notify_admins
from datetime import datetime, date, timedelta
from utils import now_br, today_br
from sqlalchemy import func
import calendar as cal

admin_bp = Blueprint('admin', __name__)


def admin_required(f):
    """Acesso exclusivo para admin (gerenciar usuários, etc.)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin():
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def manager_or_admin(f):
    """Acesso para admin e gerente."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_access_admin():
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


@admin_bp.context_processor
def inject_pending_counts():
    overtime_count = OvertimeRequest.query.filter_by(status='pending').count()
    renewals_count = Renewal.query.filter(
        Renewal.status == 'pending',
        Renewal.due_date <= today_br()
    ).count()
    at_risk_count = sum(1 for c in Client.query.all() if c.is_at_risk)
    return dict(pending_overtime_count=overtime_count,
                pending_renewals_count=renewals_count,
                at_risk_clients_count=at_risk_count)


# ── Dashboard ──────────────────────────────────────────────────────────────────

@admin_bp.route('/')
@login_required
@manager_or_admin
def dashboard():
    today = today_br()
    day_start = datetime(today.year, today.month, today.day)
    day_end   = day_start + timedelta(days=1)

    today_sales = Sale.query.filter(
        Sale.created_at >= day_start,
        Sale.created_at < day_end,
    ).all()
    today_total = sum(s.amount for s in today_sales)
    today_commissions = sum(s.commission_amount for s in today_sales)

    payment_totals = {}
    for s in today_sales:
        payment_totals[s.payment_method] = payment_totals.get(s.payment_method, 0) + s.amount

    active_attendances = Attendance.query.filter(
        Attendance.check_in >= day_start,
        Attendance.check_in < day_end,
        Attendance.check_out == None
    ).all()

    pending_overtime = OvertimeRequest.query.filter_by(status='pending').count()

    total_attendants = User.query.filter(
        User.role.in_(['attendant', 'gerente']), User.is_active == True
    ).count()

    recent_sales = Sale.query.order_by(Sale.created_at.desc()).limit(10).all()

    # ── Gráfico: vendas por dia da semana (últimas 4 semanas) ──
    week_start = datetime(today.year, today.month, today.day) - timedelta(days=27)
    sales_4w = Sale.query.filter(Sale.created_at >= week_start).all()
    day_names = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    day_totals = [0.0] * 7
    for s in sales_4w:
        day_totals[s.created_at.weekday()] += s.amount
    chart_weekday = {'labels': day_names, 'data': [round(v, 2) for v in day_totals]}

    # ── Gráfico: vendas por semana (últimas 8 semanas) ──
    week8_start = datetime(today.year, today.month, today.day) - timedelta(weeks=8)
    sales_8w = Sale.query.filter(Sale.created_at >= week8_start).all()
    week_map = {}
    for s in sales_8w:
        d = s.created_at.date()
        week_num = d.isocalendar()[1]
        year = d.isocalendar()[0]
        key = f'{year}-S{week_num:02d}'
        week_map[key] = week_map.get(key, 0) + s.amount
    week_keys = sorted(week_map.keys())
    chart_weekly = {'labels': week_keys, 'data': [round(week_map[k], 2) for k in week_keys]}

    # ── Gráfico: vendas por mês (últimos 12 meses) ──
    month12_start = date(today.year - 1 if today.month == 1 else today.year,
                         1 if today.month == 1 else today.month, 1)
    sales_12m = Sale.query.filter(Sale.created_at >= datetime(month12_start.year, month12_start.month, month12_start.day)).all()
    month_map = {}
    month_labels_pt = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    for s in sales_12m:
        key = s.created_at.strftime('%Y-%m')
        month_map[key] = month_map.get(key, 0) + s.amount
    month_keys = sorted(month_map.keys())
    chart_monthly = {
        'labels': [f"{month_labels_pt[int(k.split('-')[1])-1]}/{k.split('-')[0][2:]}" for k in month_keys],
        'data': [round(month_map[k], 2) for k in month_keys]
    }

    # Renovações do mês
    first_day_month = date(today.year, today.month, 1)
    last_day_month  = date(today.year, today.month, cal.monthrange(today.year, today.month)[1])
    month_renewals  = Renewal.query.filter(
        Renewal.due_date >= first_day_month,
        Renewal.due_date <= last_day_month
    ).all()
    renewals_total   = len(month_renewals)
    renewals_done    = sum(1 for r in month_renewals if r.status == 'renewed')
    renewals_pending = sum(1 for r in month_renewals if r.status == 'pending')
    renewals_overdue = [r for r in month_renewals if r.is_overdue]

    # Renovações vencendo nos próximos 3 dias (pendentes, não vencidas)
    in_3_days = today + timedelta(days=3)
    renewals_expiring_soon = Renewal.query.filter(
        Renewal.status == 'pending',
        Renewal.due_date >= today,
        Renewal.due_date <= in_3_days,
    ).order_by(Renewal.due_date).all()

    # ── Indicadores de clientes ───────────────────────────────────────────────
    from models import Client, ClientContact
    all_clients      = Client.query.all()
    total_clients    = len(all_clients)
    at_risk_list     = [c for c in all_clients if c.is_at_risk]
    at_risk_count    = len(at_risk_list)

    # Contatos registrados hoje
    contacts_today = ClientContact.query.filter(
        ClientContact.contacted_at >= day_start,
        ClientContact.contacted_at < day_end,
    ).count()

    # Total recebido no mês (vendas)
    month_start_dt = datetime(first_day_month.year, first_day_month.month, first_day_month.day)
    month_end_dt   = datetime(last_day_month.year, last_day_month.month, last_day_month.day) + timedelta(days=1)
    month_sales = Sale.query.filter(
        Sale.created_at >= month_start_dt,
        Sale.created_at < month_end_dt,
    ).all()
    month_total      = round(sum(s.amount for s in month_sales), 2)
    month_commission = round(sum(s.commission_amount for s in month_sales), 2)
    month_sales_count = len(month_sales)

    # Ranking de atendentes do mês
    from collections import defaultdict
    att_month: dict = defaultdict(lambda: {'name':'', 'total':0.0, 'count':0})
    for s in month_sales:
        att_month[s.attendant_id]['name']  = s.attendant.name
        att_month[s.attendant_id]['total'] += s.amount
        att_month[s.attendant_id]['count'] += 1
    ranking = sorted(att_month.values(), key=lambda x: x['total'], reverse=True)[:5]

    return render_template('admin/dashboard.html',
        today=today,
        today_total=today_total,
        today_commissions=today_commissions,
        today_sales_count=len(today_sales),
        payment_totals=payment_totals,
        active_attendances=active_attendances,
        pending_overtime=pending_overtime,
        total_attendants=total_attendants,
        recent_sales=recent_sales,
        payment_methods=PAYMENT_METHODS,
        chart_weekday=chart_weekday,
        chart_weekly=chart_weekly,
        chart_monthly=chart_monthly,
        renewals_total=renewals_total,
        renewals_done=renewals_done,
        renewals_pending=renewals_pending,
        renewals_overdue=renewals_overdue,
        # novos indicadores
        total_clients=total_clients,
        at_risk_count=at_risk_count,
        at_risk_list=at_risk_list,
        contacts_today=contacts_today,
        month_total=month_total,
        month_commission=month_commission,
        month_sales_count=month_sales_count,
        ranking=ranking,
        renewals_expiring_soon=renewals_expiring_soon,
        manager_attendance=current_user.active_attendance if current_user.is_manager() else None,
    )


# ── Atendentes ─────────────────────────────────────────────────────────────────

@admin_bp.route('/atendentes')
@login_required
@manager_or_admin
def attendants():
    attendants = User.query.filter(User.role.in_(['attendant', 'gerente'])).order_by(User.name).all()
    return render_template('admin/attendants.html', attendants=attendants)


@admin_bp.route('/atendentes/novo', methods=['GET', 'POST'])
@login_required
@admin_required
def new_attendant():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip() or None
        password = request.form.get('password', '')

        if not username or not name or not password:
            flash('Preencha todos os campos obrigatórios.', 'danger')
            return render_template('admin/attendant_form.html', attendant=None)

        if User.query.filter_by(username=username).first():
            flash('Nome de usuário já existe.', 'danger')
            return render_template('admin/attendant_form.html', attendant=None)

        role = request.form.get('role', 'attendant')
        if role not in ('attendant', 'financial', 'gerente'):
            role = 'attendant'
        salary       = float(request.form.get('monthly_salary', 0) or 0)
        hours        = int(request.form.get('work_hours_per_day', 8) or 8)
        days         = int(request.form.get('work_days_per_month', 26) or 26)
        shift_end    = int(request.form.get('shift_end_hour', 22) or 22)
        sales_target = int(request.form.get('monthly_sales_target', 700) or 700)
        user = User(username=username, name=name, email=email, role=role,
                    monthly_salary=salary, work_hours_per_day=hours,
                    work_days_per_month=days, shift_end_hour=shift_end,
                    monthly_sales_target=sales_target)
        user.set_password(password)
        db.session.add(user)
        db.session.flush()
        log_action('user_create', f'Novo usuário criado: {name} ({role})', 'User', user.id)
        db.session.commit()
        flash(f'Usuário {name} cadastrado com sucesso!', 'success')
        return redirect(url_for('admin.attendants'))

    return render_template('admin/attendant_form.html', attendant=None)


@admin_bp.route('/atendentes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_attendant(id):
    attendant = User.query.get_or_404(id)
    if request.method == 'POST':
        attendant.name = request.form.get('name', '').strip()
        attendant.email = request.form.get('email', '').strip() or None
        attendant.is_active = 'is_active' in request.form
        new_role = request.form.get('role', attendant.role)
        if new_role in ('attendant', 'financial', 'gerente'):
            attendant.role = new_role
        attendant.monthly_salary       = float(request.form.get('monthly_salary', 0) or 0)
        attendant.work_hours_per_day   = int(request.form.get('work_hours_per_day', 8) or 8)
        attendant.work_days_per_month  = int(request.form.get('work_days_per_month', 26) or 26)
        attendant.shift_end_hour       = int(request.form.get('shift_end_hour', 22) or 22)
        attendant.monthly_sales_target = int(request.form.get('monthly_sales_target', 700) or 700)
        new_password = request.form.get('password', '').strip()
        if new_password:
            attendant.set_password(new_password)
        pwd_note = ' (senha alterada)' if new_password else ''
        log_action('user_edit', f'Usuário editado: {attendant.name}{pwd_note}', 'User', attendant.id)
        db.session.commit()
        flash(f'Atendente {attendant.name} atualizado!', 'success')
        return redirect(url_for('admin.attendants'))
    return render_template('admin/attendant_form.html', attendant=attendant)


@admin_bp.route('/atendentes/<int:id>/toggle', methods=['POST'])
@login_required
@admin_required
def toggle_attendant(id):
    attendant = User.query.get_or_404(id)
    attendant.is_active = not attendant.is_active
    status = 'ativado' if attendant.is_active else 'desativado'
    log_action('user_toggle', f'Usuário {status}: {attendant.name}', 'User', attendant.id)
    db.session.commit()
    flash(f'Atendente {attendant.name} {status}.', 'success')
    return redirect(url_for('admin.attendants'))


@admin_bp.route('/atendentes/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def delete_attendant(id):
    attendant = User.query.get_or_404(id)
    if attendant.id == current_user.id:
        flash('Você não pode excluir sua própria conta.', 'danger')
        return redirect(url_for('admin.attendants'))

    name = attendant.name
    from models import (Client, ClientContact, Message,
                        CommissionPayment, SalaryPayment,
                        AbsenceRecord, AttendantGoal)

    # IDs de clientes cadastrados por este atendente
    client_ids = [c.id for c in Client.query.filter_by(registered_by=id).all()]

    # 1. ClientContacts: pelo atendente OU pelos clientes dele
    cc_filter = ClientContact.attendant_id == id
    if client_ids:
        cc_filter = db.or_(cc_filter, ClientContact.client_id.in_(client_ids))
    ClientContact.query.filter(cc_filter).delete(synchronize_session=False)

    # 2. Renovações: pelo atendente OU pelos clientes dele
    ren_filter = Renewal.attendant_id == id
    if client_ids:
        ren_filter = db.or_(ren_filter, Renewal.client_id.in_(client_ids))
    Renewal.query.filter(ren_filter).delete(synchronize_session=False)

    # 3. Vendas: pelo atendente OU pelos clientes dele
    sale_filter = Sale.attendant_id == id
    if client_ids:
        sale_filter = db.or_(sale_filter, Sale.client_id.in_(client_ids))
    Sale.query.filter(sale_filter).delete(synchronize_session=False)

    # 4. Clientes cadastrados por ele
    Client.query.filter_by(registered_by=id).delete(synchronize_session=False)

    # 5. Mensagens do chat (sender ou attendant)
    Message.query.filter(
        db.or_(Message.sender_id == id, Message.attendant_id == id)
    ).delete(synchronize_session=False)

    # 6. Pagamentos onde é o atendente ou quem pagou
    CommissionPayment.query.filter(
        db.or_(CommissionPayment.attendant_id == id,
               CommissionPayment.paid_by == id)
    ).delete(synchronize_session=False)
    SalaryPayment.query.filter(
        db.or_(SalaryPayment.attendant_id == id,
               SalaryPayment.paid_by == id)
    ).delete(synchronize_session=False)

    # 7. Ponto (breaks primeiro)
    for att in Attendance.query.filter_by(user_id=id).all():
        for b in att.breaks:
            db.session.delete(b)
        db.session.delete(att)

    # 8. Demais registros do usuário
    for model in [OvertimeRequest, AbsenceRecord, AttendantGoal]:
        model.query.filter_by(user_id=id).delete(synchronize_session=False)

    # 9. Faltas criadas por ele (created_by)
    AbsenceRecord.query.filter_by(created_by=id).update(
        {'created_by': None}, synchronize_session=False)

    log_action('user_delete', f'Usuário excluído permanentemente: {name}', 'User', id)
    db.session.delete(attendant)
    db.session.commit()
    flash(f'Usuário {name} excluído com sucesso.', 'success')
    return redirect(url_for('admin.attendants'))


@admin_bp.route('/atendentes/<int:id>/vendas')
@login_required
@manager_or_admin
def attendant_sales(id):
    attendant = User.query.get_or_404(id)
    date_str = request.args.get('date', '')
    query = Sale.query.filter_by(attendant_id=id)
    if date_str:
        try:
            fdate = datetime.strptime(date_str, '%Y-%m-%d').date()
            query = query.filter(func.date(Sale.created_at) == fdate)
        except Exception:
            pass
    sales = query.order_by(Sale.created_at.desc()).all()
    total = sum(s.amount for s in sales)
    commission = sum(s.commission_amount for s in sales)
    return render_template('admin/attendant_sales.html',
        attendant=attendant, sales=sales, total=total,
        commission=commission, date_str=date_str)


# ── Hora Extra ─────────────────────────────────────────────────────────────────

@admin_bp.route('/hora-extra')
@login_required
@manager_or_admin
def overtime_requests():
    pending = OvertimeRequest.query.filter_by(status='pending').order_by(OvertimeRequest.requested_at.desc()).all()
    history = OvertimeRequest.query.filter(
        OvertimeRequest.status != 'pending'
    ).order_by(OvertimeRequest.requested_at.desc()).limit(50).all()
    return render_template('admin/overtime_requests.html', pending=pending, history=history)


@admin_bp.route('/hora-extra/<int:id>/aprovar', methods=['POST'])
@login_required
@manager_or_admin
def approve_overtime(id):
    req = OvertimeRequest.query.get_or_404(id)
    req.status = 'approved'
    req.approved_by = current_user.id
    req.approved_at = now_br()
    log_action('overtime_approve', f'Hora extra aprovada para {req.user.name}', 'OvertimeRequest', req.id)
    notify(req.user_id, 'Hora extra aprovada!',
           f'Sua solicitação de hora extra foi aprovada por {current_user.name}.',
           link=url_for('attendant.dashboard'),
           icon='bi-check-circle-fill', color='#6ee7b7')
    db.session.commit()
    flash(f'Solicitação de {req.user.name} aprovada!', 'success')
    return redirect(url_for('admin.overtime_requests'))


@admin_bp.route('/hora-extra/<int:id>/negar', methods=['POST'])
@login_required
@manager_or_admin
def deny_overtime(id):
    req = OvertimeRequest.query.get_or_404(id)
    req.status = 'denied'
    req.approved_by = current_user.id
    req.approved_at = now_br()
    log_action('overtime_deny', f'Hora extra negada para {req.user.name}', 'OvertimeRequest', req.id)
    notify(req.user_id, 'Hora extra negada',
           f'Sua solicitação de hora extra foi negada por {current_user.name}.',
           link=url_for('attendant.dashboard'),
           icon='bi-x-circle-fill', color='#fca5a5')
    db.session.commit()
    flash(f'Solicitação de {req.user.name} negada.', 'warning')
    return redirect(url_for('admin.overtime_requests'))


# ── Vendas ─────────────────────────────────────────────────────────────────────

@admin_bp.route('/vendas/nova', methods=['POST'])
@login_required
@manager_or_admin
def admin_new_sale():
    from models import Client
    attendant_id   = request.form.get('attendant_id', type=int)
    client_id      = request.form.get('client_id', type=int) or None
    amount_str     = request.form.get('amount', '0').replace(',', '.')
    adjustment_str = request.form.get('adjustment', '0').replace(',', '.')
    payment_method = request.form.get('payment_method', '')
    description    = request.form.get('description', '').strip() or None
    commission_rate= float(request.form.get('commission_rate', 5) or 5)
    date_str       = request.form.get('sale_date', '')
    screens        = int(request.form.get('screens', 1) or 1)

    try:
        base   = float(amount_str)
        adj    = float(adjustment_str)
        amount = round(base + adj, 2)
        if amount <= 0 or not payment_method or not attendant_id:
            flash('Preencha atendente, valor e forma de pagamento.', 'danger')
            return redirect(url_for('admin.sales'))
    except ValueError:
        flash('Valor inválido.', 'danger')
        return redirect(url_for('admin.sales'))

    commission_amount = round(amount * commission_rate / 100, 2)

    created_at = now_br()
    if date_str:
        try:
            created_at = datetime.strptime(date_str, '%Y-%m-%dT%H:%M')
        except Exception:
            try:
                created_at = datetime.strptime(date_str, '%Y-%m-%d')
            except Exception:
                pass

    sale = Sale(
        attendant_id=attendant_id,
        client_id=client_id,
        amount=amount,
        adjustment=adj,
        payment_method=payment_method,
        commission_rate=commission_rate,
        commission_amount=commission_amount,
        description=description,
        screens=screens,
        is_overtime=False,
        created_at=created_at,
    )
    db.session.add(sale)
    db.session.flush()
    log_action('sale_create', f'Venda registrada pelo admin: R$ {amount:.2f} para atendente #{attendant_id}', 'Sale', sale.id)
    db.session.commit()
    flash(f'Venda de R$ {amount:.2f} registrada. Comissão: R$ {commission_amount:.2f}', 'success')
    return redirect(url_for('admin.sales'))


@admin_bp.route('/vendas/<int:sale_id>/excluir', methods=['POST'])
@login_required
@admin_required
def admin_delete_sale(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    log_action('sale_delete', f'Venda excluída: R$ {sale.amount:.2f} de {sale.attendant.name} em {sale.created_at.strftime("%d/%m/%Y")}', 'Sale', sale_id)
    db.session.delete(sale)
    db.session.commit()
    flash('Venda excluída.', 'success')
    return redirect(url_for('admin.sales'))


@admin_bp.route('/vendas')
@login_required
@manager_or_admin
def sales():
    page = request.args.get('page', 1, type=int)
    date_filter = request.args.get('date', '')
    attendant_filter = request.args.get('attendant', 0, type=int)
    payment_filter = request.args.get('payment', '')

    query = Sale.query

    if date_filter:
        try:
            fdate = datetime.strptime(date_filter, '%Y-%m-%d').date()
            query = query.filter(func.date(Sale.created_at) == fdate)
        except Exception:
            pass

    if attendant_filter:
        query = query.filter_by(attendant_id=attendant_filter)

    if payment_filter:
        query = query.filter_by(payment_method=payment_filter)

    sales = query.order_by(Sale.created_at.desc()).paginate(page=page, per_page=25)
    attendants = User.query.filter(User.role.in_(['attendant','gerente'])).order_by(User.name).all()
    from models import Client
    all_clients = Client.query.order_by(Client.name).all()

    return render_template('admin/sales.html',
        sales=sales, attendants=attendants,
        all_clients=all_clients,
        date_filter=date_filter, attendant_filter=attendant_filter,
        payment_filter=payment_filter, payment_methods=PAYMENT_METHODS)


# ── Auditoria ──────────────────────────────────────────────────────────────────

@admin_bp.route('/auditoria')
@login_required
@admin_required
def audit_log():
    page          = request.args.get('page', 1, type=int)
    user_filter   = request.args.get('user', 0, type=int)
    action_filter = request.args.get('action', '')

    query = AuditLog.query
    if user_filter:
        query = query.filter_by(user_id=user_filter)
    if action_filter:
        query = query.filter(AuditLog.action.ilike(f'%{action_filter}%'))

    logs = query.order_by(AuditLog.created_at.desc()).paginate(page=page, per_page=50)
    users = User.query.order_by(User.name).all()
    return render_template('admin/audit_log.html', logs=logs, users=users,
                           user_filter=user_filter, action_filter=action_filter)


# ── Fraudes / Comprovantes Duplicados ──────────────────────────────────────────

@admin_bp.route('/fraudes')
@login_required
@manager_or_admin
def fraud_list():
    """Lista vendas com comprovante duplicado (mesmo hash em mais de uma venda)."""
    # Subquery: hashes que aparecem em mais de uma venda
    dup_hashes = (
        db.session.query(Sale.comprovante_hash)
        .filter(Sale.comprovante_hash.isnot(None))
        .group_by(Sale.comprovante_hash)
        .having(func.count(Sale.id) > 1)
        .subquery()
    )
    fraud_sales = (
        Sale.query
        .filter(Sale.comprovante_hash.in_(db.session.query(dup_hashes)))
        .order_by(Sale.comprovante_hash, Sale.created_at)
        .all()
    )
    # Agrupar por hash para exibir pares/grupos
    groups = {}
    for s in fraud_sales:
        groups.setdefault(s.comprovante_hash, []).append(s)

    return render_template('admin/fraud_list.html', groups=groups)


# ── Caixa ──────────────────────────────────────────────────────────────────────

@admin_bp.route('/caixa')
@login_required
@manager_or_admin
def cash():
    date_str = request.args.get('date', today_br().strftime('%Y-%m-%d'))
    try:
        cash_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        cash_date = today_br()

    sales = Sale.query.filter(func.date(Sale.created_at) == cash_date).all()

    totals = {key: 0.0 for key in PAYMENT_METHODS}
    for s in sales:
        if s.payment_method in totals:
            totals[s.payment_method] += s.amount

    grand_total = sum(totals.values())

    return render_template('admin/cash.html',
        cash_date=cash_date,
        sales=sales,
        totals=totals,
        grand_total=grand_total,
        payment_methods=PAYMENT_METHODS)


# ── Relatórios ─────────────────────────────────────────────────────────────────

@admin_bp.route('/relatorios')
@login_required
@manager_or_admin
def reports():
    date_str = request.args.get('date', today_br().strftime('%Y-%m-%d'))
    try:
        report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        report_date = today_br()

    sales = Sale.query.filter(func.date(Sale.created_at) == report_date).all()

    attendant_stats = {}
    for s in sales:
        aid = s.attendant_id
        if aid not in attendant_stats:
            attendant_stats[aid] = {
                'name': s.attendant.name,
                'count': 0, 'total': 0.0, 'commission': 0.0
            }
        attendant_stats[aid]['count'] += 1
        attendant_stats[aid]['total'] += s.amount
        attendant_stats[aid]['commission'] += s.commission_amount

    payment_stats = {}
    for s in sales:
        pm = s.payment_method
        if pm not in payment_stats:
            payment_stats[pm] = {'count': 0, 'total': 0.0, 'label': PAYMENT_METHODS.get(pm, pm)}
        payment_stats[pm]['count'] += 1
        payment_stats[pm]['total'] += s.amount

    total_sales = sum(s.amount for s in sales)
    total_commission = sum(s.commission_amount for s in sales)

    return render_template('admin/reports.html',
        report_date=report_date,
        sales=sales,
        attendant_stats=attendant_stats,
        payment_stats=payment_stats,
        total_sales=total_sales,
        total_commission=total_commission,
        sales_count=len(sales))


# ── Ponto ──────────────────────────────────────────────────────────────────────

@admin_bp.route('/ponto')
@login_required
@manager_or_admin
def attendance():
    date_str = request.args.get('date', today_br().strftime('%Y-%m-%d'))
    attendant_filter = request.args.get('attendant', 0, type=int)
    try:
        att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        att_date = today_br()

    query = Attendance.query.filter(func.date(Attendance.check_in) == att_date)
    if attendant_filter:
        query = query.filter_by(user_id=attendant_filter)

    records = query.order_by(Attendance.check_in).all()
    attendants_list = User.query.filter(
        User.role.in_(['attendant', 'gerente'])
    ).order_by(User.name).all()

    # Banco de horas acumulado por atendente (todos os tempos)
    bank_hours = {}
    for a in attendants_list:
        total_extra = sum(
            (b.extra_minutes or 0)
            for att in a.attendances
            for b in att.breaks
            if b.status == 'completed'
        )
        for att in a.attendances:
            if att.active_break:
                total_extra += att.active_break.duration_minutes - 20
        bank_hours[a.id] = max(0, total_extra)

    # Resumo salarial do mês atual por atendente
    salary_summaries = {}
    for a in attendants_list:
        if a.monthly_salary and a.monthly_salary > 0:
            salary_summaries[a.id] = a.monthly_salary_summary(att_date.year, att_date.month)

    # Atendentes sem registro de ponto no dia (ausentes)
    present_ids = {r.user_id for r in records}
    absent_today = [a for a in attendants_list if a.id not in present_ids]

    # Registros de falta do dia
    absences_today = {ab.user_id: ab for ab in
                      AbsenceRecord.query.filter_by(absence_date=att_date).all()}

    return render_template('admin/attendance.html',
        records=records, attendants_list=attendants_list,
        att_date=att_date, attendant_filter=attendant_filter,
        bank_hours=bank_hours, salary_summaries=salary_summaries,
        absent_today=absent_today, absences_today=absences_today)


@admin_bp.route('/ponto/manual', methods=['POST'])
@login_required
@manager_or_admin
def attendance_manual():
    user_id   = request.form.get('user_id', type=int)
    date_str  = request.form.get('date', '')
    checkin_s = request.form.get('check_in', '')
    checkout_s= request.form.get('check_out', '').strip()
    redirect_date = date_str

    try:
        att_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        check_in = datetime.strptime(f"{date_str} {checkin_s}", '%Y-%m-%d %H:%M')
    except Exception:
        flash('Data ou horário de entrada inválidos.', 'danger')
        return redirect(url_for('admin.attendance', date=redirect_date))

    check_out = None
    if checkout_s:
        try:
            check_out = datetime.strptime(f"{date_str} {checkout_s}", '%Y-%m-%d %H:%M')
        except Exception:
            flash('Horário de saída inválido.', 'danger')
            return redirect(url_for('admin.attendance', date=redirect_date))

    rec = Attendance(user_id=user_id, check_in=check_in, check_out=check_out, date=att_date)
    db.session.add(rec)
    db.session.commit()
    flash('Ponto lançado com sucesso.', 'success')
    return redirect(url_for('admin.attendance', date=redirect_date))


@admin_bp.route('/ponto/<int:att_id>/editar', methods=['POST'])
@login_required
@manager_or_admin
def attendance_edit(att_id):
    rec = Attendance.query.get_or_404(att_id)
    date_str   = rec.date.strftime('%Y-%m-%d')
    checkin_s  = request.form.get('check_in', '').strip()
    checkout_s = request.form.get('check_out', '').strip()

    try:
        rec.check_in = datetime.strptime(f"{date_str} {checkin_s}", '%Y-%m-%d %H:%M')
    except Exception:
        flash('Horário de entrada inválido.', 'danger')
        return redirect(url_for('admin.attendance', date=date_str))

    if checkout_s:
        try:
            rec.check_out = datetime.strptime(f"{date_str} {checkout_s}", '%Y-%m-%d %H:%M')
        except Exception:
            flash('Horário de saída inválido.', 'danger')
            return redirect(url_for('admin.attendance', date=date_str))
    else:
        rec.check_out = None

    db.session.commit()
    flash('Ponto atualizado.', 'success')
    return redirect(url_for('admin.attendance', date=date_str))


@admin_bp.route('/ponto/<int:att_id>/deletar', methods=['POST'])
@login_required
@manager_or_admin
def attendance_delete(att_id):
    rec = Attendance.query.get_or_404(att_id)
    date_str = rec.date.strftime('%Y-%m-%d')
    for b in rec.breaks:
        db.session.delete(b)
    db.session.delete(rec)
    db.session.commit()
    flash('Registro de ponto removido.', 'success')
    return redirect(url_for('admin.attendance', date=date_str))


# ── Metas mensais ──────────────────────────────────────────────────────────────

@admin_bp.route('/metas', methods=['GET', 'POST'])
@login_required
@manager_or_admin
def goals():
    today = today_br()
    month_str = request.args.get('month', today.strftime('%Y-%m'))
    try:
        year, mon = int(month_str.split('-')[0]), int(month_str.split('-')[1])
    except Exception:
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])
    attendants = User.query.filter(User.role.in_(['attendant', 'gerente']), User.is_active == True).order_by(User.name).all()

    if request.method == 'POST':
        for a in attendants:
            sg = float(request.form.get(f'sg_{a.id}', 0) or 0)
            rg = int(request.form.get(f'rg_{a.id}', 0) or 0)
            goal = AttendantGoal.query.filter_by(user_id=a.id, year=year, month=mon).first()
            if goal:
                goal.sales_goal = sg
                goal.renewals_goal = rg
            else:
                db.session.add(AttendantGoal(user_id=a.id, year=year, month=mon,
                                             sales_goal=sg, renewals_goal=rg))
        db.session.commit()
        flash('Metas salvas com sucesso!', 'success')
        return redirect(url_for('admin.goals', month=month_str))

    goals_map = {g.user_id: g for g in
                 AttendantGoal.query.filter_by(year=year, month=mon).all()}
    perf = {}
    for a in attendants:
        sales = Sale.query.filter(
            Sale.attendant_id == a.id,
            func.date(Sale.created_at) >= first_day,
            func.date(Sale.created_at) <= last_day
        ).all()
        sales_total = sum(s.amount for s in sales)
        ren_done = Renewal.query.filter_by(attendant_id=a.id, status='renewed')\
            .filter(Renewal.due_date >= first_day, Renewal.due_date <= last_day).count()
        g = goals_map.get(a.id)
        sg = g.sales_goal if g else 0
        rg = g.renewals_goal if g else 0
        perf[a.id] = {
            'sales_total': sales_total,
            'sales_goal':  sg,
            'sales_pct':   min(round(sales_total / sg * 100) if sg > 0 else 0, 100),
            'ren_done':    ren_done,
            'ren_goal':    rg,
            'ren_pct':     min(round(ren_done / rg * 100) if rg > 0 else 0, 100),
            'commission':  sum(s.commission_amount for s in sales),
        }

    return render_template('admin/goals.html',
        attendants=attendants, goals_map=goals_map, perf=perf,
        month=month_str, year=year, mon=mon)


# ── Comissões ──────────────────────────────────────────────────────────────────

@admin_bp.route('/comissoes')
@login_required
@manager_or_admin
def commissions():
    today = today_br()
    month_str = request.args.get('month', today.strftime('%Y-%m'))
    try:
        year, mon = int(month_str.split('-')[0]), int(month_str.split('-')[1])
    except Exception:
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])
    attendants = User.query.filter(User.role.in_(['attendant', 'gerente']), User.is_active == True).order_by(User.name).all()

    data = []
    total_earned = total_paid = 0.0
    for a in attendants:
        sales = Sale.query.filter(
            Sale.attendant_id == a.id,
            func.date(Sale.created_at) >= first_day,
            func.date(Sale.created_at) <= last_day
        ).all()
        earned = round(sum(s.commission_amount for s in sales), 2)
        payments = CommissionPayment.query.filter_by(
            attendant_id=a.id, year=year, month=mon).all()
        paid = round(sum(p.amount for p in payments), 2)
        data.append({'attendant': a, 'earned': earned, 'paid': paid,
                     'balance': round(earned - paid, 2),
                     'sales_count': len(sales), 'payments': payments})
        total_earned += earned
        total_paid   += paid

    return render_template('admin/commissions.html',
        data=data, month=month_str, year=year, mon=mon,
        total_earned=round(total_earned, 2),
        total_paid=round(total_paid, 2),
        total_balance=round(total_earned - total_paid, 2))


@admin_bp.route('/comissoes/pagar', methods=['POST'])
@login_required
@manager_or_admin
def pay_commission():
    att_id = request.form.get('attendant_id', type=int)
    year   = request.form.get('year', type=int)
    mon    = request.form.get('month', type=int)
    amount = float(request.form.get('amount', 0) or 0)
    notes  = request.form.get('notes', '').strip() or None
    if not att_id or not year or not mon or amount <= 0:
        flash('Dados inválidos.', 'danger')
        return redirect(url_for('admin.commissions'))
    db.session.add(CommissionPayment(
        attendant_id=att_id, year=year, month=mon,
        amount=amount, paid_by=current_user.id, notes=notes))
    db.session.flush()
    att = User.query.get(att_id)
    month_names = ['','Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    notify(att_id, 'Comissão paga!',
           f'R$ {amount:.2f} de comissão referente a {month_names[mon]}/{year} foi creditado.',
           link=url_for('attendant.my_commissions'),
           icon='bi-cash-coin', color='#6ee7b7')
    db.session.commit()
    flash(f'Pagamento de R$ {amount:.2f} registrado para {att.name}!', 'success')
    return redirect(url_for('admin.commissions', month=f'{year}-{mon:02d}'))


# ── Tabela de Preços ───────────────────────────────────────────────────────────

@admin_bp.route('/tabela-precos', methods=['GET', 'POST'])
@login_required
@manager_or_admin
def price_items():
    if request.method == 'POST':
        name         = request.form.get('name', '').strip()
        price        = float(request.form.get('price', 0) or 0)
        desc         = request.form.get('description', '').strip() or None
        period_label = request.form.get('period_label', '').strip() or None
        screens      = int(request.form.get('screens', 1) or 1)
        if name and price > 0:
            db.session.add(PriceItem(name=name, price=price, description=desc,
                                     period_label=period_label, screens=screens))
            db.session.commit()
            flash(f'Item "{name}" adicionado!', 'success')
        else:
            flash('Nome e valor são obrigatórios.', 'danger')
        return redirect(url_for('admin.price_items'))

    items = PriceItem.query.order_by(PriceItem.price).all()
    return render_template('admin/prices.html', items=items)


@admin_bp.route('/tabela-precos/<int:id>/toggle', methods=['POST'])
@login_required
@manager_or_admin
def toggle_price_item(id):
    item = PriceItem.query.get_or_404(id)
    item.is_active = not item.is_active
    db.session.commit()
    return redirect(url_for('admin.price_items'))


@admin_bp.route('/tabela-precos/<int:id>/excluir', methods=['POST'])
@login_required
@manager_or_admin
def delete_price_item(id):
    item = PriceItem.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    flash('Item excluído.', 'success')
    return redirect(url_for('admin.price_items'))


@admin_bp.route('/api/precos')
@login_required
def api_prices():
    items = PriceItem.query.filter_by(is_active=True).order_by(PriceItem.price).all()
    return jsonify([{'id': i.id, 'name': i.name, 'price': i.price,
                     'description': i.description or '',
                     'screens': i.screens or 1,
                     'period_label': i.period_label or ''} for i in items])


# ── Salários ───────────────────────────────────────────────────────────────────

@admin_bp.route('/salarios', methods=['GET', 'POST'])
@login_required
@admin_required
def salaries():
    today = today_br()
    month_str = request.args.get('m', today.strftime('%Y-%m'))
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except Exception:
        year, month = today.year, today.month

    users = User.query.filter(
        User.role.in_(['attendant', 'gerente']),
        User.is_active == True
    ).order_by(User.name).all()

    if request.method == 'POST':
        uid = int(request.form.get('user_id', 0))
        user = User.query.get_or_404(uid)
        if user.role not in ('attendant', 'gerente'):
            flash('Acesso negado.', 'danger')
            return redirect(url_for('admin.salaries'))
        user.monthly_salary      = float(request.form.get('monthly_salary', 0) or 0)
        user.work_hours_per_day  = int(request.form.get('work_hours_per_day', 8) or 8)
        user.work_days_per_month = int(request.form.get('work_days_per_month', 22) or 22)
        db.session.commit()
        flash(f'Salário de {user.name} atualizado!', 'success')
        return redirect(url_for('admin.salaries', m=month_str))

    summaries = {}
    for u in users:
        if u.monthly_salary and u.monthly_salary > 0:
            summaries[u.id] = u.monthly_salary_summary(year, month)

    # Pagamentos de salário registrados no mês
    salary_payments = {}
    for u in users:
        pmts = SalaryPayment.query.filter_by(
            attendant_id=u.id, year=year, month=month
        ).order_by(SalaryPayment.paid_at.desc()).all()
        salary_payments[u.id] = {'payments': pmts, 'total': round(sum(p.amount for p in pmts), 2)}

    # Faltas do mês
    import calendar as _cal
    first_day = date(year, month, 1)
    last_day  = date(year, month, _cal.monthrange(year, month)[1])
    absences_month = {}
    for u in users:
        abs_list = AbsenceRecord.query.filter_by(user_id=u.id).filter(
            AbsenceRecord.absence_date >= first_day,
            AbsenceRecord.absence_date <= last_day,
        ).order_by(AbsenceRecord.absence_date).all()
        absences_month[u.id] = abs_list

    prev_month = date(year, month, 1) - timedelta(days=1)
    next_month = date(year, month, 28) + timedelta(days=4)
    next_month = date(next_month.year, next_month.month, 1)

    return render_template('admin/salaries.html',
        users=users, summaries=summaries,
        salary_payments=salary_payments,
        absences_month=absences_month,
        year=year, month=month, month_str=month_str,
        prev_str=prev_month.strftime('%Y-%m'),
        next_str=next_month.strftime('%Y-%m'),
    )


@admin_bp.route('/salarios/pagar', methods=['POST'])
@login_required
@admin_required
def pay_salary():
    uid    = request.form.get('attendant_id', type=int)
    year   = request.form.get('year', type=int)
    month  = request.form.get('month', type=int)
    amount = float(request.form.get('amount', 0) or 0)
    notes  = request.form.get('notes', '').strip() or None
    if not uid or not year or not month or amount <= 0:
        flash('Dados inválidos.', 'danger')
        return redirect(url_for('admin.salaries'))
    db.session.add(SalaryPayment(
        attendant_id=uid, year=year, month=month,
        amount=amount, paid_by=current_user.id, notes=notes))
    db.session.flush()
    u = User.query.get(uid)
    month_names = ['','Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    notify(uid, 'Salário pago!',
           f'R$ {amount:.2f} referente a {month_names[month]}/{year} foi registrado.',
           link=url_for('attendant.dashboard'),
           icon='bi-wallet2', color='#fde68a')
    db.session.commit()
    flash(f'Pagamento de R$ {amount:.2f} registrado para {u.name}!', 'success')
    return redirect(url_for('admin.salaries', m=f'{year}-{month:02d}'))


@admin_bp.route('/salarios/falta', methods=['POST'])
@login_required
@admin_required
def register_absence():
    uid          = request.form.get('user_id', type=int)
    absence_date = request.form.get('absence_date', '')
    abs_type     = request.form.get('type', 'unjustified')
    notes        = request.form.get('notes', '').strip() or None
    month_str    = request.form.get('month_str', today_br().strftime('%Y-%m'))
    try:
        abs_date = datetime.strptime(absence_date, '%Y-%m-%d').date()
    except Exception:
        flash('Data inválida.', 'danger')
        return redirect(url_for('admin.salaries', m=month_str))
    existing = AbsenceRecord.query.filter_by(user_id=uid, absence_date=abs_date).first()
    if existing:
        existing.type  = abs_type
        existing.notes = notes
    else:
        db.session.add(AbsenceRecord(
            user_id=uid, absence_date=abs_date,
            type=abs_type, notes=notes, created_by=current_user.id))
    db.session.commit()
    u = User.query.get(uid)
    flash(f'Falta de {u.name} em {abs_date.strftime("%d/%m/%Y")} registrada.', 'success')
    return redirect(url_for('admin.salaries', m=month_str))


@admin_bp.route('/salarios/falta/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def delete_absence(id):
    ab = AbsenceRecord.query.get_or_404(id)
    month_str = f'{ab.absence_date.year}-{ab.absence_date.month:02d}'
    db.session.delete(ab)
    db.session.commit()
    flash('Falta removida.', 'success')
    return redirect(url_for('admin.salaries', m=month_str))


# ── Relatório PDF (print-friendly) ────────────────────────────────────────────

@admin_bp.route('/relatorios/pdf/comissoes')
@login_required
@manager_or_admin
def commission_pdf():
    today = today_br()
    month_str = request.args.get('month', today.strftime('%Y-%m'))
    try:
        year, mon = int(month_str.split('-')[0]), int(month_str.split('-')[1])
    except Exception:
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])
    month_labels_pt = ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                        'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    attendants = User.query.filter(User.role.in_(['attendant', 'gerente']), User.is_active == True).order_by(User.name).all()

    data = []
    total_earned = total_paid = 0.0
    for a in attendants:
        sales = Sale.query.filter(
            Sale.attendant_id == a.id,
            func.date(Sale.created_at) >= first_day,
            func.date(Sale.created_at) <= last_day
        ).all()
        earned = round(sum(s.commission_amount for s in sales), 2)
        paid   = round(sum(p.amount for p in CommissionPayment.query.filter_by(
            attendant_id=a.id, year=year, month=mon).all()), 2)
        data.append({'attendant': a, 'earned': earned, 'paid': paid,
                     'balance': round(earned - paid, 2), 'sales_count': len(sales),
                     'sales': sales})
        total_earned += earned
        total_paid   += paid

    return render_template('admin/commission_pdf.html',
        data=data, month_name=f'{month_labels_pt[mon-1]} {year}',
        total_earned=round(total_earned, 2),
        total_paid=round(total_paid, 2),
        total_balance=round(total_earned - total_paid, 2),
        generated_at=now_br())


@admin_bp.route('/relatorios/pdf/vendas')
@login_required
@manager_or_admin
def sales_pdf():
    date_str = request.args.get('date', today_br().strftime('%Y-%m-%d'))
    try:
        report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except Exception:
        report_date = today_br()

    sales = Sale.query.filter(func.date(Sale.created_at) == report_date).order_by(Sale.created_at).all()

    att_stats = {}
    for s in sales:
        aid = s.attendant_id
        if aid not in att_stats:
            att_stats[aid] = {'name': s.attendant.name, 'count': 0, 'total': 0.0, 'commission': 0.0, 'sales': []}
        att_stats[aid]['count']      += 1
        att_stats[aid]['total']      += s.amount
        att_stats[aid]['commission'] += s.commission_amount
        att_stats[aid]['sales'].append(s)

    total_sales      = sum(s.amount for s in sales)
    total_commission = sum(s.commission_amount for s in sales)

    return render_template('admin/sales_pdf.html',
        report_date=report_date,
        att_stats=att_stats,
        total_sales=total_sales,
        total_commission=total_commission,
        sales_count=len(sales),
        payment_methods=PAYMENT_METHODS,
        generated_at=now_br())


@admin_bp.route('/relatorios/pdf/ponto')
@login_required
@manager_or_admin
def attendance_pdf():
    month_str = request.args.get('month', today_br().strftime('%Y-%m'))
    try:
        year, mon = int(month_str.split('-')[0]), int(month_str.split('-')[1])
    except Exception:
        year, mon = today_br().year, today_br().month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])

    attendants_list = User.query.filter(
        User.role.in_(['attendant', 'gerente']), User.is_active == True
    ).order_by(User.name).all()

    rows = []
    for a in attendants_list:
        atts = [x for x in a.attendances if first_day <= x.check_in.date() <= last_day]
        if not atts:
            continue
        worked = sum(x.net_minutes for x in atts)
        deficit = sum(x.deficit_minutes(a.work_hours_per_day or 8) for x in atts)
        absences = AbsenceRecord.query.filter_by(user_id=a.id).filter(
            AbsenceRecord.absence_date >= first_day,
            AbsenceRecord.absence_date <= last_day,
        ).all()
        rows.append({
            'attendant': a,
            'days_worked': len(atts),
            'worked_h': f"{worked // 60}h{worked % 60:02d}m",
            'deficit_h': f"{deficit // 60}h{deficit % 60:02d}m",
            'absence_count': len(absences),
            'records': sorted(atts, key=lambda x: x.check_in),
        })

    month_labels_pt = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                       'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    return render_template('admin/attendance_pdf.html',
        rows=rows,
        month_name=f'{month_labels_pt[mon]} {year}',
        generated_at=now_br())


@admin_bp.route('/relatorios/pdf/salarios')
@login_required
@admin_required
def salaries_pdf():
    month_str = request.args.get('month', today_br().strftime('%Y-%m'))
    try:
        year, mon = int(month_str.split('-')[0]), int(month_str.split('-')[1])
    except Exception:
        year, mon = today_br().year, today_br().month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])

    attendants_list = User.query.filter(
        User.role.in_(['attendant', 'gerente']), User.is_active == True,
        User.monthly_salary > 0
    ).order_by(User.name).all()

    data = []
    total_base = total_deductions = total_paid = 0.0
    for a in attendants_list:
        summary  = a.monthly_salary_summary(year, mon)
        payments = SalaryPayment.query.filter_by(attendant_id=a.id, year=year, month=mon).all()
        paid     = round(sum(p.amount for p in payments), 2)
        net      = summary['net_salary']
        total_base       += a.monthly_salary or 0
        total_deductions += summary['deduction']
        total_paid       += paid
        data.append({'attendant': a, 'summary': summary, 'paid': paid, 'balance': round(net - paid, 2)})

    month_labels_pt = ['','Janeiro','Fevereiro','Março','Abril','Maio','Junho',
                       'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro']
    return render_template('admin/salaries_pdf.html',
        data=data,
        month_name=f'{month_labels_pt[mon]} {year}',
        total_base=round(total_base, 2),
        total_deductions=round(total_deductions, 2),
        total_paid=round(total_paid, 2),
        generated_at=now_br())


# ── Reset de dados (apenas admin) ─────────────────────────────────────────────

@admin_bp.route('/reset-dados', methods=['GET', 'POST'])
@login_required
@admin_required
def reset_data():
    if request.method == 'POST':
        confirm = request.form.get('confirm', '').strip()
        if confirm != 'COMFIRMAR':
            flash('Digite COMFIRMAR corretamente para prosseguir.', 'danger')
            return redirect(url_for('admin.reset_data'))

        # Apaga na ordem correta (filhos antes dos pais)
        from models import ClientContact, Message
        tables = [
            CommissionPayment, SalaryPayment, AbsenceRecord,
            OvertimeRequest, AttendantGoal,
            AttendanceBreak, Attendance,
            Sale, Renewal, ClientContact, Message, Client,
        ]
        for model in tables:
            db.session.execute(db.text(f'DELETE FROM {model.__tablename__}'))
        db.session.commit()

        flash('Todos os dados foram apagados. Usuários e planos mantidos.', 'success')
        return redirect(url_for('admin.dashboard'))

    return render_template('admin/reset_data.html')
