"""UI translations: ky.ts is committed, ky.qm is built by pyside6-lrelease."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QCoreApplication, QTranslator

_DIR = Path(__file__).resolve().parent
_translator: QTranslator | None = None


def _lrelease() -> str:
    cands = [Path(sys.executable).parent / "pyside6-lrelease", shutil.which("pyside6-lrelease")]
    for c in cands:
        if c and Path(c).exists():
            return str(c)
    import PySide6

    bundled = Path(PySide6.__file__).parent / "lrelease"
    if bundled.exists():
        return str(bundled)
    raise RuntimeError("pyside6-lrelease not found")


def build_translations() -> Path:
    """Compile ky.ts to ky.qm when the .qm is missing or older than the .ts."""
    ts, qm = _DIR / "ky.ts", _DIR / "ky.qm"
    if not ts.exists():  # frozen app: prebuilt .qm is shipped next to the module
        return qm
    if not qm.exists() or qm.stat().st_mtime < ts.stat().st_mtime:
        subprocess.run([_lrelease(), str(ts), "-qm", str(qm)], check=True, capture_output=True)
    return qm


def install_language(app: QCoreApplication, code: Literal["ru", "ky"]) -> None:
    """Install (ky) or remove (ru, the source language) the app translator."""
    global _translator
    if _translator is not None:
        app.removeTranslator(_translator)
        _translator = None
    if code == "ky":
        tr = QTranslator(app)
        if tr.load(str(build_translations())):
            app.installTranslator(tr)
            _translator = tr
