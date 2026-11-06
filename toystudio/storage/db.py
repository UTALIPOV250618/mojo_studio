"""SQLite connection and numbered-migration runner."""

import shutil
import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _migrations(package: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for entry in resources.files(package).joinpath("migrations").iterdir():
        if entry.name.endswith(".sql"):
            found.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(found)


def migrate(con: sqlite3.Connection, package: str, db_path: Path | None = None) -> None:
    """Apply missing migrations; copy `<db>.bak` first, only if something will run."""
    current = int(con.execute("PRAGMA user_version").fetchone()[0])
    pending = [(n, sql) for n, sql in _migrations(package) if n > current]
    if not pending:
        return
    if db_path is not None and current > 0 and db_path.exists():
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(db_path, db_path.with_name(db_path.name + ".bak"))
    for n, sql in pending:
        con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={n};\nCOMMIT;")
