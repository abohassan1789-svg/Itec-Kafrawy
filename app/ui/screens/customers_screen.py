"""شاشة «العملاء» — النموذج 1 «Base Screen: فورم يمين + قائمة شمال» (اختيار المستخدم 2026-11-10).

```
┌ [عميل جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]          (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ البيانات الأساسية ┐                 │ ┌ البحث والتسجيل ┐                       │
│   الكود (لوحده) · الاسم* · EN          │   🔍 الاسم / الكود / الرقم الضريبي / الموبايل │
│   المجموعة* ▾ · حساب الرقابة (منها)    │   [الكل][النشط][الموقوف]  المجموعة ▾        │
│   الحالة ☑ نشط                         │   الكود│العميل│المجموعة│الرقم الضريبي│…     │
│ ┌ الاتصال والضرايب ┐                   │   › السابق · صفحة 1 من 3 · التالي ‹         │
│   الرقم الضريبي · ض.ق.م · خصم المنبع   │   عدد العملاء: … · نشط … · موقوف …          │
│   المسؤول · الموبايل · التليفون …       │                                          │
│ ┌ الائتمان والرصيد ┐                    │                                          │
│   مدة السداد · حد الائتمان · العملة     │                                          │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* «عميل جديد» بيملى الكود المقترح والمجموعة «عملاء — عام» وافتراضيها؛ وتغيير المجموعة في «جديد» بينسخ
  افتراضيها (المدة، الحد، العملة، ض.ق.م، خصم المنبع). في «تعديل» تغيير المجموعة مابيغيّرش الأرقام دي.
* حساب الرقابة بييجي من المجموعة (مفيش حساب لكل عميل — القرار 2).
* حد الائتمان تنبيه بس مع الفاتورة · المندوب على الفاتورة بس (ردود المستخدم 2026-11-10).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.services.customer_service import PAGE_SIZE, CustomerService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED, WARN = "#087443", "#64748B", "#B45309"
NO_TAX = "— من غير"
ALL_GROUPS = "كل المجموعات"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشط"), ("inactive", "الموقوف"))
OPENING_NOTE = "رصيد أول المدة بيتسجل في «مستند الأرصدة الافتتاحية» (مع القيود — المرحلة 4)."
LIMIT_HINT = "حد الائتمان تنبيه بس: الفاتورة اللي تعدّيه بتطلّع تنبيه ومابتتمنعش · 0 = من غير حد."
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


class CustomersScreen(BaseScreen):
    MODULE = "sales"
    TARGET = "customers"
    TITLE = "العملاء"
    ICON = "👥"
    FORM_TITLE = "البيانات الأساسية"
    NEW_TEXT = "عميل جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الرقم الضريبي أو الموبايل… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "العميل"), ("group_name", "المجموعة"), ("tax_reg_no", "الرقم الضريبي"),
               ("mobile", "الموبايل"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, customers: CustomerService | None = None, parent: QWidget | None = None) -> None:
        self.customers = customers or CustomerService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._groups: dict[int, dict] = {}
        super().__init__(parent)
        self.count_label.parentWidget().layout().addLayout(self.paging)   # تحت عدد السجلات
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    @staticmethod
    def _form(box: QGroupBox) -> QFormLayout:
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        return form

    @staticmethod
    def _ltr(edit: QLineEdit, size: int, placeholder: str = "") -> QLineEdit:
        edit.setMaxLength(size)
        edit.setLayoutDirection(Qt.LeftToRight)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        return edit

    def _load_choices(self) -> tuple[list[dict], list[dict], dict[str, list[dict]]]:
        try:
            return (self.customers.group_choices(), self.customers.currency_choices(),
                    self.customers.tax_choices())
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            return [], [], {"vat": [], "wht": []}

    def build_form(self, box: QGroupBox) -> None:
        groups, self._currencies, self._taxes = self._load_choices()
        self._groups = {int(g["id"]): g for g in groups}
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setPlaceholderText("بيتولّد لوحده")
        self.code_edit.setToolTip("كود العميل بيتولّد لوحده وقت الحفظ (1، 2، 3…) وثابت بعد كده")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(200)
        self.name_ar.setPlaceholderText("اسم الشركة أو العميل")
        self.name_en = self._ltr(QLineEdit(), 200)
        self.group = QComboBox()
        self.group.setToolTip("المجموعة بتحدد حساب الرقابة، وافتراضيها بيتنسخ للعميل الجديد")
        self.group.addItem("— اختار المجموعة —", None)
        for g in groups:
            if g["is_active"]:
                self.group.addItem(f"{g['code']} — {g['name_ar']}", int(g["id"]))
        self.group.currentIndexChanged.connect(self._on_group_changed)
        self.control_account = QLineEdit()
        self.control_account.setReadOnly(True)
        self.control_account.setToolTip("حساب الرقابة بييجي من المجموعة — العميل بُعد على سطر القيد (مش حساب لوحده)")
        self.is_active = QCheckBox("نشط")
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("المجموعة *", self.group)
        form.addRow("حساب الرقابة", self.control_account)
        form.addRow("الحالة", self.is_active)

    def _build_contact(self) -> QGroupBox:
        box = QGroupBox("الاتصال والضرايب")
        form = self._form(box)
        self.tax_reg_no = self._ltr(QLineEdit(), 30, "اختياري — أرقام وحروف")
        self.tax_reg_no.setToolTip("رقم التسجيل الضريبي: مايتكررش لعميلين، ومن غير عدد أرقام معيّن")
        self.vat_tax = QComboBox()
        self.wht_tax = QComboBox()
        for combo, key in ((self.vat_tax, "vat"), (self.wht_tax, "wht")):
            combo.addItem(NO_TAX, None)
            for tax in self._taxes[key]:
                combo.addItem(f"{tax['code']} — {tax['name_ar']}", int(tax["id"]))
        self.vat_tax.setToolTip("ض.ق.م اللي بيتحط لوحده في فاتورة العميل")
        self.wht_tax.setToolTip("خصم المنبع اللي العميل بيخصمه (الجهات الحكومية مثلاً 1%)")
        self.contact_person = QLineEdit()
        self.contact_person.setMaxLength(120)
        self.mobile = self._ltr(QLineEdit(), 30)
        self.phone = self._ltr(QLineEdit(), 30)
        self.email = self._ltr(QLineEdit(), 120, "name@company.com")
        self.address = QLineEdit()
        self.address.setMaxLength(300)
        form.addRow("رقم التسجيل الضريبي", self.tax_reg_no)
        form.addRow("ض.ق.م", self.vat_tax)
        form.addRow("خصم المنبع", self.wht_tax)
        form.addRow("المسؤول", self.contact_person)
        form.addRow("الموبايل", self.mobile)
        form.addRow("التليفون", self.phone)
        form.addRow("الإيميل", self.email)
        form.addRow("العنوان", self.address)
        return box

    def _build_credit(self) -> QGroupBox:
        box = QGroupBox("الائتمان والرصيد")
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
        self.credit_limit.setToolTip(LIMIT_HINT)
        self.currency = QComboBox()
        for cur in self._currencies:
            self.currency.addItem(f"{cur['code']} — {cur['name_ar']}", cur["code"])
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        hint = QLabel(f"{LIMIT_HINT}\n{OPENING_NOTE}")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        form.addRow("مدة السداد", self.terms)
        form.addRow("حد الائتمان", self.credit_limit)
        form.addRow("العملة", self.currency)
        form.addRow("ملاحظات", self.notes)
        form.addRow("", hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_contact(), self._build_credit()):
            align_form_right(box)
            right.addWidget(box)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.toggle_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.toggle_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        self.status_buttons = QButtonGroup(self)
        self.status_buttons.setExclusive(True)
        for key, text in STATUS_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("status", key)
            self.status_buttons.addButton(chip)
            row.addWidget(chip)
        self.status_buttons.buttonClicked.connect(self._on_status)
        row.addStretch(1)
        row.addWidget(QLabel("المجموعة"))
        self.group_filter = QComboBox()
        self.group_filter.addItem(ALL_GROUPS, None)
        for g in self._groups.values():
            suffix = "" if g["is_active"] else " (موقوفة)"
            self.group_filter.addItem(f"{g['code']} — {g['name_ar']}{suffix}", int(g["id"]))
        self.group_filter.currentIndexChanged.connect(self._on_filter)
        row.addWidget(self.group_filter)
        layout.addLayout(row)
        # الصفحات (بتتحط تحت عدد السجلات بعد ما الـ Base يخلص)
        self.paging = QHBoxLayout()
        self.prev_button = QPushButton("› السابق")
        self.next_button = QPushButton("التالي ‹")
        for button in (self.prev_button, self.next_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(CHIP_STYLE)
        self.prev_button.clicked.connect(lambda: self._go_page(self.page - 1))
        self.next_button.clicked.connect(lambda: self._go_page(self.page + 1))
        self.page_label = QLabel("")
        self.page_label.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        self.paging.addStretch(1)
        self.paging.addWidget(self.prev_button)
        self.paging.addWidget(self.page_label)
        self.paging.addWidget(self.next_button)
        self.paging.addStretch(1)

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.group, self.is_active, self.tax_reg_no, self.vat_tax, self.wht_tax,
                self.contact_person, self.mobile, self.phone, self.email, self.address, self.terms,
                self.credit_limit, self.currency, self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.group_filter.setEnabled(not editing)
        self._update_paging(editing)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            defaults = self.customers.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        if defaults.get("group_id") is not None:
            self._set_combo(self.group, defaults["group_id"])
            self._apply_group_defaults(defaults)                   # لو المجموعة كانت مختارة أصلاً
        self.name_ar.setFocus()

    # ---- المجموعة ← الافتراضي ----------------------------------------------
    def _show_account(self) -> None:
        group = self._groups.get(self.group.currentData() or -1)
        self.control_account.setText(f"{group['account_code']} — {group['account_name']} (من المجموعة)"
                                     if group else "")

    def _apply_group_defaults(self, defaults: dict[str, Any]) -> None:
        self.terms.setValue(int(defaults["payment_terms_days"]))
        self.credit_limit.setValue(float(defaults["credit_limit"]))
        self._set_combo(self.currency, defaults["currency_code"])
        self._set_combo(self.vat_tax, defaults["vat_tax_code_id"])
        self._set_combo(self.wht_tax, defaults["wht_tax_code_id"])

    def _on_group_changed(self) -> None:
        self._show_account()
        group = self._groups.get(self.group.currentData() or -1)
        if self.mode == "new" and group is not None:              # «جديد» بس: افتراضي المجموعة بيتنسخ
            self._apply_group_defaults(self.customers.group_defaults(group))

    # ---- القائمة والصفحات ----------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return keyword, self.status, self.group_filter.currentData()

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self._on_filter()

    def _on_filter(self) -> None:
        self.page = 1
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()                                      # المعروض اتخفى بالفلتر

    def _go_page(self, page: int) -> None:
        self.page = max(1, min(self.pages, page))
        self.refresh_list()

    def _update_paging(self, editing: bool = False) -> None:
        self.prev_button.setEnabled(not editing and self.page > 1)
        self.next_button.setEnabled(not editing and self.page < self.pages)
        self.page_label.setText(f"صفحة {self.page} من {self.pages}")

    def fetch_rows(self, keyword: str) -> list[dict]:
        query = self._query(keyword)
        if query != self._last_query:                              # بحث / فلتر جديد ← أول صفحة
            self._last_query = query
            self.page = 1
        self._counts = self.customers.count_customers(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.customers.list_customers(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.customers.lookup_customers()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "group_name":
            return value or ""
        if key == "tax_reg_no":
            return f"⁦{value}⁩" if value else "—"
        if key == "mobile":
            return f"⁦{value}⁩" if value else ""
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col, tax_col = cols.index("is_active"), cols.index("tax_reg_no")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(ACTIVE_COLOR if row.get("is_active") else MUTED))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if not row.get("tax_reg_no") and (tax := self.table.item(r, tax_col)) is not None:
                tax.setForeground(QColor(WARN))
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"عدد العملاء: {counts['total']} · نشط {counts['active']} · "
                                     f"موقوف {counts['inactive']} · من غير رقم ضريبي {counts['no_tax']}")
        if hasattr(self, "prev_button"):
            self._update_paging(self.mode in ("new", "edit"))

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} العميل «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.customers.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل العميل." if new_state else
                          "تم إيقاف العميل (مش هيتختار في أي مستند جديد).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفى من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفه.")

    # ---- البيانات ----------------------------------------------------------
    @staticmethod
    def _set_combo(combo: QComboBox, value: Any, label: str = "") -> None:
        index = combo.findData(value)
        if index < 0 and value is not None:                   # قيمة مش في الاختيارات (اتوقفت بعد الربط)
            combo.addItem(label or str(value), value)
            index = combo.count() - 1
        combo.setCurrentIndex(max(0, index))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.customers.get_customer(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        self.name_ar.setText(record.get("name_ar") or "")
        self.name_en.setText(record.get("name_en") or "")
        self.group.blockSignals(True)
        label = f"{record.get('group_code')} — {record.get('group_name')}"
        self._set_combo(self.group, record.get("group_id"),
                        label if record.get("group_active") else f"{label} (موقوفة)")
        self.group.blockSignals(False)
        self.control_account.setText(f"{record.get('account_code')} — {record.get('account_name')} (من المجموعة)")
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.tax_reg_no.setText(record.get("tax_reg_no") or "")
        self._set_combo(self.vat_tax, record.get("vat_tax_code_id"),
                        f"{record.get('vat_code')} — {record.get('vat_name')}")
        self._set_combo(self.wht_tax, record.get("wht_tax_code_id"),
                        f"{record.get('wht_code')} — {record.get('wht_name')}")
        for key in ("contact_person", "mobile", "phone", "email", "address", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self.terms.setValue(int(record.get("payment_terms_days") or 0))
        self.credit_limit.setValue(float(record.get("credit_limit") or 0))
        self._set_combo(self.currency, record.get("currency_code"), record.get("currency_code") or "")

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.control_account, self.tax_reg_no,
                     self.contact_person, self.mobile, self.phone, self.email, self.address, self.notes):
            edit.clear()
        self.group.blockSignals(True)
        self.group.setCurrentIndex(0)
        self.group.blockSignals(False)
        self.is_active.setChecked(True)
        self.vat_tax.setCurrentIndex(0)
        self.wht_tax.setCurrentIndex(0)
        self.terms.setValue(30)
        self.credit_limit.setValue(0)
        self.currency.setCurrentIndex(0)

    def collect(self) -> dict:
        return {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(), "group_id": self.group.currentData(),
                "tax_reg_no": self.tax_reg_no.text(), "contact_person": self.contact_person.text(),
                "mobile": self.mobile.text(), "phone": self.phone.text(), "email": self.email.text(),
                "address": self.address.text(), "payment_terms_days": self.terms.value(),
                "credit_limit": Decimal(str(round(self.credit_limit.value(), 2))),
                "currency_code": self.currency.currentData(), "vat_tax_code_id": self.vat_tax.currentData(),
                "wht_tax_code_id": self.wht_tax.currentData(), "notes": self.notes.text(),
                "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.customers.save_customer(None if record_id is None else int(record_id), payload,
                                            expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.customers.delete_customer(int(record_id), expected_version=self.current_version)

