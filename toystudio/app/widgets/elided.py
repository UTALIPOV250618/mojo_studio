"""Widgets that elide their text on the right and expose the full text as tooltip."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter, QPaintEvent, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionButton,
    QStylePainter,
    QWidget,
)


class ElidedButton(QPushButton):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        self._full = text
        super().setText(text)
        self._sync()

    def _text_width(self) -> int:
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        return (
            self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, opt, self).width()
        )

    def _elided(self) -> str:
        return self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, self._text_width()
        )

    def _sync(self) -> None:
        self.setToolTip(self._full if self._elided() != self._full else "")

    def minimumSizeHint(self) -> QSize:
        return QSize(32, super().sizeHint().height())

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._sync()

    def showEvent(self, e: QShowEvent) -> None:
        super().showEvent(e)
        self._sync()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QStylePainter(self)
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.text = self._elided()
        p.drawControl(QStyle.ControlElement.CE_PushButton, opt)


class ElidedLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        self._full = text
        super().setText(text)
        self._sync()

    def _elided(self) -> str:
        return self.fontMetrics().elidedText(
            self._full, Qt.TextElideMode.ElideRight, self.contentsRect().width()
        )

    def _sync(self) -> None:
        self.setToolTip(self._full if self._elided() != self._full else "")

    def minimumSizeHint(self) -> QSize:
        return QSize(16, super().sizeHint().height())

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self._sync()

    def showEvent(self, e: QShowEvent) -> None:
        super().showEvent(e)
        self._sync()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setPen(self.palette().windowText().color())
        p.drawText(self.contentsRect(), int(self.alignment()), self._elided())
