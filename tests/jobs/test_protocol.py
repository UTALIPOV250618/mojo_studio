import pytest

from toystudio.jobs.protocol import Done, Event, Failed, Progress, decode, encode


@pytest.mark.parametrize(
    "msg",
    [
        Progress("asr", 3, 10, "m1"),
        Progress("asr", 3, 10, None),
        Event("moment", {"a": 1}),
        Done(),
        Failed("boom", "detail"),
    ],
)
def test_roundtrip(msg):
    data = encode(msg)
    assert data.endswith(b"\n") and data.count(b"\n") == 1
    assert decode(data) == msg


def test_unknown_type():
    with pytest.raises(ValueError):
        decode(b'{"t":"zzz"}')


def test_malformed_json():
    with pytest.raises(ValueError):
        decode(b"not json")
