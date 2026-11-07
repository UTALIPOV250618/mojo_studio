"""Synthetic S3 sources: camera-like 1080p H.264 and an iPhone-like 4K HEVC 10-bit HLG.

Every picture carries a white box in the TOP-LEFT of the STORED frame, so orientation of
the proxy can be checked after the display-matrix rotation. Temporal noise lifts the
bitrate to camera-like values (testsrc2 alone decodes unrealistically fast).
"""

from __future__ import annotations

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
