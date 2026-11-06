"""Main window: top bar plus a stack of pages."""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from toystudio.app.i18n import install_language
from toystudio.app.theme.apply import apply_theme, set_variant
from toystudio.app.widgets.top_bar import TopBar


def _t(text: str) -> str:
    return QCoreApplication.translate("Shell", text)


class Shell(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setMinimumSize(QSize(1280, 720))
        self.setWindowTitle("Той Студия")
        root = QWidget()
        root.setObjectName("Root")
        lay = QVBoxLayout(root)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.top_bar = TopBar()
        lay.addWidget(self.top_bar)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        page = QWidget()
        pl = QVBoxLayout(page)
        pl.setContentsMargins(32, 32, 32, 32)
        self._title = QLabel(_t("Проекты"))
        self._title.setStyleSheet("font-size: 24px; font-weight: 600;")
        self._new_btn = QPushButton(_t("Новый проект"))
        set_variant(self._new_btn, "variant", "primary")
        pl.addWidget(self._title)
        pl.addWidget(self._new_btn, 0, Qt.AlignmentFlag.AlignLeft)
        pl.addStretch(1)
        self.stack.addWidget(page)

        self.top_bar.language_changed.connect(self._on_language)
        self.top_bar.theme_changed.connect(self._on_theme)

    def page_title(self) -> str:
        return self._title.text()

    def set_language(self, code: Literal["ru", "ky"]) -> None:
        install_language(QApplication.instance(), code)  # type: ignore[arg-type]
        self.top_bar.set_language_checked(code)
        # installTranslator only posts LanguageChange; deliver it now so the switch is immediate.
        QApplication.sendEvent(self, QEvent(QEvent.Type.LanguageChange))

    def _on_language(self, code: str) -> None:
        self.set_language("ky" if code == "ky" else "ru")

    def _on_theme(self, theme: str) -> None:
        apply_theme(QApplication.instance(), "light" if theme == "light" else "dark")  # type: ignore[arg-type]

    def retranslate(self) -> None:
        self._title.setText(_t("Проекты"))
        self._new_btn.setText(_t("Новый проект"))

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
