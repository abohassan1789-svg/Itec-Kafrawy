"""شاشة «أكواد الضريبة» — النموذج 1 «زي Hr GAD: الفورم يمين + القائمة شمال» (اختيار المستخدم 2026-11-08).

```
┌ [ضريبة جديدة][تعديل][حفظ][حذف][إلغاء][تحديث][تصدير إكسل][خروج]                    (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات الضريبة ┐ الكود* الاسم*      │ ┌ البحث والتسجيل ┐                       │
│   النوع* (+ إضافة | − خصم) النسبة %   │   الكود│الضريبة│النوع│النسبة│الحالة     │
│   بتتحسب على · الترتيب · بتتطبق على   │   عدد الضرايب: 6 · إضافة 3 · خصم 3 · …   │
│   الحالة ☑ نشطة ☐ معفي                │                                          │
│ ┌ الحسابات ┐ المبيعات ▾ · المشتريات ▾ │                                          │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* النوع: **+ إضافة** بتزوّد إجمالي الفاتورة (أخضر) · **خصم** بتقلّله (أحمر).
* «معفي» بيقفل النسبة (0) والنوع (إضافة) و«بتتحسب على» (الصافي).
* الحسابات (سكريبت 0010): حساب المبيعات / المشتريات من الدليل (فرعي نشط في الميزانية). «ضريبة جديدة» وتغيير
  النوع بيحطوا حسابات الدور لوحدهم (ض.ق.م مخرجات / مدخلات · خصم المنبع لينا / علينا)، والناحية اللي الضريبة
  مابتتطبقش عليها حسابها بيتقفل ويفضى.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QColor, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.tax_code_service import APPLIES, BASES, KIND_ADD, KIND_DEDUCT, SIDES, TaxCodeService, fmt_rate
from app.ui.common.base_screen import BaseScreen
from app.ui.common.data_table import default_format

ADD_COLOR, DEDUCT_COLOR, MUTED = "#087443", "#B91C1C", "#64748B"
# RLM (‏) قبل العلامة عشان + و − يفضلوا يمين الكلمة (من غيره Qt بيرميهم شمال).
ADD_TEXT, DEDUCT_TEXT = "‏+ إضافة", "‏− خصم"
NO_ACCOUNT = "— من غير حساب"
ACCOUNT_HINT = "فاضي = بياخد حساب الدور لوحده (ض.ق.م · خصم المنبع)"


def kind_text(row: dict) -> str:
    if row.get("is_exempt"):
        return "معفي"
    return ADD_TEXT if row.get("kind") == KIND_ADD else DEDUCT_TEXT


def kind_color(row: dict) -> str:
    if row.get("is_exempt"):
        return MUTED
    return ADD_COLOR if row.get("kind") == KIND_ADD else DEDUCT_COLOR


class TaxCodesScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "tax_codes"
    TITLE = "أكواد الضريبة"
    ICON = "🧾"
    FORM_TITLE = "بيانات الضريبة"
    NEW_TEXT = "ضريبة جديدة"
    SEARCH_PLACEHOLDER = "🔍 بحث بالكود / الاسم…"
    COLUMNS = [("code", "الكود"), ("name_ar", "الضريبة"), ("kind", "النوع"), ("rate", "النسبة"),
               ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, taxes: TaxCodeService | None = None, parent: QWidget | None = None) -> None:
        self.taxes = taxes or TaxCodeService()
        self._suggested: dict[str, int | None] | None = None   # حسابات الدور للنوع الحالي (في جديد/تعديل)
        super().__init__(parent)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(14, 20, 14, 12)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(10)
        self.code_edit.setPlaceholderText("إنجليزي — مثال: VAT14")
        self.code_edit.setValidator(QRegularExpressionValidator(QRegularExpression("[A-Za-z][A-Za-z0-9_]{0,9}"),
                                                                self.code_edit))
        self.code_edit.textEdited.connect(lambda text: self.code_edit.setText(text.upper()))
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(60)
        self.name_ar.setPlaceholderText("مثال: قيمة مضافة 14%")
        self.kind_add = QRadioButton(f"{ADD_TEXT} (بتزوّد الإجمالي)")
        self.kind_deduct = QRadioButton(f"{DEDUCT_TEXT} (بتقلّل الإجمالي)")
        self.kind_add.setStyleSheet(f"QRadioButton {{ color:{ADD_COLOR}; font-weight:800; }}")
        self.kind_deduct.setStyleSheet(f"QRadioButton {{ color:{DEDUCT_COLOR}; font-weight:800; }}")
        self.kind_group = QButtonGroup(self)
        self.kind_group.addButton(self.kind_add)
        self.kind_group.addButton(self.kind_deduct)
        self.kind_add.setChecked(True)
        kinds = QHBoxLayout()
        kinds.setSpacing(18)
        kinds.addWidget(self.kind_add)
        kinds.addWidget(self.kind_deduct)
        kinds.addStretch(1)
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 100)
        self.rate.setDecimals(2)
        self.rate.setSuffix(" %")
        self.rate.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.rate.setLayoutDirection(Qt.LeftToRight)
        self.calc_base = QComboBox()
        for key, text in BASES.items():
            self.calc_base.addItem(text, key)
        self.calc_order = QSpinBox()
        self.calc_order.setRange(1, 9)
        self.calc_order.setButtonSymbols(QSpinBox.NoButtons)
        self.calc_order.setToolTip("الضرايب بتتحسب بالترتيب ده (1 الأول)")
        self.applies_to = QComboBox()
        for key, text in APPLIES.items():
            self.applies_to.addItem(text, key)
        self.is_active = QCheckBox("نشطة")
        self.is_exempt = QCheckBox("معفي")
        self.is_exempt.setToolTip("معفي = من غير نسبة (بيظهر في إقرار القيمة المضافة لوحده — غير «الصفري»)")
        self.is_exempt.toggled.connect(self._exempt_changed)
        flags = QHBoxLayout()
        flags.setSpacing(18)
        flags.addWidget(self.is_active)
        flags.addWidget(self.is_exempt)
        flags.addStretch(1)
        form.addRow("الكود *", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("النوع *", kinds)
        form.addRow("النسبة", self.rate)
        form.addRow("بتتحسب على", self.calc_base)
        form.addRow("الترتيب", self.calc_order)
        form.addRow("بتتطبق على", self.applies_to)
        form.addRow("الحالة", flags)

    def _build_accounts(self) -> QGroupBox:
        box = QGroupBox("الحسابات")
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(14, 20, 14, 12)
        try:
            choices = self.taxes.account_choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            choices = []
        self.sales_account = QComboBox()
        self.purchase_account = QComboBox()
        for combo, tip in ((self.sales_account, "حساب الضريبة في فواتير البيع (مثلاً ض.ق.م مخرجات)"),
                           (self.purchase_account, "حساب الضريبة في فواتير الشراء (مثلاً ض.ق.م مدخلات)")):
            combo.addItem(NO_ACCOUNT, None)
            for acc in choices:
                combo.addItem(f"{acc['code']} — {acc['name_ar']}", int(acc["id"]))
            combo.setToolTip(tip)
        self.account_hint = QLabel(ACCOUNT_HINT)
        self.account_hint.setAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)
        self.account_hint.setToolTip("لو سبت الحساب فاضي والنسبة أكبر من صفر، البرنامج بياخد حساب الدور: "
                                     "ض.ق.م مخرجات / مدخلات للإضافة، وخصم المنبع لينا / علينا للخصم.")
        self.account_hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        form.addRow("حساب المبيعات", self.sales_account)
        form.addRow("حساب المشتريات", self.purchase_account)
        form.addRow("", self.account_hint)
        self.applies_to.currentIndexChanged.connect(lambda _i: self._applies_changed())
        self.kind_add.toggled.connect(self._kind_changed)
        return box

    def _account_combos(self) -> dict[str, QComboBox]:
        return {"sales": self.sales_account, "purch": self.purchase_account}

    @staticmethod
    def _set_account(combo: QComboBox, account_id: Any, label: str = "") -> None:
        index = combo.findData(None if account_id is None else int(account_id))
        if index < 0 and account_id is not None:                 # حساب مش في الاختيارات (اتقفل بعد الربط)
            combo.addItem(label or str(account_id), int(account_id))
            index = combo.count() - 1
        combo.setCurrentIndex(max(0, index))

    def _applies_changed(self) -> None:
        """الناحية اللي الضريبة مابتتطبقش عليها: حسابها بيتقفل ويفضى."""
        editing = self.mode != "view"
        applies = self.applies_to.currentData() or "both"
        for side, combo in self._account_combos().items():
            on = applies in ("both", side)
            if editing and not on:
                combo.setCurrentIndex(0)
            if editing and on and combo.currentData() is None and self._suggestions():
                self._set_account(combo, self._suggested.get(SIDES[side][0]))
            combo.setEnabled(editing and on)

    def _suggestions(self) -> dict[str, int | None] | None:
        """حسابات الدور للنوع الحالي (بتتجاب مرة واحدة في جديد / تعديل)."""
        if self._suggested is None:
            try:
                self._suggested = self.taxes.suggested_accounts(
                    KIND_DEDUCT if self.kind_deduct.isChecked() else KIND_ADD)
            except Exception:  # noqa: BLE001
                return None
        return self._suggested

    def _kind_changed(self, _checked: bool = False) -> None:
        """تغيير النوع (إضافة / خصم) بيحط حسابات الدور بتاعه — لو الحساب كان فاضي أو حساب النوع التاني."""
        if self.mode == "view":
            return
        old = self._suggested or {}
        try:
            self._suggested = self.taxes.suggested_accounts(KIND_DEDUCT if self.kind_deduct.isChecked() else KIND_ADD)
        except Exception:  # noqa: BLE001
            return
        for side, combo in self._account_combos().items():
            field = SIDES[side][0]
            if combo.isEnabled() and combo.currentData() in (None, old.get(field)):
                self._set_account(combo, self._suggested.get(field))
        self._applies_changed()

    def new_record(self) -> None:
        super().new_record()
        if self.mode == "new":
            self._suggested = None
            self._kind_changed()

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(form_box)
        accounts = self._build_accounts()
        from app.ui.common.base_screen import align_form_right

        align_form_right(accounts)
        right.addWidget(accounts)
        right.addStretch(1)
        content.addLayout(right, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.kind_add, self.kind_deduct, self.rate, self.calc_base, self.calc_order,
                self.applies_to, self.is_active, self.is_exempt, self.sales_account, self.purchase_account]

    def new_only_widgets(self) -> list[QWidget]:
        return [self.code_edit]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        self._exempt_changed(self.is_exempt.isChecked())
        self._applies_changed()

    def _exempt_changed(self, exempt: bool) -> None:
        """المعفي: النسبة 0 والنوع إضافة والأساس الصافي — والخانات دي بتتقفل."""
        if exempt and self.mode != "view":
            self.rate.setValue(0)
            self.kind_add.setChecked(True)
            self.calc_base.setCurrentIndex(self.calc_base.findData("net"))
        editing = self.mode != "view"
        for widget in (self.rate, self.kind_add, self.kind_deduct, self.calc_base):
            widget.setEnabled(editing and not exempt)

    # ---- القائمة -----------------------------------------------------------
    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "kind":
            return kind_text(row)
        if key == "rate":
            return "—" if row.get("is_exempt") else f"⁦{fmt_rate(value)}⁩"
        if key == "is_active":
            return "نشطة" if value else "موقوفة"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        keys = [k for k, _t in self.COLUMNS]
        kind_col, status_col = keys.index("kind"), keys.index("is_active")
        for r, row in enumerate(self.table.rows):
            for col, color in ((kind_col, kind_color(row)),
                               (status_col, ADD_COLOR if row.get("is_active") else MUTED)):
                item = self.table.item(r, col)
                if item is not None:
                    item.setForeground(QColor(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
        totals = self.taxes.summary(self.table.rows)
        self.count_label.setText(f"عدد الضرايب: {totals['total']} · إضافة {totals['add']} · "
                                 f"خصم {totals['deduct']} · موقوفة {totals['inactive']}")

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.taxes.list_codes(keyword)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.taxes.get_code(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        self.name_ar.setText(record.get("name_ar") or "")
        (self.kind_deduct if record.get("kind") == KIND_DEDUCT else self.kind_add).setChecked(True)
        self.rate.setValue(float(record.get("rate") or 0))
        self.calc_base.setCurrentIndex(max(0, self.calc_base.findData(record.get("calc_base") or "net")))
        self.calc_order.setValue(int(record.get("calc_order") or 1))
        self.applies_to.setCurrentIndex(max(0, self.applies_to.findData(record.get("applies_to") or "both")))
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.is_exempt.setChecked(bool(record.get("is_exempt")))
        self._suggested = None
        self._set_account(self.sales_account, record.get("sales_account_id"),
                          f"{record.get('sales_account_code')} — {record.get('sales_account_name')}")
        self._set_account(self.purchase_account, record.get("purchase_account_id"),
                          f"{record.get('purchase_account_code')} — {record.get('purchase_account_name')}")

    def clear_form(self) -> None:
        self.code_edit.clear()
        self.name_ar.clear()
        self.kind_add.setChecked(True)
        self.rate.setValue(0)
        self.calc_base.setCurrentIndex(0)
        self.calc_order.setValue(1)
        self.applies_to.setCurrentIndex(0)
        self.is_active.setChecked(True)
        self.is_exempt.setChecked(False)
        self.sales_account.setCurrentIndex(0)
        self.purchase_account.setCurrentIndex(0)

    def collect(self) -> dict:
        return {"code": self.code_edit.text(), "name_ar": self.name_ar.text(),
                "kind": KIND_DEDUCT if self.kind_deduct.isChecked() else KIND_ADD,
                "rate": Decimal(str(round(self.rate.value(), 2))), "calc_base": self.calc_base.currentData(),
                "calc_order": self.calc_order.value(), "applies_to": self.applies_to.currentData(),
                "is_active": self.is_active.isChecked(), "is_exempt": self.is_exempt.isChecked(),
                "sales_account_id": self.sales_account.currentData(),
                "purchase_account_id": self.purchase_account.currentData()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.taxes.save_code(None if record_id is None else int(record_id), payload, expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.taxes.delete_code(int(record_id), expected_version=self.current_version)
