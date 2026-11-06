"""Human-readable formatting for RU/KY UI."""

from toystudio.core.models import US_PER_S

_GB = 1024**3
_MB = 1024**2

# lang -> (hours unit, minutes unit); KY forms to be confirmed with a native speaker.
_UNITS = {"ru": ("ч", "мин"), "ky": ("саат", "мүн")}


def format_timecode(us: int) -> str:
    total = us // US_PER_S
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def format_size(bytes_: int) -> str:
    if bytes_ >= _GB:
        return f"{bytes_ / _GB:.1f}".replace(".", ",") + " ГБ"
    return f"{round(bytes_ / _MB)} МБ"


def format_duration_words(us: int, lang: str) -> str:
    if lang not in _UNITS:
        raise ValueError(f"unsupported language: {lang}")
    h_unit, m_unit = _UNITS[lang]
    total_minutes = us // (60 * US_PER_S)
    hours, minutes = divmod(total_minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {h_unit}")
    if minutes or not hours:
        parts.append(f"{minutes} {m_unit}")
    return " ".join(parts)
