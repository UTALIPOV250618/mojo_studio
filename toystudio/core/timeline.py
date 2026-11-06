"""Immutable timeline model. Positions are computed from durations, never stored."""

from dataclasses import dataclass
from itertools import accumulate

from toystudio.core.models import AudioMode, Reason, Track

MIN_SEGMENT_US = 40_000  # one frame at 25 fps


@dataclass(frozen=True)
class Segment:
    """A piece of a track.

    For TITLE segments src_in/src_out are an offset/length window (in us) measured from the
    start of the anchor video segment, and bound_in/bound_out are 0..anchor duration.
    """

    id: str
    track: Track
    media_id: str
    src_in: int
    src_out: int
    moment_id: str | None
    bound_in: int
    bound_out: int
    locked: bool = False
    manual: bool = False
    audio_mode: AudioMode = AudioMode.ORIGINAL
    crop_x: float = 0.5
    reasons: tuple[Reason, ...] = ()
    text: str | None = None
    anchor_id: str | None = None

    def __post_init__(self) -> None:
        if not self.bound_in <= self.src_in < self.src_out <= self.bound_out:
            raise ValueError(
                f"Segment {self.id}: need bound_in <= src_in < src_out <= bound_out, got "
                f"{self.bound_in}, {self.src_in}, {self.src_out}, {self.bound_out}"
            )

    @property
    def duration(self) -> int:
        return self.src_out - self.src_in


@dataclass(frozen=True)
class Timeline:
    video: tuple[Segment, ...] = ()
    music: tuple[Segment, ...] = ()
    titles: tuple[Segment, ...] = ()

    def track_segments(self, track: Track) -> tuple[Segment, ...]:
        if track is Track.VIDEO:
            return self.video
        if track is Track.MUSIC:
            return self.music
        return self.titles

    def duration(self) -> int:
        """Total length of the video track (music and titles do not extend it)."""
        return sum(s.duration for s in self.video)

    def starts(self, track: Track) -> list[int]:
        segs = self.track_segments(track)
        return [0, *accumulate(s.duration for s in segs)][: len(segs)]

    def index_of(self, seg_id: str) -> tuple[Track, int]:
        for track in Track:
            for i, s in enumerate(self.track_segments(track)):
                if s.id == seg_id:
                    return track, i
        raise KeyError(seg_id)

    def segment(self, seg_id: str) -> Segment:
        track, i = self.index_of(seg_id)
        return self.track_segments(track)[i]

    def map_position(self, old: "Timeline", pos: int) -> int:
        """Translate a video position of `old` into this layout.

        Stays at the same offset inside the same segment; if that segment is gone,
        goes to the start of the next surviving segment (or the end).
        """
        old_starts = old.starts(Track.VIDEO)
        new_starts = self.starts(Track.VIDEO)
        new_index = {s.id: i for i, s in enumerate(self.video)}
        hit = len(old.video)
        for i, (seg, start) in enumerate(zip(old.video, old_starts, strict=True)):
            if pos < start + seg.duration:
                hit = i
                break
        if hit < len(old.video):
            seg = old.video[hit]
            if seg.id in new_index:
                j = new_index[seg.id]
                offset = max(0, min(pos - old_starts[hit], self.video[j].duration))
                return new_starts[j] + offset
        for seg in old.video[hit + 1 :]:
            if seg.id in new_index:
                return new_starts[new_index[seg.id]]
        return self.duration()
