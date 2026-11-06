"""S5: export an EDL the way docs/architecture.md §9 describes, or as one big filter_complex.

    python -m spikes.s5_export.build_chunks EDL.json --sources DIR --music FILE \
        --preset youtube1080 --fps 25 --encoder libx264 --chunk-s 30 --out DIR [--cache DIR]
        [--mode chunked|single] [--naive] [--jobs N]

chunked (default, §9):
  1. pieces = EDL segments quantised to whole output frames; chunks of ~chunk-s;
  2. each chunk = one ffmpeg call, `-ss in -t dur -i src` per piece, fps/scale/pad/
     setsar/format, fixed encoder settings, closed GOP; file name = hash of the input;
     an existing file is reused;
  3. audio = ONE pass over all segments (atrim + 10 ms afade at seams + looped music,
     loudnorm -14 LUFS) -> WAV;
  4. concat demuxer `-c copy` + WAV -> AAC 192k, +faststart.
single: the same graph in one ffmpeg call, encoded straight to the output.
--naive: no frame quantisation (video `-t dur`, audio exact dur) to show why it matters.

`--encoder auto` probes h264_nvenc -> h264_qsv -> h264_amf -> h264_videotoolbox ->
libx264 with a test frame (§9 p.5). `--list-encoders` prints what works here.
Writes <out>/export.mp4 and <out>/manifest.json (pieces, expected events, timings).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path
from typing import Any

from spikes.common.media import SAMPLE_RATE, tool
from spikes.s5_export.plan import (
    US,
    Piece,
    chunk_key,
    expected_events,
    frame_time_us,
    plan_chunks,
    plan_chunks_local,
    quantise,
)

PRESETS: dict[str, dict[str, Any]] = {
    "youtube1080": {"w": 1920, "h": 1080, "bitrate": "12M", "maxrate": "16M", "bufsize": "24M"},
}
ENCODER_ORDER = ["h264_nvenc", "h264_qsv", "h264_amf", "h264_videotoolbox", "libx264"]
MUSIC_GAIN = 0.25
FADE_S = 0.01
INPUT_PAD_S = 0.5  # read a little past the piece; trim=end_frame cuts exactly


def encoder_args(name: str, preset: dict[str, Any], fps: Fraction) -> list[str]:
    gop = str(round(2 * fps))
    rate = ["-b:v", preset["bitrate"], "-maxrate", preset["maxrate"], "-bufsize", preset["bufsize"]]
    common = ["-g", gop, "-pix_fmt", "yuv420p", "-profile:v", "high"]
    if name == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", *rate, *common, "-flags", "+cgop"]
    if name == "h264_videotoolbox":
        return ["-c:v", name, *rate, *common, "-allow_sw", "0", "-realtime", "0"]
    if name == "h264_nvenc":
        return ["-c:v", name, "-preset", "p4", "-rc", "vbr", *rate, *common, "-forced-idr", "1"]
    if name == "h264_qsv":
        return ["-c:v", name, "-preset", "medium", *rate, *common]
    if name == "h264_amf":
        return ["-c:v", name, "-quality", "balanced", "-rc", "vbr_peak", *rate, *common]
    raise ValueError(f"unknown encoder {name}")


def probe_encoder(name: str) -> bool:
    """§9 p.5: encode one test frame; success = the encoder works on this machine."""
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=black:s=640x360:r=25:d=0.2",
        "-frames:v", "1", *encoder_args(name, PRESETS["youtube1080"], Fraction(25)),
        "-f", "null", "-",
    ]  # fmt: skip
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def detect_encoders() -> list[str]:
    return [e for e in ENCODER_ORDER if probe_encoder(e)]


def run(cmd: list[str]) -> float:
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({cmd[:6]}...): {proc.stderr.strip()[-2000:]}")
    return time.perf_counter() - t0


def _s(us: int) -> str:
    return f"{us / US:.6f}"


def _ffmpeg() -> list[str]:
    return [tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-nostdin"]


# ------------------------------------------------------------------ graph parts


def video_inputs(
    pieces: list[Piece], src: dict[str, Path], fps: Fraction, naive: bool
) -> list[str]:
    """`-ss in -t dur -i src` per piece. In naive mode Piece.frames holds dur_us."""
    args: list[str] = []
    for p in pieces:
        t = _s(p.frames) if naive else _s(frame_time_us(p.frames, fps) + round(INPUT_PAD_S * US))
        args += ["-ss", _s(p.in_us), "-t", t, "-i", str(src[p.src])]
    return args


def video_chain(i: int, p: Piece, preset: dict[str, Any], fps: Fraction, naive: bool) -> str:
    w, h = preset["w"], preset["h"]
    trim = "" if naive else f",trim=end_frame={p.frames}"
    return (
        f"[{i}:v]fps={fps}:start_time=0,"
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
        f"{trim},setpts=PTS-STARTPTS[v{i}]"
    )


def video_graph(pieces: list[Piece], preset: dict[str, Any], fps: Fraction, naive: bool) -> str:
    parts = [video_chain(i, p, preset, fps, naive) for i, p in enumerate(pieces)]
    labels = "".join(f"[v{i}]" for i in range(len(pieces)))
    parts.append(f"{labels}concat=n={len(pieces)}:v=1:a=0[vout]")
    return ";\n".join(parts)


def audio_inputs(segs: list[dict[str, Any]], src: dict[str, Path], music: Path) -> list[str]:
    args: list[str] = []
    for s in segs:
        t = _s(int(s["dur_us"]) + round(INPUT_PAD_S * US))
        args += ["-ss", _s(int(s["in_us"])), "-t", t, "-vn", "-i", str(src[s["src"]])]
    args += ["-stream_loop", "-1", "-i", str(music)]
    return args


def audio_graph(segs: list[dict[str, Any]], first_input: int, total_us: int) -> str:
    """segs: [{src, in_us, dur_us}] with dur_us already the exported length."""
    parts = []
    for k, s in enumerate(segs):
        d = int(s["dur_us"]) / US
        parts.append(
            f"[{first_input + k}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
            f"asetpts=PTS-STARTPTS,atrim=end_sample={round(d * SAMPLE_RATE)},"
            f"afade=t=in:d={FADE_S},afade=t=out:st={d - FADE_S:.6f}:d={FADE_S}[a{k}]"
        )
    labels = "".join(f"[a{k}]" for k in range(len(segs)))
    music = first_input + len(segs)
    parts.append(f"{labels}concat=n={len(segs)}:v=0:a=1[voice]")
    parts.append(
        f"[{music}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
        f"volume={MUSIC_GAIN},atrim=end_sample={round(total_us / US * SAMPLE_RATE)}[music]"
    )
    parts.append(
        "[voice][music]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I=-14:TP=-1.5:LRA=11,aresample={SAMPLE_RATE}[aout]"
    )
    return ";\n".join(parts)


def audio_segments(
    edl: dict[str, Any], pieces: list[Piece], fps: Fraction, naive: bool
) -> list[dict[str, Any]]:
    """Audio follows the same (quantised) durations as the video."""
    if naive:
        return [dict(s) for s in edl["segments"]]
    return [
        {"src": p.src, "in_us": p.in_us, "dur_us": frame_time_us(p.frames, fps)} for p in pieces
    ]


def concat_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


# ---------------------------------------------------------------------- export


def source_identity(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def build(args: argparse.Namespace) -> dict[str, Any]:
    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    fps = Fraction(args.fps)
    preset = {**PRESETS[args.preset], "fps": str(fps)}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache) if args.cache else out / "chunks"
    cache.mkdir(parents=True, exist_ok=True)
    srcdir = Path(args.sources)
    src = {k: srcdir / v["file"] for k, v in edl["sources"].items()}
    music = Path(args.music)
    encoder = args.encoder
    if encoder == "auto":
        found = detect_encoders()
        if not found:
            raise RuntimeError("no working H.264 encoder")
        encoder = found[0]
    enc = encoder_args(encoder, preset, fps)
    mux_rate = ["-video_track_timescale", "90000"]

    if args.naive:
        # Raw durations: Piece.frames carries dur_us; video `-t dur`, audio exact dur.
        pieces = [
            Piece(i, s["src"], int(s["in_us"]), int(s["dur_us"]))
            for i, s in enumerate(edl["segments"])
        ]
        qpieces = quantise(edl["segments"], fps)
        total_us = sum(int(s["dur_us"]) for s in edl["segments"])
    else:
        pieces = quantise(edl["segments"], fps)
        qpieces = pieces
        total_us = frame_time_us(sum(p.frames for p in pieces), fps)
    asegs = audio_segments(edl, qpieces, fps, args.naive)
    final = out / "export.mp4"
    timings: dict[str, float] = {}
    info: dict[str, Any] = {}
    t_all = time.perf_counter()

    if args.mode == "single":
        n = len(pieces)
        graph = (
            video_graph(pieces, preset, fps, args.naive) + ";\n" + audio_graph(asegs, n, total_us)
        )
        gfile = out / "graph.txt"
        gfile.write_text(graph, encoding="utf-8")
        cmd = [
            *_ffmpeg(), *video_inputs(pieces, src, fps, args.naive), *audio_inputs(asegs, src, music),
            "-/filter_complex", str(gfile), "-map", "[vout]", "-map", "[aout]",
            *enc, "-fps_mode", "cfr", "-r", str(fps), "-c:a", "aac", "-b:a", "192k",
            *mux_rate, "-movflags", "+faststart", str(final),
        ]  # fmt: skip
        info["cmdline_chars"] = len(subprocess.list2cmdline(cmd))
        timings["single_s"] = run(cmd)
    else:
        ids = {k: source_identity(p) for k, p in src.items()}
        if args.naive:
            # chunk on raw durations: reuse planner with a fake 1-us "fps"
            chunks = plan_chunks(pieces, Fraction(US), args.chunk_s)
        else:
            if args.plan == "local":
                chunks = plan_chunks_local(pieces, fps, args.chunk_s, ids)
            else:
                chunks = plan_chunks(pieces, fps, args.chunk_s)
        jobs: list[tuple[Path, list[str]]] = []
        files: list[Path] = []
        chunk_frames: list[int] = []
        for ch in chunks:
            key = chunk_key(ch, ids, {**preset, "naive": args.naive}, enc)
            f = cache / f"{key}.mp4"
            files.append(f)
            chunk_frames.append(sum(p.frames for p in ch) if not args.naive else -1)
            if f.exists():
                continue
            gfile = cache / f"{key}.graph.txt"
            gfile.write_text(video_graph(ch, preset, fps, args.naive), encoding="utf-8")
            tmp = cache / f"{key}.part.mp4"
            cmd = [
                *_ffmpeg(), *video_inputs(ch, src, fps, args.naive),
                "-/filter_complex", str(gfile), "-map", "[vout]",
                *enc, "-fps_mode", "cfr", "-r", str(fps), *mux_rate, str(tmp),
            ]  # fmt: skip
            jobs.append((f, cmd))
        info["chunks_total"] = len(chunks)
        info["chunks_rendered"] = len(jobs)
        info["chunks_reused"] = len(chunks) - len(jobs)
        info["chunk_frames"] = chunk_frames
        info["chunk_files"] = [f.name for f in files]

        def render(job: tuple[Path, list[str]]) -> float:
            f, cmd = job
            dt = run(cmd)
            Path(cmd[-1]).replace(f)
            return dt

        wav = out / "audio.wav"
        agraph = out / "audio_graph.txt"
        agraph.write_text(audio_graph(asegs, 0, total_us), encoding="utf-8")
        acmd = [
            *_ffmpeg(), *audio_inputs(asegs, src, music), "-/filter_complex", str(agraph),
            "-map", "[aout]", "-c:a", "pcm_s16le", str(wav),
        ]  # fmt: skip
        info["audio_cmdline_chars"] = len(subprocess.list2cmdline(acmd))

        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as ex:
            audio_job = ex.submit(run, acmd) if args.parallel_audio else None
            with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as vex:
                per_chunk = list(vex.map(render, jobs))
            timings["chunks_s"] = time.perf_counter() - t0
            if audio_job is not None:
                timings["audio_s"] = audio_job.result()
        timings["per_chunk_s"] = per_chunk  # type: ignore[assignment]
        if audio_job is None:
            timings["audio_s"] = run(acmd)
        timings["video_and_audio_s"] = time.perf_counter() - t0

        lst = out / "chunks.ffconcat"
        lst.write_text(
            "ffconcat version 1.0\n" + "\n".join(concat_line(f) for f in files) + "\n",
            encoding="utf-8",
        )
        mcmd = [
            *_ffmpeg(), "-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(wav),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            *mux_rate, "-movflags", "+faststart", str(final),
        ]  # fmt: skip
        timings["mux_s"] = run(mcmd)
    timings["total_s"] = time.perf_counter() - t_all

    manifest = {
        "edl": str(Path(args.edl).name),
        "mode": args.mode,
        "naive": args.naive,
        "encoder": encoder,
        "encoder_args": enc,
        "preset": preset,
        "fps": str(fps),
        "chunk_s": args.chunk_s if args.mode == "chunked" else None,
        "plan": args.plan if args.mode == "chunked" else None,
        "jobs": args.jobs,
        "parallel_audio": args.parallel_audio,
        "n_segments": len(edl["segments"]),
        "nominal_duration_us": sum(int(s["dur_us"]) for s in edl["segments"]),
        "export_duration_us": total_us,
        "expected_frames": sum(p.frames for p in qpieces),
        "expected_frames_nominal": float(
            Fraction(sum(int(s["dur_us"]) for s in edl["segments"]), US) * fps
        ),
        "expected_events": expected_events(qpieces, edl, fps),
        "timings": timings,
        **info,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("edl", nargs="?")
    ap.add_argument("--sources")
    ap.add_argument("--music")
    ap.add_argument("--preset", default="youtube1080", choices=sorted(PRESETS))
    ap.add_argument("--fps", default="25")
    ap.add_argument("--encoder", default="libx264")
    ap.add_argument("--chunk-s", type=float, default=30.0)
    ap.add_argument("--out")
    ap.add_argument("--cache")
    ap.add_argument("--mode", default="chunked", choices=["chunked", "single"])
    ap.add_argument("--naive", action="store_true")
    ap.add_argument("--plan", default="greedy", choices=["greedy", "local"])
    ap.add_argument("--jobs", type=int, default=1)
    ap.add_argument(
        "--parallel-audio", action="store_true", help="render the WAV while chunks encode"
    )
    ap.add_argument("--list-encoders", action="store_true")
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = make_parser()
    args = ap.parse_args(argv)
    if args.list_encoders:
        print(json.dumps(detect_encoders()))
        return 0
    if not (args.edl and args.sources and args.music and args.out):
        ap.error("edl, --sources, --music and --out are required")
    m = build(args)
    print(json.dumps({"out": str(Path(args.out) / "export.mp4"), "timings": m["timings"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
