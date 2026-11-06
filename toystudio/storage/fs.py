"""Crash-safe file writes."""

import contextlib
import os
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
