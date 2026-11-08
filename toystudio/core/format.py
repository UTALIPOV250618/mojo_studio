"""Human-readable formatting for RU/KY UI."""

import math
from typing import Literal

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


# ETA hour unit follows the brief ("дагы ~2 с"), which differs from _UNITS["ky"].
_ETA_TEXT: dict[str, tuple[str, str, str, str]] = {
    # (under a minute, prefix, minute unit, hour unit)
    "ru": ("меньше минуты", "ещё ~", "мин", "ч"),
    "ky": ("бир мүнөткө жетпей", "дагы ~", "мүн", "с"),
}


def format_eta(seconds: float, lang: Literal["ru", "ky"]) -> str:
    if lang not in _ETA_TEXT:
        raise ValueError(f"unsupported language: {lang}")
    under_minute, prefix, m_unit, h_unit = _ETA_TEXT[lang]
    if seconds < 60:
        return under_minute
    minutes = math.ceil(seconds / 60)
    if minutes < 60:
        return f"{prefix}{minutes} {m_unit}"
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        return f"{prefix}{hours} {h_unit}"
    return f"{prefix}{hours} {h_unit} {rest} {m_unit}"
