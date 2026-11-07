"""S3: the whole decode-throughput matrix in one command (Mac and the reference Windows PC).

    python -m spikes.s3_decode.bench_decode [--work spikes/data/s3] [--hwaccels none,d3d11va]
        [--only h264_1080p25,h264_1080p50,hevc_4k_hlg_rot] [--real label=path ...]

For every source x hwaccel x metrics mode (full = read pipe + compute, drain = read pipe and
discard, none = no pipe output) it runs `single_pass.run_single_pass`, verifies the proxy
(SDR tags, short side 540, no leftover rotation, marker in the right quadrant, wav 16 kHz mono)
and writes spikes/results/s3/<os>-<source>-<hw>-<mode>-*.json, then summary-<os>-*.json.
Sources are generated once under --work (gitignored). All paths go to ffmpeg as argument lists.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from spikes.common.media import probe, tool
from spikes.common.results import write_result
from spikes.s3_decode import sources
from spikes.s3_decode.single_pass import (
    RunStats,
    SourceInfo,
    run_single_pass,
    source_info,
    zscale_available,
)
from spikes.s3_decode.stats import (
    TARGET_X_RT,
    THRESHOLD_PEAK_MB,
    THRESHOLD_PYTHON_CPU_PCT,
    THRESHOLD_X_RT,
    analysis_minutes,
    median_of,
)

FOOTAGE_S = 3 * 3600 + 40 * 60  # §5.3 example: 3 h 40 min
MODES = ("full", "drain", "none")
SYNTH = ("h264_1080p25", "h264_1080p50", "hevc_4k_hlg_rot")
DEFAULT_HW = {"darwin": "none,videotoolbox", "win32": "none,d3d11va"}


def os_tag() -> str:
    return {"darwin": "darwin", "win32": "win"}.get(sys.platform, sys.platform)


def make_source(name: str, work: Path, dur_1080: float, dur_4k: float) -> Path:
    path = work / f"{name}.mp4"
    if path.exists():
        return path
    if name == "h264_1080p25":
        return sources.make_h264(path, duration_s=dur_1080, fps="25", mbps=20)
    if name == "h264_1080p50":
        return sources.make_h264(path, duration_s=dur_1080, fps="50", mbps=28)
    if name == "hevc_4k_hlg_rot":
        return sources.make_hevc_hlg_rotated(path, duration_s=dur_4k)
    raise ValueError(f"unknown synthetic source {name}")


def first_frame_gray(path: Path, w: int, h: int) -> np.ndarray:
    cmd = [tool("ffmpeg"), "-v", "error", "-i", str(path), "-frames:v", "1"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "gray", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w)


def verify_outputs(out_dir: Path, info: SourceInfo, *, marker: bool) -> dict[str, Any]:
    """Checks of proxy.mp4 / audio.wav against what the brief requires."""
    res: dict[str, Any] = {}
    pdata = probe(out_dir / "proxy.mp4")
    v = next(s for s in pdata["streams"] if s["codec_type"] == "video")
    w, h = int(v["width"]), int(v["height"])
    res.update(
        proxy_codec=v["codec_name"],
        proxy_size=[w, h],
        proxy_pix_fmt=v["pix_fmt"],
        proxy_trc=v.get("color_transfer"),
        proxy_primaries=v.get("color_primaries"),
        proxy_fps=v["avg_frame_rate"],
        proxy_rotation=any("rotation" in sd for sd in v.get("side_data_list", []) or []),
        proxy_mb=round(float(pdata["format"]["size"]) / 1e6, 2),
        proxy_short_side_540=min(w, h) == 540,
        proxy_sdr=v.get("color_transfer") in ("bt709", "smpte170m", None),
        proxy_aspect_ok=abs(w / h - info.width / info.height) < 0.01,
    )
    if marker:
        frame = first_frame_gray(out_dir / "proxy.mp4", w, h)
        found, lead = sources.marker_quadrant(frame)
        want = sources.expected_marker_quadrant(info.rotation)
        res.update(marker_found=found, marker_expected=want, marker_lead=round(lead, 3))
        res["orientation_ok"] = found == want
    res["proxy_mean_luma"] = round(float(first_frame_gray(out_dir / "proxy.mp4", w, h).mean()), 1)
    wav = out_dir / "audio.wav"
    if wav.exists():
        a = next(s for s in probe(wav)["streams"] if s["codec_type"] == "audio")
        res.update(
            wav_rate=int(a["sample_rate"]),
            wav_channels=int(a["channels"]),
            wav_ok=int(a["sample_rate"]) == 16000 and int(a["channels"]) == 1,
        )
    return res


def run_row(
    src: Path, work: Path, label: str, hw: str, mode: str, metric_fps: float, marker: bool
) -> dict[str, Any]:
    out = work / "runs" / f"{label}-{hw}-{mode}-{metric_fps:g}"
    shutil.rmtree(out, ignore_errors=True)
    info = source_info(src)
    stats: RunStats = run_single_pass(
        src, out, hwaccel=None if hw == "none" else hw, metrics=mode, metric_fps=metric_fps
    )
    row: dict[str, Any] = {
        "source": label,
        "hwaccel": hw,
        "mode": mode,
        "metric_fps": metric_fps,
        "info": {
            "size_displayed": [info.width, info.height],
            "rotation": info.rotation,
            "codec": info.codec,
            "pix_fmt": info.pix_fmt,
            "trc": info.trc,
            "fps": info.avg_fps,
            "bit_rate_mbps": round(info.bit_rate / 1e6, 1),
            "duration_s": round(info.duration_s, 2),
        },
        "stats": {k: v for k, v in vars(stats).items() if k != "command"},
        "command": stats.command,
    }
    if stats.returncode == 0 and (out / "proxy.mp4").exists():
        row["verify"] = verify_outputs(out, info, marker=marker)
    shutil.rmtree(out, ignore_errors=True)
    return row


def median_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["stats"]["returncode"] == 0]
    if not ok:
        return rows[0]
    target = median_of([r["stats"]["x_rt"] for r in ok])
    return min(ok, key=lambda r: abs(r["stats"]["x_rt"] - target))


def verdicts(full: dict[str, Any], none: dict[str, Any] | None) -> dict[str, Any]:
    s = full["stats"]
    v: dict[str, Any] = {
        "x_rt": round(s["x_rt"], 1),
        "ge_12x": s["x_rt"] >= THRESHOLD_X_RT,
        "ge_15x_target": s["x_rt"] >= TARGET_X_RT,
        "python_cpu_lt_50pct": s["python_cpu_pct"] < THRESHOLD_PYTHON_CPU_PCT,
        "peak_lt_1_5gb": s["total_peak_mb"] < THRESHOLD_PEAK_MB,
        "analysis_min_3h40": round(analysis_minutes(FOOTAGE_S, s["x_rt"]), 1),
    }
    if none is not None and none["stats"]["x_rt"] > 0:
        ratio = s["x_rt"] / none["stats"]["x_rt"]
        v["full_vs_nopipe_ratio"] = round(ratio, 3)
        v["pipe_does_not_stall"] = ratio >= 0.9
    return v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", type=Path, default=Path("spikes/data/s3"))
    ap.add_argument("--hwaccels", default=DEFAULT_HW.get(sys.platform, "none"))
    ap.add_argument("--only", default=",".join(SYNTH))
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--real", action="append", default=[], metavar="LABEL=PATH")
    ap.add_argument("--dur-1080", type=float, default=240.0, help="seconds of 1080p sources")
    ap.add_argument("--dur-4k", type=float, default=90.0, help="seconds of the 4K source")
    ap.add_argument("--metric-fps", type=float, default=4.0)
    ap.add_argument(
        "--stress-fps", type=float, default=25.0, help="extra CPU run at this fps; 0=off"
    )
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--prepare-only", action="store_true")
    args = ap.parse_args(argv)

    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)
    srcs: list[tuple[str, Path, bool]] = []
    for name in [n for n in args.only.split(",") if n]:
        print(f"[prepare] {name}", flush=True)
        srcs.append((name, make_source(name, work, args.dur_1080, args.dur_4k), True))
    for item in args.real:
        label, _, p = item.partition("=")
        srcs.append((label, Path(p), False))
    if args.prepare_only:
        return 0
    hws = [h for h in args.hwaccels.split(",") if h]
    modes = [m for m in args.modes.split(",") if m]
    tonemap = "zscale" if zscale_available() else "fallback"
    print(f"[env] tonemap path: {tonemap}", flush=True)

    summary: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for label, path, marker in srcs:
        for hw in hws:
            by_mode: dict[str, dict[str, Any]] = {}
            plan = [(m, args.metric_fps) for m in modes]
            if args.stress_fps > 0 and hw == "none" and "full" in modes:
                plan.append(("full", args.stress_fps))
            for mode, mfps in plan:
                reps = [
                    run_row(path, work, label, hw, mode, mfps, marker) for _ in range(args.repeat)
                ]
                row = median_row(reps)
                row["repeats_x_rt"] = [round(r["stats"]["x_rt"], 2) for r in reps]
                st = row["stats"]
                print(
                    f"[{label} {hw} {mode} {mfps:g}fps] {st['x_rt']:.1f}x  "
                    f"ffmpeg {st['ffmpeg_cpu_pct']:.0f}%  py {st['python_cpu_pct']:.1f}%  "
                    f"peak {st['total_peak_mb']:.0f} MB  rc={st['returncode']}",
                    flush=True,
                )
                if st["returncode"] != 0:
                    print("   " + " | ".join(st["stderr_tail"][-3:]), flush=True)
                write_result("s3", f"{os_tag()}-{label}-{hw}-{mode}-{mfps:g}fps", row)
                all_rows.append(row)
                if mfps == args.metric_fps:
                    by_mode[mode] = row
            if "full" in by_mode and by_mode["full"]["stats"]["returncode"] == 0:
                summary.append(
                    {
                        "source": label,
                        "hwaccel": hw,
                        "tonemap": by_mode["full"]["stats"]["tonemap"],
                        **verdicts(by_mode["full"], by_mode.get("none")),
                        "x_rt_by_mode": {
                            m: round(r["stats"]["x_rt"], 1) for m, r in by_mode.items()
                        },
                        "verify": by_mode["full"].get("verify"),
                    }
                )
    path = write_result(
        "s3",
        f"summary-{os_tag()}",
        {"tonemap_path": tonemap, "footage_s": FOOTAGE_S, "summary": summary},
    )
    print(f"[done] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
