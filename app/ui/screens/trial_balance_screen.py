"""شاشة «ميزان المراجعة» — النموذج 4 «تبويب لكل نوع» (اختيار المستخدم وردوده 2026-11-17).

```
┌ [عرض ▶] [تصدير إكسل] [تحديث] [خروج]                                                    (عرض) ┐
│ من [..] إلى [..] (الشهر ده) (آخر 3 شهور) (من أول السنة)  المستوى [كل المستويات ▾] ☐ الحسابات الصفرية │
│ [1 الأصول (12)] [2 الخصوم (3)] [3 حقوق الملكية (2)] [4 الإيرادات (2)] [5 المصروفات (5)] [📋 الكل]   │
│ ╔ الكود │ الحساب │ سابق مدين │ سابق دائن │ حركة مدين │ حركة دائن │ مجموع مدين │ مجموع دائن │ رصيد مدين │ رصيد دائن ╗ │
│ الحسابات بالشجرة (الرئيسي = مجموع اللي تحته) ← إجمالي النوع                                           │
│ ✅ الميزان متوازن — مدين = دائن في الأعمدة كلها                                                      │
└ … · دبل كليك على الحساب = كشف الحساب على نفس الفترة                                                ┘
```
* 8 أعمدة · المستوى الافتراضي شجرة بكل المستويات وفلتر المستوى · الفترة من أول السنة المالية لحد النهارده.
* الرصيد السابق: الميزانية كل اللي قبل «من» · الإيرادات والمصروفات من أول السنة المالية بتاعة «من».
* المرحّل بس · من غير طباعة دلوقتي · تصدير إكسل (الميزان كله بكل الأنواع) من شريط الأدوات.
* دبل كليك على الحساب ← «كشف الحساب ودفتر الأستاذ» على نفس الحساب والفترة.
* صلاحية واحدة (عرض) — والتصدير مع العرض. التبويبات بتتبدّل من غير ما الميزان يتقرا تاني من القاعدة.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.services.account_service import TYPES as ACCOUNT_TYPES
from app.services.trial_balance_service import TYPES, TrialBalanceService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, export_rows_to_excel
from app.ui.common.theme import FIRST_COLUMN, GREEN, TEXT_MUTED
from app.ui.screens.account_statement_screen import CHIP_STYLE, PERIOD_CHIPS, _main_window_opener, _pydate, _qdate

ALL_TAB = "all"
AMOUNT_KEYS = ("pd", "pc", "debit", "credit", "td", "tc", "ed", "ec")
OFF_COLOR = "#94A3B8"
# نوع الصف ← (الخلفية، لون الكلام)
ROW_STYLES = {"type_total": ("#F1F5F9", "#111827"), "type": ("#E8F5EC", GREEN), "grand_total": ("#1F4E79", "#FFFFFF")}
BALANCED_STYLE = ("background:#ECFDF3; color:#087443; border:1px solid #B7E4C7; border-radius:6px; "
                  "padding:5px 10px; font-weight:900;")
UNBALANCED_STYLE = ("background:#FEE2E2; color:#B91C1C; border:1px solid #FECACA; border-radius:6px; "
                    "padding:5px 10px; font-weight:900;")

Opener = Callable[[str], Any]


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    v = Decimal(value)
    return f"{v:,.2f}" if abs(v) >= Decimal("0.005") else ""


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


class TrialBalanceScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "trial_balance"
    TITLE = "ميزان المراجعة"
    ICON = "⚖️"
    LIST_TITLE = "ميزان المراجعة"
    COLUMNS = [("code", "الكود"), ("name", "الحساب"),
               ("pd", "رصيد سابق\nمدين"), ("pc", "رصيد سابق\nدائن"),
               ("debit", "حركة الفترة\nمدين"), ("credit", "حركة الفترة\nدائن"),
               ("td", "المجاميع\nمدين"), ("tc", "المجاميع\nدائن"),
               ("ed", "الرصيد\nمدين"), ("ec", "الرصيد\nدائن")]
    EXIT_LAST = True

    def __init__(self, service: TrialBalanceService | None = None, opener: Opener | None = None,
                 parent: QWidget | None = None) -> None:
        self.service = service or TrialBalanceService()
        self.opener = opener or _main_window_opener
        self.result: dict[str, Any] | None = None
        self.current_type: str = "asset"
        self._ready = False
        self._from, self._to = self.service.default_period()
        super().__init__(parent)
        self.search.hide()
        self._order_toolbar()
        list_layout = self.count_label.parentWidget().layout()
        list_layout.insertWidget(list_layout.indexOf(self.count_label), self._build_check_bar())
        self.table.cellDoubleClicked.connect(self._on_double_click)
        self.table.setToolTip("دبل كليك على الحساب = كشف الحساب على نفس الفترة")
        self._ready = True
        self._load_levels()
        self.run()

    # ---- الصلاحية: واحدة (عرض) والتصدير معاها -----------------------------------
    def has_action(self, action: str) -> bool:
        return action == "export" or super().has_action(action)

    def can(self, action: str) -> bool:
        return super().can("view" if action == "export" else action)

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        QVBoxLayout(box)                          # مفيش فورم — الشاشة تقرير بس

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        form_box.hide()
        content.addWidget(list_box, 1)

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.run_button = self._button("عرض ▶", "primary", self.run)
        self.run_button.setToolTip("يعرض الميزان بالفلاتر اللي فوق")
        layout.addWidget(self.run_button)

    def _order_toolbar(self) -> None:
        """نفس ترتيب كشف الحساب: عرض ▶ · تصدير إكسل · تحديث · خروج."""
        layout = self.run_button.parentWidget().layout()
        buttons = (self.run_button, self.export_button, self.refresh_button, self.exit_button)
        for button in buttons:
            layout.removeWidget(button)
        for i, button in enumerate(buttons):
            layout.insertWidget(i, button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.from_edit, self.to_edit = QDateEdit(), QDateEdit()
        for edit, value in ((self.from_edit, self._from), (self.to_edit, self._to)):
            edit.setCalendarPopup(True)
            edit.setLayoutDirection(Qt.LeftToRight)      # قبل الـ format (الـ RTL بيقلب الأجزاء)
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setDate(_qdate(value))
        for text, widget in (("من", self.from_edit), ("إلى", self.to_edit)):
            label = QLabel(text)
            label.setStyleSheet("font-weight:800;")
            row.addWidget(label)
            row.addWidget(widget, 1)
        self.period_buttons = QButtonGroup(self)
        self.period_buttons.setExclusive(True)
        for key, text in PERIOD_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            self.period_buttons.addButton(chip)
            row.addWidget(chip)
        level_label = QLabel("المستوى")
        level_label.setStyleSheet("font-weight:800;")
        self.level_combo = QComboBox()
        self.level_combo.addItem("كل المستويات (شجرة)", "all")
        self.zero_check = QCheckBox("الحسابات الصفرية")
        self.zero_check.setToolTip("يظهر الحسابات اللي مالهاش رصيد ولا حركة في الفترة")
        row.addWidget(level_label)
        row.addWidget(self.level_combo, 2)
        row.addWidget(self.zero_check)
        row.addStretch(1)
        layout.insertLayout(0, row)
        layout.insertLayout(1, self._build_type_tabs())
        self._mark_period_chip()
        self.from_edit.dateChanged.connect(self._on_dates)
        self.to_edit.dateChanged.connect(self._on_dates)
        self.period_buttons.buttonClicked.connect(self._on_period_chip)
        self.level_combo.currentIndexChanged.connect(lambda _i: self.run())
        self.zero_check.toggled.connect(lambda _on: self.run())

    def _build_type_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_tabs: dict[str, QPushButton] = {}
        colors = {k: v[3] for k, v in ACCOUNT_TYPES.items()}
        for key in (*TYPES, ALL_TAB):
            color = colors.get(key, GREEN)
            button = QPushButton("")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(36)
            button.setStyleSheet(
                f"QPushButton {{ background:#FFFFFF; color:{color}; border:1px solid #E2E8F0; "
                f"border-top:3px solid {color}; border-radius:6px; padding:5px 14px; font-weight:900; }}"
                "QPushButton:hover { background:#F8FAFC; }"
                f"QPushButton:checked {{ background:{color}; color:#FFFFFF; border:1px solid {color}; }}")
            button.clicked.connect(lambda _checked=False, k=key: self.show_type(k))
            self.type_group.addButton(button)
            self.type_tabs[key] = button
            row.addWidget(button)
        row.addStretch(1)
        self._update_tabs()
        return row

    def _build_check_bar(self) -> QLabel:
        self.check_label = QLabel("")
        self.check_label.setWordWrap(True)
        return self.check_label

    # ---- الفلاتر -------------------------------------------------------------------
    def _load_levels(self) -> None:
        try:
            levels = self.service.levels()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل المستويات", exc)
            levels = []
        self.level_combo.blockSignals(True)
        while self.level_combo.count() > 1:
            self.level_combo.removeItem(1)
        for n in levels:
            self.level_combo.addItem(f"مستوى {n}", n)
        self.level_combo.addItem("الحسابات الفرعية بس", "leaf")
        self.level_combo.blockSignals(False)

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
        any_on = False
        for chip in self.period_buttons.buttons():
            try:
                on = self._chip_start(chip.property("value")) == start
            except Exception:  # noqa: BLE001
                on = False
            if on and not any_on:
                chip.setChecked(True)
                any_on = True
        if not any_on:
            self.period_buttons.setExclusive(False)
            for chip in self.period_buttons.buttons():
                chip.setChecked(False)
            self.period_buttons.setExclusive(True)

    def filters(self) -> dict[str, Any]:
        return {"date_from": _pydate(self.from_edit.date()), "date_to": _pydate(self.to_edit.date()),
                "level": self.level_combo.currentData() or "all", "show_zero": self.zero_check.isChecked()}

    def run(self) -> None:
        """«عرض ▶» = الميزان من جديد من القاعدة بالفلاتر الحالية."""
        self.result = None
        self.refresh_list()

    def refresh_screen(self) -> None:
        self.run()

    def show_type(self, key: str) -> None:
        """تبديل التبويب — من غير ما الميزان يتقرا تاني."""
        self.current_type = key
        self.refresh_list()

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        if not self._ready:
            return []
        if self.result is None:
            self.result = self.service.trial_balance(**self.filters())
        return self.service.rows_for(self.result, None if self.current_type == ALL_TAB else self.current_type)

    def fetch_record(self, record_id: Any) -> dict | None:
        return None

    def fill_form(self, record: dict) -> None:
        """مفيش فورم."""

    def clear_form(self) -> None:
        """مفيش فورم."""

    def _on_selection(self) -> None:
        """اختيار سطر مابيعملش حاجة (الدبل كليك هو اللي بيفتح الكشف)."""

    def open_lookup(self) -> None:
        """F1 ← «من»."""
        self.from_edit.setFocus()

    def _tree(self) -> bool:
        return bool(self.result) and self.result["level"] == "all"

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key in AMOUNT_KEYS:
            if row.get("kind") in ("type_total", "grand_total", "type"):
                return f"{Decimal(value or 0):,.2f}"
            return money(value)
        if key == "code":
            return "" if not value else str(value)
        if key == "name":
            text = str(value or "")
            if row.get("kind") == "account":
                if not row.get("is_active", True):
                    text += " (موقوف)"
                if row.get("carried"):
                    text += " *"
                if self._tree():
                    text = "    " * (int(row.get("level", 1)) - 1) + text
            return text
        return "" if value is None else str(value)

    def refresh_list(self) -> None:
        super().refresh_list()
        self._paint_rows()
        self._fit_columns()
        self._update_tabs()
        self._update_check()

    def _fit_columns(self) -> None:
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        self.table.resizeColumnsToContents()
        for c in range(len(self.COLUMNS)):
            header.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, self.table.columnWidth(c) + 18)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setTextElideMode(Qt.ElideNone)

    def _paint_rows(self) -> None:
        bold = QFont()
        bold.setBold(True)
        first = QColor(FIRST_COLUMN)
        for r, row in enumerate(self.table.rows):
            kind = row.get("kind")
            for c in range(len(self.COLUMNS)):
                item = self.table.item(r, c)
                if item is None:
                    continue
                if c >= 2:
                    item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
                if c == 1:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter)
                if kind in ROW_STYLES:
                    bg, fg = ROW_STYLES[kind]
                    item.setBackground(QBrush(QColor(bg)))
                    item.setForeground(QBrush(QColor(fg)))
                    item.setFont(bold)
                    continue
                if c == 0:
                    item.setBackground(first)
                if not row.get("is_leaf", True):
                    item.setFont(bold)
                    if c <= 1 and int(row.get("level", 1)) == 1:
                        item.setForeground(QBrush(QColor(GREEN)))
                elif not row.get("is_active", True):
                    item.setForeground(QBrush(QColor(OFF_COLOR)))
            if kind == "account" and row.get("carried"):
                self.table.item(r, 1).setToolTip(
                    f"* الرصيد السابق فيه {abs(row['carried']):,.2f} "
                    f"{'خسائر' if row['carried'] > 0 else 'أرباح'} سنين قبل كده لسه ماتقفلتش (لحد قيد الإقفال)")

    def _update_tabs(self) -> None:
        if not hasattr(self, "type_tabs"):
            return
        counts = self.result["active_counts"] if self.result else {}
        for key, button in self.type_tabs.items():
            if key == ALL_TAB:
                button.setText("📋 الكل")
            else:
                root = ACCOUNT_TYPES[key][2]
                button.setText(f"{ltr(root)} {TYPES[key][0]} ({ltr(counts.get(key, 0))})")
            button.setChecked(key == self.current_type)

    def _update_check(self) -> None:
        if not hasattr(self, "check_label"):
            return
        res = self.result
        if res is None:
            self.check_label.setText("")
            self.count_label.setText("")
            return
        g = res["grand"]
        if res["balanced"]:
            self.check_label.setStyleSheet(BALANCED_STYLE)
            total = f"{g['ed']:,.2f}"
            self.check_label.setText(f"✅ الميزان متوازن — مدين = دائن في الأعمدة كلها · الرصيد {ltr(total)}")
        else:
            diff = f"{abs(g['ed'] - g['ec']):,.2f}"
            self.check_label.setStyleSheet(UNBALANCED_STYLE)
            self.check_label.setText(f"⚠️ الميزان مش متوازن — الفرق في الرصيد {ltr(diff)} · بلّغ المسؤول")
        note = ""
        if res["carried"]:
            r = res["retained"]
            note = (f" · * على {ltr(r['code'])} {r['name']}: صافي إيرادات ومصروفات سنين قبل "
                    f"{ltr(res['year_start'].strftime('%d-%m-%Y'))} لسه ماتقفلتش")
        accounts = sum(1 for row in self.table.rows if row.get("kind") == "account")
        self.count_label.setText(
            f"من {ltr(res['date_from'].strftime('%d-%m-%Y'))} إلى {ltr(res['date_to'].strftime('%d-%m-%Y'))} · "
            f"المرحّل بس · {accounts:,} حساب في التبويب ده · دبل كليك على الحساب = كشف الحساب على نفس الفترة"
            f"{note}")

    # ---- دبل كليك ← كشف الحساب ------------------------------------------------------
    def _on_double_click(self, r: int, _c: int) -> None:
        if 0 <= r < len(self.table.rows):
            self.open_row(self.table.rows[r])

    def open_row(self, row: dict) -> None:
        if row.get("kind") != "account" or self.result is None:
            return
        try:
            window = self.opener("account_statement")
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر فتح كشف الحساب", exc)
            return
        if window is not None and hasattr(window, "show_account"):
            window.show_account(row["id"], self.result["date_from"], self.result["date_to"],
                                "main" if row.get("is_leaf", True) else "sub")

    # ---- تصدير إكسل (الميزان كله: كل الأنواع + الإجمالي) ------------------------------
    def export_rows(self) -> list[dict]:
        if self.result is None:
            return []
        rows: list[dict] = []
        for key in TYPES:
            rows.extend(self.service.rows_for(self.result, key))
        rows.append(self.service.rows_for(self.result, None)[-1])
        return rows

    def export_excel(self) -> None:
        if not self.can("export") or self.result is None:
            return
        res = self.result
        title = f"ميزان المراجعة {res['date_from']:%d-%m-%Y} إلى {res['date_to']:%d-%m-%Y}"
        path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل", f"{title}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        columns = [(k, t.replace("\n", " ")) for k, t in self.COLUMNS]
        try:
            export_rows_to_excel(Path(path), "ميزان المراجعة", columns, self.export_rows(),
                                 lambda k, v, r: self.format_cell(k, v, r).strip())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", "اتصدّر الميزان (كل الأنواع + الإجمالي).")
