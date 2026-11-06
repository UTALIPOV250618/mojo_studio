"""Apply generated QSS themes and bundled fonts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

log = logging.getLogger(__name__)
_GENERATED = Path(__file__).resolve().parent / "generated"
_FONTS = Path(__file__).resolve().parents[1] / "fonts"


def apply_theme(app: QApplication, theme: Literal["dark", "light"]) -> None:
    app.setStyleSheet((_GENERATED / f"theme_{theme}.qss").read_text("utf-8"))


def set_variant(w: QWidget, name: str, value: str) -> None:
    w.setProperty(name, value)
    style = w.style()
    style.unpolish(w)
    style.polish(w)
    w.update()


def load_fonts() -> None:
    files = sorted(_FONTS.glob("*.ttf"))
    if not files:
        log.info("no bundled fonts in %s; falling back to Segoe UI / system font", _FONTS)
        return
    for f in files:
        if QFontDatabase.addApplicationFont(str(f)) < 0:
            log.warning("failed to load font %s; falling back to Segoe UI", f)
