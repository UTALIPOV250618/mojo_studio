"""GUI-side job manager: runs workers via QProcess, queues, cancels, throttles."""

from __future__ import annotations

import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from toystudio.jobs.protocol import Done, Event, Failed, Progress, decode

log = logging.getLogger(__name__)

STDERR_TAIL = 4096
PROGRESS_INTERVAL_S = 0.25


@dataclass
class _Job:
    job_id: str
    argv: list[str]
    process: QProcess | None = None
    buf: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    terminal: Done | Failed | None = None
    cancelled: bool = False
    last_emit: float = 0.0
    pending: Progress | None = None
    timer: QTimer | None = None


class JobManager(QObject):
    progress = Signal(str, object)
    event = Signal(str, object)
    finished = Signal(str)
    failed = Signal(str, str, str)

    def __init__(self, max_parallel: int = 1, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._max = max_parallel
        self._queue: deque[_Job] = deque()
        self._active: dict[str, _Job] = {}

    def start(self, job_id: str, argv: list[str]) -> None:
        if job_id in self._active or any(j.job_id == job_id for j in self._queue):
            raise ValueError(f"job already queued or running: {job_id}")
        self._queue.append(_Job(job_id, list(argv)))
        self._pump()

    def running(self) -> list[str]:
        """Jobs whose worker process is running (queued ones are not included)."""
        return list(self._active)

    def pending(self) -> list[str]:
        """Jobs waiting in the queue for a free slot."""
        return [j.job_id for j in self._queue]

    def is_active(self, job_id: str) -> bool:
        """Running or queued: the project of such a job is busy."""
        return job_id in self._active or any(j.job_id == job_id for j in self._queue)

    def cancel(self, job_id: str) -> None:
        for job in self._queue:
            if job.job_id == job_id:
                self._queue.remove(job)
                self.failed.emit(job_id, "cancelled", "")
                return
        job = self._active.get(job_id)
        if job is not None and job.process is not None:
            job.cancelled = True
            job.process.kill()

    def shutdown(self) -> None:
        """Kill everything without emitting signals (teardown)."""
        self._queue.clear()
        for job in list(self._active.values()):
            proc = job.process
            if job.timer:
                job.timer.stop()
            if proc is not None:
                proc.blockSignals(True)
                proc.kill()
                proc.waitForFinished(3000)
        self._active.clear()

    # -- internals -------------------------------------------------------
    def _pump(self) -> None:
        while self._queue and len(self._active) < self._max:
            job = self._queue.popleft()
            self._launch(job)

    def _launch(self, job: _Job) -> None:
        proc = QProcess(self)
        job.process = proc
        self._active[job.job_id] = job
        proc.readyReadStandardOutput.connect(lambda j=job: self._on_stdout(j))
        proc.readyReadStandardError.connect(lambda j=job: self._on_stderr(j))
        proc.started.connect(lambda j=job: self._on_started(j))
        proc.finished.connect(lambda *_a, j=job: self._on_finished(j))
        proc.errorOccurred.connect(lambda err, j=job: self._on_error(j, err))
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUTF8", "1")  # Windows: UTF-8 stderr/stdout for Cyrillic text
        env.insert("PYTHONIOENCODING", "utf-8")
        proc.setProcessEnvironment(env)
        proc.start(job.argv[0], job.argv[1:])

    def _on_started(self, job: _Job) -> None:
        if sys.platform != "win32" or job.process is None:
            return
        try:
            import psutil

            pid = job.process.processId()
            psutil.Process(pid).nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        except Exception:
            log.debug("could not lower priority for %s", job.job_id, exc_info=True)

    def _on_stderr(self, job: _Job) -> None:
        assert job.process is not None
        job.stderr += bytes(job.process.readAllStandardError().data())
        if len(job.stderr) > STDERR_TAIL:
            del job.stderr[:-STDERR_TAIL]

    def _on_stdout(self, job: _Job) -> None:
        assert job.process is not None
        job.buf += bytes(job.process.readAllStandardOutput().data())
        self._drain_lines(job)

    def _drain_lines(self, job: _Job) -> None:
        while True:
            idx = job.buf.find(b"\n")
            if idx < 0:
                return
            line = bytes(job.buf[:idx])
            del job.buf[: idx + 1]
            if not line.strip():
                continue
            try:
                msg = decode(line)
            except ValueError:
                log.debug("non-protocol stdout from %s: %r", job.job_id, line)
                continue
            self._handle(job, msg)

    def _handle(self, job: _Job, msg: object) -> None:
        if isinstance(msg, Progress):
            self._throttled(job, msg)
        elif isinstance(msg, Event):
            self.event.emit(job.job_id, msg)
        elif isinstance(msg, (Done, Failed)):
            job.terminal = msg

    def _throttled(self, job: _Job, msg: Progress) -> None:
        now = time.monotonic()
        if now - job.last_emit >= PROGRESS_INTERVAL_S:
            job.last_emit = now
            job.pending = None
            self.progress.emit(job.job_id, msg)
            return
        job.pending = msg
        if job.timer is None:
            t = QTimer(self)
            t.setSingleShot(True)
            t.timeout.connect(lambda j=job: self._flush(j))
            job.timer = t
        if not job.timer.isActive():
            delay = PROGRESS_INTERVAL_S - (now - job.last_emit)
            job.timer.start(max(1, int(delay * 1000)))

    def _flush(self, job: _Job) -> None:
        if job.pending is not None:
            msg, job.pending = job.pending, None
            job.last_emit = time.monotonic()
            self.progress.emit(job.job_id, msg)

    def _on_error(self, job: _Job, err: QProcess.ProcessError) -> None:
        # FailedToStart never produces finished(); other errors are followed by it.
        if err == QProcess.ProcessError.FailedToStart:
            assert job.process is not None
            job.stderr += job.process.errorString().encode()
            self._on_finished(job)

    def _on_finished(self, job: _Job) -> None:
        if job.job_id not in self._active:
            return
        proc = job.process
        if proc is not None:
            job.buf += bytes(proc.readAllStandardOutput().data())
            job.stderr += bytes(proc.readAllStandardError().data())
            if len(job.stderr) > STDERR_TAIL:
                del job.stderr[:-STDERR_TAIL]
        if job.buf and not job.buf.endswith(b"\n"):
            job.buf += b"\n"
        self._drain_lines(job)
        if job.timer:
            job.timer.stop()
        if job.pending is not None and not job.cancelled:
            self._flush(job)
        del self._active[job.job_id]
        if proc is not None:
            proc.deleteLater()
        if job.cancelled:
            self.failed.emit(job.job_id, "cancelled", "")
        elif isinstance(job.terminal, Done):
            self.finished.emit(job.job_id)
        elif isinstance(job.terminal, Failed):
            self.failed.emit(job.job_id, job.terminal.code, job.terminal.detail)
        else:
            tail = bytes(job.stderr[-STDERR_TAIL:]).decode("utf-8", "replace")
            self.failed.emit(job.job_id, "crashed", tail)
        self._pump()
