"""48 px top bar: logo, project stepper, RU/KY switch, theme switch."""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QPushButton, QWidget

from toystudio.app.theme.apply import set_variant
from toystudio.app.widgets.elided import ElidedButton

_STEPS = (
    QT_TRANSLATE_NOOP("Shell", "Файлы"),
    QT_TRANSLATE_NOOP("Shell", "Анализ"),
    QT_TRANSLATE_NOOP("Shell", "Задание"),
    QT_TRANSLATE_NOOP("Shell", "Черновик"),
    QT_TRANSLATE_NOOP("Shell", "Экспорт"),
)
_LANG_LABELS = {"ru": "Русский", "ky": "Кыргызча"}


class TopBar(QWidget):
    language_changed = Signal(str)
    theme_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TopBar")
        self.setFixedHeight(48)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self._theme = "dark"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 16, 0)
        lay.setSpacing(8)
        self._logo = QLabel("Той Студия")
        lay.addWidget(self._logo)
        lay.addSpacing(16)
        self._steps: list[ElidedButton] = []
        self._step_group = QButtonGroup(self)
        self._step_group.setExclusive(True)
        for i, text in enumerate(_STEPS):
            b = ElidedButton(QCoreApplication.translate("Shell", text))
            b.setCheckable(True)
            b.setEnabled(i == 0)
            set_variant(b, "variant", "ghost")
            self._step_group.addButton(b)
            lay.addWidget(b)
            self._steps.append(b)
        self._current = 0
        self._steps[0].setChecked(True)
        lay.addStretch(1)
        self._lang: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        group.setExclusive(True)
        for code, label in _LANG_LABELS.items():
            b = QPushButton(label)
            b.setCheckable(True)
            b.setProperty("chip", "true")
            b.clicked.connect(lambda _=False, c=code: self.language_changed.emit(c))
            group.addButton(b)
            lay.addWidget(b)
            self._lang[code] = b
        self._lang["ru"].setChecked(True)
        self._theme_btn = QPushButton(QCoreApplication.translate("Shell", "Сменить тему"))
        set_variant(self._theme_btn, "variant", "ghost")
        self._theme_btn.clicked.connect(self._toggle_theme)
        lay.addWidget(self._theme_btn)

    def _toggle_theme(self) -> None:
        self._theme = "light" if self._theme == "dark" else "dark"
        self.theme_changed.emit(self._theme)

    def set_step(self, i: int, reachable: int) -> None:
        self._current = i
        for n, b in enumerate(self._steps):
            b.setChecked(n == i)
            b.setEnabled(n <= reachable)

    def current_step(self) -> int:
        return self._current

    def step_labels(self) -> list[str]:
        return [b.full_text() for b in self._steps]

    def step_enabled(self) -> list[bool]:
        return [b.isEnabled() for b in self._steps]

    def language_labels(self) -> tuple[str, str]:
        return (self._lang["ru"].text(), self._lang["ky"].text())

    def set_language_checked(self, code: str) -> None:
        self._lang[code].setChecked(True)

    def click_language(self, code: str) -> None:
        self._lang[code].click()

    def click_theme(self) -> None:
        self._theme_btn.click()

    def retranslate(self) -> None:
        for b, text in zip(self._steps, _STEPS, strict=True):
            b.setText(QCoreApplication.translate("Shell", text))
        self._theme_btn.setText(QCoreApplication.translate("Shell", "Сменить тему"))

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
