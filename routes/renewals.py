from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, User, Client, Renewal
from datetime import datetime, date, timedelta
from utils import now_br, today_br
import calendar

renewals_bp = Blueprint('renewals', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_access_admin():
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ── Lista de renovações ────────────────────────────────────────────────────────

@renewals_bp.route('/')
@login_required
@admin_required
def index():
    today = today_br()
    month = request.args.get('month', today.strftime('%Y-%m'))
    status_filter = request.args.get('status', '')

    try:
        year, mon = int(month.split('-')[0]), int(month.split('-')[1])
    except Exception:
        year, mon = today.year, today.month

    first_day = date(year, mon, 1)
    last_day = date(year, mon, calendar.monthrange(year, mon)[1])

    query = Renewal.query.filter(
        Renewal.due_date >= first_day,
        Renewal.due_date <= last_day
    )
    if status_filter:
        query = query.filter_by(status=status_filter)

    renewals = query.order_by(Renewal.due_date).all()

    total = len(renewals)
    renewed = sum(1 for r in renewals if r.status == 'renewed')
    pending = sum(1 for r in renewals if r.status == 'pending')
    cancelled = sum(1 for r in renewals if r.status == 'cancelled')
    overdue = sum(1 for r in renewals if r.is_overdue)
    total_value = sum(r.amount for r in renewals if r.status == 'renewed')
    renewal_rate = round((renewed / total * 100) if total > 0 else 0, 1)

    # ── Gráficos ──────────────────────────────────────────────────────────────
    day_names = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']

    # Por dia da semana (últimas 4 semanas)
    w4_start = today - timedelta(days=27)
    all_4w = Renewal.query.filter(Renewal.due_date >= w4_start).all()
    day_renewed   = [0] * 7
    day_cancelled = [0] * 7
    day_pending   = [0] * 7
    for r in all_4w:
        dow = r.due_date.weekday()
        if r.status == 'renewed':   day_renewed[dow]   += 1
        elif r.status == 'cancelled': day_cancelled[dow] += 1
        else:                          day_pending[dow]   += 1
    chart_weekday = {
        'labels': day_names,
        'renewed': day_renewed,
        'cancelled': day_cancelled,
        'pending': day_pending,
    }

    # Por semana (últimas 8 semanas)
    w8_start = today - timedelta(weeks=8)
    all_8w = Renewal.query.filter(Renewal.due_date >= w8_start).all()
    week_renewed = {}
    week_cancelled = {}
    for r in all_8w:
        iso = r.due_date.isocalendar()
        key = f'{iso[0]}-S{iso[1]:02d}'
        if r.status == 'renewed':
            week_renewed[key] = week_renewed.get(key, 0) + 1
        elif r.status == 'cancelled':
            week_cancelled[key] = week_cancelled.get(key, 0) + 1
    week_keys = sorted(set(list(week_renewed.keys()) + list(week_cancelled.keys())))
    chart_weekly = {
        'labels': week_keys,
        'renewed': [week_renewed.get(k, 0) for k in week_keys],
        'cancelled': [week_cancelled.get(k, 0) for k in week_keys],
    }

    # Por mês (últimos 12 meses)
    m12_start = today.replace(day=1) - timedelta(days=365)
    all_12m = Renewal.query.filter(Renewal.due_date >= m12_start).all()
    month_labels_pt = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez']
    month_renewed = {}
    month_cancelled = {}
    for r in all_12m:
        key = r.due_date.strftime('%Y-%m')
        if r.status == 'renewed':
            month_renewed[key] = month_renewed.get(key, 0) + 1
        elif r.status == 'cancelled':
            month_cancelled[key] = month_cancelled.get(key, 0) + 1
    month_keys = sorted(set(list(month_renewed.keys()) + list(month_cancelled.keys())))
    chart_monthly = {
        'labels': [f"{month_labels_pt[int(k.split('-')[1])-1]}/{k.split('-')[0][2:]}" for k in month_keys],
        'renewed': [month_renewed.get(k, 0) for k in month_keys],
        'cancelled': [month_cancelled.get(k, 0) for k in month_keys],
    }

    return render_template('admin/renewals.html',
        renewals=renewals,
        month=month,
        stats=dict(total=total, renewed=renewed, pending=pending,
                   cancelled=cancelled, overdue=overdue,
                   total_value=total_value, renewal_rate=renewal_rate),
        status_filter=status_filter,
        chart_weekday=chart_weekday,
        chart_weekly=chart_weekly,
        chart_monthly=chart_monthly,
    )


# ── Nova renovação ─────────────────────────────────────────────────────────────

@renewals_bp.route('/nova', methods=['GET', 'POST'])
@login_required
@admin_required
def new_renewal():
    clients = Client.query.order_by(Client.name).all()
    attendants = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()

    if request.method == 'POST':
        client_id = request.form.get('client_id') or None
        client_name_manual = request.form.get('client_name_manual', '').strip() or None
        plan_name = request.form.get('plan_name', '').strip()
        amount = request.form.get('amount', '0').replace(',', '.')
        due_date_str = request.form.get('due_date', '')
        attendant_id = request.form.get('attendant_id') or None
        notes = request.form.get('notes', '').strip() or None

        if not plan_name or not due_date_str:
            flash('Plano e data de vencimento são obrigatórios.', 'danger')
            return render_template('admin/renewal_form.html', renewal=None, clients=clients, attendants=attendants)

        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            amount = float(amount)
        except Exception:
            flash('Dados inválidos.', 'danger')
            return render_template('admin/renewal_form.html', renewal=None, clients=clients, attendants=attendants)

        renewal = Renewal(
            client_id=int(client_id) if client_id else None,
            client_name_manual=client_name_manual,
            plan_name=plan_name,
            amount=amount,
            due_date=due_date,
            attendant_id=int(attendant_id) if attendant_id else None,
            notes=notes,
        )
        db.session.add(renewal)
        db.session.commit()
        flash('Renovação cadastrada com sucesso!', 'success')
        return redirect(url_for('renewals.index'))

    return render_template('admin/renewal_form.html', renewal=None, clients=clients, attendants=attendants)


# ── Editar renovação ───────────────────────────────────────────────────────────

@renewals_bp.route('/<int:id>/editar', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_renewal(id):
    renewal = Renewal.query.get_or_404(id)
    clients = Client.query.order_by(Client.name).all()
    attendants = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()

    if request.method == 'POST':
        renewal.client_id = request.form.get('client_id') or None
        renewal.client_name_manual = request.form.get('client_name_manual', '').strip() or None
        renewal.plan_name = request.form.get('plan_name', '').strip()
        renewal.notes = request.form.get('notes', '').strip() or None
        renewal.attendant_id = request.form.get('attendant_id') or None
        try:
            renewal.amount = float(request.form.get('amount', '0').replace(',', '.'))
            renewal.due_date = datetime.strptime(request.form.get('due_date'), '%Y-%m-%d').date()
        except Exception:
            flash('Dados inválidos.', 'danger')
            return render_template('admin/renewal_form.html', renewal=renewal, clients=clients, attendants=attendants)

        db.session.commit()
        flash('Renovação atualizada!', 'success')
        return redirect(url_for('renewals.index'))

    return render_template('admin/renewal_form.html', renewal=renewal, clients=clients, attendants=attendants)


# ── Marcar como renovado ───────────────────────────────────────────────────────

@renewals_bp.route('/<int:id>/renovar', methods=['POST'])
@login_required
@admin_required
def mark_renewed(id):
    renewal = Renewal.query.get_or_404(id)
    renewal.status = 'renewed'
    renewal.renewed_at = now_br()
    db.session.commit()
    flash(f'Renovação de "{renewal.client_display}" marcada como renovada!', 'success')
    return redirect(request.referrer or url_for('renewals.index'))


# ── Marcar como cancelado ──────────────────────────────────────────────────────

@renewals_bp.route('/<int:id>/cancelar', methods=['POST'])
@login_required
@admin_required
def mark_cancelled(id):
    renewal = Renewal.query.get_or_404(id)
    renewal.status = 'cancelled'
    db.session.commit()
    flash(f'Renovação de "{renewal.client_display}" marcada como cancelada.', 'warning')
    return redirect(request.referrer or url_for('renewals.index'))


# ── Excluir ────────────────────────────────────────────────────────────────────

@renewals_bp.route('/<int:id>/excluir', methods=['POST'])
@login_required
@admin_required
def delete_renewal(id):
    renewal = Renewal.query.get_or_404(id)
    db.session.delete(renewal)
    db.session.commit()
    flash('Renovação excluída.', 'success')
    return redirect(url_for('renewals.index'))
