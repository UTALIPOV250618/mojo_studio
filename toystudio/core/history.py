"""Bounded undo/redo history of immutable timeline snapshots."""

from collections import deque

from toystudio.core.timeline import Timeline


class History:
    """A list of timeline states with a cursor; the oldest states drop past `limit`."""

    def __init__(self, limit: int = 200) -> None:
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        self._limit = limit
        self._states: deque[Timeline] = deque()
        self._cursor = -1

    @property
    def current(self) -> Timeline:
        if self._cursor < 0:
            raise LookupError("history is empty")
        return self._states[self._cursor]

    @property
    def can_undo(self) -> bool:
        return self._cursor > 0

    @property
    def can_redo(self) -> bool:
        return self._cursor < len(self._states) - 1

    def push(self, t: Timeline) -> None:
        """Make `t` current, discarding any redo states beyond the cursor."""
        while len(self._states) > self._cursor + 1:
            self._states.pop()
        if len(self._states) == self._limit:
            self._states.popleft()
        self._states.append(t)
        self._cursor = len(self._states) - 1

    def undo(self) -> Timeline | None:
        if not self.can_undo:
            return None
        self._cursor -= 1
        return self._states[self._cursor]

    def redo(self) -> Timeline | None:
        if not self.can_redo:
            return None
        self._cursor += 1
        return self._states[self._cursor]
