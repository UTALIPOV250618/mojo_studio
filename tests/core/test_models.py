import pytest

from toystudio.core.models import (
    US_PER_S,
    AudioMode,
    Mark,
    Moment,
    MomentType,
    Reason,
    ToiKind,
    Track,
)


def test_moment_duration() -> None:
    m = Moment(1, 1, MomentType.TOAST, 10 * US_PER_S, 40 * US_PER_S, 0.9)
    assert m.duration == 30 * US_PER_S
    assert m.mark is Mark.NONE


@pytest.mark.parametrize("end", [10, 5])
def test_moment_invalid_range(end: int) -> None:
    with pytest.raises(ValueError):
        Moment(1, 1, MomentType.DANCE, 10, end, 0.5)


def test_enum_values() -> None:
    assert MomentType.GIFTS == "gifts"
    assert Track.VIDEO == "video"
    assert AudioMode.DUCK == "duck"
    assert ToiKind.KYZ_UZATUU == "kyz_uzatuu"
    assert Reason("c", "ру", "ky").code == "c"
