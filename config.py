import os
from datetime import timedelta

# Carrega variáveis do .env se existir
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip())

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'deb1b966b804d0b37deeb60ac0a77e01535f6c39def3735f754bd7b27bd963fc')

    # Railway fornece DATABASE_URL com prefixo "postgres://" (antigo); SQLAlchemy exige "postgresql://"
    _db_url = os.environ.get('DATABASE_URL', 'sqlite:///vendas.db')
    if _db_url.startswith('postgres://'):
        _db_url = _db_url.replace('postgres://', 'postgresql://', 1)
    SQLALCHEMY_DATABASE_URI = _db_url

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Em produção (Railway), UPLOAD_FOLDER aponta para o Volume montado em /data/uploads
    UPLOAD_FOLDER = os.environ.get(
        'UPLOAD_FOLDER',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'uploads')
    )

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # token válido por 1 hora
    WTF_CSRF_SSL_STRICT = False  # Railway usa proxy, não força HTTPS no CSRF
    SESSION_COOKIE_SECURE = os.environ.get('RAILWAY_ENVIRONMENT') is not None
    SESSION_COOKIE_SAMESITE = 'Lax'
