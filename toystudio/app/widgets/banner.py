"""Inline banner for errors, modes and information: what happened, what to do, 1-2 buttons."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QCoreApplication, QEvent, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from toystudio.app.theme.apply import set_variant
from toystudio.app.widgets.icons import IconLabel

_ICON = {
    "error": ("alert", "status-error"),
    "offline": ("cloud-off", "status-offline"),
    "info": ("info", "teal-400"),
}


class Banner(QFrame):
    action = Signal(str)

    def __init__(self, kind: Literal["error", "offline", "info"], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Banner")
        set_variant(self, "kind", kind)
        root = QHBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(12)
        icon_name, color = _ICON[kind]
        self._icon = IconLabel(20)
        self._icon.set_icon(icon_name, color)
        root.addWidget(self._icon, 0, alignment=Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        self._title = QLabel()
        self._title.setProperty("role", "heading")
        self._title.setWordWrap(True)
        self._body = QLabel()
        self._body.setProperty("role", "body")
        self._body.setWordWrap(True)
        self.support_link = QPushButton()
        self.support_link.setFlat(True)
        set_variant(self.support_link, "variant", "ghost")
        self.support_link.clicked.connect(self._toggle_detail)
        self.support_detail_label = QLabel()
        self.support_detail_label.setProperty("role", "caption")
        self.support_detail_label.setWordWrap(True)
        self.support_detail_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        for w in (self._title, self._body, self.support_link, self.support_detail_label):
            text.addWidget(w)
        root.addLayout(text, 1)
        self._buttons_box = QHBoxLayout()
        self._buttons_box.setSpacing(8)
        root.addLayout(self._buttons_box)
        self._buttons: list[QPushButton] = []
        self.support_link.hide()
        self.support_detail_label.hide()
        self.retranslate()

    def set_content(
        self,
        title: str,
        body: str,
        actions: list[tuple[str, str]],
        support_detail: str | None = None,
    ) -> None:
        self._title.setText(title)
        self._body.setText(body)
        self._body.setVisible(bool(body))
        for b in self._buttons:
            self._buttons_box.removeWidget(b)
            b.deleteLater()
        self._buttons = []
        for i, (key, text) in enumerate(actions):
            b = QPushButton(text)
            if i == 0:
                set_variant(b, "variant", "primary")
            b.clicked.connect(lambda _=False, k=key: self.action.emit(k))
            self._buttons_box.addWidget(b)
            self._buttons.append(b)
        self.support_detail_label.setText(support_detail or "")
        self.support_detail_label.hide()
        self.support_link.setVisible(bool(support_detail))

    def action_buttons(self) -> list[QPushButton]:
        return list(self._buttons)

    def _toggle_detail(self) -> None:
        self.support_detail_label.setVisible(not self.support_detail_label.isVisible())

    def retranslate(self) -> None:
        self.support_link.setText(QCoreApplication.translate("Banner", "Подробнее для поддержки"))

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
