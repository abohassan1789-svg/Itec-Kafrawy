"""شاشة «القيود اليومية» — النموذج 1 «رأس القيد فوق + السطور + قائمة القيود شمال» (اختيار المستخدم 2026-11-16).

```
┌ [قيد جديد][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][تصدير إكسل][خروج]            (عرض) ┐
├────────────────────────────────────────────────┬─────────────────────────────────┤
│ ┌ رأس القيد ┐ الرقم · التاريخ · البيان · المرجع · الحالة │ ┌ القيود ┐ 🔍 رقم / بيان / مرجع      │
│ ┌ سطور القيد ┐                                     │   من [..] إلى [..]                  │
│   الحساب│البيان│مدين│دائن│الطرف│مركز التكلفة│أمر التوريد│✕ │   [الكل][مسودة][مرحّل] [يدوي][أوتوماتيك]│
│   [+ سطر]                                          │   الرقم│التاريخ│المبلغ│الحالة          │
│ ✔ متوازن · مدين 2,000.00 = دائن 2,000.00             │   › السابق  صفحة 1 من 3  التالي ‹      │
└────────────────────────────────────────────────┴─────────────────────────────────┘
```
* «حفظ مسودة» = حفظ من غير ترحيل (حتى لو مش متوازن) · «ترحيل» وانت بتعدّل = حفظ + ترحيل مرة واحدة.
* «إلغاء الترحيل» بالصلاحية بس — القيد بيرجع مسودة ويتعدّل · مفيش عكس ولا نسخ ولا طباعة (ردود المستخدم).
* الطرف بيتفتح بس لو الحساب حساب رقابة (وبيبقى إجباري) · القيود الأوتوماتيك قراية بس.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError, ConcurrentEditError
from app.services.journal_service import PAGE_SIZE, PARTY_LABELS, JournalService, entry_label
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.common.theme import GREEN, toolbar_button_style

OK_COLOR, BAD, MUTED, DRAFT_COLOR, AUTO_COLOR = "#087443", "#B91C1C", "#64748B", "#92400E", "#1D4ED8"
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
SOURCE_CHIPS = (("all", "كله"), ("manual", "يدوي"), ("auto", "أوتوماتيك"))
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
BADGE = "border-radius:9px; padding:3px 10px; font-weight:900; border:1px solid {bd}; background:{bg}; color:{fg};"
STATUS_BADGES = {"draft": ("مسودة", "#FEF3C7", "#92400E", "#FDE68A"),
                 "posted": ("مرحّل ✔", "#ECFDF3", "#087443", "#B7E4C7")}
# أعمدة جدول السطور
C_ACC, C_DESC, C_DR, C_CR, C_PARTY, C_CC, C_PRJ, C_DEL = range(8)
LINE_HEADERS = ["الحساب", "البيان", "مدين", "دائن", "الطرف", "مركز التكلفة", "أمر التوريد", ""]
NO_PARTY, NO_CC, NO_PRJ = "— (الحساب مالوش طرف)", "اختياري — كود أو اسم", "اختياري"


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):,.2f}"


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


class JournalEntriesScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "journal_entries"
    TITLE = "القيود اليومية"
    ICON = "📒"
    FORM_TITLE = "رأس القيد"
    LIST_TITLE = "القيود"
    NEW_TEXT = "قيد جديد"
    SEARCH_PLACEHOLDER = "🔍 رقم القيد أو البيان أو المرجع… (F1)"
    COLUMNS = [("entry_no", "رقم القيد"), ("entry_date", "التاريخ"), ("total_debit", "المبلغ"),
               ("status", "الحالة")]
    FORM_STRETCH, LIST_STRETCH = 17, 8
    EXIT_LAST = True

    def __init__(self, service: JournalService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or JournalService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self.source = "all"
        self._last_query: tuple | None = None
        self._choices: dict[str, Any] = {"accounts": [], "cost_centers": [], "projects": [], "parties": {}}
        self._accounts: dict[int, dict] = {}
        today = dt.date.today()
        self._from = today.replace(day=1)
        self._to = (self._from + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        list_layout = self.count_label.parentWidget().layout()
        list_layout.addLayout(self.paging)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (JV-2026-00001) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setLayoutDirection(Qt.LeftToRight)
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("القيد ده عن إيه (زي: مصاريف بنكية — كشف أكتوبر)")
        self.reference = QLineEdit()
        self.reference.setMaxLength(100)
        self.reference.setPlaceholderText("اختياري — رقم إيصال أو كشف أو عقد")
        self.status_label = QLabel("")
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        top = QHBoxLayout()
        top.addWidget(self.number_edit, 2)
        top.addWidget(QLabel("التاريخ *"))
        top.addWidget(self.date_edit, 1)
        top.addWidget(QLabel("الحالة"))
        top.addWidget(self.status_label)
        form.addRow("رقم القيد", top)
        form.addRow("البيان *", self.description)
        form.addRow("المرجع", self.reference)
        form.addRow(self.year_note)

    def _build_lines(self) -> QGroupBox:
        box = QGroupBox("سطور القيد")
        col = QVBoxLayout(box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.lines_table = QTableWidget(0, len(LINE_HEADERS))
        self.lines_table.setHorizontalHeaderLabels(LINE_HEADERS)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.lines_table.verticalHeader().setDefaultSectionSize(36)
        header = self.lines_table.horizontalHeader()
        for c, mode in ((C_DESC, QHeaderView.Stretch), (C_DEL, QHeaderView.ResizeToContents)):
            header.setSectionResizeMode(c, mode)
        for c, width in ((C_ACC, 235), (C_DR, 105), (C_CR, 105), (C_PARTY, 180), (C_CC, 135), (C_PRJ, 100)):
            self.lines_table.setColumnWidth(c, width)
        col.addWidget(self.lines_table, 1)
        row = QHBoxLayout()
        self.add_line_button = QPushButton("+ سطر")
        self.add_line_button.setCursor(Qt.PointingHandCursor)
        self.add_line_button.setStyleSheet(toolbar_button_style("new"))
        self.add_line_button.clicked.connect(lambda: self._add_line(focus=True))
        self.balance_label = QLabel("")
        self.balance_label.setAlignment(Qt.AlignCenter)
        row.addWidget(self.add_line_button)
        row.addWidget(self.balance_label, 1)
        col.addLayout(row)
        hint = QLabel("الطرف بيتفتح لما الحساب يبقى حساب رقابة (العملاء / الموردين / العهد / جاري الشركاء) وبيبقى إجباري · "
                      "مركز التكلفة وأمر التوريد اختياريين · المبالغ بالجنيه · البيان الفاضي = بيان القيد.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        col.addWidget(hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        lines = self._build_lines()
        align_form_right(lines)
        right.addWidget(lines, 1)
        content.addWidget(holder, self.FORM_STRETCH)                # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)
        self._load_choices()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_entry)
        self.post_button.setToolTip("بيتأكد إن القيد متوازن وفي سنة مفتوحة وبياخد الرقم الرسمي — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_entry)
        self.unpost_button.setToolTip("القيد بيرجع مسودة برقمه وسطوره ويتعدّل (السنة لازم مفتوحة)")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        # بعد «حفظ» على طول (قبل «حذف»)
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)

    def _chips(self, layout: QHBoxLayout, chips: tuple, current: str, slot) -> QButtonGroup:
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, text in chips:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == current)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            group.addButton(chip)
            layout.addWidget(chip)
        group.buttonClicked.connect(slot)
        return group

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        period = QHBoxLayout()
        self.from_edit, self.to_edit = QDateEdit(), QDateEdit()
        for edit, value in ((self.from_edit, self._from), (self.to_edit, self._to)):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setLayoutDirection(Qt.LeftToRight)
            edit.setDate(_qdate(value))
            edit.dateChanged.connect(self._on_period)
        period.addWidget(QLabel("من"))
        period.addWidget(self.from_edit, 1)
        period.addWidget(QLabel("إلى"))
        period.addWidget(self.to_edit, 1)
        layout.addLayout(period)
        chips = QHBoxLayout()
        chips.setSpacing(5)
        self.status_buttons = self._chips(chips, STATUS_CHIPS, "all", self._on_status)
        chips.addSpacing(8)
        self.source_buttons = self._chips(chips, SOURCE_CHIPS, "all", self._on_source)
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

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «قيد جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"accounts": [], "cost_centers": [], "projects": [], "parties": {}}
        self._accounts = {a["id"]: a for a in self._choices["accounts"]}

    def _account_rows(self) -> list[tuple[int, str, str]]:
        return [(a["id"], a["code"], f"{a['code']} — {a['name_ar']}") for a in self._choices["accounts"]
                if a["is_active"]]

    def _party_rows(self, kind: str) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name_ar']}") for p in self._choices["parties"].get(kind, [])
                if p["is_active"]]

    def _cc_rows(self) -> list[tuple[int, str, str]]:
        return [(c["id"], c["code"], f"{c['code']} {c['name_ar']}") for c in self._choices["cost_centers"]
                if c["is_active"]]

    def _project_rows(self) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name']}") for p in self._choices["projects"]
                if p["status"] == "open"]

    # ---- السطور ----------------------------------------------------------------
    def _row_of(self, widget: QWidget, column: int) -> int:
        for r in range(self.lines_table.rowCount()):
            if self.lines_table.cellWidget(r, column) is widget:
                return r
        return -1

    def _add_line(self, line: dict | None = None, focus: bool = False) -> None:
        line = line or {}
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)
        account = LookupEdit("اكتب كود الحساب أو اسمه…")
        account.set_items(self._account_rows(), line.get("account_id"),
                          f"{line.get('account_code', '')} — {line.get('account_name', '')}")
        desc = QLineEdit(line.get("description") or "")
        desc.setMaxLength(300)
        desc.setPlaceholderText("= بيان القيد")
        debit, credit = QLineEdit(money(line.get("debit") or None)), QLineEdit(money(line.get("credit") or None))
        for edit in (debit, credit):
            edit.setLayoutDirection(Qt.LeftToRight)
            edit.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
            edit.setPlaceholderText("0.00")
            edit.textChanged.connect(self._update_balance)
        debit.textEdited.connect(lambda t, w=credit: t.strip() and w.clear())
        credit.textEdited.connect(lambda t, w=debit: t.strip() and w.clear())
        party = LookupEdit()
        cc = LookupEdit(NO_CC)
        cc.set_items(self._cc_rows(), line.get("cost_center_id"),
                     f"{line.get('cost_center_code', '')} {line.get('cost_center_name', '')}")
        prj = LookupEdit(NO_PRJ)
        prj.set_items(self._project_rows(), line.get("project_id"), line.get("project_code"))
        remove = QPushButton("✕")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("شيل السطر ده")
        remove.setStyleSheet(toolbar_button_style("delete"))
        remove.clicked.connect(lambda _c=False, w=remove: self._remove_line(w))
        for c, w in ((C_ACC, account), (C_DESC, desc), (C_DR, debit), (C_CR, credit), (C_PARTY, party), (C_CC, cc),
                     (C_PRJ, prj), (C_DEL, remove)):
            self.lines_table.setCellWidget(r, c, w)
        party_id = next((line.get(k) for k in ("customer_id", "supplier_id", "employee_id", "partner_id")
                         if line.get(k)), None)
        self._sync_party(r, party_id, f"{line.get('party_code') or ''} — {line.get('party_name') or ''}")
        account.value_changed.connect(lambda _v, w=account: self._on_account(w))
        editing = self.mode in EDITING
        for w in (account, desc, debit, credit, cc, prj, remove):
            w.setEnabled(editing)
        if focus:
            account.setFocus()
        self._update_balance()

    def _remove_line(self, button: QPushButton) -> None:
        r = self._row_of(button, C_DEL)
        if r >= 0:
            self.lines_table.removeRow(r)
            self._update_balance()

    def _on_account(self, combo: LookupEdit) -> None:
        r = self._row_of(combo, C_ACC)
        if r >= 0:
            self._sync_party(r)

    def _sync_party(self, r: int, value: Any = None, text: str | None = None) -> None:
        """الطرف حسب الحساب: حساب رقابة ← قايمة (عملاء / موردين / موظفين / شركاء) وإجباري · غيره ← مقفول."""
        account = self._accounts.get(self.lines_table.cellWidget(r, C_ACC).currentData())
        party = self.lines_table.cellWidget(r, C_PARTY)
        kind = (account or {}).get("party_kind")
        if kind:
            party.set_items(self._party_rows(kind), value, text,
                            placeholder=f"اكتب كود أو اسم {PARTY_LABELS[kind]} *")
            party.setToolTip(f"الحساب ده حساب رقابة — لازم تختار {PARTY_LABELS[kind]}")
        else:
            party.set_items([], placeholder=NO_PARTY)
            party.setToolTip("")
        party.setEnabled(self.mode in EDITING and bool(kind))

    def _line_rows(self) -> list[dict[str, Any]]:
        rows = []
        for r in range(self.lines_table.rowCount()):
            w = self.lines_table.cellWidget
            rows.append({"account_id": w(r, C_ACC).currentData(), "description": w(r, C_DESC).text(),
                         "debit": w(r, C_DR).text(), "credit": w(r, C_CR).text(),
                         "party_id": w(r, C_PARTY).currentData(), "cost_center_id": w(r, C_CC).currentData(),
                         "project_id": w(r, C_PRJ).currentData()})
        return rows

    def _update_balance(self, *_args: Any) -> None:
        try:
            d, c = self.service.totals(self._line_rows())
        except BusinessError:
            self.balance_label.setText("✖ فيه مبلغ مكتوب غلط")
            self._paint_balance(False)
            return
        if d == c and d > 0:
            self.balance_label.setText(f"✔ متوازن · مدين {ltr(money(d))} = دائن {ltr(money(c))}")
            self._paint_balance(True)
        else:
            self.balance_label.setText(f"✖ مش متوازن · مدين {ltr(money(d))} · دائن {ltr(money(c))} · "
                                       f"الفرق {ltr(money(abs(d - c)))} — مايترحّلش")
            self._paint_balance(False)

    def _paint_balance(self, ok: bool) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.balance_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                         "padding:6px 10px; font-weight:900;")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.description, self.reference, self.add_line_button]

    def _is_editable_record(self) -> bool:
        rec = self.current_record or {}
        return (rec.get("status") == "draft" and rec.get("source_type") == "manual"
                and rec.get("year_status") != "closed")

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        for r in range(self.lines_table.rowCount()):
            for c in (C_ACC, C_DESC, C_DR, C_CR, C_CC, C_PRJ, C_DEL):
                w = self.lines_table.cellWidget(r, c)
                if w is not None:
                    w.setEnabled(editing)
            account = self._accounts.get(self.lines_table.cellWidget(r, C_ACC).currentData())
            self.lines_table.cellWidget(r, C_PARTY).setEnabled(editing and bool((account or {}).get("party_kind")))
        if not editing:
            rec = self.current_record or {}
            self.edit_button.setEnabled(self.edit_button.isEnabled() and self._is_editable_record())
            self.delete_button.setEnabled(self.delete_button.isEnabled() and self._is_editable_record()
                                          and not rec.get("entry_no"))
        for chip in (*self.status_buttons.buttons(), *self.source_buttons.buttons()):
            chip.setEnabled(not editing)
        self.from_edit.setEnabled(not editing)
        self.to_edit.setEnabled(not editing)
        self._update_paging(editing)

    def update_extra_buttons(self) -> None:
        rec = self.current_record or {}
        editing = self.mode in EDITING
        manual_open = rec.get("source_type") == "manual" and rec.get("year_status") != "closed"
        self.post_button.setEnabled(self.can("post") and (
            (editing and self.can("save")) or
            (not editing and self.current_id is not None and manual_open and rec.get("status") == "draft")))
        self.unpost_button.setEnabled(self.can("unpost") and not editing and self.current_id is not None
                                      and manual_open and rec.get("status") == "posted")

    def new_record(self) -> None:
        self._load_choices()
        super().new_record()
        if self.mode != "new":
            return
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.number_edit.setText("بيتاخد مع الترحيل")
        self._show_status(None)
        self._add_line()
        self._add_line()
        self.set_mode("new")
        self.description.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب القيد من جديد
        if self.mode == "edit" and not self._is_editable_record():  # جهاز تاني رحّله في الوقت ده
            self.load(self.current_id)
            messages.warn(self, "تعديل", "القيد ده مرحّل — الغي ترحيله الأول عشان تعدّله.")

    # ---- الترحيل / إلغاء الترحيل --------------------------------------------------
    def post_entry(self) -> None:
        if not self.post_button.isEnabled():
            return
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            if not messages.ask_yes_no(self, "ترحيل", "هيتحفظ القيد ويترحّل — بعد الترحيل مايتعدّلش غير بـ "
                                                       "«إلغاء الترحيل». تكمّل؟"):
                return
            try:
                saved = self.service.save_draft(None if self.mode == "new" else int(self.current_id), payload,
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
            if not messages.ask_yes_no(self, "ترحيل", f"ترحيل القيد {entry_label(self.current_record or {})}؟"):
                return
            try:
                self.service.post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل", f"اترحّل القيد برقم {ltr((self.current_record or {}).get('entry_no'))}.")

    def unpost_entry(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = entry_label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"القيد {label} هيرجع مسودة (برقمه وسطوره) وتقدر تعدّله. تكمّل؟"):
            return
        try:
            self.service.unpost(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self._after_action()
        messages.info(self, "تم", f"القيد {label} رجع مسودة — دوس «تعديل» عشان تعدّله.")

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
        self.record_saved.emit(self.current_id)

    # ---- القائمة والصفحات --------------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return self._from, self._to, self.status, self.source, keyword

    def _on_period(self, *_args: Any) -> None:
        self._from, self._to = _pydate(self.from_edit.date()), _pydate(self.to_edit.date())
        self._after_filter()

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("value")
        self._after_filter()

    def _on_source(self, chip: QPushButton) -> None:
        self.source = chip.property("value")
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
        self._counts = self.service.count_entries(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.service.list_entries(*query, page=self.page, page_size=PAGE_SIZE)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "entry_no":
            if row.get("source_type") != "manual":
                return row.get("source_no") or value or ""
            return value or f"مسودة {row.get('draft_no')}"
        if key == "total_debit":
            return money(value)
        if key == "status":
            text = "مرحّل" if value == "posted" else "مسودة"
            return f"{text} · أوتوماتيك" if row.get("source_type") != "manual" else text
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col = cols.index("status")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is None:
                continue
            color = AUTO_COLOR if row.get("source_type") != "manual" else (
                OK_COLOR if row.get("status") == "posted" else DRAFT_COLOR)
            item.setForeground(QColor(color))
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"{counts['total']:,} قيد في الفترة · مسودة {counts['drafts']:,} · "
                                     f"مرحّل {counts['posted']:,} ({money(counts['posted_amount'])} جنيه)")
        self._update_paging(self.mode in EDITING)

    # ---- البيانات ----------------------------------------------------------
    def _show_status(self, record: dict | None) -> None:
        if not record:
            self.status_label.setText("جديد")
            self.status_label.setStyleSheet(BADGE.format(bg="#EFF6FF", fg="#1D4ED8", bd="#BFDBFE"))
            self.year_note.hide()
            return
        text, bg, fg, bd = STATUS_BADGES[record["status"]]
        if record.get("source_type") != "manual":
            text += " · أوتوماتيك"
        self.status_label.setText(text)
        self.status_label.setStyleSheet(BADGE.format(bg=bg, fg=fg, bd=bd))
        note = ""
        if record.get("year_status") == "closed":
            note = f"🔒 السنة المالية {record['year_code']} مقفولة — القيد ده مايتعدّلش ولا يتلغى ترحيله."
        elif record.get("source_type") != "manual":
            note = f"🔗 قيد أوتوماتيك من المستند {ltr(record.get('source_no') or '')} — للقراية بس (بيتعدّل من المستند)."
        elif record["status"] == "posted" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = f"✔ اترحّل بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}"
        self.year_note.setText(note)
        self.year_note.setStyleSheet(f"color:{'#5B21B6' if record.get('year_status') == 'closed' else MUTED}; "
                                     "font-weight:800;")
        self.year_note.setVisible(bool(note))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_entry(int(record_id))

    def fill_form(self, record: dict) -> None:
        number = record.get("entry_no") or f"مسودة {record.get('draft_no')} — الرقم الرسمي بيتاخد مع الترحيل"
        self.number_edit.setText(number)
        self.date_edit.setDate(_qdate(record["entry_date"]))
        self.description.setText(record.get("description") or "")
        self.reference.setText(record.get("reference") or "")
        self._show_status(record)
        self.lines_table.setRowCount(0)
        for line in record.get("lines") or []:
            self._add_line(line)
        self._update_balance()

    def clear_form(self) -> None:
        for edit in (self.number_edit, self.description, self.reference):
            edit.clear()
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.status_label.setText("")
        self.status_label.setStyleSheet("")
        self.year_note.hide()
        self.lines_table.setRowCount(0)
        self.balance_label.setText("")
        self.balance_label.setStyleSheet("")

    def collect(self) -> dict:
        return {"entry_date": _pydate(self.date_edit.date()), "description": self.description.text(),
                "reference": self.reference.text(), "lines": self._line_rows()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_entry(int(record_id), expected_version=self.current_version)

