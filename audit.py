"""Funções de auditoria — registra ações críticas na tabela audit_logs."""
from flask import request
from flask_login import current_user
from models import db, AuditLog
from utils import now_br


def log_action(action: str, description: str = '',
               target_type: str = None, target_id: int = None):
    """Registra uma ação do usuário atual no log de auditoria."""
    try:
        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip and ',' in ip:
            ip = ip.split(',')[0].strip()
        entry = AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            description=description[:1000] if description else '',
            ip_address=ip,
            created_at=now_br(),
        )
        db.session.add(entry)
        db.session.flush()   # não faz commit — deixa para a rota commitar junto
    except Exception:
        pass   # auditoria nunca deve quebrar a operação principal
