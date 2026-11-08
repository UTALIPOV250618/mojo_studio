"""Apply generated QSS themes and bundled fonts."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QWidget

from toystudio.app.theme.generated import tokens

log = logging.getLogger(__name__)
_current: Literal["dark", "light"] = "dark"
_GENERATED = Path(__file__).resolve().parent / "generated"
_FONTS = Path(__file__).resolve().parents[1] / "fonts"


def apply_theme(app: QApplication, theme: Literal["dark", "light"]) -> None:
    global _current
    _current = theme
    app.setStyleSheet((_GENERATED / f"theme_{theme}.qss").read_text("utf-8"))


def token(name: str) -> str:
    """Colour token of the currently applied theme (for icons and painting)."""
    return (tokens.LIGHT if _current == "light" else tokens.DARK)[name]


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
