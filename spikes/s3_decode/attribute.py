"""S3: where does the time go? Decode-only vs +scale vs +x264 proxy at several presets.

    python -m spikes.s3_decode.attribute SRC [--seconds 60] [--hwaccel d3d11va]

Runs ffmpeg on the first --seconds of SRC with progressively more of the stage-1 graph and
writes spikes/results/s3/attrib-<os>-<name>-*.json. Argument lists only (no shell).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from spikes.common.media import tool
from spikes.common.results import write_result
from spikes.s3_decode.single_pass import probe_hwaccel, proxy_size_filter, source_info
from spikes.s3_decode.stats import x_realtime


def timed(cmd: list[str], media_s: float) -> dict[str, Any]:
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
    wall = time.perf_counter() - t0
    return {
        "wall_s": round(wall, 2),
        "x_rt": round(x_realtime(media_s, wall), 1),
        "rc": proc.returncode,
        "err": proc.stderr.strip()[-200:],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src", type=Path)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--hwaccel", default=None)
    ap.add_argument("--presets", default="ultrafast,superfast,veryfast")
    args = ap.parse_args(argv)
    info = source_info(args.src)
    sec = min(args.seconds, info.duration_s)
    hw_active: bool | None = None
    if args.hwaccel:
        hw_active, hw_format = probe_hwaccel(args.src, args.hwaccel)
        print(f"hwaccel {args.hwaccel}: active={hw_active} format={hw_format}")
    level = "warning" if args.hwaccel else "error"
    base = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", level, "-y"]
    if args.hwaccel:
        base += ["-hwaccel", args.hwaccel]
    base += ["-t", f"{sec:g}", "-i", str(args.src)]
    scale = proxy_size_filter(info.width, info.height)
    runs: dict[str, Any] = {
        "decode_only": timed([*base, "-an", "-f", "null", "-"], sec),
        "decode_scale540": timed([*base, "-an", "-vf", scale, "-f", "null", "-"], sec),
    }
    for preset in [p for p in args.presets.split(",") if p]:
        cmd = [*base, "-an", "-vf", scale, "-c:v", "libx264", "-preset", preset, "-crf", "26"]
        runs[f"proxy_x264_{preset}"] = timed([*cmd, "-f", "null", "-"], sec)
    for k, v in runs.items():
        print(f"{k:24s} {v['x_rt']:6.1f}x  ({v['wall_s']} s)")
    tag = "win" if sys.platform == "win32" else sys.platform
    hw = args.hwaccel or "cpu"
    write_result(
        "s3",
        f"attrib-{tag}-{args.src.stem}-{hw}",
        {"seconds": sec, "hw_active": hw_active, "runs": runs},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
