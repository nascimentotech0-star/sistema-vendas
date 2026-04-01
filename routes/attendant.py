import os
import uuid
import calendar as cal
from datetime import datetime, date, timedelta
from utils import now_br, today_br
from functools import wraps

from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename

from models import (db, Attendance, AttendanceBreak, OvertimeRequest, Client, Sale, Renewal,
                    PAYMENT_METHODS, BREAK_ALLOWED_MINUTES, DAYS_AT_RISK,
                    CommissionPayment, PriceItem)
from flask import jsonify as _jsonify

attendant_bp = Blueprint('attendant', __name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'pdf'}


def attendant_required(f):
    """Atendentes e gerentes podem acessar estas rotas."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not current_user.is_active:
            flash('Sua conta está desativada. Contate o administrador.', 'danger')
            return redirect(url_for('auth.logout'))
        # Apenas financeiro e admin puro são bloqueados aqui
        if current_user.is_financial():
            return redirect(url_for('financial.index'))
        if current_user.is_admin():
            # Admin puro não precisa de rotas de atendente, mas não bloqueia — apenas redireciona
            pass
        return f(*args, **kwargs)
    return decorated


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _shift_end():
    """Retorna a hora de término do turno do usuário logado (padrão 22)."""
    try:
        return current_user.shift_end_hour or 22
    except Exception:
        return 22


def get_commission_rate():
    hour = now_br().hour
    return 5.0 if 8 <= hour < _shift_end() else 20.0


def is_overtime_now():
    hour = now_br().hour
    return not (8 <= hour < _shift_end())


def can_request_overtime_now():
    """Solicitação permitida 1h antes do fim do turno."""
    hour = now_br().hour
    end  = _shift_end()
    return hour >= (end - 1)


# ── Dashboard ──────────────────────────────────────────────────────────────────

@attendant_bp.route('/')
@login_required
@attendant_required
def dashboard():
    today = today_br()
    day_start = datetime(today.year, today.month, today.day, 0, 0, 0)
    day_end   = day_start + timedelta(days=1)
    attendance = current_user.active_attendance

    today_sales = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        Sale.created_at >= day_start,
        Sale.created_at < day_end,
    ).order_by(Sale.created_at.desc()).all()

    today_total = sum(s.amount for s in today_sales)
    today_commission = sum(s.commission_amount for s in today_sales)

    overtime_req = OvertimeRequest.query.filter(
        OvertimeRequest.user_id == current_user.id,
        OvertimeRequest.requested_at >= day_start,
        OvertimeRequest.requested_at < day_end,
    ).first()

    overtime = is_overtime_now()
    commission_rate = 20.0 if overtime else 5.0

    active_break = attendance.active_break if attendance else None
    can_request_overtime = can_request_overtime_now()

    # ── Gráficos (vendas do próprio atendente) ────────────────────────────────
    day_names = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

    w4_start = today - timedelta(days=27)
    sales_4w = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        db.func.date(Sale.created_at) >= w4_start
    ).all()
    day_totals = [0.0] * 7
    for s in sales_4w:
        day_totals[s.created_at.weekday()] += s.amount
    chart_weekday = {'labels': day_names, 'data': [round(v, 2) for v in day_totals]}

    w8_start = today - timedelta(weeks=8)
    sales_8w = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        db.func.date(Sale.created_at) >= w8_start
    ).all()
    week_map = {}
    for s in sales_8w:
        d = s.created_at.date()
        iso = d.isocalendar()
        key = f'{iso[0]}-S{iso[1]:02d}'
        week_map[key] = week_map.get(key, 0) + s.amount
    week_keys = sorted(week_map.keys())
    chart_weekly = {'labels': week_keys, 'data': [round(week_map[k], 2) for k in week_keys]}

    m12_start = today.replace(day=1) - timedelta(days=365)
    sales_12m = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        db.func.date(Sale.created_at) >= m12_start
    ).all()
    month_labels_pt = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    month_map = {}
    for s in sales_12m:
        key = s.created_at.strftime('%Y-%m')
        month_map[key] = month_map.get(key, 0) + s.amount
    month_keys = sorted(month_map.keys())
    chart_monthly = {
        'labels': [f"{month_labels_pt[int(k.split('-')[1])-1]}/{k.split('-')[0][2:]}" for k in month_keys],
        'data': [round(month_map[k], 2) for k in month_keys]
    }

    # ── Renovações do mês (clientes do atendente) ─────────────────────────────
    my_clients_ids = [c.id for c in Client.query.filter_by(registered_by=current_user.id).all()]
    first_month = date(today.year, today.month, 1)
    last_month  = date(today.year, today.month, cal.monthrange(today.year, today.month)[1])
    my_renewals = Renewal.query.filter(
        Renewal.client_id.in_(my_clients_ids),
        Renewal.due_date >= first_month,
        Renewal.due_date <= last_month
    ).order_by(Renewal.due_date).all() if my_clients_ids else []

    renewals_pending  = [r for r in my_renewals if r.status == 'pending']
    renewals_overdue  = [r for r in my_renewals if r.is_overdue]
    renewals_done     = sum(1 for r in my_renewals if r.status == 'renewed')

    # ── Clientes em risco (sem contato há 5+ dias) ────────────────────────────
    my_clients = Client.query.filter_by(registered_by=current_user.id).all()
    at_risk_clients = sorted(
        [c for c in my_clients if c.is_at_risk],
        key=lambda c: c.days_without_contact, reverse=True
    )

    # ── Comissão acumulada no mês ─────────────────────────────────────────────
    month_start = datetime(today.year, today.month, 1)
    month_end   = datetime(today.year, today.month,
                           cal.monthrange(today.year, today.month)[1]) + timedelta(days=1)
    month_sales = Sale.query.filter(
        Sale.attendant_id == current_user.id,
        Sale.created_at >= month_start,
        Sale.created_at < month_end,
    ).all()
    month_total      = sum(s.amount for s in month_sales)
    month_commission = sum(s.commission_amount for s in month_sales)

    # ── Resumo salarial do mês ────────────────────────────────────────────────
    salary_summary = current_user.monthly_salary_summary(today.year, today.month)

    # Déficit de hoje (ponto em aberto: projeção se encerrar agora)
    today_deficit_mins = 0
    today_net_mins = 0
    if attendance and attendance.check_out is None:
        today_net_mins = attendance.net_minutes
        expected = (current_user.work_hours_per_day or 8) * 60
        today_deficit_mins = max(0, expected - today_net_mins)

    return render_template('attendant/dashboard.html',
        attendance=attendance,
        today_sales=today_sales,
        today_total=today_total,
        today_commission=today_commission,
        overtime_req=overtime_req,
        commission_rate=commission_rate,
        is_overtime=overtime,
        payment_methods=PAYMENT_METHODS,
        now=now_br(),
        active_break=active_break,
        break_allowed=BREAK_ALLOWED_MINUTES,
        can_request_overtime=can_request_overtime,
        chart_weekday=chart_weekday,
        chart_weekly=chart_weekly,
        chart_monthly=chart_monthly,
        my_renewals=my_renewals,
        renewals_pending=renewals_pending,
        renewals_overdue=renewals_overdue,
        renewals_done=renewals_done,
        at_risk_clients=at_risk_clients,
        salary_summary=salary_summary,
        today_deficit_mins=today_deficit_mins,
        today_net_mins=today_net_mins,
        month_total=month_total,
        month_commission=month_commission,
    )


# ── Ponto ──────────────────────────────────────────────────────────────────────

@attendant_bp.route('/ponto/entrada', methods=['POST'])
@login_required
@attendant_required
def checkin():
    if current_user.active_attendance:
        flash('Você já iniciou o atendimento.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    att = Attendance(user_id=current_user.id, check_in=now_br(), date=today_br())
    db.session.add(att)
    db.session.commit()
    flash('Atendimento iniciado! Boas vendas!', 'success')
    return redirect(url_for('attendant.dashboard'))


@attendant_bp.route('/ponto/saida', methods=['POST'])
@login_required
@attendant_required
def checkout():
    att = current_user.active_attendance
    if not att:
        flash('Nenhum atendimento ativo.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    att.check_out = now_br()
    db.session.commit()
    flash(f'Atendimento encerrado. Duração: {att.duration}', 'success')
    return redirect(url_for('attendant.dashboard'))


# ── Renovações do atendente ────────────────────────────────────────────────────

@attendant_bp.route('/renovacoes')
@login_required
@attendant_required
def renewals():
    today = today_br()
    month = request.args.get('month', today.strftime('%Y-%m'))
    status_filter = request.args.get('status', '')

    try:
        year, mon = int(month.split('-')[0]), int(month.split('-')[1])
    except Exception:
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    last_day  = date(year, mon, cal.monthrange(year, mon)[1])

    my_client_ids = [c.id for c in Client.query.filter_by(registered_by=current_user.id).all()]

    # Mostra renovações dos clientes do atendente OU renovações que ele atendeu
    query = Renewal.query.filter(
        Renewal.due_date >= first_day,
        Renewal.due_date <= last_day,
        db.or_(
            Renewal.client_id.in_(my_client_ids) if my_client_ids else db.false(),
            Renewal.attendant_id == current_user.id
        )
    )

    if status_filter:
        query = query.filter_by(status=status_filter)

    all_renewals = query.order_by(Renewal.due_date).all()

    total     = len(all_renewals)
    renewed   = sum(1 for r in all_renewals if r.status == 'renewed')
    pending   = sum(1 for r in all_renewals if r.status == 'pending')
    cancelled = sum(1 for r in all_renewals if r.status == 'cancelled')
    overdue   = sum(1 for r in all_renewals if r.is_overdue)
    rate      = round((renewed / total * 100) if total > 0 else 0, 1)

    # ── Gráficos ──────────────────────────────────────────────────────────────
    day_names = ['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']
    w4_start = today - timedelta(days=27)
    all_4w = Renewal.query.filter(
        Renewal.client_id.in_(my_client_ids),
        Renewal.due_date >= w4_start
    ).all() if my_client_ids else []
    day_renewed   = [0]*7
    day_cancelled = [0]*7
    day_pending   = [0]*7
    for r in all_4w:
        dow = r.due_date.weekday()
        if r.status == 'renewed':    day_renewed[dow]   += 1
        elif r.status == 'cancelled': day_cancelled[dow] += 1
        else:                         day_pending[dow]   += 1
    chart_weekday = {'labels': day_names, 'renewed': day_renewed,
                     'cancelled': day_cancelled, 'pending': day_pending}

    m6_start = today.replace(day=1) - timedelta(days=180)
    all_6m = Renewal.query.filter(
        Renewal.client_id.in_(my_client_ids),
        Renewal.due_date >= m6_start
    ).all() if my_client_ids else []
    month_labels_pt = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    month_renewed  = {}
    month_cancelled = {}
    month_pending   = {}
    for r in all_6m:
        key = r.due_date.strftime('%Y-%m')
        if r.status == 'renewed':    month_renewed[key]   = month_renewed.get(key, 0) + 1
        elif r.status == 'cancelled': month_cancelled[key] = month_cancelled.get(key, 0) + 1
        else:                         month_pending[key]   = month_pending.get(key, 0) + 1
    month_keys = sorted(set(list(month_renewed) + list(month_cancelled) + list(month_pending)))
    chart_monthly = {
        'labels':    [f"{month_labels_pt[int(k.split('-')[1])-1]}/{k.split('-')[0][2:]}" for k in month_keys],
        'renewed':   [month_renewed.get(k, 0) for k in month_keys],
        'cancelled': [month_cancelled.get(k, 0) for k in month_keys],
        'pending':   [month_pending.get(k, 0) for k in month_keys],
    }

    my_clients = Client.query.order_by(Client.name).all()
    price_items = PriceItem.query.filter_by(is_active=True).order_by(PriceItem.price).all()

    return render_template('attendant/renewals.html',
        renewals=all_renewals,
        month=month,
        status_filter=status_filter,
        stats=dict(total=total, renewed=renewed, pending=pending,
                   cancelled=cancelled, overdue=overdue, rate=rate),
        chart_weekday=chart_weekday,
        chart_monthly=chart_monthly,
        my_clients=my_clients,
        price_items=price_items,
    )


# ── Ações de renovação (atendente) ─────────────────────────────────────────────

@attendant_bp.route('/renovacoes/<int:id>/renovar', methods=['POST'])
@login_required
@attendant_required
def att_renew(id):
    renewal = Renewal.query.get_or_404(id)

    # Comprovante obrigatório
    file = request.files.get('comprovante')
    if not file or not file.filename or not allowed_file(file.filename):
        flash('Comprovante de pagamento é obrigatório para confirmar a renovação.', 'danger')
        return redirect(url_for('attendant.renewals'))

    ext = file.filename.rsplit('.', 1)[1].lower()
    fname = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], fname))

    # Atualiza valor se informado
    amount_str = request.form.get('amount', '').strip().replace(',', '.')
    if amount_str:
        try:
            renewal.amount = float(amount_str)
        except ValueError:
            pass

    renewal.status = 'renewed'
    renewal.renewed_at = now_br()
    renewal.attendant_id = current_user.id
    renewal.comprovante_filename = fname
    db.session.commit()
    flash(f'Renovação de "{renewal.client_display}" confirmada com comprovante!', 'success')
    return redirect(url_for('attendant.renewals'))


@attendant_bp.route('/renovacoes/<int:id>/cancelar', methods=['POST'])
@login_required
@attendant_required
def att_cancel(id):
    renewal = Renewal.query.get_or_404(id)
    renewal.status = 'cancelled'
    renewal.attendant_id = current_user.id
    db.session.commit()
    flash(f'Renovação de "{renewal.client_display}" marcada como cancelada.', 'warning')
    return redirect(url_for('attendant.renewals'))


@attendant_bp.route('/renovacoes/nova', methods=['POST'])
@login_required
@attendant_required
def att_new_renewal():
    client_id   = request.form.get('client_id') or None
    plan_name   = request.form.get('plan_name', '').strip()
    amount_str  = request.form.get('amount', '0').replace(',', '.')
    due_date_str = request.form.get('due_date', '')
    notes       = request.form.get('notes', '').strip() or None

    if not client_id or not plan_name or not due_date_str:
        flash('Cliente, plano e data de vencimento são obrigatórios.', 'danger')
        return redirect(url_for('attendant.renewals'))

    if not Client.query.get(int(client_id)):
        flash('Cliente não encontrado.', 'danger')
        return redirect(url_for('attendant.renewals'))

    try:
        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        amount   = float(amount_str)
    except Exception:
        flash('Data ou valor inválido.', 'danger')
        return redirect(url_for('attendant.renewals'))

    # Comprovante opcional ao cadastrar (para migração de clientes existentes)
    comprovante_filename = None
    file = request.files.get('comprovante')
    if file and file.filename and allowed_file(file.filename):
        ext = file.filename.rsplit('.', 1)[1].lower()
        fname = f"{uuid.uuid4().hex}.{ext}"
        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], fname))
        comprovante_filename = fname

    renewal = Renewal(
        client_id=int(client_id),
        plan_name=plan_name,
        amount=amount,
        due_date=due_date,
        attendant_id=current_user.id,
        notes=notes,
        comprovante_filename=comprovante_filename,
        status='pending',
    )
    db.session.add(renewal)
    db.session.commit()
    client_name = Client.query.get(int(client_id)).name
    flash(f'Renovação de {client_name} — {plan_name} cadastrada!', 'success')
    return redirect(url_for('attendant.renewals'))


# ── Pausa / Descanso ──────────────────────────────────────────────────────────

@attendant_bp.route('/pausa/iniciar', methods=['POST'])
@login_required
@attendant_required
def start_break():
    att = current_user.active_attendance
    if not att:
        flash('Inicie o atendimento antes de pausar.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    if att.active_break:
        flash('Você já está em pausa.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    brk = AttendanceBreak(
        attendance_id=att.id,
        user_id=current_user.id,
        started_at=now_br(),
        status='active'
    )
    db.session.add(brk)
    db.session.commit()
    flash(f'Pausa iniciada. Você tem {BREAK_ALLOWED_MINUTES} minutos de descanso.', 'info')
    return redirect(url_for('attendant.dashboard'))


@attendant_bp.route('/pausa/encerrar', methods=['POST'])
@login_required
@attendant_required
def end_break():
    att = current_user.active_attendance
    if not att or not att.active_break:
        flash('Nenhuma pausa ativa.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    brk = att.active_break
    brk.ended_at = now_br()
    brk.status = 'completed'
    duration = int((brk.ended_at - brk.started_at).total_seconds() / 60)
    brk.extra_minutes = max(0, duration - BREAK_ALLOWED_MINUTES)
    db.session.commit()
    if brk.extra_minutes > 0:
        flash(f'Pausa encerrada. Duração: {brk.duration_str}. '
              f'Você excedeu {brk.extra_minutes} minuto(s) — adicionado ao banco de horas.', 'warning')
    else:
        flash(f'Pausa encerrada. Duração: {brk.duration_str}. Bem-vindo de volta!', 'success')
    return redirect(url_for('attendant.dashboard'))


# ── Hora Extra ─────────────────────────────────────────────────────────────────

@attendant_bp.route('/hora-extra/solicitar', methods=['POST'])
@login_required
@attendant_required
def request_overtime():
    if not can_request_overtime_now():
        end = _shift_end()
        flash(f'Solicitação de hora extra só pode ser enviada a partir das {end - 1}h.', 'danger')
        return redirect(url_for('attendant.dashboard'))
    today = today_br()
    day_start = datetime(today.year, today.month, today.day)
    day_end   = day_start + timedelta(days=1)
    existing = OvertimeRequest.query.filter(
        OvertimeRequest.user_id == current_user.id,
        OvertimeRequest.requested_at >= day_start,
        OvertimeRequest.requested_at < day_end,
    ).first()
    if existing:
        flash('Você já enviou uma solicitação hoje.', 'warning')
        return redirect(url_for('attendant.dashboard'))
    req = OvertimeRequest(user_id=current_user.id, requested_at=now_br(), status='pending')
    db.session.add(req)
    db.session.commit()
    flash('Solicitação de hora extra enviada! Aguarde aprovação do administrador.', 'info')
    return redirect(url_for('attendant.dashboard'))


# ── Clientes ───────────────────────────────────────────────────────────────────

@attendant_bp.route('/clientes')
@login_required
@attendant_required
def clients():
    search = request.args.get('q', '').strip()
    query = Client.query.filter_by(registered_by=current_user.id)
    if search:
        query = query.filter(Client.name.ilike(f'%{search}%'))
    clients_list = query.order_by(Client.name).all()
    return render_template('attendant/clients.html', clients=clients_list, search=search)


@attendant_bp.route('/clientes/novo', methods=['GET', 'POST'])
@login_required
@attendant_required
def new_client():
    overtime = is_overtime_now()

    def _chart_data():
        today = today_br()
        day_start = datetime(today.year, today.month, today.day)
        day_end   = day_start + timedelta(days=1)
        week_start_dt = datetime(today.year, today.month, today.day) - timedelta(days=6)
        sales_7d = Sale.query.filter(
            Sale.attendant_id == current_user.id,
            Sale.created_at >= week_start_dt,
        ).all()
        day_labels, day_vals = [], []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            day_labels.append(d.strftime('%d/%m'))
            day_vals.append(round(sum(s.amount for s in sales_7d if s.created_at.date() == d), 2))
        clients_today = Client.query.filter(
            Client.registered_by == current_user.id,
            Client.created_at >= day_start,
            Client.created_at < day_end,
        ).count()
        sales_today = Sale.query.filter(
            Sale.attendant_id == current_user.id,
            Sale.created_at >= day_start,
            Sale.created_at < day_end,
        ).count()
        return day_labels, day_vals, clients_today, sales_today

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('Nome é obrigatório.', 'danger')
            dl, dv, ct, st = _chart_data()
            return render_template('attendant/client_form.html',
                                   client=None, payment_methods=PAYMENT_METHODS,
                                   is_overtime=overtime,
                                   commission_rate=get_commission_rate(),
                                   chart_labels=dl, chart_vals=dv,
                                   clients_today=ct, sales_today=st)

        client = Client(
            name=name,
            phone=request.form.get('phone', '').strip() or None,
            whatsapp=request.form.get('whatsapp', '').strip() or None,
            email=request.form.get('email', '').strip() or None,
            city=request.form.get('city', '').strip() or None,
            state=request.form.get('state', '').strip() or None,
            notes=request.form.get('notes', '').strip() or None,
            registered_by=current_user.id
        )
        db.session.add(client)
        db.session.flush()

        amount_str = request.form.get('amount', '').strip().replace(',', '.')
        payment_method = request.form.get('payment_method', '')
        description = request.form.get('description', '').strip() or None

        if amount_str and payment_method:
            try:
                amount = float(amount_str)
                screens    = int(request.form.get('screens', 1) or 1)
                adjustment = float(request.form.get('adjustment', 0) or 0)
                amount = round(amount + adjustment, 2)  # valor final cobrado
                if amount > 0:
                    commission_rate = get_commission_rate()
                    commission_amount = round(amount * commission_rate / 100, 2)

                    # Comprovante opcional
                    comprovante_filename = None
                    file = request.files.get('comprovante')
                    if file and file.filename and allowed_file(file.filename):
                        ext = file.filename.rsplit('.', 1)[1].lower()
                        fname = f"{uuid.uuid4().hex}.{ext}"
                        file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], fname))
                        comprovante_filename = fname

                    sale = Sale(
                        attendant_id=current_user.id,
                        client_id=client.id,
                        amount=amount,
                        payment_method=payment_method,
                        commission_rate=commission_rate,
                        commission_amount=commission_amount,
                        description=description,
                        comprovante_filename=comprovante_filename,
                        is_overtime=overtime,
                        screens=screens,
                        adjustment=adjustment,
                    )
                    db.session.add(sale)
                    db.session.commit()
                    flash(f'Cliente {name} cadastrado! Venda de R$ {amount:.2f} registrada. '
                          f'Comissão: R$ {commission_amount:.2f} ({commission_rate:.0f}%)', 'success')
                    return redirect(url_for('attendant.dashboard'))
            except ValueError:
                pass

        db.session.commit()
        flash(f'Cliente {name} cadastrado com sucesso!', 'success')
        return redirect(url_for('attendant.dashboard'))

    dl, dv, ct, st = _chart_data()
    return render_template('attendant/client_form.html',
                           client=None, payment_methods=PAYMENT_METHODS,
                           is_overtime=overtime,
                           commission_rate=get_commission_rate(),
                           chart_labels=dl, chart_vals=dv,
                           clients_today=ct, sales_today=st)


@attendant_bp.route('/clientes/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@attendant_required
def edit_client(id):
    client = Client.query.get_or_404(id)
    if request.method == 'POST':
        client.name = request.form.get('name', '').strip()
        client.phone = request.form.get('phone', '').strip() or None
        client.whatsapp = request.form.get('whatsapp', '').strip() or None
        client.email = request.form.get('email', '').strip() or None
        client.city = request.form.get('city', '').strip() or None
        client.state = request.form.get('state', '').strip() or None
        client.notes = request.form.get('notes', '').strip() or None
        db.session.commit()
        flash('Cliente atualizado!', 'success')
        return redirect(url_for('attendant.clients'))
    return render_template('attendant/client_form.html', client=client)


# ── Vendas ─────────────────────────────────────────────────────────────────────

@attendant_bp.route('/vendas')
@login_required
@attendant_required
def sales():
    page = request.args.get('page', 1, type=int)
    sales_list = Sale.query.filter_by(attendant_id=current_user.id).order_by(Sale.created_at.desc()).paginate(page=page, per_page=20)
    return render_template('attendant/sales.html', sales=sales_list, payment_methods=PAYMENT_METHODS)


@attendant_bp.route('/vendas/nova', methods=['GET', 'POST'])
@login_required
@attendant_required
def new_sale():
    overtime = is_overtime_now()

    if overtime:
        today = today_br()
        day_start = datetime(today.year, today.month, today.day)
        approved = OvertimeRequest.query.filter(
            OvertimeRequest.user_id == current_user.id,
            OvertimeRequest.requested_at >= day_start,
            OvertimeRequest.requested_at < day_start + timedelta(days=1),
            OvertimeRequest.status == 'approved'
        ).first()
        if not approved:
            flash(f'Fora do horário comercial (08h–{_shift_end():02d}h). Solicite aprovação de hora extra para registrar vendas.', 'warning')
            return redirect(url_for('attendant.dashboard'))

    clients_list = Client.query.filter_by(registered_by=current_user.id).order_by(Client.name).all()

    if request.method == 'POST':
        amount_str = request.form.get('amount', '').strip().replace(',', '.')
        payment_method = request.form.get('payment_method', '')
        client_id = request.form.get('client_id') or None
        client_name_manual = request.form.get('client_name_manual', '').strip() or None
        description = request.form.get('description', '').strip() or None

        if not amount_str or not payment_method:
            flash('Valor e forma de pagamento são obrigatórios.', 'danger')
            return render_template('attendant/sale_form.html', clients=clients_list,
                                   is_overtime=overtime, payment_methods=PAYMENT_METHODS)
        try:
            amount = float(amount_str)
            if amount <= 0:
                raise ValueError
        except ValueError:
            flash('Valor inválido.', 'danger')
            return render_template('attendant/sale_form.html', clients=clients_list,
                                   is_overtime=overtime, payment_methods=PAYMENT_METHODS)

        screens    = int(request.form.get('screens', 1) or 1)
        adjustment = float(request.form.get('adjustment', 0) or 0)
        amount = round(amount + adjustment, 2)  # valor final cobrado
        commission_rate = get_commission_rate()
        commission_amount = round(amount * (commission_rate / 100), 2)

        # Comprovante opcional
        comprovante_filename = None
        file = request.files.get('comprovante')
        if file and file.filename and allowed_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f"{uuid.uuid4().hex}.{ext}"
            file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
            comprovante_filename = filename

        sale = Sale(
            attendant_id=current_user.id,
            client_id=int(client_id) if client_id else None,
            client_name_manual=client_name_manual,
            amount=amount,
            payment_method=payment_method,
            commission_rate=commission_rate,
            commission_amount=commission_amount,
            description=description,
            comprovante_filename=comprovante_filename,
            is_overtime=is_overtime_now(),
            screens=screens,
            adjustment=adjustment,
        )
        db.session.add(sale)
        db.session.commit()

        flash(f'Venda de R$ {amount:.2f} registrada! Comissão: R$ {commission_amount:.2f} ({commission_rate:.0f}%)', 'success')
        return redirect(url_for('attendant.sales'))

    return render_template('attendant/sale_form.html', clients=clients_list,
                           is_overtime=overtime, payment_methods=PAYMENT_METHODS)


# ── Comissões do atendente ─────────────────────────────────────────────────────

@attendant_bp.route('/comissoes')
@login_required
@attendant_required
def my_commissions():
    sales = Sale.query.filter_by(attendant_id=current_user.id).all()
    months: dict = {}
    for s in sales:
        k = (s.created_at.year, s.created_at.month)
        if k not in months:
            months[k] = {'earned': 0.0, 'sales': 0}
        months[k]['earned'] += s.commission_amount
        months[k]['sales']  += 1

    payments_raw = CommissionPayment.query.filter_by(attendant_id=current_user.id).all()
    paid_map = {}
    for p in payments_raw:
        k = (p.year, p.month)
        paid_map[k] = paid_map.get(k, 0.0) + p.amount

    month_names = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    data = []
    for (yr, mo), d in sorted(months.items(), reverse=True):
        paid = round(paid_map.get((yr, mo), 0.0), 2)
        data.append({
            'year': yr, 'month': mo,
            'label': f"{month_names[mo-1]}/{str(yr)[2:]}",
            'earned': round(d['earned'], 2),
            'sales':  d['sales'],
            'paid':   paid,
            'balance': round(d['earned'] - paid, 2),
        })

    return render_template('attendant/commissions.html',
        data=data,
        total_earned=round(sum(d['earned'] for d in data), 2),
        total_paid=round(sum(d['paid'] for d in data), 2),
        total_balance=round(sum(d['balance'] for d in data), 2),
    )


# ── API Preços (para quick-select no formulário) ───────────────────────────────

@attendant_bp.route('/api/precos')
@login_required
@attendant_required
def api_prices():
    items = PriceItem.query.filter_by(is_active=True).order_by(PriceItem.price).all()
    return _jsonify([{'id': i.id, 'name': i.name, 'price': i.price,
                      'description': i.description or '',
                      'screens': i.screens or 1,
                      'period_label': i.period_label or ''} for i in items])
