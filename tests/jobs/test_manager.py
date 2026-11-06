import subprocess
import sys
from pathlib import Path

import pytest

from toystudio.jobs.manager import JobManager
from toystudio.jobs.worker_main import main

FAKE = str(Path(__file__).parent / "fake_worker.py")
T = 10_000


@pytest.fixture
def mgr(qtbot):
    m = JobManager()
    yield m
    m.shutdown()


def test_worker_ok(qtbot, mgr):
    got = []
    mgr.progress.connect(lambda j, p: got.append(p))
    with qtbot.waitSignal(mgr.finished, timeout=T) as sig:
        mgr.start("j1", [sys.executable, FAKE, "ok"])
    assert sig.args == ["j1"]
    assert got and got[-1].done == 10  # last progress always delivered
    assert len(got) <= 10


def test_worker_crash_marks_failed(qtbot, mgr):
    with qtbot.waitSignal(mgr.failed, timeout=T) as sig:
        mgr.start("j2", [sys.executable, FAKE, "crash"])
    assert sig.args[:2] == ["j2", "crashed"]
    assert "boom stderr" in sig.args[2]


def test_cancel(qtbot, mgr):
    mgr.start("j3", [sys.executable, FAKE, "hang"])
    with qtbot.waitSignal(mgr.failed, timeout=T) as sig:
        mgr.cancel("j3")
    assert sig.args == ["j3", "cancelled", ""]
    assert mgr.running() == []


def test_queue_one_at_a_time(qtbot, mgr):
    m = JobManager(max_parallel=1)
    try:
        m.start("a", [sys.executable, FAKE, "hang"])
        m.start("b", [sys.executable, FAKE, "ok"])
        assert m.running() == ["a"]
    finally:
        m.shutdown()


def test_cancel_queued(qtbot, mgr):
    mgr.start("a", [sys.executable, FAKE, "hang"])
    mgr.start("b", [sys.executable, FAKE, "ok"])
    with qtbot.waitSignal(mgr.failed, timeout=T) as sig:
        mgr.cancel("b")
    assert sig.args == ["b", "cancelled", ""]
    with qtbot.waitSignal(mgr.failed, timeout=T):
        mgr.cancel("a")
    assert mgr.running() == []


def test_queue_advances(qtbot, mgr):
    mgr.start("a", [sys.executable, FAKE, "crash"])
    with qtbot.waitSignal(mgr.finished, timeout=T) as sig:
        mgr.start("b", [sys.executable, FAKE, "ok"])
    assert sig.args == ["b"]


def test_real_worker_noop(qtbot, mgr):
    with qtbot.waitSignal(mgr.finished, timeout=T):
        mgr.start(
            "n",
            [
                sys.executable,
                "-m",
                "toystudio.jobs.worker_main",
                "--job",
                "noop",
                "--project",
                ".",
                "--job-id",
                "n",
            ],
        )


def test_worker_main_unknown_kind(capsys):
    rc = main(["--job", "nope", "--project", ".", "--job-id", "x"])
    assert rc == 2
    assert '"unknown_job"' in capsys.readouterr().out


def test_worker_main_exception(capsys, monkeypatch):
    from toystudio.jobs import worker_main

    def bad(ctx):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(worker_main.JOB_KINDS, "bad", bad)
    rc = main(["--job", "bad", "--project", ".", "--job-id", "x"])
    assert rc == 1
    out = capsys.readouterr().out
    assert '"exception"' in out and "kaboom" in out


def test_noop_subprocess_exits_zero():
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "toystudio.jobs.worker_main",
            "--job",
            "noop",
            "--project",
            ".",
            "--job-id",
            "n",
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert r.returncode == 0
    assert b'"done"' in r.stdout


def test_crash_stderr_cyrillic_intact(qtbot, mgr):
    with qtbot.waitSignal(mgr.failed, timeout=T) as sig:
        mgr.start("ru", [sys.executable, FAKE, "crash_ru"])
    assert sig.args[:2] == ["ru", "crashed"]
    assert "ошибка: Свадьба Айбек" in sig.args[2]


def test_stray_print_does_not_break_protocol(qtbot, mgr):
    failures = []
    mgr.failed.connect(lambda *a: failures.append(a))
    with qtbot.waitSignal(mgr.finished, timeout=T):
        mgr.start("sp", [sys.executable, FAKE, "print_stray"])
    assert failures == []


def test_worker_main_flushes_stray_stdout(capfdbinary):
    from toystudio.jobs import worker_main

    worker_main.JOB_KINDS["stray"] = lambda ctx: print("x", end="")
    try:
        assert worker_main.main(["--job", "stray", "--project", "p", "--job-id", "j"]) == 0
    finally:
        del worker_main.JOB_KINDS["stray"]
    out = capfdbinary.readouterr().out
    assert out.startswith(b"x") and out.endswith(b"\n")
    assert out[1:].lstrip().startswith(b"{")


def test_duplicate_job_id_rejected(qtbot, mgr):
    mgr.start("d", [sys.executable, FAKE, "hang"])
    with pytest.raises(ValueError):
        mgr.start("d", [sys.executable, FAKE, "hang"])  # active
    mgr.start("q", [sys.executable, FAKE, "ok"])
    with pytest.raises(ValueError):
        mgr.start("q", [sys.executable, FAKE, "ok"])  # queued
