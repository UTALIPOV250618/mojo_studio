"""Bad-frame intervals on synthetic per-frame metrics (no ffmpeg)."""

import numpy as np

from toystudio.core.models import QualityInterval, QualityLabel
from toystudio.engine.analysis.quality import bad_intervals, overall_label


def metrics(n, **over):  # n кадров, по умолчанию «нормальная» картинка
    base = {
        "sharpness": np.full(n, 100.0),
        "brightness": np.full(n, 120.0 / 255.0),
        "motion": np.full(n, 2.0),
        "shake": np.full(n, 0.5),
        "histdiff": np.full(n, 0.02),
    }
    base.update(over)
    return base


def test_dark_interval():
    b = np.full(40, 120.0 / 255.0)
    b[8:21] = 20.0 / 255.0  # кадры 8..20 тёмные
    out = bad_intervals(1, metrics(40, brightness=b))
    assert out == [QualityInterval(1, 2_000_000, 5_250_000, QualityLabel.DARK)]


def test_short_dark_ignored():
    b = np.full(40, 120.0 / 255.0)
    b[8:11] = 20.0 / 255.0  # 3 кадра = 0,75 с < MIN_BAD_S
    assert bad_intervals(1, metrics(40, brightness=b)) == []


def test_blurry_relative_to_median():
    s = np.full(40, 100.0)
    s[10:21] = 20.0 / 255.0  # 20 < 0,35 × медиана(100) → размыто; медиана файла остаётся 100
    out = bad_intervals(2, metrics(40, sharpness=s))
    assert out == [QualityInterval(2, 2_500_000, 5_250_000, QualityLabel.BLURRY)]

    s2 = np.full(40, 100.0)
    s2[10:21] = 50.0  # 50 > 35 → не размыто
    assert bad_intervals(2, metrics(40, sharpness=s2)) == []


def test_shaky_interval():
    sh = np.full(40, 0.5)
    sh[8:21] = 6.0  # сдвиг > SHAKE_PX = 3 px
    out = bad_intervals(1, metrics(40, shake=sh))
    assert [i.label for i in out] == [QualityLabel.SHAKY]


def test_dark_wins_over_blurry_and_shaky_on_overlap():
    b = np.full(40, 120.0 / 255.0)
    b[8:21] = 20.0 / 255.0
    s = np.full(40, 100.0)
    s[8:21] = 10.0
    sh = np.full(40, 0.5)
    sh[8:21] = 6.0
    out = bad_intervals(1, metrics(40, brightness=b, sharpness=s, shake=sh))
    assert [(i.label, i.start_us, i.end_us) for i in out] == [
        (QualityLabel.DARK, 2_000_000, 5_250_000)
    ]


def test_same_label_gap_under_half_second_is_merged():
    b = np.full(40, 120.0 / 255.0)
    b[8:13] = 20.0 / 255.0  # кадры 8..12
    b[14:21] = 20.0 / 255.0  # кадры 14..20; разрыв — один кадр 13 (0,25 с)
    out = bad_intervals(1, metrics(40, brightness=b))
    assert [(i.label, i.start_us, i.end_us) for i in out] == [
        (QualityLabel.DARK, 2_000_000, 5_250_000)
    ]


def test_overall_label_dark_when_mostly_dark():
    dur = 10_000_000
    mostly = [QualityInterval(1, 0, 6_000_000, QualityLabel.DARK)]  # 60 %
    assert overall_label(mostly, dur) == QualityLabel.DARK

    little = [QualityInterval(1, 0, 2_000_000, QualityLabel.DARK)]  # 20 %
    assert overall_label(little, dur) == QualityLabel.CLEAR

    assert overall_label([], dur) == QualityLabel.CLEAR
    assert overall_label(mostly, 0) == QualityLabel.CLEAR


def test_overall_label_picks_largest_share():
    dur = 10_000_000
    ivs = [
        QualityInterval(1, 0, 2_000_000, QualityLabel.DARK),  # 20 %
        QualityInterval(1, 2_000_000, 5_500_000, QualityLabel.SHAKY),  # 35 %
    ]
    assert overall_label(ivs, dur) == QualityLabel.SHAKY


def test_normal_brightness_in_metric_scale_is_not_dark():
    # metrics.mean_brightness returns 0..1: a typical 0.47 frame must never be DARK
    b = np.full(40, 0.47)
    assert bad_intervals(1, metrics(40, brightness=b)) == []
