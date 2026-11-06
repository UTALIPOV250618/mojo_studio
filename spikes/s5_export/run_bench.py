"""S5: the whole measurement matrix in one command (Mac and the reference Windows PC).

    python -m spikes.s5_export.run_bench --work "spikes/data/s5/Той Студия" [--only sync,reuse,speed]

Prepares 720p sources (sync/reuse runs) and 1080p sources (speed runs) under --work,
the EDLs in spikes/results/s5/, then for every run: build_chunks -> measure_sync ->
write_result("s5", <run name>, ...). Encoders = all that pass the §9 p.5 test frame.
Ends with write_result("s5", "summary-<os>", ...). Run directories are recreated, so
speed numbers are always cold renders.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from spikes.common.results import write_result
from spikes.s5_export import build_chunks, make_edl
from spikes.s5_export.measure_sync import measure
from spikes.s5_export.plan import make_test_edl

SEED = 5


def short(enc: str) -> str:
    return {"libx264": "x264", "h264_videotoolbox": "vt"}.get(enc, enc.removeprefix("h264_"))


def fps_label(fps: str) -> str:
    return {"30000/1001": "2997"}.get(fps, fps)


class Bench:
    def __init__(self, work: Path, edl_dir: Path) -> None:
        self.work = work
        self.edl_dir = edl_dir
        self.rows: list[dict[str, Any]] = []
        self.tag = "win" if sys.platform == "win32" else platform.system().lower()

    def edl(self, suffix: str = "") -> Path:
        return self.edl_dir / f"edl-seed{SEED}{suffix}.json"

    def run(
        self,
        name: str,
        *,
        src: str,
        fps: str = "25",
        encoder: str = "libx264",
        chunk_s: float = 30.0,
        mode: str = "chunked",
        naive: bool = False,
        plan: str = "greedy",
        jobs: int = 1,
        parallel_audio: bool = False,
        no_pad: bool = False,
        edl_suffix: str = "",
        cache: Path | None = None,
        fresh: bool = True,
        sync: bool = True,
    ) -> dict[str, Any]:
        out = self.work / "runs" / name
        if fresh and out.exists():
            shutil.rmtree(out)
        srcdir = self.work / src
        argv = [
            str(self.edl(edl_suffix)), "--sources", str(srcdir),
            "--music", str(srcdir / "music.m4a"), "--fps", fps, "--encoder", encoder,
            "--chunk-s", str(chunk_s), "--out", str(out), "--mode", mode, "--jobs", str(jobs),
            "--plan", plan,
        ]  # fmt: skip
        if naive:
            argv.append("--naive")
        if parallel_audio:
            argv.append("--parallel-audio")
        if no_pad:
            argv.append("--no-pad")
        if cache is not None:
            argv += ["--cache", str(cache)]
        print(f"== {name}", flush=True)
        manifest = build_chunks.build(build_chunks.make_parser().parse_args(argv))
        result = measure(out / "export.mp4", manifest) if sync else None
        summary = {k: v for k, v in manifest.items() if k not in ("expected_events",)}
        data = {"run": name, "sources": src, "manifest": summary, "sync": result}
        path = write_result("s5", f"{self.tag}-{name}", data)
        row = {"run": name, "result": path.name, **_row(manifest, result)}
        self.rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row


def _slope(v: dict[str, Any]) -> float | None:
    d = v.get("drift")
    return round(d["slope_ms_per_min"], 4) if d else None


def _row(m: dict[str, Any], r: dict[str, Any] | None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "mode": m["mode"],
        "naive": m["naive"],
        "encoder": m["encoder"],
        "fps": m["fps"],
        "chunk_s": m["chunk_s"],
        "plan": m.get("plan"),
        "jobs": m["jobs"],
        "parallel_audio": m.get("parallel_audio"),
        "export_s": round(m["timings"]["total_s"], 1),
        "chunks_s": round(m["timings"].get("chunks_s", 0), 1),
        "audio_s": round(m["timings"].get("audio_s", 0), 1),
        "mux_s": round(m["timings"].get("mux_s", 0), 1),
        "chunks_total": m.get("chunks_total"),
        "chunks_rendered": m.get("chunks_rendered"),
        "audio_cmdline_chars": m.get("audio_cmdline_chars", m.get("cmdline_chars")),
    }
    if r:
        row.update(
            max_abs_delta_ms=r.get("max_abs_delta_us", 0) / 1000,
            mean_delta_ms=r.get("mean_delta_us", 0) / 1000,
            drift_ms_per_min=round(r.get("drift", {}).get("slope_ms_per_min", 0), 3),
            drift_stderr=round(r.get("drift", {}).get("stderr_ms_per_min", 0), 3),
            beep_err_max_ms=(r["beep_vs_expected"].get("max_abs_us") or 0) / 1000,
            flash_err_max_ms=(r["flash_vs_expected"].get("max_abs_us") or 0) / 1000,
            beep_drift_ms_per_min=_slope(r["beep_vs_expected"]),
            flash_drift_ms_per_min=_slope(r["flash_vs_expected"]),
            n_pairs=r.get("n_pairs"),
            frames=r["frames"],
            expected_frames=r["expected_frames"],
            frames_diff=r["frames_diff"],
            verdict=r["verdict"],
            verdict_edl=r.get("verdict_edl"),
        )
    return row


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--work", required=True, help="put it under a folder named 'Той Студия'")
    ap.add_argument("--only", default="sync,reuse,eof,speed")
    ap.add_argument("--encoders", default="auto", help="comma list or 'auto'")
    ap.add_argument("--edl-dir", default=str(make_edl.DEFAULT_EDL_DIR))
    args = ap.parse_args(argv)
    work = Path(args.work)
    parts = set(args.only.split(","))
    edl_dir = Path(args.edl_dir)
    encoders = (
        build_chunks.detect_encoders() if args.encoders == "auto" else args.encoders.split(",")
    )
    print("encoders:", encoders, flush=True)
    make_edl.main(["--sources", str(work / "src 720p"), "--size", "1280x720",
                   "--seed", str(SEED), "--edl-dir", str(edl_dir)])  # fmt: skip
    assert make_test_edl(make_edl.SOURCES, seed=SEED)["segments"]  # deterministic
    b = Bench(work, edl_dir)
    hw = [e for e in encoders if e != "libx264"]

    if "sync" in parts:
        for c in (20, 30, 60):
            b.run(f"sync-chunked-x264-25-c{c}", src="src 720p", chunk_s=c)
        b.run("sync-chunked-x264-2997-c30", src="src 720p", fps="30000/1001")
        for e in hw:
            b.run(f"sync-chunked-{short(e)}-25-c30", src="src 720p", encoder=e)
        b.run("sync-single-x264-25", src="src 720p", mode="single")
        b.run("sync-single-x264-2997", src="src 720p", mode="single", fps="30000/1001")
        b.run("sync-naive-x264-25-c30", src="src 720p", naive=True)

    if "reuse" in parts:
        b.run("sync-local-x264-25-c30", src="src 720p", plan="local")
        for plan in ("greedy", "local"):
            for c in (20, 30, 60):
                cache = work / "runs" / f"reuse-{plan}-cache-c{c}"
                if cache.exists():
                    shutil.rmtree(cache)
                for suffix in ("", "-move", "-longer"):
                    b.run(f"reuse-{plan}{suffix or '-base'}-c{c}", src="src 720p", chunk_s=c,
                          cache=cache, plan=plan, edl_suffix=suffix,
                          sync=c == 30 and suffix == "-longer")  # fmt: skip

    if "eof" in parts:  # segments ending at source EOF + a source with short audio
        b.run("eof-local-x264-25-c30", src="src 720p", plan="local", edl_suffix="-eof")
        b.run("eof-nopad-local-x264-25-c30", src="src 720p", plan="local", edl_suffix="-eof",
              no_pad=True)  # fmt: skip

    if "speed" in parts:
        make_edl.make_media(work / "src 1080p", "1920x1080")
        for e in encoders:
            for j in (1, 2):
                b.run(f"speed-chunked-{short(e)}-25-c30-j{j}", src="src 1080p", encoder=e,
                      jobs=j, sync=e == "libx264" and j == 1)  # fmt: skip
            b.run(f"speed-chunked-{short(e)}-25-c30-j1-pa", src="src 1080p", encoder=e,
                  parallel_audio=True, sync=False)  # fmt: skip
            b.run(f"speed-single-{short(e)}-25", src="src 1080p", encoder=e, mode="single",
                  sync=False)  # fmt: skip

    path = write_result("s5", f"summary-{b.tag}", {"encoders": encoders, "rows": b.rows})
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
