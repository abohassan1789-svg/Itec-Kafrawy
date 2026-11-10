"""شاشة «سجل الدخول» — النموذج 1 «زي Hr GAD: فلاتر فوق + جدول» (اختيار المستخدم 2026-11-07).

للقراءة بس (القاعدة نفسها مابتسمحش بتعديل السجل). على الـ Base Screen من غير فورم:
* فوق: فلاتر في سطر واحد (المستخدم، الحالة، من، إلى) وتحتها البحث.
* الجدول واخد الشاشة كلها: المستخدم، الاسم، الدخول، الخروج، المدة، الحالة، الجهاز، السبب —
  الفاشل بالأحمر وسببه جنبه، والجلسة اللي لسه مفتوحة بالأزرق.
* تحت: العدد + ناجح / فاشل / مفتوحة · شريط الأدوات: تحديث / تصدير إكسل / خروج.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import QComboBox, QDateEdit, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.services.user_service import UserService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN, RED

LIMIT = 1000
STATE_COLORS = {"failed": RED, "open": "#2563EB", "closed": GREEN}
STATUS_FILTERS = (("الكل", None), ("✔ ناجح", "success"), ("✖ فشل", "failed"), ("● مفتوحة دلوقتي", "open"))


class LoginLogsScreen(BaseScreen):
    MODULE = "sys"
    TARGET = "login_logs"
    TITLE = "سجل الدخول"
    ICON = "🕘"
    LIST_TITLE = "سجل الدخول والخروج"
    SEARCH_PLACEHOLDER = "🔍 بحث بالمستخدم / الاسم / الجهاز…"
    COLUMNS = [("username", "المستخدم"), ("full_name", "الاسم"), ("login_time", "وقت الدخول"),
               ("logout_time", "وقت الخروج"), ("duration", "المدة"), ("status_ar", "الحالة"),
               ("device_name", "الجهاز"), ("notes", "السبب")]

    def __init__(self, users: UserService | None = None, parent: QWidget | None = None) -> None:
        self.users = users or UserService()
        self._ready = False                      # الفلاتر لسه بتتبني ← ماتحمّلش مرتين
        super().__init__(parent)
        self._ready = True
        self.setMinimumSize(1100, 600)
        self.refresh_list()

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        QVBoxLayout(box)                          # مفيش فورم — الشاشة جدول بس

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        self._form_box = form_box                 # بنمسكه عشان مايتمسحش، ومش بيظهر
        form_box.hide()
        content.addWidget(list_box, 1)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.user_filter = QComboBox()
        self.user_filter.addItem("الكل", None)
        try:
            for user in self.users.list_users(include_inactive=True):
                label = user["username"] + (f" — {user['full_name']}" if user.get("full_name") else "")
                self.user_filter.addItem(label, int(user["id"]))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل المستخدمين", exc)
        self.status_filter = QComboBox()
        for text, code in STATUS_FILTERS:
            self.status_filter.addItem(text, code)
        today = QDate.currentDate()
        self.date_from = self._date_edit(QDate(today.year(), today.month(), 1))   # أول الشهر
        self.date_to = self._date_edit(today)
        for text, widget, stretch in (("المستخدم", self.user_filter, 3), ("الحالة", self.status_filter, 2),
                                      ("من", self.date_from, 2), ("إلى", self.date_to, 2)):
            label = QLabel(text)
            label.setStyleSheet("font-weight:800;")
            row.addWidget(label)
            row.addWidget(widget, stretch)
        layout.insertLayout(0, row)               # الفلاتر فوق البحث (زي النموذج)
        self.user_filter.currentIndexChanged.connect(self.refresh_list)
        self.status_filter.currentIndexChanged.connect(self.refresh_list)
        self.date_from.dateChanged.connect(self.refresh_list)
        self.date_to.dateChanged.connect(self.refresh_list)

    @staticmethod
    def _date_edit(value: QDate) -> QDateEdit:
        edit = QDateEdit(value)
        edit.setCalendarPopup(True)
        # الـ RTL بيقلب ترتيب أجزاء التاريخ (yyyy/MM/dd) ← الاتجاه لازم يتظبط **قبل** الـ format.
        edit.setLayoutDirection(Qt.LeftToRight)
        edit.setDisplayFormat("dd/MM/yyyy")
        return edit

    # ---- البيانات ----------------------------------------------------------
    def filters(self) -> dict[str, Any]:
        start, end = self.date_from.date(), self.date_to.date()
        if start > end:
            start, end = end, start
        return {"user_id": self.user_filter.currentData(), "status": self.status_filter.currentData(),
                "date_from": start.toPython(), "date_to": end.toPython()}

    def fetch_rows(self, keyword: str) -> list[dict]:
        if not getattr(self, "_ready", False):
            return []
        return self.users.login_log_rows(keyword=keyword, limit=LIMIT, **self.filters())

    def fetch_record(self, record_id: Any) -> dict | None:
        return next((r for r in self.table.rows if r.get("id") == record_id), None)

    def fill_form(self, record: dict) -> None:
        """مفيش فورم."""

    def clear_form(self) -> None:
        """مفيش فورم."""

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key in ("login_time", "logout_time"):
            if value is None:
                return "—" if key == "logout_time" and row.get("state") != "failed" else ""
            return value.astimezone().strftime("%d-%m-%Y %I:%M %p").replace("AM", "ص").replace("PM", "م")
        if key == "full_name" and not value:
            return "—"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        rows = self.table.rows
        status_col = [k for k, _t in self.COLUMNS].index("status_ar")
        notes_col = [k for k, _t in self.COLUMNS].index("notes")
        bold = QFont()
        bold.setBold(True)
        for r, row in enumerate(rows):
            color = QBrush(QColor(STATE_COLORS[row.get("state", "closed")]))
            for c in (status_col, notes_col):
                item = self.table.item(r, c)
                if item is not None:
                    item.setForeground(color)
                    item.setFont(bold)
            if row.get("state") == "open":
                self.table.item(r, status_col).setToolTip("لسه شغّال — أو البرنامج اتقفل من غير «تسجيل الخروج»")
        counts = {s: sum(1 for r in rows if r.get("state") == s) for s in STATE_COLORS}
        text = (f"عدد السجلات المعروضة: {len(rows)} · ناجح {counts['closed'] + counts['open']} · "
                f"فاشل {counts['failed']} · مفتوحة {counts['open']}")
        if len(rows) >= LIMIT:
            text += f"  (أول {LIMIT} بس — ضيّق الفلاتر)"
        self.count_label.setText(text)
        self.table.setTextElideMode(Qt.ElideNone)
