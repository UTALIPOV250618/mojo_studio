"""View-model of screen 2: project creation, file import, disk check, start of analysis."""

from __future__ import annotations

import contextlib
import ntpath
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from toystudio.app.widgets.drop_zone import FileRow, RowState
from toystudio.core.budget import needed_bytes
from toystudio.core.eta import DEFAULT_X_RT, over_target, profile_key, project_remaining_s
from toystudio.core.models import US_PER_S, MediaKind, MediaRow, MediaStatus, ToiKind
from toystudio.jobs.launch import stop_file_for, worker_argv
from toystudio.jobs.manager import JobManager
from toystudio.storage.app_store import AppStore
from toystudio.storage.fs import free_bytes, is_removable
from toystudio.storage.project_store import ProjectStore

MEDIA_DIR = "media"
_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)}
_RESERVED |= {f"LPT{i}" for i in range(1, 10)}
_BAD = (MediaStatus.CORRUPT, MediaStatus.MISSING)


@dataclass(frozen=True)
class DiskCheck:
    free: int
    needed: int
    drive: str  # "D:" on Windows, "" elsewhere
    ok: bool

    @property
    def shortage(self) -> int:
        return max(0, self.needed - self.free)


def drive_of(path: Path, platform: str = sys.platform) -> str:
    return ntpath.splitdrive(str(path))[0] if platform == "win32" else ""


def sanitize_folder_name(name: str) -> str:
    clean = _INVALID.sub("", name).strip().rstrip(". ").strip()
    if clean.split(".")[0].upper() in _RESERVED:
        clean += "_"
    return clean


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def _creation_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


class NewProjectVM(QObject):
    rows_changed = Signal(object, list)
    disk_changed = Signal(object)
    banner = Signal(object)  # "disk" | "corrupt" | None
    can_start_changed = Signal(bool)
    warning_4k = Signal(bool)
    analysis_started = Signal(Path)
    date_changed = Signal(object)
    copy_changed = Signal(bool)

    def __init__(self, app_store: AppStore, jobs: JobManager, projects_dir: Path) -> None:
        super().__init__()
        self._app = app_store
        self._jobs = jobs
        self._projects_dir = projects_dir
        self._store: ProjectStore | None = None
        self.root: Path | None = None
        self._name = ""
        self._kind = ToiKind.WEDDING
        self._date = date.today()
        self._date_touched = False
        self._copy = False
        self.removable_hint = False
        self._status = "files"
        self._import_active = False
        self._rerun = False
        self._copy_pct: dict[int, int] = {}
        self._excluded: set[int] = set()
        self._dups: dict[MediaKind, list[FileRow]] = {k: [] for k in MediaKind}
        self._dup_n = 0
        self._media: list[MediaRow] = []
        self._rows: dict[MediaKind, list[FileRow]] = {k: [] for k in MediaKind}
        self._disk = DiskCheck(0, 0, "", True)
        self._can_start = False
        self.banner_key: str | None = None
        self.corrupt_name: str | None = None
        self.warning_4k_active = False
        self.eta_4k_s = 0.0
        jobs.event.connect(self._on_event)
        jobs.finished.connect(self._on_job_end)
        jobs.failed.connect(self._on_job_failed)

    # ---- details
    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        clean = value.strip()
        if self._store is not None and self.root is not None and clean:
            self._store.rename(clean)
            self._app.register_project(self.root, clean, self._status, datetime.now())

    @property
    def kind(self) -> ToiKind:
        return self._kind

    @kind.setter
    def kind(self, value: ToiKind) -> None:
        self._kind = value
        self._save_details()

    @property
    def event_date(self) -> date:
        return self._date

    @event_date.setter
    def event_date(self, value: date) -> None:
        self._date_touched = True
        self._date = value
        self._save_details()

    @property
    def copy(self) -> bool:
        return self._copy

    @copy.setter
    def copy(self, value: bool) -> None:
        self._set_copy(value)

    def _save_details(self) -> None:
        if self._store is not None:
            self._store.update_details(self._kind, self._date)

    def _set_copy(self, value: bool) -> None:
        changed = value != self._copy
        self._copy = value
        if not value:
            self.removable_hint = False
        if changed:
            self.copy_changed.emit(value)
            if self._apply_copy_to_unprocessed() and self._import_active:
                self._rerun = True
        self._refresh()

    def _apply_copy_to_unprocessed(self) -> bool:
        """Rows the worker has not touched yet follow the toggle; imported rows stay as they are."""
        if self._store is None:
            return False
        changed = False
        for m in self._store.list_media():
            if m.status is MediaStatus.OK and m.info is None and m.copied != self._copy:
                self._store.set_media_path(m.id, m.path, self._copy)
                changed = True
        return changed

    def set_projects_dir(self, path: Path) -> bool:
        """Pick another parent folder; possible only until the project folder exists."""
        if self._store is not None:
            return False
        self._projects_dir = path
        self._refresh()
        return True

    @property
    def projects_dir(self) -> Path:
        return self._projects_dir

    # ---- files
    def add_files(self, kind: MediaKind, paths: list[Path]) -> None:
        self._dups = {k: [] for k in MediaKind}
        files = [p for p in paths if p.is_file()]
        if not files:
            return
        if any(is_removable(p) for p in files):
            self.removable_hint = True
            self._set_copy(True)
        store = self._ensure_project()
        added = False
        for p in files:
            try:
                size = p.stat().st_size
            except OSError:
                continue
            _, created = store.add_media(p, kind, size, self._copy)
            if created:
                added = True
            else:
                self._dup_n += 1
                row = FileRow(f"dup:{self._dup_n}", p.name, size, None, "duplicate", None)
                self._dups[kind].append(row)
        self._refresh()
        if added:
            self._start_import()

    def remove_file(self, media_id: int) -> None:
        if self._store is None:
            return
        old = next((m for m in self._store.list_media() if m.id == media_id), None)
        self._store.remove_media(media_id)
        self._excluded.discard(media_id)
        self._copy_pct.pop(media_id, None)
        if old is not None and self.root is not None and _inside(old.path, self.root / MEDIA_DIR):
            with contextlib.suppress(OSError):
                old.path.unlink()
        self._refresh()

    def replace_file(self, media_id: int, path: Path) -> None:
        if self._store is None:
            return
        old = next((m for m in self._store.list_media() if m.id == media_id), None)
        kind = old.kind if old is not None else MediaKind.VIDEO
        self.remove_file(media_id)
        self.add_files(kind, [path])

    def exclude_bad(self) -> None:
        """«Продолжить без него»: bad files stay in the list but no longer block anything."""
        self._excluded.update(m.id for m in self._media if m.status in _BAD)
        self._refresh()

    def first_bad_id(self) -> int | None:
        return next((m.id for m in self._bad_media()), None)

    def rows(self, kind: MediaKind) -> list[FileRow]:
        return list(self._rows[kind])

    def recheck(self) -> None:
        self._refresh(force=True)

    # ---- project
    def _folder_for(self) -> Path:
        base = sanitize_folder_name(self._name)
        if not base:
            n = 1
            while (self._projects_dir / f"Новый проект {n}").exists():
                n += 1
            return self._projects_dir / f"Новый проект {n}"
        folder, n = self._projects_dir / base, 2
        while folder.exists():
            folder = self._projects_dir / f"{base} ({n})"
            n += 1
        return folder

    def _ensure_project(self) -> ProjectStore:
        if self._store is not None:
            return self._store
        folder = self._folder_for()
        name = self._name.strip() or folder.name
        self._store = ProjectStore.create(folder, name, self._kind, self._date)
        self.root = folder
        self._app.register_project(folder, name, self._status, datetime.now())
        return self._store

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None

    # ---- jobs
    def _import_id(self) -> str:
        return f"import:{self.root}"

    def _start_import(self) -> None:
        if self.root is None:
            return
        if self._import_active:
            self._rerun = True
            return
        stop = stop_file_for(self.root, "import")
        stop.unlink(missing_ok=True)
        argv = worker_argv(
            "import", self.root, self._import_id(), app_db=self._app.path, stop_file=stop
        )
        self._import_active = True
        try:
            self._jobs.start(self._import_id(), argv)
        except ValueError:  # the manager still has it: run again once it ends
            self._rerun = True
        self._refresh()

    def _on_event(self, job_id: str, ev: object) -> None:
        if self.root is None or job_id != self._import_id():
            return
        kind = getattr(ev, "kind", "")
        payload = getattr(ev, "payload", {})
        mid = int(payload.get("media_id", 0))
        if kind == "copy":
            total = int(payload.get("total_bytes", 0))
            done = int(payload.get("done_bytes", 0))
            pct = int(done * 100 / total) if total else 100
            if pct >= 100:
                self._copy_pct.pop(mid, None)
            elif self._copy_pct.get(mid) == pct:
                return
            else:
                self._copy_pct[mid] = pct
        elif kind == "media":
            self._copy_pct.pop(mid, None)
        self._refresh()

    def _on_job_end(self, job_id: str) -> None:
        if self.root is None or job_id != self._import_id():
            return
        self._import_active = False
        self._copy_pct.clear()
        if self._rerun:
            self._rerun = False
            self._start_import()
        else:
            self._refresh()

    def _on_job_failed(self, job_id: str, code: str, detail: str) -> None:
        self._on_job_end(job_id)

    def start_analysis(self) -> None:
        if not self._can_start or self.root is None:
            return
        job_id = f"analyze:{self.root}"
        stop = stop_file_for(self.root, "analyze")
        stop.unlink(missing_ok=True)
        argv = worker_argv("analyze", self.root, job_id, app_db=self._app.path, stop_file=stop)
        self._jobs.start(job_id, argv)
        self._status = "analysis"
        self._app.set_status(self.root, "analysis", datetime.now())
        self.analysis_started.emit(self.root)

    # ---- derived state
    def _bad_media(self) -> list[MediaRow]:
        return [m for m in self._media if m.status in _BAD and m.id not in self._excluded]

    def _row_for(self, m: MediaRow) -> FileRow:
        state: RowState
        percent = None
        if m.status is MediaStatus.CORRUPT:
            state = "corrupt"
        elif m.status is MediaStatus.MISSING:
            state = "missing"
        elif m.info is None:
            percent = self._copy_pct.get(m.id)
            state = "copying" if percent is not None else "checking"
        else:
            state = "ready"
        duration = m.info.duration_us if m.info else None
        return FileRow(str(m.id), m.path.name, m.size, duration, state, percent)

    def _pending_copy_bytes(self) -> int:
        media_dir = None if self.root is None else self.root / MEDIA_DIR
        return sum(
            m.size
            for m in self._media
            if m.copied
            and m.id not in self._excluded
            and not (media_dir and _inside(m.path, media_dir))
        )

    def disk_check(self) -> DiskCheck:
        live = [m for m in self._media if m.status not in _BAD and m.id not in self._excluded]
        items = [(m.size, m.info.duration_us if m.info else 0) for m in live]
        needed = needed_bytes(items, False) + self._pending_copy_bytes()
        target = self._projects_dir if self._copy or self.root is None else self.root
        free = free_bytes(target)
        return DiskCheck(free, needed, drive_of(target), free >= needed)

    def _update_default_date(self) -> None:
        if self._date_touched:
            return
        for m in self._media:
            if m.kind is MediaKind.VIDEO and m.info is not None and m.status is MediaStatus.OK:
                d = _creation_date(m.info.creation_time)
                if d is not None:
                    if d != self._date:
                        self._date = d
                        self._save_details()
                        self.date_changed.emit(d)
                    return

    def _refresh(self, force: bool = False) -> None:
        self._media = self._store.list_media() if self._store is not None else []
        self._update_default_date()
        for kind in MediaKind:
            rows = [self._row_for(m) for m in self._media if m.kind is kind] + self._dups[kind]
            if force or rows != self._rows[kind]:
                self._rows[kind] = rows
                self.rows_changed.emit(kind, list(rows))
        disk = self.disk_check()
        if force or disk != self._disk:
            self._disk = disk
            self.disk_changed.emit(disk)
        bad = self._bad_media()
        self.corrupt_name = bad[0].path.name if bad else None
        key = "disk" if not disk.ok else "corrupt" if bad else None
        if key != self.banner_key or force:
            self.banner_key = key
            self.banner.emit(key)
        ready = [
            m
            for m in self._media
            if m.kind is MediaKind.VIDEO
            and m.status is MediaStatus.OK
            and m.info is not None
            and m.id not in self._excluded
        ]
        heavy = [(m.info, m.info.duration_us / US_PER_S) for m in ready if m.info is not None]
        warn = over_target(heavy)
        self.eta_4k_s = project_remaining_s(
            [(profile_key(i), s) for i, s in heavy if profile_key(i) == "4k_hevc10"], DEFAULT_X_RT
        )
        if warn != self.warning_4k_active:
            self.warning_4k_active = warn
            self.warning_4k.emit(warn)
        can = bool(ready) and not self._import_active and not self._rerun and disk.ok
        if can != self._can_start:
            self._can_start = can
            self.can_start_changed.emit(can)

    @property
    def can_start(self) -> bool:
        return self._can_start
