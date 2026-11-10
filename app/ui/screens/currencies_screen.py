"""شاشة «العملات وأسعار الصرف» — النموذج 10 «الفورم + محوّل بالتاريخ» (اختيار المستخدم 2026-11-08).

```
┌ [عملة جديدة][تعديل][حفظ][حذف][إلغاء][تحديث][💲 تسجيل سعر][تصدير إكسل][خروج]          (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات العملة ┐ الكود* الاسم* …      │ ┌ البحث والتسجيل ┐ الكود│العملة│آخر سعر│…  │
│ ┌ محوّل ┐ المبلغ [1,250] بتاريخ [📅]    │   عدد العملات: 5 · نشطة 4 · الأساسية: EGP │
│   ██ = 60,125.00 EGP ██               │   آخر أسعار USD: التاريخ│السعر│التغيير   │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* «💲 تسجيل سعر» بيفتح نافذة (بتوري آخر سعر والتغيير، وبتسأل لو التغيير أكبر من 5% أو اليوم متسجل).
* المحوّل بياخد **آخر سعر في التاريخ أو قبله** — نفس الطريقة اللي المستندات هتاخد بيها السعر.
* الأساسية (EGP): سعرها 1، مابتتوقفش ولا بتتمسح ومالهاش أسعار.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, QRegularExpression, Qt
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.currency_service import CurrencyService, fmt_amount, fmt_rate
from app.ui.common import messages
from app.ui.common.base_screen import RIGHT, BaseScreen
from app.ui.common.data_table import DataTable, default_format
from app.ui.common.theme import GREEN, GREEN_DARK, RED, TEXT_MUTED

STATUS_COLORS = {"base": "#92400E", "active": "#087443", "inactive": "#64748B"}
UP, DOWN = "#B91C1C", "#087443"          # السعر طلع = الجنيه نزل (أحمر) · نزل = أخضر


def status_kind(row: dict) -> str:
    if row.get("is_base"):
        return "base"
    return "active" if row.get("is_active") else "inactive"


STATUS_TEXT = {"base": "⭐ أساسية", "active": "نشطة", "inactive": "موقوفة"}


def change_text(rate: Any, previous: Any) -> str:
    if rate is None or previous is None:
        return "—"
    diff = Decimal(rate) - Decimal(previous)
    if diff > 0:
        return f"▲ ⁦+{fmt_rate(diff)}⁩"
    if diff < 0:
        return f"▼ ⁦-{fmt_rate(-diff)}⁩"
    return "= 0"


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


class CurrenciesScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "currencies"
    TITLE = "العملات وأسعار الصرف"
    ICON = "💱"
    FORM_TITLE = "بيانات العملة"
    NEW_TEXT = "عملة جديدة"
    SEARCH_PLACEHOLDER = "🔍 بحث بالكود / الاسم…"
    COLUMNS = [("code", "الكود"), ("name_ar", "العملة"), ("last_rate", "آخر سعر"), ("last_rate_date", "بتاريخ"),
               ("status", "الحالة")]
    EXIT_LAST = True
    RATES_LIMIT = 30

    def __init__(self, currencies: CurrencyService | None = None, parent: QWidget | None = None) -> None:
        self.currencies = currencies or CurrencyService()
        super().__init__(parent)
        if self.table.rows:                 # أول ما تفتح: أول عملة ليها أسعار (وإلا أول عملة غير الأساسية)
            rows = self.table.rows
            first = next((r for r in rows if not r["is_base"] and r["is_active"] and r["last_rate"] is not None),
                         next((r for r in rows if not r["is_base"] and r["is_active"]), rows[0]))
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(14, 20, 14, 12)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(3)
        self.code_edit.setPlaceholderText("3 حروف إنجليزي — مثال: USD")
        self.code_edit.setValidator(QRegularExpressionValidator(QRegularExpression("[A-Za-z]{0,3}"), self.code_edit))
        self.code_edit.textEdited.connect(lambda text: self.code_edit.setText(text.upper()))
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(60)
        self.name_ar.setPlaceholderText("مثال: دولار أمريكي")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(60)
        self.symbol = QLineEdit()
        self.symbol.setMaxLength(10)
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 6)
        self.decimals.setValue(2)
        self.decimals.setButtonSymbols(QSpinBox.NoButtons)
        self.fraction_name = QLineEdit()
        self.fraction_name.setMaxLength(30)
        self.fraction_name.setPlaceholderText("للتفقيط — مثال: سنت")
        self.is_active = QCheckBox("نشطة")
        self.is_base = QCheckBox("عملة أساسية 🔒")
        self.is_base.setEnabled(False)
        self.is_base.setToolTip("العملة الأساسية ثابتة (EGP)")
        flags = QHBoxLayout()
        flags.setSpacing(18)
        flags.addWidget(self.is_active)
        flags.addWidget(self.is_base)
        flags.addStretch(1)
        form.addRow("الكود *", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("بالإنجليزي", self.name_en)
        form.addRow("الرمز", self.symbol)
        form.addRow("الكسور", self.decimals)
        form.addRow("اسم الكسر", self.fraction_name)
        form.addRow("الحالة", flags)

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(form_box)
        right.addWidget(self._build_converter())
        right.addStretch(1)
        self._add_rates_section(list_box.layout())
        content.addLayout(right, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def _date_edit(self) -> QDateEdit:
        edit = QDateEdit(QDate.currentDate())
        edit.setCalendarPopup(True)
        edit.setLayoutDirection(Qt.LeftToRight)                   # قبل الـ format عشان مايتقلبش
        edit.setDisplayFormat("dd/MM/yyyy")
        return edit

    def _build_converter(self) -> QGroupBox:
        box = QGroupBox("محوّل")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 20, 14, 12)
        layout.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.convert_amount = QDoubleSpinBox()
        self.convert_amount.setRange(0, 999_999_999_999)
        self.convert_amount.setDecimals(2)
        self.convert_amount.setGroupSeparatorShown(True)
        self.convert_amount.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.convert_amount.setLayoutDirection(Qt.LeftToRight)
        self.convert_amount.setValue(1000)
        self.convert_date = self._date_edit()
        self.convert_code = QLabel("")
        self.convert_code.setStyleSheet("font-weight:900;")
        row.addWidget(QLabel("المبلغ"))
        row.addWidget(self.convert_amount, 2)
        row.addWidget(self.convert_code)
        row.addSpacing(10)
        row.addWidget(QLabel("بتاريخ"))
        row.addWidget(self.convert_date, 1)
        layout.addLayout(row)
        panel = QFrame()
        panel.setObjectName("convertPanel")
        panel.setStyleSheet(f"QFrame#convertPanel {{ background:qlineargradient(x1:0, y1:0, x2:1, y2:1, "
                            f"stop:0 {GREEN_DARK}, stop:1 {GREEN}); border-radius:8px; }}"
                            "QFrame#convertPanel QLabel { color:#FFFFFF; background:transparent; }")
        inner = QVBoxLayout(panel)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(2)
        caption = QLabel("= بالجنيه المصري")
        caption.setStyleSheet("font-weight:700;")
        self.convert_result = QLabel("")
        self.convert_result.setStyleSheet("font-size:24px; font-weight:900;")
        self.convert_note = QLabel("")
        self.convert_note.setWordWrap(True)
        self.convert_note.setStyleSheet("font-weight:700;")
        for label in (caption, self.convert_result, self.convert_note):
            label.setAlignment(RIGHT)
            inner.addWidget(label)
        layout.addWidget(panel)
        self.convert_amount.valueChanged.connect(self.update_converter)
        self.convert_date.dateChanged.connect(self.update_converter)
        return box

    def _add_rates_section(self, layout: QVBoxLayout) -> None:
        self.rates_title = QLabel("")
        self.rates_title.setStyleSheet(f"color:{GREEN}; font-weight:900; margin-top:6px;")
        layout.addWidget(self.rates_title)
        self.rates_table = DataTable([("rate_date", "التاريخ"), ("rate", "السعر بالجنيه"), ("change", "التغيير"),
                                      ("created_by_name", "سجّله")], formatter=self._rate_cell)
        self.rates_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)   # الأعمدة بالتساوي
        self.rates_table.itemSelectionChanged.connect(self.update_extra_buttons)
        layout.addWidget(self.rates_table, 1)
        bottom = QHBoxLayout()
        self.rates_note = QLabel("")
        self.rates_note.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        self.delete_rate_button = QPushButton("🗑 حذف السعر المحدد")
        self.delete_rate_button.setStyleSheet(
            f"QPushButton {{ background:#FFFFFF; color:{RED}; border:1px solid #FCA5A5; border-radius:6px; "
            "font-weight:800; padding:5px 10px; } QPushButton:hover { background:#FEF2F2; }"
            "QPushButton:disabled { color:#CBD5E1; border-color:#E2E8F0; }")
        self.delete_rate_button.clicked.connect(self.delete_selected_rate)
        self.delete_rate_button.setVisible(self.has_action("delete"))
        bottom.addWidget(self.rates_note, 1)
        bottom.addWidget(self.delete_rate_button)
        layout.addLayout(bottom)

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.rate_button = self._button("💲 تسجيل سعر", "rate", self.register_rate)
        layout.addWidget(self.rate_button)
        self.rate_button.setVisible(self.has_action("create"))

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.symbol, self.decimals, self.fraction_name, self.is_active]

    def new_only_widgets(self) -> list[QWidget]:
        return [self.code_edit]

    # ---- الأوضاع -----------------------------------------------------------
    def _record_is_base(self) -> bool:
        return bool(self.current_record and self.current_record.get("is_base"))

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        if self._record_is_base():
            self.delete_button.setEnabled(False)
            if mode == "edit":
                self.is_active.setEnabled(False)               # الأساسية مابتتوقفش
        viewing = mode == "view"
        self.convert_amount.setEnabled(viewing)
        self.convert_date.setEnabled(viewing)
        self.rates_table.setEnabled(viewing)

    def update_extra_buttons(self) -> None:
        record = self.current_record or {}
        idle = self.mode == "view" and self.current_id is not None
        self.rate_button.setEnabled(idle and not record.get("is_base") and bool(record.get("is_active"))
                                    and self.can("create"))
        if idle and record.get("is_base"):
            self.rate_button.setToolTip("العملة الأساسية سعرها دايماً 1")
        elif idle and not record.get("is_active"):
            self.rate_button.setToolTip("العملة موقوفة — فعّلها الأول")
        else:
            self.rate_button.setToolTip("")
        self.delete_rate_button.setEnabled(idle and self.rates_table.current_row_data() is not None
                                           and self.can("delete"))

    # ---- القائمة -----------------------------------------------------------
    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "last_rate":
            return "1.0000" if row.get("is_base") else fmt_rate(value)
        if key == "last_rate_date":
            return "—" if row.get("is_base") or value is None else value.strftime("%d/%m/%Y")
        if key == "status":
            return STATUS_TEXT[status_kind(row)]
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        col = [k for k, _t in self.COLUMNS].index("status")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, col)
            if item is not None:
                item.setForeground(QColor(STATUS_COLORS[status_kind(row)]))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        totals = self.currencies.summary(self.table.rows)
        self.count_label.setText(f"عدد العملات: {totals['total']} · نشطة {totals['active']} · "
                                 f"الأساسية: {totals['base']}")

    @staticmethod
    def _rate_cell(key: str, value: Any, row: dict) -> str:
        if key == "rate_date":
            return value.strftime("%d/%m/%Y") if value else ""
        if key == "rate":
            return fmt_rate(value)
        if key == "change":
            return change_text(row.get("rate"), row.get("prev_rate"))
        return default_format(key, value, row)

    def _show_rates(self, record: dict | None) -> None:
        if record is None or record.get("id") is None:
            self.rates_title.setText("آخر الأسعار")
            self.rates_table.set_rows([])
            self.rates_note.setText("")
            return
        self.rates_title.setText(f"آخر أسعار {record['code']} — {record['name_ar']}")
        if record.get("is_base"):
            self.rates_table.set_rows([])
            self.rates_note.setText("⭐ العملة الأساسية سعرها دايماً 1 — مالهاش أسعار صرف.")
            return
        rows = self.currencies.list_rates(int(record["id"]), self.RATES_LIMIT)
        self.rates_table.set_rows(rows)
        self.rates_table.scrollToTop()
        change_col = 2
        for r, row in enumerate(rows):
            item = self.rates_table.item(r, change_col)
            if item is not None and row.get("prev_rate") is not None:
                diff = Decimal(row["rate"]) - Decimal(row["prev_rate"])
                if diff:
                    item.setForeground(QColor(UP if diff > 0 else DOWN))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
        total = int(record.get("rates_count") or 0)
        if not rows:
            self.rates_note.setText("لسه مفيش أسعار — دوس «💲 تسجيل سعر».")
        elif total > len(rows):
            self.rates_note.setText(f"آخر {len(rows)} من {total} سعر")
        else:
            self.rates_note.setText(f"عدد الأسعار: {total}")

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.currencies.list_currencies(keyword)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.currencies.get_currency(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        self.name_ar.setText(record.get("name_ar") or "")
        self.name_en.setText(record.get("name_en") or "")
        self.symbol.setText(record.get("symbol") or "")
        self.decimals.setValue(int(record.get("decimals") if record.get("decimals") is not None else 2))
        self.fraction_name.setText(record.get("fraction_name") or "")
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.is_base.setChecked(bool(record.get("is_base")))
        self._show_rates(record)
        self.update_converter()

    def clear_form(self) -> None:
        for field in (self.code_edit, self.name_ar, self.name_en, self.symbol, self.fraction_name):
            field.clear()
        self.decimals.setValue(2)
        self.is_active.setChecked(True)
        self.is_base.setChecked(False)
        self._show_rates(None)
        self.update_converter()

    def collect(self) -> dict:
        return {"code": self.code_edit.text(), "name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                "symbol": self.symbol.text(), "decimals": self.decimals.value(),
                "fraction_name": self.fraction_name.text(), "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.currencies.save_currency(None if record_id is None else int(record_id), payload, expected_version=self.current_version)

    def delete(self) -> None:
        """زي الـ Base بس بيقول إن أسعار العملة هتتمسح معاها."""
        if self.current_id is None or not self.can("delete") or self._record_is_base():
            return
        record = self.current_record or {}
        question = f"هل تريد حذف العملة {record.get('code', '')}؟"
        if record.get("rates_count"):
            question += f"\nهيتمسح معاها {record['rates_count']} سعر صرف."
        if not messages.ask_yes_no(self, "تأكيد الحذف", question):
            return
        try:
            self.currencies.delete_currency(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر الحذف", exc)
            return
        self.show_empty()
        self.refresh_list()

    # ---- المحوّل -----------------------------------------------------------
    def update_converter(self) -> None:
        record = self.current_record if self.current_id is not None else None
        if record is None or self.mode == "new":
            self.convert_code.setText("")
            self.convert_result.setText("—")
            self.convert_note.setText("اختار عملة من القائمة.")
            return
        decimals = int(record.get("decimals") or 0)
        if self.convert_amount.decimals() != decimals:
            self.convert_amount.setDecimals(decimals)
        self.convert_code.setText(record["code"])
        on_date = self.convert_date.date().toPython()
        try:
            result = self.currencies.convert(int(record["id"]), Decimal(str(self.convert_amount.value())), on_date)
        except Exception as exc:  # noqa: BLE001
            self.convert_result.setText("—")
            self.convert_note.setText(messages.error_text(exc))
            return
        if result["is_base"]:
            self.convert_result.setText(f"⁦{fmt_amount(result['amount_base'])} EGP⁩")
            self.convert_note.setText("العملة الأساسية — السعر دايماً 1.")
        elif result["rate"] is None:
            self.convert_result.setText("—")
            self.convert_note.setText(f"مفيش سعر لـ {record['code']} في ⁦{on_date:%d/%m/%Y}⁩ أو قبله — "
                                      "سجّل سعر الأول.")
        else:
            self.convert_result.setText(f"⁦{fmt_amount(result['amount_base'])} EGP⁩")
            self.convert_note.setText(f"السعر المستخدم: ⁦{fmt_rate(result['rate'])}⁩ بتاريخ "
                                      f"⁦{result['rate_date']:%d/%m/%Y}⁩ (آخر سعر في ⁦{on_date:%d/%m/%Y}⁩ أو قبله)")

    # ---- أسعار الصرف -------------------------------------------------------
    def make_rate_dialog(self):
        from app.ui.dialogs.rate_dialog import RateDialog

        return RateDialog(self.currencies, self.current_record or {}, self)

    def register_rate(self) -> None:
        if not self.rate_button.isEnabled() or self.current_record is None:
            return
        dialog = self.make_rate_dialog()
        if dialog.exec() != QDialog.Accepted:
            return
        self.refresh_list()
        self.load(self.current_id)
        messages.info(self, "تسجيل سعر", "اتسجل السعر.")

    def delete_selected_rate(self) -> None:
        row = self.rates_table.current_row_data()
        if row is None or not self.can("delete") or self.mode != "view":
            return
        if not messages.ask_yes_no(self, "حذف سعر", f"هل تريد حذف سعر يوم {row['rate_date']:%d/%m/%Y} "
                                                   f"({fmt_rate(row['rate'])})؟"):
            return
        try:
            self.currencies.delete_rate(int(row["id"]), expected_version=row.get("row_version"))
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر حذف السعر", exc)
            return
        self.refresh_list()
        self.load(self.current_id)
