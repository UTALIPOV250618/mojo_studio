"""UI translations: ky.ts is committed, ky.qm is built by pyside6-lrelease."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QCoreApplication, QTranslator

log = logging.getLogger(__name__)
_DIR = Path(__file__).resolve().parent
_translator: QTranslator | None = None


def _lrelease() -> str:
    names = ("pyside6-lrelease", "pyside6-lrelease.exe")
    cands: list[str | Path | None] = []
    scripts = Path(sys.executable).parent
    for d in (scripts, scripts / "Scripts"):
        cands.extend(d / n for n in names)
    cands.extend(shutil.which(n) for n in names)
    import PySide6

    pyside_dir = Path(PySide6.__file__).parent
    cands.extend(pyside_dir / n for n in ("lrelease", "lrelease.exe"))
    for c in cands:
        if c and Path(c).is_file():
            return str(c)
    raise RuntimeError("pyside6-lrelease not found")


def build_translations() -> Path:
    """Compile ky.ts to ky.qm when the .qm is missing or older than the .ts."""
    ts, qm = _DIR / "ky.ts", _DIR / "ky.qm"
    if not ts.exists():  # frozen app: prebuilt .qm is shipped next to the module
        return qm
    if not qm.exists() or qm.stat().st_mtime < ts.stat().st_mtime:
        subprocess.run([_lrelease(), str(ts), "-qm", str(qm)], check=True, capture_output=True)
    return qm


def install_language(app: QCoreApplication, code: Literal["ru", "ky"]) -> bool:
    """Install (ky) or remove (ru, the source language) the app translator.

    Returns True when the requested language is active; on failure the UI stays Russian.
    """
    global _translator
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
    if code != "ky":
        return True
    try:
        qm = build_translations()
    except (RuntimeError, OSError, subprocess.CalledProcessError):
        log.exception("could not build Kyrgyz translations; staying in Russian")
        return False
    tr = QTranslator(app)
    if not tr.load(str(qm)):
        log.error("could not load translation file %s; staying in Russian", qm)
        return False
    app.installTranslator(tr)
    _translator = tr
    return True
