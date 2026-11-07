"""S3: ONE ffmpeg decode -> proxy 540p + wav 16 kHz mono + 160x90 gray frames in a pipe.

    python -m spikes.s3_decode.single_pass SRC --out-dir DIR [--hwaccel d3d11va]
        [--metrics full|drain|none] [--metric-fps 4] [--tonemap auto|zscale|fallback]

Python reads the raw frames from ffmpeg's stdout in bounded chunks and computes sharpness,
brightness, motion and shake (stage 1b); results go to DIR/frames.npz. `-progress` goes to
stderr and is parsed in a thread. Every ffmpeg call is an argument list (no shell), so
Cyrillic and space-containing paths are fine on Windows.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import psutil  # type: ignore[import-untyped]

from spikes.common.media import probe, tool
from spikes.s3_decode.metrics import FrameReader, MetricsAccumulator
from spikes.s3_decode.stats import ResourceSampler, cpu_load_pct, pick_cfr, x_realtime

try:
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]

PROGRESS_KEYS = {
    "frame", "fps", "bitrate", "total_size", "out_time_ms", "out_time",
    "dup_frames", "drop_frames", "speed", "progress",
}  # fmt: skip
HDR_TRCS = {"arib-std-b67", "smpte2084"}
PROXY_SHORT_SIDE = 540
METRIC_LONG, METRIC_SHORT = 160, 90

# zimg path: scale first (cheap), then linear light -> tonemap -> BT.709.
ZSCALE_TONEMAP = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)
# No zimg (e.g. Homebrew ffmpeg): treat the HLG signal as BT.709 gamma and only convert
# primaries / matrix. HLG is designed to look acceptable this way; no highlight roll-off.
FALLBACK_TONEMAP = "colorspace=all=bt709:iall=bt2020:fast=1,format=yuv420p"
SDR_TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709"


@dataclass
class SourceInfo:
    width: int  # displayed (after rotation)
    height: int
    rotation: int  # degrees as ffprobe prints it (display matrix); 0 if none
    avg_fps: str
    duration_s: float
    hdr: bool
    has_audio: bool
    codec: str
    pix_fmt: str
    trc: str
    bit_rate: int


def rotation_of(stream: dict[str, Any]) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return round(float(sd["rotation"]))
    tag = (stream.get("tags") or {}).get("rotate")
    return int(tag) if tag else 0


def source_info(path: Path) -> SourceInfo:
    data = probe(path)
    v = next(s for s in data["streams"] if s["codec_type"] == "video")
    rot = rotation_of(v)
    w, h = int(v["width"]), int(v["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    fmt = data.get("format", {})
    dur = float(fmt.get("duration") or v.get("duration") or 0)
    return SourceInfo(
        width=w,
        height=h,
        rotation=rot,
        avg_fps=v.get("avg_frame_rate", "25/1"),
        duration_s=dur,
        hdr=v.get("color_transfer") in HDR_TRCS,
        has_audio=any(s["codec_type"] == "audio" for s in data["streams"]),
        codec=v["codec_name"],
        pix_fmt=v.get("pix_fmt", ""),
        trc=v.get("color_transfer", ""),
        bit_rate=int(fmt.get("bit_rate") or 0),
    )


def proxy_size_filter(width: int, height: int, short_side: int = PROXY_SHORT_SIDE) -> str:
    """Scale so the SHORT side of the displayed picture is 540 (portrait 4K -> 540x960)."""
    if width >= height:
        return f"scale=-2:{short_side}"
    return f"scale={short_side}:-2"


def metric_size(width: int, height: int) -> tuple[int, int]:
    """(w, h) of the gray metric frames: 160x90, or 90x160 for portrait pictures."""
    return (METRIC_LONG, METRIC_SHORT) if width >= height else (METRIC_SHORT, METRIC_LONG)


def zscale_available() -> bool:
    proc = subprocess.run(
        [tool("ffmpeg"), "-hide_banner", "-filters"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return any(line.split()[1:2] == ["zscale"] for line in proc.stdout.splitlines())


def build_filter_graph(
    info: SourceInfo, *, metrics: bool, metric_fps: float, tonemap: str
) -> tuple[str, str]:
    """Returns (graph, tonemap_used). tonemap: 'zscale' | 'fallback' | 'none' (resolved)."""
    chain = [proxy_size_filter(info.width, info.height)]
    used = "none"
    if info.hdr:
        used = tonemap
        if tonemap == "zscale":
            chain.append(ZSCALE_TONEMAP)
        elif tonemap == "fallback":
            chain.append(FALLBACK_TONEMAP)
        if tonemap != "none":
            chain.append(SDR_TAGS)
    chain.append(f"fps=fps={pick_cfr(info.avg_fps)}")
    chain.append("format=yuv420p")
    head = "[0:v:0]" + ",".join(chain)
    if not metrics:
        return f"{head}[proxy]", used
    mw, mh = metric_size(info.width, info.height)
    tail = f"fps=fps={metric_fps:g},scale={mw}:{mh}:flags=area,format=gray"
    return f"{head},split=2[proxy][t];[t]{tail}[m]", used


def build_command(
    src: Path,
    out_dir: Path,
    info: SourceInfo,
    *,
    hwaccel: str | None = None,
    metrics: bool = True,
    metric_fps: float = 4.0,
    tonemap: str = "zscale",
    preset: str = "veryfast",
) -> tuple[list[str], str]:
    graph, used = build_filter_graph(info, metrics=metrics, metric_fps=metric_fps, tonemap=tonemap)
    gop = round(float(eval_fraction(pick_cfr(info.avg_fps))))
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostdin", "-nostats", "-loglevel", "error"]
    cmd += ["-progress", "pipe:2", "-y"]
    if hwaccel:
        cmd += ["-hwaccel", hwaccel]
    cmd += ["-i", str(src), "-filter_complex", graph]
    cmd += ["-map", "[proxy]", "-c:v", "libx264", "-preset", preset, "-crf", "26"]
    cmd += ["-g", str(gop), "-pix_fmt", "yuv420p", "-color_primaries", "bt709"]
    cmd += ["-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart"]
    cmd += [str(out_dir / "proxy.mp4")]
    if info.has_audio:
        cmd += ["-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
        cmd += [str(out_dir / "audio.wav")]
    if metrics:
        cmd += ["-map", "[m]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    return cmd, used


def eval_fraction(text: str) -> float:
    num, _, den = text.partition("/")
    return float(num) / float(den or 1)


def parse_progress_line(line: str) -> tuple[str, str] | None:
    key, sep, value = line.strip().partition("=")
    return (key, value) if sep and key and " " not in key else None


@dataclass
class RunStats:
    command: list[str]
    tonemap: str
    media_s: float
    wall_s: float
    x_rt: float
    returncode: int
    ffmpeg_cpu_s: float
    ffmpeg_cpu_pct: float
    python_cpu_s: float
    python_cpu_pct: float
    ffmpeg_peak_mb: float
    python_peak_mb: float
    total_peak_mb: float
    frames: int
    partial_bytes: int
    metric_compute_s: float
    pipe_wait_s: float
    out_time_s: float
    stderr_tail: list[str] = field(default_factory=list)


def run_single_pass(
    src: Path,
    out_dir: Path,
    *,
    hwaccel: str | None = None,
    metrics: str = "full",
    metric_fps: float = 4.0,
    tonemap: str = "auto",
    preset: str = "veryfast",
    chunk_frames: int = 64,
) -> RunStats:
    """metrics: 'full' (read + compute), 'drain' (read, discard), 'none' (no pipe output)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    info = source_info(src)
    if tonemap == "auto":
        tonemap = "zscale" if zscale_available() else "fallback"
    cmd, used = build_command(
        src,
        out_dir,
        info,
        hwaccel=hwaccel,
        metrics=metrics != "none",
        metric_fps=metric_fps,
        tonemap=tonemap,
        preset=preset,
    )
    tail: deque[str] = deque(maxlen=30)
    progress = {"out_time_us": 0}

    def drain_stderr(stream: Any) -> None:
        for raw in iter(stream.readline, ""):
            kv = parse_progress_line(raw)
            if kv and kv[0] == "out_time_us":
                try:
                    progress["out_time_us"] = int(kv[1])
                except ValueError:
                    pass
            elif kv is None or not (kv[0] in PROGRESS_KEYS or kv[0].startswith("stream_")):
                tail.append(raw.rstrip())

    r0 = resource.getrusage(resource.RUSAGE_CHILDREN) if resource is not None else None
    me = psutil.Process()
    cpu0 = me.cpu_times()
    t0 = time.perf_counter()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=False,
        bufsize=0,
    )
    err_thread = threading.Thread(
        target=drain_stderr,
        args=(
            io.TextIOWrapper(io.BufferedReader(proc.stderr), encoding="utf-8", errors="replace"),
        ),
        daemon=True,
    )
    err_thread.start()
    ff = psutil.Process(proc.pid)
    sampler = ResourceSampler({"ffmpeg": ff, "python": me})
    sampler.start()
    ff_cpu = 0.0
    sample_stop = threading.Event()

    def watch_cpu() -> None:
        nonlocal ff_cpu
        while not sample_stop.is_set():
            try:
                t = ff.cpu_times()
                ff_cpu = t.user + t.system
            except psutil.Error:
                return
            sample_stop.wait(0.2)

    cpu_thread = threading.Thread(target=watch_cpu, daemon=True)
    cpu_thread.start()

    acc = MetricsAccumulator(fps=metric_fps)
    reader: FrameReader | None = None
    assert proc.stdout is not None
    if metrics != "none":
        mw, mh = metric_size(info.width, info.height)
        reader = FrameReader(proc.stdout, mw, mh, chunk_frames)
        for chunk in reader.chunks():
            if metrics == "full":
                acc.update(chunk)
    rc = proc.wait()
    wall = time.perf_counter() - t0
    sample_stop.set()
    sampler.stop()
    err_thread.join(timeout=2)
    cpu_thread.join(timeout=1)
    cpu1 = me.cpu_times()
    py_cpu = (cpu1.user + cpu1.system) - (cpu0.user + cpu0.system)
    if resource is not None:  # exact CPU of the reaped child (POSIX); Windows keeps sampling
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        ff_cpu = (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime)

    if metrics == "full" and acc.frames:
        np.savez_compressed(out_dir / "frames.npz", **acc.result())
    media_s = info.duration_s
    mb = 1024 * 1024
    return RunStats(
        command=cmd,
        tonemap=used,
        media_s=media_s,
        wall_s=wall,
        x_rt=x_realtime(media_s, wall),
        returncode=rc,
        ffmpeg_cpu_s=ff_cpu,
        ffmpeg_cpu_pct=cpu_load_pct(ff_cpu, wall),
        python_cpu_s=py_cpu,
        python_cpu_pct=cpu_load_pct(py_cpu, wall),
        ffmpeg_peak_mb=sampler.peak_rss.get("ffmpeg", 0) / mb,
        python_peak_mb=sampler.peak_rss.get("python", 0) / mb,
        total_peak_mb=sampler.peak_total / mb,
        frames=reader.frames_read if reader else 0,
        partial_bytes=reader.partial_bytes if reader else 0,
        metric_compute_s=acc.compute_s,
        pipe_wait_s=reader.read_wait_s if reader else 0.0,
        out_time_s=progress["out_time_us"] / 1e6,
        stderr_tail=list(tail),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("src", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--hwaccel", default=None, help="d3d11va, cuda, videotoolbox, ...")
    ap.add_argument("--metrics", choices=("full", "drain", "none"), default="full")
    ap.add_argument("--metric-fps", type=float, default=4.0)
    ap.add_argument("--tonemap", choices=("auto", "zscale", "fallback", "none"), default="auto")
    ap.add_argument("--preset", default="veryfast")
    args = ap.parse_args(argv)
    stats = run_single_pass(
        args.src,
        args.out_dir,
        hwaccel=args.hwaccel,
        metrics=args.metrics,
        metric_fps=args.metric_fps,
        tonemap=args.tonemap,
        preset=args.preset,
    )
    d = asdict(stats)
    d.pop("command")
    print(json.dumps(d, ensure_ascii=False, indent=2))
    if stats.returncode != 0:
        print("ffmpeg failed:\n" + "\n".join(stats.stderr_tail), file=sys.stderr)
    return stats.returncode


if __name__ == "__main__":
    raise SystemExit(main())
