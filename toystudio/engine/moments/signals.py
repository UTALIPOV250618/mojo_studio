"""Per-file signals the moment rules work on, and their loader (the only impure part)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from toystudio.core.models import (
    AudioEvent,
    MediaRow,
    QualityInterval,
    SpeechRegion,
)
from toystudio.engine.analysis.single_pass import outputs_for

MOTION_FPS = 4  # frame metrics are computed at 4 frames per second


@dataclass(frozen=True, eq=False)
class FileSignals:
    media_id: int
    duration_us: int
    speech: list[SpeechRegion]
    events: list[AudioEvent]
    motion_per_s: np.ndarray  # mean motion for each second of the file
    cuts_us: list[int]
    bad: list[QualityInterval]


class SignalStore(Protocol):
    """The slice of ProjectStore that load_signals needs (storage is a sibling layer)."""

    def list_media(self) -> list[MediaRow]: ...
    def speech_regions(self, media_id: int) -> list[SpeechRegion]: ...
    def audio_events(self, media_id: int) -> list[AudioEvent]: ...
    def quality_intervals(self, media_id: int) -> list[QualityInterval]: ...
    def scene_cuts(self, media_id: int) -> list[int]: ...


def motion_per_second(motion: np.ndarray, fps: int = MOTION_FPS) -> np.ndarray:
    """Mean of 4 fps motion samples for each (possibly partial) second."""
    m = np.asarray(motion, dtype=np.float64).ravel()
    if not len(m):
        return np.empty(0, dtype=np.float64)
    seconds = -(-len(m) // fps)
    padded = np.full(seconds * fps, np.nan)
    padded[: len(m)] = m
    return np.nanmean(padded.reshape(seconds, fps), axis=1)


def load_signals(store: SignalStore, cache: Path, media_id: int) -> FileSignals:
    media = next(m for m in store.list_media() if m.id == media_id)
    duration_us = media.info.duration_us if media.info is not None else 0
    npz = outputs_for(cache, media_id).frames
    motion = np.empty(0, dtype=np.float64)
    if npz.exists():
        with np.load(npz) as data:
            if "motion" in data.files:
                motion = motion_per_second(data["motion"])
    return FileSignals(
        media_id=media_id,
        duration_us=duration_us,
        speech=store.speech_regions(media_id),
        events=store.audio_events(media_id),
        motion_per_s=motion,
        cuts_us=store.scene_cuts(media_id),
        bad=store.quality_intervals(media_id),
    )
