"""Worker process entry point: python -m toystudio.jobs.worker_main --job ..."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from toystudio.engine.analysis.single_pass import Stopped
from toystudio.jobs import analyze_job, import_job
from toystudio.jobs.protocol import Done, Failed, Message, Progress, encode


@dataclass
class JobContext:
    project: str
    job_id: str
    stop_file: Path | None = None
    app_db: Path | None = None

    def report(self, msg: Message) -> None:
        sys.stdout.flush()  # drain stray print() text so it cannot glue onto a protocol line
        sys.stdout.buffer.write(encode(msg))
        sys.stdout.buffer.flush()

    def should_stop(self) -> bool:
        """The GUI asks to stop by creating the stop file (then kills after a grace period)."""
        return self.stop_file is not None and self.stop_file.exists()


def _noop(ctx: JobContext) -> None:
    for i in range(3):
        ctx.report(Progress("noop", i + 1, 3, None))


JOB_KINDS: dict[str, Callable[[JobContext], None]] = {
    "noop": _noop,
    "import": import_job.run,
    "analyze": analyze_job.run,
}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="toystudio-worker")
    ap.add_argument("--job", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--stop-file", type=Path, default=None)
    ap.add_argument("--app-db", type=Path, default=None)
    args = ap.parse_args(argv)
    ctx = JobContext(args.project, args.job_id, args.stop_file, args.app_db)
    fn = JOB_KINDS.get(args.job)
    if fn is None:
        ctx.report(Failed("unknown_job", f"unknown job kind: {args.job}"))
        return 2
    try:
        fn(ctx)
    except Stopped:
        ctx.report(Failed("stopped", ""))
        return 1
    except Exception:  # noqa: BLE001 - any job failure goes to the GUI
        ctx.report(Failed("exception", traceback.format_exc()[-4096:]))
        return 1
    ctx.report(Done())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
