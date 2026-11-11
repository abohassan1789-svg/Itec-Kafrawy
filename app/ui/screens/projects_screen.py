"""شاشة «أوامر التوريد» — النموذج 1 «Base Screen: فورم يمين + قائمة شمال» (اختيار المستخدم 2026-11-10).

```
┌ [أمر جديد][تعديل][حفظ][حذف][إلغاء][تحديث][تصدير إكسل][خروج]                          (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات الأمر ┐                      │ ┌ البحث والتسجيل ┐                         │
│   رقم الأمر (Itec-001 مقترح ويتعدّل)  │   🔍 رقم الأمر أو المشروع أو العميل (F1)     │
│   اسم المشروع* · العميل (اختياري)      │   [الكل][جاري][مقفول][ملغي]                 │
│   مركز التكلفة* (فرعي) · الحالة        │   المركز ▾ (الأب بيجيب الأبناء) · السنة ▾     │
│ ┌ القيمة والفاتورة ┐                   │   الرقم│المشروع│العميل│الحالة│القيمة│المركز   │
│   القيمة + العملة · شامل · فاتورة/بيان │   › السابق · صفحة 1 من 3 · التالي ‹          │
│ ┌ التواريخ والملاحظات ┐                │   عدد الأوامر: … · جاري … · مقفول … · ملغي …   │
│   تاريخ الأمر · البداية ☐ · النهاية ☐   │                                            │
│ ┌ مربوط بيه ┐                          │                                            │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* «أمر جديد» بيملى الرقم المقترح (بيتصرف وقت الحفظ جوه قفل — لو جهاز تاني أخده الأمر بياخد اللي بعده)،
  ولو المستخدم غيّره بيتحفظ زي ما كتبه (ومايتكررش).
* اختيار العميل في «جديد» بيحط عملته · شامل/غير شامل · فاتورة/بيان · العملة = افتراضي للفاتورة ويتغيّر فيها.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError
from app.services.project_service import DOC_KINDS, PAGE_SIZE, STATUS_NAMES, ProjectService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

STATUS_COLORS = {"open": "#087443", "closed": "#64748B", "cancelled": "#B91C1C"}
MUTED = "#64748B"
NO_CUSTOMER = "— من غير عميل —"
ALL_CENTERS = "كل المراكز"
ALL_YEARS = "كل السنين"
STATUS_CHIPS = (("all", "الكل"), ("open", "جاري"), ("closed", "مقفول"), ("cancelled", "ملغي"))
DEFAULTS_HINT = "شامل / غير شامل · فاتورة / بيان · العملة: افتراضي للفاتورة، وتقدر تغيّرهم فيها."
CODE_HINT = "بيطلع لوحده — وتقدر تكتب رقم تاني (مايتكررش)"
LINKED_TEXT = ("🧾 فواتير البيع على الأمر: مع المبيعات (المرحلة 8)\n"
               "💸 المصروفات المحمّلة: مع المصروفات (المرحلة 9)\n"
               "📒 القيود والربحية: مع القيود (المرحلة 4 و 9)\n"
               "الأمر اللي عليه حركة مايتمسحش — يتقفل بس.")
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _money(value: Any) -> str:
    return f"{Decimal(value or 0):,.2f}"


class ProjectsScreen(BaseScreen):
    MODULE = "sales"
    TARGET = "projects"
    TITLE = "أوامر التوريد"
    ICON = "📋"
    FORM_TITLE = "بيانات الأمر"
    NEW_TEXT = "أمر جديد"
    SEARCH_PLACEHOLDER = "🔍 رقم الأمر أو المشروع أو العميل… (F1)"
    COLUMNS = [("code", "رقم الأمر"), ("name", "المشروع"), ("customer_name", "العميل"), ("status", "الحالة"),
               ("amount", "القيمة"), ("center_code", "المركز")]
    EXIT_LAST = True

    def __init__(self, projects: ProjectService | None = None, parent: QWidget | None = None) -> None:
        self.projects = projects or ProjectService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._suggested = ""
        self._customers: dict[int, dict] = {}
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
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        return label

    @staticmethod
    def _date_edit() -> QDateEdit:
        edit = QDateEdit(QDate.currentDate())
        edit.setCalendarPopup(True)
        edit.setDisplayFormat("dd/MM/yyyy")
        edit.setMinimumDate(QDate(2000, 1, 1))
        edit.setLayoutDirection(Qt.LeftToRight)
        return edit

    def _load_choices(self) -> tuple[list[dict], list[dict], list[dict]]:
        try:
            return (self.projects.customer_choices(), self.projects.center_choices(),
                    self.projects.currency_choices())
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            return [], [], []

    def build_form(self, box: QGroupBox) -> None:
        customers, self._centers, self._currencies = self._load_choices()
        self._customers = {int(c["id"]): c for c in customers}
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(30)
        self.code_edit.setLayoutDirection(Qt.LeftToRight)
        self.code_edit.setToolTip(CODE_HINT)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(200)
        self.name_edit.setPlaceholderText("اسم المشروع")
        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.setInsertPolicy(QComboBox.NoInsert)
        self.customer.addItem(NO_CUSTOMER, None)
        for c in customers:
            self.customer.addItem(f"{c['code']} — {c['name_ar']}", int(c["id"]))
        completer = self.customer.completer()
        completer.setFilterMode(Qt.MatchContains)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        self.customer.setToolTip("اختياري — اكتب جزء من الاسم أو الكود")
        self.customer.currentIndexChanged.connect(self._on_customer_changed)
        self.center = QComboBox()
        self.center.addItem("— اختار المركز —", None)
        for c in self._centers:
            if c["is_leaf"] and c["is_active"]:
                self.center.addItem(f"{c['code']} — {c['name_ar']}", int(c["id"]))
        self.center.setToolTip("مركز تكلفة فرعي نشط (من شاشة «مراكز التكلفة»)")
        self.status_combo = QComboBox()
        for key, text in STATUS_NAMES.items():
            self.status_combo.addItem(text, key)
        self.status_combo.setToolTip("المقفول والملغي مش هيتختاروا في فاتورة أو مصروف جديد")
        form.addRow("رقم الأمر", self.code_edit)
        form.addRow("", self._hint(CODE_HINT))
        form.addRow("اسم المشروع *", self.name_edit)
        form.addRow("العميل", self.customer)
        form.addRow("مركز التكلفة *", self.center)
        form.addRow("الحالة", self.status_combo)

    def _build_value(self) -> QGroupBox:
        box = QGroupBox("القيمة والفاتورة")
        form = self._form(box)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 9_999_999_999_999.99)
        self.amount.setDecimals(2)
        self.amount.setGroupSeparatorShown(True)
        self.amount.setButtonSymbols(QDoubleSpinBox.NoButtons)
        self.amount.setLayoutDirection(Qt.LeftToRight)
        self.amount.setToolTip("قيمة الأمر بعملته — الصفر مسموح لحد ما تتحدد")
        self.currency = QComboBox()
        for cur in self._currencies:
            self.currency.addItem(f"{cur['code']} — {cur['name_ar']}", cur["code"])
        self.tax_combo = QComboBox()
        self.tax_combo.addItem("شامل الضريبة", True)
        self.tax_combo.addItem("غير شامل", False)
        self.kind_combo = QComboBox()
        for key, text in DOC_KINDS.items():
            self.kind_combo.addItem(text, key)
        form.addRow("القيمة", self.amount)
        form.addRow("العملة", self.currency)
        form.addRow("شامل / غير شامل", self.tax_combo)
        form.addRow("فاتورة / بيان", self.kind_combo)
        form.addRow("", self._hint(DEFAULTS_HINT))
        return box

    def _optional_date(self) -> tuple[QCheckBox, QDateEdit, QWidget]:
        check = QCheckBox("محدد")
        edit = self._date_edit()
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(check)
        row.addWidget(edit, 1)
        check.toggled.connect(lambda on: edit.setEnabled(on and self.mode in ("new", "edit")))
        return check, edit, holder

    def _build_dates(self) -> QGroupBox:
        box = QGroupBox("التواريخ والملاحظات")
        form = self._form(box)
        self.order_date = self._date_edit()
        self.has_start, self.start_date, start_row = self._optional_date()
        self.has_end, self.end_date, end_row = self._optional_date()
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("تاريخ الأمر *", self.order_date)
        form.addRow("تاريخ البداية", start_row)
        form.addRow("تاريخ النهاية", end_row)
        form.addRow("ملاحظات", self.notes)
        return box

    def _build_linked(self) -> QGroupBox:
        box = QGroupBox("مربوط بيه")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 10)
        self.linked_label = self._hint(LINKED_TEXT)
        layout.addWidget(self.linked_label)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_value(), self._build_dates(), self._build_linked()):
            align_form_right(box)
            right.addWidget(box)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        chips = QHBoxLayout()
        chips.setSpacing(6)
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
            chips.addWidget(chip)
        self.status_buttons.buttonClicked.connect(self._on_status)
        chips.addStretch(1)
        layout.addLayout(chips)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("المركز"))
        self.center_filter = QComboBox()
        self.center_filter.addItem(ALL_CENTERS, None)
        for c in self._centers:
            indent = "   " * (int(c["level"]) - 1)
            suffix = "" if c["is_leaf"] else " (رئيسي — بيجيب اللي تحته)"
            self.center_filter.addItem(f"{indent}{c['code']} — {c['name_ar']}{suffix}", int(c["id"]))
        self.center_filter.currentIndexChanged.connect(self._on_filter)
        row.addWidget(self.center_filter, 1)
        row.addWidget(QLabel("السنة"))
        self.year_filter = QComboBox()
        self.year_filter.addItem(ALL_YEARS, None)
        self._fill_years()
        self.year_filter.currentIndexChanged.connect(self._on_filter)
        row.addWidget(self.year_filter)
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

    def _fill_years(self) -> None:
        """السنين اللي فيها أوامر (بتتحدّث مع «تحديث» من غير ما الاختيار يضيع)."""
        try:
            years = self.projects.years()
        except Exception:  # noqa: BLE001
            return
        current = self.year_filter.currentData()
        have = [self.year_filter.itemData(i) for i in range(1, self.year_filter.count())]
        if have == years:
            return
        self.year_filter.blockSignals(True)
        while self.year_filter.count() > 1:
            self.year_filter.removeItem(1)
        for y in years:
            self.year_filter.addItem(str(y), y)
        index = self.year_filter.findData(current)
        self.year_filter.setCurrentIndex(max(0, index))
        self.year_filter.blockSignals(False)

    def editable_widgets(self) -> list[QWidget]:
        return [self.code_edit, self.name_edit, self.customer, self.center, self.status_combo, self.amount,
                self.currency, self.tax_combo, self.kind_combo, self.order_date, self.has_start, self.has_end,
                self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        self.start_date.setEnabled(editing and self.has_start.isChecked())
        self.end_date.setEnabled(editing and self.has_end.isChecked())
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.center_filter.setEnabled(not editing)
        self.year_filter.setEnabled(not editing)
        self._update_paging(editing)

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            defaults = self.projects.new_defaults()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تجهيز أمر جديد", exc)
            return
        self._suggested = defaults["code"] or ""
        self.code_edit.setText(self._suggested)
        self.order_date.setDate(_qdate(defaults["order_date"]))
        self._set_combo(self.status_combo, defaults["status"])
        self._set_combo(self.currency, defaults["currency_code"])
        self._set_combo(self.tax_combo, defaults["tax_inclusive"])
        self._set_combo(self.kind_combo, defaults["doc_kind"])
        self.name_edit.setFocus()

    # ---- العميل ← العملة (في «جديد» بس) ---------------------------------------
    def _on_customer_changed(self) -> None:
        customer = self._customers.get(self.customer.currentData() or -1)
        if self.mode == "new" and customer is not None and customer.get("currency_code"):
            self._set_combo(self.currency, customer["currency_code"])

    # ---- القائمة والصفحات ----------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return keyword, self.status, self.center_filter.currentData(), self.year_filter.currentData()

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
        self._counts = self.projects.count_projects(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.projects.list_projects(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.projects.lookup_projects()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "code":
            return f"⁦{value}⁩"
        if key == "customer_name":
            return value or "—"
        if key == "center_code":
            return f"{value} {row.get('center_name') or ''}"
        if key == "amount":
            return f"⁦{_money(value)} {row.get('currency_code') or ''}⁩"
        if key == "status":
            return STATUS_NAMES.get(value, value or "")
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        if hasattr(self, "year_filter"):
            self._fill_years()
        super().refresh_list()
        status_col = [k for k, _t in self.COLUMNS].index("status")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(STATUS_COLORS.get(row.get("status"), MUTED)))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        self.table.resizeColumnToContents(status_col)              # «مقفول» بالبولد مايتقصّش
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"عدد الأوامر: {counts['total']} · جاري {counts['open']} · "
                                     f"مقفول {counts['closed']} · ملغي {counts['cancelled']}")
        if hasattr(self, "prev_button"):
            self._update_paging(self.mode in ("new", "edit"))

    # ---- البيانات ----------------------------------------------------------
    @staticmethod
    def _set_combo(combo: QComboBox, value: Any, label: str = "") -> None:
        index = combo.findData(value)
        if index < 0 and value is not None:                   # قيمة مش في الاختيارات (اتوقفت بعد الربط)
            combo.addItem(label or str(value), value)
            index = combo.count() - 1
        combo.setCurrentIndex(max(0, index))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.projects.get_project(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._suggested = ""
        self.code_edit.setText(record.get("code") or "")
        self.name_edit.setText(record.get("name") or "")
        self.customer.blockSignals(True)
        if record.get("customer_id") is None:
            self.customer.setCurrentIndex(0)
        else:
            label = f"{record.get('customer_code')} — {record.get('customer_name')}"
            self._set_combo(self.customer, record["customer_id"],
                            label if record.get("customer_active") else f"{label} (موقوف)")
        self.customer.blockSignals(False)
        label = f"{record.get('center_code')} — {record.get('center_name')}"
        if not record.get("center_active"):
            label += " (موقوف)"
        elif not record.get("center_leaf"):
            label += " (بقى رئيسي)"
        self._set_combo(self.center, record.get("cost_center_id"), label)
        self._set_combo(self.status_combo, record.get("status"))
        self.amount.setValue(float(record.get("amount") or 0))
        self._set_combo(self.currency, record.get("currency_code"), record.get("currency_code") or "")
        self._set_combo(self.tax_combo, bool(record.get("tax_inclusive", True)))
        self._set_combo(self.kind_combo, record.get("doc_kind"))
        if record.get("order_date"):
            self.order_date.setDate(_qdate(record["order_date"]))
        for check, edit, key in ((self.has_start, self.start_date, "start_date"),
                                 (self.has_end, self.end_date, "end_date")):
            value = record.get(key)
            check.setChecked(value is not None)
            edit.setDate(_qdate(value) if value else QDate.currentDate())
        self.notes.setText(record.get("notes") or "")

    def clear_form(self) -> None:
        self._suggested = ""
        for edit in (self.code_edit, self.name_edit, self.notes):
            edit.clear()
        self.customer.blockSignals(True)
        self.customer.setCurrentIndex(0)
        self.customer.blockSignals(False)
        self.center.setCurrentIndex(0)
        self.status_combo.setCurrentIndex(0)
        self.amount.setValue(0)
        self.currency.setCurrentIndex(0)
        self.tax_combo.setCurrentIndex(0)
        self.kind_combo.setCurrentIndex(0)
        self.order_date.setDate(QDate.currentDate())
        for check, edit in ((self.has_start, self.start_date), (self.has_end, self.end_date)):
            check.setChecked(False)
            edit.setDate(QDate.currentDate())

    def _customer_id(self) -> int | None:
        """العميل من الخانة (اللي بتتكتب فيها): لازم يبقى من القايمة أو فاضي."""
        text = self.customer.currentText().strip()
        if not text or text == NO_CUSTOMER:
            return None
        index = self.customer.findText(text, Qt.MatchExactly)
        if index < 0:
            raise BusinessError(f"العميل «{text}» مش في القايمة — اختاره من القايمة أو سيب الخانة فاضية.")
        return self.customer.itemData(index)

    def collect(self) -> dict:
        code = self.code_edit.text().strip()
        return {"code": code, "auto_code": self.mode == "new" and (not code or code == self._suggested),
                "name": self.name_edit.text(), "customer_id": self._customer_id(),
                "cost_center_id": self.center.currentData(), "status": self.status_combo.currentData(),
                "amount": Decimal(str(round(self.amount.value(), 2))),
                "currency_code": self.currency.currentData(), "tax_inclusive": self.tax_combo.currentData(),
                "doc_kind": self.kind_combo.currentData(), "order_date": _pydate(self.order_date.date()),
                "start_date": _pydate(self.start_date.date()) if self.has_start.isChecked() else None,
                "end_date": _pydate(self.end_date.date()) if self.has_end.isChecked() else None,
                "notes": self.notes.text()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        saved = self.projects.save_project(None if record_id is None else int(record_id), payload,
                                           expected_version=self.current_version)
        if record_id is None and payload.get("auto_code") and self._suggested:
            code = (self.projects.get_project(saved) or {}).get("code")
            if code and code != self._suggested:                  # جهاز تاني أخد الرقم المقترح
                messages.info(self, "رقم الأمر", f"الرقم {self._suggested} اتاخد من جهاز تاني — "
                                                  f"الأمر اتحفظ برقم {code}.")
        return saved

    def delete_record(self, record_id: Any) -> None:
        self.projects.delete_project(int(record_id), expected_version=self.current_version)
