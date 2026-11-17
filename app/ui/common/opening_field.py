"""خانة «رصيد أول المدة» في شاشتي العملاء والموردين (قرار المستخدم 2026-11-17).

المبلغ + الجنب (مدين = عليه · دائن = ليه) — بتعدّل **نفس سطر** الطرف في مستند «الأرصدة الافتتاحية» (مايتكررش).
بعد ترحيل المستند الخانة بتتقفل وبتطلع ملاحظة. ``payload()`` بيرجّع المبلغ الجديد + اللي الشاشة شافته
(``old_*``) عشان لو جهاز تاني غيّر الرصيد في الوقت ده يطلع تعارض.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

MUTED, LOCKED = "#64748B", "#5B21B6"


def _money(value: Decimal) -> str:
    return f"{value:,.2f}" if value else ""


class OpeningBalanceField(QWidget):
    def __init__(self, default_side: str = "dr", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.default_side = default_side
        self._old = (Decimal("0"), Decimal("0"))
        self._posted = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.amount = QLineEdit()
        self.amount.setLayoutDirection(Qt.LeftToRight)
        self.amount.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        self.amount.setPlaceholderText("0.00")
        self.amount.setToolTip("رصيد الطرف يوم بداية الشغل على البرنامج (الفاضي = مالوش رصيد)")
        self.side = QComboBox()
        self.side.addItem("مدين (عليه)", "dr")
        self.side.addItem("دائن (ليه)", "cr")
        row.addWidget(self.amount, 2)
        row.addWidget(self.side, 1)
        col.addLayout(row)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        col.addWidget(self.note)
        self.clear()

    def clear(self) -> None:
        self._old = (Decimal("0"), Decimal("0"))
        self._posted = False
        self.amount.clear()
        self.side.setCurrentIndex(self.side.findData(self.default_side))
        self.note.setText("بيتسجل سطر في مستند «الأرصدة الافتتاحية» (نفس السطر لو اتعدّل من هناك).")
        self.note.setStyleSheet(f"color:{MUTED}; font-weight:600;")

    def load(self, record: dict[str, Any]) -> None:
        d = Decimal(record.get("opening_debit") or 0)
        c = Decimal(record.get("opening_credit") or 0)
        self._old = (d, c)
        self._posted = record.get("opening_status") == "posted"
        self.amount.setText(_money(d or c))
        self.side.setCurrentIndex(self.side.findData("cr" if c else ("dr" if d else self.default_side)))
        when = record.get("opening_date")
        date_text = f" بتاريخ ⁦{when:%d/%m/%Y}⁩" if when else ""
        if self._posted:
            self.note.setText(f"🔒 مستند الأرصدة الافتتاحية مرحّل{date_text} — الرصيد مايتعدّلش (الغي ترحيل المستند الأول).")
            self.note.setStyleSheet(f"color:{LOCKED}; font-weight:800;")
        else:
            self.note.setText(f"سطر في مستند «الأرصدة الافتتاحية»{date_text} (مسودة) — التعديل هنا = نفس السطر هناك.")
            self.note.setStyleSheet(f"color:{MUTED}; font-weight:600;")

    def set_editing(self, editing: bool) -> None:
        for widget in (self.amount, self.side):
            widget.setEnabled(editing and not self._posted)

    def payload(self) -> dict[str, Any]:
        text = self.amount.text().strip()
        dr = self.side.currentData() == "dr"
        return {"debit": text if dr else "", "credit": "" if dr else text,
                "old_debit": self._old[0], "old_credit": self._old[1]}
