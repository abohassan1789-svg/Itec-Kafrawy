"""شاشة «الجرد والتسويات» — النموذج 1 «الكلاسيك» (اختيار المستخدم 2026-11-27).

```
┌ [جرد جديد][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][استيراد الفعلي][ورقة العدّ][خروج]  (عرض) ┐
│ ┌ جرد ┐ الرقم · التاريخ · الحالة                                   │ ┌ الجرود ┐ 🔍                     │
│   المخزن * [..]  النوع [دوري ▾]   ☐ اللي مااتعدّش = صفر            │   من [..] إلى [..]               │
│   ملاحظات                                                          │   [الكل][مسودة][مرحّل]           │
│ ┌ الأصناف — الدفتري والفعلي ┐ 🔍 [الكل][فيها فرق][مااتعدّتش] [✔ الباقي مطابق] ☐ إخفاء الدفتري │   الرقم│التاريخ│المخزن│النوع│…  │
│   الكود│الصنف│النوع│الوحدة│الدفتري│الفعلي│الفرق│التكلفة│قيمة الفرق│ملاحظة │                                  │
│   [صنف…][فاتورة ▾][+ صنف مش في الدفتر]      اتعدّ 6 من 11 · عجز … · زيادة … │   › السابق  صفحة 1  ‹            │
│                                                                    │ ┌ المراجعة ┐ ✖ / ⚠ …             │
│                                                                    │ ┌ 📒 القيد ┐ الحساب│مدين│دائن     │
└────────────────────────────────────────────────────────────────────┴──────────────────────────────────┘
```
* «الفعلي» بيتكتب في الجدول (Tab / Enter للخانة اللي بعدها) والفرق والقيمة بيتحسبوا وانت بتكتب · الغلط والتنبيه في
  عمود «ملاحظة» · القيد اللي هيتعمل تحت.
* «تسوية مباشرة» (نوع): الجدول بيبقى سطور بتتضاف — الكمية ± والسبب.
* «حفظ مسودة» بيبعت السطور اللي اتغيّرت بس (كل سطر بإصداره — كذا جهاز يعدّوا نفس الجرد) · «ترحيل» (وانت بتعدّل =
  حفظ + ترحيل) · «إلغاء الترحيل» بالصلاحية.
* اللي مالوش صلاحية «إظهار الدفتري» بيعدّ أعمى (من غير الدفتري والفروق) · «ورقة العدّ» إكسل و«استيراد الفعلي» منها.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError, ConcurrentEditError
from app.services.stock_count_service import (
    COUNT_TYPES,
    PAGE_SIZE,
    REASONS,
    ZERO,
    StockCountService,
    count_label,
    line_state,
    parse_adjust,
    parse_cost,
    parse_counted,
)
from app.services.stock_ledger_service import KINDS, fmt_qty
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.common.theme import toolbar_button_style
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, STATUS_BADGES, ltr, money
from app.ui.screens.vouchers_screen import BAD, DRAFT_COLOR, MUTED, OK_COLOR, _pydate, _qdate

GREEN = "#137A38"
WARN = "#B45309"
INPUT_BG = "#FFFBEB"
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
ROW_CHIPS = (("all", "الكل"), ("diff", "فيها فرق"), ("un", "مااتعدّتش"))
KIND_ITEMS = (("فاتورة", "invoice"), ("بيان", "statement"))
KIND_COLORS = {"invoice": "#087443", "statement": "#92400E"}
# أعمدة جدول العدّ
C_CODE, C_NAME, C_KIND, C_UNIT, C_BOOK, C_COUNT, C_DIFF, C_REASON, C_COST, C_VALUE, C_NOTE = range(11)
HEADERS = ["الكود", "الصنف", "النوع", "الوحدة", "الدفتري", "الفعلي", "الفرق", "السبب", "التكلفة", "قيمة الفرق",
           "ملاحظة"]
NUM_COLS = (C_BOOK, C_COUNT, C_DIFF, C_COST, C_VALUE)
BOOK_COLS = (C_BOOK, C_DIFF, C_COST, C_VALUE, C_NOTE)        # بتستخبّى في الجرد الأعمى


def _qty_text(value: Any) -> str:
    return "" if value is None else fmt_qty(Decimal(value))


def _signed_qty(value: Any) -> str:
    if value is None:
        return ""
    d = Decimal(value)
    return ("+" if d > 0 else "") + fmt_qty(d)


def _signed_money(value: Decimal) -> str:
    return ("+" if value > 0 else "") + money(value)


class _CountGrid(QTableWidget):
    """Tab / Enter بينقلوا للخانة اللي بعدها اللي بتتكتب (زي الإكسل)."""

    edit_cols: tuple[int, ...] = (C_COUNT, C_COST)

    def _editable(self, r: int, c: int) -> bool:
        item = self.item(r, c)
        return (not self.isRowHidden(r)) and item is not None and bool(item.flags() & Qt.ItemIsEditable)

    def step(self, forward: bool = True) -> bool:
        cells = [(r, c) for r in range(self.rowCount()) for c in self.edit_cols]
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


class StockCountsScreen(BaseScreen):
    MODULE = "inv"
    TARGET = "stock_counts"
    TITLE = "الجرد والتسويات"
    ICON = "🧮"
    FORM_TITLE = "جرد"
    NEW_TEXT = "جرد جديد"
    LIST_TITLE = "الجرود والتسويات"
    SEARCH_PLACEHOLDER = "🔍 رقم الجرد أو المخزن أو الصنف… (F1)"
    COLUMNS = [("count_no", "رقم الجرد"), ("count_date", "التاريخ"), ("warehouse_name", "المخزن"),
               ("type_name", "النوع"), ("net_value", "الصافي")]
    FORM_STRETCH, LIST_STRETCH = 17, 8
    EXIT_LAST = True

    def __init__(self, service: StockCountService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or StockCountService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self.row_filter = "all"
        self._last_query: tuple | None = None
        self._counts: dict[str, int] | None = None
        self._choices: dict[str, Any] = {"warehouses": [], "items": []}
        self._item_rows: list[tuple[int, str, str]] = []
        self._filling = False
        self._show_book = self.service.can_show_book()
        self.rows: list[dict[str, Any]] = []
        self._states: list[dict[str, Any]] = []
        self._dirty: set[tuple[int, str]] = set()
        self._review: dict[str, Any] | None = None
        self._sheet_key: tuple | None = None
        today = dt.date.today()
        self._from = today.replace(day=1)
        self._to = (self._from + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh_review)
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.export_button.setText("ورقة العدّ (إكسل)")
        self.export_button.setMinimumWidth(self.export_button.fontMetrics().horizontalAdvance("ورقة العدّ (إكسل)") + 30)
        self.export_button.setToolTip("بتطلّع أصناف الجرد ده في ملف إكسل: يتطبع ويتعدّ عليه، أو «الفعلي» يتكتب فيه "
                                      "ويتستورد تاني")
        self.count_label.parentWidget().layout().addLayout(self.paging)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        self.form_box = box
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (SC-2026-00001) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setLayoutDirection(Qt.LeftToRight)
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.date_edit.setToolTip("الرصيد الدفتري بيتحسب آخر اليوم ده")
        self.date_edit.dateChanged.connect(self._on_date)
        self.status_label = QLabel("")
        top = QHBoxLayout()
        top.addWidget(self.number_edit, 2)
        top.addWidget(QLabel("تاريخ الجرد *"))
        top.addWidget(self.date_edit, 1)
        top.addWidget(QLabel("الحالة"))
        top.addWidget(self.status_label)
        form.addRow("رقم الجرد", top)
        self.warehouse = QComboBox()
        self.warehouse.currentIndexChanged.connect(self._on_warehouse)
        self.type_combo = QComboBox()
        for code, name in COUNT_TYPES.items():
            if code != "direct" or self._show_book:              # التسوية المباشرة محتاجة الدفتري
                self.type_combo.addItem(name if code == "direct" else f"جرد {name}", code)
        self.type_combo.setToolTip("دوري / سنوي / جزئي = تصنيف للتقارير · تسوية مباشرة = كمية ± بسبب من غير جرد")
        self.type_combo.currentIndexChanged.connect(self._on_type)
        self.zero_check = QCheckBox("اللي مااتعدّش = صفر (جرد شامل)")
        self.zero_check.setToolTip("كل صنف ليه رصيد في المخزن ومااتكتبلوش «الفعلي» هيتحسب صفر ويتسجّل عجز بكل رصيده")
        self.zero_check.toggled.connect(self._on_zero)
        mid = QHBoxLayout()
        mid.addWidget(self.warehouse, 3)
        mid.addWidget(QLabel("النوع"))
        mid.addWidget(self.type_combo, 2)
        mid.addWidget(self.zero_check, 2)
        form.addRow("المخزن *", mid)
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("اختياري (زي: جرد آخر الشهر)")
        form.addRow("ملاحظات", self.description)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        form.addRow(self.year_note)

    def _build_lines(self) -> QGroupBox:
        box = QGroupBox("الأصناف — الدفتري والفعلي")
        self.lines_box = box
        col = QVBoxLayout(box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(5)
        self.row_search = QLineEdit()
        self.row_search.setPlaceholderText("🔍 كود الصنف أو اسمه…")
        self.row_search.textChanged.connect(self._apply_filter)
        bar.addWidget(self.row_search, 2)
        self.row_buttons = QButtonGroup(self)
        self.row_buttons.setExclusive(True)
        for key, text in ROW_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            self.row_buttons.addButton(chip)
            bar.addWidget(chip)
        self.row_buttons.buttonClicked.connect(self._on_row_filter)
        self.fill_button = QPushButton("✔ الباقي مطابق")
        self.fill_button.setCursor(Qt.PointingHandCursor)
        self.fill_button.setStyleSheet(CHIP_STYLE)
        self.fill_button.setToolTip("كل صنف لسه مااتعدّش ياخد رصيده الدفتري (الفعلي = الدفتري)")
        self.fill_button.clicked.connect(self.fill_matching)
        bar.addWidget(self.fill_button)
        self.blind_check = QCheckBox("إخفاء الدفتري")
        self.blind_check.setToolTip("جرد أعمى: العدّ من غير ما الرصيد الدفتري والفروق يبانوا")
        self.blind_check.toggled.connect(self._on_blind)
        self.blind_check.setVisible(self._show_book)
        bar.addWidget(self.blind_check)
        col.addLayout(bar)

        self.grid = _CountGrid(0, len(HEADERS))
        self.grid.setHorizontalHeaderLabels(HEADERS)
        self.grid.verticalHeader().setVisible(False)
        self.grid.setSelectionMode(QAbstractItemView.SingleSelection)
        self.grid.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.grid.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.grid.setAlternatingRowColors(True)
        header = self.grid.horizontalHeader()
        header.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(C_NOTE, QHeaderView.Stretch)
        for c, width in ((C_CODE, 85), (C_KIND, 55), (C_UNIT, 55), (C_BOOK, 75), (C_COUNT, 80), (C_DIFF, 80),
                         (C_REASON, 100), (C_COST, 75), (C_VALUE, 90)):
            self.grid.setColumnWidth(c, width)
        self.grid.itemChanged.connect(self._on_item_changed)
        col.addWidget(self.grid, 1)
        self.total_label = QLabel("")
        self.total_label.setStyleSheet(f"color:{MUTED}; font-weight:900;")
        col.addWidget(self.total_label)

        add = QHBoxLayout()
        add.setSpacing(5)
        self.add_item = LookupEdit("اكتب كود الصنف أو اسمه…")
        self.add_kind = QComboBox()
        for text, value in KIND_ITEMS:
            self.add_kind.addItem(text, value)
        self.add_button = QPushButton("+ صنف مش في الدفتر")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.setStyleSheet(toolbar_button_style("new"))
        self.add_button.clicked.connect(self.add_row)
        add.addWidget(self.add_item, 4)
        add.addWidget(self.add_kind, 1)
        add.addWidget(self.add_button)
        add.addStretch(2)
        col.addLayout(add)
        return box

    def _build_entry(self) -> QWidget:
        """صفحة «المراجعة والقيد»: الشروط ✔ ✖ ⚠ ℹ + القيد اللي هيتعمل (أو المرحّل)."""
        holder = QWidget()
        self.entry_holder = holder
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        self.review_box = QGroupBox("المراجعة")
        rcol = QVBoxLayout(self.review_box)
        rcol.setContentsMargins(10, 18, 10, 8)
        self.checks_label = QLabel("")
        self.checks_label.setWordWrap(True)
        self.checks_label.setTextFormat(Qt.RichText)
        self.checks_label.setAlignment(Qt.AlignTop | Qt.AlignRight)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; } "
                             "QScrollArea > QWidget > QWidget { background:transparent; }")
        scroll.setWidget(self.checks_label)
        rcol.addWidget(scroll)
        col.addWidget(self.review_box, 1)
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
            self.entry_table.setColumnWidth(c, 85)
        self.entry_table.setMaximumHeight(190)
        jcol.addWidget(self.entry_table)
        self.entry_label = QLabel("")
        self.entry_label.setWordWrap(True)
        self.entry_label.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        jcol.addWidget(self.entry_label)
        col.addWidget(self.entry_box)
        return holder

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        """النموذج 1: الفورم والأصناف يمين (الجدول واخد كل الطول) · شمال صفحتين بزرارين: «الجرود» و«المراجعة
        والقيد» (وقت التعديل القائمة مقفولة، فالمراجعة بتظهر مكانها لوحدها)."""
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        lines = self._build_lines()
        align_form_right(lines)
        right.addWidget(lines, 1)
        content.addWidget(holder, self.FORM_STRETCH)                # RTL: أول عنصر = يمين
        side = QWidget()
        left = QVBoxLayout(side)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        tabs = QHBoxLayout()
        tabs.setSpacing(5)
        self.side_buttons = QButtonGroup(self)
        self.side_buttons.setExclusive(True)
        for index, text in enumerate(("📋 الجرود", "📒 المراجعة والقيد")):
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(index == 0)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            self.side_buttons.addButton(chip, index)
            tabs.addWidget(chip)
        tabs.addStretch(1)
        left.addLayout(tabs)
        self.side_stack = QStackedWidget()
        self.side_stack.addWidget(list_box)
        self.side_stack.addWidget(self._build_entry())
        self.side_buttons.idClicked.connect(self.show_side)
        left.addWidget(self.side_stack, 1)
        content.addWidget(side, self.LIST_STRETCH)
        self._load_choices()
        self._apply_columns()

    def show_side(self, index: int) -> None:
        """0 = قائمة الجرود · 1 = المراجعة والقيد (مش متاحة في الجرد الأعمى)."""
        if index == 1 and self._blind():
            index = 0
        self.side_stack.setCurrentIndex(index)
        button = self.side_buttons.button(index)
        if button is not None:
            button.setChecked(True)

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_count)
        self.post_button.setToolTip("بيسجّل الفروق في دفتر المخزون والقيد وبياخد الرقم الرسمي — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_count)
        self.unpost_button.setToolTip("الحركات والقيد بيتمسحوا والجرد بيرجع مسودة برقمه (السنة لازم مفتوحة)")
        self.import_button = self._button("استيراد الفعلي", "export", self.import_excel)
        self.import_button.setToolTip("بيقرا عمود «الفعلي» من ورقة العدّ (إكسل) ويحطه في الجدول — وبعدين «حفظ»")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        self.import_button.setVisible(self.has_action("import"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)
        layout.addWidget(self.import_button)

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
        """بتتحمّل من جديد مع «جرد جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"warehouses": [], "items": []}
        self._item_rows = [(i["id"], i["code"], f"{i['code']} — {i['name']}") for i in self._choices["items"]
                           if i["is_active"]]
        self.add_item.set_items(self._item_rows, None)
        self._fill_warehouses()

    def _fill_warehouses(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
            current = rec.get("warehouse_id", self.warehouse.currentData())
            self.warehouse.clear()
            for w in self._choices["warehouses"]:
                if w["is_active"] or w["id"] == current:
                    text = f"{w['code']} — {w['name_ar']}" + (" 👻 وهمي" if w["is_virtual"] else "") + \
                           ("" if w["is_active"] else " (موقوف)")
                    self.warehouse.addItem(text, w["id"])
            index = self.warehouse.findData(current)
            self.warehouse.setCurrentIndex(index if index >= 0 else -1)
        finally:
            self._filling = False

    def _virtual(self) -> bool:
        wid = self.warehouse.currentData()
        return any(w["id"] == wid and w["is_virtual"] for w in self._choices["warehouses"])

    def _direct(self) -> bool:
        return self.type_combo.currentData() == "direct"

    def _doc_id(self) -> int | None:
        """الجرد المفتوح (مش «جديد»)."""
        return None if self.mode == "new" or self.current_id is None else int(self.current_id)

    def _blind(self) -> bool:
        return not self._show_book or (self.blind_check.isChecked() and not self._direct())

    # ---- تغيير الرأس -----------------------------------------------------------------
    def _has_entries(self) -> bool:
        col = C_DIFF if self._direct_grid else C_COUNT
        return any(self.grid.item(r, col).text().strip() for r in range(self.grid.rowCount()))

    def _on_warehouse(self, *_args: Any) -> None:
        if self._filling or self.mode not in EDITING:
            return
        key = self._sheet_key
        if key and self._has_entries() and key[0] != self.warehouse.currentData():
            if not messages.ask_yes_no(self, "تغيير المخزن", "المخزن اتغيّر — العدّ اللي اتكتب هيتمسح. تكمّل؟"):
                self._filling = True
                self.warehouse.setCurrentIndex(self.warehouse.findData(key[0]))
                self._filling = False
                return
        self._reload_sheet(keep=False)

    def _on_type(self, *_args: Any) -> None:
        direct = self._direct()
        self.zero_check.setVisible(not direct)
        if self._filling:
            return
        if self.mode in EDITING and direct != self._direct_grid:
            if self._has_entries() and not messages.ask_yes_no(
                    self, "تغيير النوع", "التغيير بين «جرد» و«تسوية مباشرة» بيمسح اللي اتكتب. تكمّل؟"):
                self._filling = True
                self.type_combo.setCurrentIndex(
                    self.type_combo.findData("direct" if self._direct_grid else "periodic"))
                self.zero_check.setVisible(not self._direct_grid)
                self._filling = False
                return
            if direct:
                self.zero_check.setChecked(False)
            self._reload_sheet(keep=False)
        self._changed()

    def _on_date(self, *_args: Any) -> None:
        if not self._filling and self.mode in EDITING:
            self._reload_sheet(keep=True)                         # الدفتري بتاريخ تاني — اللي اتكتب بيفضل

    def _on_zero(self, *_args: Any) -> None:
        if not self._filling:
            self._refresh_rows()
            self._changed()

    def _on_blind(self, *_args: Any) -> None:
        self._apply_columns()
        self._refresh_rows()
        self._apply_filter()
        self._changed()

    # ---- جدول العدّ --------------------------------------------------------------------
    _direct_grid = False                                          # الجدول معروض بشكل «تسوية مباشرة»

    def _key(self, row: dict[str, Any]) -> tuple[int, str]:
        return int(row["item_id"]), row["stock_kind"]

    def _inputs(self, r: int) -> dict[str, str]:
        combo = self.grid.cellWidget(r, C_REASON)
        return {"count": self.grid.item(r, C_COUNT).text().strip() if not self._direct_grid else "",
                "adjust": self.grid.item(r, C_DIFF).text().strip() if self._direct_grid else "",
                "cost": self.rows[r].get("_cost", ""),
                "reason": combo.currentData() if combo is not None else ""}

    def _reload_sheet(self, keep: bool) -> None:
        """يجيب صفوف الجدول من جديد (مخزن / تاريخ / نوع اتغيّر) — ``keep`` = اللي اتكتب يفضل."""
        edits: dict[tuple[int, str], dict[str, str]] = {}
        if keep:
            edits = {self._key(self.rows[r]): self._inputs(r) for r in range(len(self.rows))
                     if self._key(self.rows[r]) in self._dirty}
        wid, day = self.warehouse.currentData(), _pydate(self.date_edit.date())
        ctype = self.type_combo.currentData() or "periodic"
        try:
            rows = self.service.sheet(wid, day, self._doc_id(), ctype)
            have = {self._key(r) for r in rows}
            for key in edits:
                if key not in have and wid:
                    rows.append(self.service.item_state(key[0], key[1], wid, day))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الأصناف", exc)
            rows = []
        self.rows = rows
        self._sheet_key = (wid, day, ctype == "direct")
        self._direct_grid = ctype == "direct"
        self._dirty = set()
        self._fill_grid()
        if edits:
            self._filling = True
            for r, row in enumerate(self.rows):
                e = edits.get(self._key(row))
                if e is None:
                    continue
                self.grid.item(r, C_DIFF if self._direct_grid else C_COUNT).setText(
                    e["adjust"] if self._direct_grid else e["count"])
                row["_cost"] = e["cost"]
                combo = self.grid.cellWidget(r, C_REASON)
                if combo is not None and e["reason"]:
                    combo.setCurrentIndex(max(0, combo.findData(e["reason"])))
                self._dirty.add(self._key(row))
            self._filling = False
            self._refresh_rows()
        self._changed()

    def _cell(self, text: str, numeric: bool = False) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if numeric:
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        return item

    def _make_row(self, r: int, row: dict[str, Any]) -> None:
        grid = self.grid
        direct = self._direct_grid
        count_text = "" if direct else _qty_text(row["counted_qty"])
        adjust_text = _signed_qty(row["adjust_qty"]) if direct else ""
        row["_cost"] = _qty_text(row["unit_cost"])
        row["_orig"] = {"count": count_text, "adjust": adjust_text, "cost": row["_cost"],
                        "reason": row.get("reason") or ("damaged" if direct else "")}
        name = row["item_name"] + ("" if row.get("item_active", True) else " (موقوف)")
        cells = {C_CODE: ltr(row["item_code"]), C_NAME: name, C_KIND: KINDS[row["stock_kind"]],
                 C_UNIT: row["unit_name"], C_BOOK: "", C_COUNT: count_text, C_DIFF: adjust_text, C_REASON: "",
                 C_COST: "", C_VALUE: "", C_NOTE: ""}
        for c, text in cells.items():
            item = self._cell(text, c in NUM_COLS)
            if c == C_KIND:
                item.setForeground(QColor(KIND_COLORS[row["stock_kind"]]))
            grid.setItem(r, c, item)
        if direct:
            combo = QComboBox()
            for code, text in REASONS.items():
                combo.addItem(text, code)
            combo.setCurrentIndex(max(0, combo.findData(row["_orig"]["reason"])))
            combo.currentIndexChanged.connect(lambda _i, w=combo: self._on_reason(w))
            grid.setCellWidget(r, C_REASON, combo)

    def _fill_grid(self) -> None:
        self._filling = True
        grid = self.grid
        grid.clearContents()
        grid.setRowCount(0)
        grid.setRowCount(len(self.rows))
        self._states = [{} for _ in self.rows]
        for r, row in enumerate(self.rows):
            self._make_row(r, row)
        self._filling = False
        self._apply_columns()
        self._refresh_rows()
        self._apply_filter()

    def _apply_columns(self) -> None:
        """عناوين وأعمدة الجدول على حسب: جرد / تسوية مباشرة · أعمى ولا لأ."""
        if not hasattr(self, "grid"):
            return
        direct, blind = self._direct_grid, self._blind()
        self.grid.horizontalHeaderItem(C_COUNT).setText("بعد التسوية" if direct else "الفعلي")
        self.grid.horizontalHeaderItem(C_DIFF).setText("التسوية ±" if direct else "الفرق")
        self.grid.edit_cols = (C_DIFF, C_COST) if direct else (C_COUNT, C_COST)
        for c in BOOK_COLS:
            self.grid.setColumnHidden(c, blind and not (direct and c == C_DIFF))
        self.grid.setColumnHidden(C_REASON, not direct)
        header = self.grid.horizontalHeader()                     # التسوية المباشرة: الاسم ياخد العرض
        header.setSectionResizeMode(C_NOTE, QHeaderView.Interactive if direct else QHeaderView.Stretch)
        if direct:
            self.grid.setColumnWidth(C_NOTE, 90)
        self.fill_button.setVisible(not direct)
        self.blind_check.setVisible(self._show_book and not direct)
        self.grid.setColumnHidden(C_COUNT, direct and blind)
        self.lines_box.setTitle("سطور التسوية المباشرة" if direct else
                                ("الأصناف — العدّ" if blind else "الأصناف — الدفتري والفعلي"))
        self.add_button.setText("+ صنف" if direct else "+ صنف مش في الدفتر")
        for chip in self.row_buttons.buttons():
            chip.setVisible(not direct and (chip.property("value") != "diff" or not blind))
        self.side_buttons.button(1).setVisible(not blind)
        if blind:
            self.show_side(0)

    def _set_input(self, item: QTableWidgetItem, on: bool) -> None:
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if on:
            flags |= Qt.ItemIsEditable
        item.setFlags(flags)
        item.setBackground(QColor(INPUT_BG) if on else QBrush())

    def _update_row(self, r: int) -> None:
        """حسبة السطر (الفرق · التكلفة · القيمة · الملاحظة) من اللي مكتوب — نفس حسبة الخدمة."""
        row, grid = self.rows[r], self.grid
        direct, blind = self._direct_grid, self._blind()
        editing = self.mode in EDITING and not row.get("posted")
        was = self._filling
        self._filling = True
        try:
            input_col = C_DIFF if direct else C_COUNT
            self._set_input(grid.item(r, input_col), editing)
            combo = grid.cellWidget(r, C_REASON)
            if combo is not None:
                combo.setEnabled(editing)
            note, cost_cell = grid.item(r, C_NOTE), grid.item(r, C_COST)
            inp = self._inputs(r)
            state: dict[str, Any] = {"state": "none", "value": ZERO, "entered": False, "error": ""}
            try:
                counted = None if direct else parse_counted(inp["count"])
                adjust = parse_adjust(inp["adjust"]) if direct else None
                cost = parse_cost(inp["cost"])
            except BusinessError as exc:
                state = {"state": "bad", "value": ZERO, "entered": True, "error": str(exc)}
                note.setText(f"✖ {exc}")
                note.setForeground(QColor(BAD))
                self._set_input(cost_cell, False)
                self._states[r] = state
                return
            entered = counted is not None or adjust is not None
            book = row.get("book_qty")
            if row.get("posted"):
                diff = row.get("diff_qty")
                state = {"state": "none" if diff is None else ("match" if diff == 0 else
                                                               ("short" if diff < 0 else "over")),
                         "diff": diff, "after": None if book is None or diff is None else book + diff,
                         "cost": row.get("cost"), "value": row.get("value") or ZERO, "need_cost": False,
                         "error": "", "warning": "", "auto": bool(row.get("auto_zero"))}
            elif book is None:                                    # جرد أعمى (من غير صلاحية): مفيش حسبة
                state = {"state": "none", "diff": None, "after": None, "cost": None, "value": ZERO,
                         "need_cost": False, "error": "", "warning": "", "auto": False}
            else:
                same = inp["count"] == row["_orig"]["count"] and row.get("line_id")
                state = line_state(direct=direct, book=book, counted=counted, adjust=adjust,
                                   avg_cost=row.get("avg_cost") or ZERO, typed_cost=cost,
                                   seen=row.get("seen_book_qty") if same else None, is_virtual=self._virtual(),
                                   zero_uncounted=self.zero_check.isChecked() and not direct)
            state["entered"] = entered
            self._states[r] = state
            grid.item(r, C_BOOK).setText("" if book is None else fmt_qty(book))
            diff = state.get("diff")
            if direct:
                grid.item(r, C_COUNT).setText("" if diff is None or state.get("after") is None
                                              else fmt_qty(state["after"]))
                color = BAD if state.get("after") is not None and state["after"] < 0 else "#111827"
                grid.item(r, C_COUNT).setForeground(QColor(color))
            else:
                text, color = "", "#111827"
                if diff is not None:
                    text, color = (("✔", OK_COLOR) if diff == 0 else
                                   (f"▼ {fmt_qty(diff)}", BAD) if diff < 0 else (f"▲ +{fmt_qty(diff)}", OK_COLOR))
                grid.item(r, C_DIFF).setText(text)
                grid.item(r, C_DIFF).setForeground(QColor(color))
            need = bool(state.get("need_cost"))
            self._set_input(cost_cell, editing and need and not blind)
            if need:
                cost_cell.setText(inp["cost"])
            elif self._virtual() and diff:
                cost_cell.setText("—")
            else:
                cost_cell.setText(money(state["cost"]) if diff and state.get("cost") is not None else "")
            value = state.get("value") or ZERO
            grid.item(r, C_VALUE).setText(_signed_money(value) if value else "")
            grid.item(r, C_VALUE).setForeground(QColor(BAD if value < 0 else OK_COLOR))
            if state.get("error"):
                text, color = f"✖ {state['error']}", BAD
            elif state.get("warning"):
                text, color = f"⚠ {state['warning']}", WARN
            elif state.get("auto"):
                text, color = "⚠ مااتعدّش — هيتحسب صفر", WARN
            else:
                text, color = {"none": ("" if direct or blind else "مااتعدّش", MUTED), "match": ("✔ مطابق", OK_COLOR),
                               "short": ("عجز", BAD), "over": ("زيادة", OK_COLOR)}[state["state"]]
            note.setText(text)
            note.setToolTip(text)
            note.setForeground(QColor(color))
        finally:
            self._filling = was

    def _refresh_rows(self) -> None:
        for r in range(len(self.rows)):
            self._update_row(r)
        self._update_totals()

    def _update_totals(self) -> None:
        if not hasattr(self, "total_label"):
            return
        direct, blind = self._direct_grid, self._blind()
        n = len(self._states)
        entered = sum(1 for s in self._states if s.get("entered"))
        if not n:
            self.total_label.setText("")
            self.total_label.setStyleSheet(f"color:{MUTED}; font-weight:900;")
            return
        if blind:
            self.total_label.setText(f"اتعدّ {entered:,} من {n:,}")
            return
        shorts = [s for s in self._states if s.get("state") == "short"]
        overs = [s for s in self._states if s.get("state") == "over"]
        short_v = -sum((s["value"] for s in shorts), ZERO)
        over_v = sum((s["value"] for s in overs), ZERO)
        head = f"{entered:,} سطر" if direct else f"اتعدّ {entered:,} من {n:,}"
        self.total_label.setText(f"{head} · عجز {len(shorts):,} ({ltr(money(short_v))}) · زيادة {len(overs):,} "
                                 f"({ltr(money(over_v))}) · الصافي {ltr(_signed_money(over_v - short_v) or '0.00')}")
        self.total_label.setStyleSheet(
            f"color:{BAD if any(s.get('error') for s in self._states) else MUTED}; font-weight:900;")

    def _apply_filter(self, *_args: Any) -> None:
        if not hasattr(self, "grid"):
            return
        text = self.row_search.text().strip().lower()
        mode = "all" if self._direct_grid else self.row_filter
        for r, row in enumerate(self.rows):
            hit = not text or text in str(row["item_code"]).lower() or text in str(row["item_name"]).lower()
            state = self._states[r] if r < len(self._states) else {}
            if hit and mode == "diff":
                hit = state.get("state") in ("short", "over", "bad")
            elif hit and mode == "un":
                hit = not state.get("entered")
            self.grid.setRowHidden(r, not hit)

    def _on_row_filter(self, chip: QPushButton) -> None:
        self.row_filter = chip.property("value")
        self._apply_filter()

    def _mark(self, r: int) -> None:
        row = self.rows[r]
        if self._inputs(r) != row["_orig"]:
            self._dirty.add(self._key(row))
        else:
            self._dirty.discard(self._key(row))

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        r, c = item.row(), item.column()
        if c == C_COST:
            self.rows[r]["_cost"] = item.text().strip()
        elif c != (C_DIFF if self._direct_grid else C_COUNT):
            return
        self._mark(r)
        self._update_row(r)
        self._update_totals()
        self._changed()

    def _on_reason(self, combo: QComboBox) -> None:
        if self._filling:
            return
        for r in range(self.grid.rowCount()):
            if self.grid.cellWidget(r, C_REASON) is combo:
                self._mark(r)
                break
        self._changed()

    def set_cell(self, item_id: int, stock_kind: str = "invoice", count: str | None = None,
                 adjust: str | None = None, cost: str | None = None, reason: str | None = None) -> None:
        """كتابة في خانات صف (زي ما المستخدم بيكتب) — للاستيراد والاختبارات."""
        r = next(i for i, row in enumerate(self.rows) if self._key(row) == (int(item_id), stock_kind))
        if count is not None:
            self.grid.item(r, C_COUNT).setText(count)
        if adjust is not None:
            self.grid.item(r, C_DIFF).setText(adjust)
        if cost is not None:
            self.grid.item(r, C_COST).setText(cost)
            if self.rows[r].get("_cost", "") != cost.strip():      # الخانة مقفولة (فيه متوسط) — القيمة بتتسجّل برضه
                self.rows[r]["_cost"] = cost.strip()
                self._mark(r)
                self._update_row(r)
        if reason is not None:
            combo = self.grid.cellWidget(r, C_REASON)
            combo.setCurrentIndex(max(0, combo.findData(reason)))

    def fill_matching(self) -> None:
        """«✔ الباقي مطابق»: كل اللي مااتعدّش ياخد رصيده الدفتري."""
        if self.mode not in EDITING or self._direct_grid or self._blind():
            return
        n = 0
        self._filling = True
        for r, row in enumerate(self.rows):
            if row.get("book_qty") is not None and not self.grid.item(r, C_COUNT).text().strip():
                self.grid.item(r, C_COUNT).setText(fmt_qty(row["book_qty"]))
                self._mark(r)
                n += 1
        self._filling = False
        self._refresh_rows()
        self._apply_filter()
        self._changed()
        if n:
            messages.info(self, "الباقي مطابق", f"{n:,} سطر اتسجّلوا «مطابق» (الفعلي = الدفتري) — راجع واحفظ.")

    def add_row(self, item_id: int | None = None, stock_kind: str | None = None) -> bool:
        """«+ صنف»: صف جديد لصنف مش في الجدول (أو يقف على صفه لو موجود)."""
        if self.mode not in EDITING:
            return False
        item_id = item_id or self.add_item.currentData()
        kind = stock_kind or self.add_kind.currentData() or "invoice"
        wid = self.warehouse.currentData()
        if not item_id or not wid:
            messages.warn(self, "صنف", "اختار المخزن والصنف الأول.")
            return False
        key = (int(item_id), kind)
        r = next((i for i, row in enumerate(self.rows) if self._key(row) == key), -1)
        if r < 0:
            try:
                row = self.service.item_state(int(item_id), kind, wid, _pydate(self.date_edit.date()))
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر إضافة الصنف", exc)
                return False
            self.rows.append(row)
            self._states.append({})
            r = len(self.rows) - 1
            self._filling = True
            self.grid.insertRow(r)
            self._make_row(r, row)
            self._filling = False
            self._update_row(r)
            self._update_totals()
        self.grid.setRowHidden(r, False)
        col = C_DIFF if self._direct_grid else C_COUNT
        self.grid.setCurrentCell(r, col)
        self.grid.scrollToItem(self.grid.item(r, col))
        self.add_item.set_items(self._item_rows, None)
        return True

    # ---- المراجعة والقيد ----------------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _header_data(self) -> dict[str, Any]:
        return {"count_date": _pydate(self.date_edit.date()), "warehouse_id": self.warehouse.currentData(),
                "count_type": self.type_combo.currentData() or "periodic",
                "zero_uncounted": self.zero_check.isChecked(), "description": self.description.text()}

    def _line_data(self, r: int) -> dict[str, Any]:
        row, inp = self.rows[r], self._inputs(r)
        need = bool(self._states[r].get("need_cost")) or (row.get("book_qty") is None and inp["cost"])
        return {"item_id": row["item_id"], "stock_kind": row["stock_kind"], "counted_qty": inp["count"],
                "adjust_qty": inp["adjust"], "reason": inp["reason"], "unit_cost": inp["cost"] if need else "",
                "row_version": row.get("row_version"),
                "label": f"{row['item_code']} «{row['item_name']}» ({KINDS[row['stock_kind']]})"}

    def _data(self, full: bool = False) -> dict[str, Any]:
        """``full`` = كل السطور المكتوبة (للمعاينة) · غير كده اللي اتغيّر بس (للحفظ)."""
        col = C_DIFF if self._direct_grid else C_COUNT
        lines = []
        for r, row in enumerate(self.rows):
            if row.get("posted"):
                continue
            if (full and self.grid.item(r, col).text().strip()) or (not full and self._key(row) in self._dirty):
                lines.append(self._line_data(r))
        return {**self._header_data(), "lines": lines}

    def _refresh_review(self) -> None:
        self._timer.stop()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        if (not editing and not rec) or self._blind():
            self._clear_review()
            return
        try:
            review = self.service.review(self._data(full=True), self._doc_id())
        except Exception as exc:  # noqa: BLE001 - للعرض بس
            review = {"checks": [("no", messages.error_text(exc))], "entry": None, "ready": False, "entry_error": ""}
        self._review = review
        posted = not editing and rec.get("status") == "posted"
        marks = {"no": ("✖", BAD), "warn": ("⚠", WARN), "ok": ("✔", OK_COLOR), "info": ("ℹ", "#2563EB")}
        self.checks_label.setText("".join(
            f"<div style='margin:3px 0'><b style='color:{marks[s][1]}'>{marks[s][0]}</b>&nbsp; {t}</div>"
            for s, t in review["checks"]))
        if posted:
            self.review_box.setTitle("✔ مرحّل")
            self.review_box.setStyleSheet(f"QGroupBox::title {{ color:{OK_COLOR}; }}")
        elif review["ready"]:
            self.review_box.setTitle("✔ المراجعة — جاهز للترحيل")
            self.review_box.setStyleSheet(f"QGroupBox::title {{ color:{OK_COLOR}; }}")
        else:
            self.review_box.setTitle("✖ المراجعة — فيه مشاكل")
            self.review_box.setStyleSheet(f"QGroupBox::title {{ color:{BAD}; }}")
        problems = sum(1 for s, _t in review["checks"] if s == "no")
        self.side_buttons.button(1).setText("📒 المراجعة والقيد" + (f" ✖ {problems}" if problems else ""))
        rows = review.get("entry") or []
        self.entry_box.setTitle("📒 القيد المرحّل" if posted else "📒 القيد اللي هيتعمل")
        self.entry_table.setRowCount(len(rows))
        for r, ln in enumerate(rows):
            self.entry_table.setItem(r, 0, QTableWidgetItem(f"{ln['account_code']} {ln['account_name']}"))
            for c, key in ((1, "debit"), (2, "credit")):
                cell = QTableWidgetItem(money(ln[key]) if ln[key] else "")
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
                self.entry_table.setItem(r, c, cell)
        self.entry_table.setVisible(bool(rows))
        if review.get("entry_error"):
            self.entry_label.setText(f"✖ {review['entry_error']}")
        elif rows:
            self.entry_label.setText("✔ متوازن — الزيادة مدين المخزون / دائن تسوية الجرد، والعجز العكس"
                                     + ("" if posted else " (بقيمة الحركات وقت الترحيل)"))
        elif self._virtual():
            self.entry_label.setText("المخزن الوهمي: الكميات بتتصلّح بس — من غير قيد.")
        else:
            self.entry_label.setText("مفيش فروق ليها قيمة — مفيش قيد (المطابق مالوش حركة).")

    def _clear_review(self) -> None:
        self._review = None
        if not hasattr(self, "checks_label"):
            return
        self.checks_label.setText("")
        self.review_box.setTitle("المراجعة")
        self.review_box.setStyleSheet("")
        self.side_buttons.button(1).setText("📒 المراجعة والقيد")
        self.entry_table.setRowCount(0)
        self.entry_table.setVisible(False)
        self.entry_label.setText("")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.warehouse, self.type_combo, self.zero_check, self.description, self.add_item,
                self.add_kind, self.add_button]

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
                                          and not rec.get("count_no"))
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.period_from.setEnabled(not editing)
        self.period_to.setEnabled(not editing)
        self._update_paging(editing)
        self.show_side(1 if editing else 0)                       # وقت التعديل القائمة مقفولة ← المراجعة مكانها
        self._refresh_rows()
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
        direct = self._direct() if hasattr(self, "type_combo") else False
        self.import_button.setEnabled(editing and self.can("import") and not direct)
        self.export_button.setEnabled(self.can("export") and (editing or self.current_id is not None))
        if hasattr(self, "fill_button"):
            self.fill_button.setEnabled(editing and not direct and not self._blind())

    def new_record(self) -> None:
        self._load_choices()
        super().new_record()
        if self.mode != "new":
            return
        self._filling = True
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.number_edit.setText("بيتاخد مع الترحيل")
        self.type_combo.setCurrentIndex(0)
        self.zero_check.setChecked(False)
        self.zero_check.setVisible(True)
        active = [w for w in self._choices["warehouses"] if w["is_active"]]
        first = next((w for w in active if w["is_default"]), active[0] if active else None)
        self.warehouse.setCurrentIndex(self.warehouse.findData(first["id"]) if first else -1)
        self._filling = False
        self._show_status(None)
        self._reload_sheet(keep=False)
        self.set_mode("new")
        self.grid.setFocus()
        self.grid.step(True)

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب الجرد من جديد
        if self.mode == "edit" and not self._is_editable_record():  # جهاز تاني رحّله في الوقت ده
            self.load(self.current_id)
            messages.warn(self, "تعديل", "الجرد ده مرحّل — الغي ترحيله الأول عشان تعدّله.")

    # ---- الترحيل / إلغاء الترحيل --------------------------------------------------
    def _post_question(self) -> str | None:
        """مشاكل المراجعة (بتوقف الترحيل) ← ``None`` · غير كده نص السؤال بالإجماليات."""
        try:
            review = self.service.review(self._data(full=True), self._doc_id())
        except Exception:  # noqa: BLE001 - الترحيل نفسه هيطلّع الغلط
            return "ترحيل الجرد؟"
        bad = [text for state, text in review["checks"] if state == "no"]
        if bad:
            messages.warn(self, "مايترحّلش", "المراجعة فيها مشاكل:\n\n" + "\n".join(f"✖ {t}" for t in bad))
            return None
        t = review["totals"]
        text = "الفروق هتتسجّل في المخازن والقيد — بعد الترحيل الجرد مايتعدّلش غير بـ «إلغاء الترحيل»."
        if review.get("show_book", True):
            text = (f"عجز {t['short_n']:,} سطر ({money(t['short_value'])}) · زيادة {t['over_n']:,} سطر "
                    f"({money(t['over_value'])}) · مطابق {t['match']:,}.\n\n") + text
        warns = [x for s, x in review["checks"] if s == "warn"]
        if warns:
            text += "\n\n" + "\n".join(f"⚠ {w}" for w in warns)
        return text + "\n\nتكمّل؟"

    def post_count(self) -> None:
        if not self.post_button.isEnabled():
            return
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            question = self._post_question()
            if question is None or not messages.ask_yes_no(self, "ترحيل", "هيتحفظ الجرد ويترحّل.\n" + question):
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
            if not self.load(self.current_id):                    # آخر عدّ (جهاز تاني ممكن يكون زوّد سطور)
                return
            question = self._post_question()
            if question is None or not messages.ask_yes_no(
                    self, "ترحيل", f"ترحيل {count_label(self.current_record or {})}؟\n" + question):
                return
            try:
                self.service.post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل", f"اترحّل الجرد برقم {ltr((self.current_record or {}).get('count_no'))}.")

    def unpost_count(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = count_label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"حركات الجرد {label} وقيده هيتمسحوا والأرصدة هترجع زي ما كانت، والجرد هيرجع "
                                   "مسودة (برقمه) وتقدر تعدّله. تكمّل؟"):
            return
        try:
            self.service.unpost(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self._after_action()
        messages.info(self, "تم", f"الجرد {label} رجع مسودة — دوس «تعديل» عشان تعدّله.")

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
        self.record_saved.emit(self.current_id)

    # ---- إكسل ---------------------------------------------------------------------
    def export_excel(self, path: str | None = None) -> None:
        """«ورقة العدّ»: أصناف الجدول في ملف إكسل (بالدفتري لو مش أعمى) — يتعدّ عليه ويتستورد تاني."""
        if not self.can("export") or not self.rows:
            return
        if not path:                                              # الزرار بيبعت False
            path, _ = QFileDialog.getSaveFileName(self, "ورقة العدّ", "ورقة_العدّ.xlsx", "Excel (*.xlsx)")
            if not path:
                return
        rows = []
        for r, row in enumerate(self.rows):
            text = self.grid.item(r, C_COUNT).text().strip() if not self._direct_grid else ""
            try:
                counted = parse_counted(text)
            except BusinessError:
                counted = None
            rows.append({**row, "counted_qty": counted})
        try:
            n = self.service.export_sheet(path, rows, blind=self._blind())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "ورقة العدّ", f"اتصدّر {n:,} سطر — اكتب «الفعلي» في الملف واستورده من «استيراد الفعلي».")

    def import_excel(self, path: str | None = None) -> None:
        """«استيراد الفعلي»: بيحط عمود «الفعلي» من الملف في الجدول (من غير حفظ) — أي غلط في الملف ← مفيش حاجة بتتحط."""
        if self.mode not in EDITING or not self.can("import") or self._direct_grid:
            return
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "استيراد الفعلي من إكسل", "", "Excel (*.xlsx)")
            if not path:
                return
        try:
            good, errors = self.service.match_sheet(self.service.read_sheet(path))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر الاستيراد", exc)
            return
        if errors:
            more = f"\n… و{len(errors) - 12:,} تاني" if len(errors) > 12 else ""
            messages.warn(self, "الملف فيه أغلاط", "مفيش حاجة اتحطت — صلّح الملف واستورده تاني:\n\n"
                          + "\n".join(f"✖ {e}" for e in errors[:12]) + more)
            return
        if not good:
            messages.warn(self, "استيراد", "الملف مفيهوش ولا صف مكتوب فيه «الفعلي».")
            return
        index = {self._key(row): r for r, row in enumerate(self.rows)}
        for line in good:
            key = (line["item_id"], line["stock_kind"])
            if key not in index:
                if not self.add_row(line["item_id"], line["stock_kind"]):
                    return
                index[key] = len(self.rows) - 1
        self._filling = True
        for line in good:
            r = index[(line["item_id"], line["stock_kind"])]
            self.grid.item(r, C_COUNT).setText(fmt_qty(line["counted_qty"]))
            self._mark(r)
        self._filling = False
        self._refresh_rows()
        self._apply_filter()
        self._changed()
        messages.info(self, "استيراد الفعلي", f"اتحط «الفعلي» لـ {len(good):,} سطر — راجع الفروق واحفظ.")

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
        self._counts = self.service.count_counts(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        rows = self.service.list_counts(*query, page=self.page, page_size=PAGE_SIZE)
        for row in rows:
            row["type_name"] = COUNT_TYPES.get(row["count_type"], "")
        return rows

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "count_no":
            return value or f"مسودة {row.get('draft_no')}"
        if key == "net_value":
            if value is None or row.get("status") != "posted":
                return ""
            return _signed_money(Decimal(value)) or "0.00"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        no_col, net_col = cols.index("count_no"), cols.index("net_value")
        for r, row in enumerate(self.table.rows):
            net = Decimal(row.get("net_value") or 0)
            for col, color in ((no_col, OK_COLOR if row.get("status") == "posted" else DRAFT_COLOR),
                               (net_col, BAD if net < 0 else OK_COLOR)):
                item = self.table.item(r, col)
                if item is not None:
                    item.setForeground(QColor(color))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
        counts = self._counts
        if counts is not None:
            self.count_label.setText(f"{counts['total']:,} جرد في الفترة · مسودة {counts['drafts']:,} · "
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
            note = f"🔒 السنة المالية {record['year_code']} مقفولة — الجرد ده مايتعدّلش ولا يتلغى ترحيله."
        elif record["status"] == "posted" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = f"✔ اترحّل بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}"
        self.year_note.setText(note)
        self.year_note.setStyleSheet(f"color:{'#5B21B6' if record.get('year_status') == 'closed' else MUTED}; "
                                     "font-weight:800;")
        self.year_note.setVisible(bool(note))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_count(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.number_edit.setText(record.get("count_no") or
                                     f"مسودة {record.get('draft_no')} — الرقم الرسمي بيتاخد مع الترحيل")
            self.date_edit.setDate(_qdate(record["count_date"]))
            self.description.setText(record.get("description") or "")
            index = self.type_combo.findData(record["count_type"])
            if index < 0:                                         # تسوية مباشرة والمستخدم مالوش «إظهار الدفتري»
                self.type_combo.addItem(COUNT_TYPES[record["count_type"]], record["count_type"])
                index = self.type_combo.count() - 1
            self.type_combo.setCurrentIndex(index)
            self.zero_check.setChecked(bool(record.get("zero_uncounted")))
            self.zero_check.setVisible(record["count_type"] != "direct")
            self._show_status(record)
            self._fill_warehouses(record)
        finally:
            self._filling = False
        self._reload_sheet(keep=False)

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.description):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.year_note.hide()
            self.zero_check.setChecked(False)
        finally:
            self._filling = False
        if hasattr(self, "grid"):
            self.rows, self._states, self._dirty, self._sheet_key = [], [], set(), None
            self._direct_grid = False
            self._fill_grid()
            self._clear_review()

    def collect(self) -> dict:
        return self._data()

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_count(int(record_id), expected_version=self.current_version)
