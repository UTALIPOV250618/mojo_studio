"""Worker process entry point: python -m toystudio.jobs.worker_main --job ..."""

from __future__ import annotations

import argparse
import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass

from toystudio.jobs.protocol import Done, Failed, Message, Progress, encode


@dataclass
class JobContext:
    project: str
    job_id: str

    def report(self, msg: Message) -> None:
        sys.stdout.flush()  # drain stray print() text so it cannot glue onto a protocol line
        sys.stdout.buffer.write(encode(msg))
        sys.stdout.buffer.flush()


def _noop(ctx: JobContext) -> None:
    for i in range(3):
        ctx.report(Progress("noop", i + 1, 3, None))


JOB_KINDS: dict[str, Callable[[JobContext], None]] = {"noop": _noop}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="toystudio-worker")
    ap.add_argument("--job", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--job-id", required=True)
    args = ap.parse_args(argv)
    ctx = JobContext(args.project, args.job_id)
    fn = JOB_KINDS.get(args.job)
    if fn is None:
        ctx.report(Failed("unknown_job", f"unknown job kind: {args.job}"))
        return 2
    try:
        fn(ctx)
    except Exception:  # noqa: BLE001 - any job failure goes to the GUI
        ctx.report(Failed("exception", traceback.format_exc()[-4096:]))
        return 1
    ctx.report(Done())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
