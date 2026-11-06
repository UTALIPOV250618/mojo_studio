"""SQLite connection and numbered-migration runner."""

import contextlib
import os
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


def _backup(con: sqlite3.Connection, db_path: Path) -> None:
    """Consistent online backup into <db>.bak via tmp + fsync + os.replace."""
    bak = db_path.with_name(db_path.name + ".bak")
    tmp = bak.with_name(bak.name + ".tmp")
    try:
        dst = sqlite3.connect(str(tmp))
        try:
            con.backup(dst)
        finally:
            dst.close()
        fd = os.open(tmp, os.O_RDWR)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, bak)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def migrate(con: sqlite3.Connection, package: str, db_path: Path | None = None) -> None:
    """Apply missing migrations; copy `<db>.bak` first, only if something will run."""
    current = int(con.execute("PRAGMA user_version").fetchone()[0])
    pending = [(n, sql) for n, sql in _migrations(package) if n > current]
    if not pending:
        return
    if db_path is not None and current > 0 and db_path.exists():
        _backup(con, db_path)
    for n, sql in pending:
        try:
            con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={n};\nCOMMIT;")
        except BaseException:
            if con.in_transaction:
                con.rollback()
            raise
