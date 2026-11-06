import pytest

from toystudio.core.format import format_duration_words, format_size, format_timecode


def test_timecode_hours() -> None:
    assert format_timecode(4_352_000_000) == "01:12:32"


def test_timecode_minutes() -> None:
    assert format_timecode(298_000_000) == "04:58"


def test_size_gb() -> None:
    assert format_size(9_019_431_321) == "8,4 ГБ"


def test_size_mb() -> None:
    assert format_size(671_088_640) == "640 МБ"


def test_duration_words_ru() -> None:
    assert format_duration_words(13_200_000_000, "ru") == "3 ч 40 мин"


def test_duration_words_ky() -> None:
    assert format_duration_words(13_200_000_000, "ky") == "3 саат 40 мүн"


def test_duration_words_partial() -> None:
    assert format_duration_words(3_600_000_000, "ru") == "1 ч"
    assert format_duration_words(2_400_000_000, "ky") == "40 мүн"


def test_duration_words_unknown_lang() -> None:
    with pytest.raises(ValueError):
        format_duration_words(1, "xx")
