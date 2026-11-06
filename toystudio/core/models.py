"""Core domain types."""

from dataclasses import dataclass
from enum import StrEnum

US_PER_S = 1_000_000


class MomentType(StrEnum):
    TOAST = "toast"
    DANCE = "dance"
    ENTRANCE = "entrance"
    GIFTS = "gifts"
    APPLAUSE = "applause"
    MUSIC = "music"


class Mark(StrEnum):
    NONE = "none"
    MUST = "must"
    EXCLUDE = "exclude"


class ToiKind(StrEnum):
    WEDDING = "wedding"
    TOI = "toi"
    JUBILEE = "jubilee"
    BESH_BARMAK = "besh_barmak"
    SUNNOT = "sunnot"
    KYZ_UZATUU = "kyz_uzatuu"
    OTHER = "other"


class Track(StrEnum):
    VIDEO = "video"
    MUSIC = "music"
    TITLE = "title"


class AudioMode(StrEnum):
    ORIGINAL = "original"
    MUSIC = "music"
    DUCK = "duck"


@dataclass(frozen=True)
class Reason:
    code: str
    ru: str
    ky: str


@dataclass(frozen=True)
class Moment:
    id: int
    media_id: int
    type: MomentType
    start: int
    end: int
    score: float
    mark: Mark = Mark.NONE

    def __post_init__(self) -> None:
        if self.end <= self.start:
            raise ValueError(f"Moment end ({self.end}) must be greater than start ({self.start})")

    @property
    def duration(self) -> int:
        return self.end - self.start
