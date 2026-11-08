"""Small inline Lucide-style stroke icons rendered from SVG strings."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QEvent, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QWidget

from toystudio.app.theme.apply import token
from toystudio.app.theme.generated import tokens

APP_ICON_SIZE = 256

_BODY: dict[str, str] = {
    "spinner": '<path d="M21 12a9 9 0 1 1-6.219-8.56"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "alert": '<circle cx="12" cy="12" r="10"/><path d="M12 8v4"/><path d="M12 16h.01"/>',
    "cloud-off": (
        '<path d="m2 2 20 20"/><path d="M5.782 5.782A7 7 0 0 0 9 19h8.5a4.5 4.5 0 0 0 1.307-.193"/>'
        '<path d="M21.532 16.5A4.5 4.5 0 0 0 17.5 10h-1.79A7.008 7.008 0 0 0 10 5.07"/>'
    ),
    "pencil": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/>',
    "film": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/>'
        '<path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/>'
        '<path d="M17 16.5h4"/>'
    ),
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>',
    "upload": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/>'
        '<path d="M12 3v12"/>'
    ),
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
}

NAMES = frozenset(_BODY)


def _svg(name: str, color: str) -> bytes:
    if name not in _BODY:
        raise KeyError(f"unknown icon: {name}")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{_BODY[name]}</svg>"
    ).encode()


def pixmap(name: str, color: str, size: int = 16) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(_svg(name, color)))
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    renderer.render(p)
    p.end()
    return pm


def icon(name: str, color: str, size: int = 16) -> QIcon:
    return QIcon(pixmap(name, color, size))


def app_icon(size: int = APP_ICON_SIZE) -> QIcon:
    """Window and tray icon: the film glyph on a garnet rounded square (theme-independent)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(tokens.DARK["garnet-500"]))
    p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    inset = size * 0.18
    glyph = QSvgRenderer(QByteArray(_svg("film", tokens.DARK["ink-on-accent"])))
    glyph.render(p, QRectF(inset, inset, size - 2 * inset, size - 2 * inset))
    p.end()
    return QIcon(pm)


class IconLabel(QLabel):
    """Shows an icon coloured by a theme token; re-renders when the theme changes."""

    def __init__(self, size: int = 16, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self._name = ""
        self._token = "ink"
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_icon(self, name: str, color_token: str) -> None:
        self._name, self._token = name, color_token
        self._render()

    def icon_name(self) -> str:
        return self._name

    def has_icon(self) -> bool:
        pm = self.pixmap()
        return bool(self._name) and pm is not None and not pm.isNull()

    def _render(self) -> None:
        if self._name:
            self.setPixmap(pixmap(self._name, token(self._token), self._size))

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.StyleChange and self._name:
            self._render()
        super().changeEvent(e)
