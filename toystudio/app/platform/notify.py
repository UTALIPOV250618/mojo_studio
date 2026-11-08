"""Windows notification through the tray icon, and minimize-to-tray."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QWidget


class Notifier:
    def __init__(self, icon: QIcon) -> None:
        self._icon = icon
        self._tray: QSystemTrayIcon | None = None
        self._on_click: Callable[[], None] | None = None
        self._window: QWidget | None = None

    def _ensure_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        if self._tray is None:
            tray = QSystemTrayIcon(self._icon)
            tray.messageClicked.connect(self._clicked)
            tray.activated.connect(self._tray_activated)
            self._tray = tray
        self._tray.show()
        return self._tray

    def notify(self, title: str, body: str, on_click: Callable[[], None]) -> None:
        tray = self._ensure_tray()
        if tray is None:
            return
        self._on_click = on_click
        tray.showMessage(title, body, self._icon)

    def minimize_to_tray(self, window: QWidget) -> None:
        tray = self._ensure_tray()
        if tray is None:
            window.showMinimized()
            return
        self._window = window
        window.hide()

    def _clicked(self) -> None:
        if self._on_click is not None:
            self._on_click()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if (
            reason
            in (
                QSystemTrayIcon.ActivationReason.Trigger,
                QSystemTrayIcon.ActivationReason.DoubleClick,
            )
            and self._window is not None
        ):
            self._window.showNormal()
            self._window.activateWindow()
