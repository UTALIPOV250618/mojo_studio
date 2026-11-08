"""Camera sync: time offsets of files against a reference by audio envelope."""

import math

import numpy as np

from toystudio.core.models import US_PER_S, SyncResult

_GUARD_S = 0.5


def _prepare(env: np.ndarray, rate_hz: int) -> np.ndarray:
    x = np.log1p(np.asarray(env, dtype=np.float64))
    n = len(x)
    win = max(1, rate_hz)
    csum = np.concatenate([[0.0], np.cumsum(x)])
    idx = np.arange(n)
    lo = np.clip(idx - win // 2, 0, n)
    hi = np.clip(idx + win - win // 2, 0, n)
    mean = (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)
    x = x - mean
    std = x.std()
    return x / std if std > 0 else x


def estimate_offset(ref: np.ndarray, other: np.ndarray, rate_hz: int = 100) -> tuple[int, float]:
    """Return (offset_us, confidence): where `other` starts on the `ref` timeline."""
    a = _prepare(ref, rate_hz)
    b = _prepare(other, rate_hz)
    n_ref, n_other = len(a), len(b)
    if n_ref == 0 or n_other == 0:
        return 0, 0.0
    size = 1 << (n_ref + n_other - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
    # lags from -(n_other-1) to n_ref-1
    lags = np.concatenate([corr[size - (n_other - 1) :], corr[:n_ref]])
    first_lag = -(n_other - 1)
    peak = int(np.argmax(lags))
    guard = int(_GUARD_S * rate_hz)
    mask = np.ones(len(lags), dtype=bool)
    mask[max(0, peak - guard) : peak + guard + 1] = False
    second = float(lags[mask].max()) if mask.any() else float(lags.mean())
    std = float(lags.std())
    confidence = (float(lags[peak]) - second) / std if std > 0 else 0.0
    offset_us = round((peak + first_lag) * US_PER_S / rate_hz)
    return offset_us, confidence


def sync_project(
    envelopes: dict[int, np.ndarray | None],
    rate_hz: int = 100,
    min_confidence: float = 6.0,
    min_overlap_s: float = 30.0,
) -> list[SyncResult]:
    audio = {mid: env for mid, env in envelopes.items() if env is not None}
    results: dict[int, SyncResult] = {
        mid: SyncResult(mid, None, 0.0) for mid, env in envelopes.items() if env is None
    }
    if audio:
        ref_id = min(audio, key=lambda m: (-len(audio[m]), m))
        ref = audio[ref_id]
        results[ref_id] = SyncResult(ref_id, 0, math.inf)
        for mid, env in audio.items():
            if mid == ref_id:
                continue
            offset_us, conf = estimate_offset(ref, env, rate_hz)
            start = round(offset_us * rate_hz / US_PER_S)
            overlap_s = (min(len(ref), start + len(env)) - max(0, start)) / rate_hz
            ok = conf >= min_confidence and overlap_s >= min_overlap_s
            results[mid] = SyncResult(mid, offset_us if ok else None, conf)
    return [results[mid] for mid in sorted(results)]
