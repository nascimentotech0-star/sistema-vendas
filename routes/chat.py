import os
import uuid
from flask import (Blueprint, render_template, request, jsonify,
                   redirect, url_for, flash, current_app, send_from_directory)
from flask_login import login_required, current_user
from extensions import csrf
from models import db, User, Message
from datetime import datetime
from utils import now_br

chat_bp = Blueprint('chat', __name__)

ALLOWED_IMAGE = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_AUDIO = {'webm', 'ogg', 'mp4', 'mp3', 'm4a', 'wav'}


def _save_file(file_storage):
    """Salva o arquivo e retorna (file_name, file_type, original_name) ou None."""
    if not file_storage or not file_storage.filename:
        return None
    original = file_storage.filename
    ext = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    if ext in ALLOWED_IMAGE:
        ftype = 'image'
    elif ext in ALLOWED_AUDIO:
        ftype = 'audio'
    else:
        return None
    fname = f"chat_{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(current_app.config['UPLOAD_FOLDER'], fname))
    return fname, ftype, original


def _mark_read(attendant_id):
    unread = Message.query.filter_by(attendant_id=attendant_id, read_at=None)\
        .filter(Message.sender_id != current_user.id).all()
    for m in unread:
        m.read_at = now_br()
    db.session.commit()


def _msg_dict(m, current_id):
    d = {
        'id':           m.id,
        'content':      m.content or '',
        'sender_name':  m.sender.name,
        'is_mine':      m.sender_id == current_id,
        'is_admin':     m.is_from_admin,
        'time':         m.created_at.strftime('%H:%M'),
        'file_type':    m.file_type,
        'file_name':    m.file_name,
        'original_name': m.original_name,
    }
    return d


# ── Hub do admin ───────────────────────────────────────────────────────────────

@chat_bp.route('/')
@login_required
def index():
    if current_user.is_admin():
        attendants = User.query.filter_by(role='attendant', is_active=True).order_by(User.name).all()
        unread_by = {}
        last_msg_by = {}
        for a in attendants:
            unread_by[a.id] = Message.query.filter_by(
                attendant_id=a.id, read_at=None
            ).filter(Message.sender_id == a.id).count()
            last_msg_by[a.id] = Message.query.filter_by(attendant_id=a.id)\
                .order_by(Message.created_at.desc()).first()
        return render_template('chat/admin_hub.html',
            attendants=attendants, unread_by=unread_by, last_msg_by=last_msg_by)
    return redirect(url_for('chat.room', attendant_id=current_user.id))


# ── Sala de chat ───────────────────────────────────────────────────────────────

@chat_bp.route('/sala/<int:attendant_id>')
@login_required
def room(attendant_id):
    if not current_user.is_admin() and current_user.id != attendant_id:
        flash('Acesso negado.', 'danger')
        return redirect(url_for('chat.index'))

    attendant = User.query.filter_by(id=attendant_id, role='attendant').first_or_404()
    messages  = Message.query.filter_by(attendant_id=attendant_id)\
        .order_by(Message.created_at).all()
    _mark_read(attendant_id)
    return render_template('chat/room.html', attendant=attendant, messages=messages)


# ── Enviar mensagem (texto ou arquivo) ────────────────────────────────────────

@chat_bp.route('/enviar', methods=['POST'])
@login_required
@csrf.exempt
def send():
    # Suporte a multipart/form-data (arquivo) e JSON (texto puro)
    if request.content_type and 'multipart' in request.content_type:
        content      = request.form.get('content', '').strip()
        attendant_id = int(request.form.get('attendant_id', 0))
        file_info    = _save_file(request.files.get('file'))
    else:
        data         = request.get_json(force=True, silent=True) or {}
        content      = data.get('content', '').strip()
        attendant_id = int(data.get('attendant_id', 0))
        file_info    = None

    if not attendant_id:
        return jsonify({'error': 'Dados inválidos'}), 400
    if not current_user.is_admin() and current_user.id != attendant_id:
        return jsonify({'error': 'Acesso negado'}), 403
    if not content and not file_info:
        return jsonify({'error': 'Mensagem vazia'}), 400

    msg = Message(
        sender_id    = current_user.id,
        attendant_id = attendant_id,
        content      = content or None,
        file_name    = file_info[0] if file_info else None,
        file_type    = file_info[1] if file_info else None,
        original_name= file_info[2] if file_info else None,
    )
    db.session.add(msg)
    db.session.commit()
    return jsonify(_msg_dict(msg, current_user.id))


# ── Polling ────────────────────────────────────────────────────────────────────

@chat_bp.route('/poll')
@login_required
def poll():
    attendant_id = request.args.get('attendant_id', type=int)
    after_id     = request.args.get('after', 0, type=int)
    if not attendant_id:
        return jsonify([])
    if not current_user.is_admin() and current_user.id != attendant_id:
        return jsonify([])

    msgs = Message.query.filter_by(attendant_id=attendant_id)\
        .filter(Message.id > after_id).order_by(Message.created_at).all()
    for m in msgs:
        if m.sender_id != current_user.id and not m.read_at:
            m.read_at = now_br()
    if msgs:
        db.session.commit()

    return jsonify([_msg_dict(m, current_user.id) for m in msgs])


# ── Servir arquivos do chat ────────────────────────────────────────────────────

@chat_bp.route('/arquivo/<path:filename>')
@login_required
def serve_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


# ── Badge não-lidas ────────────────────────────────────────────────────────────

@chat_bp.route('/nao-lidas')
@login_required
def unread_count():
    if current_user.is_admin():
        count = Message.query.filter(
            Message.read_at == None,
            Message.sender_id != current_user.id
        ).count()
    else:
        count = Message.query.filter_by(
            attendant_id=current_user.id, read_at=None
        ).filter(Message.sender_id != current_user.id).count()
    return jsonify({'count': count})
