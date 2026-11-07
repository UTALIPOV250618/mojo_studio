"""Synthetic S3 sources: camera-like 1080p H.264 and an iPhone-like 4K HEVC 10-bit HLG.

Every picture carries a white box in the TOP-LEFT of the STORED frame, so orientation of
the proxy can be checked after the display-matrix rotation. Temporal noise lifts the
bitrate to camera-like values (testsrc2 alone decodes unrealistically fast).
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import numpy as np

from spikes.common.media import run_ffmpeg

MARKER = "drawbox=x=0:y=0:w=iw/2.5:h=ih/2.5:color=white:t=fill"
QUADRANTS = ("top-left", "top-right", "bottom-left", "bottom-right")


def _video_src(size: str, fps: str, duration_s: float, noise: int) -> str:
    return (
        f"testsrc2=size={size}:rate={fps}:duration={duration_s},noise=alls={noise}:allf=t,{MARKER}"
    )


def make_h264(
    path: Path, *, duration_s: float, fps: str, size: str = "1920x1080", mbps: int = 20
) -> Path:
    """Camera-like 1080p H.264 with stereo AAC audio."""
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", _video_src(size, fps, duration_s, 12) + ",format=yuv420p",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_s}",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{mbps}M",
            "-maxrate", f"{mbps * 6 // 5}M", "-bufsize", f"{mbps * 2}M",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", str(path),
        ]
    )  # fmt: skip
    return path


def make_hevc_hlg_rotated(
    path: Path, *, duration_s: float, fps: str = "30", size: str = "3840x2160", mbps: int = 45
) -> Path:
    """SYNTHETIC iPhone-like clip: 4K HEVC main10, BT.2020 / HLG tags, display rotation -90.

    The pixels are SDR test pattern labelled as HLG, only the tags and the codec path
    match a phone file. Stored landscape, displayed portrait (rotation -90 = 90 deg CW).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = path.with_suffix(".norot.mp4")
    x265 = (
        f"colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:"
        f"bitrate={mbps * 1000}:vbv-maxrate={mbps * 1200}:vbv-bufsize={mbps * 2000}:log-level=error"
    )
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", _video_src(size, fps, duration_s, 6) + ",format=yuv420p10le",
            "-f", "lavfi", "-i", f"sine=frequency=330:sample_rate=48000:duration={duration_s}",
            "-c:v", "libx265", "-preset", "ultrafast", "-x265-params", x265, "-tag:v", "hvc1",
            "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
            "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-shortest", str(raw),
        ]
    )  # fmt: skip
    run_ffmpeg(["-display_rotation", "-90", "-i", str(raw), "-c", "copy", str(path)])
    raw.unlink()
    return path


def expected_marker_quadrant(rotation: int) -> str:
    """Where the stored top-left marker lands after the display-matrix rotation.

    ffprobe's `rotation` is counter-clockwise degrees; -90 means rotate 90 deg clockwise
    to display (iPhone portrait): top-left -> top-right.
    """
    cw = (-rotation) % 360
    return {0: "top-left", 90: "top-right", 180: "bottom-right", 270: "bottom-left"}[cw]


def marker_quadrant(frame: np.ndarray) -> tuple[str, float]:
    """Brightest quadrant of a gray frame and its lead over the runner-up (0..1)."""
    h, w = frame.shape
    q = [
        frame[: h // 2, : w // 2],
        frame[: h // 2, w // 2 :],
        frame[h // 2 :, : w // 2],
        frame[h // 2 :, w // 2 :],
    ]
    means = np.array([float(x.mean()) for x in q]) / 255.0
    order = np.argsort(means)[::-1]
    return QUADRANTS[int(order[0])], float(means[order[0]] - means[order[1]])


def make_h264_xavc_like(
    path: Path, *, duration_s: float, fps: str = "50", size: str = "1920x1080", mbps: int = 80
) -> Path:
    """Harsh camera-like load: 1080p50, ~80 Mbit/s, long GOP, motion everywhere + noise.

    `scroll` moves the whole picture every frame (real motion compensation work, unlike a
    static pattern with noise); keyint = 1 s, B-frames on, like Sony XAVC S long GOP.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    video = (
        f"testsrc2=size={size}:rate={fps}:duration={duration_s},"
        "scroll=horizontal=0.013:vertical=0.007,noise=alls=22:allf=t+u,"
        f"{MARKER},format=yuv420p"
    )
    gop = int(float(Fraction(fps)))
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", video,
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration_s}",
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{mbps}M",
            "-maxrate", f"{mbps * 6 // 5}M", "-bufsize", f"{mbps * 2}M",
            "-g", str(gop), "-bf", "2",
            "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-shortest", str(path),
        ]
    )  # fmt: skip
    return path


def make_hlg_ramp(path: Path, *, duration_s: float = 3.0, size: str = "1920x1080") -> Path:
    """HLG test ramp: the SIGNAL goes 0..1 left to right (10-bit limited range), BT.2020/HLG tags.

    The pixel values ARE HLG-coded, so a correct HDR->SDR conversion must give a monotonic
    ramp from black to SDR white with highlight separation and no clipping plateau.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    x265 = "colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc:log-level=error"
    video = (
        f"color=c=black:s={size}:r=25:d={duration_s},format=yuv420p10le,"
        "geq=lum='64+876*X/W':cb=512:cr=512"
    )
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", video, "-f", "lavfi",
            "-i", f"sine=frequency=330:sample_rate=48000:duration={duration_s}",
            "-c:v", "libx265", "-preset", "ultrafast", "-crf", "12", "-x265-params", x265,
            "-tag:v", "hvc1", "-color_primaries", "bt2020", "-color_trc", "arib-std-b67",
            "-colorspace", "bt2020nc", "-c:a", "aac", "-shortest", str(path),
        ]
    )  # fmt: skip
    return path


def analyze_ramp(row: np.ndarray) -> dict[str, float | bool]:
    """Pixel-level SDR check of one proxy luma row of the HLG ramp (uint8, left to right)."""
    r = row.astype(np.float32)
    n = r.size
    sm = np.convolve(r, np.ones(5, dtype=np.float32) / 5, mode="valid")
    drops = int((np.diff(sm) < -1.5).sum())
    sep = float(r[int(0.93 * n) : int(0.97 * n)].mean() - r[int(0.83 * n) : int(0.87 * n)].mean())
    out: dict[str, float | bool] = {
        "p1": float(np.percentile(r, 1)),
        "p50": float(np.percentile(r, 50)),
        "p99": float(np.percentile(r, 99)),
        "clipped_frac": float((r >= 234).mean()),
        "black_level": float(r[: max(1, n // 50)].mean()),
        "monotonic_drops": float(drops),
        "highlight_separation": sep,
    }
    out["pass"] = bool(
        out["black_level"] <= 24
        and drops == 0
        and out["clipped_frac"] <= 0.02
        and sep >= 3.0
        and out["p99"] - out["p1"] >= 100
    )
    return out
