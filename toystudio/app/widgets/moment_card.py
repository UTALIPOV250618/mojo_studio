"""Moment card of the results feed: preview, neutral type badge, time, reason, frame quality."""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QEvent, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from toystudio.app.theme.apply import set_variant
from toystudio.app.widgets.elided import ElidedLabel
from toystudio.core.format import format_timecode
from toystudio.core.models import Mark, MomentType, QualityLabel

PREVIEW_W = 128  # 16:9 like the project card
PREVIEW_H = PREVIEW_W * 9 // 16
_EXCLUDED_OPACITY = 0.55

_TYPE_NAMES: dict[MomentType, str] = {
    MomentType.TOAST: QT_TRANSLATE_NOOP("MomentCard", "Тост"),
    MomentType.DANCE: QT_TRANSLATE_NOOP("MomentCard", "Танец"),
    MomentType.ENTRANCE: QT_TRANSLATE_NOOP("MomentCard", "Выход молодых"),
    MomentType.GIFTS: QT_TRANSLATE_NOOP("MomentCard", "Подарки"),
    MomentType.APPLAUSE: QT_TRANSLATE_NOOP("MomentCard", "Аплодисменты"),
    MomentType.MUSIC: QT_TRANSLATE_NOOP("MomentCard", "Музыка"),
}
_QUALITY_NAMES: dict[QualityLabel, str] = {
    QualityLabel.CLEAR: QT_TRANSLATE_NOOP("MomentCard", "чётко"),
    QualityLabel.DARK: QT_TRANSLATE_NOOP("MomentCard", "темновато"),
    QualityLabel.SHAKY: QT_TRANSLATE_NOOP("MomentCard", "трясётся"),
    QualityLabel.BLURRY: QT_TRANSLATE_NOOP("MomentCard", "размыто"),
}
_MARKS = {Mark.NONE: "", Mark.MUST: "★", Mark.EXCLUDE: "⊖"}


class MomentCard(QFrame):
    """Display only: marks are shown here, switching them lives on screen 4 (phase 3)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("MomentCard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._type = MomentType.TOAST
        self._quality = QualityLabel.CLEAR
        self._pixmap: QPixmap | None = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(12)
        self._preview = QLabel()
        self._preview.setObjectName("MomentPreview")
        self._preview.setFixedSize(PREVIEW_W, PREVIEW_H)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._preview)
        body = QVBoxLayout()
        body.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(8)
        self._badge = QLabel()
        self._badge.setObjectName("MomentTypeBadge")
        self._badge.setProperty("role", "label")
        self._mark = QLabel()
        self._mark.setObjectName("MomentMark")
        self._mark.setProperty("role", "label")
        head.addWidget(self._badge)
        head.addStretch(1)
        head.addWidget(self._mark)
        self._time = QLabel()
        self._time.setProperty("role", "timecode")
        self._reason = ElidedLabel()
        self._reason.setProperty("role", "caption")
        self._quality_label = QLabel()
        self._quality_label.setProperty("role", "caption")
        self._quality_label.setProperty("tone", "muted")
        body.addLayout(head)
        body.addWidget(self._time)
        body.addWidget(self._reason)
        body.addWidget(self._quality_label)
        body.addStretch(1)
        lay.addLayout(body, 1)
        self.setFixedHeight(PREVIEW_H + 16)
        self.set_data(MomentType.TOAST, 0, 0, QualityLabel.CLEAR, None, [], Mark.NONE)

    def set_data(
        self,
        type: MomentType,
        start_us: int,
        duration_us: int,
        quality: QualityLabel,
        preview: QPixmap | None,
        reasons: list[str],
        mark: Mark,
    ) -> None:
        self._type, self._quality = type, quality
        self._time.setText(f"{format_timecode(start_us)} · {format_timecode(duration_us)}")
        self._reason.setText(reasons[0] if reasons else "")
        self._reason.setVisible(bool(reasons))
        self._pixmap = preview if preview is not None and not preview.isNull() else None
        self._show_preview()
        self._mark.setText(_MARKS[mark])
        self._mark.setVisible(mark is not Mark.NONE)
        set_variant(self, "mark", mark.value)
        set_variant(self._mark, "mark", mark.value)
        if mark is Mark.EXCLUDE:
            effect = QGraphicsOpacityEffect(self)
            effect.setOpacity(_EXCLUDED_OPACITY)
            self.setGraphicsEffect(effect)
        else:
            self.setGraphicsEffect(None)  # type: ignore[arg-type]
        self.retranslate()

    def _show_preview(self) -> None:
        if self._pixmap is None:  # neutral placeholder: the dark preview background
            self._preview.clear()
            return
        self._preview.setPixmap(
            self._pixmap.scaled(
                PREVIEW_W,
                PREVIEW_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def retranslate(self) -> None:
        self._badge.setText(QCoreApplication.translate("MomentCard", _TYPE_NAMES[self._type]))
        self._quality_label.setText(
            QCoreApplication.translate("MomentCard", _QUALITY_NAMES[self._quality])
        )

    def changeEvent(self, e: QEvent) -> None:
        if e.type() == QEvent.Type.LanguageChange:
            self.retranslate()
        super().changeEvent(e)

    # ---- queries (tests)
    def type_text(self) -> str:
        return self._badge.text()

    def time_text(self) -> str:
        return self._time.text()

    def reason_text(self) -> str:
        return self._reason.full_text()

    def quality_text(self) -> str:
        return self._quality_label.text()

    def mark_text(self) -> str:
        return self._mark.text()

    def has_preview(self) -> bool:
        return self._pixmap is not None

    def badge_is_neutral(self) -> bool:
        """The type never gets a colour of its own: no per-type styling hook exists."""
        return self._badge.objectName() == "MomentTypeBadge" and not self._badge.property("type")
