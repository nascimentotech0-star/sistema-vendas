from flask import Blueprint, render_template, request
from flask_login import login_required, current_user
from functools import wraps
from models import db, User, Sale, Renewal
from datetime import date
from sqlalchemy import func
import calendar

financial_bp = Blueprint('financial', __name__)

MONTH_ABBR = ['', 'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
              'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

MONTH_FULL = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
              'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']


def financial_or_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not (
                current_user.can_access_admin() or current_user.is_financial()):
            from flask import redirect, url_for, flash
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def _month_data(year, month):
    """Computes all financial metrics for a given year/month."""
    first_day = date(year, month, 1)
    last_day  = date(year, month, calendar.monthrange(year, month)[1])

    # Vendas novas registradas no mês
    sales = Sale.query.filter(
        func.date(Sale.created_at) >= first_day,
        func.date(Sale.created_at) <= last_day
    ).all()
    sales_total = sum(s.amount for s in sales)
    sales_count = len(sales)

    # Renovações confirmadas no mês (pela data de renovação)
    renewals_done = Renewal.query.filter(
        Renewal.status == 'renewed',
        func.date(Renewal.renewed_at) >= first_day,
        func.date(Renewal.renewed_at) <= last_day
    ).all()
    renewals_total = sum(r.amount for r in renewals_done)
    renewals_count = len(renewals_done)

    # Renovações pendentes com vencimento no mês
    renewals_pending = Renewal.query.filter(
        Renewal.status == 'pending',
        Renewal.due_date >= first_day,
        Renewal.due_date <= last_day
    ).all()
    pending_total = sum(r.amount for r in renewals_pending)
    pending_count = len(renewals_pending)

    # Renovações canceladas com vencimento no mês
    renewals_cancelled = Renewal.query.filter(
        Renewal.status == 'cancelled',
        Renewal.due_date >= first_day,
        Renewal.due_date <= last_day
    ).all()
    cancelled_total = sum(r.amount for r in renewals_cancelled)
    cancelled_count = len(renewals_cancelled)

    total_received = sales_total + renewals_total
    # Faturado = recebido + ainda a receber no mês
    total_billed   = total_received + pending_total

    return {
        'year':             year,
        'month':            month,
        'label_short':      f"{MONTH_ABBR[month]}/{str(year)[2:]}",
        'label_full':       f"{MONTH_FULL[month]}/{year}",
        'first_day':        first_day,
        'last_day':         last_day,
        'sales_total':      sales_total,
        'sales_count':      sales_count,
        'renewals_total':   renewals_total,
        'renewals_count':   renewals_count,
        'total_received':   total_received,
        'total_billed':     total_billed,
        'pending_total':    pending_total,
        'pending_count':    pending_count,
        'cancelled_total':  cancelled_total,
        'cancelled_count':  cancelled_count,
    }


@financial_bp.route('/')
@login_required
@financial_or_admin
def index():
    today = date.today()

    # Mês selecionado (padrão: atual)
    sel = request.args.get('m', f"{today.year}-{today.month:02d}")
    try:
        sel_year, sel_month = int(sel.split('-')[0]), int(sel.split('-')[1])
    except Exception:
        sel_year, sel_month = today.year, today.month

    # Últimos 12 meses (para a tabela e gráfico)
    months_data = []
    y, m = today.year, today.month
    for _ in range(12):
        months_data.insert(0, _month_data(y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    # Calcula crescimento (% vs mês anterior)
    for i, md in enumerate(months_data):
        if i == 0:
            md['growth'] = None
        else:
            prev = months_data[i - 1]['total_received']
            curr = md['total_received']
            md['growth'] = round((curr - prev) / prev * 100, 1) if prev > 0 else None

    current_month = months_data[-1]

    # Mês visualizado (pode ser diferente do atual)
    viewed_month = _month_data(sel_year, sel_month)

    # Crescimento do mês visualizado vs anterior
    prev_year, prev_month = sel_year, sel_month - 1
    if prev_month == 0:
        prev_month = 12
        prev_year -= 1
    prev_data = _month_data(prev_year, prev_month)
    if prev_data['total_received'] > 0:
        viewed_month['growth'] = round(
            (viewed_month['total_received'] - prev_data['total_received'])
            / prev_data['total_received'] * 100, 1)
    else:
        viewed_month['growth'] = None

    # Breakdown por atendente (mês visualizado)
    attendants   = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()
    att_rows     = []
    grand_total  = viewed_month['total_received'] or 1  # evita divisão por zero
    for a in attendants:
        att_sales = Sale.query.filter(
            Sale.attendant_id == a.id,
            func.date(Sale.created_at) >= viewed_month['first_day'],
            func.date(Sale.created_at) <= viewed_month['last_day']
        ).all()
        att_ren = Renewal.query.filter(
            Renewal.attendant_id == a.id,
            Renewal.status == 'renewed',
            func.date(Renewal.renewed_at) >= viewed_month['first_day'],
            func.date(Renewal.renewed_at) <= viewed_month['last_day']
        ).all()
        s_total = sum(s.amount for s in att_sales)
        r_total = sum(r.amount for r in att_ren)
        total   = s_total + r_total
        if total > 0 or att_sales or att_ren:
            att_rows.append({
                'name':           a.name,
                'sales_total':    s_total,
                'sales_count':    len(att_sales),
                'renewals_total': r_total,
                'renewals_count': len(att_ren),
                'total':          total,
                'pct':            round(total / grand_total * 100, 1),
            })
    att_rows.sort(key=lambda x: x['total'], reverse=True)

    # Dados para o gráfico de evolução
    chart_labels   = [m['label_short'] for m in months_data]
    chart_sales    = [round(m['sales_total'],    2) for m in months_data]
    chart_renewals = [round(m['renewals_total'], 2) for m in months_data]
    chart_pending  = [round(m['pending_total'],  2) for m in months_data]

    # Seletor de meses disponíveis (últimos 12 + próximo)
    month_options = []
    y2, m2 = today.year, today.month
    for _ in range(13):
        month_options.insert(0, {
            'value': f"{y2}-{m2:02d}",
            'label': f"{MONTH_FULL[m2]}/{y2}",
        })
        m2 -= 1
        if m2 == 0:
            m2 = 12
            y2 -= 1

    return render_template('financial/index.html',
        months_data   = months_data,
        current_month = current_month,
        viewed_month  = viewed_month,
        prev_data     = prev_data,
        att_rows      = att_rows,
        chart_labels  = chart_labels,
        chart_sales   = chart_sales,
        chart_renewals= chart_renewals,
        chart_pending = chart_pending,
        month_options = month_options,
        sel           = sel,
    )
