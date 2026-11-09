"""Screen 3: analysis progress - five stages, overall bar, time left, stop/resume.

The screen only displays its view-model. The Shell keeps the view-models of running
analyses alive and drives the project card, the taskbar and the finish notification.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from toystudio.app.platform.notify import Notifier
from toystudio.app.theme.apply import set_variant, token
from toystudio.app.viewmodels.analysis_vm import AnalysisVM
from toystudio.app.widgets.analysis_stage import AnalysisStage
from toystudio.app.widgets.banner import Banner
from toystudio.app.widgets.empty_state import EmptyState
from toystudio.app.widgets.icons import icon


class AnalysisScreen(QWidget):
    def __init__(
        self,
        vm: AnalysisVM,
        notifier: Notifier | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.vm = vm
        self._notifier = notifier or Notifier(QApplication.windowIcon())
        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)
        columns = QHBoxLayout()
        columns.setSpacing(32)

        # ---- left: progress
        left = QVBoxLayout()
        left.setSpacing(8)
        self.error_banner = Banner("error")
        self.error_banner.hide()
        self.overall_bar = QProgressBar()
        set_variant(self.overall_bar, "bar", "thin")
        self.overall_bar.setRange(0, 100)
        self.overall_bar.setTextVisible(False)
        self.eta_label = QLabel()
        self.eta_label.setProperty("role", "timecode-lg")
        self._offline_note = QLabel()
        self._offline_note.setProperty("role", "caption")
        self._offline_note.setProperty("tone", "muted")
        left.addWidget(self.error_banner)
        left.addWidget(self.overall_bar)
        left.addWidget(self.eta_label)
        left.addWidget(self._offline_note)
        left.addSpacing(8)
        self.stage_widgets = [AnalysisStage(v.title) for v in vm.stages]
        for w in self.stage_widgets:
            left.addWidget(w)
        failed_row = QHBoxLayout()
        self.failed_label = QLabel()
        self.failed_label.setProperty("role", "body")
        self.failed_label.setProperty("tone", "error")
        self.failed_label.setWordWrap(True)
        self.retry_button = QPushButton()
        self.replace_hint = QLabel()  # corrupt files fail again on rerun: replace them
        self.replace_hint.setProperty("role", "body")
        self.replace_hint.setProperty("tone", "muted")
        failed_row.addWidget(self.failed_label, 1)
        failed_row.addWidget(self.retry_button)
        failed_row.addWidget(self.replace_hint)
        self._failed_box = QWidget()
        self._failed_box.setLayout(failed_row)
        left.addWidget(self._failed_box)
        missing_row = QHBoxLayout()
        self.missing_label = QLabel()
        self.missing_label.setProperty("role", "body")
        self.missing_label.setProperty("tone", "error")
        self.missing_label.setWordWrap(True)
        self.missing_retry_button = QPushButton()
        missing_row.addWidget(self.missing_label, 1)
        missing_row.addWidget(self.missing_retry_button)
        self._missing_box = QWidget()
        self._missing_box.setLayout(missing_row)
        left.addWidget(self._missing_box)
        self.saved_label = QLabel()
        self.saved_label.setProperty("role", "body")
        self.saved_label.setProperty("tone", "muted")
        self.done_label = QLabel()
        self.done_label.setProperty("role", "heading")
        self.done_label.setProperty("tone", "done")
        left.addWidget(self.saved_label)
        left.addWidget(self.done_label)
        left.addStretch(1)
        columns.addLayout(left, 60)

        # ---- right: found so far (moments arrive in phase 2b)
        right = QVBoxLayout()
        self._found_title = QLabel()
        self._found_title.setProperty("role", "heading")
        self.found_empty = EmptyState("film", "", "", None)
        right.addWidget(self._found_title)
        right.addWidget(self.found_empty, 1)
        columns.addLayout(right, 40)
        root.addLayout(columns, 1)

        # ---- bottom
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.notify_check = QCheckBox()
        self.notify_check.setChecked(vm.notify_when_done)
        self.minimize_button = QPushButton()
        self.stop_button = QPushButton()
        set_variant(self.stop_button, "variant", "ghost")
        self.resume_button = QPushButton()
        set_variant(self.resume_button, "variant", "primary")
        bottom.addWidget(self.notify_check)
        bottom.addStretch(1)
        for b in (self.minimize_button, self.stop_button, self.resume_button):
            bottom.addWidget(b)
        root.addLayout(bottom)

        self.stop_button.clicked.connect(vm.stop)
        self.resume_button.clicked.connect(vm.resume)
        self.retry_button.clicked.connect(vm.resume)
        self.missing_retry_button.clicked.connect(vm.resume)
        self.error_banner.action.connect(lambda _key: vm.resume())
        self.minimize_button.clicked.connect(lambda: self._notifier.minimize_to_tray(self.window()))
        self.notify_check.toggled.connect(lambda on: setattr(vm, "notify_when_done", on))
        vm.changed.connect(self._render)
        vm.finished.connect(self._on_finished)
        vm.failed.connect(self._on_failed)
        self._failure: tuple[str, str] | None = None
        self.retranslate()

    # ---- vm -> widgets
    def _render(self) -> None:
        vm = self.vm
        if vm.running:
            self._failure = None  # a new run supersedes the old crash banner
        for w, view in zip(self.stage_widgets, vm.stages, strict=True):
            w.set_title(view.title)
            w.set_state(view.state, view.found_text, view.percent)
        self.overall_bar.setValue(round(vm.overall_percent))
        self.eta_label.setText(vm.eta_text or "")
        self.eta_label.setVisible(vm.running and bool(vm.eta_text))
        running, resume = vm.running, vm.can_resume
        self.notify_check.setVisible(running)
        self.minimize_button.setVisible(running)
        self.stop_button.setVisible(running)
        self.stop_button.setEnabled(not vm.stopping)
        self.resume_button.setVisible(resume)
        self.saved_label.setVisible(resume)
        self.done_label.setVisible(vm.done and not vm.all_failed)
        self._offline_note.setVisible(running)
        failed = [] if running else vm.failed_files
        self._failed_box.setVisible(bool(failed) and not vm.all_failed)
        self.retry_button.setVisible(vm.failed_retryable)
        self.replace_hint.setVisible(not vm.failed_retryable)
        self.failed_label.setText(
            QCoreApplication.translate("AnalysisScreen", "Не удалось проанализировать: %1").replace(
                "%1", ", ".join(failed)
            )
        )
        missing = [] if running else vm.missing_files
        self._missing_box.setVisible(bool(missing))
        self.missing_label.setText(
            "\n".join(
                QCoreApplication.translate(
                    "AnalysisScreen",
                    "Файл не найден: %1. Подключите диск с ним или замените файл.",
                ).replace("%1", name)
                for name in missing
            )
        )
        self._render_banner()

    def _render_banner(self) -> None:
        vm = self.vm
        if self._failure is not None and not vm.running:
            code, detail = self._failure
            title = QCoreApplication.translate("AnalysisScreen", "Анализ остановился из-за ошибки.")
            body = QCoreApplication.translate(
                "AnalysisScreen", "Пройденные этапы сохранены. Попробуйте продолжить."
            )
            self.error_banner.set_content(
                title,
                body,
                [("retry", QCoreApplication.translate("AnalysisScreen", "Повторить"))],
                f"{code}: {detail}".strip(": "),
            )
            self.error_banner.show()
        elif vm.all_failed and not vm.running:
            title = QCoreApplication.translate(
                "AnalysisScreen", "Ни один файл не удалось проанализировать."
            )
            body = QCoreApplication.translate("AnalysisScreen", "Проверьте файлы на шаге «Файлы».")
            self.error_banner.set_content(title, body, [])
            self.error_banner.show()
        else:
            self.error_banner.hide()

    def _on_finished(self, _cuts: int) -> None:
        self._failure = None
        self._render()

    def _on_failed(self, code: str, detail: str) -> None:
        self._failure = (code, detail)
        self._render()

    # ---- i18n
    def retranslate(self) -> None:
        self.vm.retranslate()
        self._offline_note.setText(
            QCoreApplication.translate(
                "AnalysisScreen", "Анализ идёт на этом компьютере, интернет не нужен"
            )
        )
        self._found_title.setText(QCoreApplication.translate("AnalysisScreen", "Уже найдено"))
        self.found_empty.set_texts(
            QCoreApplication.translate(
                "AnalysisScreen", "Моменты появятся после поиска тостов и танцев"
            ),
            "",
            None,
        )
        self.notify_check.setText(
            QCoreApplication.translate("AnalysisScreen", "Уведомить, когда закончится")
        )
        self.notify_check.setIcon(icon("bell", token("ink-muted"), 16))
        self.minimize_button.setText(QCoreApplication.translate("AnalysisScreen", "Свернуть"))
        self.stop_button.setText(QCoreApplication.translate("AnalysisScreen", "Остановить"))
        self.resume_button.setText(
            QCoreApplication.translate("AnalysisScreen", "Продолжить анализ")
        )
        self.retry_button.setText(QCoreApplication.translate("AnalysisScreen", "Повторить"))
        self.missing_retry_button.setText(QCoreApplication.translate("AnalysisScreen", "Повторить"))
        self.replace_hint.setText(
            QCoreApplication.translate("AnalysisScreen", "Замените файл на шаге «Файлы».")
        )
        self.saved_label.setText(
            QCoreApplication.translate("AnalysisScreen", "Пройденные этапы сохранены")
        )
        self.done_label.setText(QCoreApplication.translate("AnalysisScreen", "Анализ завершён"))
        self._render()

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
