"""Shared test helpers."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from toystudio.core.models import US_PER_S, Track
from toystudio.core.timeline import Segment, Timeline


def tl3() -> Timeline:
    """Three 10 s video segments a/b/c (bounds +-5 s) and title t1 anchored to b."""
    video = tuple(
        Segment(
            id=sid,
            track=Track.VIDEO,
            media_id="m1",
            src_in=start * US_PER_S,
            src_out=(start + 10) * US_PER_S,
            moment_id=f"m{sid}",
            bound_in=(start - 5) * US_PER_S,
            bound_out=(start + 15) * US_PER_S,
        )
        for sid, start in (("a", 100), ("b", 200), ("c", 300))
    )
    title = Segment(
        id="t1",
        track=Track.TITLE,
        media_id="m1",
        src_in=1 * US_PER_S,
        src_out=4 * US_PER_S,
        moment_id=None,
        bound_in=0,
        bound_out=10 * US_PER_S,
        text="Тост отца",
        anchor_id="b",
    )
    return Timeline(video=video, titles=(title,))
