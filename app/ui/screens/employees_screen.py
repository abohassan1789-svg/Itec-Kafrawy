"""شاشة «الموظفين» — النموذج 1 «Base Screen: فورم يمين + قائمة شمال» (اختيار المستخدم 2026-11-11).

```
┌ [موظف جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]          (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ البيانات الأساسية ┐                 │ ┌ البحث والتسجيل ┐                       │
│   الكود EMP-001 (لوحده) · الاسم* · EN  │   🔍 الاسم / الكود / الوظيفة / القسم …     │
│   الوظيفة · القسم ▾ (نص حر) · الحالة   │   [الكل][النشط][الموقوف]  الدور ▾           │
│ ┌ الاتصال والهوية ┐                     │   الكود│الموظف│الوظيفة│القسم│الأدوار│…    │
│   الموبايل · … · الرقم القومي · التعيين │   › السابق · صفحة 1 من 1 · التالي ‹         │
│ ┌ الأدوار ┐                             │   عدد الموظفين: … · نشط … · مندوبين …       │
│   ☐ مندوب ☐ أمين خزينة ☐ أمين مخزن ☐ عهدة │                                          │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* الموظف «طرف» مش شئون عاملين: بيتستخدم في العهد والمندوب وأمين الخزينة وأمين المخزن.
* **القسم نص حر**: خانة بتكتب فيها أو تختار من الأقسام اللي اتكتبت قبل كده — والقسم الجديد بيظهر لوحده في
  القايمة بعد الحفظ.
* الرقم القومي وتاريخ التعيين اختياريين · مفيش حد عهدة ولا ربط بمستخدم البرنامج (ردود المستخدم 2026-11-11).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.repositories.employee_repository import ROLE_COLUMNS
from app.services.employee_service import PAGE_SIZE, ROLE_LABELS, EmployeeService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED = "#087443", "#64748B"
ALL_ROLES = "كل الأدوار"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشط"), ("inactive", "الموقوف"))
ROLES_HINT = ("الأدوار بتحدد الموظف بيظهر فين: «مندوب» في فاتورة البيع · «أمين خزينة» في الخزائن · "
              "«أمين مخزن» في المخازن · «عهدة» في صرف العهد (1107 عهد الموظفين ببُعد الموظف).")
DEPT_HINT = "اكتب قسم جديد أو اختار من الأقسام اللي اتكتبت قبل كده"
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


class EmployeesScreen(BaseScreen):
    MODULE = "hr"
    TARGET = "employees"
    TITLE = "الموظفين"
    ICON = "🪪"
    FORM_TITLE = "البيانات الأساسية"
    NEW_TEXT = "موظف جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الوظيفة أو القسم أو الموبايل… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "الموظف"), ("job_title", "الوظيفة"), ("department", "القسم"),
               ("roles", "الأدوار"), ("mobile", "الموبايل"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, employees: EmployeeService | None = None, parent: QWidget | None = None) -> None:
        self.employees = employees or EmployeeService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
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

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setLayoutDirection(Qt.LeftToRight)
        self.code_edit.setPlaceholderText("بيتولّد لوحده")
        self.code_edit.setToolTip("كود الموظف بيتولّد لوحده وقت الحفظ (EMP-001، EMP-002…) وثابت بعد كده")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(200)
        self.name_ar.setPlaceholderText("اسم الموظف")
        self.name_en = self._ltr(QLineEdit(), 200)
        self.job_title = QLineEdit()
        self.job_title.setMaxLength(120)
        self.job_title.setPlaceholderText("مثلاً: مهندس مبيعات، أمين مخزن، محاسب")
        self.department = QComboBox()
        self.department.setEditable(True)
        self.department.setInsertPolicy(QComboBox.NoInsert)
        self.department.lineEdit().setMaxLength(80)
        self.department.lineEdit().setPlaceholderText(DEPT_HINT)
        self.department.setToolTip(DEPT_HINT)
        self._reload_departments()
        self.is_active = QCheckBox("نشط")
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("الوظيفة", self.job_title)
        form.addRow("القسم", self.department)
        form.addRow("الحالة", self.is_active)

    def _build_contact(self) -> QGroupBox:
        box = QGroupBox("الاتصال والهوية")
        form = self._form(box)
        self.mobile = self._ltr(QLineEdit(), 30)
        self.phone = self._ltr(QLineEdit(), 30)
        self.email = self._ltr(QLineEdit(), 120, "name@company.com")
        self.address = QLineEdit()
        self.address.setMaxLength(300)
        self.national_id = self._ltr(QLineEdit(), 17, "اختياري — 14 رقم")
        self.national_id.setToolTip("الرقم القومي: اختياري، 14 رقم، ومايتكررش لموظفين")
        self.has_hire = QCheckBox("محدد")
        self.hire_date = QDateEdit(QDate.currentDate())
        self.hire_date.setCalendarPopup(True)
        self.hire_date.setDisplayFormat("dd/MM/yyyy")
        self.hire_date.setMinimumDate(QDate(1950, 1, 1))
        self.hire_date.setLayoutDirection(Qt.LeftToRight)
        self.has_hire.toggled.connect(lambda on: self.hire_date.setEnabled(on and self.mode in ("new", "edit")))
        hire_row = QWidget()
        row = QHBoxLayout(hire_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.has_hire)
        row.addWidget(self.hire_date, 1)
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الموبايل", self.mobile)
        form.addRow("التليفون", self.phone)
        form.addRow("الإيميل", self.email)
        form.addRow("العنوان", self.address)
        form.addRow("الرقم القومي", self.national_id)
        form.addRow("تاريخ التعيين", hire_row)
        form.addRow("ملاحظات", self.notes)
        return box

    def _build_roles(self) -> QGroupBox:
        box = QGroupBox("الأدوار")
        layout = QVBoxLayout(box)
        layout.setContentsMargins(14, 18, 14, 10)
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        self.role_checks: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(ROLE_LABELS.items()):
            check = QCheckBox(label)
            self.role_checks[key] = check
            grid.addWidget(check, i // 2, i % 2)
        layout.addLayout(grid)
        hint = QLabel(ROLES_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        layout.addWidget(hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        contact = self._build_contact()
        align_form_right(contact)
        right.addWidget(contact)
        right.addWidget(self._build_roles())
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
        row.addWidget(QLabel("الدور"))
        self.role_filter = QComboBox()
        self.role_filter.addItem(ALL_ROLES, None)
        for key, label in ROLE_LABELS.items():
            self.role_filter.addItem(label, key)
        self.role_filter.currentIndexChanged.connect(self._on_filter)
        row.addWidget(self.role_filter)
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
        return [self.name_ar, self.name_en, self.job_title, self.department, self.is_active, self.mobile, self.phone,
                self.email, self.address, self.national_id, self.has_hire, self.notes, *self.role_checks.values()]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        self.hire_date.setEnabled(editing and self.has_hire.isChecked())
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.role_filter.setEnabled(not editing)
        self._update_paging(editing)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            defaults = self.employees.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        self.name_ar.setFocus()

    # ---- الأقسام (نص حر + قايمة باللي اتكتب قبل كده) ------------------------
    def _reload_departments(self) -> None:
        current = self.department.currentText()
        try:
            names = self.employees.department_choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            names = []
        self.department.blockSignals(True)
        self.department.clear()
        self.department.addItem("")
        self.department.addItems(names)
        self.department.setEditText(current)
        self.department.blockSignals(False)

    # ---- القائمة والصفحات ----------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return keyword, self.status, self.role_filter.currentData()

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
        self._counts = self.employees.count_employees(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.employees.list_employees(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.employees.lookup_employees()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "roles":
            return EmployeeService.roles_text(row) or "—"
        if key in ("job_title", "department"):
            return value or ""
        if key == "mobile":
            return f"⁦{value}⁩" if value else ""
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col = cols.index("is_active")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(ACTIVE_COLOR if row.get("is_active") else MUTED))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"عدد الموظفين: {counts['total']} · نشط {counts['active']} · "
                                     f"موقوف {counts['inactive']} · مندوبين {counts['sales_reps']}")
        if hasattr(self, "prev_button"):
            self._update_paging(self.mode in ("new", "edit"))
        if hasattr(self, "department") and self.mode not in ("new", "edit"):
            self._reload_departments()                             # القسم الجديد يظهر في القايمة بعد الحفظ

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} الموظف «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.employees.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل الموظف." if new_state else
                          "تم إيقاف الموظف (مش هيتختار في أي مستند جديد).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفى من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفه.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.employees.get_employee(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key in ("name_ar", "name_en", "job_title", "mobile", "phone", "email", "address", "national_id", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self.department.setEditText(record.get("department") or "")
        self.is_active.setChecked(bool(record.get("is_active", True)))
        hire = record.get("hire_date")
        self.has_hire.setChecked(hire is not None)
        self.hire_date.setDate(_qdate(hire) if hire else QDate.currentDate())
        for key, check in self.role_checks.items():
            check.setChecked(bool(record.get(ROLE_COLUMNS[key])))

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.job_title, self.mobile, self.phone,
                     self.email, self.address, self.national_id, self.notes):
            edit.clear()
        self.department.setEditText("")
        self.is_active.setChecked(True)
        self.has_hire.setChecked(False)
        self.hire_date.setDate(QDate.currentDate())
        for check in self.role_checks.values():
            check.setChecked(False)

    def collect(self) -> dict:
        data = {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(), "job_title": self.job_title.text(),
                "department": self.department.currentText(), "mobile": self.mobile.text(),
                "phone": self.phone.text(), "email": self.email.text(), "address": self.address.text(),
                "national_id": self.national_id.text(), "notes": self.notes.text(),
                "hire_date": _pydate(self.hire_date.date()) if self.has_hire.isChecked() else None,
                "is_active": self.is_active.isChecked()}
        for key, check in self.role_checks.items():
            data[ROLE_COLUMNS[key]] = check.isChecked()
        return data

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.employees.save_employee(None if record_id is None else int(record_id), payload,
                                            expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.employees.delete_employee(int(record_id), expected_version=self.current_version)
