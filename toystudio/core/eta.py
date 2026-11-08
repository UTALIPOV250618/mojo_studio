"""Analysis time estimate: media-speed profiles, adaptive speed and remaining time."""

from toystudio.core.models import MediaInfo

_WARMUP_WALL_S = 60.0
_HFR_FPS = 31.0
_HEAVY_BIT_RATE = 40_000_000
_BIG_SHORT_SIDE = 1440

DEFAULT_X_RT: dict[str, float] = {"light": 8.0, "hfr_or_heavy": 4.0, "4k_hevc10": 0.7}


def profile_key(info: MediaInfo) -> str:
    short_side = min(info.width, info.height)
    if short_side >= _BIG_SHORT_SIDE or (info.codec == "hevc" and info.hdr):
        return "4k_hevc10"
    if info.fps > _HFR_FPS or info.bit_rate > _HEAVY_BIT_RATE:
        return "hfr_or_heavy"
    return "light"


class Eta:
    """Speed estimate (x realtime) that adapts to observed processing.

    Until at least 60 s of wall time is observed, `prior_x_rt` is used.
    On the observation that crosses 60 s, the EMA is seeded with the cumulative
    ratio; later observations use their own media/wall ratio.
    """

    def __init__(self, prior_x_rt: float, alpha: float = 0.2) -> None:
        self._x_rt = prior_x_rt
        self._alpha = alpha
        self._media_total = 0.0
        self._wall_total = 0.0
        self._started = False

    @property
    def x_rt(self) -> float:
        return self._x_rt

    def observe(self, media_s: float, wall_s: float) -> None:
        if wall_s <= 0:
            return
        self._media_total += media_s
        self._wall_total += wall_s
        if not self._started:
            if self._wall_total < _WARMUP_WALL_S:
                return
            self._started = True
            sample = self._media_total / self._wall_total
        else:
            sample = media_s / wall_s
        self._x_rt = (1 - self._alpha) * self._x_rt + self._alpha * sample

    def remaining_s(self, media_left_s: float) -> float:
        return media_left_s / self._x_rt


def project_remaining_s(items: list[tuple[str, float]], speeds: dict[str, float]) -> float:
    return sum(media_left / speeds[key] for key, media_left in items)


def over_target(items: list[tuple[MediaInfo, float]]) -> bool:
    return any(profile_key(info) == "4k_hevc10" for info, _ in items)
