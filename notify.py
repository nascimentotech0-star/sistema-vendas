"""Helper para criar notificações internas."""
from models import db, Notification, User
from utils import now_br


def notify(recipient_id: int, title: str, body: str = '',
           link: str = None, icon: str = 'bi-bell-fill', color: str = '#a5b4fc'):
    """Cria uma notificação para o usuário `recipient_id`."""
    try:
        n = Notification(
            recipient_id=recipient_id,
            title=title,
            body=body,
            link=link,
            icon=icon,
            color=color,
            is_read=False,
            created_at=now_br(),
        )
        db.session.add(n)
        db.session.flush()
    except Exception:
        pass   # notificação nunca deve quebrar a operação principal


def notify_admins(title: str, body: str = '', link: str = None,
                  icon: str = 'bi-bell-fill', color: str = '#a5b4fc'):
    """Cria uma notificação para todos os admins e gerentes ativos."""
    try:
        recipients = User.query.filter(
            User.role.in_(['admin', 'gerente']),
            User.is_active == True
        ).all()
        for u in recipients:
            notify(u.id, title, body, link, icon, color)
    except Exception:
        pass
