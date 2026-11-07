"""S2: test media — three proxies, a music bed WAV, the title ASS, reference frames.

    python -m spikes.s2_player.prepare --work DIR

Proxies follow docs/architecture.md §5 ("копия для анализа"): 960x540, H.264 High,
CRF 26, GOP = 1 s (-g = round(fps), no scene-cut keyframes), CFR at the source rate,
BT.709 tags, AAC 48 kHz stereo. The three differ only in frame rate (25 / 29.97 / 50),
everything else in the codec setup is identical.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from pathlib import Path

import numpy as np

from spikes.common.media import SAMPLE_RATE, generate_set, probe, run_ffmpeg, tool
from spikes.s2_player.timeline import Source

PROXY_W, PROXY_H = 960, 540
SOURCES = {"s25": "clip_25", "s2997": "clip_29.97", "s50": "clip_50"}
MUSIC_GAIN = 0.25
TITLE_TEXT = "Үйлөнүү тою — Өмүрбек"
TITLE_FONT = "Inter"
TITLE_START_S, TITLE_END_S = 2.0, 12.0
COLOR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
# Reference frames are converted the way mpv does it: BT.709 limited -> full-range RGB.
REF_VF = (
    "scale=in_color_matrix=bt709:in_range=limited:out_range=full"
    ":flags=bicubic+accurate_rnd+full_chroma_int,format=bgr0"
)


def make_proxy(src: Path, dst: Path, gop_s: float = 1.0) -> Path:
    fps = Fraction(
        next(s for s in probe(src)["streams"] if s["codec_type"] == "video")["r_frame_rate"]
    )
    gop = str(max(1, round(fps * Fraction(gop_s))))
    run_ffmpeg(
        [
            "-i", str(src),
            "-vf", f"scale={PROXY_W}:{PROXY_H},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
            "-g", gop, "-keyint_min", gop, "-sc_threshold", "0", "-bf", "2",
            "-fps_mode", "cfr", "-r", str(fps), *COLOR,
            "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE), "-ac", "2",
            "-movflags", "+faststart", str(dst),
        ]
    )  # fmt: skip
    return dst


TONE_HZ = {"clip_25": 310.0, "clip_29.97": 470.0, "clip_50": 690.0}
TONE_GAIN = 0.3


def make_tone_proxies(proxy_dir: Path) -> Path:
    """Copies of the proxies whose audio never goes silent: the original clip audio
    (beeps) plus a steady per-source tone. Video is copied, audio re-encoded with
    the same AAC settings. Cuts then land mid-waveform, so a gap, overlap or
    decoder warm-up at an EDL seam is visible in the audio (S2 seam-audio check)."""
    dst_dir = proxy_dir.parent / f"{proxy_dir.name}-tone"
    dst_dir.mkdir(parents=True, exist_ok=True)
    for stem in SOURCES.values():
        dst = dst_dir / f"{stem}.mp4"
        if dst.exists():
            continue
        tone = f"sine=frequency={TONE_HZ[stem]}:sample_rate={SAMPLE_RATE}:duration=61"
        mix = (
            f"[1:a]volume={TONE_GAIN},aformat=channel_layouts=stereo[t];"
            "[0:a][t]amix=inputs=2:normalize=0:duration=first[a]"
        )
        run_ffmpeg(
            [
                "-i", str(proxy_dir / f"{stem}.mp4"), "-f", "lavfi", "-i", tone,
                "-filter_complex", mix, "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                "-c:a", "aac", "-b:a", "128k", "-ar", str(SAMPLE_RATE), "-ac", "2",
                "-movflags", "+faststart", str(dst),
            ]
        )  # fmt: skip
    return dst_dir


def load_sources(proxy_dir: Path) -> dict[str, Source]:
    out = {}
    for name, stem in SOURCES.items():
        path = proxy_dir / f"{stem}.mp4"
        v = next(s for s in probe(path)["streams"] if s["codec_type"] == "video")
        out[name] = Source(
            name, str(path.resolve()), Fraction(v["r_frame_rate"]), int(v["nb_frames"])
        )
    return out


def make_music_bed(music: Path, dst: Path, duration_s: float) -> Path:
    """What the product renders as music_bed.wav: looped music, gain applied, PCM."""
    run_ffmpeg(
        [
            "-stream_loop", "-1", "-i", str(music), "-t", f"{duration_s:.3f}",
            "-af", f"volume={MUSIC_GAIN}", "-c:a", "pcm_s16le",
            "-ar", str(SAMPLE_RATE), "-ac", "2", str(dst),
        ]
    )  # fmt: skip
    return dst


def write_title_ass(dst: Path, font: str = TITLE_FONT, text: str = TITLE_TEXT) -> Path:
    def ts(s: float) -> str:
        cs = round(s * 100)
        return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

    dst.write_text(
        "\n".join(
            [
                "[Script Info]",
                "ScriptType: v4.00+",
                f"PlayResX: {PROXY_W}",
                f"PlayResY: {PROXY_H}",
                "WrapStyle: 0",
                "ScaledBorderAndShadow: yes",
                "",
                "[V4+ Styles]",
                STYLE_FORMAT,
                f"Style: Title,{font},48,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,{STYLE_REST}",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
                f"Dialogue: 0,{ts(TITLE_START_S)},{ts(TITLE_END_S)},Title,,0,0,0,,{text}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return dst


STYLE_FORMAT = (
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
    "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
    "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
)
STYLE_REST = "0,0,0,0,100,100,0,0,1,3,1,2,40,40,48,1"


def extract_frame(path: str, frame: int, fps: Fraction) -> np.ndarray:
    """Frame `frame` of a proxy as HxWx4 bgr0, decoded by ffmpeg (accurate -ss lands
    on the first frame with pts >= ss; ss sits a quarter frame before the frame)."""
    ss = max(0.0, float((Fraction(frame) - Fraction(1, 4)) / fps))
    cmd = [
        tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-ss", f"{ss:.6f}",
        "-i", path, "-frames:v", "1", "-an", "-vf", REF_VF, "-f", "rawvideo", "-",
    ]  # fmt: skip
    proc = subprocess.run(cmd, check=False, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip())
    data = np.frombuffer(proc.stdout, dtype=np.uint8)
    if data.size != PROXY_W * PROXY_H * 4:
        raise RuntimeError(f"frame {frame} of {path}: got {data.size} bytes")
    return data.reshape(PROXY_H, PROXY_W, 4)


def reference_frames(
    sources: dict[str, Source], wanted: set[tuple[str, int]], cache: Path, jobs: int = 8
) -> dict[tuple[str, int], np.ndarray]:
    """Extract (and cache as .npy) the reference frames the bench compares with."""
    cache.mkdir(parents=True, exist_ok=True)

    def one(key: tuple[str, int]) -> tuple[tuple[str, int], np.ndarray]:
        name, frame = key
        f = cache / f"{name}-{frame:06d}.npy"
        if f.exists():
            return key, np.load(f)
        arr = extract_frame(sources[name].path, frame, sources[name].fps)
        np.save(f, arr)
        return key, arr

    with ThreadPoolExecutor(jobs) as pool:
        return dict(pool.map(one, sorted(wanted)))


def proxy_dir_for(work: Path, gop_s: float = 1.0) -> Path:
    return work / ("proxy" if gop_s == 1.0 else f"proxy-g{gop_s:g}")


def prepare(work: Path, gop_s: float = 1.0) -> dict[str, Path]:
    """Sources (generate_set) and proxies; gop_s != 1 is an experiment, not §5."""
    src_dir, proxy_dir = work / "src", proxy_dir_for(work, gop_s)
    if not (src_dir / "music.m4a").exists():
        generate_set(src_dir)
    proxy_dir.mkdir(parents=True, exist_ok=True)
    for stem in SOURCES.values():
        dst = proxy_dir / f"{stem}.mp4"
        if not dst.exists():
            make_proxy(src_dir / f"{stem}.mp4", dst, gop_s)
    return {"src": src_dir, "proxy": proxy_dir, "music": src_dir / "music.m4a"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--gop-s", type=float, default=1.0)
    args = ap.parse_args(argv)
    paths = prepare(args.work, args.gop_s)
    for name, s in load_sources(paths["proxy"]).items():
        print(name, s.fps, s.frames, s.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
