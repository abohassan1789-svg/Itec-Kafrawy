"""شاشة «السنة المالية والفترات» — النموذج 1 «زي Hr GAD: الفورم يمين + الفترات تحت الفورم + السنين شمال»
(اختيار المستخدم 2026-11-07).

```
┌ [سنة جديدة][تعديل][حفظ][حذف][إلغاء][تحديث][🔒 قفل / فتح السنة][تصدير إكسل][خروج]      (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات السنة ┐ السنة* الاسم من* إلى* │ ┌ البحث والتسجيل ┐                     │
│   الحالة (للعرض) ملاحظات              │ │ السنة │ الاسم │ من │ إلى │ الحالة │     │
│ ┌ الفترات (12 شهر — للتقارير بس) ┐     │ │ …                                   │ │
│ │ # │ الفترة │ من │ إلى │               │ عدد السنين: 3 · مفتوحة 2 · مقفولة 1   │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* «سنة جديدة» بتقترح السنة اللي بعد آخر سنة على طول (أو من «أول شهر في السنة» في بيانات الشركة).
* الفترات بتتعمل لوحدها من «من/إلى» (بتتعرض وانت بتكتب، وبتتحفظ من القاعدة).
* السنة المقفولة: «تعديل» و«حذف» مقفولين — لازم «فتح السنة» الأول (صلاحية لوحدها + كلمة سر إجراء).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDateEdit,
    QHeaderView,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.services.fiscal_year_service import LOCK_ACTION_NAME, FiscalYearService, add_months, build_periods, fmt
from app.ui.common import messages
from app.ui.common.base_screen import RIGHT, BaseScreen
from app.ui.common.data_table import DataTable, default_format
from app.ui.common.theme import TEXT_MUTED, WARNING_TEXT

# ألوان الحالة (نفس النموذج): مفتوحة أخضر · الحالية أزرق · مقفولة أحمر.
STATUS_STYLE = {
    "open": ("#ECFDF3", "#087443", "#B7E4C7"),
    "current": ("#EFF6FF", "#1D4ED8", "#BFDBFE"),
    "closed": ("#FEE2E2", "#B91C1C", "#FECACA"),
}


def _status_kind(row: dict) -> str:
    if row.get("status") == "closed":
        return "closed"
    return "current" if row.get("is_current") else "open"


def status_text(row: dict) -> str:
    kind = _status_kind(row)
    if kind == "closed":
        return "🔒 مقفولة"
    return "● مفتوحة — الحالية" if kind == "current" else "مفتوحة"


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


class FiscalYearsScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "fiscal_years"
    TITLE = "السنة المالية والفترات"
    ICON = "📅"
    FORM_TITLE = "بيانات السنة"
    NEW_TEXT = "سنة جديدة"
    SEARCH_PLACEHOLDER = "🔍 بحث بالسنة / الاسم…"
    COLUMNS = [("year_code", "السنة"), ("name_ar", "الاسم"), ("start_date", "من"), ("end_date", "إلى"),
               ("status", "الحالة")]
    EXIT_LAST = True

    def __init__(self, years: FiscalYearService | None = None, parent: QWidget | None = None) -> None:
        self.years = years or FiscalYearService()
        self._filling = False
        self._periods_version = 0
        super().__init__(parent)
        self.periods_table.set_rows([])
        if self.table.rows:                       # أول ما تفتح: السنة الحالية (أو الأحدث)
            current = next((r for r in self.table.rows if r.get("is_current")), self.table.rows[0])
            self.load(current["id"])
            self.table.select_id(current["id"])

    # ---- البناء ------------------------------------------------------------
    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(form_box)
        right.addWidget(self._build_periods_box(), 1)
        content.addLayout(right, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def build_form(self, box: QGroupBox) -> None:
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(14, 20, 14, 12)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.year_code = QLineEdit()
        self.year_code.setMaxLength(20)
        self.year_code.setPlaceholderText("مثال: 2026")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(100)
        self.name_ar.setPlaceholderText("مثال: السنة المالية 2026")
        self.start_date = self._date_edit()
        self.end_date = self._date_edit()
        self.start_date.dateChanged.connect(self._on_start_changed)
        self.end_date.dateChanged.connect(self._on_dates_changed)
        self.status_label = QLabel("")
        self.status_label.setFixedHeight(32)
        self.status_label.setAlignment(RIGHT)
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(60)
        self.closed_hint = QLabel("🔒 السنة مقفولة — «تعديل» و«حذف» مقفولين لحد ما تتفتح.")
        self.closed_hint.setStyleSheet(f"color:{WARNING_TEXT}; font-weight:800;")
        self.closed_hint.setAlignment(RIGHT)
        form.addRow("السنة *", self.year_code)
        form.addRow("الاسم", self.name_ar)
        form.addRow("من *", self.start_date)
        form.addRow("إلى *", self.end_date)
        form.addRow("الحالة", self.status_label)
        form.addRow("ملاحظات", self.notes)
        form.addRow("", self.closed_hint)
        self._set_status(None)

    @staticmethod
    def _date_edit() -> QDateEdit:
        edit = QDateEdit(QDate.currentDate())
        edit.setCalendarPopup(True)
        # الـ RTL بيقلب ترتيب أجزاء التاريخ (yyyy/MM/dd) ← الاتجاه لازم يتظبط **قبل** الـ format.
        edit.setLayoutDirection(Qt.LeftToRight)
        edit.setDisplayFormat("dd/MM/yyyy")
        edit.setMinimumDate(QDate(2000, 1, 1))
        return edit

    def _build_periods_box(self) -> QGroupBox:
        self.periods_box = QGroupBox("الفترات")
        layout = QVBoxLayout(self.periods_box)
        layout.setContentsMargins(10, 16, 10, 8)
        layout.setSpacing(6)
        self.periods_table = DataTable([("period_no", "#"), ("name_ar", "الفترة"), ("start_date", "من"),
                                        ("end_date", "إلى")], id_key="period_no", formatter=self._period_cell)
        self.periods_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.periods_table.setFocusPolicy(Qt.NoFocus)
        self.periods_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)   # الأعمدة بالتساوي
        self.periods_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        layout.addWidget(self.periods_table, 1)
        # RLM في الأول: «ℹ» حرف اتجاهه شمال، من غيره السطر كله بيتقلب.
        note = QLabel("\u200fℹ الشهور بتتعمل لوحدها من «من/إلى» — للتقارير بس، ومفيش قفل شهري.")
        note.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        layout.addWidget(note)
        return self.periods_box

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.lock_button = self._button("🔒 قفل / فتح السنة", "lock", self.toggle_lock)
        layout.addWidget(self.lock_button)
        self.lock_button.setVisible(self.has_action("lock"))

    def editable_widgets(self) -> list[QWidget]:
        return [self.year_code, self.name_ar, self.start_date, self.end_date, self.notes]

    # ---- الأوضاع -----------------------------------------------------------
    def _is_closed(self) -> bool:
        return bool(self.current_record and self.current_record.get("status") == "closed")

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        if mode == "view" and self._is_closed():
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
        self.closed_hint.setVisible(mode == "view" and self._is_closed())

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.lock_button.setEnabled(idle and self.can("lock"))
        if idle:
            self.lock_button.setToolTip("فتح السنة المقفولة" if self._is_closed() else "قفل السنة")
        else:
            self.lock_button.setToolTip("")

    # ---- القائمة -----------------------------------------------------------
    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key in ("start_date", "end_date"):
            return fmt(value)
        if key == "status":
            return status_text(row)
        if key == "name_ar" and not value:
            return ""
        return default_format(key, value, row)

    @staticmethod
    def _period_cell(key: str, value: Any, row: dict) -> str:
        if key in ("start_date", "end_date"):
            return fmt(value)
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        status_col = [k for k, _t in self.COLUMNS].index("status")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                _bg, fg, _border = STATUS_STYLE[_status_kind(row)]
                item.setForeground(QColor(fg))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        totals = self.years.summary(self.table.rows)
        self.count_label.setText(f"عدد السنين: {totals['total']} · مفتوحة {totals['open']} · "
                                 f"مقفولة {totals['closed']}")

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.years.list_years(keyword)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.years.get_year(int(record_id))

    def _set_status(self, record: dict | None) -> None:
        if record is None:
            text, kind = "", "open"
        elif record.get("status") == "closed":
            kind = "closed"
            by = record.get("closed_by_name") or ""
            when = record["closed_at"].strftime("%d/%m/%Y") if record.get("closed_at") else ""
            text = "🔒 مقفولة" + (f" — {by} · ⁦{when}⁩" if by or when else "")
        elif record.get("id") is None:
            kind, text = "open", "● مفتوحة (سنة جديدة)"
        else:
            kind = _status_kind(record)
            text = "● مفتوحة — الحالية" if kind == "current" else "● مفتوحة"
        bg, fg, border = STATUS_STYLE[kind]
        self.status_label.setText(text)
        style = f"background:{bg}; color:{fg}; border:1px solid {border};" if text else \
            "background:#F1F5F9; border:1px solid #E2E8F0;"
        self.status_label.setStyleSheet(f"QLabel {{ {style} border-radius:6px; padding:0 10px; font-weight:900; }}")

    def _show_periods(self, periods: list[dict]) -> None:
        today = date.today()
        self._periods_version += 1                    # أي scroll متأجل من ملية قديمة بيتلغي
        self.periods_table.set_rows(periods)
        self.periods_table.scrollToTop()              # الجدول بيفتكر الـ scroll بتاع السنة اللي قبلها
        self.periods_box.setTitle(f"الفترات ({len(periods)} شهر — للتقارير بس، من غير قفل)" if periods
                                  else "الفترات")
        blue = QColor(STATUS_STYLE["current"][1])
        for r, period in enumerate(periods):
            if period["start_date"] <= today <= period["end_date"]:      # الشهر الحالي بالأزرق
                for c in range(self.periods_table.columnCount()):
                    item = self.periods_table.item(r, c)
                    item.setForeground(blue)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                # بعد ما الشاشة تترسم (قبلها الـ scroll مالوش أثر)
                QTimer.singleShot(0, lambda row=r, v=self._periods_version: self._scroll_period(row, v))

    def _scroll_period(self, row: int, version: int) -> None:
        item = self.periods_table.item(row, 0)
        if version == self._periods_version and item is not None:
            self.periods_table.scrollToItem(item, QAbstractItemView.PositionAtCenter)

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.year_code.setText(record.get("year_code") or "")
            self.name_ar.setText(record.get("name_ar") or "")
            self.start_date.setDate(_qdate(record["start_date"]))
            self.end_date.setDate(_qdate(record["end_date"]))
            self.notes.setPlainText(record.get("notes") or "")
        finally:
            self._filling = False
        self._set_status(record)
        periods = record.get("periods")
        self._show_periods(periods if periods is not None else build_periods(record["start_date"],
                                                                             record["end_date"]))

    def clear_form(self) -> None:
        self._filling = True
        try:
            self.year_code.clear()
            self.name_ar.clear()
            self.notes.clear()
            self.start_date.setDate(QDate.currentDate())
            self.end_date.setDate(QDate.currentDate())
        finally:
            self._filling = False
        self._set_status(None)
        self._show_periods([])

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            suggestion = self.years.suggest_new_year()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "سنة جديدة", exc)
            return
        self.fill_form({**suggestion, "id": None})

    def _on_start_changed(self, _value: QDate) -> None:
        # في «سنة جديدة»: تغيير «من» بيظبط «إلى» على 12 شهر لوحده.
        if not self._filling and self.mode == "new":
            start = self.start_date.date().toPython()
            self._filling = True
            try:
                self.end_date.setDate(_qdate(add_months(start, 12) - timedelta(days=1)))
            finally:
                self._filling = False
        self._on_dates_changed(_value)

    def _on_dates_changed(self, _value: QDate) -> None:
        if self._filling or self.mode not in ("new", "edit"):
            return
        self._show_periods(build_periods(self.start_date.date().toPython(), self.end_date.date().toPython()))

    def collect(self) -> dict:
        return {
            "year_code": self.year_code.text(),
            "name_ar": self.name_ar.text(),
            "start_date": self.start_date.date().toPython(),
            "end_date": self.end_date.date().toPython(),
            "notes": self.notes.toPlainText(),
        }

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.years.save_year(None if record_id is None else int(record_id), payload, expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.years.delete_year(int(record_id), expected_version=self.current_version)

    # ---- قفل / فتح السنة ---------------------------------------------------
    def toggle_lock(self) -> None:
        if self.mode != "view" or self.current_id is None or not self.can("lock"):
            return
        record = self.current_record or {}
        closing = record.get("status") != "closed"
        code = record.get("year_code", "")
        if closing:
            question = (f"هل تريد قفل السنة المالية {code}؟\n"
                        "بعد القفل مش هينفع تتعدّل ولا تتمسح لحد ما تتفتح تاني.")
        else:
            question = f"هل تريد فتح السنة المالية {code}؟"
        if not messages.ask_yes_no(self, "قفل السنة" if closing else "فتح السنة", question):
            return
        password = None
        try:
            if self.years.needs_lock_password():
                from app.ui.dialogs.action_password_dialog import ask_action_password

                password = ask_action_password(self, LOCK_ACTION_NAME)
                if password is None:
                    return
            self.years.set_closed(int(self.current_id), closing, password, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error(LOCK_ACTION_NAME, exc)
            return
        self.refresh_list()
        self.load(self.current_id)
        messages.info(self, LOCK_ACTION_NAME, f"السنة المالية {code} {'اتقفلت' if closing else 'اتفتحت'}.")
