"""Worker job `import`: copy chosen files into <project>/media, check integrity, probe."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING

from toystudio.core.models import MediaRow, MediaStatus
from toystudio.engine.analysis.single_pass import Stopped
from toystudio.engine.media.probe import check_integrity, probe_media
from toystudio.jobs.protocol import Event
from toystudio.storage.project_store import ProjectStore

if TYPE_CHECKING:
    from toystudio.jobs.worker_main import JobContext

BLOCK = 8 * 1024 * 1024
MEDIA_DIR = "media"


def _inside(path: Path, folder: Path) -> bool:
    return path.resolve().is_relative_to(folder.resolve())


def free_name(folder: Path, name: str) -> Path:
    """`folder/name`, or `name (2).ext`, `name (3).ext`... if it is taken."""
    target = folder / name
    stem, suffix = Path(name).stem, Path(name).suffix
    n = 2
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def copy_file(ctx: JobContext, media_id: int, src: Path, dst: Path) -> None:
    """Copy in 8 MB blocks via ``dst.part`` -> ``os.replace``; stoppable between blocks."""
    total = src.stat().st_size
    part = dst.with_name(dst.name + ".part")
    done = 0
    try:
        with src.open("rb") as fin, part.open("wb") as fout:
            ctx.report(Event("copy", {"media_id": media_id, "done_bytes": 0, "total_bytes": total}))
            while block := fin.read(BLOCK):
                if ctx.should_stop():
                    raise Stopped()
                fout.write(block)
                done += len(block)
                ctx.report(
                    Event("copy", {"media_id": media_id, "done_bytes": done, "total_bytes": total})
                )
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(part, dst)
    except BaseException:
        with contextlib.suppress(OSError):
            part.unlink()
        raise


def _mark(ctx: JobContext, store: ProjectStore, media_id: int, status: MediaStatus) -> None:
    store.set_media_status(media_id, status)
    ctx.report(Event("media", {"media_id": media_id, "status": status.value}))


def import_one(ctx: JobContext, store: ProjectStore, row: MediaRow) -> None:
    path = row.path
    if not path.is_file():
        _mark(ctx, store, row.id, MediaStatus.MISSING)
        return
    media_dir = store.root / MEDIA_DIR
    if row.copied and not _inside(path, media_dir):
        media_dir.mkdir(parents=True, exist_ok=True)
        dst = free_name(media_dir, path.name)
        copy_file(ctx, row.id, path, dst)
        store.set_media_path(row.id, dst, copied=True)
        path = dst
    if ctx.should_stop():
        raise Stopped()
    if row.info is not None:  # already probed: only the copy was missing
        ctx.report(
            Event(
                "media", {"media_id": row.id, "status": "ok", "duration_us": row.info.duration_us}
            )
        )
        return
    try:
        info = probe_media(path) if check_integrity(path) else None
    except ValueError:
        info = None
    if info is None:
        _mark(ctx, store, row.id, MediaStatus.CORRUPT)
        return
    store.set_media_info(row.id, info)
    store.set_media_status(row.id, MediaStatus.OK)
    ctx.report(
        Event("media", {"media_id": row.id, "status": "ok", "duration_us": info.duration_us})
    )


def _needs_copy(row: MediaRow, store: ProjectStore) -> bool:
    return row.copied and not _inside(row.path, store.root / MEDIA_DIR)


def run(ctx: JobContext) -> None:
    store = ProjectStore.open(Path(ctx.project))
    try:
        for row in store.list_media():
            if row.status is MediaStatus.OK and (row.info is None or _needs_copy(row, store)):
                if ctx.should_stop():
                    raise Stopped()
                import_one(ctx, store, row)
    finally:
        store.close()
