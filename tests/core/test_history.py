"""Bounded undo/redo history over immutable timelines."""

import pytest

from tests.conftest import tl3
from toystudio.core.history import History
from toystudio.core.timeline import Timeline
from toystudio.core.timeline_ops import delete


def _states() -> tuple[Timeline, Timeline, Timeline]:
    a = tl3()
    b = delete(a, "a")
    c = delete(b, "b")
    return a, b, c


def test_current_before_push_raises() -> None:
    h = History()
    with pytest.raises(LookupError):
        _ = h.current


def test_push_makes_current_and_undo_walks_back() -> None:
    a, b, c = _states()
    h = History()
    h.push(a)
    h.push(b)
    h.push(c)
    assert h.current is c
    assert h.undo() is b
    assert h.undo() is a
    assert h.undo() is None
    assert h.current is a


def test_redo_returns_next_state() -> None:
    a, b, c = _states()
    h = History()
    h.push(a)
    h.push(b)
    h.push(c)
    h.undo()
    h.undo()
    assert h.redo() is b
    assert h.current is b


def test_push_after_undo_drops_redo() -> None:
    a, b, c = _states()
    h = History()
    h.push(a)
    h.push(b)
    h.push(c)
    h.undo()
    assert h.can_redo is True
    h.push(a)
    assert h.can_redo is False
    assert h.redo() is None


def test_can_undo_and_can_redo_flags() -> None:
    a, b, _ = _states()
    h = History()
    assert h.can_undo is False
    assert h.can_redo is False
    h.push(a)
    assert h.can_undo is False
    h.push(b)
    assert h.can_undo is True
    assert h.can_redo is False
    h.undo()
    assert h.can_undo is False
    assert h.can_redo is True


def test_limit_bounds_undo_depth() -> None:
    states = [Timeline(), tl3(), delete(tl3(), "a"), delete(tl3(), "b"), delete(tl3(), "c")]
    h = History(limit=3)
    for t in states:
        h.push(t)
    assert h.current is states[4]
    undos = 0
    while h.undo() is not None:
        undos += 1
    assert undos == 2
    assert h.current is states[2]


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        History(limit=0)
