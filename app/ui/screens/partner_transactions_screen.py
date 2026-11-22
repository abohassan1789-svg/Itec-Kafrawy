"""شاشة «حركات الشركاء» — النموذج 1 «الكلاسيك» (اختيار المستخدم 2026-11-22). مكانها «الحسابات» ← «حقوق الملكية».

```
┌ [جديد][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][تصدير إكسل][خروج]                (عرض) ┐
│ [⬇ إيداع شريك] [⬆ سحب شريك] [🏛 زيادة رأس المال] [٪ توزيع أرباح / خسارة]                                  │
├━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┤
│ ┌ الفورم ┐ الرقم · التاريخ · الحالة                           │ ┌ 🤝 الشريك ┐ رأس المال المدفوع / المكتتب ·   │
│  (حركة)   الشريك · الطريقة (رأس المال) · الخزنة / البنك · المبلغ │   الجاري له / عليه · إيداعات ومسحوبات السنة   │
│  (توزيع)  ربح / خسارة · المبلغ [كل المتاح] · الربح المتاح        │   (التوزيع: نصيب كل شريك بنسبته)            │
│  البيان · رقم الإيصال                                          │ ┌ المستندات ┐ 🔍 · من · إلى · الحالة         │
│ ┌ 📒 القيد اللي هيتعمل ┐ الحساب│البيان│مدين│دائن              │   › السابق  صفحة 1 من 3  التالي ‹           │
└──────────────────────────────────────────────────────────────┴──────────────────────────────────────────┘
```
* السحب أكبر من رصيد جاري الشريك ← **تحذير بس** ويبقى «عليه» (رد المستخدم) · الخزنة مش مكفية ← تحذير.
* توزيع الأرباح قبل إقفال السنة **عادي** · و«تحميل خسارة» على جاري الشركاء بنسب الخسارة.
* مسودة + ترحيل + إلغاء ترحيل · مفيش طباعة دلوقتي.
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
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.errors import BusinessError, ConcurrentEditError
from app.services.journal_service import parse_money
from app.services.partner_transaction_service import (
    DIST_KINDS,
    PAGE_SIZE,
    PartnerTransactionService,
    doc_label,
)
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.screens.custody_screen import BUTTON_STYLE, _cell, _date_edit, _table
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, STATUS_BADGES, ltr, money
from app.ui.screens.vouchers_screen import BAD, BLUE, DRAFT_COLOR, MUTED, OK_COLOR, WARN_STYLE, _pydate, _qdate

PURPLE = "#7C3AED"
KIND_INFO = {"deposit": ("⬇ إيداع شريك", "#137A38"), "withdrawal": ("⬆ سحب شريك", "#B91C1C"),
             "capital": ("🏛 زيادة رأس المال", "#2563EB"), "distribution": ("٪ توزيع أرباح / خسارة", PURPLE)}
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
SOURCE_ITEMS = (("💵 خزنة", "treasury"), ("🏦 بنك", "bank"))
VIA_ITEMS = (("💵 نقدي (خزنة / بنك)", "cash"), ("🔁 تحويل من جاري الشريك", "current"))
SOURCE_LABELS = {"deposit": "الاستلام في *", "withdrawal": "الصرف من *", "capital": "اتدفع في *"}
PLACEHOLDERS = {"deposit": "زي: تمويل مؤقت للشركة", "withdrawal": "زي: مسحوبات شخصية",
                "capital": "زي: استكمال حصة رأس المال", "distribution": "زي: توزيع أرباح تحت الحساب"}
CARD_EMPTY = "اختار شريك ← رأس ماله ورصيد جاريه بيظهروا هنا."


def balance_text(value: Decimal) -> str:
    """رصيد الشريك (دائن − مدين): موجب = «له» · سالب = «عليه»."""
    if value == 0:
        return "0.00"
    return f"{ltr(money(abs(value)))} {'له' if value > 0 else 'عليه'}"


def _pct(value: Any) -> str:
    return f"{Decimal(value or 0).normalize():f}%"


class PartnerTransactionsScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "partner_transactions"
    TITLE = "حركات الشركاء"
    ICON = "💰"
    FORM_TITLE = "إيداع شريك"
    NEW_TEXT = "جديد"
    LIST_TITLE = "المستندات"
    SEARCH_PLACEHOLDER = "🔍 الرقم أو الشريك أو البيان… (F1)"
    COLUMNS = [("doc_no", "الرقم"), ("doc_date", "التاريخ"), ("partner_name", "الشريك"), ("amount", "المبلغ"),
               ("description", "البيان"), ("status", "الحالة")]
    FORM_STRETCH, LIST_STRETCH = 12, 11
    EXIT_LAST = True

    def __init__(self, service: PartnerTransactionService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or PartnerTransactionService()
        self.kind = "deposit"
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._choices: dict[str, Any] = {"partners": [], "treasuries": [], "banks": []}
        self._counts: dict[str, Any] | None = None
        self._filling = False
        self._summary: dict[str, Any] | None = None
        today = dt.date.today()
        self._from = today.replace(month=1, day=1)
        self._to = today.replace(month=12, day=31)
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
        self.number_edit.setToolTip("الرقم الرسمي (PD / PW / PC / PF) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = _date_edit(dt.date.today())
        self.date_edit.dateChanged.connect(self._on_date)
        self.status_label = QLabel("")
        row = QHBoxLayout()
        row.addWidget(self.number_edit, 2)
        row.addWidget(QLabel("التاريخ *"))
        row.addWidget(self.date_edit, 1)
        row.addWidget(QLabel("الحالة"))
        row.addWidget(self.status_label)
        top.addRow("الرقم", row)
        col.addLayout(top)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_txn_page())
        self.stack.addWidget(self._build_dist_page())
        col.addWidget(self.stack)

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
        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(WARN_STYLE)
        self.warn_label.hide()
        col.addWidget(self.warn_label)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        col.addWidget(self.year_note)
        col.addStretch(1)

    def _build_txn_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(7)
        self.partner_edit = LookupEdit("اكتب كود الشريك أو اسمه… *")
        self.partner_edit.value_changed.connect(self._on_partner)
        form.addRow("الشريك *", self.partner_edit)
        self.via_combo = QComboBox()
        for text, value in VIA_ITEMS:
            self.via_combo.addItem(text, value)
        self.via_combo.setToolTip("الزيادة اتدفعت فلوس (خزنة / بنك) ولا اتحوّلت من رصيد جاري الشريك")
        self.via_combo.currentIndexChanged.connect(self._on_via)
        self.via_label = QLabel("الطريقة")
        form.addRow(self.via_label, self.via_combo)
        self.source_kind = QComboBox()
        for text, value in SOURCE_ITEMS:
            self.source_kind.addItem(text, value)
        self.source_kind.currentIndexChanged.connect(self._on_source_kind)
        self.source_edit = LookupEdit("اختار الخزنة… *")
        self.source_edit.value_changed.connect(self._changed)
        self.source_row = QWidget()
        row = QHBoxLayout(self.source_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.source_kind)
        row.addWidget(self.source_edit, 1)
        self.source_label = QLabel(SOURCE_LABELS["deposit"])
        form.addRow(self.source_label, self.source_row)
        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        self.amount_edit.textChanged.connect(self._changed)
        form.addRow("المبلغ *", self.amount_edit)
        return page

    def _build_dist_page(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(7)
        self.dist_kind = QComboBox()
        self.dist_kind.addItem("٪ توزيع ربح (من الأرباح المحتجزة)", "profit")
        self.dist_kind.addItem("📉 تحميل خسارة (على جاري الشركاء)", "loss")
        self.dist_kind.currentIndexChanged.connect(self._changed)
        form.addRow("النوع", self.dist_kind)
        self.dist_amount = QLineEdit()
        self.dist_amount.setLayoutDirection(Qt.LeftToRight)
        self.dist_amount.setPlaceholderText("0.00")
        self.dist_amount.textChanged.connect(self._changed)
        self.all_button = QPushButton("كل المتاح")
        self.all_button.setStyleSheet(BUTTON_STYLE.format(bg=PURPLE))
        self.all_button.setCursor(Qt.PointingHandCursor)
        self.all_button.setToolTip("يكتب الربح المتاح للتوزيع كله لحد التاريخ")
        self.all_button.clicked.connect(self.fill_available)
        row = QHBoxLayout()
        row.addWidget(self.dist_amount, 1)
        row.addWidget(self.all_button)
        form.addRow("المبلغ *", row)
        self.avail_label = QLabel("")
        self.avail_label.setWordWrap(True)
        self.avail_label.setStyleSheet("background:#F5F3FF; border:1px solid #DDD6FE; border-radius:6px; "
                                       "padding:6px 10px; font-weight:800;")
        form.addRow(self.avail_label)
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
        self.card_box = QGroupBox("🤝 الشريك")
        col = QVBoxLayout(self.card_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(5)
        self.card_label = QLabel(CARD_EMPTY)
        self.card_label.setWordWrap(True)
        self.card_label.setStyleSheet("font-weight:800;")
        col.addWidget(self.card_label)
        self.shares_table = _table(["الشريك", "النسبة", "النصيب", "الجاري بعدها"], 0)
        self.shares_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.shares_table.setMinimumHeight(90)
        self.shares_table.setMaximumHeight(160)
        col.addWidget(self.shares_table)
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
        self.entry_table.setMaximumHeight(170)
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

    # ---- النوع --------------------------------------------------------------------
    @property
    def is_dist(self) -> bool:
        return self.kind == "distribution"

    def _paint_kind(self) -> None:
        text, color = KIND_INFO[self.kind]
        if hasattr(self, "kind_tabs"):
            editing = self.mode in EDITING
            for key, button in self.kind_tabs.items():
                button.setChecked(key == self.kind)
                button.setEnabled(not editing)
            self.tab_line.setStyleSheet(f"background:{color}; border:none;")
            self.card_box.setTitle("٪ نصيب كل شريك (بنسبته من شاشة الشركاء)" if self.is_dist else "🤝 الشريك")
            self.shares_table.setVisible(self.is_dist)
        self.form_box.setTitle(text.split(" ", 1)[1])
        self.form_box.setStyleSheet(f"QGroupBox#formBox {{ border-top:3px solid {color}; }}"
                                    f"QGroupBox#formBox::title {{ color:{color}; }}")
        self.stack.setCurrentIndex(1 if self.is_dist else 0)
        capital = self.kind == "capital"
        self.via_label.setVisible(capital)
        self.via_combo.setVisible(capital)
        via_current = capital and self.via_combo.currentData() == "current"
        self.source_label.setVisible(not via_current)
        self.source_row.setVisible(not via_current)
        if not self.is_dist:
            self.source_label.setText(SOURCE_LABELS[self.kind])
        self.description.setPlaceholderText(PLACEHOLDERS[self.kind])

    def show_kind(self, kind: str) -> None:
        """التبديل بين الأنواع الأربعة (القائمة والفورم)."""
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

    def partner_rows(self) -> list[tuple[int, str, str]]:
        """الإيداع وزيادة رأس المال للنشطين بس — والسحب كمان للي خرج (ياخد رصيده)."""
        return [(p["id"], p["code"], f"{p['code']} — {p['name_ar']}" + ("" if p["is_active"] else " (خرج)"))
                for p in self._choices["partners"] if p["is_active"] or self.kind == "withdrawal"]

    def source_rows(self, kind: str) -> list[tuple[int, str, str]]:
        if kind == "treasury":
            return [(t["id"], t["code"], f"{t['code']} — {t['name_ar']}") for t in self._choices["treasuries"]
                    if t["is_active"] and t["is_base_currency"]]
        return [(b["id"], b["code"], f"{b['bank_name']} — {b['code']}" +
                 (f" ({b['account_number']})" if b.get("account_number") else ""))
                for b in self._choices["banks"] if b["is_active"] and b["is_base_currency"]]

    def _fill_source(self, rec: dict | None = None) -> None:
        rec = rec or {}
        kind = self.source_kind.currentData()
        value = rec.get("treasury_id") if kind == "treasury" else rec.get("bank_account_id")
        self.source_edit.set_items(self.source_rows(kind), value,
                                   f"{rec.get('source_code', '')} — {rec.get('source_name', '')}",
                                   placeholder="اختار الخزنة… *" if kind == "treasury" else "اختار الحساب البنكي… *")

    def _on_source_kind(self, *_args: Any) -> None:
        if self._filling:
            return
        self._fill_source()
        rows = self.source_rows(self.source_kind.currentData())
        if self.mode in EDITING and rows:
            self.source_edit.set_value(rows[0][0])
        self._changed()

    def _on_via(self, *_args: Any) -> None:
        self._paint_kind()
        if not self._filling:
            self._changed()

    def _on_partner(self, *_args: Any) -> None:
        if self._filling:
            return
        self._load_summary()
        self._changed()

    def _on_date(self, *_args: Any) -> None:
        if not self._filling:
            self._load_summary()
            self._changed()

    def _load_summary(self) -> None:
        try:
            self._summary = (None if self.is_dist else
                             self.service.partner_summary(self.partner_edit.currentData(), _pydate(self.date_edit.date())))
        except Exception:  # noqa: BLE001 - الكارت للعرض بس
            self._summary = None

    def fill_available(self) -> None:
        """«كل المتاح» ← الربح المتاح للتوزيع لحد التاريخ."""
        if self.mode not in EDITING:
            return
        try:
            avail = self.service.available_profit(_pydate(self.date_edit.date()))["available"]
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "الربح المتاح", exc)
            return
        if avail <= 0:
            messages.info(self, "الربح المتاح", f"مفيش ربح متاح للتوزيع لحد التاريخ ده ({money(avail)}).")
            return
        self.dist_amount.setText(money(avail))

    # ---- الكارت ومعاينة القيد -----------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        common = {"description": self.description.text(), "reference_no": self.reference_no.text()}
        if self.is_dist:
            return {**common, "dist_kind": self.dist_kind.currentData(), "dist_date": _pydate(self.date_edit.date()),
                    "amount": self.dist_amount.text()}
        source = self.source_kind.currentData()
        if self.kind == "capital" and self.via_combo.currentData() == "current":
            source = "current"
        value = self.source_edit.currentData()
        return {**common, "txn_kind": self.kind, "txn_date": _pydate(self.date_edit.date()),
                "partner_id": self.partner_edit.currentData(), "source_kind": source,
                "treasury_id": value if source == "treasury" else None,
                "bank_account_id": value if source == "bank" else None, "amount": self.amount_edit.text()}

    def _refresh_side(self) -> None:
        self._timer.stop()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        if not editing and not rec:
            self._clear_side()
            return
        posted = not editing and rec.get("status") == "posted"
        if self.is_dist:
            self._render_shares(posted)
        else:
            self._render_card()
        self._refresh_warnings(posted)
        self._refresh_entry(posted)

    def _render_card(self) -> None:
        s = self._summary
        if not s or not self.partner_edit.text():
            self.card_label.setText(CARD_EMPTY)
            return
        sub, paid = Decimal(s["subscribed_capital"] or 0), Decimal(s["capital"])
        pct = int(paid / sub * 100) if sub else 0
        parts = [f"<b>{s['code']} — {s['name_ar']}</b> · {'متضامن' if s['partner_type'] == 'general' else 'موصي'} · "
                 f"ربح {ltr(_pct(s['profit_percent']))} / خسارة {ltr(_pct(s['loss_percent']))}"
                 + ("" if s["is_active"] else " · <span style='color:#B91C1C'>خرج (موقوف)</span>"),
                 f"🏛 رأس المال المدفوع {ltr(money(paid))} من المكتتب {ltr(money(sub))} ({pct}%)"
                 + (f" — لسه عليه {ltr(money(sub - paid))}" if sub > paid else ""),
                 f"💰 رصيد الجاري (32): <span style='color:{BLUE if s['current'] >= 0 else BAD}'>"
                 f"{balance_text(Decimal(s['current']))}</span>",
                 f"السنة دي: إيداعات {ltr(money(s['year']['deposit']))} · مسحوبات "
                 f"{ltr(money(s['year']['withdrawal']))} · زيادة رأس مال {ltr(money(s['year']['capital']))}"]
        self.card_label.setText("<br>".join(parts))

    def _render_shares(self, posted: bool) -> None:
        rec = self.current_record or {}
        d = _pydate(self.date_edit.date())
        kind = self.dist_kind.currentData()
        try:
            preview = self.service.distribution_preview(kind, self.dist_amount.text(), d)
        except Exception:  # noqa: BLE001
            preview = {"available": Decimal("0"), "retained": Decimal("0"), "income": Decimal("0"), "shares": [],
                       "error": "مش قادر أحسب الربح المتاح دلوقتي."}
        shares = rec.get("lines") if posted else preview["shares"]
        if posted:
            shares = [{"partner_id": ln["partner_id"], "partner_name": ln["partner_name"],
                       "share_percent": ln["share_percent"], "amount": ln["amount"]} for ln in rec.get("lines") or []]
        self.avail_label.setText(
            f"الأرباح المحتجزة (33) {ltr(money(preview['retained']))} + ربح الفترة لحد التاريخ "
            f"{ltr(money(preview['income']))} = <b>المتاح للتوزيع {ltr(money(preview['available']))}</b>")
        sign = Decimal(1) if kind == "profit" else Decimal(-1)
        self.shares_table.setRowCount(len(shares or []))
        for r, s in enumerate(shares or []):
            _cell(self.shares_table, r, 0, s["partner_name"], bold=True)
            _cell(self.shares_table, r, 1, _pct(s["share_percent"]), number=True)
            _cell(self.shares_table, r, 2, money(s["amount"]), number=True, color=PURPLE, bold=True)
            after = ""
            if not posted:
                try:
                    current = self.service.repository.partner_balances(int(s["partner_id"]))["current"]
                    after = balance_text(current + sign * Decimal(s["amount"]))
                except Exception:  # noqa: BLE001
                    after = ""
            _cell(self.shares_table, r, 3, after.replace("⁦", "").replace("⁩", ""), number=True)
        lines = [f"<b>{DIST_KINDS[kind]}</b> على الشركاء النشطين بنسبهم من شاشة الشركاء."]
        if posted:
            lines.append("الأنصبة اتحسبت وقت الترحيل بالنسب ساعتها.")
        elif preview.get("error"):
            lines.append(f"<span style='color:{BAD}'>✖ {preview['error']}</span>")
        elif kind == "profit":
            amount = self._money(self.dist_amount.text())
            if amount is not None and amount > preview["available"]:
                lines.append(f"<span style='color:{BAD}'>✖ المبلغ أكبر من المتاح للتوزيع — مش هيترحّل.</span>")
            elif amount is not None and amount > preview["retained"]:
                lines.append("قبل إقفال السنة عادي — «الأرباح المحتجزة» ممكن تبقى بالسالب لحد قيد الإقفال.")
        self.card_label.setText("<br>".join(lines))

    @staticmethod
    def _money(text: str) -> Decimal | None:
        try:
            return parse_money(text, "المبلغ") if text.strip() else None
        except BusinessError:
            return None

    def _warnings(self, data: dict[str, Any]) -> list[str]:
        return self.service.dist_warnings(data) if self.is_dist else self.service.txn_warnings(data)

    def _refresh_warnings(self, posted: bool) -> None:
        if posted or self.mode not in EDITING:
            self.warn_label.hide()
            return
        try:
            warnings = self._warnings(self._data())
        except Exception:  # noqa: BLE001
            warnings = []
        self.warn_label.setText("\n".join(f"⚠️ {w}" for w in warnings) + ("\nتقدر ترحّل بس هيجيلك تحذير."
                                                                         if warnings else ""))
        self.warn_label.setVisible(bool(warnings))

    def _clear_side(self) -> None:
        self.card_label.setText(CARD_EMPTY if not self.is_dist else "")
        self.shares_table.setRowCount(0)
        self.avail_label.setText("")
        self.warn_label.hide()
        self.entry_table.setRowCount(0)
        self.entry_label.setText("")
        self.entry_label.setStyleSheet("")
        self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")

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
                rows = self.service.preview_distribution(data) if self.is_dist else self.service.preview_txn(data)
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
        common = [self.date_edit, self.description, self.reference_no]
        if self.is_dist:
            return common + [self.dist_kind, self.dist_amount, self.all_button]
        return common + [self.partner_edit, self.via_combo, self.source_kind, self.source_edit, self.amount_edit]

    def _is_editable_record(self) -> bool:
        rec = self.current_record or {}
        return rec.get("status") == "draft" and rec.get("year_status") != "closed"

    def set_mode(self, mode: str) -> None:
        for widget in (self.partner_edit, self.via_combo, self.source_kind, self.source_edit, self.amount_edit,
                       self.dist_kind, self.dist_amount, self.all_button):
            widget.setEnabled(False)
        super().set_mode(mode)
        editing = mode in EDITING
        if not editing:
            rec = self.current_record or {}
            no = rec.get("dist_no") if self.is_dist else rec.get("txn_no")
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
        if not self.is_dist:
            rows = self.source_rows("treasury")
            if rows:
                self.source_edit.set_value(rows[0][0])
        self.set_mode("new")
        (self.dist_amount if self.is_dist else self.partner_edit).setFocus()

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
    def _confirm(self, data: dict[str, Any]) -> bool:
        """التحذيرات (السحب أكبر من الجاري · الخزنة مش مكفية · المدفوع أكبر من المكتتب · خسارة والفترة كسبانة)."""
        try:
            warnings = self._warnings(data)
        except Exception:  # noqa: BLE001 - الحفظ نفسه هيطلّع الغلط
            return True
        if warnings and not messages.ask_yes_no(self, "⚠️ تحذير", "\n".join(warnings) + "\n\nتكمّل الترحيل؟"):
            return False
        return True

    def _save_call(self):
        return self.service.save_distribution if self.is_dist else self.service.save_txn

    def post_doc(self) -> None:
        if not self.post_button.isEnabled():
            return
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
                saved = self._save_call()(None if self.mode == "new" else int(self.current_id), payload,
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
            if not self._confirm(self._record_data(rec)):
                return
            if not messages.ask_yes_no(self, "ترحيل", f"ترحيل {doc_label(rec)}؟"):
                return
            post = self.service.post_distribution if self.is_dist else self.service.post_txn
            try:
                post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل", f"اترحّل برقم {ltr(doc_label(self.current_record or {}))}.")

    def unpost_doc(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = doc_label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"قيد {label} هيتمسح والمستند هيرجع مسودة (برقمه) وتقدر تعدّله. تكمّل؟"):
            return
        unpost = self.service.unpost_distribution if self.is_dist else self.service.unpost_txn
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
        if key == "partner_name" and row.get("dist_kind"):
            return "كل الشركاء — " + ("ربح" if row["dist_kind"] == "profit" else "خسارة")
        if key == "description" and row.get("source_kind") == "current":
            return f"🔁 من الجاري — {value}"
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
            what = KIND_INFO[self.kind][0].split(" ", 1)[1]
            self.count_label.setText(f"{counts['total']:,} «{what}» في الفترة · مسودة {counts['drafts']:,} · "
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
            if self.is_dist:
                self.dist_kind.setCurrentIndex(1 if rec.get("dist_kind") == "loss" else 0)
            else:
                self.partner_edit.set_items(self.partner_rows(), rec.get("partner_id"),
                                            f"{rec.get('partner_code', '')} — {rec.get('partner_name', '')}")
                self.via_combo.setCurrentIndex(1 if rec.get("source_kind") == "current" else 0)
                self.source_kind.setCurrentIndex(1 if rec.get("source_kind") == "bank" else 0)
                self._fill_source(rec)
        finally:
            self._filling = False
        self._paint_kind()

    def fetch_record(self, record_id: Any) -> dict | None:
        if self.is_dist:
            return self.service.get_distribution(int(record_id))
        return self.service.get_txn(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            no = record.get("dist_no") if self.is_dist else record.get("txn_no")
            self.number_edit.setText(no or f"مسودة {record.get('draft_no')} — الرقم الرسمي بيتاخد مع الترحيل")
            self.date_edit.setDate(_qdate(record["dist_date"] if self.is_dist else record["txn_date"]))
            self.description.setText(record.get("description") or "")
            self.reference_no.setText(record.get("reference_no") or "")
            (self.dist_amount if self.is_dist else self.amount_edit).setText(money(record.get("amount")))
            self._show_status(record)
        finally:
            self._filling = False
        self._set_lookups(record)
        self._load_summary()
        self._changed()

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.amount_edit, self.dist_amount, self.description, self.reference_no):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.year_note.hide()
            self._summary = None
        finally:
            self._filling = False
        self._set_lookups()
        if hasattr(self, "entry_table"):
            self._clear_side()

    def _record_data(self, rec: dict[str, Any]) -> dict[str, Any]:
        if self.is_dist:
            return {k: rec.get(k) for k in ("dist_kind", "dist_date", "amount", "description", "reference_no")}
        return {k: rec.get(k) for k in ("txn_kind", "txn_date", "partner_id", "source_kind", "treasury_id",
                                        "bank_account_id", "amount", "description", "reference_no")}

    def collect(self) -> dict:
        return self._data()

    def save_record(self, payload: dict, record_id: Any) -> Any:
        rid = None if record_id is None else int(record_id)
        return self._save_call()(rid, payload, expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        if self.is_dist:
            self.service.delete_distribution(int(record_id), expected_version=self.current_version)
        else:
            self.service.delete_txn(int(record_id), expected_version=self.current_version)
