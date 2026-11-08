"""Throughput / CPU arithmetic and a psutil resource sampler for the S3 runs."""

from __future__ import annotations

import statistics
import threading
from dataclasses import dataclass, field

import psutil  # type: ignore[import-untyped]

# §5.3 reference: 15x -> 3 h 40 min of footage in ~15 min.
THRESHOLD_X_RT = 12.0
TARGET_X_RT = 15.0
THRESHOLD_PYTHON_CPU_PCT = 50.0
THRESHOLD_PEAK_MB = 1536.0


def x_realtime(media_s: float, wall_s: float) -> float:
    """How many seconds of media are processed per wall second."""
    return media_s / wall_s if wall_s > 0 else 0.0


def cpu_load_pct(cpu_s: float, wall_s: float) -> float:
    """CPU time as % of ONE core (200 = two cores busy)."""
    return 100.0 * cpu_s / wall_s if wall_s > 0 else 0.0


def analysis_minutes(footage_s: float, x_rt: float) -> float:
    return footage_s / x_rt / 60.0 if x_rt > 0 else float("inf")


def fmt_hms(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def median_of(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def pick_cfr(avg_frame_rate: str) -> str:
    """Nearest standard frame rate (as an ffmpeg fraction) for a possibly VFR source."""
    num, _, den = avg_frame_rate.partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return "25"
    standard = {
        "24000/1001": 24000 / 1001,
        "24": 24.0,
        "25": 25.0,
        "30000/1001": 30000 / 1001,
        "30": 30.0,
        "50": 50.0,
        "60000/1001": 60000 / 1001,
        "60": 60.0,
    }
    return min(standard, key=lambda k: abs(standard[k] - fps))


@dataclass
class ResourceSampler:
    """Samples RSS of watched processes every `interval` s; keeps per-process and summed peaks."""

    procs: dict[str, psutil.Process]
    interval: float = 0.1
    peak_rss: dict[str, int] = field(default_factory=dict)
    peak_total: int = 0
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def _run(self) -> None:
        while not self._stop.is_set():
            total = 0
            for name, proc in self.procs.items():
                try:
                    mi = proc.memory_info()
                    # Windows: working set (rss) shrinks under pressure; `private` is committed
                    rss = max(mi.rss, getattr(mi, "private", 0))
                except psutil.Error:
                    continue
                self.peak_rss[name] = max(self.peak_rss.get(name, 0), rss)
                total += rss
            self.peak_total = max(self.peak_total, total)
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
