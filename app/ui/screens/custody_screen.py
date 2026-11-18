"""شاشة «العهد» — النموذج 1 «الكلاسيك» + تعديلات المستخدم (2026-11-18). مكانها قسم «الموظفين».

```
┌ [جديد][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][تصدير إكسل][خروج]                (عرض) ┐
│ [💼 صرف عهدة] [🧾 تسوية عهدة]                                                                            │
├━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ (خط بلون النوع) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┤
│ ┌ صرف عهدة / تسوية عهدة ┐ الرقم · التاريخ · الحالة · الموظف     │ ┌ 👤 الموظف ┐ رصيده · العهد المفتوحة · ليه │
│  (صرف)   النوع · الصرف من · المبلغ · أمر التوريد               │   الرقم│التاريخ│المبلغ│الباقي│التسوية دي     │
│  (تسوية) [📂 اختار العهد…] [➕ بند مصروف] [↩ فلوس راجعة]       │ ┌ المستندات ┐ 🔍 · من · إلى · الحالة         │
│          جدول السطور (بيتضاف من واجهة منبثقة) · الإجماليات      │   الرقم│التاريخ│الموظف│المبلغ│البيان│الحالة │
│  البيان · رقم الإيصال                                          │                                          │
│ ┌ 📒 القيد اللي هيتعمل ┐ الحساب│مدين│دائن                      │   › السابق  صفحة 1 من 3  التالي ‹           │
└──────────────────────────────────────────────────────────────┴──────────────────────────────────────────┘
```
* **التسوية = طريقين** (رد المستخدم): «➕ بند مصروف» (نوع المصروف · البيان · أمر التوريد · المبلغ) و«↩ فلوس راجعة»
  (خزنة / بنك · المبلغ · البيان) — **كل واحد واجهة منبثقة** فيها «إضافة وسطر جديد».
* أول ما يختار الموظف في التسوية ← **واجهة منبثقة بالعهد المفتوحة بس** بتشيك بوكس (عهدة أو أكتر).
* الزيادة عن العهد المختارة = **«ليه»** (بتتسجل وتتحسب للموظف) — وتتصرف له بصرف نوعه «سداد اللي ليه».
* مسودة + ترحيل + إلغاء ترحيل · الرصيد مش مكفي ← تحذير بس · مفيش طباعة دلوقتي.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.custody_service import (
    PAGE_SIZE,
    PURPOSES,
    CustodyService,
    disb_label,
    settle_label,
)
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.journal_service import parse_money
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, STATUS_BADGES, ltr, money
from app.ui.screens.vouchers_screen import BAD, BLUE, DRAFT_COLOR, MUTED, OK_COLOR, WARN_STYLE, _pydate, _qdate

GREEN = "#137A38"
KIND_INFO = {"disb": ("💼 صرف عهدة", "#2563EB"), "settle": ("🧾 تسوية عهدة", GREEN)}
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
SOURCE_ITEMS = (("💵 خزنة", "treasury"), ("🏦 بنك", "bank"))
RIGHT = Qt.AlignRight | Qt.AlignVCenter
NUM = Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter
BUTTON_STYLE = ("QPushButton {{ background:{bg}; color:#FFFFFF; border:none; border-radius:6px; padding:6px 12px; "
                "font-weight:900; }} QPushButton:disabled {{ background:#CBD5E1; }}")
HEADER_STYLE = "background:{c}; color:#FFFFFF; font-size:14px; font-weight:900; border-radius:6px; padding:8px 12px;"


def _date_edit(value: dt.date) -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd/MM/yyyy")
    edit.setLayoutDirection(Qt.LeftToRight)
    edit.setDate(_qdate(value))
    return edit


def _table(headers: list[str], stretch: int) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    return table


def _cell(table: QTableWidget, r: int, c: int, text: Any, number: bool = False, color: str | None = None,
          bold: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    if number:
        item.setTextAlignment(NUM)
    if color:
        item.setForeground(QColor(color))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    table.setItem(r, c, item)
    return item


def balance_text(value: Decimal) -> str:
    """رصيد الموظف: موجب = «عليه» (عهدة معاه) · سالب = «ليه» (صرف من جيبه)."""
    if value == 0:
        return "0.00"
    return f"{ltr(money(abs(value)))} {'عليه' if value > 0 else 'ليه'}"


# =====================================================================================
# الواجهات المنبثقة
# =====================================================================================
class CustodyPickerDialog(QDialog):
    """العهد المفتوحة بس على الموظف — يعلّم على عهدة أو أكتر يسوّيها (رد المستخدم)."""

    HEADERS = ["", "الرقم", "التاريخ", "البيان", "أمر التوريد", "المبلغ", "اتسوّى", "الباقي"]

    def __init__(self, parent: QWidget, employee_name: str, rows: list[dict[str, Any]], selected: list[int]) -> None:
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle(f"📂 العهد المفتوحة — {employee_name}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(860, 440)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        header = QLabel(f"📂 العهد المفتوحة على «{employee_name}» — علّم على العهدة (أو العهد) اللي بتسوّيها")
        header.setWordWrap(True)
        header.setStyleSheet(HEADER_STYLE.format(c=GREEN))
        layout.addWidget(header)
        self.table = _table(self.HEADERS, 3)
        self.table.setRowCount(len(rows))
        self.checks: list[QCheckBox] = []
        for r, row in enumerate(rows):
            check = QCheckBox()
            check.setChecked(int(row["id"]) in selected)
            check.toggled.connect(self._update)
            holder = QWidget()
            box = QHBoxLayout(holder)
            box.setContentsMargins(6, 0, 6, 0)
            box.addWidget(check)
            self.table.setCellWidget(r, 0, holder)
            self.checks.append(check)
            _cell(self.table, r, 1, row["disb_no"], bold=True)
            _cell(self.table, r, 2, row["disb_date"].strftime("%d/%m/%Y"))
            _cell(self.table, r, 3, row.get("description") or "")
            _cell(self.table, r, 4, row.get("project_code") or "")
            _cell(self.table, r, 5, money(row["amount"]), number=True)
            _cell(self.table, r, 6, money(row["used_amount"]), number=True)
            _cell(self.table, r, 7, money(row["remaining"]), number=True, color=BLUE, bold=True)
        self.table.setColumnWidth(0, 40)
        self.table.cellClicked.connect(self._toggle)
        layout.addWidget(self.table, 1)
        self.total_label = QLabel("")
        self.total_label.setStyleSheet(f"color:{BLUE}; font-weight:900;")
        layout.addWidget(self.total_label)
        hint = QLabel("لو التسوية أكبر من العهد اللي علّمت عليها، الزيادة بتتسجل «ليه» عند الموظف.")
        hint.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        layout.addWidget(hint)
        buttons = QHBoxLayout()
        all_button = QPushButton("☑ علّم على الكل")
        all_button.clicked.connect(lambda: [c.setChecked(True) for c in self.checks])
        none_button = QPushButton("☐ شيل الكل")
        none_button.clicked.connect(lambda: [c.setChecked(False) for c in self.checks])
        ok = QPushButton("✔ تمام")
        ok.setStyleSheet(BUTTON_STYLE.format(bg=GREEN))
        ok.clicked.connect(self.accept)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(all_button)
        buttons.addWidget(none_button)
        buttons.addStretch(1)
        buttons.addWidget(ok)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self._update()

    def _toggle(self, row: int, column: int) -> None:
        if column != 0:
            self.checks[row].setChecked(not self.checks[row].isChecked())

    def selected_ids(self) -> list[int]:
        return [int(self.rows[i]["id"]) for i, c in enumerate(self.checks) if c.isChecked()]

    def _update(self, *_args: Any) -> None:
        ids = set(self.selected_ids())
        total = sum((Decimal(r["remaining"]) for r in self.rows if int(r["id"]) in ids), Decimal("0"))
        self.total_label.setText(f"اخترت {len(ids)} عهدة · الباقي فيهم {ltr(money(total))} جنيه")


class LineDialog(QDialog):
    """واجهة منبثقة لسطر واحد: «بند مصروف» (نوع · بيان · أمر توريد · مبلغ) أو «فلوس راجعة» (خزنة / بنك · مبلغ · بيان).

    ``lines`` = السطور اللي اتضافت (بـ «إضافة وسطر جديد» ممكن أكتر من سطر في نفس المرة)."""

    def __init__(self, screen: "CustodyScreen", kind: str, line: dict[str, Any] | None = None) -> None:
        super().__init__(screen)
        self.owner = screen
        self.kind = kind
        self.editing = line is not None
        self.lines: list[dict[str, Any]] = []
        expense = kind == "expense"
        title = ("➕ بند مصروف" if expense else "↩ فلوس راجعة") + (" — تعديل" if self.editing else "")
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(680, 340)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        header = QLabel(title + (" — المصروف بيتقيّد على حساب نوعه" if expense
                                 else " — الفلوس اللي الموظف رجّعها لخزنة أو بنك"))
        header.setStyleSheet(HEADER_STYLE.format(c=GREEN if expense else "#0E7490"))
        layout.addWidget(header)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(RIGHT)
        self.type_edit = LookupEdit("اكتب كود نوع المصروف أو اسمه… *")
        self.project_edit = LookupEdit("اختياري — رقم أمر التوريد أو المشروع")
        self.source_kind = QComboBox()
        self.source_edit = LookupEdit("اختار الخزنة… *")
        if expense:
            self.type_edit.set_items(screen.expense_rows())
            form.addRow("نوع المصروف *", self.type_edit)
        else:
            for text, value in SOURCE_ITEMS:
                self.source_kind.addItem(text, value)
            self.source_kind.currentIndexChanged.connect(self._on_source_kind)
            row = QHBoxLayout()
            row.addWidget(self.source_kind)
            row.addWidget(self.source_edit, 1)
            form.addRow("رجعت في *", row)
        self.description = QLineEdit()
        self.description.setMaxLength(300)
        self.description.setPlaceholderText("زي: سولار العربية / نقل كابلات للموقع" if expense
                                            else "زي: الباقي من العهدة")
        form.addRow("البيان", self.description)
        if expense:
            self.project_edit.set_items(screen.project_rows())
            form.addRow("أمر التوريد", self.project_edit)
        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        form.addRow("المبلغ *", self.amount_edit)
        layout.addLayout(form)
        self.error_label = QLabel("")
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(WARN_STYLE)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color:{OK_COLOR}; font-weight:900;")
        layout.addWidget(self.count_label)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.more_button = QPushButton("➕ إضافة وسطر جديد")
        self.more_button.setStyleSheet(BUTTON_STYLE.format(bg="#2563EB"))
        self.more_button.clicked.connect(self.add_more)
        self.more_button.setVisible(not self.editing)
        self.ok_button = QPushButton("✔ حفظ وقفل" if not self.editing else "✔ حفظ التعديل")
        self.ok_button.setStyleSheet(BUTTON_STYLE.format(bg=GREEN))
        self.ok_button.clicked.connect(self.add_close)
        cancel = QPushButton("إلغاء")
        cancel.clicked.connect(self._cancel)
        buttons.addWidget(self.more_button)
        buttons.addStretch(1)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        if not expense:
            self._on_source_kind()
        if line:
            self._fill(line)

    def _on_source_kind(self, *_args: Any) -> None:
        kind = self.source_kind.currentData() or "treasury"
        rows = self.owner.source_rows(kind)
        self.source_edit.set_items(rows, rows[0][0] if rows else None,
                                   placeholder="اختار الخزنة… *" if kind == "treasury" else "اختار الحساب البنكي… *")

    def _fill(self, line: dict[str, Any]) -> None:
        if self.kind == "expense":
            self.type_edit.set_value(line.get("expense_type_id"))
            self.project_edit.set_value(line.get("project_id"))
        else:
            self.source_kind.setCurrentIndex(0 if line.get("return_kind") == "treasury" else 1)
            self._on_source_kind()
            self.source_edit.set_value(line.get("treasury_id") or line.get("bank_account_id"))
        self.description.setText(line.get("description") or "")
        self.amount_edit.setText(money(line.get("amount")))

    def _line(self) -> dict[str, Any]:
        if self.kind == "expense":
            return {"line_kind": "expense", "expense_type_id": self.type_edit.currentData(),
                    "project_id": self.project_edit.currentData(), "amount": self.amount_edit.text(),
                    "description": self.description.text()}
        kind = self.source_kind.currentData()
        value = self.source_edit.currentData()
        return {"line_kind": "return", "return_kind": kind, "treasury_id": value if kind == "treasury" else None,
                "bank_account_id": value if kind == "bank" else None, "amount": self.amount_edit.text(),
                "description": self.description.text()}

    def _take(self) -> bool:
        try:
            clean = self.owner.service.check_line(self._line())
        except BusinessError as exc:
            self.error_label.setText(f"✖ {str(exc).replace('السطر 1: ', '')}")
            self.error_label.show()
            return False
        self.error_label.hide()
        self.lines.append(clean)
        return True

    def add_more(self) -> None:
        """يضيف السطر ويفضّي الخانات لسطر جديد (والواجهة مفتوحة)."""
        if not self._take():
            return
        self.amount_edit.clear()
        self.description.clear()
        if self.kind == "expense":
            self.type_edit.set_value(None)
            self.type_edit.setFocus()
        else:
            self.amount_edit.setFocus()
        self.count_label.setText(f"✔ اتضاف {len(self.lines)} سطر — كمّل أو دوس «حفظ وقفل»")

    def add_close(self) -> None:
        empty = not self.amount_edit.text().strip() and not self.description.text().strip()
        if self.lines and empty and (self.kind == "return" or self.type_edit.currentData() is None):
            self.accept()                                   # اتضاف قبل كده والخانات فاضية
            return
        if self._take():
            self.accept()

    def _cancel(self) -> None:
        if self.lines:
            self.accept()                                   # اللي اتضاف بـ «إضافة وسطر جديد» يفضل
        else:
            self.reject()


# =====================================================================================
# الشاشة
# =====================================================================================
class CustodyScreen(BaseScreen):
    MODULE = "hr"
    TARGET = "custody"
    TITLE = "العهد"
    ICON = "💼"
    FORM_TITLE = "صرف عهدة"
    NEW_TEXT = "جديد"
    LIST_TITLE = "المستندات"
    SEARCH_PLACEHOLDER = "🔍 الرقم أو الموظف أو البيان… (F1)"
    COLUMNS = [("doc_no", "الرقم"), ("doc_date", "التاريخ"), ("employee_name", "الموظف"), ("amount", "المبلغ"),
               ("description", "البيان"), ("status", "الحالة")]
    FORM_STRETCH, LIST_STRETCH = 12, 11
    EXIT_LAST = True

    def __init__(self, service: CustodyService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or CustodyService()
        self.kind = "disb"
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._choices: dict[str, Any] = {"employees": [], "treasuries": [], "banks": [], "expense_types": [],
                                         "projects": []}
        self._counts: dict[str, Any] | None = None
        self._filling = False
        self.lines: list[dict[str, Any]] = []
        self.custody_ids: list[int] = []
        self._summary: dict[str, Any] | None = None
        today = dt.date.today()
        self._from = today.replace(day=1)
        self._to = (self._from + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_side)
        self._load_choices()
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.count_label.parentWidget().layout().addLayout(self.paging)
        self._show_first()

    # ---- البناء --------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        self.form_box = box
        col = QVBoxLayout(box)
        col.setContentsMargins(14, 18, 14, 10)
        col.setSpacing(6)
        top = QFormLayout()
        top.setSpacing(7)
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (CD / CS) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = _date_edit(dt.date.today())
        self.date_edit.dateChanged.connect(self._changed)
        self.status_label = QLabel("")
        row = QHBoxLayout()
        row.addWidget(self.number_edit, 2)
        row.addWidget(QLabel("التاريخ *"))
        row.addWidget(self.date_edit, 1)
        row.addWidget(QLabel("الحالة"))
        row.addWidget(self.status_label)
        top.addRow("الرقم", row)
        self.employee_edit = LookupEdit("اكتب كود الموظف أو اسمه… *")
        self.employee_edit.value_changed.connect(self._on_employee)
        top.addRow("الموظف *", self.employee_edit)
        col.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_disb_page())
        self.stack.addWidget(self._build_settle_page())
        col.addWidget(self.stack, 1)

        bottom = QFormLayout()
        bottom.setSpacing(7)
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.textChanged.connect(self._changed)
        self.reference_no = QLineEdit()
        self.reference_no.setMaxLength(40)
        self.reference_no.setPlaceholderText("اختياري")
        self.reference_no.setToolTip("رقم الإيصال الورقي (اختياري)")
        row = QHBoxLayout()
        row.addWidget(self.description, 3)
        row.addWidget(QLabel("رقم الإيصال"))
        row.addWidget(self.reference_no, 1)
        bottom.addRow("البيان", row)
        col.addLayout(bottom)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        col.addWidget(self.year_note)

    def _build_disb_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(7)
        self.purpose_combo = QComboBox()
        for key, text in PURPOSES.items():
            self.purpose_combo.addItem("💼 " + text if key == "custody" else "💸 " + text, key)
        self.purpose_combo.setToolTip("«سداد اللي ليه» = الموظف صرف من جيبه في تسوية قبل كده وبيتصرفله")
        self.purpose_combo.currentIndexChanged.connect(self._on_purpose)
        form.addRow("النوع", self.purpose_combo)
        self.source_kind = QComboBox()
        for text, value in SOURCE_ITEMS:
            self.source_kind.addItem(text, value)
        self.source_kind.currentIndexChanged.connect(self._on_source_kind)
        self.source_edit = LookupEdit("اختار الخزنة… *")
        self.source_edit.value_changed.connect(self._changed)
        row = QHBoxLayout()
        row.addWidget(self.source_kind)
        row.addWidget(self.source_edit, 1)
        form.addRow("الصرف من *", row)
        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        self.amount_edit.textChanged.connect(self._changed)
        form.addRow("المبلغ *", self.amount_edit)
        self.project_edit = LookupEdit("اختياري — رقم أمر التوريد أو المشروع")
        form.addRow("أمر التوريد", self.project_edit)
        self.funds_label = QLabel("")
        self.funds_label.setWordWrap(True)
        self.funds_label.setStyleSheet(WARN_STYLE)
        self.funds_label.hide()
        form.addRow(self.funds_label)
        return page

    def _build_settle_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self.pick_button = QPushButton("📂 اختار العهد…")
        self.pick_button.setStyleSheet(BUTTON_STYLE.format(bg="#0E7490"))
        self.pick_button.setToolTip("العهد المفتوحة على الموظف — علّم على اللي بتسوّيه")
        self.pick_button.clicked.connect(self.choose_custodies)
        self.custody_label = QLabel("")
        self.custody_label.setWordWrap(True)
        self.custody_label.setStyleSheet(f"color:{BLUE}; font-weight:800;")
        buttons = QHBoxLayout()
        buttons.addWidget(self.pick_button)
        self.add_expense_button = QPushButton("➕ بند مصروف")
        self.add_expense_button.setStyleSheet(BUTTON_STYLE.format(bg=GREEN))
        self.add_expense_button.clicked.connect(lambda: self.add_line("expense"))
        self.add_return_button = QPushButton("↩ فلوس راجعة")
        self.add_return_button.setStyleSheet(BUTTON_STYLE.format(bg="#0E7490"))
        self.add_return_button.clicked.connect(lambda: self.add_line("return"))
        self.edit_line_button = QPushButton("✏️ تعديل")
        self.edit_line_button.setToolTip("تعديل السطر المحدد (أو دبل كليك عليه)")
        self.edit_line_button.clicked.connect(self.edit_line)
        self.delete_line_button = QPushButton("🗑 حذف")
        self.delete_line_button.setToolTip("حذف السطر المحدد")
        self.delete_line_button.clicked.connect(self.delete_line)
        for button in (self.add_expense_button, self.add_return_button, self.edit_line_button,
                       self.delete_line_button):
            button.setCursor(Qt.PointingHandCursor)
            buttons.addWidget(button)
        buttons.addStretch(1)
        col.addLayout(buttons)
        col.addWidget(self.custody_label)
        self.lines_table = _table(["النوع", "البند / الخزنة", "البيان", "أمر التوريد", "المبلغ"], 2)
        self.lines_table.setMinimumHeight(120)
        self.lines_table.cellDoubleClicked.connect(lambda *_a: self.edit_line())
        col.addWidget(self.lines_table, 1)
        self.totals_label = QLabel("")
        self.totals_label.setWordWrap(True)
        self.totals_label.setStyleSheet("background:#F8FAFC; border:1px solid #E2E8F0; border-radius:6px; "
                                        "padding:6px 10px; font-weight:900;")
        col.addWidget(self.totals_label)
        return page

    def _build_kind_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.kind_group = QButtonGroup(self)
        self.kind_group.setExclusive(True)
        self.kind_tabs: dict[str, QPushButton] = {}
        for key, (text, color) in KIND_INFO.items():
            button = QPushButton(text)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(36)
            button.setStyleSheet(
                f"QPushButton {{ background:#FFFFFF; color:{color}; border:1px solid #E2E8F0; "
                f"border-top:3px solid {color}; border-radius:6px; padding:5px 18px; font-weight:900; }}"
                "QPushButton:hover { background:#F8FAFC; }"
                f"QPushButton:checked {{ background:{color}; color:#FFFFFF; border:1px solid {color}; }}"
                "QPushButton:disabled { color:#94A3B8; border-top-color:#CBD5E1; }")
            button.clicked.connect(lambda _c=False, k=key: self.show_kind(k))
            self.kind_group.addButton(button)
            self.kind_tabs[key] = button
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_card(self) -> QGroupBox:
        self.card_box = QGroupBox("👤 الموظف")
        col = QVBoxLayout(self.card_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(5)
        self.card_label = QLabel("اختار موظف ← رصيده وعهده المفتوحة بتظهر هنا.")
        self.card_label.setWordWrap(True)
        self.card_label.setStyleSheet("font-weight:800;")
        col.addWidget(self.card_label)
        self.open_table = _table(["الرقم", "التاريخ", "المبلغ", "الباقي", "التسوية دي"], 0)
        self.open_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.open_table.setMinimumHeight(90)
        self.open_table.setMaximumHeight(150)
        col.addWidget(self.open_table)
        return self.card_box

    def _build_entry(self) -> QGroupBox:
        self.entry_box = QGroupBox("القيد اللي هيتعمل لما يترحّل")
        col = QVBoxLayout(self.entry_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.entry_table = _table(["الحساب", "البيان", "مدين", "دائن"], 0)
        self.entry_table.setSelectionMode(QAbstractItemView.NoSelection)
        for c in (2, 3):
            self.entry_table.setColumnWidth(c, 115)
        self.entry_table.setMinimumHeight(90)
        self.entry_table.setMaximumHeight(150)
        col.addWidget(self.entry_table)
        self.entry_label = QLabel("")
        self.entry_label.setWordWrap(True)
        col.addWidget(self.entry_label)
        return self.entry_box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        outer = QWidget()
        col = QVBoxLayout(outer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addLayout(self._build_kind_tabs())
        self.tab_line = QFrame()
        self.tab_line.setFixedHeight(3)
        col.addWidget(self.tab_line)
        col.addSpacing(6)
        body = QHBoxLayout()
        body.setSpacing(10)
        right_holder = QWidget()
        right = QVBoxLayout(right_holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(form_box, 1)
        right.addWidget(self._build_entry())
        left_holder = QWidget()
        left = QVBoxLayout(left_holder)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(8)
        left.addWidget(self._build_card())
        left.addWidget(list_box, 1)
        body.addWidget(right_holder, self.FORM_STRETCH)                 # RTL: أول عنصر = يمين
        body.addWidget(left_holder, self.LIST_STRETCH)
        col.addLayout(body, 1)
        content.addWidget(outer)
        self._paint_kind()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_doc)
        self.post_button.setToolTip("بيعمل القيد ويرحّله وبياخد الرقم الرسمي — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_doc)
        self.unpost_button.setToolTip("القيد بيتمسح والمستند بيرجع مسودة برقمه وتتعدّل (السنة لازم مفتوحة)")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        period = QHBoxLayout()
        self.period_from, self.period_to = _date_edit(self._from), _date_edit(self._to)
        for edit in (self.period_from, self.period_to):
            edit.dateChanged.connect(self._on_period)
        period.addWidget(QLabel("من"))
        period.addWidget(self.period_from, 1)
        period.addWidget(QLabel("إلى"))
        period.addWidget(self.period_to, 1)
        layout.addLayout(period)
        chips = QHBoxLayout()
        chips.setSpacing(5)
        self.status_buttons = QButtonGroup(self)
        self.status_buttons.setExclusive(True)
        for key, text in STATUS_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            self.status_buttons.addButton(chip)
            chips.addWidget(chip)
        self.status_buttons.buttonClicked.connect(self._on_status)
        chips.addStretch(1)
        layout.addLayout(chips)
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

    # ---- النوع (صرف / تسوية) ------------------------------------------------------
    def _paint_kind(self) -> None:
        text, color = KIND_INFO[self.kind]
        if hasattr(self, "kind_tabs"):
            editing = self.mode in EDITING
            for key, button in self.kind_tabs.items():
                button.setChecked(key == self.kind)
                button.setEnabled(not editing)
            self.tab_line.setStyleSheet(f"background:{color}; border:none;")
        self.form_box.setTitle(text.split(" ", 1)[1])
        self.form_box.setStyleSheet(f"QGroupBox#formBox {{ border-top:3px solid {color}; }}"
                                    f"QGroupBox#formBox::title {{ color:{color}; }}")
        self.stack.setCurrentIndex(0 if self.kind == "disb" else 1)
        self.description.setPlaceholderText("العهدة دي لإيه (زي: عهدة مصاريف موقع)" if self.kind == "disb"
                                            else "زي: تسوية مصاريف الأسبوع")

    def show_kind(self, kind: str) -> None:
        """التبديل بين «صرف عهدة» و«تسوية عهدة» (القائمة والفورم)."""
        if self.mode in EDITING or kind == self.kind or kind not in KIND_INFO:
            self._paint_kind()
            return
        self.kind = kind
        self.page = 1
        self._last_query = None
        self.current_id = None
        self.current_record = None
        self.current_version = None
        self._paint_kind()
        self.refresh_list()
        self._show_first()

    def _show_first(self) -> None:
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])
        else:
            self.show_empty()

    # ---- الاختيارات -------------------------------------------------------------
    def _load_choices(self) -> None:
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            pass

    def employee_rows(self) -> list[tuple[int, str, str]]:
        return [(e["id"], e["code"], f"{e['code']} — {e['name_ar']}" + (f" ({e['job_title']})" if e.get("job_title")
                                                                         else ""))
                for e in self._choices["employees"] if e["is_active"] and e["takes_custody"]]

    def source_rows(self, kind: str) -> list[tuple[int, str, str]]:
        if kind == "treasury":
            return [(t["id"], t["code"], f"{t['code']} — {t['name_ar']}") for t in self._choices["treasuries"]
                    if t["is_active"] and t["is_base_currency"]]
        return [(b["id"], b["code"], f"{b['bank_name']} — {b['code']}" +
                 (f" ({b['account_number']})" if b.get("account_number") else ""))
                for b in self._choices["banks"] if b["is_active"] and b["is_base_currency"]]

    def expense_rows(self) -> list[tuple[int, str, str]]:
        return [(x["id"], x["code"], f"{x['code']} — {x['name_ar']} ({x['account_code']})")
                for x in self._choices["expense_types"] if x["is_active"] and x["account_active"]]

    def project_rows(self) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name']}") for p in self._choices["projects"]
                if p["status"] != "cancelled"]

    def _fill_source(self, rec: dict | None = None) -> None:
        rec = rec or {}
        kind = self.source_kind.currentData()
        value = rec.get("treasury_id") if kind == "treasury" else rec.get("bank_account_id")
        rows = self.source_rows(kind)
        self.source_edit.set_items(rows, value, f"{rec.get('source_code', '')} — {rec.get('source_name', '')}",
                                   placeholder="اختار الخزنة… *" if kind == "treasury" else "اختار الحساب البنكي… *")

    def _on_source_kind(self, *_args: Any) -> None:
        if self._filling:
            return
        self._fill_source()
        rows = self.source_rows(self.source_kind.currentData())
        if self.mode in EDITING and rows:
            self.source_edit.set_value(rows[0][0])
        self._changed()

    def _on_purpose(self, *_args: Any) -> None:
        payback = self.purpose_combo.currentData() == "payback"
        self.project_edit.setEnabled(self.mode in EDITING and not payback)
        if payback:
            self.project_edit.set_value(None)
        if self._filling or self.mode not in EDITING:
            return
        if payback and self._summary and self._summary["credit_due"] > 0 and not self.amount_edit.text().strip():
            self.amount_edit.setText(money(self._summary["credit_due"]))
        self._changed()

    def _on_employee(self, *_args: Any) -> None:
        if self._filling:
            return
        self._load_summary()
        if self.kind == "settle" and self.mode in EDITING:
            self.custody_ids = []
            if self._summary and self._summary["open"]:
                self.choose_custodies()                       # أول ما يختار الموظف (رد المستخدم)
            self._render_lines()
        self._changed()

    def _load_summary(self) -> None:
        try:
            self._summary = self.service.employee_summary(self.employee_edit.currentData())
        except Exception:  # noqa: BLE001 - الكارت للعرض بس
            self._summary = None

    # ---- الواجهات المنبثقة ------------------------------------------------------
    def _exec(self, dialog: QDialog) -> bool:
        """بيفتح الواجهة المنبثقة (الاختبارات بتبدّلها)."""
        return dialog.exec() == QDialog.Accepted

    def choose_custodies(self) -> None:
        if self.mode not in EDITING or self.kind != "settle":
            return
        employee_id = self.employee_edit.currentData()
        if not employee_id:
            messages.warn(self, "العهد", "اختار الموظف الأول.")
            return
        rows = (self._summary or {}).get("open") or []
        if not rows:
            messages.info(self, "العهد", "مفيش عهد مفتوحة على الموظف ده — التسوية كلها هتتسجل «ليه» عنده.")
            return
        name = self.employee_edit.text().split(" — ", 1)[-1]
        dialog = CustodyPickerDialog(self, name, rows, self.custody_ids)
        if self._exec(dialog):
            self.custody_ids = dialog.selected_ids()
            self._render_lines()
            self._changed()

    def add_line(self, kind: str) -> None:
        if self.mode not in EDITING or self.kind != "settle":
            return
        dialog = LineDialog(self, kind)
        if self._exec(dialog) or dialog.lines:
            self.lines.extend(dialog.lines)
            self._render_lines()
            self._changed()

    def _selected_line(self) -> int | None:
        selected = self.lines_table.selectionModel().selectedRows()
        row = selected[0].row() if selected else self.lines_table.currentRow()
        return row if 0 <= row < len(self.lines) else None

    def edit_line(self) -> None:
        index = self._selected_line()
        if self.mode not in EDITING or index is None:
            return
        line = self.lines[index]
        dialog = LineDialog(self, line["line_kind"], line)
        if self._exec(dialog) and dialog.lines:
            self.lines[index] = dialog.lines[-1]
            self._render_lines()
            self._changed()

    def delete_line(self) -> None:
        index = self._selected_line()
        if self.mode not in EDITING or index is None:
            return
        del self.lines[index]
        self._render_lines()
        self._changed()

    # ---- سطور التسوية والإجماليات -----------------------------------------------------
    def _name(self, group: str, value: Any, field: str = "name_ar") -> str:
        row = next((r for r in self._choices.get(group, []) if r["id"] == value), None)
        return (row or {}).get(field) or ""

    def _line_texts(self, line: dict[str, Any]) -> tuple[str, str, str]:
        if line["line_kind"] == "expense":
            what = line.get("expense_name") or self._name("expense_types", line.get("expense_type_id"))
            project = line.get("project_code") or self._name("projects", line.get("project_id"), "code")
            return "🧾 مصروف", what, project
        if line.get("return_kind") == "treasury":
            what = line.get("source_name") or self._name("treasuries", line.get("treasury_id"))
        else:
            bank = next((b for b in self._choices["banks"] if b["id"] == line.get("bank_account_id")), None)
            what = line.get("source_name") or (f"{bank['bank_name']} — {bank['code']}" if bank else "")
        return "↩ مرتجع", what, ""

    def _totals(self) -> tuple[Decimal, Decimal]:
        expense = sum((Decimal(ln["amount"]) for ln in self.lines if ln["line_kind"] == "expense"), Decimal("0"))
        returned = sum((Decimal(ln["amount"]) for ln in self.lines if ln["line_kind"] == "return"), Decimal("0"))
        return expense, returned

    def _render_lines(self) -> None:
        self.lines_table.setRowCount(len(self.lines))
        for r, line in enumerate(self.lines):
            kind, what, project = self._line_texts(line)
            color = GREEN if line["line_kind"] == "expense" else "#0E7490"
            _cell(self.lines_table, r, 0, kind, color=color, bold=True)
            _cell(self.lines_table, r, 1, what)
            _cell(self.lines_table, r, 2, line.get("description") or "")
            _cell(self.lines_table, r, 3, project)
            _cell(self.lines_table, r, 4, money(line["amount"]), number=True, bold=True)
        expense, returned = self._totals()
        total = expense + returned
        rec = self.current_record or {}
        posted = self.mode not in EDITING and rec.get("status") == "posted"
        if posted:
            allocated, excess = Decimal(rec.get("allocated_total") or 0), Decimal(rec.get("excess_amount") or 0)
        else:
            preview = self._allocation(total, returned)
            allocated, excess = preview["allocated"], preview["excess"]
        text = (f"مصروفات {ltr(money(expense))} + راجع {ltr(money(returned))} = الإجمالي {ltr(money(total))}"
                f"  ·  على العهد {ltr(money(allocated))}")
        if excess > 0:
            text += f"  ·  <span style='color:{BLUE}'>ليه عندنا {ltr(money(excess))}</span>"
        self.totals_label.setText(text)
        # العهد المختارة
        if posted:
            links = [lk for lk in rec.get("links") or [] if Decimal(lk["allocated_amount"]) > 0]
            self.custody_label.setText("اتسوّى من: " + " + ".join(f"{ltr(lk['disb_no'])} ({ltr(money(lk['allocated_amount']))})"
                                                          for lk in links) if links else "مااتعلّمش على عهدة.")
        elif self.custody_ids:
            names = {r["id"]: r["disb_no"] for r in (self._summary or {}).get("open") or []}
            for lk in rec.get("links") or []:
                names.setdefault(lk["id"], lk["disb_no"] or f"مسودة {lk['draft_no']}")
            self.custody_label.setText("العهد اللي بتتسوّى: " + " · ".join(ltr(names.get(i, i)) for i in self.custody_ids))
        else:
            self.custody_label.setText("مااخترتش عهدة — دوس «📂 اختار العهد…»" if (self._summary or {}).get("open")
                                       else "مفيش عهد مفتوحة على الموظف.")

    def _allocation(self, total: Decimal, returned: Decimal) -> dict[str, Any]:
        rows = [r for r in ((self._summary or {}).get("open") or []) if int(r["id"]) in self.custody_ids]
        allocation, excess = self.service._allocate(rows, total)
        allocated = sum((r["allocated"] for r in allocation), Decimal("0"))
        return {"rows": allocation, "allocated": allocated, "excess": excess,
                "return_over": max(Decimal("0"), returned - allocated)}

    # ---- الكارت ومعاينة القيد -----------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        common = {"employee_id": self.employee_edit.currentData(), "description": self.description.text(),
                  "reference_no": self.reference_no.text()}
        if self.kind == "disb":
            kind = self.source_kind.currentData()
            value = self.source_edit.currentData()
            return {**common, "disb_date": _pydate(self.date_edit.date()), "purpose": self.purpose_combo.currentData(),
                    "source_kind": kind, "treasury_id": value if kind == "treasury" else None,
                    "bank_account_id": value if kind == "bank" else None, "amount": self.amount_edit.text(),
                    "project_id": self.project_edit.currentData()}
        return {**common, "settle_date": _pydate(self.date_edit.date()), "lines": [dict(ln) for ln in self.lines],
                "custody_ids": list(self.custody_ids)}

    def _refresh_side(self) -> None:
        self._timer.stop()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        if not editing and not rec:
            self._clear_side()
            return
        posted = not editing and rec.get("status") == "posted"
        self._render_card(posted)
        if self.kind == "settle":
            self._render_lines()
        else:
            self._refresh_funds(posted)
        self._refresh_entry(posted)

    def _render_card(self, posted: bool) -> None:
        s = self._summary
        name = self.employee_edit.text()
        if not s or not name:
            self.card_label.setText("اختار موظف ← رصيده وعهده المفتوحة بتظهر هنا.")
            self.open_table.setRowCount(0)
            return
        parts = [f"<b>{name}</b>", f"رصيده: {balance_text(s['balance'])}",
                 f"العهد المفتوحة: {len(s['open'])} بـ {ltr(money(s['open_total']))}"]
        if s["credit_due"] > 0:
            parts.append(f"<span style='color:{BLUE}'>ليه عندنا {ltr(money(s['credit_due']))} "
                         "(يتصرفله بـ «سداد اللي ليه»)</span>")
        self.card_label.setText(" · ".join(parts))
        rows: list[dict[str, Any]] = list(s["open"])
        alloc: dict[int, Decimal] = {}
        if self.kind == "settle":
            if posted:
                rows = [{"id": lk["id"], "disb_no": lk["disb_no"], "disb_date": lk["disb_date"], "amount": lk["amount"],
                         "remaining": Decimal(lk["amount"]) - Decimal(lk["other_used"]) - Decimal(lk["allocated_amount"])}
                        for lk in (self.current_record or {}).get("links") or []]
                alloc = {lk["id"]: Decimal(lk["allocated_amount"]) for lk in (self.current_record or {}).get("links") or []}
            else:
                expense, returned = self._totals()
                alloc = {int(r["id"]): r["allocated"] for r in self._allocation(expense + returned, returned)["rows"]}
        self.open_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            _cell(self.open_table, r, 0, row["disb_no"], bold=True)
            _cell(self.open_table, r, 1, row["disb_date"].strftime("%d/%m/%Y"))
            _cell(self.open_table, r, 2, money(row["amount"]), number=True)
            _cell(self.open_table, r, 3, money(row["remaining"]), number=True, color=BLUE, bold=True)
            value = alloc.get(int(row["id"]))
            text = ""
            if value is not None:
                text = money(value) + (" ✔ تتقفل" if not posted and value > 0 and value == row["remaining"] else "")
            _cell(self.open_table, r, 4, text, number=True, color=OK_COLOR, bold=True)

    def _refresh_funds(self, posted: bool) -> None:
        if posted or self.mode not in EDITING:
            self.funds_label.hide()
            return
        try:
            warning = self.service.funds_warning(self._data())
        except Exception:  # noqa: BLE001
            warning = None
        self.funds_label.setText(f"⚠️ {warning} — تقدر ترحّل بس هيجيلك تحذير." if warning else "")
        self.funds_label.setVisible(bool(warning))

    def _clear_side(self) -> None:
        self.card_label.setText("اختار موظف ← رصيده وعهده المفتوحة بتظهر هنا.")
        self.open_table.setRowCount(0)
        self.funds_label.hide()
        self.entry_table.setRowCount(0)
        self.entry_label.setText("")
        self.entry_label.setStyleSheet("")
        self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")
        if self.kind == "settle":
            self.lines_table.setRowCount(0)
            self.totals_label.setText("")
            self.custody_label.setText("")

    def _refresh_entry(self, posted: bool) -> None:
        rows: list[dict[str, Any]] = []
        error = ""
        if posted:
            self.entry_box.setTitle("القيد المرحّل")
            entry = self.service.entry_for(self.kind, int(self.current_id)) if self.current_id is not None else None
            rows = (entry or {}).get("lines") or []
        else:
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")
            try:
                data = self._data()
                rows = (self.service.preview_disbursement(data) if self.kind == "disb"
                        else self.service.preview_settlement(data))
            except BusinessError as exc:
                error = str(exc)
            except Exception:  # noqa: BLE001
                error = "مش قادر أجهّز القيد دلوقتي."
        self.entry_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            _cell(self.entry_table, r, 0, f"{row['account_code']} {row['account_name']}")
            _cell(self.entry_table, r, 1, row.get("description") or "")
            _cell(self.entry_table, r, 2, money(row["debit"]) if row["debit"] else "", number=True)
            _cell(self.entry_table, r, 3, money(row["credit"]) if row["credit"] else "", number=True)
        if error:
            self.entry_label.setText(f"✖ {error}")
            self._paint_entry(False)
        elif rows:
            d = sum((Decimal(r["debit"]) for r in rows), Decimal("0"))
            c = sum((Decimal(r["credit"]) for r in rows), Decimal("0"))
            self.entry_label.setText(f"✔ متوازن · مدين {ltr(money(d))} = دائن {ltr(money(c))}")
            self._paint_entry(d == c)
        else:
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")

    def _paint_entry(self, ok: bool) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.entry_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                       "padding:6px 10px; font-weight:900;")

    # ---- الأوضاع ----------------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        common = [self.date_edit, self.employee_edit, self.description, self.reference_no]
        if self.kind == "disb":
            return common + [self.purpose_combo, self.source_kind, self.source_edit, self.amount_edit,
                             self.project_edit]
        return common + [self.pick_button, self.add_expense_button, self.add_return_button, self.edit_line_button,
                         self.delete_line_button]

    def _is_editable_record(self) -> bool:
        rec = self.current_record or {}
        return rec.get("status") == "draft" and rec.get("year_status") != "closed"

    def set_mode(self, mode: str) -> None:
        for widget in (self.purpose_combo, self.source_kind, self.source_edit, self.amount_edit, self.project_edit,
                       self.pick_button, self.add_expense_button, self.add_return_button, self.edit_line_button,
                       self.delete_line_button):
            widget.setEnabled(False)
        super().set_mode(mode)
        editing = mode in EDITING
        if self.kind == "disb" and editing:
            self.project_edit.setEnabled(self.purpose_combo.currentData() != "payback")
        if not editing:
            rec = self.current_record or {}
            no = rec.get("disb_no") if self.kind == "disb" else rec.get("settle_no")
            self.edit_button.setEnabled(self.edit_button.isEnabled() and self._is_editable_record())
            self.delete_button.setEnabled(self.delete_button.isEnabled() and self._is_editable_record() and not no)
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.period_from.setEnabled(not editing)
        self.period_to.setEnabled(not editing)
        self._update_paging(editing)
        if hasattr(self, "kind_tabs"):
            self._paint_kind()
        if hasattr(self, "entry_table"):
            self._changed()

    def update_extra_buttons(self) -> None:
        rec = self.current_record or {}
        editing = self.mode in EDITING
        year_open = rec.get("year_status") != "closed"
        self.post_button.setEnabled(self.can("post") and (
            (editing and self.can("save")) or
            (not editing and self.current_id is not None and year_open and rec.get("status") == "draft")))
        self.unpost_button.setEnabled(self.can("unpost") and not editing and self.current_id is not None
                                      and year_open and rec.get("status") == "posted")

    def new_record(self) -> None:
        self._load_choices()
        super().new_record()
        if self.mode != "new":
            return
        self.number_edit.setText("بيتاخد مع الترحيل")
        self._show_status(None)
        self._set_lookups()
        if self.kind == "disb":
            rows = self.source_rows("treasury")
            if rows:
                self.source_edit.set_value(rows[0][0])
        self.set_mode("new")
        self.employee_edit.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()
        if self.mode == "edit" and not self._is_editable_record():
            self.load(self.current_id)
            messages.warn(self, "تعديل", "المستند ده مرحّل — الغي ترحيله الأول عشان تعدّله.")
        elif self.mode == "edit":
            self._load_summary()
            self._changed()

    # ---- الترحيل / إلغاء الترحيل ------------------------------------------------------
    def _label(self, rec: dict[str, Any]) -> str:
        return disb_label(rec) if self.kind == "disb" else settle_label(rec)

    def _confirm(self, data: dict[str, Any]) -> bool:
        """صرف: الرصيد مش مكفي ← تحذير · تسوية: الزيادة «ليه» / مفيش عهدة متعلّم عليها ← تنبيه."""
        try:
            if self.kind == "disb":
                warning = self.service.funds_warning(data)
                if warning and not messages.ask_yes_no(self, "⚠️ الرصيد مش مكفي", f"{warning}\n\nتكمّل الترحيل؟"):
                    return False
                return True
            expense, returned = self._totals()
            preview = self.service.allocation_preview(data["employee_id"], data["custody_ids"], expense + returned,
                                                      returned)
        except Exception:  # noqa: BLE001 - الحفظ نفسه هيطلّع الغلط
            return True
        if preview["excess"] > 0:
            note = (f"التسوية أكبر من العهد اللي اخترتها بـ {money(preview['excess'])} — الزيادة هتتسجل «ليه» عند "
                    "الموظف (صرف من جيبه) وتتصرفله بعدين بـ «سداد اللي ليه».")
            if not data["custody_ids"] and (self._summary or {}).get("open"):
                note = "مااخترتش أي عهدة — " + note
            if not messages.ask_yes_no(self, "ليه عند الموظف", f"{note}\n\nتكمّل الترحيل؟"):
                return False
        return True

    def post_doc(self) -> None:
        if not self.post_button.isEnabled():
            return
        save, post = ((self.service.save_disbursement, self.service.post_disbursement) if self.kind == "disb"
                      else (self.service.save_settlement, self.service.post_settlement))
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            if not self._confirm(payload):
                return
            if not messages.ask_yes_no(self, "ترحيل", "هيتحفظ المستند ويترحّل بقيد — بعد الترحيل مايتعدّلش "
                                                       "غير بـ «إلغاء الترحيل». تكمّل؟"):
                return
            try:
                saved = save(None if self.mode == "new" else int(self.current_id), payload,
                             expected_version=self.current_version, post=True)
            except ConcurrentEditError as exc:
                self.on_conflict(exc)
                return
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            self.current_id = saved
            self.mode = "view"
        else:
            rec = self.current_record or {}
            data = self._record_data(rec)
            if not self._confirm(data):
                return
            if not messages.ask_yes_no(self, "ترحيل", f"ترحيل {self._label(rec)}؟"):
                return
            try:
                post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل", f"اترحّل برقم {ltr(self._label(self.current_record or {}))}.")

    def unpost_doc(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = self._label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"قيد {label} هيتمسح والمستند هيرجع مسودة (برقمه) وتقدر تعدّله. تكمّل؟"):
            return
        unpost = self.service.unpost_disbursement if self.kind == "disb" else self.service.unpost_settlement
        try:
            unpost(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self._after_action()
        messages.info(self, "تم", f"{label} رجع مسودة — دوس «تعديل» عشان تعدّله.")

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
        self.record_saved.emit(self.current_id)

    # ---- القائمة والصفحات ------------------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return self.kind, self._from, self._to, self.status, keyword

    def _on_period(self, *_args: Any) -> None:
        self._from, self._to = _pydate(self.period_from.date()), _pydate(self.period_to.date())
        self._after_filter()

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("value")
        self._after_filter()

    def _after_filter(self) -> None:
        self.page = 1
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()

    def _go_page(self, page: int) -> None:
        self.page = max(1, min(self.pages, page))
        self.refresh_list()

    def _update_paging(self, editing: bool = False) -> None:
        if not hasattr(self, "prev_button"):
            return
        self.prev_button.setEnabled(not editing and self.page > 1)
        self.next_button.setEnabled(not editing and self.page < self.pages)
        self.page_label.setText(f"صفحة {self.page} من {self.pages}")

    def fetch_rows(self, keyword: str) -> list[dict]:
        query = self._query(keyword)
        if query != self._last_query:
            self._last_query = query
            self.page = 1
        self._counts = self.service.count_docs(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.service.list_docs(*query, page=self.page, page_size=PAGE_SIZE)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "doc_no":
            return value or f"مسودة {row.get('draft_no')}"
        if key == "amount":
            return money(value)
        if key == "status":
            return "مرحّل" if value == "posted" else "مسودة"
        if key == "description" and row.get("purpose") == "payback":
            return f"💸 سداد — {value}"
        if key == "description" and Decimal(row.get("excess_amount") or 0) > 0:
            return f"{value} (ليه {money(row['excess_amount'])})"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        status_col = [k for k, _t in self.COLUMNS].index("status")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(OK_COLOR if row.get("status") == "posted" else DRAFT_COLOR))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        counts = self._counts
        if counts is not None:
            what = "صرف" if self.kind == "disb" else "تسوية"
            self.count_label.setText(f"{counts['total']:,} {what} في الفترة · مسودة {counts['drafts']:,} · "
                                     f"مرحّل {counts['posted']:,} ({money(counts['posted_amount'])} جنيه)")
        self._update_paging(self.mode in EDITING)

    # ---- البيانات -----------------------------------------------------------------
    def _show_status(self, record: dict | None) -> None:
        if not record:
            self.status_label.setText("جديد")
            self.status_label.setStyleSheet(BADGE.format(bg="#EFF6FF", fg="#1D4ED8", bd="#BFDBFE"))
            self.year_note.hide()
            return
        text, bg, fg, bd = STATUS_BADGES[record["status"]]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(BADGE.format(bg=bg, fg=fg, bd=bd))
        note = ""
        if record.get("year_status") == "closed":
            note = f"🔒 السنة المالية {record['year_code']} مقفولة — المستند ده مايتعدّلش ولا يتلغى ترحيله."
        elif record["status"] == "posted" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = f"✔ اترحّل بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}"
        self.year_note.setText(note)
        self.year_note.setStyleSheet(f"color:{'#5B21B6' if record.get('year_status') == 'closed' else MUTED}; "
                                     "font-weight:800;")
        self.year_note.setVisible(bool(note))

    def _set_lookups(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
            self.employee_edit.set_items(self.employee_rows(), rec.get("employee_id"),
                                         f"{rec.get('employee_code', '')} — {rec.get('employee_name', '')}")
            if self.kind == "disb":
                self.purpose_combo.setCurrentIndex(1 if rec.get("purpose") == "payback" else 0)
                self.source_kind.setCurrentIndex(1 if rec.get("source_kind") == "bank" else 0)
                self._fill_source(rec)
                self.project_edit.set_items(self.project_rows(), rec.get("project_id"),
                                            f"{rec.get('project_code', '')} — {rec.get('project_name', '')}")
        finally:
            self._filling = False

    def fetch_record(self, record_id: Any) -> dict | None:
        if self.kind == "disb":
            return self.service.get_disbursement(int(record_id))
        return self.service.get_settlement(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            no = record.get("disb_no") if self.kind == "disb" else record.get("settle_no")
            self.number_edit.setText(no or f"مسودة {record.get('draft_no')} — الرقم الرسمي بيتاخد مع الترحيل")
            self.date_edit.setDate(_qdate(record["disb_date"] if self.kind == "disb" else record["settle_date"]))
            self.description.setText(record.get("description") or "")
            self.reference_no.setText(record.get("reference_no") or "")
            if self.kind == "disb":
                self.amount_edit.setText(money(record.get("amount")))
            else:
                self.lines = [dict(ln) for ln in record.get("lines") or []]
                self.custody_ids = [int(lk["id"]) for lk in record.get("links") or []]
            self._show_status(record)
        finally:
            self._filling = False
        self._set_lookups(record)
        self._load_summary()
        if self.kind == "settle":
            self._render_lines()                              # السطور على طول (من غير ما تستنى التايمر)
        self._changed()

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.amount_edit, self.description, self.reference_no):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.year_note.hide()
            self.lines = []
            self.custody_ids = []
            self._summary = None
        finally:
            self._filling = False
        self._set_lookups()
        if hasattr(self, "entry_table"):
            self._clear_side()

    @staticmethod
    def _parse(value: Any) -> Any:
        try:
            return parse_money(value, "المبلغ")
        except BusinessError:
            return value

    def _record_data(self, rec: dict[str, Any]) -> dict[str, Any]:
        if self.kind == "disb":
            return {k: rec.get(k) for k in ("disb_date", "employee_id", "purpose", "source_kind", "treasury_id",
                                            "bank_account_id", "amount", "project_id", "description",
                                            "reference_no")}
        return {"settle_date": rec.get("settle_date"), "employee_id": rec.get("employee_id"),
                "description": rec.get("description"), "reference_no": rec.get("reference_no"),
                "lines": rec.get("lines") or [], "custody_ids": [int(lk["id"]) for lk in rec.get("links") or []]}

    def collect(self) -> dict:
        return self._data()

    def save_record(self, payload: dict, record_id: Any) -> Any:
        rid = None if record_id is None else int(record_id)
        if self.kind == "disb":
            return self.service.save_disbursement(rid, payload, expected_version=self.current_version)
        return self.service.save_settlement(rid, payload, expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        if self.kind == "disb":
            self.service.delete_disbursement(int(record_id), expected_version=self.current_version)
        else:
            self.service.delete_settlement(int(record_id), expected_version=self.current_version)
