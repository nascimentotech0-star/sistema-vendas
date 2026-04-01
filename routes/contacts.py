from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from models import db, Client, ClientContact, User, DAYS_AT_RISK, CONTACT_TAGS
from datetime import datetime, date, timedelta
from utils import now_br, today_br
from functools import wraps
from sqlalchemy import func

contacts_bp = Blueprint('contacts', __name__)


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.can_access_admin():
            flash('Acesso negado.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


# ── Atualizar dados de contato do cliente (whatsapp/phone) ────────────────────

@contacts_bp.route('/cliente/<int:client_id>/atualizar-contato', methods=['POST'])
@login_required
def update_client_contact(client_id):
    client = Client.query.get_or_404(client_id)
    whatsapp = request.form.get('whatsapp', '').strip() or None
    phone    = request.form.get('phone', '').strip() or None
    if whatsapp is not None:
        client.whatsapp = whatsapp
    if phone is not None:
        client.phone = phone
    db.session.commit()
    flash('Contato atualizado!', 'success')
    return redirect(url_for('contacts.client_detail', client_id=client_id))


# ── Registrar contato manual ───────────────────────────────────────────────────

@contacts_bp.route('/cliente/<int:client_id>/registrar', methods=['POST'])
@login_required
def register_contact(client_id):
    client    = Client.query.get_or_404(client_id)
    direction = request.form.get('direction', 'outgoing')
    channel   = request.form.get('channel', 'whatsapp')
    tag       = request.form.get('tag', '').strip() or None
    notes     = request.form.get('notes', '').strip() or None

    contact = ClientContact(
        client_id    = client_id,
        attendant_id = current_user.id,
        contacted_at = now_br(),
        direction    = direction,
        channel      = channel,
        tag          = tag,
        event_type   = 'manual',
        notes        = notes,
    )
    db.session.add(contact)
    db.session.commit()
    flash(f'Contato com {client.name} registrado!', 'success')
    return redirect(url_for('contacts.client_detail', client_id=client_id))


# ── Detalhe do cliente + auto-registro de visualização ───────────────────────

@contacts_bp.route('/cliente/<int:client_id>')
@login_required
def client_detail(client_id):
    client   = Client.query.get_or_404(client_id)
    contacts = ClientContact.query.filter_by(client_id=client_id)\
        .order_by(ClientContact.contacted_at.desc()).all()

    # Auto-registra visualização apenas para atendentes (não admin)
    if not current_user.can_access_admin():
        view = ClientContact(
            client_id    = client_id,
            attendant_id = current_user.id,
            contacted_at = now_br(),
            direction    = 'outgoing',
            channel      = 'system',
            event_type   = 'view',
            notes        = None,
        )
        db.session.add(view)
        db.session.commit()
        # Recarrega para incluir o registro novo
        contacts = ClientContact.query.filter_by(client_id=client_id)\
            .order_by(ClientContact.contacted_at.desc()).all()

    return render_template('contacts/client_detail.html',
        client=client, contacts=contacts, contact_tags=CONTACT_TAGS)


# ── Auditoria de atendimentos (admin) ─────────────────────────────────────────

@contacts_bp.route('/admin/auditoria')
@login_required
@admin_required
def audit():
    today      = today_br()
    date_from  = request.args.get('from', (today - timedelta(days=6)).strftime('%Y-%m-%d'))
    date_to    = request.args.get('to', today.strftime('%Y-%m-%d'))
    att_filter = request.args.get('attendant', 0, type=int)
    tag_filter = request.args.get('tag', '')
    type_filter= request.args.get('type', '')  # '' | manual | view

    try:
        d_from = datetime.strptime(date_from, '%Y-%m-%d').date()
        d_to   = datetime.strptime(date_to,   '%Y-%m-%d').date()
    except Exception:
        d_from, d_to = today - timedelta(days=6), today

    query = ClientContact.query.filter(
        func.date(ClientContact.contacted_at) >= d_from,
        func.date(ClientContact.contacted_at) <= d_to,
    )
    if att_filter:
        query = query.filter_by(attendant_id=att_filter)
    if tag_filter:
        query = query.filter_by(tag=tag_filter)
    if type_filter:
        query = query.filter_by(event_type=type_filter)

    records = query.order_by(ClientContact.contacted_at.desc()).all()

    # Stats por atendente
    from collections import defaultdict
    att_stats = defaultdict(lambda: {'name':'', 'manual':0, 'view':0, 'tags':defaultdict(int)})
    for r in records:
        s = att_stats[r.attendant_id]
        s['name'] = r.attendant.name
        if r.event_type == 'manual':
            s['manual'] += 1
            if r.tag:
                s['tags'][r.tag] += 1
        else:
            s['view'] += 1

    attendants = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()

    return render_template('contacts/audit.html',
        records=records,
        att_stats=dict(att_stats),
        attendants=attendants,
        contact_tags=CONTACT_TAGS,
        date_from=date_from,
        date_to=date_to,
        att_filter=att_filter,
        tag_filter=tag_filter,
        type_filter=type_filter,
        total_manual=sum(1 for r in records if r.event_type == 'manual'),
        total_views=sum(1 for r in records if r.event_type == 'view'),
    )


# ── Clientes em risco (admin) ─────────────────────────────────────────────────

@contacts_bp.route('/admin/sem-contato')
@login_required
@admin_required
def at_risk():
    all_clients   = Client.query.all()
    risk_clients  = sorted([c for c in all_clients if c.is_at_risk],
                            key=lambda c: c.days_without_contact, reverse=True)
    attendants    = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()

    by_attendant     = {}
    no_contact_clients = []
    for c in risk_clients:
        if c.last_contact:
            aid = c.last_contact.attendant_id
            by_attendant.setdefault(aid, []).append(c)
        else:
            no_contact_clients.append(c)

    return render_template('contacts/at_risk.html',
        risk_clients=risk_clients,
        by_attendant=by_attendant,
        no_contact_clients=no_contact_clients,
        attendants={a.id: a for a in attendants},
        days_threshold=DAYS_AT_RISK)


# ── Lista geral de clientes (admin) ───────────────────────────────────────────

@contacts_bp.route('/admin/clientes')
@login_required
@admin_required
def all_clients():
    search           = request.args.get('q', '').strip()
    risk_only        = request.args.get('risk', '') == '1'
    attendant_filter = request.args.get('attendant', 0, type=int)

    query = Client.query
    if search:
        query = query.filter(db.or_(
            Client.name.ilike(f'%{search}%'),
            Client.phone.ilike(f'%{search}%'),
            Client.whatsapp.ilike(f'%{search}%'),
        ))
    if attendant_filter:
        query = query.filter_by(registered_by=attendant_filter)

    clients      = query.order_by(Client.name).all()
    if risk_only:
        clients  = [c for c in clients if c.is_at_risk]

    at_risk_count = sum(1 for c in Client.query.all() if c.is_at_risk)
    attendants    = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()

    return render_template('contacts/all_clients.html',
        clients=clients, search=search, risk_only=risk_only,
        at_risk_count=at_risk_count, attendant_filter=attendant_filter,
        attendants=attendants, days_threshold=DAYS_AT_RISK)
