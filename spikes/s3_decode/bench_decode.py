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
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np

from spikes.common.media import probe, tool
from spikes.common.results import RESULTS_DIR, write_result
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
SYNTH = ("h264_1080p25", "h264_1080p50", "h264_1080p50_xavc", "hevc_4k_hlg_rot")
DEFAULT_HW = {"darwin": "none,videotoolbox", "win32": "none,d3d11va"}


def os_tag() -> str:
    return {"darwin": "darwin", "win32": "win"}.get(sys.platform, sys.platform)


def make_source(name: str, work: Path, durs: dict[str, float]) -> Path:
    """Generate once; a file whose duration is off (interrupted generation or copy) is rebuilt."""
    path = work / f"{name}.mp4"
    want = durs["4k" if name.startswith("hevc") else "xavc" if "xavc" in name else "1080"]
    if path.exists():
        try:
            intact = abs(source_info(path).duration_s - want) < 1.0
        except Exception:  # noqa: BLE001 - unreadable file: rebuild
            intact = False
        if intact:
            return path
        print(f"[prepare] {path.name}: wrong duration, rebuilding", flush=True)
        path.unlink()
    if name == "h264_1080p25":
        return sources.make_h264(path, duration_s=want, fps="25", mbps=20)
    if name == "h264_1080p50":
        return sources.make_h264(path, duration_s=want, fps="50", mbps=28)
    if name == "h264_1080p50_xavc":
        return sources.make_h264_xavc_like(path, duration_s=want)
    if name == "hevc_4k_hlg_rot":
        return sources.make_hevc_hlg_rotated(path, duration_s=want)
    raise ValueError(f"unknown synthetic source {name}")


def first_frame_gray(path: Path, w: int, h: int, at_s: float = 0.0) -> np.ndarray:
    """Y plane of one frame exactly as stored (limited range 16..235, no range conversion)."""
    cmd = [tool("ffmpeg"), "-v", "error", "-ss", f"{at_s:g}", "-i", str(path), "-frames:v", "1"]
    cmd += ["-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
    raw = subprocess.run(cmd, check=True, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.uint8, count=w * h).reshape(h, w)


def verify_outputs(
    out_dir: Path, info: SourceInfo, *, marker: bool, frame_name: str
) -> dict[str, Any]:
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
        # TAGS only (setparams forces them): NOT evidence that the tone mapping is right.
        # None = not applicable (SDR source: ffmpeg leaves the tags unset).
        proxy_tags_sdr=(v.get("color_transfer") == "bt709") if info.hdr else None,
        proxy_aspect_ok=abs(w / h - info.width / info.height) < 0.01,
    )
    if marker:
        frame = first_frame_gray(out_dir / "proxy.mp4", w, h)
        found, lead = sources.marker_quadrant(frame)
        want = sources.expected_marker_quadrant(info.rotation)
        res.update(marker_found=found, marker_expected=want, marker_lead=round(lead, 3))
        res["orientation_ok"] = found == want
    mid = first_frame_gray(out_dir / "proxy.mp4", w, h, at_s=min(1.0, info.duration_s / 2))
    res["proxy_mean_luma"] = round(float(mid.mean()), 1)
    if info.hdr:  # luma stats + a JPEG for visual review (the real iPhone frame: judge by eye)
        res["proxy_luma_p1_p50_p99"] = [float(np.percentile(mid, q)) for q in (1, 50, 99)]
        res["proxy_clipped_frac"] = round(float((mid >= 234).mean()), 4)
        jpg = RESULTS_DIR / "s3" / "frames" / f"{frame_name}.jpg"
        jpg.parent.mkdir(parents=True, exist_ok=True)
        cmd = [tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{min(1.0, info.duration_s / 2):g}"]
        cmd += ["-i", str(out_dir / "proxy.mp4"), "-frames:v", "1", "-q:v", "5", str(jpg)]
        subprocess.run(cmd, check=False, capture_output=True)
        res["proxy_frame_jpg"] = str(jpg.relative_to(RESULTS_DIR.parent))
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
    src: Path,
    work: Path,
    label: str,
    hw: str,
    mode: str,
    metric_fps: float,
    marker: bool,
    threads: int = 0,
) -> dict[str, Any]:
    out = work / "runs" / f"{label}-{hw}-{mode}-{metric_fps:g}-t{threads}"
    shutil.rmtree(out, ignore_errors=True)
    info = source_info(src)
    stats: RunStats = run_single_pass(
        src,
        out,
        hwaccel=None if hw == "none" else hw,
        metrics=mode,
        metric_fps=metric_fps,
        threads=threads,
    )
    row: dict[str, Any] = {
        "source": label,
        "hwaccel": hw,
        "mode": mode,
        "metric_fps": metric_fps,
        "threads": threads,
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
        name = f"{os_tag()}-{label}-{hw}-t{threads}"
        row["verify"] = verify_outputs(out, info, marker=marker, frame_name=name)
    shutil.rmtree(out, ignore_errors=True)
    return row


def median_row(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["stats"]["returncode"] == 0]
    if not ok:
        return rows[0]
    target = median_of([r["stats"]["x_rt"] for r in ok])
    return min(ok, key=lambda r: abs(r["stats"]["x_rt"] - target))


def verdicts(
    full: dict[str, Any], none: dict[str, Any] | None, cpu_full: dict[str, Any] | None
) -> dict[str, Any]:
    s = full["stats"]
    info = full["info"]
    hw = full["hwaccel"] != "none"
    # The 12x / 15x threshold is defined for 1080p H.264 only.
    applies = info["codec"] == "h264" and info["size_displayed"][1] == 1080
    v: dict[str, Any] = {
        "x_rt": round(s["x_rt"], 1),
        "ge_12x": (s["x_rt"] >= THRESHOLD_X_RT) if applies else None,
        "ge_15x_target": (s["x_rt"] >= TARGET_X_RT) if applies else None,
        "python_cpu_lt_50pct": s["python_cpu_pct"] < THRESHOLD_PYTHON_CPU_PCT,
        "peak_lt_1_5gb": s["total_peak_mb"] < THRESHOLD_PEAK_MB,
        "analysis_min_3h40": round(analysis_minutes(FOOTAGE_S, s["x_rt"]), 1),
    }
    if hw:
        v["hw_active"] = bool(s.get("hw_active"))
        v["hw_format"] = s.get("hw_format")
        v["hw_fallback_warning"] = s.get("hw_fallback_warning", False)
        if cpu_full is not None:
            ratio = s["ffmpeg_cpu_pct"] / max(cpu_full["stats"]["ffmpeg_cpu_pct"], 1e-9)
            v["ffmpeg_cpu_vs_software"] = round(ratio, 2)
            v["hw_cpu_suspicious"] = ratio > 0.9  # hw decode should free CPU
        if not v["hw_active"]:  # a "GPU gain" from a silent software fallback is meaningless
            v["hw_verdict_valid"] = False
            for k in ("ge_12x", "ge_15x_target"):
                v[k] = None
        else:
            v["hw_verdict_valid"] = True
            if cpu_full is not None:
                v["hw_gain_x"] = round(s["x_rt"] / cpu_full["stats"]["x_rt"], 2)
    if none is not None and none["stats"]["x_rt"] > 0:
        ratio = s["x_rt"] / none["stats"]["x_rt"]
        v["full_vs_nopipe_ratio"] = round(ratio, 3)
        v["pipe_does_not_stall"] = ratio >= 0.9
    return v


def hdr_ramp_check(work: Path) -> dict[str, Any]:
    """Pixel-level SDR sanity of each available tonemap path on a known HLG-coded ramp."""
    ramp = work / "hlg_ramp.mp4"
    if not ramp.exists():
        sources.make_hlg_ramp(ramp)
    paths = ["fallback"] + (["zscale"] if zscale_available() else [])
    out: dict[str, Any] = {"zscale": "unavailable in this ffmpeg"} if len(paths) == 1 else {}
    for tm in paths:
        d = work / "runs" / f"ramp-{tm}"
        shutil.rmtree(d, ignore_errors=True)
        st = run_single_pass(ramp, d, metrics="none", tonemap=tm)
        if st.returncode != 0:
            out[tm] = {"error": st.stderr_tail[-3:]}
            continue
        vs = next(x for x in probe(d / "proxy.mp4")["streams"] if x["codec_type"] == "video")
        w, h = int(vs["width"]), int(vs["height"])
        frame = first_frame_gray(d / "proxy.mp4", w, h)
        rows = frame[h * 3 // 8 : h * 5 // 8].astype(np.float32).mean(axis=0)
        out[tm] = sources.analyze_ramp(rows)
        shutil.rmtree(d, ignore_errors=True)
    return out


def parse_only(text: str) -> list[str]:
    """`all` = every synthetic source, `none` = no synthetic source (never an empty argument:
    Windows PowerShell 5.1 drops empty strings when calling native programs)."""
    names = [n.strip() for n in text.split(",") if n.strip()]
    if names == ["all"]:
        return list(SYNTH)
    if names == ["none"]:
        return []
    unknown = [n for n in names if n not in SYNTH]
    if unknown:
        raise SystemExit(f"unknown source(s) {unknown}; use all, none or any of {list(SYNTH)}")
    return names


def read_real_list(path: Path) -> list[str]:
    """`label=path` per line, UTF-8 (avoids console code-page mangling of Cyrillic paths)."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", type=Path, default=Path("spikes/data/s3"))
    ap.add_argument("--hwaccels", default=DEFAULT_HW.get(sys.platform, "none"))
    ap.add_argument("--only", default="all", help="all | none | comma list of synthetic sources")
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--real", action="append", default=[], metavar="LABEL=PATH")
    ap.add_argument("--real-file", type=Path, help="UTF-8 text file with one LABEL=PATH per line")
    ap.add_argument("--dur-1080", type=float, default=240.0, help="seconds of 1080p sources")
    ap.add_argument("--dur-xavc", type=float, default=90.0, help="seconds of the 80 Mbit/s source")
    ap.add_argument("--dur-4k", type=float, default=90.0, help="seconds of the 4K source")
    ap.add_argument("--metric-fps", type=float, default=4.0)
    ap.add_argument(
        "--stress-fps", type=float, default=25.0, help="extra CPU run at this fps; 0=off"
    )
    ap.add_argument("--threads", type=int, default=0, help="cap ffmpeg threads (ASR-share proxy)")
    ap.add_argument("--repeat", type=int, default=1, help="use 3 on a laptop (thermal drift)")
    ap.add_argument("--no-ramp", action="store_true", help="skip the HLG ramp pixel check")
    ap.add_argument("--prepare-only", action="store_true")
    args = ap.parse_args(argv)

    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)
    durs = {"1080": args.dur_1080, "xavc": args.dur_xavc, "4k": args.dur_4k}
    srcs: list[tuple[str, Path, bool]] = []
    for name in parse_only(args.only):
        print(f"[prepare] {name}", flush=True)
        srcs.append((name, make_source(name, work, durs), True))
    real = [*args.real, *(read_real_list(args.real_file) if args.real_file else [])]
    for item in real:
        label, _, p = item.partition("=")
        srcs.append((label, Path(p), False))
    if args.prepare_only:
        return 0
    hws = [h for h in args.hwaccels.split(",") if h]
    modes = [m for m in args.modes.split(",") if m]
    tonemap = "zscale" if zscale_available() else "fallback"
    print(f"[env] tonemap path: {tonemap}", flush=True)
    ramp = None if args.no_ramp else hdr_ramp_check(work)
    if ramp is not None:
        print(f"[hdr ramp] {json.dumps(ramp)[:300]}", flush=True)
        write_result("s3", f"{os_tag()}-hdr-ramp", {"hdr_ramp": ramp})

    summary: list[dict[str, Any]] = []
    for label, path, marker in srcs:
        cpu_full: dict[str, Any] | None = None
        for hw in hws:
            plan = [(m, args.metric_fps) for m in modes]
            if args.stress_fps > 0 and hw == "none" and "full" in modes:
                plan.append(("full", args.stress_fps))
            reps: dict[tuple[str, float], list[dict[str, Any]]] = {k: [] for k in plan}
            for _ in range(args.repeat):  # modes interleaved per repeat: drift hits all alike
                for mode, mfps in plan:
                    reps[(mode, mfps)].append(
                        run_row(path, work, label, hw, mode, mfps, marker, args.threads)
                    )
            by_mode: dict[str, dict[str, Any]] = {}
            for (mode, mfps), rs in reps.items():
                row = median_row(rs)
                xs = [r["stats"]["x_rt"] for r in rs]
                row["repeats_x_rt"] = [round(x, 2) for x in xs]
                row["x_rt_min_max"] = [round(min(xs), 2), round(max(xs), 2)]
                st = row["stats"]
                hwinfo = f" hw_active={st['hw_active']}" if hw != "none" else ""
                print(
                    f"[{label} {hw} {mode} {mfps:g}fps t{args.threads}] {st['x_rt']:.1f}x "
                    f"(min {min(xs):.1f} max {max(xs):.1f}) ffmpeg {st['ffmpeg_cpu_pct']:.0f}% "
                    f"py {st['python_cpu_pct']:.1f}% peak {st['total_peak_mb']:.0f} MB "
                    f"rc={st['returncode']}{hwinfo}",
                    flush=True,
                )
                if st["returncode"] != 0:
                    print("   " + " | ".join(st["stderr_tail"][-3:]), flush=True)
                write_result(
                    "s3", f"{os_tag()}-{label}-{hw}-{mode}-{mfps:g}fps-t{args.threads}", row
                )
                if mfps == args.metric_fps:
                    by_mode[mode] = row
            full = by_mode.get("full")
            if hw == "none":
                cpu_full = full
            if full is not None and full["stats"]["returncode"] == 0:
                summary.append(
                    {
                        "source": label,
                        "hwaccel": hw,
                        "threads": args.threads,
                        "tonemap": full["stats"]["tonemap"],
                        **verdicts(full, by_mode.get("none"), cpu_full if hw != "none" else None),
                        "x_rt_by_mode": {
                            m: round(r["stats"]["x_rt"], 1) for m, r in by_mode.items()
                        },
                        "verify": full.get("verify"),
                    }
                )
    path = write_result(
        "s3",
        f"summary-{os_tag()}-t{args.threads}",
        {
            "tonemap_path": tonemap,
            "footage_s": FOOTAGE_S,
            "hdr_ramp": ramp,
            "summary": summary,
        },
    )
    print(f"[done] {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
