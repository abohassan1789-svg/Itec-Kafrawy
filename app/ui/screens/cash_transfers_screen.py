"""شاشة «حركات الخزائن والبنوك» — النموذج 2 «تبويب لكل نوع حركة» (اختيار المستخدم 2026-11-17).

```
┌ [حركة جديدة][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][تصدير إكسل][خروج]       (عرض) ┐
│ [📋 الكل (9)] [📥 إيداع في البنك (4)] [📤 سحب من البنك (2)] [💵 بين خزنتين (2)] [🏦 بين بنكين (1)]     │
├━━━━━━━━━━━━━━━━━━━━━━━━━━━ (خط بلون التبويب) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┤
│ ┌ حركة ┐ الرقم · التاريخ · الحالة · النوع               │ ┌ الحركات ┐ 🔍 رقم / خزنة / بنك / بيان   │
│   من *  [خزنة/بنك] [..]        إلى * [خزنة/بنك] [..]     │   من [..] إلى [..]   [الكل][مسودة][مرحّل] │
│   المبلغ · مصاريف بنكية · رقم الإيصال · البيان          │   الرقم│التاريخ│النوع│من ← إلى│المبلغ│الحالة │
│ [المبلغ] [رصيد «من» ← بعد] [رصيد «إلى» ← بعد]           │                                        │
│ ⚠️ «من» مش مكفي (تحذير بس)                               │                                        │
│ ┌ 📒 القيد اللي هيتعمل ┐ الحساب│مدين│دائن  ✔ متوازن      │   › السابق  صفحة 1 من 3  التالي ‹         │
└────────────────────────────────────────────────────────┴────────────────────────────────────────┘
```
* التبويب بيفلتر القائمة — ووانت بتكتب حركة بيجهّز «من» و«إلى» المناسبين (والنوع بيطلع لوحده من «من» و«إلى»).
* «حفظ مسودة» · «ترحيل» (وانت بتعدّل = حفظ + ترحيل) · «إلغاء الترحيل» بالصلاحية — زي السندات (رد المستخدم).
* الرصيد مش مكفي ← تحذير بس (رد المستخدم) · مفيش «إضافة أموال للخزينة» (رد المستخدم) · الطباعة بعدين.
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
    QFrame,
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

from app.services.cash_transfer_service import (
    PAGE_SIZE,
    TRANSFER_TYPES,
    CashTransferService,
    transfer_label,
    type_of,
)
from app.services.errors import BusinessError, ConcurrentEditError
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, STATUS_BADGES, ltr, money
from app.ui.screens.vouchers_screen import (
    BAD,
    BLUE,
    DRAFT_COLOR,
    KPI_STYLE,
    MUTED,
    OK_COLOR,
    VALUE_STYLE,
    WARN_STYLE,
    _pydate,
    _qdate,
    signed,
)

GREEN = "#137A38"
ALL_TAB = "all"
TYPE_ICONS = {"deposit": "📥", "withdraw": "📤", "t2t": "💵", "b2b": "🏦"}
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
KIND_ITEMS = (("💵 خزنة", "treasury"), ("🏦 بنك", "bank"))


def type_color(key: str) -> str:
    return TRANSFER_TYPES[key][1] if key in TRANSFER_TYPES else GREEN


class CashTransfersScreen(BaseScreen):
    MODULE = "cash"
    TARGET = "cash_transfers"
    TITLE = "حركات الخزائن والبنوك"
    ICON = "🔁"
    FORM_TITLE = "حركة"
    NEW_TEXT = "حركة جديدة"
    LIST_TITLE = "الحركات"
    SEARCH_PLACEHOLDER = "🔍 رقم الحركة أو الخزنة أو البنك أو البيان… (F1)"
    COLUMNS = [("transfer_no", "رقم الحركة"), ("transfer_date", "التاريخ"), ("transfer_type", "النوع"),
               ("route", "من ← إلى"), ("amount", "المبلغ"), ("status", "الحالة")]
    FORM_STRETCH, LIST_STRETCH = 12, 11
    EXIT_LAST = True

    def __init__(self, service: CashTransferService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or CashTransferService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self.list_type: str | None = None             # تبويب القائمة (None = الكل)
        self._last_query: tuple | None = None
        self._choices: dict[str, Any] = {"treasuries": [], "banks": []}
        self._counts: dict[str, Any] | None = None
        self._filling = False
        today = dt.date.today()
        self._from = today.replace(day=1)
        self._to = (self._from + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_side)
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.count_label.parentWidget().layout().addLayout(self.paging)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def _end_row(self, side: str) -> QHBoxLayout:
        combo = QComboBox()
        for text, value in KIND_ITEMS:
            combo.addItem(text, value)
        edit = LookupEdit("اختار الخزنة… *")
        edit.value_changed.connect(self._changed)
        combo.currentIndexChanged.connect(lambda _i, s=side: self._on_kind(s))
        setattr(self, f"{side}_kind", combo)
        setattr(self, f"{side}_edit", edit)
        row = QHBoxLayout()
        row.addWidget(combo)
        row.addWidget(edit, 1)
        return row

    def build_form(self, box: QGroupBox) -> None:
        self.form_box = box
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (CT-2026-00001) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setLayoutDirection(Qt.LeftToRight)
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.status_label = QLabel("")
        top = QHBoxLayout()
        top.addWidget(self.number_edit, 2)
        top.addWidget(QLabel("التاريخ *"))
        top.addWidget(self.date_edit, 1)
        top.addWidget(QLabel("الحالة"))
        top.addWidget(self.status_label)
        form.addRow("رقم الحركة", top)

        self.type_label = QLabel("")
        self.type_label.setToolTip("النوع بيطلع لوحده من «من» و«إلى»")
        form.addRow("النوع", self.type_label)
        form.addRow("من *", self._end_row("from"))
        form.addRow("إلى *", self._end_row("to"))

        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        self.amount_edit.textChanged.connect(self._changed)
        self.fees_edit = QLineEdit()
        self.fees_edit.setLayoutDirection(Qt.LeftToRight)
        self.fees_edit.setPlaceholderText("0.00 (اختياري)")
        self.fees_edit.setToolTip("عمولة التحويل / رسوم الإيداع — بتخرج من «من» زيادة وبتروح على «مصاريف بنكية»")
        self.fees_edit.textChanged.connect(self._changed)
        amounts = QHBoxLayout()
        amounts.addWidget(self.amount_edit, 1)
        amounts.addWidget(QLabel("مصاريف بنكية"))
        amounts.addWidget(self.fees_edit, 1)
        form.addRow("المبلغ *", amounts)
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("الحركة دي عن إيه (زي: إيداع نقدية الخزينة في البنك)")
        form.addRow("البيان *", self.description)
        self.reference_no = QLineEdit()
        self.reference_no.setMaxLength(40)
        self.reference_no.setPlaceholderText("إيصال البنك / رقم التحويل (اختياري)")
        form.addRow("رقم الإيصال", self.reference_no)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        form.addRow(self.year_note)
        self._paint_form(GREEN)

    def _paint_form(self, color: str) -> None:
        self.form_box.setStyleSheet(f"QGroupBox#formBox {{ border-top:3px solid {color}; }}"
                                    f"QGroupBox#formBox::title {{ color:{color}; }}")

    def _build_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_tabs: dict[str, QPushButton] = {}
        for key in (ALL_TAB, *TRANSFER_TYPES):
            color = type_color(key)
            button = QPushButton("")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(36)
            button.setStyleSheet(
                f"QPushButton {{ background:#FFFFFF; color:{color}; border:1px solid #E2E8F0; "
                f"border-top:3px solid {color}; border-radius:6px; padding:5px 14px; font-weight:900; }}"
                "QPushButton:hover { background:#F8FAFC; }"
                f"QPushButton:checked {{ background:{color}; color:#FFFFFF; border:1px solid {color}; }}"
                "QPushButton:disabled { color:#94A3B8; border-top-color:#CBD5E1; }")
            button.clicked.connect(lambda _checked=False, k=key: self.show_type(k))
            self.type_group.addButton(button)
            self.type_tabs[key] = button
            row.addWidget(button)
        row.addStretch(1)
        self._update_tabs()
        return row

    def _build_kpis(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.kpi_values: dict[str, QLabel] = {}
        self.kpi_captions: dict[str, QLabel] = {}
        cards = (("amount", "المبلغ (+ المصاريف)", BLUE), ("from", "رصيد «من» ← بعد الحركة", BAD),
                 ("to", "رصيد «إلى» ← بعد الحركة", OK_COLOR))
        for key, title, color in cards:
            card = QFrame()
            card.setObjectName("kpi")
            card.setStyleSheet(KPI_STYLE.format(color=color))
            col = QVBoxLayout(card)
            col.setContentsMargins(10, 6, 10, 6)
            col.setSpacing(1)
            caption = QLabel(title)
            caption.setStyleSheet(f"color:{MUTED}; font-weight:800; border:none;")
            value = QLabel("—")
            value.setStyleSheet(VALUE_STYLE)
            col.addWidget(caption)
            col.addWidget(value)
            row.addWidget(card, 1 if key == "amount" else 2)
            self.kpi_values[key] = value
            self.kpi_captions[key] = caption
        return bar

    def _build_entry(self) -> QGroupBox:
        self.entry_box = QGroupBox("القيد اللي هيتعمل لما يترحّل")
        col = QVBoxLayout(self.entry_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.entry_table = QTableWidget(0, 3)
        self.entry_table.setHorizontalHeaderLabels(["الحساب", "مدين", "دائن"])
        self.entry_table.verticalHeader().setVisible(False)
        self.entry_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.entry_table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.entry_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            self.entry_table.setColumnWidth(c, 130)
        self.entry_table.setMinimumHeight(110)
        self.entry_table.setMaximumHeight(170)
        col.addWidget(self.entry_table)
        self.entry_label = QLabel("")
        self.entry_label.setWordWrap(True)
        col.addWidget(self.entry_label)
        return self.entry_box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        """التبويبات فوق الاتنين (النموذج 2) وتحتها خط بلون التبويب، والفورم يمين والقائمة شمال."""
        outer = QWidget()
        col = QVBoxLayout(outer)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addLayout(self._build_tabs())
        self.tab_line = QFrame()
        self.tab_line.setFixedHeight(3)
        col.addWidget(self.tab_line)
        col.addSpacing(6)
        body = QHBoxLayout()
        body.setSpacing(10)
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        right.addWidget(self._build_kpis())
        self.funds_label = QLabel("")
        self.funds_label.setWordWrap(True)
        self.funds_label.setStyleSheet(WARN_STYLE)
        self.funds_label.hide()
        right.addWidget(self.funds_label)
        right.addWidget(self._build_entry())
        right.addStretch(1)
        body.addWidget(holder, self.FORM_STRETCH)                 # RTL: أول عنصر = يمين
        body.addWidget(list_box, self.LIST_STRETCH)
        col.addLayout(body, 1)
        content.addWidget(outer)
        self._load_choices()
        self._update_tabs()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_transfer)
        self.post_button.setToolTip("بيعمل القيد ويرحّله وبياخد الرقم الرسمي — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_transfer)
        self.unpost_button.setToolTip("القيد بيتمسح والحركة بترجع مسودة برقمها وتتعدّل (السنة لازم مفتوحة)")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        period = QHBoxLayout()
        self.period_from, self.period_to = QDateEdit(), QDateEdit()       # (from_edit / to_edit = «من» و«إلى»)
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

    # ---- التبويبات -------------------------------------------------------------
    def _form_type(self) -> str:
        return type_of(self.from_kind.currentData(), self.to_kind.currentData())

    def _active_tab(self) -> str:
        """وانت بتكتب = نوع الحركة نفسها · غير كده = فلتر القائمة."""
        if self.mode in EDITING and hasattr(self, "from_kind"):
            return self._form_type()
        return self.list_type or ALL_TAB

    def _update_tabs(self) -> None:
        if not hasattr(self, "type_tabs"):
            return
        counts = (self._counts or {}).get("types") or {}
        total = (self._counts or {}).get("all", 0)
        editing = self.mode in EDITING
        active = self._active_tab()
        for key, button in self.type_tabs.items():
            if key == ALL_TAB:
                button.setText(f"📋 الكل ({ltr(total)})")
                button.setEnabled(not editing)
            else:
                button.setText(f"{TYPE_ICONS[key]} {TRANSFER_TYPES[key][0]} ({ltr(counts.get(key, 0))})")
            button.setChecked(key == active)
        color = type_color(active)
        if hasattr(self, "tab_line"):
            self.tab_line.setStyleSheet(f"background:{color}; border:none;")
        if hasattr(self, "form_box"):
            self._paint_form(type_color(self._form_type()) if (editing or self.current_record) else GREEN)
        if hasattr(self, "type_label"):
            key = self._form_type()
            self.type_label.setText(f"{TYPE_ICONS[key]} {TRANSFER_TYPES[key][0]}")
            self.type_label.setStyleSheet(BADGE.format(bg=type_color(key), fg="#FFFFFF", bd=type_color(key)))

    def show_type(self, key: str) -> None:
        """التبويب: وانت بتكتب ← يجهّز «من» و«إلى» للنوع ده · غير كده ← يفلتر القائمة."""
        if self.mode in EDITING:
            if key in TRANSFER_TYPES:
                _name, _color, from_kind, to_kind = TRANSFER_TYPES[key]
                self._set_kinds(from_kind, to_kind)
            self._update_tabs()
            return
        self.list_type = None if key == ALL_TAB else key
        self._after_filter()
        self._update_tabs()

    def _set_kinds(self, from_kind: str, to_kind: str) -> None:
        """يحط «من» و«إلى» على أول خزنة / حساب بنكي (و«إلى» غير «من»)."""
        self._filling = True
        try:
            for side, kind in (("from", from_kind), ("to", to_kind)):
                getattr(self, f"{side}_kind").setCurrentIndex(0 if kind == "treasury" else 1)
                self._fill_end(side, kind)
            from_rows = self._end_rows(from_kind)
            if from_rows:
                self.from_edit.set_value(from_rows[0][0])
            self._default_to()
        finally:
            self._filling = False
        self._changed()

    def _default_to(self) -> None:
        kind = self.to_kind.currentData()
        taken = self.from_edit.currentData() if self.from_kind.currentData() == kind else None
        rows = [r for r in self._end_rows(kind) if r[0] != taken]
        if rows:
            self.to_edit.set_value(rows[0][0])

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «حركة جديدة» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"treasuries": [], "banks": []}

    def _end_rows(self, kind: str) -> list[tuple[int, str, str]]:
        if kind == "treasury":
            return [(t["id"], t["code"], f"{t['code']} — {t['name_ar']}") for t in self._choices["treasuries"]
                    if t["is_active"] and t["is_base_currency"]]
        return [(b["id"], b["code"], f"{b['bank_name']} — {b['code']}" +
                 (f" ({b['account_number']})" if b.get("account_number") else ""))
                for b in self._choices["banks"] if b["is_active"] and b["is_base_currency"]]

    def _fill_end(self, side: str, kind: str, rec: dict | None = None) -> None:
        rec = rec or {}
        value = rec.get(f"{side}_treasury_id") if kind == "treasury" else rec.get(f"{side}_bank_account_id")
        text = f"{rec.get(f'{side}_code', '')} — {rec.get(f'{side}_name', '')}"
        placeholder = "اختار الخزنة… *" if kind == "treasury" else "اختار الحساب البنكي… *"
        getattr(self, f"{side}_edit").set_items(self._end_rows(kind), value, text, placeholder=placeholder)

    def _set_lookups(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
            for side in ("from", "to"):
                kind = rec.get(f"{side}_kind") or ("treasury" if side == "from" else "bank")
                getattr(self, f"{side}_kind").setCurrentIndex(0 if kind == "treasury" else 1)
                self._fill_end(side, kind, rec)
        finally:
            self._filling = False
        self._update_tabs()

    def _on_kind(self, side: str) -> None:
        if self._filling:
            return
        kind = getattr(self, f"{side}_kind").currentData()
        self._fill_end(side, kind)
        if self.mode in EDITING:
            if side == "to":
                self._default_to()
            else:
                rows = self._end_rows(kind)
                if rows:
                    self.from_edit.set_value(rows[0][0])
        self._update_tabs()
        self._changed()

    # ---- الكروت ومعاينة القيد -------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        out: dict[str, Any] = {"transfer_date": _pydate(self.date_edit.date()), "amount": self.amount_edit.text(),
                               "fees": self.fees_edit.text(), "reference_no": self.reference_no.text(),
                               "description": self.description.text() or TRANSFER_TYPES[self._form_type()][0]}
        for side in ("from", "to"):
            kind = getattr(self, f"{side}_kind").currentData()
            value = getattr(self, f"{side}_edit").currentData()
            out.update({f"{side}_kind": kind, f"{side}_treasury_id": value if kind == "treasury" else None,
                        f"{side}_bank_account_id": value if kind == "bank" else None})
        return out

    def _refresh_side(self) -> None:
        """الكروت (المبلغ · رصيد «من» و«إلى» قبل وبعد) + تحذير الرصيد + القيد."""
        self._timer.stop()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        self._update_tabs()
        if not editing and not rec:
            self._clear_side()
            return
        data = self._data()
        posted = not editing and rec.get("status") == "posted"
        when = "دلوقتي (الحركة جوّاه)" if posted else "← بعد الحركة"
        self.kpi_captions["from"].setText(f"رصيد «من» {when}")
        self.kpi_captions["to"].setText(f"رصيد «إلى» {when}")
        try:
            b = self.service.balances(data["from_kind"], data["from_treasury_id"] or data["from_bank_account_id"],
                                      data["to_kind"], data["to_treasury_id"] or data["to_bank_account_id"],
                                      data["amount"], data["fees"], posted=posted)
        except Exception:  # noqa: BLE001 - الكروت للعرض بس
            b = None
        if b is None:
            self._clear_side(keep_entry=True)
        else:
            amount = b["amount"]
            text = ltr(money(amount)) if amount else "—"
            if b["fees"]:
                text += f"  + {ltr(money(b['fees']))}"
            self.kpi_values["amount"].setText(text)

            def pair(before: Decimal | None, after: Decimal | None) -> str:
                if before is None:
                    return "—"
                if posted:
                    return signed(before)
                return f"{signed(before)}  ←  {signed(after)}"

            for side in ("from", "to"):
                self.kpi_values[side].setText(pair(b[side], b[f"{side}_after"]))
                low = b[f"{side}_after"] is not None and b[f"{side}_after"] < 0
                self.kpi_values[side].setStyleSheet(
                    f"{VALUE_STYLE} color:{BAD if low else OK_COLOR};" if b[side] is not None else VALUE_STYLE)
            short = not posted and b["from"] is not None and b["from_available"] < 0 and amount > 0
            if short:
                what = "الخزنة" if data["from_kind"] == "treasury" else "الحساب البنكي"
                self.funds_label.setText(f"⚠️ رصيد {what} «{b['from_name']}» مش هيكفي ({ltr(money(b['from']))}) — "
                                         "تقدر ترحّل بس هيجيلك تحذير.")
            self.funds_label.setVisible(short)
        self._refresh_entry(data, posted)

    def _clear_side(self, keep_entry: bool = False) -> None:
        for key in ("amount", "from", "to"):
            self.kpi_values[key].setText("—")
            self.kpi_values[key].setStyleSheet(VALUE_STYLE)
        self.funds_label.hide()
        if not keep_entry:
            self.entry_table.setRowCount(0)
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")

    def _refresh_entry(self, data: dict[str, Any], posted: bool) -> None:
        rows: list[dict[str, Any]] = []
        error = ""
        if posted:
            self.entry_box.setTitle("القيد المرحّل")
            entry = self.service.entry_for(int(self.current_id)) if self.current_id is not None else None
            for line in (entry or {}).get("lines") or []:
                rows.append({"account": f"{line['account_code']} {line['account_name']}",
                             "debit": line["debit"], "credit": line["credit"]})
        else:
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")
            try:
                for line in self.service.preview(data):
                    rows.append({"account": f"{line['account_code']} {line['account_name']}",
                                 "debit": line["debit"], "credit": line["credit"]})
            except BusinessError as exc:
                error = str(exc)
            except Exception:  # noqa: BLE001
                error = "مش قادر أجهّز القيد دلوقتي."
        self.entry_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.entry_table.setItem(r, 0, QTableWidgetItem(row["account"]))
            for c, key in ((1, "debit"), (2, "credit")):
                item = QTableWidgetItem(money(row[key]) if row[key] else "")
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
                self.entry_table.setItem(r, c, item)
        if error:
            self.entry_label.setText(f"✖ {error}")
            self._paint_entry(False)
        elif rows:
            d = sum((Decimal(r["debit"]) for r in rows), Decimal("0"))
            c = sum((Decimal(r["credit"]) for r in rows), Decimal("0"))
            prefix = f"{ltr(transfer_label(self.current_record or {}))} · " if posted else ""
            self.entry_label.setText(f"✔ {prefix}متوازن · مدين {ltr(money(d))} = دائن {ltr(money(c))}")
            self._paint_entry(d == c)
        else:
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")

    def _paint_entry(self, ok: bool) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.entry_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                       "padding:6px 10px; font-weight:900;")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.from_kind, self.from_edit, self.to_kind, self.to_edit, self.amount_edit,
                self.fees_edit, self.description, self.reference_no]

    def _is_editable_record(self) -> bool:
        rec = self.current_record or {}
        return rec.get("status") == "draft" and rec.get("year_status") != "closed"

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
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
        self._update_tabs()
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
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.number_edit.setText("بيتاخد مع الترحيل")
        self._show_status(None)
        self._set_lookups()
        key = self.list_type or "deposit"                     # التبويب المفتوح بيحدد النوع (الكل ← إيداع)
        _name, _color, from_kind, to_kind = TRANSFER_TYPES[key]
        self.set_mode("new")
        self._set_kinds(from_kind, to_kind)
        self._update_tabs()
        self.amount_edit.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب الحركة من جديد
        if self.mode == "edit" and not self._is_editable_record():  # جهاز تاني رحّلها في الوقت ده
            self.load(self.current_id)
            messages.warn(self, "تعديل", "الحركة دي مرحّلة — الغي ترحيلها الأول عشان تعدّلها.")

    # ---- الترحيل / إلغاء الترحيل --------------------------------------------------
    def _confirm_funds(self, data: dict[str, Any]) -> bool:
        """«من» مش مكفي ← تحذير بس والمستخدم يكمّل (رد المستخدم)."""
        try:
            warning = self.service.funds_warning(data)
        except Exception:  # noqa: BLE001 - الحفظ نفسه هيطلّع الغلط
            return True
        if not warning:
            return True
        return messages.ask_yes_no(self, "⚠️ الرصيد مش مكفي", f"{warning}\n\nتكمّل الترحيل؟")

    def post_transfer(self) -> None:
        if not self.post_button.isEnabled():
            return
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            if not self._confirm_funds(payload):
                return
            if not messages.ask_yes_no(self, "ترحيل", "هتتحفظ الحركة وتترحّل بقيد — بعد الترحيل ماتتعدّلش "
                                                       "غير بـ «إلغاء الترحيل». تكمّل؟"):
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
            rec = self.current_record or {}
            if not self._confirm_funds(self._record_data(rec)):
                return
            if not messages.ask_yes_no(self, "ترحيل", f"ترحيل الحركة {transfer_label(rec)}؟"):
                return
            try:
                self.service.post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل",
                      f"اترحّلت الحركة برقم {ltr((self.current_record or {}).get('transfer_no'))}.")

    def unpost_transfer(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = transfer_label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"قيد الحركة {label} هيتمسح والحركة هترجع مسودة (برقمها) وتقدر تعدّلها. تكمّل؟"):
            return
        try:
            self.service.unpost(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self._after_action()
        messages.info(self, "تم", f"الحركة {label} رجعت مسودة — دوس «تعديل» عشان تعدّلها.")

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
        self.record_saved.emit(self.current_id)

    # ---- القائمة والصفحات --------------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return self._from, self._to, self.status, keyword, self.list_type

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
            row["route"] = f"{row['from_name']} ← {row['to_name']}"
        return rows

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "transfer_no":
            return value or f"مسودة {row.get('draft_no')}"
        if key == "transfer_type":
            return TRANSFER_TYPES.get(value, ("",))[0]
        if key == "amount":
            return money(value)
        if key == "status":
            return "مرحّلة" if value == "posted" else "مسودة"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col, type_col = cols.index("status"), cols.index("transfer_type")
        for r, row in enumerate(self.table.rows):
            for col, color in ((status_col, OK_COLOR if row.get("status") == "posted" else DRAFT_COLOR),
                               (type_col, type_color(row.get("transfer_type")))):
                item = self.table.item(r, col)
                if item is not None:
                    item.setForeground(QColor(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
        counts = self._counts
        if counts is not None:
            self.count_label.setText(f"{counts['total']:,} حركة في الفترة · مسودة {counts['drafts']:,} · "
                                     f"مرحّلة {counts['posted']:,} ({money(counts['posted_amount'])} جنيه)")
        self._update_paging(self.mode in EDITING)
        self._update_tabs()

    # ---- البيانات ----------------------------------------------------------
    def _show_status(self, record: dict | None) -> None:
        if not record:
            self.status_label.setText("جديدة")
            self.status_label.setStyleSheet(BADGE.format(bg="#EFF6FF", fg="#1D4ED8", bd="#BFDBFE"))
            self.year_note.hide()
            return
        text, bg, fg, bd = STATUS_BADGES[record["status"]]
        self.status_label.setText("مرحّلة ✔" if record["status"] == "posted" else text)
        self.status_label.setStyleSheet(BADGE.format(bg=bg, fg=fg, bd=bd))
        note = ""
        if record.get("year_status") == "closed":
            note = f"🔒 السنة المالية {record['year_code']} مقفولة — الحركة دي ماتتعدّلش ولا يتلغى ترحيلها."
        elif record["status"] == "posted" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = f"✔ اترحّلت بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}"
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
            self.amount_edit.setText(money(record.get("amount")))
            self.fees_edit.setText(money(record.get("fees")) if record.get("fees") else "")
            self.description.setText(record.get("description") or "")
            self.reference_no.setText(record.get("reference_no") or "")
            self._show_status(record)
        finally:
            self._filling = False
        self._set_lookups(record)
        self._changed()

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.amount_edit, self.fees_edit, self.description, self.reference_no):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.year_note.hide()
        finally:
            self._filling = False
        self._set_lookups()
        if hasattr(self, "entry_table"):
            self._clear_side()

    @staticmethod
    def _record_data(rec: dict[str, Any]) -> dict[str, Any]:
        return {k: rec.get(k) for k in ("transfer_date", "from_kind", "from_treasury_id", "from_bank_account_id",
                                        "to_kind", "to_treasury_id", "to_bank_account_id", "amount", "fees",
                                        "description", "reference_no")}

    def collect(self) -> dict:
        data = self._data()
        data["description"] = self.description.text()
        return data

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_transfer(int(record_id), expected_version=self.current_version)
