"""Bad-frame intervals (stage 4): dark, blurry and shaky stretches of a clip."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from toystudio.core.models import QualityInterval, QualityLabel

# Предварительные пороги: калибруются в 2б на реальных тоях.
DARK_LUMA = 40.0 / 255.0  # метрика яркости 0..1 (metrics.mean_brightness): luma 40 из 255
BLUR_REL = 0.35  # резкость ниже BLUR_REL × медианы файла
SHAKE_PX = 3.0  # сдвиг фазовой корреляции на кадре 160 px
MIN_BAD_S = 1.0  # короче — отбрасываем
MERGE_GAP_S = 0.5  # разрыв между интервалами одной метки, который склеиваем

# Приоритет при пересечении: первая сработавшая метка побеждает.
_PRIORITY = (QualityLabel.DARK, QualityLabel.BLURRY, QualityLabel.SHAKY)


def _frame_labels(m: Mapping[str, np.ndarray]) -> list[QualityLabel]:
    brightness = np.asarray(m["brightness"], dtype=np.float64)
    sharpness = np.asarray(m["sharpness"], dtype=np.float64)
    shake = np.asarray(m["shake"], dtype=np.float64)
    blur_thr = BLUR_REL * float(np.median(sharpness))
    conds = {
        QualityLabel.DARK: brightness < DARK_LUMA,
        QualityLabel.BLURRY: sharpness < blur_thr,
        QualityLabel.SHAKY: shake > SHAKE_PX,
    }
    labels: list[QualityLabel] = []
    for i in range(brightness.size):
        labels.append(next((lbl for lbl in _PRIORITY if conds[lbl][i]), QualityLabel.CLEAR))
    return labels


def _runs(labels: list[QualityLabel]) -> dict[QualityLabel, list[tuple[int, int]]]:
    """Maximal (first, last) frame runs per bad label, inclusive."""
    runs: dict[QualityLabel, list[tuple[int, int]]] = {lbl: [] for lbl in _PRIORITY}
    start = 0
    for i in range(1, len(labels) + 1):
        if i == len(labels) or labels[i] != labels[start]:
            if labels[start] != QualityLabel.CLEAR:
                runs[labels[start]].append((start, i - 1))
            start = i
    return runs


def bad_intervals(
    media_id: int, m: Mapping[str, np.ndarray], fps: float = 4.0
) -> list[QualityInterval]:
    """Dark / blurry / shaky intervals in µs; frame i covers [i/fps, (i+1)/fps)."""
    labels = _frame_labels(m)
    if not labels:
        return []
    gap_frames = MERGE_GAP_S * fps
    min_frames = MIN_BAD_S * fps
    out: list[QualityInterval] = []
    for lbl, runs in _runs(labels).items():
        merged: list[list[int]] = []
        for a, b in runs:
            # Same-label neighbours separated by a short gap are one stretch.
            if merged and (a - merged[-1][1] - 1) < gap_frames:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        for a, b in merged:
            if (b - a + 1) < min_frames:
                continue
            out.append(
                QualityInterval(
                    media_id=media_id,
                    start_us=round(a / fps * 1_000_000),
                    end_us=round((b + 1) / fps * 1_000_000),
                    label=lbl,
                )
            )
    out.sort(key=lambda iv: (iv.start_us, iv.end_us))
    return out


def overall_label(intervals: list[QualityInterval], duration_us: int) -> QualityLabel:
    """Label covering more than 30 % of the duration, otherwise CLEAR."""
    if duration_us <= 0 or not intervals:
        return QualityLabel.CLEAR
    share: dict[QualityLabel, int] = {}
    for iv in intervals:
        share[iv.label] = share.get(iv.label, 0) + (iv.end_us - iv.start_us)
    best = max(share, key=lambda lbl: share[lbl])
    return best if share[best] / duration_us > 0.30 else QualityLabel.CLEAR
