"""شاشة «بضاعة أول المدة» — النموذج 4 «زي الإكسل» (اختيار المستخدم 2026-11-23).

```
┌ [تعديل][حفظ][إلغاء][تحديث][استيراد من إكسل][نموذج فاضي][تصدير إكسل][خروج]                    (عرض) ┐
├ ┌ الفلتر ┐ المخزن ▾ · [فاتورة][بيان] · ☐ اللي عليها رصيد بس · 🔍 كود أو اسم…                         ┤
├ [أصناف عليها رصيد] [قيمة فاتورة] [قيمة بيان] [الإجمالي ← 1105]      الأرصدة الافتتاحية: مسودة …    ┤
│   # │ الكود │ الصنف │ الوحدة │ رصيد أول المدة │ السعر │ القيمة       ← كل الأصناف المخزنية بخانات      │
│ الكتابة في سطر فاضي بتعمل سطر جديد · مسح الرصيد بيشيل السطر · Tab / Enter للخانة اللي بعدها         │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```
* مستند واحد — «تعديل» بيفتح الخانات، وتغيير المخزن / النوع وانت بتعدّل بيحفظ اللي كتبته لحد «حفظ».
* الحفظ بيبعت **السطور اللي اتغيّرت بس** بالإصدار اللي الشاشة شافته (جهاز تاني غيّر نفس السطر ← تعارض).
* السعر صفر مسموح بتحذير · المخزن الوهمي كمية بس · بعد ترحيل «الأرصدة الافتتاحية» كله مقفول.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError
from app.services.opening_stock_service import KINDS, OpeningStockService, line_value, parse_cost, parse_qty
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen
from app.ui.common.theme import GREEN, toolbar_button_style

MUTED, BAD, WARN, LOCKED, OK_COLOR = "#64748B", "#B91C1C", "#B45309", "#5B21B6", "#087443"
C_NO, C_CODE, C_NAME, C_UNIT, C_QTY, C_COST, C_VALUE = range(7)
HEADERS = ["#", "الكود", "الصنف", "الوحدة", "رصيد أول المدة", "السعر", "القيمة"]
EDIT_COLS = (C_QTY, C_COST)
ZERO = Decimal("0")
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 14px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}")
KPI_CARDS = (("items", "أصناف عليها رصيد", GREEN), ("invoice_value", "قيمة «فاتورة»", "#2563EB"),
             ("statement_value", "قيمة «بيان»", "#7C3AED"), ("value", "الإجمالي", "#B45309"))
KPI_STYLE = ("QFrame#kpi {{ background:#FFFFFF; border:1px solid #E2E8F0; border-right:4px solid {color}; "
             "border-radius:7px; }}")


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def fmt_qty(value: Any) -> str:
    if value in (None, ""):
        return ""
    d = Decimal(str(value))
    if not d:
        return ""
    text = f"{d:,.6f}".rstrip("0").rstrip(".")
    return text


def fmt_money(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):,.2f}"


class _GridTable(QTableWidget):
    """Tab / Enter بينقلوا للخانة اللي بعدها اللي بتتكتب (الرصيد ← السعر ← السطر اللي بعده) زي الإكسل."""

    def _editable(self, r: int, c: int) -> bool:
        item = self.item(r, c)
        return (not self.isRowHidden(r)) and item is not None and bool(item.flags() & Qt.ItemIsEditable)

    def step(self, forward: bool = True) -> bool:
        cells = [(r, c) for r in range(self.rowCount()) for c in EDIT_COLS]
        if not cells:
            return False
        cur = (self.currentRow(), self.currentColumn())
        start = cells.index(cur) if cur in cells else -1
        order = cells[start + 1:] if forward else list(reversed(cells[:max(start, 0)]))
        for r, c in order:
            if self._editable(r, c):
                self.setCurrentCell(r, c)
                self.editItem(self.item(r, c))
                return True
        return False

    def keyPressEvent(self, event) -> None:  # noqa: ANN001 - Qt
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Backtab):
            forward = event.key() != Qt.Key_Backtab and not (event.modifiers() & Qt.ShiftModifier)
            if self.step(forward):
                return
        super().keyPressEvent(event)

    def closeEditor(self, editor, hint) -> None:  # noqa: ANN001 - Qt
        super().closeEditor(editor, QAbstractItemDelegate.NoHint
                            if hint in (QAbstractItemDelegate.EditNextItem, QAbstractItemDelegate.EditPreviousItem) else hint)
        if hint == QAbstractItemDelegate.EditNextItem:
            self.step(True)
        elif hint == QAbstractItemDelegate.EditPreviousItem:
            self.step(False)


class ImportReviewDialog(QDialog):
    """مراجعة ملف الإكسل: الصح والغلط (بالأحمر) والتحذيرات + «إضافة وتحديث» / «استبدال الكل»."""

    def __init__(self, reviewed: list[dict[str, Any]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle("📥  مراجعة ملف الإكسل — بضاعة أول المدة")
        self.resize(980, 600)
        self.reviewed = reviewed
        bad = sum(1 for r in reviewed if r["errors"])
        warn = sum(1 for r in reviewed if r["warnings"] and not r["errors"])
        col = QVBoxLayout(self)
        self.summary = QLabel(f"{len(reviewed) - bad} صف صح · {bad} غلط · {warn} تحذير")
        color = BAD if bad else OK_COLOR
        self.summary.setStyleSheet(f"color:{color}; font-weight:900;")
        col.addWidget(self.summary)
        heads = ["الصف", "الكود", "الصنف", "المخزن", "النوع", "الرصيد", "السعر", "القيمة", "الملاحظات"]
        self.table = QTableWidget(len(reviewed), len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        for r, row in enumerate(reviewed):
            notes = " · ".join(row["errors"] + row["warnings"])
            cells = [str(row["row"]), ltr(row["code"]), row["item_name"] or "", row["warehouse_name"] or "",
                     KINDS.get(row["stock_kind"], "—"), fmt_qty(row["qty"]),
                     "—" if row["is_virtual"] else fmt_money(row["unit_cost"]), fmt_money(row["value"]), notes]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if row["errors"]:
                    item.setBackground(QColor("#FEE2E2"))
                    item.setForeground(QColor(BAD))
                elif row["warnings"]:
                    item.setBackground(QColor("#FFF7ED"))
                self.table.setItem(r, c, item)
        col.addWidget(self.table, 1)
        self.merge = QRadioButton("إضافة وتحديث — الموجود يتعدّل والجديد يتضاف (الباقي زي ما هو)")
        self.replace = QRadioButton("استبدال الكل — كل بضاعة أول المدة تتمسح وتتكتب من الملف")
        self.merge.setChecked(True)
        col.addWidget(self.merge)
        col.addWidget(self.replace)
        if bad:
            hint = QLabel("فيه صفوف غلط (بالأحمر) — صلّحهم في الإكسل واستورد تاني (مفيش حاجة هتتحفظ).")
            hint.setStyleSheet(f"color:{BAD}; font-weight:800;")
            col.addWidget(hint)
        buttons = QHBoxLayout()
        self.ok_button = QPushButton("استيراد")
        self.ok_button.setStyleSheet(toolbar_button_style("save"))
        self.ok_button.setEnabled(not bad and bool(reviewed))
        self.ok_button.clicked.connect(self.accept)
        cancel = QPushButton("إلغاء")
        cancel.setStyleSheet(toolbar_button_style("cancel"))
        cancel.clicked.connect(self.reject)
        buttons.addStretch(1)
        buttons.addWidget(self.ok_button)
        buttons.addWidget(cancel)
        col.addLayout(buttons)

    @property
    def replace_all(self) -> bool:
        return self.replace.isChecked()


class OpeningStockScreen(BaseScreen):
    MODULE = "inv"
    TARGET = "opening_stock"
    TITLE = "بضاعة أول المدة"
    ICON = "📥"
    FORM_TITLE = "الفلتر"
    EXIT_LAST = True

    def __init__(self, service: OpeningStockService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or OpeningStockService()
        self.rows: list[dict[str, Any]] = []
        self._edits: dict[tuple[int, str, int], dict[str, Any]] = {}   # (المخزن، النوع، الصنف) ← التعديل
        self._filling = False
        self._warehouses: list[dict[str, Any]] = []
        super().__init__(parent)
        self.resize(1200, 760)
        self.load(1)

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        row = QHBoxLayout(box)
        row.setContentsMargins(14, 18, 14, 10)
        row.setSpacing(8)
        self.warehouse = QComboBox()
        self.warehouse.setMinimumWidth(200)
        self.warehouse.setToolTip("المخزن اللي بتكتب رصيده (الوهمي كمية بس)")
        self.warehouse.currentIndexChanged.connect(self._filter_changed)
        self.kind_group = QButtonGroup(self)
        self.kind_group.setExclusive(True)
        self.kind_buttons: dict[str, QPushButton] = {}
        row.addWidget(QLabel("المخزن"))
        row.addWidget(self.warehouse)
        row.addSpacing(10)
        row.addWidget(QLabel("النوع"))
        for code, name in KINDS.items():
            button = QPushButton(name)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(CHIP_STYLE)
            button.setChecked(code == "invoice")
            self.kind_group.addButton(button)
            self.kind_buttons[code] = button
            row.addWidget(button)
        self.kind_group.buttonToggled.connect(lambda _b, on: on and self._filter_changed())
        row.addSpacing(10)
        self.only_balance = QCheckBox("اللي عليها رصيد بس")
        self.only_balance.toggled.connect(self._apply_filter)
        row.addWidget(self.only_balance)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("🔍 كود أو اسم الصنف…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit, 1)

    def _build_kpis(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.kpi_values: dict[str, QLabel] = {}
        self.kpi_captions: dict[str, QLabel] = {}
        for key, title, color in KPI_CARDS:
            card = QFrame()
            card.setObjectName("kpi")
            card.setStyleSheet(KPI_STYLE.format(color=color))
            col = QVBoxLayout(card)
            col.setContentsMargins(10, 6, 10, 6)
            col.setSpacing(1)
            caption = QLabel(title)
            caption.setStyleSheet(f"color:{MUTED}; font-weight:800; border:none;")
            value = QLabel("—")
            value.setStyleSheet("font-size:15px; font-weight:900; border:none;")
            col.addWidget(caption)
            col.addWidget(value)
            row.addWidget(card, 1)
            self.kpi_values[key] = value
            self.kpi_captions[key] = caption
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(300)
        row.addWidget(self.status_label, 2)
        return bar

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        column = QVBoxLayout()
        column.setSpacing(8)
        column.addWidget(form_box)
        column.addWidget(self._build_kpis())
        self.grid = _GridTable(0, len(HEADERS))
        self.grid.setHorizontalHeaderLabels(HEADERS)
        self.grid.verticalHeader().setVisible(False)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.grid.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.grid.setAlternatingRowColors(True)
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        for c, width in ((C_NO, 55), (C_CODE, 105), (C_UNIT, 90), (C_QTY, 140), (C_COST, 120), (C_VALUE, 140)):
            self.grid.setColumnWidth(c, width)
        self.grid.itemChanged.connect(self._on_item_changed)
        column.addWidget(self.grid, 1)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        column.addWidget(self.hint)
        content.addLayout(column, 1)
        list_box.setParent(self)                                 # مفيش قائمة سجلات (مستند واحد)
        list_box.hide()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.import_button = self._button("استيراد من إكسل", "primary", self.import_excel)
        self.import_button.setToolTip("الأعمدة: كود الصنف · المخزن (كود أو اسم) · فاتورة / بيان · الرصيد · السعر — "
                                      "المراجعة بتعلّم الغلط بالأحمر قبل ما حاجة تتحفظ")
        self.template_button = self._button("نموذج فاضي", "refresh", self.download_template)
        self.template_button.setToolTip("ملف إكسل فاضي بالأعمدة المطلوبة")
        self.import_button.setVisible(self.has_action("import"))
        self.template_button.setVisible(self.has_action("import"))
        layout.addWidget(self.import_button)
        layout.addWidget(self.template_button)

    # ---- الفلتر والجدول ---------------------------------------------------------
    def _load_warehouses(self) -> None:
        keep = self.warehouse.currentData()
        try:
            self._warehouses = self.service.warehouses()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل المخازن", exc)
            self._warehouses = []
        self.warehouse.blockSignals(True)
        self.warehouse.clear()
        for w in self._warehouses:
            if not w["is_active"] and w["id"] != keep:
                continue
            tag = " (وهمي)" if w["is_virtual"] else (" (موقوف)" if not w["is_active"] else "")
            self.warehouse.addItem(f"{w['code']} — {w['name_ar']}{tag}", w["id"])
        if keep is None:
            keep = next((w["id"] for w in self._warehouses if w["is_default"]), None)
        index = self.warehouse.findData(keep)
        self.warehouse.setCurrentIndex(index if index >= 0 else 0)
        self.warehouse.blockSignals(False)

    @property
    def warehouse_id(self) -> int | None:
        return self.warehouse.currentData()

    @property
    def stock_kind(self) -> str:
        return next((k for k, b in self.kind_buttons.items() if b.isChecked()), "invoice")

    def set_filter(self, warehouse_id: int | None = None, stock_kind: str | None = None) -> None:
        """تغيير المخزن / النوع (زي ما المستخدم بيختار) — التعديلات بتفضل لحد «حفظ»."""
        if warehouse_id is not None:
            self.warehouse.setCurrentIndex(self.warehouse.findData(warehouse_id))
        if stock_kind is not None and stock_kind != self.stock_kind:
            self.kind_buttons[stock_kind].setChecked(True)

    def _virtual(self) -> bool:
        return any(w["id"] == self.warehouse_id and w["is_virtual"] for w in self._warehouses)

    def _filter_changed(self, *_args: Any) -> None:
        if self.warehouse_id is None:
            self.rows = []
        else:
            try:
                self.rows = self.service.grid(self.warehouse_id, self.stock_kind)
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر تحميل الأصناف", exc)
                self.rows = []
        self._fill_grid()

    def _key(self, row: dict[str, Any]) -> tuple[int, str, int]:
        return (int(row["warehouse_id"]), row["stock_kind"], int(row["item_id"]))

    def _fill_grid(self) -> None:
        self._filling = True
        grid = self.grid
        grid.setRowCount(0)
        editing = self.mode in EDITING and not self._locked()
        virtual = self._virtual()
        for r, row in enumerate(self.rows):
            grid.insertRow(r)
            edit = self._edits.get(self._key(row))
            qty = edit["qty"] if edit else fmt_qty(row["qty"])
            cost = edit["unit_cost"] if edit else ("" if row["line_id"] is None else fmt_qty(row["unit_cost"]))
            name = row["name"] + ("" if row["is_active"] else " (موقوف)")
            cells = [(C_NO, str(r + 1)), (C_CODE, ltr(row["code"])), (C_NAME, name), (C_UNIT, row["unit_name"] or ""),
                     (C_QTY, qty), (C_COST, "—" if virtual else cost), (C_VALUE, "")]
            for c, text in cells:
                item = QTableWidgetItem(text)
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                if c in (C_QTY, C_COST, C_VALUE):
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
                if editing and (c == C_QTY or (c == C_COST and not virtual)):
                    flags |= Qt.ItemIsEditable
                    item.setBackground(QColor("#FFFBEB"))
                item.setFlags(flags)
                if not row["is_active"]:
                    item.setForeground(QColor("#94A3B8"))
                grid.setItem(r, c, item)
            self._update_value(r)
        self._filling = False
        self._apply_filter()
        self._update_hint()

    def _update_value(self, r: int) -> None:
        qty_text, cost_text = self.grid.item(r, C_QTY).text(), self.grid.item(r, C_COST).text()
        cell = self.grid.item(r, C_VALUE)
        try:
            qty = parse_qty(qty_text)
            cost = parse_cost("" if self._virtual() else cost_text)
        except BusinessError:
            cell.setText("✖ رقم غلط")
            cell.setForeground(QColor(BAD))
            return
        cell.setForeground(QColor("#0F172A"))
        if qty is None:
            cell.setText("")
        elif self._virtual():
            cell.setText("كمية بس")
            cell.setForeground(QColor(MUTED))
        else:
            cell.setText(fmt_money(line_value(qty, cost)))
            if not cost:
                cell.setText("0.00 ⚠ سعر صفر")
                cell.setForeground(QColor(WARN))

    def _apply_filter(self, *_args: Any) -> None:
        text = self.filter_edit.text().strip().lower()
        only = self.only_balance.isChecked()
        for r, row in enumerate(self.rows):
            hit = not text or text in str(row["code"]).lower() or text in str(row["name"]).lower()
            if hit and only:
                hit = bool(self.grid.item(r, C_QTY).text().strip())
            self.grid.setRowHidden(r, not hit)

    def set_cell(self, item_id: int, qty: str | None = None, cost: str | None = None) -> None:
        """كتابة في خانة صنف (زي ما المستخدم بيكتب)."""
        r = next(i for i, row in enumerate(self.rows) if row["item_id"] == item_id)
        if qty is not None:
            self.grid.item(r, C_QTY).setText(qty)
        if cost is not None:
            self.grid.item(r, C_COST).setText(cost)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling or item.column() not in EDIT_COLS:
            return
        r = item.row()
        row = self.rows[r]
        self._edits[self._key(row)] = {
            "item_id": row["item_id"], "warehouse_id": row["warehouse_id"], "stock_kind": row["stock_kind"],
            "line_id": row["line_id"], "row_version": row["row_version"] or 0,
            "qty": self.grid.item(r, C_QTY).text(),
            "unit_cost": "" if self._virtual() else self.grid.item(r, C_COST).text(),
            "orig_qty": row["qty"], "orig_cost": row["unit_cost"], "is_virtual": self._virtual(),
            "label": f"{row['code']} «{row['name']}»"}
        self._filling = True
        self._update_value(r)
        self._filling = False
        self._update_hint()

    def _pending(self) -> list[dict[str, Any]]:
        """التعديلات اللي فعلاً اتغيّرت (والأرقام سليمة — وإلا BusinessError)."""
        out = []
        for e in self._edits.values():
            qty = parse_qty(e["qty"], f"رصيد {e['label']}")
            cost = ZERO if e["is_virtual"] else parse_cost(e["unit_cost"], f"سعر {e['label']}")
            old_qty = None if e["line_id"] is None else Decimal(e["orig_qty"])
            old_cost = ZERO if e["line_id"] is None or e["is_virtual"] else Decimal(e["orig_cost"])
            if (qty, cost if qty is not None else ZERO) == (old_qty, old_cost if old_qty is not None else ZERO):
                continue
            out.append({**e, "qty": qty, "unit_cost": cost})
        return out

    def _update_hint(self) -> None:
        if not hasattr(self, "hint"):
            return
        base = ("الكتابة في سطر فاضي بتعمل سطر جديد · مسح الرصيد (أو صفر) بيشيل السطر · Tab / Enter للخانة اللي بعدها · "
                "السعر بالوحدة الأساسية")
        if self.mode in EDITING:
            try:
                n = len(self._pending())
                base += f" · {n} تعديل لسه ماتحفظش (بيفضلوا لو غيّرت المخزن أو النوع)" if n else ""
            except BusinessError:
                base += " · ✖ فيه رقم مكتوب غلط"
        if self._virtual():
            base += " · المخزن الوهمي كمية بس (مالوش قيمة)."
        self.hint.setText(base)

    # ---- الحالة والأوضاع ------------------------------------------------------------
    def _locked(self) -> bool:
        return bool((self.current_record or {}).get("locked"))

    def editable_widgets(self) -> list[QWidget]:
        return []

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        self.edit_button.setEnabled(self.edit_button.isEnabled() and not self._locked())
        if hasattr(self, "grid") and not self._filling:
            self._fill_grid()

    def update_extra_buttons(self) -> None:
        idle = self.mode not in EDITING
        self.import_button.setEnabled(idle and self.can("import") and self.can("save") and not self._locked())
        self.template_button.setEnabled(idle and self.can("import"))

    def edit_record(self) -> None:
        if self._locked():
            messages.warn(self, "تعديل", "الأرصدة الافتتاحية مترحّلة — بضاعة أول المدة مقفولة "
                                         "(الغي ترحيل الأرصدة الافتتاحية الأول).")
            return
        super().edit_record()
        if self.mode == "edit" and self._locked():                # جهاز تاني رحّل في الوقت ده
            self.load(1)
            messages.warn(self, "تعديل", "الأرصدة الافتتاحية اترحّلت من جهاز تاني — بضاعة أول المدة مقفولة.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return []

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.summary()

    def fill_form(self, record: dict) -> None:
        self._edits = {}
        self._load_warehouses()
        self.kpi_values["items"].setText(f"{int(record['items']):,}  ·  {int(record['lines']):,} سطر")
        for key in ("invoice_value", "statement_value", "value"):
            self.kpi_values[key].setText(ltr(fmt_money(record[key])))
        account = record.get("inventory_account") or {}
        self.kpi_captions["value"].setText(f"الإجمالي ← {account.get('code', '1105')} {account.get('name_ar', 'المخزون')}")
        when = record["balance_date"].strftime("%d/%m/%Y") if record.get("balance_date") else "—"
        if record["locked"]:
            text = (f"✔ الأرصدة الافتتاحية مترحّلة ({ltr(when)}) — بضاعة أول المدة دخلت دفتر المخزون ومقفولة "
                    "لحد «إلغاء الترحيل».")
            color = LOCKED
        else:
            text = (f"الأرصدة الافتتاحية: مسودة بتاريخ {ltr(when)} — بضاعة أول المدة بتترحّل معاها "
                    "(القيمة على حساب المخزون والفرق على 34).")
            color = MUTED
        if record.get("zero_cost"):
            text += f"\n⚠ {record['zero_cost']} سطر بسعر صفر."
            color = WARN if not record["locked"] else color
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color:{color}; font-weight:800;")
        self._filter_changed()

    def clear_form(self) -> None:
        self._edits = {}
        self.grid.setRowCount(0)
        self.rows = []

    def collect(self) -> dict:
        return {"changes": self._pending()}

    def save(self) -> None:
        if self.mode in EDITING and self.save_button.isEnabled():
            try:
                pending = self._pending()
            except BusinessError as exc:
                messages.show_error(self, "تعذّر الحفظ", exc)
                return
            zero = [p for p in pending if p["qty"] is not None and not p["is_virtual"] and not p["unit_cost"]]
            if zero and not messages.ask_yes_no(
                    self, "سعر صفر", f"فيه {len(zero)} صنف رصيده بسعر صفر (مالوش قيمة في المخزون) — تكمّل الحفظ؟"):
                return
        super().save()

    def save_record(self, payload: dict, record_id: Any) -> Any:
        clean = [{k: v for k, v in c.items() if k not in ("orig_qty", "orig_cost", "is_virtual")}
                 for c in payload["changes"]]
        self.service.save(clean)
        return 1

    def reload_after_conflict(self) -> None:
        self.mode = "view"
        self.load(1)

    # ---- الإكسل ---------------------------------------------------------------------
    def review_dialog(self, reviewed: list[dict[str, Any]]) -> tuple[bool, bool]:
        """نافذة المراجعة ← (استيراد؟، استبدال الكل؟) — الاختبارات بتبدّلها."""
        dialog = ImportReviewDialog(reviewed, self)
        return bool(dialog.exec()), dialog.replace_all

    def import_excel(self, path: str | None = None) -> None:
        if not self.import_button.isEnabled():
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "استيراد من إكسل", "", "Excel (*.xlsx)")
            if not path:
                return
        try:
            reviewed = self.service.review(self.service.read_excel(path))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر قراية الملف", exc)
            return
        ok, replace = self.review_dialog(reviewed)
        if not ok:
            return
        if replace and not messages.ask_yes_no(self, "استبدال الكل",
                                               "كل بضاعة أول المدة الحالية هتتمسح وتتكتب من الملف. تكمّل؟"):
            return
        try:
            counts = self.service.import_rows(reviewed, replace=replace)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر الاستيراد", exc)
            return
        self.load(1)
        self.record_saved.emit(1)
        text = f"اتضاف {counts['created']} · اتعدّل {counts['updated']}"
        if replace:
            text += f" · اتمسح {counts['deleted']} (قبل الاستبدال)"
        messages.info(self, "تم الاستيراد", text + ".")

    def download_template(self, path: str | None = None) -> None:
        if not self.can("import"):
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "نموذج فاضي", "نموذج_بضاعة_أول_المدة.xlsx", "Excel (*.xlsx)")
            if not path:
                return
        try:
            self.service.write_template(Path(path))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر حفظ النموذج", exc)
            return
        messages.info(self, "نموذج فاضي", "اتحفظ النموذج — املاه وبعدين «استيراد من إكسل».")

    def export_excel(self, path: str | None = None) -> None:
        if not self.can("export"):
            return
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل", f"{self.TITLE}.xlsx", "Excel (*.xlsx)")
            if not path:
                return
        try:
            n = self.service.export(Path(path))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", f"اتصدّر {n} سطر (بنفس أعمدة الاستيراد).")

