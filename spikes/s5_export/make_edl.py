"""S5: synthetic sources and the test EDL.

    python -m spikes.s5_export.make_edl --sources DIR [--size 1280x720] [--seed 5]
        [--edl-dir spikes/results/s5] [--no-media]

Makes clip_25 / clip_29.97 / clip_50 (60 s, flash + beep every 2 s from 1.0 s) and a
120 s music track in DIR (skips files that exist), then writes three EDLs:
  edl-seed<N>.json          120 segments of 1-4 s, ~5 min
  edl-seed<N>-move.json     segment 60 moved by +2 s in its source (same length)
  edl-seed<N>-longer.json   segment 60 made 2 s longer
  edl-seed<N>-eof.json      segment 30 cut to end exactly at the EOF of clip_25_shortaudio
                            (audio 0.3 s shorter than video), segment 90 at the EOF of
                            clip_50; both lengths round UP past EOF by 10 ms
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from spikes.common.media import generate_clip, generate_music, run_ffmpeg
from spikes.s5_export.plan import US, make_test_edl

SOURCES = {
    "s25": {"file": "clip_25.mp4", "fps": "25"},
    "s2997": {"file": "clip_29.97.mp4", "fps": "30000/1001"},
    "s50": {"file": "clip_50.mp4", "fps": "50"},
}
DEFAULT_EDL_DIR = Path(__file__).resolve().parents[1] / "results" / "s5"
EDIT_INDEX = 60
SHORT_AUDIO = {"file": "clip_25_shortaudio.mp4", "fps": "25"}
SHORT_AUDIO_END_S = 59.7
# (index, source, in_us, dur_us): end = 60.0 s = source EOF; 2.51 s -> 63 frames at 25
# (2.52 s) and 3.51 s -> 88 frames (3.52 s), i.e. 10 ms past EOF after rounding.
EOF_EDITS = [(30, "s25sa", 57_490_000, 2_510_000), (90, "s50", 56_490_000, 3_510_000)]


def make_media(dir: Path, size: str) -> None:
    for spec in SOURCES.values():
        p = dir / spec["file"]
        if not p.exists():
            generate_clip(p, duration_s=60, fps=spec["fps"], size=size)
    short = dir / SHORT_AUDIO["file"]
    if not short.exists():
        run_ffmpeg(
            ["-i", str(dir / SOURCES["s25"]["file"]), "-c:v", "copy",
             "-af", f"atrim=end={SHORT_AUDIO_END_S}", "-c:a", "aac", str(short)]
        )  # fmt: skip
    if not (dir / "music.m4a").exists():
        generate_music(dir / "music.m4a", duration_s=120)


def edited(edl: dict[str, Any], *, move_us: int = 0, extend_us: int = 0) -> dict[str, Any]:
    out = copy.deepcopy(edl)
    seg = out["segments"][EDIT_INDEX]
    limit = out["events"]["source_duration_us"] - out["margin_us"]
    if move_us:
        shift = move_us if seg["in_us"] + seg["dur_us"] + move_us < limit else -move_us
        seg["in_us"] += shift
    if extend_us:
        if seg["in_us"] + seg["dur_us"] + extend_us < limit:
            seg["dur_us"] += extend_us
        else:
            seg["in_us"] -= extend_us
            seg["dur_us"] += extend_us
    out["edited"] = {"index": EDIT_INDEX, "move_us": move_us, "extend_us": extend_us}
    return out


def eof_variant(edl: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(edl)
    out["sources"]["s25sa"] = dict(SHORT_AUDIO)
    for i, src, in_us, dur_us in EOF_EDITS:
        out["segments"][i] = {"src": src, "in_us": in_us, "dur_us": dur_us}
    out["edited"] = {"eof": [i for i, *_ in EOF_EDITS], "short_audio_end_s": SHORT_AUDIO_END_S}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--sources", required=True)
    ap.add_argument("--size", default="1280x720")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--edl-dir", default=str(DEFAULT_EDL_DIR))
    ap.add_argument("--no-media", action="store_true")
    args = ap.parse_args(argv)
    if not args.no_media:
        make_media(Path(args.sources), args.size)
    edl = make_test_edl(SOURCES, seed=args.seed)
    every = edl["events"]["every_us"]  # moving by one event period keeps cuts clear
    variants = {
        "": edl,
        "-move": edited(edl, move_us=every),
        "-longer": edited(edl, extend_us=every),
        "-eof": eof_variant(edl),
    }
    d = Path(args.edl_dir)
    d.mkdir(parents=True, exist_ok=True)
    for suffix, e in variants.items():
        p = d / f"edl-seed{args.seed}{suffix}.json"
        p.write_text(json.dumps(e, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        total = sum(s["dur_us"] for s in e["segments"]) / US
        print(f"{p}  {len(e['segments'])} segments, {total:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
