"""Core domain types."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

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


class MediaKind(StrEnum):
    VIDEO = "video"
    MUSIC = "music"


class MediaStatus(StrEnum):
    OK = "ok"
    CORRUPT = "corrupt"
    MISSING = "missing"


@dataclass(frozen=True)
class MediaInfo:
    duration_us: int
    width: int  # display size, after rotation
    height: int
    rotation: int
    fps: float
    codec: str
    hdr: bool
    has_audio: bool
    has_video: bool
    bit_rate: int
    creation_time: str | None


class StageName(StrEnum):
    PROBE = "probe"
    PROXY = "proxy"
    SPEECH = "speech"
    SCENES = "scenes"
    QUALITY = "quality"
    MOMENTS = "moments"
    SYNC = "sync"  # project-level stage, stored with media_id = 0


STAGE_ORDER: tuple[StageName, ...] = (
    StageName.PROBE,
    StageName.PROXY,
    StageName.SPEECH,
    StageName.SCENES,
    StageName.QUALITY,
    StageName.MOMENTS,
)
PROJECT_STAGES: tuple[StageName, ...] = (StageName.SYNC,)


class StageState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class StageRun:
    media_id: int
    stage: StageName
    state: StageState
    progress: float
    version: int
    error_code: str | None


class QualityLabel(StrEnum):
    CLEAR = "clear"
    DARK = "dark"
    SHAKY = "shaky"
    BLURRY = "blurry"


@dataclass(frozen=True)
class QualityInterval:
    media_id: int
    start_us: int
    end_us: int
    label: QualityLabel


@dataclass(frozen=True)
class MediaRow:
    id: int
    kind: MediaKind
    path: Path
    copied: bool
    size: int
    info: MediaInfo | None
    status: MediaStatus
    sync_offset_us: int | None
    sync_confidence: float | None
