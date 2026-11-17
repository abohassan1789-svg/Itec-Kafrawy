"""شاشة «الأرصدة الافتتاحية» — النموذج 2 «شريط الأقسام + كل الأطراف جاهزة» (اختيار المستخدم 2026-11-17).

```
┌ [تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][إلغاء][تحديث][تصدير إكسل][خروج]                  (عرض) ┐
├────────────┬────────────────────────────────────────────────────────────────────────────┤
│ الأقسام     │ ┌ المستند ┐ الرقم OB · التاريخ * · الحالة · ملاحظات                              │
│ 💵 الخزائن 1│ ┌ 👥 العملاء ┐ 🔍 كود / اسم                                                    │
│ 🏦 البنوك   │   الكود │ الاسم │ الحساب │ مدين │ دائن      ← كل العملاء بخانة فاضية           │
│ 👥 العملاء 3│   («حسابات تانية»: الحساب │ الطرف │ مركز التكلفة │ البيان │ مدين │ دائن │ ✕ + سطر)   │
│ …          │ ✔ مدين … · دائن … · الفرق … ← «34 أرصدة افتتاحية» دائن                         │
└────────────┴────────────────────────────────────────────────────────────────────────────┘
```
* مستند واحد بس — «تعديل» بيفتح كل الأقسام، والتنقل بين الأقسام وانت بتعدّل بيحفظ اللي كتبته لحد «حفظ مسودة».
* الحفظ بيبعت **السطور اللي اتغيّرت بس** ومعاها القيمة اللي الشاشة شافتها (جهاز تاني غيّر نفس السطر ← تعارض).
* «حسابات تانية»: الافتراضي حسابات الميزانية، والتشيك بوكس «كل الحسابات» بيفتح الإيرادات والمصروفات.
* بعد الترحيل كله مقفول لحد «إلغاء الترحيل» (بالصلاحية بس).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError, ConcurrentEditError
from app.services.journal_service import parse_money
from app.services.opening_balance_service import (
    BALANCE_SHEET,
    PARTY_LABELS,
    SECTIONS,
    OpeningBalanceService,
)
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, export_rows_to_excel
from app.ui.common.lookup_edit import LookupEdit
from app.ui.common.theme import BORDER_CARD, GREEN, TEXT_MUTED, toolbar_button_style

OK_COLOR, BAD, MUTED, LOCKED = "#087443", "#B91C1C", "#64748B", "#5B21B6"
BADGE = "border-radius:9px; padding:3px 10px; font-weight:900; border:1px solid {bd}; background:{bg}; color:{fg};"
STATUS_BADGES = {"draft": ("مسودة", "#FEF3C7", "#92400E", "#FDE68A"),
                 "posted": ("مرحّل ✔", "#ECFDF3", "#087443", "#B7E4C7")}
# جدول القسم
M_CODE, M_NAME, M_ACC, M_DR, M_CR = range(5)
MASTER_HEADERS = ["الكود", "الاسم", "الحساب", "مدين", "دائن"]
# «حسابات تانية»
O_ACC, O_PARTY, O_CC, O_DESC, O_DR, O_CR, O_DEL = range(7)
OTHER_HEADERS = ["الحساب", "الطرف", "مركز التكلفة", "البيان", "مدين", "دائن", ""]
NO_PARTY = "— (الحساب مالوش طرف)"
EXPORT_COLUMNS = [("section_name", "القسم"), ("account_code", "الحساب"), ("account_name", "اسم الحساب"),
                  ("party_code", "كود الطرف"), ("party_name", "الطرف"), ("debit", "مدين"), ("credit", "دائن")]
ZERO = Decimal("0")


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    amount = Decimal(str(value))
    return f"{amount:,.2f}" if amount else ""


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


class OpeningBalancesScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "opening_balances"
    TITLE = "الأرصدة الافتتاحية"
    ICON = "🏁"
    FORM_TITLE = "المستند"
    EXIT_LAST = True

    def __init__(self, service: OpeningBalanceService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or OpeningBalanceService()
        self.section = "trs"
        self.section_rows: list[dict] = []
        self._edits: dict[tuple[str, int], dict] = {}      # (القسم، الطرف/الخزنة) ← التغيير
        self._lines: dict[int, tuple[Decimal, Decimal]] = {}  # كل سطور المستند (للإجماليات)
        self._other_meta: list[dict] = []                  # بيانات كل سطر في «حسابات تانية» (الأصلي)
        self._other_removed: list[dict] = []
        self._choices: dict[str, Any] = {"accounts": [], "cost_centers": [], "parties": {}}
        self._accounts: dict[int, dict] = {}
        self._filling = False
        super().__init__(parent)
        self.resize(1250, 760)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.load(1)

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        self.number_edit = QLineEdit("OB")
        self.number_edit.setReadOnly(True)
        self.number_edit.setMaximumWidth(90)
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setLayoutDirection(Qt.LeftToRight)
        self.date_edit.setToolTip("تاريخ الأرصدة (بداية الشغل على البرنامج) — بيتعدّل عادي وهو مسودة")
        self.status_label = QLabel("")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        self.notes.setPlaceholderText("اختياري")
        self.status_note = QLabel("")
        self.status_note.setWordWrap(True)
        top = QHBoxLayout()
        top.addWidget(self.number_edit)
        top.addWidget(QLabel("التاريخ *"))
        top.addWidget(self.date_edit)
        top.addWidget(QLabel("الحالة"))
        top.addWidget(self.status_label)
        top.addStretch(1)
        form.addRow("رقم المستند", top)
        form.addRow("ملاحظات", self.notes)
        form.addRow(self.status_note)

    def _build_side(self) -> QFrame:
        side = QFrame()
        side.setObjectName("sectionsSide")
        side.setFixedWidth(220)
        side.setStyleSheet(f"QFrame#sectionsSide {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; "
                           "border-radius:8px; }")
        layout = QVBoxLayout(side)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)
        title = QLabel("الأقسام")
        title.setStyleSheet(f"color:{GREEN}; font-weight:900; border-bottom:2px solid {GREEN}; padding-bottom:4px;")
        layout.addWidget(title)
        self.sections_list = QListWidget()
        self.sections_list.setStyleSheet(
            "QListWidget { border:0; background:transparent; outline:0; }"
            "QListWidget::item { padding:8px 6px; border-radius:6px; font-weight:800; }"
            f"QListWidget::item:selected {{ background:#ECFDF3; color:{GREEN}; border-right:4px solid {GREEN}; }}")
        for code, (name, icon) in SECTIONS.items():
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.UserRole, code)
            self.sections_list.addItem(item)
        self.sections_list.setCurrentRow(0)
        self.sections_list.currentItemChanged.connect(self._section_changed)
        layout.addWidget(self.sections_list, 1)
        hint = QLabel("الرقم = عدد اللي ليهم رصيد")
        hint.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        layout.addWidget(hint)
        return side

    def _build_section(self) -> QGroupBox:
        self.section_box = QGroupBox("")
        col = QVBoxLayout(self.section_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        top = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍 كود أو اسم…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.all_accounts = QCheckBox("كل الحسابات (الإيرادات والمصروفات كمان)")
        self.all_accounts.setToolTip("الافتراضي حسابات الميزانية بس (أصول · خصوم · حقوق ملكية)")
        self.all_accounts.toggled.connect(self._on_all_accounts)
        self.add_line_button = QPushButton("+ سطر")
        self.add_line_button.setCursor(Qt.PointingHandCursor)
        self.add_line_button.setStyleSheet(toolbar_button_style("new"))
        self.add_line_button.clicked.connect(lambda: self.add_other_line(focus=True))
        top.addWidget(self.filter_edit, 1)
        top.addWidget(self.all_accounts)
        top.addWidget(self.add_line_button)
        col.addLayout(top)
        self.stack = QStackedWidget()
        self.section_table = QTableWidget(0, len(MASTER_HEADERS))
        self.section_table.setHorizontalHeaderLabels(MASTER_HEADERS)
        self.section_table.verticalHeader().setVisible(False)
        self.section_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.section_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.section_table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        header = self.section_table.horizontalHeader()
        header.setSectionResizeMode(M_NAME, QHeaderView.Stretch)
        for c, width in ((M_CODE, 95), (M_ACC, 190), (M_DR, 120), (M_CR, 120)):
            self.section_table.setColumnWidth(c, width)
        self.section_table.itemChanged.connect(self._on_item_changed)
        self.other_table = QTableWidget(0, len(OTHER_HEADERS))
        self.other_table.setHorizontalHeaderLabels(OTHER_HEADERS)
        self.other_table.verticalHeader().setVisible(False)
        self.other_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.other_table.verticalHeader().setDefaultSectionSize(36)
        oh = self.other_table.horizontalHeader()
        oh.setSectionResizeMode(O_DESC, QHeaderView.Stretch)
        oh.setSectionResizeMode(O_DEL, QHeaderView.ResizeToContents)
        for c, width in ((O_ACC, 240), (O_PARTY, 190), (O_CC, 140), (O_DR, 110), (O_CR, 110)):
            self.other_table.setColumnWidth(c, width)
        self.stack.addWidget(self.section_table)
        self.stack.addWidget(self.other_table)
        col.addWidget(self.stack, 1)
        self.section_hint = QLabel("")
        self.section_hint.setWordWrap(True)
        self.section_hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        col.addWidget(self.section_hint)
        return self.section_box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        content.addWidget(self._build_side())                    # RTL: أول عنصر = يمين
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addWidget(form_box)
        column.addWidget(self._build_section(), 1)
        self.balance_label = QLabel("")
        self.balance_label.setAlignment(Qt.AlignCenter)
        self.balance_label.setWordWrap(True)
        column.addWidget(self.balance_label)
        content.addLayout(column, 1)
        list_box.setParent(self)                                 # مفيش قائمة سجلات (مستند واحد)
        list_box.hide()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_document)
        self.post_button.setToolTip("بيعمل قيد أوتوماتيك واحد OB والفرق على «34 أرصدة افتتاحية» — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_document)
        self.unpost_button.setToolTip("القيد بيتمسح والمستند يرجع مسودة ويتعدّل (السنة لازم مفتوحة)")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)

    # ---- الأقسام -----------------------------------------------------------
    def _section_changed(self, item: QListWidgetItem | None, _previous: Any = None) -> None:
        if item is not None:
            self.show_section(item.data(Qt.UserRole))

    def show_section(self, section: str) -> None:
        if section not in SECTIONS:
            return
        self.section = section
        row = list(SECTIONS).index(section)
        if self.sections_list.currentRow() != row:
            self.sections_list.blockSignals(True)
            self.sections_list.setCurrentRow(row)
            self.sections_list.blockSignals(False)
        name, icon = SECTIONS[section]
        self.section_box.setTitle(f"{icon}  {name}")
        other = section == "oth"
        self.stack.setCurrentIndex(1 if other else 0)
        self.filter_edit.setVisible(not other)
        self.all_accounts.setVisible(other)
        self.add_line_button.setVisible(other)
        self.add_line_button.setEnabled(self.mode in EDITING)
        if other:
            self.section_hint.setText("أي حساب تاني (أصول ثابتة · مجمع إهلاك · ضرايب · أوراق قبض/دفع · جاري الشركاء…) — "
                                      "الطرف بيتفتح لما الحساب يبقى رقابة وبيبقى إجباري · حساب «34» بيتحسب لوحده.")
            return
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        try:
            self.section_rows = self.service.section_rows(section)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل القسم", exc)
            self.section_rows = []
        self._fill_master()
        hints = {"trs": "كل خزنة على حسابها · الرصيد العادي مدين.", "bnk": "كل حساب بنكي على حسابه · الجاري المدين = دائن.",
                 "cus": "كل العملاء على حساب رقابة مجموعتهم · مدين = عليه · دائن = ليه (دفع زيادة).",
                 "sup": "كل الموردين على حساب رقابة مجموعتهم · دائن = ليه · مدين = دفعنا له زيادة.",
                 "emp": "عهدة كل موظف على «عهد الموظفين» · مدين = العهدة اللي معاه.",
                 "prt": "رأس مال كل شريك على «رأس المال» · دائن عادة (جاري الشركاء من «حسابات تانية»)."}
        self.section_hint.setText(hints.get(section, "") + " الفاضي = مالوش رصيد.")

    def _fill_master(self) -> None:
        self._filling = True
        table = self.section_table
        table.setRowCount(0)
        editing = self.mode in EDITING
        for r, row in enumerate(self.section_rows):
            table.insertRow(r)
            edit = self._edits.get((self.section, int(row["ref_id"])))
            debit = edit["debit"] if edit else money(row["debit"])
            credit = edit["credit"] if edit else money(row["credit"])
            name = row["name"] + ("" if row["is_active"] else " (موقوف)")
            cells = [(M_CODE, ltr(row["code"])), (M_NAME, name),
                     (M_ACC, f"{row['account_code'] or ''} {row['account_name'] or '— مش متحدد'}"),
                     (M_DR, debit), (M_CR, credit)]
            for c, text in cells:
                item = QTableWidgetItem(text)
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                if c in (M_DR, M_CR):
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
                    if editing and row["account_id"]:
                        flags |= Qt.ItemIsEditable
                item.setFlags(flags)
                if not row["is_active"]:
                    item.setForeground(QColor("#94A3B8"))
                table.setItem(r, c, item)
        self._filling = False
        self._apply_filter()

    def _apply_filter(self, *_args: Any) -> None:
        text = self.filter_edit.text().strip().lower()
        for r, row in enumerate(self.section_rows):
            hit = not text or text in str(row["code"]).lower() or text in str(row["name"]).lower()
            self.section_table.setRowHidden(r, not hit)

    def set_amount(self, r: int, side: str, text: str) -> None:
        """كتابة مبلغ في صف من القسم (زي ما المستخدم بيكتب في الخانة)."""
        self.section_table.item(r, M_DR if side == "dr" else M_CR).setText(text)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() not in (M_DR, M_CR):
            return
        r = item.row()
        row = self.section_rows[r]
        other_col = M_CR if item.column() == M_DR else M_DR
        if item.text().strip():
            self._filling = True
            self.section_table.item(r, other_col).setText("")
            self._filling = False
        self._edits[(self.section, int(row["ref_id"]))] = {
            "line_id": row["line_id"], "account_id": row["account_id"], "party_kind": row["party_kind"],
            "party_id": row["party_id"], "debit": self.section_table.item(r, M_DR).text(),
            "credit": self.section_table.item(r, M_CR).text(), "old_debit": row["debit"],
            "old_credit": row["credit"], "label": row["name"]}
        self._update_balance()

    # ---- «حسابات تانية» -------------------------------------------------------------
    def _load_choices(self) -> None:
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع التحميل
            self._choices = {"accounts": [], "cost_centers": [], "parties": {}}
        self._accounts = {a["id"]: a for a in self._choices["accounts"]}

    def _account_rows(self) -> list[tuple[int, str, str]]:
        every = self.all_accounts.isChecked()
        return [(a["id"], a["code"], f"{a['code']} — {a['name_ar']}") for a in self._choices["accounts"]
                if a["is_active"] and (every or a["account_type"] in BALANCE_SHEET)]

    def _party_rows(self, kind: str) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name_ar']}") for p in self._choices["parties"].get(kind, [])
                if p["is_active"]]

    def _cc_rows(self) -> list[tuple[int, str, str]]:
        return [(c["id"], c["code"], f"{c['code']} {c['name_ar']}") for c in self._choices["cost_centers"]
                if c["is_active"]]

    def _row_of(self, widget: QWidget, column: int) -> int:
        for r in range(self.other_table.rowCount()):
            if self.other_table.cellWidget(r, column) is widget:
                return r
        return -1

    def add_other_line(self, line: dict | None = None, focus: bool = False) -> None:
        line = line or {}
        r = self.other_table.rowCount()
        self.other_table.insertRow(r)
        account = LookupEdit("اكتب كود الحساب أو اسمه…")
        account.set_items(self._account_rows(), line.get("account_id"),
                          f"{line.get('account_code', '')} — {line.get('account_name', '')}")
        party = LookupEdit()
        cc = LookupEdit("اختياري")
        cc.set_items(self._cc_rows(), line.get("cost_center_id"),
                     f"{line.get('cost_center_code', '')} {line.get('cost_center_name', '')}")
        desc = QLineEdit(line.get("description") or "")
        desc.setMaxLength(300)
        debit, credit = QLineEdit(money(line.get("debit"))), QLineEdit(money(line.get("credit")))
        for edit in (debit, credit):
            edit.setLayoutDirection(Qt.LeftToRight)
            edit.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
            edit.setPlaceholderText("0.00")
            edit.textChanged.connect(self._update_balance)
        debit.textEdited.connect(lambda t, w=credit: t.strip() and w.clear())
        credit.textEdited.connect(lambda t, w=debit: t.strip() and w.clear())
        remove = QPushButton("✕")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("شيل السطر ده")
        remove.setStyleSheet(toolbar_button_style("delete"))
        remove.clicked.connect(lambda _c=False, w=remove: self._remove_other(w))
        for c, w in ((O_ACC, account), (O_PARTY, party), (O_CC, cc), (O_DESC, desc), (O_DR, debit), (O_CR, credit),
                     (O_DEL, remove)):
            self.other_table.setCellWidget(r, c, w)
        party_id = next((line.get(k) for k in ("customer_id", "supplier_id", "employee_id", "partner_id")
                         if line.get(k)), None)
        saved = bool(line.get("id"))                               # سطر من القاعدة (مش جديد)
        self._other_meta.append({"line_id": line.get("id"),
                                 "old_debit": (line.get("debit") or ZERO) if saved else ZERO,
                                 "old_credit": (line.get("credit") or ZERO) if saved else ZERO, "original": None})
        self._sync_party(r, party_id, f"{line.get('party_code') or ''} — {line.get('party_name') or ''}")
        if saved:
            self._other_meta[-1]["original"] = self._other_snapshot(r)
        account.value_changed.connect(lambda _v, w=account: self._on_account(w))
        editing = self.mode in EDITING
        for w in (account, cc, desc, debit, credit, remove):
            w.setEnabled(editing)
        if focus:
            account.setFocus()
        self._update_balance()

    def _remove_other(self, button: QPushButton) -> None:
        r = self._row_of(button, O_DEL)
        if r < 0:
            return
        meta = self._other_meta.pop(r)
        if meta["line_id"]:
            self._other_removed.append(meta)
        self.other_table.removeRow(r)
        self._update_balance()

    def _on_account(self, combo: LookupEdit) -> None:
        r = self._row_of(combo, O_ACC)
        if r >= 0:
            self._sync_party(r)

    def _sync_party(self, r: int, value: Any = None, text: str | None = None) -> None:
        account = self._accounts.get(self.other_table.cellWidget(r, O_ACC).currentData())
        party = self.other_table.cellWidget(r, O_PARTY)
        kind = (account or {}).get("party_kind")
        if kind:
            party.set_items(self._party_rows(kind), value, text,
                            placeholder=f"اكتب كود أو اسم {PARTY_LABELS[kind]} *")
        else:
            party.set_items([], placeholder=NO_PARTY)
        party.setEnabled(self.mode in EDITING and bool(kind))

    def _on_all_accounts(self, _checked: bool) -> None:
        for r in range(self.other_table.rowCount()):
            account = self.other_table.cellWidget(r, O_ACC)
            current = account.currentData()
            info = self._accounts.get(current) or {}
            account.set_items(self._account_rows(), current, f"{info.get('code', '')} — {info.get('name_ar', '')}")

    def _other_snapshot(self, r: int) -> dict[str, Any]:
        w = self.other_table.cellWidget
        account_id = w(r, O_ACC).currentData()
        kind = (self._accounts.get(account_id) or {}).get("party_kind")
        return {"account_id": account_id, "party_kind": kind, "party_id": w(r, O_PARTY).currentData() if kind else None,
                "cost_center_id": w(r, O_CC).currentData(), "description": w(r, O_DESC).text().strip() or None,
                "debit": w(r, O_DR).text().strip(), "credit": w(r, O_CR).text().strip()}

    @staticmethod
    def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
        def norm(x: dict) -> tuple:
            return (x["account_id"], x["party_id"], x["cost_center_id"], x["description"],
                    parse_money(x["debit"], "مدين"), parse_money(x["credit"], "دائن"))
        try:
            return norm(a) == norm(b)
        except BusinessError:
            return False

    # ---- الإجماليات ----------------------------------------------------------------
    def _totals(self) -> tuple[Decimal, Decimal]:
        amounts: dict[Any, tuple[Decimal, Decimal]] = dict(self._lines)
        for key, edit in self._edits.items():
            pair = (parse_money(edit["debit"], "مدين"), parse_money(edit["credit"], "دائن"))
            amounts[edit["line_id"] or ("new",) + key] = pair
        other_ids = {m["line_id"] for m in self._other_meta if m["line_id"]} | {
            m["line_id"] for m in self._other_removed}
        for line_id in other_ids:
            amounts.pop(line_id, None)
        for r in range(self.other_table.rowCount()):
            w = self.other_table.cellWidget
            amounts[("oth", r)] = (parse_money(w(r, O_DR).text(), "مدين"), parse_money(w(r, O_CR).text(), "دائن"))
        return (sum((d for d, _c in amounts.values()), ZERO), sum((c for _d, c in amounts.values()), ZERO))

    def _update_balance(self, *_args: Any) -> None:
        if not hasattr(self, "balance_label"):
            return
        try:
            d, c = self._totals()
        except BusinessError:
            self.balance_label.setText("✖ فيه مبلغ مكتوب غلط")
            self._paint(False)
            return
        diff = d - c
        opening = (self.current_record or {}).get("opening_account") or {}
        acc_text = f"«{opening.get('code', '34')} {opening.get('name_ar', 'أرصدة افتتاحية')}»"
        tail = "" if not diff else f" · الفرق {ltr(money(abs(diff)))} ← {acc_text} {'دائن' if diff > 0 else 'مدين'} (لوحده)"
        self.balance_label.setText(f"✔ مدين {ltr(money(d) or '0.00')} · دائن {ltr(money(c) or '0.00')}{tail}")
        self._paint(True)

    def _paint(self, ok: bool) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.balance_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                         "padding:6px 10px; font-weight:900;")

    def _update_side(self) -> None:
        sections = (self.current_record or {}).get("sections") or {}
        for i in range(self.sections_list.count()):
            item = self.sections_list.item(i)
            code = item.data(Qt.UserRole)
            name, icon = SECTIONS[code]
            n = (sections.get(code) or {}).get("lines", 0)
            item.setText(f"{icon}  {name}   ({n})" if n else f"{icon}  {name}")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.notes]

    def _posted(self) -> bool:
        return (self.current_record or {}).get("status") == "posted"

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        self.edit_button.setEnabled(self.edit_button.isEnabled() and not self._posted())
        if not hasattr(self, "section_table"):
            return
        if self.section != "oth" and not self._filling:
            self._fill_master()
        for r in range(self.other_table.rowCount()):
            for c in (O_ACC, O_CC, O_DESC, O_DR, O_CR, O_DEL):
                self.other_table.cellWidget(r, c).setEnabled(editing)
            account = self._accounts.get(self.other_table.cellWidget(r, O_ACC).currentData())
            self.other_table.cellWidget(r, O_PARTY).setEnabled(editing and bool((account or {}).get("party_kind")))
        self.add_line_button.setEnabled(editing)
        self.all_accounts.setEnabled(True)

    def update_extra_buttons(self) -> None:
        editing = self.mode in EDITING
        posted = self._posted()
        self.post_button.setEnabled(self.can("post") and not posted and (
            (editing and self.can("save")) or not editing))
        self.unpost_button.setEnabled(self.can("unpost") and not editing and posted)

    def edit_record(self) -> None:
        if self._posted():
            messages.warn(self, "تعديل", "المستند مرحّل — الغي ترحيله الأول عشان تعدّله.")
            return
        self._load_choices()
        super().edit_record()
        if self.mode == "edit" and self._posted():                # جهاز تاني رحّله في الوقت ده
            self.load(1)
            messages.warn(self, "تعديل", "المستند اترحّل من جهاز تاني — الغي ترحيله الأول عشان تعدّله.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return []

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_document()

    def fill_form(self, record: dict) -> None:
        self._filling = True
        self.date_edit.setDate(_qdate(record["balance_date"]))
        self.notes.setText(record.get("notes") or "")
        text, bg, fg, bd = STATUS_BADGES[record["status"]]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(BADGE.format(bg=bg, fg=fg, bd=bd))
        if record["status"] == "posted":
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = (f"✔ اترحّل بقيد أوتوماتيك {ltr('OB')} بواسطة {ltr(record.get('posted_by_name') or '')} — {ltr(when)} · "
                    "مقفول لحد «إلغاء الترحيل».")
            color = LOCKED
        elif record.get("year_code") is None:
            note, color = "⚠ مفيش سنة مالية فيها التاريخ ده — ضيفها من «السنة المالية والفترات» قبل الترحيل.", BAD
        else:
            note = f"مسودة · السنة المالية {record['year_code']} · الفرق بيتحسب على «34 أرصدة افتتاحية» لوحده مع الترحيل."
            color = MUTED
        self.status_note.setText(note)
        self.status_note.setStyleSheet(f"color:{color}; font-weight:800;")
        self._edits = {}
        self._other_removed = []
        self._other_meta = []
        try:
            lines = self.service.lines()
        except Exception:  # noqa: BLE001
            lines = []
        self._lines = {int(l["id"]): (Decimal(l["debit"]), Decimal(l["credit"])) for l in lines}
        if not self._choices["accounts"]:
            self._load_choices()
        other = [l for l in lines if l["section"] == "oth"]
        self.all_accounts.blockSignals(True)
        self.all_accounts.setChecked(any(l["account_type"] not in BALANCE_SHEET for l in other))
        self.all_accounts.blockSignals(False)
        self.other_table.setRowCount(0)
        self._filling = False
        for line in other:
            self.add_other_line(line)
        self._update_side()
        self.show_section(self.section)
        self._update_balance()

    def clear_form(self) -> None:
        self.status_label.setText("")
        self.status_note.setText("")
        self.section_table.setRowCount(0)
        self.other_table.setRowCount(0)
        self._edits, self._lines, self._other_meta, self._other_removed = {}, {}, [], []

    def collect(self) -> dict:
        changes = [e for e in self._edits.values()
                   if (parse_money(e["debit"], f"مدين «{e['label']}»"), parse_money(e["credit"], f"دائن «{e['label']}»"))
                   != (Decimal(e["old_debit"]), Decimal(e["old_credit"]))]
        for r, meta in enumerate(self._other_meta):
            snap = self._other_snapshot(r)
            if meta["line_id"] and meta["original"] and self._same(snap, meta["original"]):
                continue
            if not meta["line_id"] and not snap["account_id"] and not snap["debit"] and not snap["credit"]:
                continue                                          # سطر فاضي خالص
            changes.append({**snap, "line_id": meta["line_id"], "old_debit": meta["old_debit"],
                            "old_credit": meta["old_credit"], "label": f"حسابات تانية — السطر {r + 1}",
                            "new_row": not meta["line_id"]})
        for meta in self._other_removed:
            changes.append({"line_id": meta["line_id"], "debit": "", "credit": "", "old_debit": meta["old_debit"],
                            "old_credit": meta["old_credit"], "label": "سطر اتشال من «حسابات تانية»"})
        return {"header": {"balance_date": _pydate(self.date_edit.date()), "notes": self.notes.text()},
                "changes": changes}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        self.service.save(payload["header"], payload["changes"], expected_version=self.current_version)
        return 1

    # ---- الترحيل / إلغاء الترحيل --------------------------------------------------
    def post_document(self) -> None:
        if not self.post_button.isEnabled():
            return
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            if not messages.ask_yes_no(self, "ترحيل", "هيتحفظ المستند ويترحّل بقيد أوتوماتيك واحد (OB) — بعد الترحيل "
                                                       "مايتعدّلش غير بـ «إلغاء الترحيل». تكمّل؟"):
                return
            try:
                self.service.save(payload["header"], payload["changes"], expected_version=self.current_version,
                                  post=True)
            except ConcurrentEditError as exc:
                self.on_conflict(exc)
                return
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            self.mode = "view"
        else:
            if not messages.ask_yes_no(self, "ترحيل", "ترحيل مستند الأرصدة الافتتاحية بقيد أوتوماتيك واحد (OB)؟"):
                return
            try:
                self.service.post(expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self.load(1)
        self.record_saved.emit(1)
        messages.info(self, "تم الترحيل", "اترحّل المستند — القيد OB بيظهر في «القيود اليومية» (قراية بس).")

    def unpost_document(self) -> None:
        if not self.unpost_button.isEnabled():
            return
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   "قيد الأرصدة الافتتاحية هيتمسح والمستند يرجع مسودة وتقدر تعدّله. تكمّل؟"):
            return
        try:
            self.service.unpost(expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self.load(1)
        self.record_saved.emit(1)
        messages.info(self, "تم", "المستند رجع مسودة — دوس «تعديل» عشان تعدّله.")

    def reload_after_conflict(self) -> None:
        self.mode = "view"
        self.load(1)

    def export_excel(self) -> None:
        if not self.can("export"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل", f"{self.TITLE}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            rows = [{**l, "section_name": SECTIONS[l["section"]][0]} for l in self.service.lines()]
            export_rows_to_excel(Path(path), self.TITLE, EXPORT_COLUMNS, rows,
                                 lambda k, v, _r: money(v) if k in ("debit", "credit") else (v or ""))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", f"اتصدّر {len(rows)} سطر.")
