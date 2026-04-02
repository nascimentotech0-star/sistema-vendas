from flask import Blueprint, jsonify, redirect, request, url_for
from flask_login import login_required, current_user
from models import db, Notification

notifications_bp = Blueprint('notifications', __name__)


@notifications_bp.route('/count')
@login_required
def count():
    n = Notification.query.filter_by(recipient_id=current_user.id, is_read=False).count()
    return jsonify({'count': n})


@notifications_bp.route('/recentes')
@login_required
def recent():
    items = (Notification.query
             .filter_by(recipient_id=current_user.id)
             .order_by(Notification.created_at.desc())
             .limit(8).all())
    return jsonify([{
        'id':       n.id,
        'title':    n.title,
        'body':     n.body or '',
        'link':     n.link or '',
        'icon':     n.icon or 'bi-bell-fill',
        'color':    n.color or '#a5b4fc',
        'is_read':  n.is_read,
        'time':     n.created_at.strftime('%d/%m %H:%M'),
    } for n in items])


@notifications_bp.route('/<int:nid>/ler', methods=['POST'])
@login_required
def mark_read(nid):
    n = Notification.query.filter_by(id=nid, recipient_id=current_user.id).first()
    if n:
        n.is_read = True
        db.session.commit()
        if n.link:
            return redirect(n.link)
    return redirect(request.referrer or url_for('admin.dashboard'))


@notifications_bp.route('/ler-todas', methods=['POST'])
@login_required
def mark_all_read():
    Notification.query.filter_by(
        recipient_id=current_user.id, is_read=False
    ).update({'is_read': True})
    db.session.commit()
    return jsonify({'ok': True})
