"""Screen 2: project details on the left, video and music drop zones on the right."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QDate, QEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from toystudio.app.screens.projects import _KIND_TEXT
from toystudio.app.theme.apply import set_variant
from toystudio.app.viewmodels.new_project_vm import DiskCheck, NewProjectVM
from toystudio.app.widgets.banner import Banner
from toystudio.app.widgets.drop_zone import MUSIC_EXT, VIDEO_EXT, DropZone
from toystudio.core.format import format_eta, format_size
from toystudio.core.models import MediaKind, ToiKind

_CHIP_COLUMNS = 3


def _scrolling(zone: DropZone) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setMinimumHeight(110)
    area.setWidget(zone)
    return area


def _filter(kind: MediaKind) -> str:
    exts = VIDEO_EXT if kind is MediaKind.VIDEO else MUSIC_EXT
    return " ".join(f"*{x}" for x in sorted(exts))


class NewProjectScreen(QWidget):
    def __init__(self, vm: NewProjectVM, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.vm = vm
        self._disk: DiskCheck | None = None
        self._has_files = False
        root = QHBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(32)

        # ---- left: about the toi
        left = QVBoxLayout()
        left.setSpacing(8)
        self._title = QLabel()
        self._title.setProperty("role", "title")
        self._name_label = QLabel()
        self._name_label.setProperty("role", "label")
        self.name_edit = QLineEdit()
        self._date_label = QLabel()
        self._date_label.setProperty("role", "label")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd.MM.yyyy")
        d = vm.event_date
        self.date_edit.setDate(QDate(d.year, d.month, d.day))
        self._kind_label = QLabel()
        self._kind_label.setProperty("role", "label")
        chips = QGridLayout()
        chips.setSpacing(8)
        self._group = QButtonGroup(self)
        self.chips: dict[ToiKind, QPushButton] = {}
        for i, kind in enumerate(ToiKind):
            b = QPushButton()
            b.setCheckable(True)
            b.setProperty("chip", "true")
            self._group.addButton(b)
            self.chips[kind] = b
            chips.addWidget(b, i // _CHIP_COLUMNS, i % _CHIP_COLUMNS)
            b.clicked.connect(lambda _=False, k=kind: setattr(self.vm, "kind", k))
        self.chips[vm.kind].setChecked(True)
        left.addWidget(self._title)
        left.addSpacing(8)
        left.addWidget(self._name_label)
        left.addWidget(self.name_edit)
        left.addSpacing(8)
        left.addWidget(self._date_label)
        left.addWidget(self.date_edit)
        left.addSpacing(8)
        left.addWidget(self._kind_label)
        left.addLayout(chips)
        left.addStretch(1)
        root.addLayout(left, 40)

        # ---- right: files
        right = QVBoxLayout()
        right.setSpacing(12)
        self.error_banner = Banner("error")
        self.info_banner = Banner("info")
        self.error_banner.hide()
        self.info_banner.hide()
        self.video_zone = DropZone(MediaKind.VIDEO)
        self.music_zone = DropZone(MediaKind.MUSIC)
        right.addWidget(self.error_banner)
        right.addWidget(self.info_banner)
        right.addWidget(_scrolling(self.video_zone), 6)
        right.addWidget(_scrolling(self.music_zone), 4)
        self._disk_label = QLabel()
        self._disk_label.setProperty("role", "label")
        self._disk_label.setProperty("tone", "muted")
        self._disk_label.hide()
        right.addWidget(self._disk_label)
        copy_row = QHBoxLayout()
        self.copy_check = QCheckBox()
        self.removable_label = QLabel()
        self.removable_label.setProperty("role", "caption")
        self.removable_label.setProperty("tone", "muted")
        self.removable_label.hide()
        copy_row.addWidget(self.copy_check)
        copy_row.addWidget(self.removable_label)
        copy_row.addStretch(1)
        right.addLayout(copy_row)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.start_button = QPushButton()
        set_variant(self.start_button, "variant", "primary")
        self.start_button.setMinimumHeight(40)
        self.start_button.setEnabled(False)
        bottom.addWidget(self.start_button)
        right.addLayout(bottom)
        root.addLayout(right, 60)

        # ---- wiring
        self.name_edit.textChanged.connect(lambda t: setattr(self.vm, "name", t))
        self.date_edit.dateChanged.connect(self._on_date_edited)
        self.copy_check.toggled.connect(lambda on: setattr(self.vm, "copy", on))
        self.video_zone.files_dropped.connect(lambda p: vm.add_files(MediaKind.VIDEO, p))
        self.music_zone.files_dropped.connect(lambda p: vm.add_files(MediaKind.MUSIC, p))
        self.video_zone.replace_requested.connect(lambda m: self._replace(int(m), MediaKind.VIDEO))
        self.music_zone.replace_requested.connect(lambda m: self._replace(int(m), MediaKind.MUSIC))
        self.error_banner.action.connect(self._on_banner_action)
        self.start_button.clicked.connect(vm.start_analysis)
        vm.rows_changed.connect(self._on_rows)
        vm.disk_changed.connect(self._on_disk)
        vm.banner.connect(lambda _key: self._update_banner())
        vm.can_start_changed.connect(self.start_button.setEnabled)
        vm.warning_4k.connect(lambda _on: self._update_info_banner())
        vm.copy_changed.connect(lambda _on: (self._sync_copy(), self._update_banner()))
        vm.date_changed.connect(self._on_vm_date)
        self.retranslate()

    # ---- vm -> widgets
    def _on_rows(self, kind: MediaKind, rows: list) -> None:
        (self.video_zone if kind is MediaKind.VIDEO else self.music_zone).set_rows(rows)
        self._has_files = bool(self.vm.rows(MediaKind.VIDEO) or self.vm.rows(MediaKind.MUSIC))
        self._sync_copy()
        self._update_disk_label()
        self._update_banner()

    def _on_disk(self, disk: DiskCheck) -> None:
        self._disk = disk
        self._update_disk_label()
        self._update_banner()

    def _on_vm_date(self, d: object) -> None:
        self.date_edit.blockSignals(True)
        self.date_edit.setDate(QDate(d.year, d.month, d.day))  # type: ignore[attr-defined]
        self.date_edit.blockSignals(False)

    def _on_date_edited(self, qd: QDate) -> None:
        self.vm.event_date = qd.toPython()

    def _sync_copy(self) -> None:
        self.copy_check.blockSignals(True)
        self.copy_check.setChecked(self.vm.copy)
        self.copy_check.blockSignals(False)
        self.removable_label.setVisible(self.vm.copy and self.vm.removable_hint)

    # ---- texts that depend on state
    def disk_text(self) -> str:
        disk = self._disk
        if disk is None:
            return ""
        free, need = format_size(disk.free), format_size(disk.needed)
        if disk.drive:
            text = QCoreApplication.translate(
                "NewProjectScreen", "На диске %1 свободно %2, нужно ~%3"
            )
            return text.replace("%1", disk.drive).replace("%2", free).replace("%3", need)
        text = QCoreApplication.translate("NewProjectScreen", "На диске: свободно %1, нужно ~%2")
        return text.replace("%1", free).replace("%2", need)

    def _update_disk_label(self) -> None:
        self._disk_label.setText(self.disk_text())
        self._disk_label.setVisible(self._has_files and self._disk is not None)

    def _update_banner(self) -> None:
        key = self.vm.banner_key
        if key == "disk" and self._disk is not None:
            shortage = format_size(self._disk.shortage)
            if self._disk.drive:
                title = QCoreApplication.translate(
                    "NewProjectScreen", "На диске %1 не хватает места — нужно ещё %2."
                )
                title = title.replace("%1", self._disk.drive).replace("%2", shortage)
            else:
                title = QCoreApplication.translate(
                    "NewProjectScreen", "На диске не хватает места — нужно ещё %1."
                )
                title = title.replace("%1", shortage)
            recheck = ("recheck", QCoreApplication.translate("NewProjectScreen", "Проверить снова"))
            if self.vm.root is None:
                body = QCoreApplication.translate(
                    "NewProjectScreen", "Освободите место или выберите другую папку для копии."
                )
                choose = QCoreApplication.translate("NewProjectScreen", "Выбрать папку")
                actions = [("choose_folder", choose), recheck]
            else:
                body = QCoreApplication.translate(
                    "NewProjectScreen", "Освободите место или не копируйте файлы в папку проекта."
                )
                actions = [recheck]
                if self.vm.copy:
                    no_copy = QCoreApplication.translate("NewProjectScreen", "Не копировать")
                    actions.insert(0, ("no_copy", no_copy))
            self.error_banner.set_content(title, body, actions)
            self.error_banner.show()
            return
        elif key == "import_failed" and self.vm.import_failure is not None:
            code, detail = self.vm.import_failure
            title = QCoreApplication.translate("NewProjectScreen", "Не удалось проверить файлы.")
            body = QCoreApplication.translate("NewProjectScreen", "Попробуйте ещё раз.")
            actions = [("retry", QCoreApplication.translate("NewProjectScreen", "Повторить"))]
            self.error_banner.set_content(title, body, actions, f"{code}: {detail}".strip(": "))
            self.error_banner.show()
            return
        elif key == "corrupt":
            title = QCoreApplication.translate(
                "NewProjectScreen",
                "Файл %1 не читается — возможно, он повреждён или скопирован не до конца.",
            ).replace("%1", self.vm.corrupt_name or "")
            body = QCoreApplication.translate(
                "NewProjectScreen", "Остальные файлы проанализируем без него."
            )
            actions = [
                ("continue", QCoreApplication.translate("NewProjectScreen", "Продолжить без него")),
                ("replace", QCoreApplication.translate("NewProjectScreen", "Заменить файл")),
            ]
        else:
            self.error_banner.hide()
            return
        self.error_banner.set_content(title, body, actions)
        self.error_banner.show()

    def _update_info_banner(self) -> None:
        if not self.vm.warning_4k_active:
            self.info_banner.hide()
            return
        lang = "ky" if QCoreApplication.translate("NewProjectScreen", "ru") == "ky" else "ru"
        eta = format_eta(self.vm.eta_4k_s, lang).split("~")[-1]
        text = QCoreApplication.translate(
            "NewProjectScreen",
            "Видео 4K анализируется долго — около %1 на этом компьютере. "
            "Можно уйти: мы пришлём уведомление.",
        ).replace("%1", eta)
        self.info_banner.set_content(text, "", [])
        self.info_banner.show()

    # ---- actions
    def _on_banner_action(self, key: str) -> None:
        if key == "recheck":
            self.vm.recheck()
        elif key == "choose_folder":
            self._choose_folder()
        elif key == "no_copy":
            self.vm.copy = False
        elif key == "retry":
            self.vm.retry_import()
        elif key == "continue":
            self.vm.exclude_bad()
        elif key == "replace":
            bad = self.vm.first_bad_id()
            if bad is not None:
                self._replace(bad, MediaKind.VIDEO)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            QCoreApplication.translate("NewProjectScreen", "Выбрать папку"),
            str(self.vm.projects_dir),
        )
        if folder:
            self.vm.set_projects_dir(Path(folder))

    def _replace(self, media_id: int, kind: MediaKind) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self,
            QCoreApplication.translate("NewProjectScreen", "Заменить файл"),
            "",
            _filter(kind),
        )
        if name:
            self.vm.replace_file(media_id, Path(name))

    # ---- i18n
    def retranslate(self) -> None:
        self._title.setText(QCoreApplication.translate("NewProjectScreen", "Новый проект"))
        self._name_label.setText(QCoreApplication.translate("NewProjectScreen", "Название"))
        self.name_edit.setPlaceholderText(
            QCoreApplication.translate("NewProjectScreen", "Свадьба Айбек и Нурайым")
        )
        self._date_label.setText(QCoreApplication.translate("NewProjectScreen", "Дата"))
        self._kind_label.setText(QCoreApplication.translate("NewProjectScreen", "Тип"))
        for kind, chip in self.chips.items():
            chip.setText(QCoreApplication.translate("ToiKind", _KIND_TEXT[kind]))
        self.copy_check.setText(
            QCoreApplication.translate("NewProjectScreen", "Копировать файлы в папку проекта")
        )
        self.removable_label.setText(
            QCoreApplication.translate("NewProjectScreen", "Файлы на флешке — лучше скопировать")
        )
        self.start_button.setText(QCoreApplication.translate("NewProjectScreen", "Начать анализ"))
        self._update_disk_label()
        self._update_banner()
        self._update_info_banner()

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)
