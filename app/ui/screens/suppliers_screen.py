"""شاشة «الموردين» — النموذج 1 «Base Screen: فورم يمين + قائمة شمال» (اختيار المستخدم 2026-11-11).

```
┌ [مورد جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]          (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ البيانات الأساسية ┐                 │ ┌ البحث والتسجيل ┐                       │
│   الكود SUB-001 (لوحده) · الاسم* · EN  │   🔍 الاسم / الكود / الرقم الضريبي / الموبايل │
│   المجموعة* ▾ · حساب الرقابة (منها)    │   [الكل][النشط][الموقوف]  المجموعة ▾        │
│   الحالة ☑ نشط                         │   الكود│المورد│المجموعة│الرقم الضريبي│…      │
│ ┌ الاتصال والضرايب ┐                   │   › السابق · صفحة 1 من 3 · التالي ‹         │
│   الرقم الضريبي · ض.ق.م · خصم المنبع   │   عدد الموردين: … · نشط … · موقوف …         │
│   المسؤول · الموبايل · التليفون …       │                                          │
│ ┌ السداد والتحويل ┐                     │                                          │
│   مدة السداد · العملة · البنك · IBAN    │                                          │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* نفس شاشة «العملاء» من غير حد ائتمان، وبدله «البنك» و«رقم الحساب / IBAN» (اختياريين).
* «مورد جديد» بيملى الكود المقترح والمجموعة «موردين — عام» وافتراضيها؛ وتغيير المجموعة في «جديد» بينسخ
  افتراضيها (المدة، العملة، ض.ق.م، خصم المنبع). في «تعديل» تغيير المجموعة مابيغيّرش الأرقام دي.
* مورد عليه ض.ق.م ومن غير رقم تسجيل ← **تنبيه بس** تحت الرقم (مش منع).
* «مورد نقدي» (SUB-000) نظامي: «حذف» و«إيقاف / تفعيل» و«نشط» مقفولين عليه.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
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

from app.services.supplier_service import PAGE_SIZE, SupplierService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED, WARN = "#087443", "#64748B", "#B45309"
NO_TAX = "— من غير"
ALL_GROUPS = "كل المجموعات"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشط"), ("inactive", "الموقوف"))
OPENING_NOTE = "رصيد أول المدة بيتسجل في «مستند الأرصدة الافتتاحية» (مع القيود — المرحلة 4)."
TAX_WARNING = "⚠ مورد عليه ض.ق.م ومن غير رقم تسجيل — اتأكد إنه بيطلّع فاتورة ضريبية قبل خصم ض.ق.م المدخلات."
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


class SuppliersScreen(BaseScreen):
    MODULE = "purch"
    TARGET = "suppliers"
    TITLE = "الموردين"
    ICON = "🚚"
    FORM_TITLE = "البيانات الأساسية"
    NEW_TEXT = "مورد جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الرقم الضريبي أو الموبايل… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "المورد"), ("group_name", "المجموعة"), ("tax_reg_no", "الرقم الضريبي"),
               ("mobile", "الموبايل"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, suppliers: SupplierService | None = None, parent: QWidget | None = None) -> None:
        self.suppliers = suppliers or SupplierService()
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
            return (self.suppliers.group_choices(), self.suppliers.currency_choices(),
                    self.suppliers.tax_choices())
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            return [], [], {"vat": [], "wht": []}

    def build_form(self, box: QGroupBox) -> None:
        groups, self._currencies, self._taxes = self._load_choices()
        self._groups = {int(g["id"]): g for g in groups}
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setLayoutDirection(Qt.LeftToRight)
        self.code_edit.setPlaceholderText("بيتولّد لوحده")
        self.code_edit.setToolTip("كود المورد بيتولّد لوحده وقت الحفظ (SUB-001، SUB-002…) وثابت بعد كده")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(200)
        self.name_ar.setPlaceholderText("اسم الشركة أو المورد")
        self.name_en = self._ltr(QLineEdit(), 200)
        self.group = QComboBox()
        self.group.setToolTip("المجموعة بتحدد حساب الرقابة، وافتراضيها بيتنسخ للمورد الجديد")
        self.group.addItem("— اختار المجموعة —", None)
        for g in groups:
            if g["is_active"]:
                self.group.addItem(f"{g['code']} — {g['name_ar']}", int(g["id"]))
        self.group.currentIndexChanged.connect(self._on_group_changed)
        self.control_account = QLineEdit()
        self.control_account.setReadOnly(True)
        self.control_account.setToolTip("حساب الرقابة بييجي من المجموعة — المورد بُعد على سطر القيد (مش حساب لوحده)")
        self.is_active = QCheckBox("نشط")
        self.system_badge = QLabel("🔒 نظامي (للمشتريات الكاش)")
        self.system_badge.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        flags = QHBoxLayout()
        flags.setSpacing(18)
        flags.addWidget(self.is_active)
        flags.addWidget(self.system_badge)
        flags.addStretch(1)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("المجموعة *", self.group)
        form.addRow("حساب الرقابة", self.control_account)
        form.addRow("الحالة", flags)

    def _build_contact(self) -> QGroupBox:
        box = QGroupBox("الاتصال والضرايب")
        form = self._form(box)
        self.tax_reg_no = self._ltr(QLineEdit(), 30, "اختياري — أرقام وحروف")
        self.tax_reg_no.setToolTip("رقم التسجيل الضريبي: مايتكررش لمورّدين، ومن غير عدد أرقام معيّن")
        self.tax_warning = QLabel(TAX_WARNING)
        self.tax_warning.setWordWrap(True)
        self.tax_warning.setStyleSheet(f"color:{WARN}; font-weight:700;")
        self.tax_warning.setVisible(False)
        self.vat_tax = QComboBox()
        self.wht_tax = QComboBox()
        for combo, key in ((self.vat_tax, "vat"), (self.wht_tax, "wht")):
            combo.addItem(NO_TAX, None)
            for tax in self._taxes[key]:
                combo.addItem(f"{tax['code']} — {tax['name_ar']}", int(tax["id"]))
        self.vat_tax.setToolTip("ض.ق.م اللي بيتحط لوحده في فاتورة المورد (ض.ق.م مدخلات)")
        self.wht_tax.setToolTip("خصم المنبع اللي إحنا بنخصمه من المورد وبنوّرده للضرايب")
        self.tax_reg_no.textChanged.connect(self._update_tax_warning)
        self.vat_tax.currentIndexChanged.connect(self._update_tax_warning)
        self.contact_person = QLineEdit()
        self.contact_person.setMaxLength(120)
        self.mobile = self._ltr(QLineEdit(), 30)
        self.phone = self._ltr(QLineEdit(), 30)
        self.email = self._ltr(QLineEdit(), 120, "name@company.com")
        self.address = QLineEdit()
        self.address.setMaxLength(300)
        form.addRow("رقم التسجيل الضريبي", self.tax_reg_no)
        form.addRow("", self.tax_warning)
        form.addRow("ض.ق.م", self.vat_tax)
        form.addRow("خصم المنبع", self.wht_tax)
        form.addRow("المسؤول", self.contact_person)
        form.addRow("الموبايل", self.mobile)
        form.addRow("التليفون", self.phone)
        form.addRow("الإيميل", self.email)
        form.addRow("العنوان", self.address)
        return box

    def _build_payment(self) -> QGroupBox:
        box = QGroupBox("السداد والتحويل")
        form = self._form(box)
        self.terms = QSpinBox()
        self.terms.setRange(0, 365)
        self.terms.setSuffix(" يوم")
        self.terms.setButtonSymbols(QSpinBox.NoButtons)
        self.terms.setToolTip("الاستحقاق = تاريخ فاتورة المورد + المدة (0 = نقدي)")
        self.currency = QComboBox()
        for cur in self._currencies:
            self.currency.addItem(f"{cur['code']} — {cur['name_ar']}", cur["code"])
        self.bank_name = QLineEdit()
        self.bank_name.setMaxLength(120)
        self.bank_name.setPlaceholderText("اختياري — البنك اللي بنحوّل عليه")
        self.bank_account = self._ltr(QLineEdit(), 60, "اختياري — EG00 0000 …")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        hint = QLabel(OPENING_NOTE)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        form.addRow("مدة السداد", self.terms)
        form.addRow("العملة", self.currency)
        form.addRow("البنك", self.bank_name)
        form.addRow("رقم الحساب / IBAN", self.bank_account)
        form.addRow("ملاحظات", self.notes)
        form.addRow("", hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_contact(), self._build_payment()):
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
                self.currency, self.bank_name, self.bank_account, self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.group_filter.setEnabled(not editing)
        self._update_paging(editing)
        system = self._is_system()
        self.system_badge.setVisible(system)
        if system:                                   # المورد النقدي: مابيتمسحش ولا بيتوقف
            self.delete_button.setEnabled(False)
            self.is_active.setEnabled(False)
        self._update_tax_warning()

    def _is_system(self) -> bool:
        return bool(self.current_record and self.current_record.get("is_system")) and self.mode != "new"

    def _update_tax_warning(self) -> None:
        if not hasattr(self, "tax_warning"):
            return
        self.tax_warning.setVisible(SupplierService.needs_tax_warning(
            {"vat_tax_code_id": self.vat_tax.currentData(), "tax_reg_no": self.tax_reg_no.text(),
             "is_system": self._is_system()}))

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save") and not self._is_system())

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            defaults = self.suppliers.new_defaults()
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
        self._set_combo(self.currency, defaults["currency_code"])
        self._set_combo(self.vat_tax, defaults["vat_tax_code_id"])
        self._set_combo(self.wht_tax, defaults["wht_tax_code_id"])

    def _on_group_changed(self) -> None:
        self._show_account()
        group = self._groups.get(self.group.currentData() or -1)
        if self.mode == "new" and group is not None:              # «جديد» بس: افتراضي المجموعة بيتنسخ
            self._apply_group_defaults(self.suppliers.group_defaults(group))

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
        self._counts = self.suppliers.count_suppliers(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.suppliers.list_suppliers(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.suppliers.lookup_suppliers()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "group_name":
            return value or ""
        if key == "tax_reg_no":
            return f"⁦{value}⁩" if value else "—"
        if key == "mobile":
            return f"⁦{value}⁩" if value else ""
        if key == "is_active":
            return "نشط" if value else "موقوف"
        if key == "name_ar" and row.get("is_system"):
            return f"{value} 🔒"
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
            if SupplierService.needs_tax_warning(row) and (tax := self.table.item(r, tax_col)) is not None:
                tax.setForeground(QColor(WARN))
                tax.setToolTip(TAX_WARNING)
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"عدد الموردين: {counts['total']} · نشط {counts['active']} · "
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
                                   f"عايز {question} المورد «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.suppliers.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل المورد." if new_state else
                          "تم إيقاف المورد (مش هيتختار في أي مستند جديد).")
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
        return self.suppliers.get_supplier(int(record_id))

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
        for key in ("contact_person", "mobile", "phone", "email", "address", "bank_name", "bank_account", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self.terms.setValue(int(record.get("payment_terms_days") or 0))
        self._set_combo(self.currency, record.get("currency_code"), record.get("currency_code") or "")
        self._update_tax_warning()

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.control_account, self.tax_reg_no,
                     self.contact_person, self.mobile, self.phone, self.email, self.address, self.bank_name,
                     self.bank_account, self.notes):
            edit.clear()
        self.group.blockSignals(True)
        self.group.setCurrentIndex(0)
        self.group.blockSignals(False)
        self.is_active.setChecked(True)
        self.vat_tax.setCurrentIndex(0)
        self.wht_tax.setCurrentIndex(0)
        self.terms.setValue(30)
        self.currency.setCurrentIndex(0)

    def collect(self) -> dict:
        return {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(), "group_id": self.group.currentData(),
                "tax_reg_no": self.tax_reg_no.text(), "contact_person": self.contact_person.text(),
                "mobile": self.mobile.text(), "phone": self.phone.text(), "email": self.email.text(),
                "address": self.address.text(), "payment_terms_days": self.terms.value(),
                "currency_code": self.currency.currentData(), "vat_tax_code_id": self.vat_tax.currentData(),
                "wht_tax_code_id": self.wht_tax.currentData(), "bank_name": self.bank_name.text(),
                "bank_account": self.bank_account.text(), "notes": self.notes.text(),
                "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.suppliers.save_supplier(None if record_id is None else int(record_id), payload,
                                            expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.suppliers.delete_supplier(int(record_id), expected_version=self.current_version)
