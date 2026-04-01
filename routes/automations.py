from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from functools import wraps
from models import db, User, Client, Renewal, Message, DAYS_AT_RISK
from datetime import datetime, date, timedelta
from utils import now_br, today_br
from collections import Counter

automations_bp = Blueprint('automations', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_access_admin():
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ── Painel de automações ───────────────────────────────────────────────────────

@automations_bp.route('/')
@login_required
@admin_required
def index():
    today    = today_br()
    week_end = today + timedelta(days=7)

    # Renovações desta semana (próximos 7 dias, pendentes)
    renewals_week = Renewal.query.filter(
        Renewal.status == 'pending',
        Renewal.due_date >= today,
        Renewal.due_date <= week_end,
    ).order_by(Renewal.due_date).all()

    # Renovações atrasadas
    renewals_overdue = Renewal.query.filter(
        Renewal.status == 'pending',
        Renewal.due_date < today,
    ).order_by(Renewal.due_date).all()

    # Clientes em risco (sem contato ≥ DAYS_AT_RISK)
    all_clients = Client.query.all()
    at_risk     = sorted([c for c in all_clients if c.is_at_risk],
                         key=lambda c: c.days_without_contact, reverse=True)

    # Renovações sem atendente atribuído
    unassigned = Renewal.query.filter_by(status='pending', attendant_id=None).all()

    # Atendentes ativos
    attendants = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()

    # Carga de trabalho atual (renovações pendentes por atendente)
    load = {a.id: Renewal.query.filter_by(status='pending', attendant_id=a.id).count()
            for a in attendants}

    return render_template('automations/index.html',
        renewals_week    = renewals_week,
        renewals_overdue = renewals_overdue,
        at_risk          = at_risk,
        unassigned       = unassigned,
        attendants       = attendants,
        load             = load,
        today            = today,
        days_threshold   = DAYS_AT_RISK,
    )


# ── Enviar lembrete via chat interno ─────────────────────────────────────────

@automations_bp.route('/lembrete/<int:renewal_id>', methods=['POST'])
@login_required
@admin_required
def send_reminder(renewal_id):
    renewal     = Renewal.query.get_or_404(renewal_id)
    attendant_id = request.form.get('attendant_id', type=int)

    if not attendant_id:
        flash('Selecione um atendente para enviar o lembrete.', 'warning')
        return redirect(url_for('automations.index'))

    days_left   = (renewal.due_date - today_br()).days
    client_name = renewal.client_display

    if days_left < 0:
        timing = f"*ATRASADA há {abs(days_left)} dia(s)*"
    elif days_left == 0:
        timing = "*VENCE HOJE* ⚠️"
    elif days_left == 1:
        timing = "*VENCE AMANHÃ* ⚠️"
    else:
        timing = f"vence em *{days_left} dias* ({renewal.due_date.strftime('%d/%m/%Y')})"

    msg_text = (
        f"🔔 *Lembrete de Renovação*\n\n"
        f"👤 Cliente: *{client_name}*\n"
        f"📦 Plano: {renewal.plan_name}\n"
        f"💰 Valor: R$ {renewal.amount:,.2f}\n"
        f"📅 Vencimento: {timing}\n\n"
        f"Por favor, entre em contato e registre a renovação no sistema. ✅"
    )

    msg = Message(
        sender_id    = current_user.id,
        attendant_id = attendant_id,
        content      = msg_text,
    )
    db.session.add(msg)
    db.session.commit()

    attendant = User.query.get(attendant_id)
    flash(f'Lembrete enviado para {attendant.name} via chat!', 'success')
    return redirect(url_for('automations.index'))


# ── Lembrete de follow-up (clientes em risco) ─────────────────────────────────

@automations_bp.route('/followup/<int:client_id>', methods=['POST'])
@login_required
@admin_required
def send_followup(client_id):
    client       = Client.query.get_or_404(client_id)
    attendant_id = request.form.get('attendant_id', type=int)

    if not attendant_id:
        flash('Selecione um atendente para enviar o lembrete.', 'warning')
        return redirect(url_for('automations.index'))

    days = client.days_without_contact
    msg_text = (
        f"⚠️ *Cliente sem contato — {days} dia(s)*\n\n"
        f"👤 Cliente: *{client.name}*\n"
        f"📞 Contato: {client.phone_display}\n\n"
        f"Esse cliente está *em risco*. Por favor, entre em contato "
        f"e registre o atendimento no sistema. 💬"
    )

    msg = Message(
        sender_id    = current_user.id,
        attendant_id = attendant_id,
        content      = msg_text,
    )
    db.session.add(msg)
    db.session.commit()

    attendant = User.query.get(attendant_id)
    flash(f'Lembrete de follow-up enviado para {attendant.name}!', 'success')
    return redirect(url_for('automations.index'))


# ── Auto-distribuição de renovações ───────────────────────────────────────────

@automations_bp.route('/distribuir', methods=['POST'])
@login_required
@admin_required
def auto_distribute():
    unassigned = Renewal.query.filter_by(status='pending', attendant_id=None).all()
    attendants = User.query.filter_by(role='attendant', is_active=True).all()

    if not attendants:
        flash('Nenhum atendente ativo para distribuir.', 'warning')
        return redirect(url_for('automations.index'))

    if not unassigned:
        flash('Não há renovações sem atendente para distribuir.', 'info')
        return redirect(url_for('automations.index'))

    # Carga atual
    load = Counter({a.id: Renewal.query.filter_by(
        status='pending', attendant_id=a.id).count() for a in attendants})

    distributed = 0
    for renewal in unassigned:
        least = min(attendants, key=lambda a: load[a.id])
        renewal.attendant_id = least.id
        load[least.id] += 1
        distributed += 1

    db.session.commit()
    flash(f'{distributed} renovação(ões) distribuída(s) automaticamente entre os atendentes!', 'success')
    return redirect(url_for('automations.index'))


# ── Gerador de mensagem WhatsApp (API JSON) ───────────────────────────────────

@automations_bp.route('/mensagem')
@login_required
def message_template():
    renewal_id = request.args.get('renewal_id', type=int)
    client_id  = request.args.get('client_id',  type=int)

    if renewal_id:
        renewal     = Renewal.query.get_or_404(renewal_id)
        client_name = renewal.client_display
        days_left   = (renewal.due_date - today_br()).days

        if days_left < 0:
            timing = f"venceu há {abs(days_left)} dia(s) e está em atraso"
        elif days_left == 0:
            timing = "vence *hoje*"
        elif days_left == 1:
            timing = "vence *amanhã*"
        else:
            timing = f"vence em *{days_left} dias*, no dia {renewal.due_date.strftime('%d/%m/%Y')}"

        text = (
            f"Olá, {client_name}! 👋\n\n"
            f"Passando para avisar que sua renovação do plano *{renewal.plan_name}* "
            f"{timing}.\n\n"
            f"💰 Valor: R$ {renewal.amount:,.2f}\n\n"
            f"Para renovar é só me chamar aqui mesmo! 😊"
        )

    elif client_id:
        client = Client.query.get_or_404(client_id)
        days   = client.days_without_contact
        text = (
            f"Olá, {client.name}! 👋\n\n"
            f"Tudo bem? Faz {days} dia(s) que não conversamos — "
            f"estou passando para saber se está precisando de alguma coisa "
            f"ou tem alguma dúvida que posso ajudar. 😊\n\n"
            f"Estou à disposição!"
        )
    else:
        return jsonify({'error': 'Parâmetros inválidos'}), 400

    return jsonify({'text': text})
