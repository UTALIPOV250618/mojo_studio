"""Worker job `import`: copy into the project, integrity check, probe."""

from datetime import date
from pathlib import Path

import pytest

from tests.jobs.worker_run import events, run_worker
from tests.media_fixtures import make_clip, make_truncated, needs_ffmpeg
from toystudio.core.models import MediaKind, MediaStatus, ToiKind
from toystudio.storage.project_store import ProjectStore

pytestmark = needs_ffmpeg


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "Той Студия" / "Свадьба Айбек и Нурайым"
    ProjectStore.create(root, "Свадьба Айбек и Нурайым", ToiKind.WEDDING, date(2026, 9, 1)).close()
    return root


def _add(root: Path, path: Path, *, copied: bool, kind: MediaKind = MediaKind.VIDEO) -> int:
    s = ProjectStore.open(root)
    try:
        size = path.stat().st_size if path.exists() else 1
        mid, created = s.add_media(path, kind, size, copied)
        assert created
        return mid
    finally:
        s.close()


def test_import_copies_cyrillic_path(tmp_path: Path) -> None:
    root = _project(tmp_path)
    src1 = make_clip(tmp_path / "Камера 1" / "клип 1.mp4", duration_s=2, size="320x180")
    src2 = make_clip(tmp_path / "Камера 2" / "клип 1.mp4", duration_s=3, size="320x180")
    local = make_clip(tmp_path / "Камера 3" / "оставить.mp4", duration_s=2, size="320x180")
    id1 = _add(root, src1, copied=True)
    id2 = _add(root, src2, copied=True)  # same name -> "клип 1 (2).mp4"
    id3 = _add(root, local, copied=False)

    msgs: list = []
    s = run_worker("import", root, app_db=tmp_path / "app.db", msgs=msgs)
    try:
        rows = {m.id: m for m in s.list_media()}
    finally:
        s.close()

    assert rows[id1].path == root / "media" / "клип 1.mp4"
    assert rows[id2].path == root / "media" / "клип 1 (2).mp4"
    assert rows[id1].copied and rows[id2].copied
    assert rows[id1].path.read_bytes() == src1.read_bytes()
    assert rows[id2].path.read_bytes() == src2.read_bytes()
    assert src1.exists()  # copy, not move
    assert rows[id3].path == local and not rows[id3].copied
    for mid in (id1, id2, id3):
        assert rows[mid].status is MediaStatus.OK
        assert rows[mid].info is not None and rows[mid].info.width == 320
    assert not list((root / "media").glob("*.part"))

    copies = [e for e in events(msgs, "copy") if e["media_id"] == id1]
    assert copies and copies[-1]["done_bytes"] == copies[-1]["total_bytes"] == src1.stat().st_size
    assert not [e for e in events(msgs, "copy") if e["media_id"] == id3]
    media = {e["media_id"]: e for e in events(msgs, "media")}
    assert media[id1]["status"] == "ok"
    assert media[id1]["duration_us"] == pytest.approx(2_000_000, abs=100_000)


def test_import_marks_corrupt_and_missing(tmp_path: Path) -> None:
    root = _project(tmp_path)
    good = make_clip(tmp_path / "src" / "good.mp4", duration_s=2, size="320x180")
    bad = make_truncated(good, tmp_path / "src" / "битый.mp4", keep=0.5)
    gone = tmp_path / "src" / "пропал.mp4"
    id_good = _add(root, good, copied=False)
    id_bad = _add(root, bad, copied=False)
    id_gone = _add(root, gone, copied=True)

    msgs: list = []
    s = run_worker("import", root, app_db=tmp_path / "app.db", msgs=msgs)
    try:
        rows = {m.id: m for m in s.list_media()}
    finally:
        s.close()

    assert rows[id_good].status is MediaStatus.OK and rows[id_good].info is not None
    assert rows[id_bad].status is MediaStatus.CORRUPT and rows[id_bad].info is None
    assert rows[id_gone].status is MediaStatus.MISSING
    media = {e["media_id"]: e["status"] for e in events(msgs, "media")}
    assert media == {id_good: "ok", id_bad: "corrupt", id_gone: "missing"}
    assert not (root / "media" / "пропал.mp4").exists()


def test_import_copies_already_probed_row_when_copy_requested_later(tmp_path: Path) -> None:
    root = _project(tmp_path)
    src = make_clip(tmp_path / "Камера" / "клип.mp4", duration_s=2, size="320x180")
    mid = _add(root, src, copied=False)
    run_worker("import", root, app_db=tmp_path / "app.db", msgs=[]).close()
    s = ProjectStore.open(root)
    before = s.list_media()[0]
    s.set_media_path(mid, before.path, copied=True)  # the user ticks "copy" afterwards
    s.close()

    msgs: list = []
    s = run_worker("import", root, app_db=tmp_path / "app.db", msgs=msgs)
    try:
        row = s.list_media()[0]
    finally:
        s.close()
    assert row.path == root / "media" / "клип.mp4" and row.copied
    assert row.path.read_bytes() == src.read_bytes()
    assert row.info == before.info and row.status is MediaStatus.OK
    assert [e for e in events(msgs, "copy") if e["media_id"] == mid]
