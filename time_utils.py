from datetime import datetime, timezone, timedelta

# Turkiye saati (UTC+3, sabit)
TR_TZ = timezone(timedelta(hours=3))


def tr_now() -> datetime:
    """Turkiye saatinde (UTC+3) anlik datetime."""
    return datetime.now(TR_TZ)


def tr_now_iso() -> str:
    """ISO format Turkiye saati (JSON'a kaydetmek icin)."""
    return tr_now().isoformat()


def tr_now_str(fmt: str = "%H:%M") -> str:
    """Turkiye saatinde formatlanmis string."""
    return tr_now().strftime(fmt)


def parse_iso_tr(s: str):
    """ISO string -> TR timezone'lu datetime. Naive ise TR varsayar."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=TR_TZ)
        return dt.astimezone(TR_TZ)
    except Exception:
        return None
