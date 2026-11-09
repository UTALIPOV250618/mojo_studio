"""One local-analysis stage: state icon, human title, what was found, own progress when running."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from toystudio.app.theme.apply import set_variant
from toystudio.app.widgets.icons import IconLabel

StageState = Literal["waiting", "running", "done", "error", "later"]

# state -> (icon, icon colour token, found-label tone)
_LOOK: dict[str, tuple[str, str, str]] = {
    "waiting": ("clock", "ink-faint", "faint"),
    "running": ("spinner", "teal-400", "muted"),
    "done": ("check", "status-done", "done"),
    "error": ("alert", "status-error", "error"),
    "later": ("clock", "ink-faint", "faint"),
}


class AnalysisStage(QFrame):
    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AnalysisStage")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(12)
        self._icon = IconLabel(20)
        lay.addWidget(self._icon, 0, alignment=Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(4)
        self._title = QLabel(title)
        self._title.setProperty("role", "body")
        self.found_label = QLabel()
        self.found_label.setProperty("role", "caption")
        self.found_label.setWordWrap(True)
        self._bar = QProgressBar()
        set_variant(self._bar, "bar", "thin")
        self._bar.setRange(0, 100)
        self._bar.setTextVisible(False)
        for w in (self._title, self.found_label, self._bar):
            col.addWidget(w)
        lay.addLayout(col, 1)
        self._state: StageState = "waiting"
        self._found: str | None = None
        self.set_state("waiting")

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_state(
        self, state: StageState, found_text: str | None = None, percent: float | None = None
    ) -> None:
        self._state, self._found = state, found_text
        icon, color, tone = _LOOK[state]
        self._icon.set_icon(icon, color)
        set_variant(self, "state", state)
        set_variant(self._title, "tone", "faint" if state in ("waiting", "later") else "")
        set_variant(self.found_label, "tone", tone)
        self._bar.setVisible(state == "running" and percent is not None)
        self._bar.setValue(round((percent or 0) * 100))
        self.retranslate()

    def progress_value(self) -> int:
        return self._bar.value()

    def retranslate(self) -> None:
        if self._state == "later":
            text = QCoreApplication.translate("AnalysisStage", "Появится позже")
        else:
            text = self._found or ""
        self.found_label.setText(text)
        self.found_label.setVisible(bool(text))

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
