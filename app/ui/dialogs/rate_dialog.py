"""نافذة «💲 تسجيل سعر صرف» (شاشة العملات — النموذج 10).

* العملة (المختارة) · التاريخ (النهارده) · السعر (بيبدأ بآخر سعر).
* بتوري آخر سعر قبل التاريخ ونسبة التغيير وانت بتكتب، وبتسأل تتأكد لو التغيير أكبر من 5% (غلط كتابة؟).
* لو فيه سعر لنفس اليوم بتقول، والحفظ بيسأل «تستبدله؟».
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.services.currency_service import BIG_CHANGE_PERCENT, CurrencyService, change_percent, fmt_rate
from app.services.errors import ConcurrentEditError
from app.ui.common import messages
from app.ui.common.base_screen import RIGHT
from app.ui.common.theme import GREEN_DARK

HINT_STYLE = ("background:#EFF6FF; border:1px solid #BFDBFE; color:#1D4ED8; border-radius:6px; padding:6px 8px; "
              "font-weight:700;")
WARN_STYLE = ("background:#FFF7ED; border:1px solid #FED7AA; color:#9A3412; border-radius:6px; padding:6px 8px; "
              "font-weight:800;")


class RateDialog(QDialog):
    def __init__(self, service: CurrencyService, currency: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.currency = currency
        self.saved_rate_id: int | None = None
        self._context: dict = {"previous": None, "same_day": None}
        self.setWindowTitle("💲 تسجيل سعر صرف")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(470)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        header = QLabel("💲 تسجيل سعر صرف")
        header.setStyleSheet(f"background:{GREEN_DARK}; color:#FFFFFF; font-size:16px; font-weight:900; "
                             "border-radius:6px; padding:8px 12px;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(RIGHT)
        self.currency_label = QLabel(f"{currency['code']} — {currency['name_ar']}")
        self.currency_label.setStyleSheet("font-weight:900;")
        self.rate_date = QDateEdit(QDate.currentDate())
        self.rate_date.setCalendarPopup(True)
        self.rate_date.setLayoutDirection(Qt.LeftToRight)       # قبل الـ format عشان مايتقلبش
        self.rate_date.setDisplayFormat("dd/MM/yyyy")
        self.rate = QDoubleSpinBox()
        self.rate.setDecimals(6)
        self.rate.setRange(0, 999_999_999)
        self.rate.setGroupSeparatorShown(True)
        self.rate.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.rate.setLayoutDirection(Qt.LeftToRight)
        self.rate.setAlignment(RIGHT)
        form.addRow("العملة", self.currency_label)
        form.addRow("التاريخ *", self.rate_date)
        form.addRow(f"سعر ⁦1 {currency['code']}⁩ بالجنيه *", self.rate)   # معزول LTR عشان مايتقلبش
        layout.addLayout(form)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(HINT_STYLE)
        layout.addWidget(self.hint)
        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(WARN_STYLE)
        layout.addWidget(self.warning)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("حفظ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.rate_date.dateChanged.connect(self._reload_context)
        self.rate.valueChanged.connect(self._update_hint)
        self._reload_context()
        if currency.get("last_rate"):
            self.rate.setValue(float(currency["last_rate"]))
        self.rate.setFocus()
        self.rate.selectAll()

    # ---- المعلومات وانت بتكتب ----------------------------------------------
    def _date(self) -> date:
        return self.rate_date.date().toPython()

    def _reload_context(self) -> None:
        try:
            self._context = self.service.rate_context(int(self.currency["id"]), self._date())
        except Exception:  # noqa: BLE001 — المعلومة مش لازم توقف التسجيل
            self._context = {"previous": None, "same_day": None}
        self._update_hint()

    def percent(self) -> Decimal | None:
        previous = self._context.get("previous")
        value = Decimal(str(self.rate.value()))
        if previous is None or value <= 0:
            return None
        return change_percent(value, previous["rate"])

    def _update_hint(self) -> None:
        previous, same_day = self._context.get("previous"), self._context.get("same_day")
        value = Decimal(str(self.rate.value()))
        if previous is None:
            text = "مفيش سعر قبل التاريخ ده للعملة دي — ده هيبقى أول سعر."
        else:
            text = f"آخر سعر قبله: ⁦{fmt_rate(previous['rate'])}⁩ بتاريخ ⁦{previous['rate_date']:%d/%m/%Y}⁩"
            pct = self.percent()
            if pct is not None:
                diff = value - Decimal(previous["rate"])
                arrow = "▲" if diff > 0 else "▼" if diff < 0 else "="
                text += f" ← التغيير {arrow} ⁦{fmt_rate(abs(diff))}⁩ (⁦{abs(pct)}%⁩)"
        if same_day is not None:
            text += f"\nفيه سعر متسجل في اليوم ده: ⁦{fmt_rate(same_day['rate'])}⁩ — الحفظ هيستبدله."
        self.hint.setText(text)
        pct = self.percent()
        big = pct is not None and abs(pct) > BIG_CHANGE_PERCENT
        self.warning.setText(f"⚠ التغيير كبير (⁦{abs(pct)}%⁩) — اتأكد إن السعر مكتوب صح." if big else "")
        self.warning.setVisible(big)

    # ---- الحفظ -------------------------------------------------------------
    def save(self) -> None:
        if self.rate.value() <= 0:
            messages.warn(self, "تسجيل سعر", "السعر لازم يبقى أكبر من صفر.")
            return
        pct = self.percent()
        if pct is not None and abs(pct) > BIG_CHANGE_PERCENT and not messages.ask_yes_no(
                self, "تأكيد السعر", f"السعر اتغيّر {abs(pct)}% عن آخر سعر. متأكد إنه صح؟"):
            return
        same_day = self._context.get("same_day")
        if same_day is not None and not messages.ask_yes_no(
                self, "استبدال السعر",
                f"فيه سعر متسجل يوم {self._date():%d/%m/%Y} ({fmt_rate(same_day['rate'])}). تستبدله؟"):
            return
        try:
            # إصدار سعر اليوم اللي شوفناه (0 = مكانش فيه) — لو جهاز تاني سجّل في الوقت ده ← تعارض.
            self.saved_rate_id = self.service.save_rate(
                int(self.currency["id"]), self._date(), Decimal(str(self.rate.value())),
                expected_version=int(same_day["row_version"]) if same_day is not None else 0)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تسجيل السعر", exc)
            if isinstance(exc, ConcurrentEditError):
                self._reload_context()                  # يبان السعر اللي اتسجل من الجهاز التاني
            return
        self.accept()
