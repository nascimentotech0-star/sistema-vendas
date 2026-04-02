"""
Exportação CSV para Admin e usuário Financeiro.
Endpoints:
  GET /exportar/vendas
  GET /exportar/renovacoes
  GET /exportar/clientes
  GET /exportar/auditoria
  GET /exportar/financeiro
  GET /exportar/backup          — dump JSON completo (admin apenas)
  GET /exportar/backup/auto?token=X — mesmo dump, protegido por token (agendamento externo)
Todos aceitam os mesmos parâmetros de filtro que as páginas equivalentes.
"""
import csv
import io
import json
import os
from datetime import date, datetime, timedelta
from functools import wraps
from utils import now_br, today_br

from flask import Blueprint, request, Response, redirect, url_for, flash, render_template
from flask_login import login_required, current_user
from sqlalchemy import func

from models import db, User, Client, Sale, Renewal, ClientContact, PAYMENT_METHODS, SalaryPayment, AbsenceRecord
import calendar

exports_bp = Blueprint('exports', __name__)


def _can_export():
    return current_user.is_authenticated and (
        current_user.can_access_admin() or current_user.is_financial()
    )


def export_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _can_export():
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def _csv_response(filename, rows, headers):
    """Build a UTF-8-BOM CSV Response (Excel-friendly)."""
    buf = io.StringIO()
    buf.write('\ufeff')                       # BOM for Excel
    writer = csv.writer(buf, delimiter=';')   # semicolon for pt-BR Excel
    writer.writerow(headers)
    writer.writerows(rows)
    return Response(
        buf.getvalue(),
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )


# ── Vendas ─────────────────────────────────────────────────────────────────

@exports_bp.route('/vendas')
@login_required
@export_required
def sales():
    today     = today_br()
    date_from = request.args.get('from', (today.replace(day=1)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   today.strftime('%Y-%m-%d'))
    att_id    = request.args.get('attendant', 0, type=int)

    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d').date()
        d_to   = datetime.strptime(date_to,   '%Y-%m-%d').date()
    except Exception:
        d_from, d_to = today.replace(day=1), today

    q = Sale.query.filter(
        func.date(Sale.created_at) >= d_from,
        func.date(Sale.created_at) <= d_to,
    )
    if att_id:
        q = q.filter_by(attendant_id=att_id)
    records = q.order_by(Sale.created_at.desc()).all()

    headers = ['ID', 'Data', 'Atendente', 'Cliente', 'Valor (R$)',
               'Pagamento', 'Comissão (%)', 'Comissão (R$)', 'Hora Extra']
    rows = [
        [
            s.id,
            s.created_at.strftime('%d/%m/%Y %H:%M'),
            s.attendant.name,
            s.client_display,
            f'{s.amount:.2f}'.replace('.', ','),
            PAYMENT_METHODS.get(s.payment_method, s.payment_method),
            f'{s.commission_rate:.1f}',
            f'{s.commission_amount:.2f}'.replace('.', ','),
            'Sim' if s.is_overtime else 'Não',
        ]
        for s in records
    ]
    fname = f'vendas_{date_from}_{date_to}.csv'
    return _csv_response(fname, rows, headers)


# ── Renovações ─────────────────────────────────────────────────────────────

@exports_bp.route('/renovacoes')
@login_required
@export_required
def renewals():
    today    = today_br()
    d_from   = request.args.get('from', today.replace(day=1).strftime('%Y-%m-%d'))
    d_to     = request.args.get('to',   today.strftime('%Y-%m-%d'))
    status   = request.args.get('status', '')
    att_id   = request.args.get('attendant', 0, type=int)

    try:
        df = datetime.strptime(d_from, '%Y-%m-%d').date()
        dt = datetime.strptime(d_to,   '%Y-%m-%d').date()
    except Exception:
        df, dt = today.replace(day=1), today

    q = Renewal.query.filter(
        Renewal.due_date >= df,
        Renewal.due_date <= dt,
    )
    if status:
        q = q.filter_by(status=status)
    if att_id:
        q = q.filter_by(attendant_id=att_id)
    records = q.order_by(Renewal.due_date).all()

    status_map = {'pending': 'Pendente', 'renewed': 'Renovado', 'cancelled': 'Cancelado'}
    headers = ['ID', 'Cliente', 'Plano', 'Valor (R$)', 'Vencimento',
               'Status', 'Renovado em', 'Atendente', 'Observação']
    rows = [
        [
            r.id,
            r.client_display,
            r.plan_name,
            f'{r.amount:.2f}'.replace('.', ','),
            r.due_date.strftime('%d/%m/%Y'),
            status_map.get(r.status, r.status),
            r.renewed_at.strftime('%d/%m/%Y %H:%M') if r.renewed_at else '',
            r.attendant.name if r.attendant else '',
            r.notes or '',
        ]
        for r in records
    ]
    fname = f'renovacoes_{d_from}_{d_to}.csv'
    return _csv_response(fname, rows, headers)


# ── Clientes ────────────────────────────────────────────────────────────────

@exports_bp.route('/clientes')
@login_required
@export_required
def clients():
    search   = request.args.get('q', '').strip()
    att_id   = request.args.get('attendant', 0, type=int)
    risk_only= request.args.get('risk', '') == '1'

    q = Client.query
    if search:
        q = q.filter(db.or_(
            Client.name.ilike(f'%{search}%'),
            Client.phone.ilike(f'%{search}%'),
            Client.whatsapp.ilike(f'%{search}%'),
        ))
    if att_id:
        q = q.filter_by(registered_by=att_id)
    records = q.order_by(Client.name).all()
    if risk_only:
        records = [c for c in records if c.is_at_risk]

    headers = ['ID', 'Nome', 'WhatsApp', 'Telefone', 'E-mail', 'Cidade', 'Estado',
               'Cadastrado por', 'Cadastro em', 'Último contato', 'Dias sem contato', 'Status']
    rows = [
        [
            c.id,
            c.name,
            c.whatsapp or '',
            c.phone or '',
            c.email or '',
            c.city or '',
            c.state or '',
            c.registered_by_user.name if c.registered_by_user else '',
            c.created_at.strftime('%d/%m/%Y'),
            c.last_contact.contacted_at.strftime('%d/%m/%Y %H:%M') if c.last_contact else 'Nunca',
            c.days_without_contact,
            'Em risco' if c.is_at_risk else 'OK',
        ]
        for c in records
    ]
    fname = f'clientes_{today_br()}.csv'
    return _csv_response(fname, rows, headers)


# ── Auditoria de atendimentos ───────────────────────────────────────────────

@exports_bp.route('/auditoria')
@login_required
@export_required
def audit():
    today     = today_br()
    date_from = request.args.get('from', (today - timedelta(days=29)).strftime('%Y-%m-%d'))
    date_to   = request.args.get('to',   today.strftime('%Y-%m-%d'))
    att_id    = request.args.get('attendant', 0, type=int)
    tag       = request.args.get('tag', '')
    evt_type  = request.args.get('type', '')

    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d').date()
        d_to   = datetime.strptime(date_to,   '%Y-%m-%d').date()
    except Exception:
        d_from, d_to = today - timedelta(days=29), today

    q = ClientContact.query.filter(
        func.date(ClientContact.contacted_at) >= d_from,
        func.date(ClientContact.contacted_at) <= d_to,
    )
    if att_id:
        q = q.filter_by(attendant_id=att_id)
    if tag:
        q = q.filter_by(tag=tag)
    if evt_type:
        q = q.filter_by(event_type=evt_type)
    records = q.order_by(ClientContact.contacted_at.desc()).all()

    direction_map = {'incoming': 'Entrada', 'outgoing': 'Saída'}
    channel_map   = {'whatsapp': 'WhatsApp', 'phone': 'Telefone',
                     'email': 'E-mail', 'system': 'Sistema', 'other': 'Outro'}
    headers = ['Data/Hora', 'Atendente', 'Cliente', 'Tipo', 'Direção', 'Canal', 'Assunto', 'Observação']
    rows = [
        [
            r.contacted_at.strftime('%d/%m/%Y %H:%M'),
            r.attendant.name,
            r.client.name,
            'Manual' if r.event_type == 'manual' else 'Visualização',
            direction_map.get(r.direction, r.direction),
            channel_map.get(r.channel, r.channel),
            r.tag_info[0] if r.tag_info else '',
            r.notes or '',
        ]
        for r in records
    ]
    fname = f'auditoria_{date_from}_{date_to}.csv'
    return _csv_response(fname, rows, headers)


# ── Resumo financeiro mensal ────────────────────────────────────────────────

@exports_bp.route('/financeiro')
@login_required
@export_required
def financial():
    today = today_br()

    headers = ['Mês', 'Vendas (Qtd)', 'Vendas (R$)',
               'Renovações (Qtd)', 'Renovações (R$)',
               'Total Recebido (R$)', 'A Receber (R$)', 'Perdido (R$)', 'Crescimento (%)']
    rows = []

    y, m = today.year, today.month
    prev_received = None
    month_list = []
    for _ in range(12):
        month_list.insert(0, (y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1

    for (yr, mo) in month_list:
        first_day = date(yr, mo, 1)
        last_day  = date(yr, mo, calendar.monthrange(yr, mo)[1])

        sales = Sale.query.filter(
            func.date(Sale.created_at) >= first_day,
            func.date(Sale.created_at) <= last_day
        ).all()
        ren_done = Renewal.query.filter(
            Renewal.status == 'renewed',
            func.date(Renewal.renewed_at) >= first_day,
            func.date(Renewal.renewed_at) <= last_day
        ).all()
        ren_pend = Renewal.query.filter(
            Renewal.status == 'pending',
            Renewal.due_date >= first_day,
            Renewal.due_date <= last_day
        ).all()
        ren_cancel = Renewal.query.filter(
            Renewal.status == 'cancelled',
            Renewal.due_date >= first_day,
            Renewal.due_date <= last_day
        ).all()

        s_total  = sum(s.amount for s in sales)
        r_total  = sum(r.amount for r in ren_done)
        received = s_total + r_total
        pending  = sum(r.amount for r in ren_pend)
        lost     = sum(r.amount for r in ren_cancel)

        if prev_received and prev_received > 0:
            growth = f'{((received - prev_received) / prev_received * 100):+.1f}'.replace('.', ',')
        else:
            growth = ''
        prev_received = received

        MONTH_FULL = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                      'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
        rows.append([
            f'{MONTH_FULL[mo]}/{yr}',
            len(sales),
            f'{s_total:.2f}'.replace('.', ','),
            len(ren_done),
            f'{r_total:.2f}'.replace('.', ','),
            f'{received:.2f}'.replace('.', ','),
            f'{pending:.2f}'.replace('.', ','),
            f'{lost:.2f}'.replace('.', ','),
            growth,
        ])

    fname = f'financeiro_{today}.csv'
    return _csv_response(fname, rows, headers)


# ── Folha de Salários ───────────────────────────────────────────────────────

@exports_bp.route('/salarios')
@login_required
@export_required
def salaries():
    today     = today_br()
    month_str = request.args.get('m', today.strftime('%Y-%m'))
    try:
        year, month = int(month_str[:4]), int(month_str[5:7])
    except Exception:
        year, month = today.year, today.month

    first_day = date(year, month, 1)
    last_day  = date(year, month, calendar.monthrange(year, month)[1])

    users = User.query.filter(
        User.role.in_(['attendant', 'gerente']),
        User.is_active == True
    ).order_by(User.name).all()

    headers = ['Nome', 'Cargo', 'Salário Base (R$)', 'Dias/Mês', 'h/dia',
               'Valor/hora (R$)', 'Dias Trabalhados', 'Faltas',
               'Horas Trabalhadas', 'Déficit (h)', 'Desconto (R$)',
               'Salário Previsto (R$)', 'Total Pago (R$)', 'Saldo (R$)']
    rows = []
    for u in users:
        if not u.monthly_salary or u.monthly_salary == 0:
            continue
        s = u.monthly_salary_summary(year, month)
        paid = round(sum(p.amount for p in SalaryPayment.query.filter_by(
            attendant_id=u.id, year=year, month=month).all()), 2)
        net = s['net_salary']
        rows.append([
            u.name,
            'Gerente' if u.role == 'gerente' else 'Atendente',
            f'{u.monthly_salary:.2f}'.replace('.', ','),
            u.work_days_per_month or 22,
            u.work_hours_per_day or 8,
            f'{u.hourly_rate:.2f}'.replace('.', ','),
            s['days_worked'],
            s.get('absence_count', 0),
            s['worked_h'],
            s['deficit_h'],
            f'{s["deduction"]:.2f}'.replace('.', ','),
            f'{net:.2f}'.replace('.', ','),
            f'{paid:.2f}'.replace('.', ','),
            f'{round(net - paid, 2):.2f}'.replace('.', ','),
        ])

    MONTH_FULL = ['', 'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                  'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    fname = f'salarios_{MONTH_FULL[month]}_{year}.csv'
    return _csv_response(fname, rows, headers)


# ── Backup Completo JSON ────────────────────────────────────────────────────

def _build_backup():
    """Serializa todos os dados críticos para um dict JSON."""
    def fmt(v):
        if isinstance(v, (datetime, date)): return v.isoformat()
        return v

    def rows(model, fields):
        return [{f: fmt(getattr(obj, f, None)) for f in fields}
                for obj in model.query.all()]

    from models import (Attendance, AttendanceBreak, OvertimeRequest,
                        CommissionPayment, AttendantGoal, AbsenceRecord,
                        PriceItem, Message, AuditLog)

    return {
        'generated_at': now_br().isoformat(),
        'users': rows(User, ['id','username','name','email','role','is_active',
                              'monthly_salary','work_hours_per_day','work_days_per_month',
                              'shift_end_hour','monthly_sales_target','created_at']),
        'clients': rows(Client, ['id','name','phone','whatsapp','email',
                                  'city','state','notes','registered_by','created_at']),
        'sales': rows(Sale, ['id','attendant_id','client_id','client_name_manual',
                              'amount','payment_method','commission_rate','commission_amount',
                              'description','is_overtime','screens','adjustment',
                              'comprovante_hash','created_at']),
        'renewals': rows(Renewal, ['id','client_id','client_name_manual','plan_name',
                                    'amount','due_date','status','renewed_at',
                                    'attendant_id','notes','created_at']),
        'client_contacts': rows(ClientContact, ['id','client_id','attendant_id',
                                                 'contacted_at','direction','channel',
                                                 'tag','event_type','notes']),
        'attendances': rows(Attendance, ['id','user_id','check_in','check_out','date']),
        'overtime_requests': rows(OvertimeRequest, ['id','user_id','requested_at',
                                                     'status','approved_by','approved_at','notes']),
        'commission_payments': rows(CommissionPayment, ['id','attendant_id','year','month',
                                                          'amount','paid_at','paid_by','notes']),
        'salary_payments': rows(SalaryPayment, ['id','attendant_id','year','month',
                                                  'amount','paid_at','paid_by','notes']),
        'absence_records': rows(AbsenceRecord, ['id','user_id','absence_date','type',
                                                  'notes','created_by','created_at']),
        'price_items': rows(PriceItem, ['id','name','price','description',
                                         'screens','period_label','is_active','created_at']),
        'audit_logs': rows(AuditLog, ['id','user_id','action','target_type',
                                       'target_id','description','ip_address','created_at']),
    }


@exports_bp.route('/backup')
@login_required
def backup_page():
    if not current_user.is_admin():
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login'))
    backup_token = os.environ.get('BACKUP_TOKEN', '')
    return render_template('admin/backup.html', backup_token=backup_token)


@exports_bp.route('/backup/download')
@login_required
def backup_download():
    if not current_user.is_admin():
        flash('Acesso negado.', 'danger')
        return redirect(url_for('auth.login'))
    data = _build_backup()
    fname = f'backup_{today_br().strftime("%Y-%m-%d")}.json'
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )


@exports_bp.route('/backup/auto')
def backup_auto():
    """Endpoint para agendamento externo (cron-job.org). Protegido por token."""
    token = request.args.get('token', '')
    expected = os.environ.get('BACKUP_TOKEN', '')
    if not expected or token != expected:
        return Response('Unauthorized', status=401)
    data = _build_backup()
    fname = f'backup_{today_br().strftime("%Y-%m-%d")}.json'
    return Response(
        json.dumps(data, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': f'attachment; filename="{fname}"'},
    )
