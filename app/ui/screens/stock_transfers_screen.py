"""شاشة «التحويل بين المخازن» — النموذج 8 «لوحة المراجعة قبل الترحيل» (اختيار المستخدم 2026-11-27).

```
┌ [تحويل جديد][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][تصدير إكسل][خروج]          (عرض) ┐
│ ┌ تحويل ┐ الرقم · التاريخ · الحالة          │ ┌ ✔ المراجعة — جاهز للترحيل ┐  │ ┌ التحويلات ┐ 🔍          │
│   من مخزن * [..] [فاتورة]                  │  ✔ من «الرئيسي» ← إلى «الفرع»   │   من [..] إلى [..]      │
│   إلى مخزن * [..] [فاتورة]                 │  ✔ 3 صنف — الكميات > صفر        │   [الكل][مسودة][مرحّل]  │
│   ملاحظات                                  │  ✖ صنف رصيده مش مكفي …          │   الرقم│التاريخ│من ← إلى│ │
│ ┌ الأصناف ┐ الصنف│الوحدة│الكمية│المتاح│…│✕ │  ℹ مفيش قيد (نفس حساب المخزون)   │                          │
│ [+ صنف]                    الإجمالي …     │ ┌ قبل / بعد ┐ ┌ 📒 القيد ┐        │   › السابق  صفحة 1  ‹     │
└────────────────────────────────────────────┴─────────────────────────────────┴──────────────────────────┘
```
* اللوحة بتتحسب وانت بتكتب (بعد ربع ثانية من آخر تغيير): الشروط ✔ / ✖ / ⚠ / ℹ · رصيد «من» و«إلى» قبل وبعد ·
  القيد اللي هيتعمل لو الوهمي داخل · و«ترحيل» بيقول لو فيه ✖.
* «حفظ مسودة» · «ترحيل» (وانت بتعدّل = حفظ + ترحيل) · «إلغاء الترحيل» بالصلاحية — زي حركات الخزائن.
* الرصيد مش مكفي ← ممنوع إلا بصلاحية «من غير رصيد» · الطباعة مع مرحلة الطباعة.
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
    QComboBox,
    QDateEdit,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError, ConcurrentEditError
from app.services.stock_ledger_service import KINDS, fmt_qty
from app.services.stock_transfer_service import PAGE_SIZE, ROUTE_TYPES, StockTransferService, transfer_label
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.common.theme import toolbar_button_style
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, STATUS_BADGES, ltr, money
from app.ui.screens.vouchers_screen import BAD, DRAFT_COLOR, MUTED, OK_COLOR, _pydate, _qdate

GREEN = "#137A38"
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
KIND_ITEMS = (("فاتورة", "invoice"), ("بيان", "statement"))
# أعمدة جدول الأصناف
C_ITEM, C_UNIT, C_QTY, C_AVAIL, C_COST, C_VALUE, C_DEL = range(7)
LINE_HEADERS = ["الصنف", "الوحدة", "الكمية", "المتاح", "التكلفة", "القيمة", ""]
# علامات لوحة المراجعة: (الرمز، اللون)
MARKS = {"ok": ("✔", OK_COLOR), "no": ("✖", BAD), "warn": ("⚠", "#B45309"), "info": ("ℹ", "#2563EB")}


def _num_item(text: str, color: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if color:
        item.setForeground(QColor(color))
    return item


class StockTransfersScreen(BaseScreen):
    MODULE = "inv"
    TARGET = "stock_transfers"
    TITLE = "التحويل بين المخازن"
    ICON = "⇄"
    FORM_TITLE = "تحويل"
    NEW_TEXT = "تحويل جديد"
    LIST_TITLE = "التحويلات"
    SEARCH_PLACEHOLDER = "🔍 رقم التحويل أو المخزن أو الصنف… (F1)"
    COLUMNS = [("transfer_no", "رقم التحويل"), ("transfer_date", "التاريخ"), ("route", "من ← إلى"),
               ("line_count", "أصناف"), ("status", "الحالة")]
    FORM_STRETCH, LIST_STRETCH = 14, 7
    EXIT_LAST = True

    def __init__(self, service: StockTransferService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or StockTransferService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._counts: dict[str, int] | None = None
        self._choices: dict[str, Any] = {"warehouses": [], "items": [], "units": {}}
        self._item_rows: list[tuple[int, str, str]] = []
        self._filling = False
        self._review: dict[str, Any] | None = None
        today = dt.date.today()
        self._from = today.replace(day=1)
        self._to = (self._from + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_review)
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.count_label.parentWidget().layout().addLayout(self.paging)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def _side_row(self, side: str) -> QHBoxLayout:
        wh = QComboBox()
        wh.currentIndexChanged.connect(self._changed)
        kind = QComboBox()
        for text, value in KIND_ITEMS:
            kind.addItem(text, value)
        kind.setToolTip("نوع المخزون: فاتورة أو بيان")
        kind.currentIndexChanged.connect(self._changed)
        setattr(self, f"{side}_wh", wh)
        setattr(self, f"{side}_kind", kind)
        row = QHBoxLayout()
        row.addWidget(wh, 3)
        row.addWidget(kind, 1)
        return row

    def build_form(self, box: QGroupBox) -> None:
        self.form_box = box
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (TR-2026-00001) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setLayoutDirection(Qt.LeftToRight)
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.date_edit.dateChanged.connect(self._changed)
        self.status_label = QLabel("")
        top = QHBoxLayout()
        top.addWidget(self.number_edit, 2)
        top.addWidget(QLabel("التاريخ *"))
        top.addWidget(self.date_edit, 1)
        top.addWidget(QLabel("الحالة"))
        top.addWidget(self.status_label)
        form.addRow("رقم التحويل", top)
        form.addRow("من مخزن *", self._side_row("from"))
        swap = QHBoxLayout()
        self.swap_button = QPushButton("⇅ اعكس")
        self.swap_button.setCursor(Qt.PointingHandCursor)
        self.swap_button.setToolTip("بدّل «من» و«إلى»")
        self.swap_button.clicked.connect(self._swap)
        self.type_label = QLabel("")
        swap.addWidget(self.swap_button)
        swap.addWidget(self.type_label)
        swap.addStretch(1)
        form.addRow("", swap)
        form.addRow("إلى مخزن *", self._side_row("to"))
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("اختياري (زي: طلب الفرع رقم 15)")
        form.addRow("ملاحظات", self.description)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        form.addRow(self.year_note)

    def _build_lines(self) -> QGroupBox:
        box = QGroupBox("الأصناف")
        col = QVBoxLayout(box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.lines_table = QTableWidget(0, len(LINE_HEADERS))
        self.lines_table.setHorizontalHeaderLabels(LINE_HEADERS)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.lines_table.verticalHeader().setDefaultSectionSize(34)
        header = self.lines_table.horizontalHeader()
        header.setSectionResizeMode(C_ITEM, QHeaderView.Stretch)
        header.setSectionResizeMode(C_DEL, QHeaderView.ResizeToContents)
        for c, width in ((C_UNIT, 80), (C_QTY, 75), (C_AVAIL, 80), (C_COST, 75), (C_VALUE, 90)):
            self.lines_table.setColumnWidth(c, width)
        col.addWidget(self.lines_table, 1)
        row = QHBoxLayout()
        self.add_line_button = QPushButton("+ صنف")
        self.add_line_button.setCursor(Qt.PointingHandCursor)
        self.add_line_button.setStyleSheet(toolbar_button_style("new"))
        self.add_line_button.clicked.connect(lambda: self._add_line(focus=True))
        self.total_label = QLabel("")
        self.total_label.setStyleSheet(f"color:{MUTED}; font-weight:900;")
        row.addWidget(self.add_line_button)
        row.addStretch(1)
        row.addWidget(self.total_label)
        col.addLayout(row)
        hint = QLabel("الكمية بالوحدة اللي تختارها (والبرنامج بيحوّلها للأساسية) · نفس الصنف مرتين بيتجمّع · "
                      "التكلفة بالمتوسط لوحدها (مابتتكتبش).")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        col.addWidget(hint)
        return box

    def _build_review(self) -> QWidget:
        holder = QWidget()
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self.review_box = QGroupBox("✔ المراجعة")
        rcol = QVBoxLayout(self.review_box)
        rcol.setContentsMargins(10, 18, 10, 8)
        self.checks_label = QLabel("")
        self.checks_label.setWordWrap(True)
        self.checks_label.setTextFormat(Qt.RichText)
        self.checks_label.setAlignment(Qt.AlignTop | Qt.AlignRight)
        rcol.addWidget(self.checks_label)
        col.addWidget(self.review_box)

        self.effect_box = QGroupBox("قبل / بعد")
        ecol = QVBoxLayout(self.effect_box)
        ecol.setContentsMargins(10, 18, 10, 8)
        self.effect_table = QTableWidget(0, 3)
        self.effect_table.setHorizontalHeaderLabels(["الصنف", "«من»", "«إلى»"])
        self.effect_table.verticalHeader().setVisible(False)
        self.effect_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.effect_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.effect_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            self.effect_table.setColumnWidth(c, 125)
        ecol.addWidget(self.effect_table)
        col.addWidget(self.effect_box, 1)

        self.entry_box = QGroupBox("📒 القيد")
        jcol = QVBoxLayout(self.entry_box)
        jcol.setContentsMargins(10, 18, 10, 8)
        self.entry_table = QTableWidget(0, 3)
        self.entry_table.setHorizontalHeaderLabels(["الحساب", "مدين", "دائن"])
        self.entry_table.verticalHeader().setVisible(False)
        self.entry_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.entry_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.entry_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            self.entry_table.setColumnWidth(c, 100)
        self.entry_table.setMaximumHeight(120)
        jcol.addWidget(self.entry_table)
        self.entry_label = QLabel("")
        self.entry_label.setWordWrap(True)
        self.entry_label.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        jcol.addWidget(self.entry_label)
        col.addWidget(self.entry_box)
        return holder

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        """النموذج 8: الفورم والأصناف يمين · لوحة المراجعة في النص · التحويلات شمال."""
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        lines = self._build_lines()
        align_form_right(lines)
        right.addWidget(lines, 1)
        content.addWidget(holder, self.FORM_STRETCH)                # RTL: أول عنصر = يمين
        content.addWidget(self._build_review(), 8)
        content.addWidget(list_box, self.LIST_STRETCH)
        self._load_choices()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_transfer)
        self.post_button.setToolTip("بيحرّك الأصناف في دفتر المخزون وبياخد الرقم الرسمي — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_transfer)
        self.unpost_button.setToolTip("الحركات (والقيد) بتتمسح والتحويل بيرجع مسودة برقمه (السنة لازم مفتوحة)")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        period = QHBoxLayout()
        self.period_from, self.period_to = QDateEdit(), QDateEdit()
        for edit, value in ((self.period_from, self._from), (self.period_to, self._to)):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setLayoutDirection(Qt.LeftToRight)
            edit.setDate(_qdate(value))
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

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «تحويل جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"warehouses": [], "items": [], "units": {}}
        self._item_rows = [(i["id"], i["code"], f"{i['code']} — {i['name']}") for i in self._choices["items"]
                           if i["is_active"]]
        self._fill_warehouses()

    def _fill_warehouses(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
            for side in ("from", "to"):
                combo: QComboBox = getattr(self, f"{side}_wh")
                current = rec.get(f"{side}_warehouse_id", combo.currentData())
                combo.clear()
                for w in self._choices["warehouses"]:
                    if w["is_active"] or w["id"] == current:
                        text = f"{w['code']} — {w['name_ar']}" + (" 👻 وهمي" if w["is_virtual"] else "") + \
                               ("" if w["is_active"] else " (موقوف)")
                        combo.addItem(text, w["id"])
                index = combo.findData(current)
                combo.setCurrentIndex(index if index >= 0 else -1)
                kind = getattr(self, f"{side}_kind")
                kind.setCurrentIndex(max(0, kind.findData(rec.get(f"{side}_kind") or kind.currentData() or "invoice")))
        finally:
            self._filling = False

    def _default_warehouses(self) -> None:
        """«من» = المخزن الافتراضي (أو أول نشط) · «إلى» = أول مخزن تاني حقيقي."""
        active = [w for w in self._choices["warehouses"] if w["is_active"]]
        first = next((w for w in active if w["is_default"]), active[0] if active else None)
        other = next((w for w in active if first and w["id"] != first["id"] and not w["is_virtual"]),
                     next((w for w in active if first and w["id"] != first["id"]), None))
        self._filling = True
        try:
            for side, w in (("from", first), ("to", other)):
                combo = getattr(self, f"{side}_wh")
                combo.setCurrentIndex(combo.findData(w["id"]) if w else -1)
                getattr(self, f"{side}_kind").setCurrentIndex(0)
        finally:
            self._filling = False

    def _swap(self) -> None:
        if self.mode not in EDITING:
            return
        fw, tw = self.from_wh.currentData(), self.to_wh.currentData()
        fk, tk = self.from_kind.currentData(), self.to_kind.currentData()
        self._filling = True
        try:
            self.from_wh.setCurrentIndex(self.from_wh.findData(tw))
            self.to_wh.setCurrentIndex(self.to_wh.findData(fw))
            self.from_kind.setCurrentIndex(self.from_kind.findData(tk))
            self.to_kind.setCurrentIndex(self.to_kind.findData(fk))
        finally:
            self._filling = False
        self._changed()

    # ---- السطور -------------------------------------------------------------
    def _row_of(self, widget: QWidget, column: int) -> int:
        for r in range(self.lines_table.rowCount()):
            if self.lines_table.cellWidget(r, column) is widget:
                return r
        return -1

    def _fill_units(self, combo: QComboBox, item_id: Any, unit_id: Any = None, unit_name: str = "") -> None:
        combo.blockSignals(True)
        combo.clear()
        for uid, name, factor in self._choices["units"].get(item_id, []) if item_id else []:
            combo.addItem(name if factor == 1 else f"{name} ({fmt_qty(factor)})", uid)
        if unit_id is not None and combo.findData(unit_id) < 0:
            combo.addItem(unit_name or str(unit_id), unit_id)
        combo.setCurrentIndex(max(0, combo.findData(unit_id)) if unit_id is not None else 0)
        combo.blockSignals(False)

    def _add_line(self, line: dict | None = None, focus: bool = False) -> None:
        line = line or {}
        r = self.lines_table.rowCount()
        self.lines_table.insertRow(r)
        item = LookupEdit("اكتب كود الصنف أو اسمه…")
        item.set_items(self._item_rows, line.get("item_id"),
                       f"{line.get('item_code', '')} — {line.get('item_name', '')}")
        unit = QComboBox()
        self._fill_units(unit, line.get("item_id"), line.get("unit_id"), line.get("unit_name") or "")
        qty = QLineEdit(fmt_qty(Decimal(line["qty"])) if line.get("qty") is not None else "")
        qty.setLayoutDirection(Qt.LeftToRight)
        qty.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        qty.setPlaceholderText("0")
        remove = QPushButton("✕")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("شيل الصنف ده")
        remove.setStyleSheet(toolbar_button_style("delete"))
        remove.clicked.connect(lambda _c=False, w=remove: self._remove_line(w))
        for c, w in ((C_ITEM, item), (C_UNIT, unit), (C_QTY, qty), (C_DEL, remove)):
            self.lines_table.setCellWidget(r, c, w)
        for c in (C_AVAIL, C_COST, C_VALUE):
            self.lines_table.setItem(r, c, _num_item(""))
        item.value_changed.connect(lambda _v, w=item: self._on_item(w))
        unit.currentIndexChanged.connect(self._changed)
        qty.textChanged.connect(self._changed)
        editing = self.mode in EDITING
        for w in (item, unit, qty, remove):
            w.setEnabled(editing)
        if focus:
            item.setFocus()
        self._changed()

    def _remove_line(self, button: QPushButton) -> None:
        r = self._row_of(button, C_DEL)
        if r >= 0:
            self.lines_table.removeRow(r)
            self._changed()

    def _on_item(self, edit: LookupEdit) -> None:
        r = self._row_of(edit, C_ITEM)
        if r >= 0:
            self._fill_units(self.lines_table.cellWidget(r, C_UNIT), edit.currentData())
        self._changed()

    def _line_rows(self) -> list[dict[str, Any]]:
        rows = []
        for r in range(self.lines_table.rowCount()):
            w = self.lines_table.cellWidget
            rows.append({"item_id": w(r, C_ITEM).currentData(), "unit_id": w(r, C_UNIT).currentData(),
                         "qty": w(r, C_QTY).text()})
        return rows

    def _set_lines(self, lines: list[dict]) -> None:
        self.lines_table.setRowCount(0)
        for line in lines:
            self._add_line(line)

    # ---- لوحة المراجعة ----------------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        return {"transfer_date": _pydate(self.date_edit.date()), "from_warehouse_id": self.from_wh.currentData(),
                "to_warehouse_id": self.to_wh.currentData(), "from_kind": self.from_kind.currentData(),
                "to_kind": self.to_kind.currentData(), "description": self.description.text(),
                "lines": self._line_rows()}

    def _update_type(self) -> None:
        fw, tw = self.from_wh.currentData(), self.to_wh.currentData()
        whs = {w["id"]: w for w in self._choices["warehouses"]}
        if fw is None or tw is None:
            self.type_label.setText("")
            self.type_label.setStyleSheet("")
            return
        key = "virtual" if (whs.get(fw, {}).get("is_virtual") or whs.get(tw, {}).get("is_virtual")) else \
            ("kind" if fw == tw else "wh")
        name, color = ROUTE_TYPES[key]
        self.type_label.setText(name)
        self.type_label.setStyleSheet(BADGE.format(bg=color, fg="#FFFFFF", bd=color))
        self.form_box.setStyleSheet(f"QGroupBox#formBox {{ border-top:3px solid {color}; }}"
                                    f"QGroupBox#formBox::title {{ color:{color}; }}")

    def _refresh_review(self) -> None:
        self._timer.stop()
        self._update_type()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        if not editing and not rec:
            self._clear_review()
            return
        posted = not editing and rec.get("status") == "posted"
        try:
            review = self.service.review(self._data(), int(self.current_id) if posted else None)
        except Exception as exc:  # noqa: BLE001 - اللوحة للعرض بس
            review = {"checks": [("no", messages.error_text(exc))], "lines": [], "entry": None, "ready": False,
                      "value": Decimal("0"), "entry_error": ""}
        self._review = review
        self._show_review(review, posted)

    def _clear_review(self) -> None:
        self._review = None
        self.checks_label.setText("")
        self.review_box.setTitle("✔ المراجعة")
        self.effect_table.setRowCount(0)
        self.entry_table.setRowCount(0)
        self.entry_label.setText("")
        self.total_label.setText("")
        for r in range(self.lines_table.rowCount()):
            for c in (C_AVAIL, C_COST, C_VALUE):
                self.lines_table.item(r, c).setText("")

    def _show_review(self, review: dict[str, Any], posted: bool) -> None:
        if posted:
            self.review_box.setTitle("✔ مرحّل")
            self.review_box.setStyleSheet(f"QGroupBox::title {{ color:{OK_COLOR}; }}")
        elif review["ready"]:
            self.review_box.setTitle("✔ المراجعة — جاهز للترحيل")
            self.review_box.setStyleSheet(f"QGroupBox::title {{ color:{OK_COLOR}; }}")
        else:
            self.review_box.setTitle("✖ المراجعة — فيه مشاكل")
            self.review_box.setStyleSheet(f"QGroupBox::title {{ color:{BAD}; }}")
        html = []
        for state, text in review["checks"]:
            mark, color = MARKS.get(state, MARKS["info"])
            html.append(f"<div style='margin:3px 0'><b style='color:{color}'>{mark}</b>&nbsp; {text}</div>")
        self.checks_label.setText("".join(html))

        by_item = {ln["item_id"]: ln for ln in review["lines"]}
        for r in range(self.lines_table.rowCount()):
            line = by_item.get(self.lines_table.cellWidget(r, C_ITEM).currentData())
            avail = cost = value = ""
            color = None
            if line:
                avail = fmt_qty(line["from_before"])
                cost = money(line["unit_cost"]) if line["unit_cost"] is not None else "—"
                value = money(line["value"])
                color = BAD if line["error"] else None
            self.lines_table.item(r, C_AVAIL).setText(avail)
            self.lines_table.item(r, C_AVAIL).setForeground(QColor(color or "#111827"))
            self.lines_table.item(r, C_AVAIL).setToolTip(line["error"] if line and line["error"] else "")
            self.lines_table.item(r, C_COST).setText(cost)
            self.lines_table.item(r, C_VALUE).setText(value)
            qty = self.lines_table.cellWidget(r, C_QTY)
            qty.setStyleSheet("QLineEdit { border:1px solid #DC2626; background:#FEF2F2; }" if color else "")
            qty.setToolTip(line["error"] if line and line["error"] else "")
        self.total_label.setText(f"{len(review['lines'])} صنف · القيمة {ltr(money(review['value']))}"
                                 if review["lines"] else "")

        self.effect_box.setTitle("الرصيد دلوقتي" if posted else "قبل / بعد")
        self.effect_table.setRowCount(len(review["lines"]))
        for r, line in enumerate(review["lines"]):
            self.effect_table.setItem(r, 0, QTableWidgetItem(f"{line['item_code']} {line['item_name']}"))
            if posted:
                cells = (fmt_qty(line["from_before"]), fmt_qty(line["to_before"]))
            else:
                cells = (f"{fmt_qty(line['from_before'])} ← {fmt_qty(line['from_after'])}",
                         f"{fmt_qty(line['to_before'])} ← {fmt_qty(line['to_after'])}")
            self.effect_table.setItem(r, 1, _num_item(cells[0], BAD if line["from_after"] < 0 else None))
            self.effect_table.setItem(r, 2, _num_item(cells[1], None if posted else OK_COLOR))

        entry_rows: list[dict[str, Any]] = []
        if posted and self.current_id is not None:
            self.entry_box.setTitle("📒 القيد المرحّل")
            entry = self.service.entry_for(int(self.current_id))
            entry_rows = [{"account": f"{ln['account_code']} {ln['account_name']}", "debit": ln["debit"],
                           "credit": ln["credit"]} for ln in (entry or {}).get("lines") or []]
        else:
            self.entry_box.setTitle("📒 القيد اللي هيتعمل")
            entry_rows = [{"account": f"{ln['account_code']} {ln['account_name']}", "debit": ln["debit"],
                           "credit": ln["credit"]} for ln in review.get("entry") or []]
        self.entry_table.setRowCount(len(entry_rows))
        for r, row in enumerate(entry_rows):
            self.entry_table.setItem(r, 0, QTableWidgetItem(row["account"]))
            self.entry_table.setItem(r, 1, _num_item(money(row["debit"]) if row["debit"] else ""))
            self.entry_table.setItem(r, 2, _num_item(money(row["credit"]) if row["credit"] else ""))
        self.entry_table.setVisible(bool(entry_rows))
        if review.get("entry_error"):
            self.entry_label.setText(f"✖ {review['entry_error']}")
        elif entry_rows:
            self.entry_label.setText("✔ متوازن — بقيمة حركات المخزون" + ("" if posted else " وقت الترحيل"))
        else:
            self.entry_label.setText("مفيش قيد — المخزنين على نفس حساب المخزون (المخزن بُعد)، والحركة في دفتر "
                                     "المخزون بس. القيد بيتعمل لو المخزن الوهمي داخل في التحويل.")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.from_wh, self.from_kind, self.to_wh, self.to_kind, self.description,
                self.add_line_button, self.swap_button]

    def _is_editable_record(self) -> bool:
        rec = self.current_record or {}
        return rec.get("status") == "draft" and rec.get("year_status") != "closed"

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        for r in range(self.lines_table.rowCount()):
            for c in (C_ITEM, C_UNIT, C_QTY, C_DEL):
                w = self.lines_table.cellWidget(r, c)
                if w is not None:
                    w.setEnabled(editing)
        if not editing:
            rec = self.current_record or {}
            self.edit_button.setEnabled(self.edit_button.isEnabled() and self._is_editable_record())
            self.delete_button.setEnabled(self.delete_button.isEnabled() and self._is_editable_record()
                                          and not rec.get("transfer_no"))
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.period_from.setEnabled(not editing)
        self.period_to.setEnabled(not editing)
        self._update_paging(editing)
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
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.number_edit.setText("بيتاخد مع الترحيل")
        self._show_status(None)
        self._default_warehouses()
        self._set_lines([])
        self._add_line()
        self.set_mode("new")
        self.lines_table.cellWidget(0, C_ITEM).setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب التحويل من جديد
        if self.mode == "edit" and not self._is_editable_record():  # جهاز تاني رحّله في الوقت ده
            self.load(self.current_id)
            messages.warn(self, "تعديل", "التحويل ده مرحّل — الغي ترحيله الأول عشان تعدّله.")

    # ---- الترحيل / إلغاء الترحيل --------------------------------------------------
    def _review_problems(self) -> str:
        review = self.service.review(self._data(), None)
        bad = [text for state, text in review["checks"] if state == "no"]
        return "\n".join(f"✖ {t}" for t in bad)

    def post_transfer(self) -> None:
        if not self.post_button.isEnabled():
            return
        try:
            problems = self._review_problems()
        except Exception:  # noqa: BLE001 - الترحيل نفسه هيطلّع الغلط
            problems = ""
        if problems:
            messages.warn(self, "مايترحّلش", f"لوحة المراجعة فيها مشاكل:\n\n{problems}")
            return
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            if not messages.ask_yes_no(self, "ترحيل", "هيتحفظ التحويل ويترحّل (الأصناف هتتحرك في المخازن) — بعد "
                                                       "الترحيل مايتعدّلش غير بـ «إلغاء الترحيل». تكمّل؟"):
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
            if not messages.ask_yes_no(self, "ترحيل", f"ترحيل التحويل {transfer_label(self.current_record or {})}؟"):
                return
            try:
                self.service.post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل",
                      f"اترحّل التحويل برقم {ltr((self.current_record or {}).get('transfer_no'))}.")

    def unpost_transfer(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = transfer_label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"حركات التحويل {label} (وقيده لو ليه) هتتمسح والتحويل هيرجع مسودة (برقمه) وتقدر "
                                   "تعدّله. تكمّل؟"):
            return
        try:
            self.service.unpost(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self._after_action()
        messages.info(self, "تم", f"التحويل {label} رجع مسودة — دوس «تعديل» عشان تعدّله.")

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
        self.record_saved.emit(self.current_id)

    # ---- القائمة والصفحات --------------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return self._from, self._to, self.status, keyword

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
        self._counts = self.service.count_transfers(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        rows = self.service.list_transfers(*query, page=self.page, page_size=PAGE_SIZE)
        for row in rows:
            kinds = "" if row["from_kind"] == row["to_kind"] else \
                f" ({KINDS[row['from_kind']]} ← {KINDS[row['to_kind']]})"
            row["route"] = f"{row['from_name']} ← {row['to_name']}{kinds}"
        return rows

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "transfer_no":
            return value or f"مسودة {row.get('draft_no')}"
        if key == "status":
            return "مرحّل" if value == "posted" else "مسودة"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col, route_col = cols.index("status"), cols.index("route")
        for r, row in enumerate(self.table.rows):
            for col, color in ((status_col, OK_COLOR if row.get("status") == "posted" else DRAFT_COLOR),
                               (route_col, ROUTE_TYPES.get(row.get("route_type"), ("", GREEN))[1])):
                item = self.table.item(r, col)
                if item is not None:
                    item.setForeground(QColor(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
        counts = self._counts
        if counts is not None:
            self.count_label.setText(f"{counts['total']:,} تحويل في الفترة · مسودة {counts['drafts']:,} · "
                                     f"مرحّل {counts['posted']:,}")
        self._update_paging(self.mode in EDITING)

    # ---- البيانات ----------------------------------------------------------
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
            note = f"🔒 السنة المالية {record['year_code']} مقفولة — التحويل ده مايتعدّلش ولا يتلغى ترحيله."
        elif record["status"] == "posted" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = f"✔ اترحّل بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}"
        self.year_note.setText(note)
        self.year_note.setStyleSheet(f"color:{'#5B21B6' if record.get('year_status') == 'closed' else MUTED}; "
                                     "font-weight:800;")
        self.year_note.setVisible(bool(note))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_transfer(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.number_edit.setText(record.get("transfer_no") or
                                     f"مسودة {record.get('draft_no')} — الرقم الرسمي بيتاخد مع الترحيل")
            self.date_edit.setDate(_qdate(record["transfer_date"]))
            self.description.setText(record.get("description") or "")
            self._show_status(record)
            self._fill_warehouses(record)
            self._set_lines(record.get("lines") or [])
        finally:
            self._filling = False
        self._changed()

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.description):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.year_note.hide()
            self.lines_table.setRowCount(0)
        finally:
            self._filling = False
        if hasattr(self, "checks_label"):
            self._clear_review()

    def collect(self) -> dict:
        return self._data()

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_transfer(int(record_id), expected_version=self.current_version)
