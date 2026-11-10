"""شاشة «مجموعات العملاء» — النموذج 1 «زي Hr GAD: الفورم يمين + القائمة شمال» (اختيار المستخدم 2026-11-10).

```
┌ [مجموعة جديدة][تعديل][حفظ][حذف][إلغاء][تحديث][تصدير إكسل][خروج]                    (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات المجموعة ┐ الكود (لوحده)     │ ┌ البحث والتسجيل ┐                       │
│   الاسم* · الاسم EN · حساب الرقابة* ▾  │   الكود│المجموعة│حساب الرقابة│المدة│الحد│الحالة│
│   الحالة ☑ نشطة  (نظامية)             │   عدد المجموعات: 6 · موقوفة 0 · …          │
│ ┌ الافتراضي للعميل الجديد ┐            │                                          │
│   مدة السداد · حد الائتمان · العملة    │                                          │
│   ض.ق.م ▾ · خصم المنبع ▾ · ملاحظات     │                                          │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* «حساب الرقابة»: فرعي نشط بدور «العملاء» (1103 — أو حساب فرعي منه لو 1103 اتفرّع في شجرة الحسابات).
* «مجموعة جديدة» بيملى الكود المقترح وحساب «العملاء» الافتراضي والعملة الأساسية وض.ق.م 14% و30 يوم.
* «عملاء — عام» نظامية: «حذف» بيتقفل عليها و«نشطة» مابتتشالش.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.customer_group_service import CustomerGroupService, limit_text
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format

ACTIVE_COLOR, MUTED = "#087443", "#64748B"
NO_TAX = "— من غير"
DEFAULTS_HINT = "الافتراضي ده بيتنسخ للعميل الجديد بس — العملاء الحاليين مابيتغيّروش."


class CustomerGroupsScreen(BaseScreen):
    MODULE = "sales"
    TARGET = "customer_groups"
    TITLE = "مجموعات العملاء"
    ICON = "🗂️"
    FORM_TITLE = "بيانات المجموعة"
    NEW_TEXT = "مجموعة جديدة"
    SEARCH_PLACEHOLDER = "🔍 بحث بالكود / الاسم / الحساب…"
    COLUMNS = [("code", "الكود"), ("name_ar", "المجموعة"), ("account_code", "حساب الرقابة"),
               ("payment_terms_days", "المدة"), ("credit_limit", "حد الائتمان"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, groups: CustomerGroupService | None = None, parent: QWidget | None = None) -> None:
        self.groups = groups or CustomerGroupService()
        super().__init__(parent)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    @staticmethod
    def _form(box: QGroupBox) -> QFormLayout:
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(14, 20, 14, 12)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        return form

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setPlaceholderText("بيتولّد لوحده")
        self.code_edit.setToolTip("كود المجموعة بيتولّد لوحده وقت الحفظ (1، 2، 3…) وثابت بعد كده")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("مثال: شركات مقاولات وتكييف")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setLayoutDirection(Qt.LeftToRight)
        self.control_account = QComboBox()
        self.control_account.setToolTip("الحساب اللي عملاء المجموعة بيتقيّدوا عليه — من حسابات «العملاء» في الدليل")
        try:
            accounts = self.groups.account_choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            accounts = []
        self.control_account.addItem("— اختار الحساب —", None)
        for acc in accounts:
            self.control_account.addItem(f"{acc['code']} — {acc['name_ar']}", int(acc["id"]))
        self.is_active = QCheckBox("نشطة")
        self.system_badge = QLabel("🔒 نظامية (الافتراضية لأي عميل جديد)")
        self.system_badge.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        flags = QHBoxLayout()
        flags.setSpacing(18)
        flags.addWidget(self.is_active)
        flags.addWidget(self.system_badge)
        flags.addStretch(1)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("حساب الرقابة *", self.control_account)
        form.addRow("الحالة", flags)

    def _build_defaults(self) -> QGroupBox:
        box = QGroupBox("الافتراضي للعميل الجديد")
        form = self._form(box)
        self.terms = QSpinBox()
        self.terms.setRange(0, 365)
        self.terms.setSuffix(" يوم")
        self.terms.setButtonSymbols(QSpinBox.NoButtons)
        self.terms.setToolTip("الاستحقاق = تاريخ الفاتورة + المدة (0 = نقدي)")
        self.credit_limit = QDoubleSpinBox()
        self.credit_limit.setRange(0, 9_999_999_999_999.99)
        self.credit_limit.setDecimals(2)
        self.credit_limit.setGroupSeparatorShown(True)
        self.credit_limit.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.credit_limit.setLayoutDirection(Qt.LeftToRight)
        self.credit_limit.setSpecialValueText("من غير حد")
        self.credit_limit.setToolTip("0 = من غير حد")
        self.currency = QComboBox()
        self.vat_tax = QComboBox()
        self.wht_tax = QComboBox()
        try:
            currencies = self.groups.currency_choices()
            taxes = self.groups.tax_choices()
        except Exception:  # noqa: BLE001
            currencies, taxes = [], {"vat": [], "wht": []}
        for cur in currencies:
            self.currency.addItem(f"{cur['code']} — {cur['name_ar']}", cur["code"])
        for combo, key in ((self.vat_tax, "vat"), (self.wht_tax, "wht")):
            combo.addItem(NO_TAX, None)
            for tax in taxes[key]:
                combo.addItem(f"{tax['code']} — {tax['name_ar']}", int(tax["id"]))
        self.vat_tax.setToolTip("ض.ق.م اللي بيتحط لوحده في فاتورة عميل المجموعة (14% · صفري للتصدير · معفي)")
        self.wht_tax.setToolTip("خصم المنبع اللي العميل بيخصمه (الجهات الحكومية مثلاً 1%)")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        hint = QLabel(DEFAULTS_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        form.addRow("مدة السداد", self.terms)
        form.addRow("حد الائتمان", self.credit_limit)
        form.addRow("العملة", self.currency)
        form.addRow("ض.ق.م", self.vat_tax)
        form.addRow("خصم المنبع", self.wht_tax)
        form.addRow("ملاحظات", self.notes)
        form.addRow("", hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(form_box)
        defaults = self._build_defaults()
        align_form_right(defaults)
        right.addWidget(defaults)
        right.addStretch(1)
        content.addLayout(right, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.control_account, self.is_active, self.terms, self.credit_limit,
                self.currency, self.vat_tax, self.wht_tax, self.notes]

    def _is_system(self) -> bool:
        return bool(self.current_record and self.current_record.get("is_system")) and self.mode != "new"

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        system = self._is_system()
        self.system_badge.setVisible(system)
        if system:                                   # النظامية: مابتتمسحش ولا بتتوقف
            self.delete_button.setEnabled(False)
            self.is_active.setEnabled(False)

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            defaults = self.groups.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self._set_combo(self.control_account, defaults["control_account_id"])
        self._set_combo(self.currency, defaults["currency_code"])
        self._set_combo(self.vat_tax, defaults["vat_tax_code_id"])
        self._set_combo(self.wht_tax, defaults["wht_tax_code_id"])
        self.terms.setValue(int(defaults["payment_terms_days"]))
        self.credit_limit.setValue(float(defaults["credit_limit"]))
        self.is_active.setChecked(True)
        self.name_ar.setFocus()

    # ---- القائمة -----------------------------------------------------------
    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "account_code":
            return f"{value} — {row.get('account_name') or ''}"
        if key == "payment_terms_days":
            return f"{value} يوم"
        if key == "credit_limit":
            return f"⁦{limit_text(value, row.get('currency_code') or '')}⁩"
        if key == "is_active":
            return "نشطة" if value else "موقوفة"
        if key == "name_ar" and row.get("is_system"):
            return f"{value} 🔒"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        status_col = [k for k, _t in self.COLUMNS].index("is_active")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(ACTIVE_COLOR if row.get("is_active") else MUTED))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        totals = self.groups.summary(self.table.rows)
        self.count_label.setText(f"عدد المجموعات: {totals['total']} · موقوفة {totals['inactive']} · "
                                 f"على {totals['accounts']} حساب رقابة")

    # ---- البيانات ----------------------------------------------------------
    @staticmethod
    def _set_combo(combo: QComboBox, value: Any, label: str = "") -> None:
        index = combo.findData(value)
        if index < 0 and value is not None:                   # قيمة مش في الاختيارات (اتوقفت بعد الربط)
            combo.addItem(label or str(value), value)
            index = combo.count() - 1
        combo.setCurrentIndex(max(0, index))

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.groups.list_groups(keyword)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.groups.get_group(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        self.name_ar.setText(record.get("name_ar") or "")
        self.name_en.setText(record.get("name_en") or "")
        self._set_combo(self.control_account, record.get("control_account_id"),
                        f"{record.get('account_code')} — {record.get('account_name')}")
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.terms.setValue(int(record.get("payment_terms_days") or 0))
        self.credit_limit.setValue(float(record.get("credit_limit") or 0))
        self._set_combo(self.currency, record.get("currency_code"), record.get("currency_code") or "")
        self._set_combo(self.vat_tax, record.get("vat_tax_code_id"),
                        f"{record.get('vat_code')} — {record.get('vat_name')}")
        self._set_combo(self.wht_tax, record.get("wht_tax_code_id"),
                        f"{record.get('wht_code')} — {record.get('wht_name')}")
        self.notes.setText(record.get("notes") or "")

    def clear_form(self) -> None:
        self.code_edit.clear()
        self.name_ar.clear()
        self.name_en.clear()
        self.control_account.setCurrentIndex(0)
        self.is_active.setChecked(True)
        self.terms.setValue(30)
        self.credit_limit.setValue(0)
        self.currency.setCurrentIndex(0)
        self.vat_tax.setCurrentIndex(0)
        self.wht_tax.setCurrentIndex(0)
        self.notes.clear()

    def collect(self) -> dict:
        return {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                "control_account_id": self.control_account.currentData(),
                "payment_terms_days": self.terms.value(),
                "credit_limit": Decimal(str(round(self.credit_limit.value(), 2))),
                "currency_code": self.currency.currentData(), "vat_tax_code_id": self.vat_tax.currentData(),
                "wht_tax_code_id": self.wht_tax.currentData(), "notes": self.notes.text(),
                "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.groups.save_group(None if record_id is None else int(record_id), payload,
                                      expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.groups.delete_group(int(record_id), expected_version=self.current_version)
