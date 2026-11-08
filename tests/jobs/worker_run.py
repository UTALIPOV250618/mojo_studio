"""Run the real worker (python -m toystudio.jobs.worker_main) as a subprocess in tests."""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from toystudio.jobs.manager import kill_children
from toystudio.jobs.protocol import Done, Event, Message, decode
from toystudio.storage.project_store import ProjectStore

TIMEOUT_S = 120


def worker_cmd(
    kind: str, project: Path, *, app_db: Path, stop_file: Path | None = None
) -> list[str]:
    cmd = [sys.executable, "-m", "toystudio.jobs.worker_main", "--job", kind,
           "--project", str(project), "--job-id", f"test-{kind}", "--app-db", str(app_db)]  # fmt: skip
    if stop_file is not None:
        cmd += ["--stop-file", str(stop_file)]
    return cmd


def parse(out: bytes) -> list[Message]:
    msgs: list[Message] = []
    for line in out.splitlines():
        try:
            msgs.append(decode(line))
        except ValueError:
            pass  # stray non-protocol output
    return msgs


def run_raw(
    kind: str, project: Path, *, app_db: Path, stop_file: Path | None = None
) -> tuple[int, list[Message], str]:
    r = subprocess.run(
        worker_cmd(kind, project, app_db=app_db, stop_file=stop_file),
        capture_output=True,
        check=False,
        timeout=TIMEOUT_S,
    )
    return r.returncode, parse(r.stdout), r.stderr.decode(errors="replace")


def run_worker(
    kind: str,
    project: Path,
    *,
    app_db: Path,
    stop_file: Path | None = None,
    msgs: list[Message] | None = None,
) -> ProjectStore:
    """Run a job to completion (must end with Done) and reopen the project store."""
    rc, got, err = run_raw(kind, project, app_db=app_db, stop_file=stop_file)
    if msgs is not None:
        msgs.extend(got)
    assert rc == 0, (got[-3:], err)
    assert isinstance(got[-1], Done), got[-3:]
    return ProjectStore.open(project)


def run_until(
    kind: str,
    project: Path,
    *,
    app_db: Path,
    when: Callable[[Message], bool],
    then: Callable[[subprocess.Popen[bytes]], None],
    stop_file: Path | None = None,
) -> tuple[int, list[Message]]:
    """Stream the worker's messages; on the first one matching `when`, call `then(proc)`."""
    proc = subprocess.Popen(
        worker_cmd(kind, project, app_db=app_db, stop_file=stop_file),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    assert proc.stdout is not None
    msgs: list[Message] = []
    fired = False
    try:
        for line in iter(proc.stdout.readline, b""):
            got = parse(line)
            msgs.extend(got)
            if not fired and any(when(m) for m in got):
                fired = True
                then(proc)
        rc = proc.wait(timeout=TIMEOUT_S)
    finally:
        if proc.poll() is None:
            kill_tree(proc)
            proc.wait()
    assert fired, msgs[-3:]
    return rc, msgs


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill like JobManager does: ffmpeg children first, then the worker."""
    kill_children(proc.pid)
    proc.kill()


def events(msgs: list[Message], kind: str) -> list[dict[str, object]]:
    return [m.payload for m in msgs if isinstance(m, Event) and m.kind == kind]


def is_stage(stage: str, state: str, media_id: int | None = None) -> Callable[[Message], bool]:
    def check(m: Message) -> bool:
        return (
            isinstance(m, Event)
            and m.kind == "stage"
            and m.payload.get("stage") == stage
            and m.payload.get("state") == state
            and (media_id is None or m.payload.get("media_id") == media_id)
        )

    return check
