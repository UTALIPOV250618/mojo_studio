import sys
from pathlib import Path

from toystudio.jobs.launch import stop_file_for, worker_argv


def test_worker_argv_with_cyrillic_root_and_stop_file():
    root = Path("D:/Тойлор/Свадьба Айбек и Нурайым")
    argv = worker_argv(
        "import",
        root,
        f"import:{root}",
        app_db=Path("C:/Данные/app.db"),
        stop_file=stop_file_for(root, "import"),
    )
    assert argv[:3] == [sys.executable, "-m", "toystudio.jobs.worker_main"]
    assert argv[argv.index("--job") + 1] == "import"
    assert argv[argv.index("--project") + 1] == str(root)
    assert argv[argv.index("--job-id") + 1] == f"import:{root}"
    assert argv[argv.index("--app-db") + 1] == str(Path("C:/Данные/app.db"))
    assert argv[argv.index("--stop-file") + 1] == str(root / "cache" / "stop-import")


def test_worker_argv_without_stop_file():
    argv = worker_argv("analyze", Path("/p"), "analyze:/p", app_db=Path("/a.db"))
    assert "--stop-file" not in argv


def test_stop_file_for():
    assert stop_file_for(Path("/p/Той"), "analyze") == Path("/p/Той/cache/stop-analyze")
