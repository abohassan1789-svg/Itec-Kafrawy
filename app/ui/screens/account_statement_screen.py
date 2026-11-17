"""شاشة «كشف الحساب ودفتر الأستاذ» — النموذج 1 «فلاتر فوق + جدول» (اختيار المستخدم وردوده 2026-11-17).

```
┌ [عرض ▶] [تصدير إكسل] [تحديث] [خروج]                                       (عرض) ┐
│ النوع [عميل ▾]  الاسم [كود أو اسم…]  (التجميع [بالرئيسية ▾])  من [..] إلى [..]      │
│ (الشهر ده) (آخر 3 شهور) (من أول السنة)                                             │
│ ┃ 👥 0 عميل نقدي                                                                   │
│ ┃ من … إلى … · المرحّل بس · رصيد سابق … ← رصيد آخر المدة …                        │
│ ╔ التاريخ │ المستند │ البيان │ مدين │ دائن │ الرصيد ╗                                │
│ ⏮ رصيد سابق ← الحركات بالرصيد الجاري ← إجمالي حركة الفترة ← ⏭ الرصيد آخر المدة       │
└ N سطر · دبل كليك على السطر = يفتح المستند                      › السابق صفحة 1 من 1 التالي ‹ ┘
```
* شاشة واحدة لكل الكشوف بـ «النوع» (عميل · مورد · عهدة موظف · شريك · خزنة · بنك · أي حساب = دفتر الأستاذ).
* الحساب الرئيسي: «تجميع بالحسابات الرئيسية» (كشف واحد) أو «بالحسابات الفرعية» (كل فرعي لوحده).
* الأعمدة الستة بس · المرحّل بس · مفيش فلاتر زيادة ولا طباعة دلوقتي · تصدير إكسل من شريط الأدوات.
* دبل كليك ← القيد اليدوي (شاشة «القيود اليومية») أو «الأرصدة الافتتاحية» · الفواتير والسندات لما ييجوا.
* صلاحية واحدة (عرض) — والتصدير مع العرض.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.statement_service import GROUPINGS, KINDS, PAGE_SIZE, StatementService, balance_text
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, export_rows_to_excel
from app.ui.common.lookup_edit import LookupEdit
from app.ui.common.theme import BORDER_CARD, FIRST_COLUMN, GREEN, TEXT_MUTED

MUTED = TEXT_MUTED
DEBIT_COLOR, CREDIT_COLOR = "#087443", "#1D4ED8"
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
# نوع الصف ← (الخلفية، لون الكلام) — نفس ألوان النموذج 1
ROW_STYLES = {
    "prev": ("#EFF6FF", "#1D4ED8"), "carry": ("#EFF6FF", "#1D4ED8"),
    "group": ("#E8F5EC", GREEN), "total": ("#F1F5F9", "#111827"), "end": ("#ECFDF3", "#087443"),
    "grand_total": ("#E2E8F0", "#111827"), "grand_end": ("#DCFCE7", "#065F46"),
}
# نوع المستند ← لون رقمه (زي شارات النموذج)
DOC_COLORS = {"manual": "#64748B", "opening_balance": "#3730A3", "sales_invoice": "#087443",
              "sales_return": "#B91C1C", "purchase_invoice": "#1D4ED8", "purchase_return": "#B91C1C",
              "receipt": "#92400E", "payment": "#6D28D9", "stock_adjustment": "#0F766E"}
PERIOD_CHIPS = (("month", "الشهر ده"), ("quarter", "آخر 3 شهور"), ("year", "من أول السنة"))
# نوع المستند ← الشاشة اللي بتفتحه (الباقي لما شاشته تتعمل)
DOC_SCREENS = {"manual": "journal_entries", "opening_balance": "opening_balances"}
DOC_LATER = "المستند ده ({name}) لسه شاشته ماتعملتش — هيفتح منها أول ما تتعمل."

Opener = Callable[[str], Any]


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    v = Decimal(value)
    return f"{v:,.2f}" if v else ""


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


def _main_window_opener(key: str) -> Any:
    """يفتح الشاشة من الواجهة الرئيسية (بتتأكد من الصلاحية وبترجع الشباك)."""
    for widget in QApplication.topLevelWidgets():
        if hasattr(widget, "open_screen") and hasattr(widget, "open_windows"):
            return widget.open_screen(key)
    from app.ui.screen_registry import SCREEN_FACTORIES

    window = SCREEN_FACTORIES[key]()
    window.setAttribute(Qt.WA_DeleteOnClose, True)
    window.showMaximized()
    return window


class AccountStatementScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "account_statement"
    TITLE = "كشف الحساب ودفتر الأستاذ"
    ICON = "📑"
    LIST_TITLE = "كشف الحساب"
    COLUMNS = [("entry_date", "التاريخ"), ("doc_no", "المستند"), ("description", "البيان"),
               ("debit", "مدين"), ("credit", "دائن"), ("balance", "الرصيد")]
    EXIT_LAST = True

    def __init__(self, service: StatementService | None = None, opener: Opener | None = None,
                 parent: QWidget | None = None) -> None:
        self.service = service or StatementService()
        self.opener = opener or _main_window_opener
        self.page = 1
        self.result: dict[str, Any] | None = None
        self._ready = False
        self._choices: list[dict] = []
        self._from, self._to = self.service.default_period()
        super().__init__(parent)
        self.search.hide()                         # مفيش بحث ولا فلاتر زيادة (رد المستخدم 7)
        self._order_toolbar()
        list_layout = self.count_label.parentWidget().layout()
        list_layout.removeWidget(self.count_label)
        list_layout.addLayout(self._build_footer())
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.setToolTip("دبل كليك على السطر = يفتح المستند")
        self._ready = True
        self._load_choices()
        self.refresh_list()

    # ---- الصلاحية: واحدة (عرض) والتصدير معاها -----------------------------------
    def has_action(self, action: str) -> bool:
        return action == "export" or super().has_action(action)

    def can(self, action: str) -> bool:
        return super().can("view" if action == "export" else action)

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        QVBoxLayout(box)                          # مفيش فورم — الشاشة كشف بس

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        self._form_box = form_box
        form_box.hide()
        content.addWidget(list_box, 1)

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.run_button = self._button("عرض ▶", "primary", self.run)
        self.run_button.setToolTip("يعرض الكشف بالفلاتر اللي فوق")
        layout.addWidget(self.run_button)

    def _order_toolbar(self) -> None:
        """نفس ترتيب النموذج 1: عرض ▶ · تصدير إكسل · تحديث · خروج."""
        layout = self.run_button.parentWidget().layout()
        for button in (self.run_button, self.export_button, self.refresh_button, self.exit_button):
            layout.removeWidget(button)
        for i, button in enumerate((self.run_button, self.export_button, self.refresh_button, self.exit_button)):
            layout.insertWidget(i, button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.kind_combo = QComboBox()
        for key, (label, icon, _col) in KINDS.items():
            self.kind_combo.addItem(f"{icon} {label}", key)
        self.target_edit = LookupEdit("اكتب الكود أو الاسم…")
        self.grouping_label = QLabel("التجميع")
        self.grouping_combo = QComboBox()
        for key, text in GROUPINGS.items():
            self.grouping_combo.addItem(text, key)
        self.from_edit, self.to_edit = QDateEdit(), QDateEdit()
        for edit, value in ((self.from_edit, self._from), (self.to_edit, self._to)):
            edit.setCalendarPopup(True)
            edit.setLayoutDirection(Qt.LeftToRight)      # قبل الـ format (الـ RTL بيقلب الأجزاء)
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setDate(_qdate(value))
        widgets = (("النوع", self.kind_combo, 2), ("الاسم", self.target_edit, 5),
                   (self.grouping_label, self.grouping_combo, 3), ("من", self.from_edit, 2),
                   ("إلى", self.to_edit, 2))
        for label, widget, stretch in widgets:
            if isinstance(label, str):
                label = QLabel(label)
            label.setStyleSheet("font-weight:800;")
            row.addWidget(label)
            row.addWidget(widget, stretch)
        layout.insertLayout(0, row)
        chips = QHBoxLayout()
        chips.setSpacing(5)
        self.period_buttons = QButtonGroup(self)
        self.period_buttons.setExclusive(True)
        for key, text in PERIOD_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            self.period_buttons.addButton(chip)
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.insertLayout(1, chips)
        layout.insertWidget(2, self._build_header())
        self._mark_period_chip()
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self.target_edit.value_changed.connect(self._on_target)
        self.grouping_combo.currentIndexChanged.connect(lambda _i: self.run())
        self.from_edit.dateChanged.connect(self._on_dates)
        self.to_edit.dateChanged.connect(self._on_dates)
        self.period_buttons.buttonClicked.connect(self._on_period_chip)
        self._show_grouping(False)

    def _build_header(self) -> QFrame:
        card = QFrame()
        card.setObjectName("stmtHeader")
        card.setStyleSheet(f"QFrame#stmtHeader {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; "
                           f"border-right:4px solid {GREEN}; border-radius:6px; }}")
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 6, 10, 6)
        box.setSpacing(2)
        self.title_label = QLabel("")
        self.title_label.setStyleSheet("font-weight:900; font-size:15px;")
        self.summary_label = QLabel("")
        self.summary_label.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        self.summary_label.setWordWrap(True)
        box.addWidget(self.title_label)
        box.addWidget(self.summary_label)
        return card

    def _build_footer(self) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.addWidget(self.count_label, 1)
        self.prev_button = QPushButton("› السابق")
        self.next_button = QPushButton("التالي ‹")
        for button in (self.prev_button, self.next_button):
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(CHIP_STYLE)
        self.prev_button.clicked.connect(lambda: self._go_page(self.page - 1))
        self.next_button.clicked.connect(lambda: self._go_page(self.page + 1))
        self.page_label = QLabel("")
        self.page_label.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        footer.addWidget(self.prev_button)
        footer.addWidget(self.page_label)
        footer.addWidget(self.next_button)
        return footer

    # ---- الاختيارات والفلاتر -------------------------------------------------------
    def kind(self) -> str:
        return self.kind_combo.currentData()

    def _load_choices(self, keep: Any = None) -> None:
        try:
            self._choices = self.service.choices(self.kind())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الأسماء", exc)
            self._choices = []
        rows = []
        for r in self._choices:
            text = f"{r['code']} — {r['name']}"
            if self.kind() == "account" and not r.get("is_leaf", True):
                text += " (رئيسي)"
            if not r.get("is_active", True):
                text += " (موقوف)"
            rows.append((int(r["id"]), str(r["code"]), text))
        ids = [r[0] for r in rows]
        value = keep if keep in ids else (ids[0] if ids else None)
        self.target_edit.set_items(rows, value)
        self._update_grouping()

    def _target_row(self) -> dict | None:
        value = self.target_edit.currentData()
        return next((r for r in self._choices if int(r["id"]) == value), None)

    def _show_grouping(self, visible: bool) -> None:
        self._grouping_on = visible
        self.grouping_label.setVisible(visible)
        self.grouping_combo.setVisible(visible)

    def _update_grouping(self) -> None:
        row = self._target_row()
        self._show_grouping(self.kind() == "account" and row is not None and not row.get("is_leaf", True))

    def _on_kind(self, _index: int) -> None:
        self._load_choices()
        self.run()

    def _on_target(self, _value: Any) -> None:
        self._update_grouping()
        if self.target_edit.currentData() is not None:
            self.run()

    def _on_dates(self, *_args: Any) -> None:
        self._mark_period_chip()
        self.run()

    def _chip_start(self, key: str) -> dt.date:
        to = _pydate(self.to_edit.date())
        if key == "month":
            return to.replace(day=1)
        if key == "quarter":
            month = to.month - 2
            year = to.year + (month - 1) // 12
            return dt.date(year, (month - 1) % 12 + 1, 1)
        return self.service.year_start(to)

    def _on_period_chip(self, chip: QPushButton) -> None:
        self.from_edit.setDate(_qdate(self._chip_start(chip.property("value"))))

    def _mark_period_chip(self) -> None:
        start = _pydate(self.from_edit.date())
        for chip in self.period_buttons.buttons():
            try:
                on = self._chip_start(chip.property("value")) == start
            except Exception:  # noqa: BLE001
                on = False
            chip.setChecked(on)
        if not any(c.isChecked() for c in self.period_buttons.buttons()):
            self.period_buttons.setExclusive(False)
            for chip in self.period_buttons.buttons():
                chip.setChecked(False)
            self.period_buttons.setExclusive(True)

    def filters(self) -> dict[str, Any]:
        return {"kind": self.kind(), "target_id": self.target_edit.currentData(),
                "date_from": _pydate(self.from_edit.date()), "date_to": _pydate(self.to_edit.date()),
                "grouping": self.grouping_combo.currentData() if self._grouping_on else "main"}

    def show_account(self, account_id: int, date_from: dt.date, date_to: dt.date, grouping: str = "main") -> None:
        """يفتح كشف حساب معيّن على فترة معيّنة (من «ميزان المراجعة» بالدبل كليك) — عرض واحد بس في الآخر."""
        self._ready = False
        try:
            self.kind_combo.setCurrentIndex(self.kind_combo.findData("account"))
            self.target_edit.set_value(account_id)
            self.from_edit.setDate(_qdate(date_from))
            self.to_edit.setDate(_qdate(date_to))
            self._update_grouping()
            if self._grouping_on:
                self.grouping_combo.setCurrentIndex(max(0, self.grouping_combo.findData(grouping)))
        finally:
            self._ready = True
        self.run()

    def run(self) -> None:
        """«عرض ▶» = الكشف من أول صفحة بالفلاتر الحالية."""
        self.page = 1
        self.refresh_list()

    def _go_page(self, page: int) -> None:
        pages = self.result["pages"] if self.result else 1
        self.page = max(1, min(pages, page))
        self.refresh_list()

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        if not self._ready:
            return []
        f = self.filters()
        if f["target_id"] is None:
            self.result = None
            return []
        self.result = self.service.statement(page=self.page, page_size=PAGE_SIZE, **f)
        self.page = self.result["page"]
        return self.result["rows"]

    def fetch_record(self, record_id: Any) -> dict | None:
        return None

    def fill_form(self, record: dict) -> None:
        """مفيش فورم."""

    def clear_form(self) -> None:
        """مفيش فورم."""

    def _on_selection(self) -> None:
        """اختيار سطر مابيعملش حاجة (الدبل كليك هو اللي بيفتح المستند)."""

    def open_lookup(self) -> None:
        """F1 ← خانة «الاسم»."""
        self.target_edit.setFocus()
        self.target_edit.selectAll()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        kind = row.get("kind")
        if kind != "line":
            if key == "entry_date":
                return row.get("label", "")
            if key == "debit" and kind in ("prev", "carry"):
                return money(row["balance"]) if row["balance"] > 0 else ""
            if key == "credit" and kind in ("prev", "carry"):
                return money(-row["balance"]) if row["balance"] < 0 else ""
            if key in ("debit", "credit") and kind in ("total", "grand_total"):
                return f"{Decimal(row[key]):,.2f}"
            if key == "balance" and kind in ("prev", "carry", "end", "grand_end"):
                return balance_text(row["balance"])
            return ""
        if key == "entry_date":
            return value.strftime("%d-%m-%Y") if value else ""
        if key in ("debit", "credit"):
            return money(value)
        if key == "balance":
            return balance_text(value)
        return "" if value is None else str(value)

    def refresh_list(self) -> None:
        self.table.clearSpans()
        super().refresh_list()
        self._paint_rows()
        self._update_header()
        self._fit_columns()

    def _fit_columns(self) -> None:
        """عرض الأعمدة على أطول كلام (بالخط العريض) — والبيان واخد الباقي. صفوف العناوين الممدودة مابتتحسبش."""
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        bold = QFont(self.table.font())
        bold.setBold(True)
        metrics = QFontMetrics(bold)
        for c, (key, title) in enumerate(self.COLUMNS):
            if key == "description":
                continue
            texts = [title] + [self.table.item(r, c).text() for r, row in enumerate(self.table.rows)
                               if self.table.item(r, c) is not None
                               and (row["kind"] == "line" or (c >= 3 and row["kind"] != "group"))]
            header.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, max(metrics.horizontalAdvance(t) for t in texts) + 28)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setTextElideMode(Qt.ElideNone)

    def _paint_rows(self) -> None:
        bold = QFont()
        bold.setBold(True)
        bal_col = len(self.COLUMNS) - 1
        for r, row in enumerate(self.table.rows):
            kind = row["kind"]
            if kind == "line":
                doc = self.table.item(r, 1)
                if doc is not None:
                    doc.setForeground(QBrush(QColor(DOC_COLORS.get(row.get("source_type"), MUTED))))
                    doc.setFont(bold)
                    doc.setToolTip(row.get("doc_type_name") or "")
                desc = self.table.item(r, 2)
                if desc is not None:
                    desc.setTextAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)
                    desc.setToolTip(f"{row.get('account_code')} {row.get('account_name')}")
                self._color_balance(r, bal_col, row.get("balance"))
                continue
            bg, fg = ROW_STYLES[kind]
            for c in range(len(self.COLUMNS)):
                item = self.table.item(r, c)
                if item is None:
                    continue
                item.setBackground(QBrush(QColor(bg)))
                item.setForeground(QBrush(QColor(fg)))
                item.setFont(bold)
            span = len(self.COLUMNS) if kind == "group" else 3
            self.table.setSpan(r, 0, 1, span)
            label = self.table.item(r, 0)
            if label is not None:
                label.setTextAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)
            if kind in ("prev", "carry", "end", "grand_end"):
                self._color_balance(r, bal_col, row.get("balance"))
        first = QColor(FIRST_COLUMN)
        for r, row in enumerate(self.table.rows):
            if row["kind"] == "line" and self.table.item(r, 0) is not None:
                self.table.item(r, 0).setBackground(first)

    def _color_balance(self, r: int, c: int, value: Any) -> None:
        item = self.table.item(r, c)
        if item is None or value is None or Decimal(value) == 0:
            return
        item.setForeground(QBrush(QColor(DEBIT_COLOR if Decimal(value) > 0 else CREDIT_COLOR)))
        font = item.font()
        font.setBold(True)
        item.setFont(font)

    def _update_header(self) -> None:
        res = self.result
        if res is None:
            self.title_label.setText(f"{KINDS[self.kind()][1]} اختار الاسم")
            self.summary_label.setText("")
            self.count_label.setText("")
            self._update_paging()
            return
        t = res["target"]
        icon = KINDS[res["kind"]][1]
        title = f"{icon} {ltr(t['code'])}  {t['name']}"
        if res["is_main_account"]:
            title += f"  — حساب رئيسي ({GROUPINGS[res['grouping']]})"
        self.title_label.setText(title)
        self.summary_label.setText(
            f"من {ltr(res['date_from'].strftime('%d-%m-%Y'))} إلى {ltr(res['date_to'].strftime('%d-%m-%Y'))} · "
            f"المرحّل بس · رصيد سابق {ltr(balance_text(res['prev']))} ← رصيد آخر المدة "
            f"{ltr(balance_text(res['end']))}")
        self.count_label.setText(f"{res['lines']:,} حركة في الفترة · مدين {res['debit']:,.2f} · "
                                 f"دائن {res['credit']:,.2f} · دبل كليك على السطر = يفتح المستند")
        self._update_paging()

    def _update_paging(self) -> None:
        if not hasattr(self, "prev_button"):
            return
        pages = self.result["pages"] if self.result else 1
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < pages)
        self.page_label.setText(f"صفحة {self.page} من {pages}")
        for widget in (self.prev_button, self.next_button, self.page_label):
            widget.setVisible(pages > 1)

    # ---- فتح المستند -------------------------------------------------------------
    def _on_double_click(self, r: int, _c: int) -> None:
        if 0 <= r < len(self.table.rows):
            self.open_row(self.table.rows[r])

    def open_row(self, row: dict) -> None:
        if row.get("kind") != "line":
            return
        key = DOC_SCREENS.get(row.get("source_type"))
        if key is None:
            messages.info(self, "فتح المستند", DOC_LATER.format(name=row.get("doc_type_name") or row.get("doc_no")))
            return
        try:
            window = self.opener(key)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر فتح المستند", exc)
            return
        if window is None:
            return
        if key == "journal_entries" and hasattr(window, "load"):
            if window.load(row["entry_id"]) and hasattr(window, "table"):
                if not window.table.select_id(row["entry_id"]):
                    window.table.clearSelection()

    # ---- تصدير إكسل (الكشف كله — مش الصفحة بس) --------------------------------------
    def export_excel(self) -> None:
        if not self.can("export") or self.result is None:
            return
        t = self.result["target"]
        title = f"كشف حساب {t['code']} {t['name']}"
        path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل", f"{title}.xlsx".replace("/", "-"),
                                              "Excel (*.xlsx)")
        if not path:
            return
        try:
            full = self.service.statement(page=1, page_size=None, **self.filters())
            export_rows_to_excel(Path(path), "كشف الحساب", self.COLUMNS, full["rows"], self.format_cell)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", f"اتصدّر الكشف ({full['lines']:,} حركة).")
