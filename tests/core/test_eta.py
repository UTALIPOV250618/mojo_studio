import pytest

from toystudio.core.eta import DEFAULT_X_RT, Eta, over_target, profile_key, project_remaining_s
from toystudio.core.models import US_PER_S, MediaInfo


def info(
    width: int,
    height: int,
    fps: float,
    codec: str,
    bit_rate: int,
    hdr: bool = False,
) -> MediaInfo:
    return MediaInfo(
        duration_us=60 * US_PER_S,
        width=width,
        height=height,
        rotation=0,
        fps=fps,
        codec=codec,
        hdr=hdr,
        has_audio=True,
        has_video=True,
        bit_rate=bit_rate,
        creation_time=None,
    )


def test_profile_keys() -> None:
    assert profile_key(info(1920, 1080, 25.0, "h264", 20_000_000)) == "light"
    assert profile_key(info(1920, 1080, 50.0, "h264", 28_000_000)) == "hfr_or_heavy"
    assert profile_key(info(1920, 1080, 25.0, "h264", 80_000_000)) == "hfr_or_heavy"
    assert profile_key(info(2160, 3840, 30.0, "hevc", 45_000_000, hdr=True)) == "4k_hevc10"


def test_profile_4k_by_short_side_and_hevc_hdr_rule() -> None:
    # short side >= 1440 is 4k regardless of codec/HDR
    assert profile_key(info(2560, 1440, 25.0, "h264", 20_000_000)) == "4k_hevc10"
    # 1440 short side on portrait orientation is the same rule
    assert profile_key(info(1440, 2560, 25.0, "h264", 20_000_000)) == "4k_hevc10"
    # HEVC HDR below 1440 short side is also 4k_hevc10
    assert profile_key(info(1920, 1080, 25.0, "hevc", 20_000_000, hdr=True)) == "4k_hevc10"
    # HEVC without HDR below 1440 is not
    assert profile_key(info(1920, 1080, 25.0, "hevc", 20_000_000)) == "light"


def test_default_speeds() -> None:
    assert DEFAULT_X_RT == {"light": 8.0, "hfr_or_heavy": 4.0, "4k_hevc10": 0.7}


def test_eta_uses_prior_for_first_minute_then_ema() -> None:
    e = Eta(prior_x_rt=8.0)
    e.observe(media_s=300, wall_s=30)  # 10x, but < 60 s of wall time
    assert e.x_rt == 8.0
    e.observe(media_s=300, wall_s=40)  # 70 s of wall total -> EMA starts
    assert 8.0 < e.x_rt < 10.0
    assert e.x_rt == pytest.approx(0.8 * 8.0 + 0.2 * (600 / 70))
    assert e.remaining_s(media_left_s=600) == pytest.approx(600 / e.x_rt)


def test_eta_later_observations_use_their_own_ratio() -> None:
    e = Eta(prior_x_rt=8.0, alpha=0.5)
    e.observe(media_s=600, wall_s=60)  # cumulative 10x -> x = 0.5*8 + 0.5*10 = 9
    assert e.x_rt == pytest.approx(9.0)
    e.observe(media_s=120, wall_s=60)  # own ratio 2x -> x = 0.5*2 + 0.5*9 = 5.5
    assert e.x_rt == pytest.approx(5.5)


def test_project_remaining_s() -> None:
    speeds = {"light": 8.0, "4k_hevc10": 0.5}
    items = [("light", 80.0), ("4k_hevc10", 10.0)]
    assert project_remaining_s(items, speeds) == pytest.approx(10.0 + 20.0)


def test_over_target() -> None:
    light = info(1920, 1080, 25.0, "h264", 20_000_000)
    hevc = info(2160, 3840, 30.0, "hevc", 45_000_000, hdr=True)
    assert over_target([(light, 60.0)]) is False
    assert over_target([(light, 60.0), (hevc, 60.0)]) is True
