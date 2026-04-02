import os
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
from flask import Flask
from flask_login import LoginManager, current_user
from models import db, User
from config import Config
from extensions import csrf, limiter

# ── Sentry — monitoramento de erros em produção ──────────────────────────────
_sentry_dsn = os.environ.get('SENTRY_DSN', '')
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.2,   # 20% das requisições para performance
        send_default_pii=False,   # não envia dados pessoais
        environment=os.environ.get('FLASK_ENV', 'production'),
    )


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    # Railway usa proxy reverso — necessário para HTTPS e CSRF funcionarem
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Faça login para acessar o sistema.'
    login_manager.login_message_category = 'warning'

    @login_manager.user_loader
    def load_user(user_id):
        return User.query.get(int(user_id))

    # Context processor global: badge de chat não-lidas para todos
    @app.context_processor
    def inject_globals():
        if current_user.is_authenticated:
            from models import Message, Renewal, Sale
            from sqlalchemy import func as _func
            from datetime import date as _date
            if current_user.is_admin():
                unread = Message.query.filter(
                    Message.read_at == None,
                    Message.sender_id != current_user.id
                ).count()
                overdue_count = Renewal.query.filter(
                    Renewal.status == 'pending',
                    Renewal.due_date < _date.today()
                ).count()
                # Hashes de comprovante que aparecem em mais de uma venda
                dup_sub = (
                    db.session.query(Sale.comprovante_hash)
                    .filter(Sale.comprovante_hash.isnot(None))
                    .group_by(Sale.comprovante_hash)
                    .having(_func.count(Sale.id) > 1)
                    .subquery()
                )
                fraud_count = db.session.query(_func.count()).select_from(dup_sub).scalar() or 0
            else:
                unread = Message.query.filter_by(
                    attendant_id=current_user.id, read_at=None
                ).filter(Message.sender_id != current_user.id).count()
                overdue_count = 0
                fraud_count   = 0
            return {
                'unread_chat_count':      unread,
                'renewals_overdue_count': overdue_count,
                'fraud_count':            fraud_count,
            }
        return {'unread_chat_count': 0, 'renewals_overdue_count': 0, 'fraud_count': 0}

    from routes.auth import auth_bp
    from routes.admin import admin_bp
    from routes.attendant import attendant_bp
    from routes.renewals import renewals_bp
    from routes.contacts import contacts_bp
    from routes.chat import chat_bp
    from routes.automations import automations_bp
    from routes.financial import financial_bp
    from routes.exports import exports_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp,        url_prefix='/admin')
    app.register_blueprint(attendant_bp,    url_prefix='/atendente')
    app.register_blueprint(renewals_bp,     url_prefix='/renovacoes')
    app.register_blueprint(contacts_bp,     url_prefix='/contatos')
    app.register_blueprint(chat_bp,         url_prefix='/chat')
    app.register_blueprint(automations_bp,  url_prefix='/automacoes')
    app.register_blueprint(financial_bp,    url_prefix='/financeiro')
    app.register_blueprint(exports_bp,      url_prefix='/exportar')

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    with app.app_context():
        db.create_all()
        _upgrade_db()   # primeiro: adiciona colunas novas
        _seed_admin()   # depois: usa o schema atualizado

    return app


def _upgrade_db():
    """Adiciona colunas novas sem quebrar banco existente (compatível com PostgreSQL)."""
    new_cols = [
        ('renewals',       'comprovante_filename', 'VARCHAR(255)'),
        ('messages',       'read_at',              'TIMESTAMP'),
        ('messages',       'file_name',            'VARCHAR(255)'),
        ('messages',       'file_type',            'VARCHAR(20)'),
        ('messages',       'original_name',        'VARCHAR(255)'),
        ('client_contacts','tag',                  'VARCHAR(30)'),
        ('client_contacts','event_type',           "VARCHAR(20) DEFAULT 'manual'"),
        ('price_items',    'screens',              'INTEGER DEFAULT 1'),
        ('price_items',    'period_label',         'VARCHAR(30)'),
        ('sales',          'screens',              'INTEGER DEFAULT 1'),
        ('sales',          'adjustment',           'REAL DEFAULT 0'),
        ('users',          'monthly_salary',       'REAL DEFAULT 0'),
        ('users',          'work_hours_per_day',   'INTEGER DEFAULT 8'),
        ('users',          'work_days_per_month',  'INTEGER DEFAULT 22'),
        ('users',          'shift_end_hour',        'INTEGER DEFAULT 22'),
        ('users',          'monthly_sales_target',  'INTEGER DEFAULT 700'),
        ('sales',          'comprovante_hash',       'VARCHAR(64)'),
        ('absence_records','notes',                'TEXT'),
        ('salary_payments','notes',                'TEXT'),
    ]
    for table, column, col_def in new_cols:
        # Cada ALTER TABLE em sua própria conexão isolada — evita aborto em cascata
        try:
            with db.engine.connect() as conn:
                conn.execute(db.text(
                    f'ALTER TABLE {table} ADD COLUMN {column} {col_def}'
                ))
                conn.commit()
        except Exception:
            pass  # Coluna já existe — ignorar
    _seed_default_plans()


def _seed_default_plans():
    """Insere os 6 planos padrão se ainda não existirem."""
    from models import PriceItem
    default_plans = [
        {'name': 'Plano 15 Dias — 1 Tela',    'price': 15.00,   'period_label': '15 dias', 'screens': 1, 'description': 'Acesso por 15 dias, 1 tela'},
        {'name': 'Plano Mensal — 1 Tela',      'price': 24.99,   'period_label': '1 mês',   'screens': 1, 'description': 'Acesso por 1 mês, 1 tela'},
        {'name': 'Plano Mensal — 2 Telas',     'price': 29.99,   'period_label': '1 mês',   'screens': 2, 'description': 'Acesso por 1 mês, 2 telas'},
        {'name': 'Plano Trimestral',           'price': 64.99,   'period_label': '3 meses', 'screens': 1, 'description': 'Acesso por 3 meses + 1 tela de brinde'},
        {'name': 'Plano Semestral',            'price': 124.99,  'period_label': '6 meses', 'screens': 1, 'description': 'Acesso por 6 meses + 1 tela de brinde'},
        {'name': 'Plano Anual',                'price': 244.99,  'period_label': '12 meses','screens': 1, 'description': 'Acesso por 12 meses + 1 tela de brinde + 1 mês extra'},
    ]
    for p in default_plans:
        exists = PriceItem.query.filter_by(name=p['name']).first()
        if not exists:
            db.session.add(PriceItem(
                name=p['name'], price=p['price'],
                period_label=p['period_label'], screens=p['screens'],
                description=p['description'], is_active=True
            ))
    db.session.commit()


def _seed_admin():
    if not User.query.filter_by(role='admin').first():
        admin = User(username='admin', name='Administrador', role='admin', is_active=True)
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()
        print("=" * 50)
        print("Admin criado  →  usuário: admin  |  senha: admin123")
        print("TROQUE A SENHA após o primeiro login!")
        print("=" * 50)


app = create_app()

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)
