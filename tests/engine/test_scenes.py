"""Scene cut detection on synthetic per-frame metrics (no ffmpeg)."""

import numpy as np

from toystudio.engine.analysis.scenes import detect_cuts


def metrics(n, **over):  # n кадров, по умолчанию «нормальная» картинка
    base = {
        "sharpness": np.full(n, 100.0),
        "brightness": np.full(n, 120.0),
        "motion": np.full(n, 2.0),
        "shake": np.full(n, 0.5),
        "histdiff": np.full(n, 0.02),
    }
    base.update(over)
    return base


def test_detect_single_cut():
    h = np.full(80, 0.02)
    h[40] = 0.9
    mo = np.full(80, 2.0)
    mo[40] = 30.0
    cuts = detect_cuts(metrics(80, histdiff=h, motion=mo))
    assert [c[0] for c in cuts] == [10_000_000]


def test_flash_without_motion_is_not_cut():
    h = np.full(80, 0.02)
    h[40] = 0.9  # всплеск гистограммы без движения (вспышка) — не рез
    assert detect_cuts(metrics(80, histdiff=h)) == []


def test_cuts_closer_than_gap_keep_stronger():
    h = np.full(80, 0.02)
    h[40] = 0.8
    h[42] = 0.9  # 0,5 с между резами — меньше MIN_GAP_S=1 с
    mo = np.full(80, 2.0)
    mo[40] = mo[42] = 30.0
    cuts = detect_cuts(metrics(80, histdiff=h, motion=mo))
    assert [c[0] for c in cuts] == [10_500_000]  # остаётся более сильный (кадр 42)
    assert cuts[0][1] == 1.0  # score = histdiff[42] / max(histdiff)


def test_no_cuts_on_flat_metrics():
    assert detect_cuts(metrics(80)) == []
