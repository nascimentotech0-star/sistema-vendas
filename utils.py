from datetime import datetime, date, timedelta, timezone

# Fuso horario do Brasil (UTC-3, Brasilia/Sao Paulo)
BR_TZ = timezone(timedelta(hours=-3))


def now_br():
    """Retorna datetime atual no fuso horario do Brasil (UTC-3)."""
    return datetime.now(BR_TZ).replace(tzinfo=None)


def today_br():
    """Retorna date atual no fuso horario do Brasil (UTC-3)."""
    return datetime.now(BR_TZ).date()
