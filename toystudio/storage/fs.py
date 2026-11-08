"""Crash-safe file writes."""

import contextlib
import os
import shutil
import sys
from pathlib import Path


def atomic_write(path: Path, data: bytes) -> None:
    """Write via a sibling temp file, fsync, then os.replace; the original survives failures."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def free_bytes(path: Path) -> int:
    """Free space on the volume of `path`, or of its nearest existing parent."""
    p = path.absolute()
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free


def is_removable(path: Path) -> bool:
    """True for removable drives on Windows; always False elsewhere."""
    if sys.platform != "win32":
        return False
    import ctypes

    drive = os.path.splitdrive(path.absolute())[0]
    if not drive:
        return False
    drive_removable = 2
    return bool(ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == drive_removable)  # type: ignore[attr-defined,unused-ignore]
