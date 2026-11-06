import sqlite3
from datetime import date, datetime
from pathlib import Path

from tests.conftest import tl3
from toystudio.core.models import US_PER_S, ToiKind
from toystudio.storage.app_store import AppStore
from toystudio.storage.project_store import ProjectStore
from toystudio.storage.serde import timeline_from_json, timeline_to_json


def new_store(tmp_path: Path) -> ProjectStore:
    root = tmp_path / "Той Студия" / "Свадьба Айбек и Нурайым"
    return ProjectStore.create(root, "Свадьба Айбек и Нурайым", ToiKind.WEDDING, date(2026, 10, 12))


def test_store_cyrillic_path(tmp_path):
    root = tmp_path / "Той Студия" / "Свадьба Айбек и Нурайым"
    ProjectStore.create(root, "Свадьба Айбек и Нурайым", ToiKind.WEDDING, date(2026, 10, 12))
    assert ProjectStore.open(root).name == "Свадьба Айбек и Нурайым"
    for d in ("proxy", "audio", "frames", "thumbs", "wave", "export"):
        assert (root / "cache" / d).is_dir()


def test_timeline_serde_roundtrip():
    assert timeline_from_json(timeline_to_json(tl3())) == tl3()
    assert '"v": 1' in timeline_to_json(tl3())


def test_timeline_roundtrip(tmp_path):
    s = new_store(tmp_path)
    cut = s.create_cut("Клип 5 мин", "16:9", 300 * US_PER_S)
    s.save_timeline(cut, tl3())
    assert s.load_timeline(cut) == tl3()


def test_load_timeline_none_when_empty(tmp_path):
    s = new_store(tmp_path)
    assert s.load_timeline(s.create_cut("x", "16:9", 1)) is None


def test_snapshot_retention(tmp_path):
    s = new_store(tmp_path)
    cut = s.create_cut("x", "16:9", 1)
    seqs = [s.save_timeline(cut, tl3()) for _ in range(60)]
    assert seqs[-1] == 60
    assert s.snapshot_count(cut) == 50


def test_running_jobs_become_interrupted_on_open(tmp_path):
    s = new_store(tmp_path)
    s.set_job_state("j1", "running")
    s.set_job_state("j2", "done")
    s.close()
    again = ProjectStore.open(s.root)
    assert again.job_state("j1") == "interrupted"
    assert again.job_state("j2") == "done"


def test_user_version_and_wal(tmp_path):
    s = new_store(tmp_path)
    s.close()
    con = sqlite3.connect(str(s.root / "project.db"))
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "project",
        "media",
        "stage_run",
        "transcript_segment",
        "audio_event",
        "scene_cut",
        "moment",
        "cut",
        "task",
        "plan",
        "timeline_snapshot",
        "export_job",
        "job",
    } <= tables
    con.close()


def test_backup_only_when_migrating(tmp_path, monkeypatch):
    s = new_store(tmp_path)
    s.close()
    ProjectStore.open(s.root).close()
    assert not (s.root / "project.db.bak").exists()

    from toystudio.storage import db

    real = db._migrations
    monkeypatch.setattr(
        db, "_migrations", lambda pkg: [*real(pkg), (2, "CREATE TABLE extra (x INTEGER);")]
    )
    ProjectStore.open(s.root).close()
    assert (s.root / "project.db.bak").exists()
    con = sqlite3.connect(str(s.root / "project.db"))
    assert con.execute("PRAGMA user_version").fetchone()[0] == 2
    con.close()


def test_list_projects_analysis_first(tmp_path):
    a = AppStore(tmp_path / "app.db")
    a.register_project(Path("A"), "A", "ready", datetime(2026, 11, 5))
    a.register_project(Path("B"), "B", "analysis", datetime(2026, 11, 1))
    a.register_project(Path("C"), "C", "done", datetime(2026, 11, 6))
    rows = a.list_projects()
    assert [r.name for r in rows] == ["B", "C", "A"]
    assert rows[0].root == Path("B") and rows[0].updated_at == datetime(2026, 11, 1)


def test_register_project_upserts(tmp_path):
    a = AppStore(tmp_path / "app.db")
    a.register_project(Path("A"), "A", "analysis", datetime(2026, 11, 1))
    a.register_project(Path("A"), "A2", "done", datetime(2026, 11, 2))
    rows = a.list_projects()
    assert len(rows) == 1 and rows[0].name == "A2" and rows[0].status == "done"
