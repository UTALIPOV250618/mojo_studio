"""S5: measure A/V sync, drift and frame count of an export.

    python -m spikes.s5_export.measure_sync OUT.mp4 [--manifest manifest.json] [--name NAME]
        [--no-write]

Pairs every detected flash with the nearest beep (delta = beep - flash), fits delta
over time (drift, ms/min), compares frame count with the plan, and also checks each
stream against where the EDL says the events should be. Writes
spikes/results/s5/<name>-<UTC>.json via write_result.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path
from typing import Any

from spikes.common.detect import detect_beeps, detect_flashes
from spikes.common.media import probe, tool
from spikes.common.results import write_result
from spikes.s5_export.plan import (
    US,
    dedupe_runs,
    drift_fit,
    errors_vs_expected,
    pair_nearest,
    sync_stats,
)

PAIR_MAX_US = 500_000  # events are 2 s apart in the source; cuts keep 0.1 s away
DEDUPE_US = 100_000


def count_video_packets(path: Path) -> int:
    cmd = [
        tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
        "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path),
    ]  # fmt: skip
    out = subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8")
    return int(out.stdout.strip().split(",")[0])


def _stream(info: dict[str, Any], kind: str) -> dict[str, Any]:
    for s in info["streams"]:
        if s.get("codec_type") == kind:
            return dict(s)
    return {}


def _vs_expected(errs: list[tuple[int, int]]) -> dict[str, Any]:
    """Per-stream error against the EDL: no quantisation noise, so its drift fit is
    much tighter than the raw flash/beep one."""
    e = [d for _, d in errs]
    if not e:
        return {"n": 0}
    return {
        "n": len(e),
        "max_abs_us": max(abs(x) for x in e),
        "mean_us": round(sum(e) / len(e)),
        "drift": asdict(drift_fit([t for t, _ in errs], e)),
    }


def measure(video: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    fps = Fraction(manifest["fps"])
    frame_us = round(US / fps)
    raw_flashes = detect_flashes(video)
    flashes = dedupe_runs(raw_flashes, DEDUPE_US)
    beeps = detect_beeps(video)
    pairs = pair_nearest(flashes, beeps, PAIR_MAX_US)
    stats = sync_stats(pairs)
    exp = manifest.get("expected_events", [])
    beep_err = errors_vs_expected(beeps, [e["beep_us"] for e in exp], PAIR_MAX_US)
    flash_err = errors_vs_expected(flashes, [e["flash_us"] for e in exp], PAIR_MAX_US)
    info = probe(video)
    v, a = _stream(info, "video"), _stream(info, "audio")
    frames = count_video_packets(video)
    expected_frames = int(manifest["expected_frames"])
    minute = 60 * US
    last_t = max((f for f, _ in pairs), default=0)
    first_min = [abs(b - f) for f, b in pairs if f < minute]
    last_min = [abs(b - f) for f, b in pairs if f >= last_t - minute]
    drift = stats.get("drift", {})
    return {
        "output": video.name,
        "fps": manifest["fps"],
        "frame_us": frame_us,
        "n_flash_frames": len(raw_flashes),
        "n_flashes": len(flashes),
        "n_beeps": len(beeps),
        "n_expected_events": len(exp),
        **stats,
        "max_abs_delta_first_min_us": max(first_min, default=None),
        "max_abs_delta_last_min_us": max(last_min, default=None),
        "beep_vs_expected": _vs_expected(beep_err),
        "flash_vs_expected": _vs_expected(flash_err),
        "frames": frames,
        "expected_frames": expected_frames,
        "expected_frames_nominal": manifest.get("expected_frames_nominal"),
        "frames_diff": frames - expected_frames,
        "video_duration_us": round(float(v.get("duration", 0)) * US),
        "audio_duration_us": round(float(a.get("duration", 0)) * US),
        "video_start_us": round(float(v.get("start_time", 0)) * US),
        "audio_start_us": round(float(a.get("start_time", 0)) * US),
        "pairs_us": [[f, b] for f, b in pairs],
        "verdict": {
            "sync_le_1_frame": stats.get("max_abs_delta_us", 10**9) <= frame_us,
            "drift_lt_1ms_per_min": abs(drift.get("slope_ms_per_min", 1e9)) < 1.0,
            "frames_match_pm1": abs(frames - expected_frames) <= 1,
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--manifest")
    ap.add_argument("--name", default="sync")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args(argv)
    video = Path(args.video)
    mpath = Path(args.manifest) if args.manifest else video.with_name("manifest.json")
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    result = measure(video, manifest)
    summary = {k: v for k, v in manifest.items() if k not in ("expected_events", "chunk_files")}
    data = {"manifest": summary, "sync": result}
    if not args.no_write:
        print(write_result("s5", args.name, data))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
