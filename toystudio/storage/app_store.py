"""Global app.db: list of known projects."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from toystudio.storage.db import connect


@dataclass(frozen=True)
class ProjectRow:
    root: Path
    name: str
    status: str
    updated_at: datetime


class AppStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._con: sqlite3.Connection = connect(path)
        with self._con:
            self._con.execute(
                "CREATE TABLE IF NOT EXISTS project_list ("
                " root TEXT PRIMARY KEY, name TEXT NOT NULL,"
                " status TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )

    def register_project(self, root: Path, name: str, status: str, updated_at: datetime) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO project_list(root, name, status, updated_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(root) DO UPDATE SET name=excluded.name, status=excluded.status,"
                " updated_at=excluded.updated_at",
                (str(root), name, status, updated_at.isoformat()),
            )

    def list_projects(self) -> list[ProjectRow]:
        rows = self._con.execute(
            "SELECT root, name, status, updated_at FROM project_list"
            " ORDER BY (status = 'analysis') DESC, updated_at DESC"
        ).fetchall()
        return [ProjectRow(Path(r[0]), r[1], r[2], datetime.fromisoformat(r[3])) for r in rows]

    def close(self) -> None:
        self._con.close()
