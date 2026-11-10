# منقول من Hr GAD (app/ui/common/no_wheel.py) زي ما هو.
"""السكرول بالماوس مابيغيّرش أى حاجة (طلب العميل 2026-10-23): لو المؤشر — أو الفوكس — على قايمة منسدلة أو تاريخ أو
رقم، عجلة الماوس مابتغيّرش قيمتها؛ بتروح للى وراها (الصفحة / الجدول) فتتحرك هى.

فلتر واحد على البرنامج كله (QApplication) — كل الشاشات والنوافذ، الموجودة والجديدة، من غير ما تتعدل.
القايمة وهى مفتوحة (الـ popup) بتتحرك عادى بالعجلة: ده مش الـ QComboBox نفسه.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QAbstractSpinBox, QApplication, QComboBox

# QAbstractSpinBox = QDateEdit / QTimeEdit / QSpinBox / QDoubleSpinBox
_NO_WHEEL = (QComboBox, QAbstractSpinBox)
_FILTER: QObject | None = None


class _NoWheelFilter(QObject):
    def eventFilter(self, obj, event) -> bool:  # noqa: N802 (Qt naming)
        if event.type() != QEvent.Wheel or not isinstance(obj, _NO_WHEEL):
            return False
        # زى event.ignore(): العجلة بتطلع للأب، ولو ماخدهاش للى فوقه، لحد اللى يتحرك بيها (الصفحة / الجدول).
        # Qt مابيطلّعش العجلة لفوق لوحده لو اتبعتت من الكود (sendEvent) — فاللفة هنا.
        widget = obj
        while not widget.isWindow() and widget.parentWidget() is not None:
            parent = widget.parentWidget()
            forwarded = QWheelEvent(
                obj.mapTo(parent, event.position()), event.globalPosition(), event.pixelDelta(),
                event.angleDelta(), event.buttons(), event.modifiers(), event.phase(), event.inverted())
            forwarded.ignore()
            QApplication.sendEvent(parent, forwarded)
            if forwarded.isAccepted():
                break
            widget = parent
        return True


def install(app: QApplication) -> None:
    """مرة واحدة على البرنامج (run_app)."""
    global _FILTER
    if _FILTER is None:
        _FILTER = _NoWheelFilter(app)
        app.installEventFilter(_FILTER)
