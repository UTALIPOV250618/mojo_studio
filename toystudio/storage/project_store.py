"""Per-project SQLite store (project.db) and folder layout."""

import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Self

from toystudio.core.models import ToiKind
from toystudio.core.timeline import Timeline
from toystudio.storage.db import connect, migrate
from toystudio.storage.serde import timeline_from_json, timeline_to_json

KEEP_SNAPSHOTS = 50
CACHE_DIRS = ("proxy", "audio", "frames", "thumbs", "wave", "export")
_PKG = "toystudio.storage"


def _now() -> str:
    return datetime.now().isoformat()


class ProjectStore:
    def __init__(self, root: Path, con: sqlite3.Connection) -> None:
        self.root = root
        self._con = con

    @classmethod
    def create(cls, root: Path, name: str, kind: ToiKind, event_date: date) -> Self:
        root.mkdir(parents=True, exist_ok=True)
        for d in CACHE_DIRS:
            (root / "cache" / d).mkdir(parents=True, exist_ok=True)
        con = connect(root / "project.db")
        migrate(con, _PKG, root / "project.db")
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        now = _now()
        with con:
            con.execute(
                "INSERT INTO project(id, name, kind, event_date, created_at, updated_at,"
                " schema_version) VALUES (1, ?, ?, ?, ?, ?, ?)",
                (name, kind.value, event_date.isoformat(), now, now, version),
            )
        return cls(root, con)

    @classmethod
    def open(cls, root: Path) -> Self:
        db = root / "project.db"
        if not db.is_file():
            raise FileNotFoundError(db)
        con = connect(db)
        migrate(con, _PKG, db)
        version = int(con.execute("PRAGMA user_version").fetchone()[0])
        with con:
            con.execute("UPDATE project SET schema_version=? WHERE id=1", (version,))
            con.execute("UPDATE job SET state='interrupted' WHERE state='running'")
        return cls(root, con)

    def close(self) -> None:
        self._con.close()

    @property
    def name(self) -> str:
        return str(self._con.execute("SELECT name FROM project WHERE id=1").fetchone()[0])

    def create_cut(self, title: str, aspect: str, target_us: int) -> str:
        cut_id = uuid.uuid4().hex
        with self._con:
            self._con.execute(
                "INSERT INTO cut(id, title, created_at, aspect, target_duration_us)"
                " VALUES (?, ?, ?, ?, ?)",
                (cut_id, title, _now(), aspect, target_us),
            )
        return cut_id

    def save_timeline(self, cut_id: str, t: Timeline) -> int:
        with self._con:
            seq = int(
                self._con.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM timeline_snapshot WHERE cut_id=?",
                    (cut_id,),
                ).fetchone()[0]
            )
            self._con.execute(
                "INSERT INTO timeline_snapshot(cut_id, seq, json, created_at) VALUES (?, ?, ?, ?)",
                (cut_id, seq, timeline_to_json(t), _now()),
            )
            self._con.execute(
                "DELETE FROM timeline_snapshot WHERE cut_id=? AND seq <= ?",
                (cut_id, seq - KEEP_SNAPSHOTS),
            )
        return seq

    def load_timeline(self, cut_id: str) -> Timeline | None:
        row = self._con.execute(
            "SELECT json FROM timeline_snapshot WHERE cut_id=? ORDER BY seq DESC LIMIT 1",
            (cut_id,),
        ).fetchone()
        return None if row is None else timeline_from_json(row[0])

    def snapshot_count(self, cut_id: str) -> int:
        return int(
            self._con.execute(
                "SELECT COUNT(*) FROM timeline_snapshot WHERE cut_id=?", (cut_id,)
            ).fetchone()[0]
        )

    def set_job_state(self, job_id: str, state: str) -> None:
        with self._con:
            self._con.execute(
                "INSERT INTO job(id, state, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET state=excluded.state,"
                " updated_at=excluded.updated_at",
                (job_id, state, _now()),
            )

    def job_state(self, job_id: str) -> str:
        row = self._con.execute("SELECT state FROM job WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return str(row[0])
