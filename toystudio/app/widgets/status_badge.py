"""Status badge: always an icon plus a word; colour only reinforces the meaning."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from toystudio.app.theme.apply import set_variant
from toystudio.app.widgets.icons import IconLabel

Status = Literal["analysis", "analysis_done", "ready", "exported", "error", "offline"]

# kind -> (qss badge value, icon, icon colour token, word)
_SPEC: dict[str, tuple[str, str, str, str]] = {
    "analysis": ("analysis", "spinner", "teal-400", QT_TRANSLATE_NOOP("StatusBadge", "Анализ")),
    "analysis_done": (
        "done",
        "check",
        "status-done",
        QT_TRANSLATE_NOOP("StatusBadge", "Анализ завершён"),
    ),
    "ready": (
        "ready",
        "pencil",
        "status-ready",
        QT_TRANSLATE_NOOP("StatusBadge", "Черновик готов"),
    ),
    "exported": (
        "done",
        "check",
        "status-done",
        QT_TRANSLATE_NOOP("StatusBadge", "Экспортирован"),
    ),
    "error": ("error", "alert", "status-error", QT_TRANSLATE_NOOP("StatusBadge", "Ошибка")),
    "offline": (
        "offline",
        "cloud-off",
        "status-offline",
        QT_TRANSLATE_NOOP("StatusBadge", "Нет интернета"),
    ),
}


class StatusBadge(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBadge")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 10, 4)
        lay.setSpacing(4)
        self._icon = IconLabel(14)
        self._label = QLabel()
        self._label.setProperty("role", "label")
        lay.addWidget(self._icon)
        lay.addWidget(self._label)
        self._kind: Status = "analysis"
        self._percent: int | None = None
        self.set_status("analysis")

    def set_status(self, kind: Status, percent: int | None = None) -> None:
        self._kind, self._percent = kind, percent
        badge, icon_name, color, _ = _SPEC[kind]
        self._icon.set_icon(icon_name, color)
        set_variant(self, "badge", badge)
        self.retranslate()

    def kind(self) -> Status:
        return self._kind

    def text(self) -> str:
        return self._label.text()

    def has_icon(self) -> bool:
        return self._icon.has_icon()

    def retranslate(self) -> None:
        word = QCoreApplication.translate("StatusBadge", _SPEC[self._kind][3])
        if self._kind == "analysis" and self._percent is not None:
            word = f"{word} · {self._percent}%"
        self._label.setText(word)

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
