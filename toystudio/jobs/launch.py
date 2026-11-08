"""Command lines for worker processes; shared by the GUI screens that start jobs."""

from __future__ import annotations

import sys
from pathlib import Path


def worker_argv(
    kind: str,
    root: Path,
    job_id: str,
    *,
    app_db: Path,
    stop_file: Path | None = None,
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "toystudio.jobs.worker_main",
        "--job",
        kind,
        "--project",
        str(root),
        "--job-id",
        job_id,
        "--app-db",
        str(app_db),
    ]
    if stop_file is not None:
        argv += ["--stop-file", str(stop_file)]
    return argv


def stop_file_for(root: Path, kind: str) -> Path:
    return root / "cache" / f"stop-{kind}"
