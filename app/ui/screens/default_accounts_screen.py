"""شاشة «الحسابات الافتراضية» — النموذج 9 «شريط الأقسام + تفاصيل الحساب» (اختيار المستخدم 2026-11-09).

```
┌ [تعديل][حفظ][إلغاء][اقترح من الدور][تحديث][تصدير إكسل][خروج]                            (عرض) ┐
├────────────┬─────────────────────────────────────────────────────────────────────────────┤
│ الأقسام     │ ┌ المفاتيح ──────────────────────────────────────────────────────────────┐ │
│ 🛒 المبيعات 6│ │ [بحث…]   المفتاح │ الحساب │ الحالة (✔ متحدد / ⚠ ناقص)                 │ │
│ 📦 المشتريات │ └──────────────────────────────────────────────────────────────────────────┘ │
│ 🏬 المخازن  │ ┌ الحساب المختار ┐ المفتاح · القسم · الحساب ▾ · النوع المسموح · الدور      │
│ …          │                   بيتستخدم في · الرصيد (مع القيود)                          │
└────────────┴─────────────────────────────────────────────────────────────────────────────┘
```
* المفاتيح ثابتة (مفيش «جديد» ولا «حذف») — «تعديل» بيفتح اختيار الحساب بس (فرعي نشط من النوع المسموح).
* «اقترح من الدور» (في التعديل) = أول حساب فرعي نشط بدور المفتاح.
* البحث بيدوّر في كل الأقسام (المفتاح، الاستخدام، كود واسم الحساب).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.default_account_service import GROUPS, TYPE_NAMES, DefaultAccountService, account_label, ltr
from app.ui.common import messages
from app.ui.common.base_screen import RIGHT, BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.theme import BORDER_CARD, GREEN, RED_DARK, TEXT_MUTED

OK_COLOR, MISSING_COLOR = "#087443", RED_DARK
OK_TEXT, MISSING_TEXT = "✔ متحدد", "⚠ ناقص"
NOT_SET = "— مش متحدد —"
BALANCE_TEXT = "بيظهر مع القيود (المرحلة 4)"
SUGGEST_BUTTON = "اقترح من الدور"


def status_text(row: dict) -> str:
    return OK_TEXT if row.get("account_id") else MISSING_TEXT


class DefaultAccountsScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "default_accounts"
    TITLE = "الحسابات الافتراضية"
    ICON = "🔗"
    FORM_TITLE = "الحساب المختار"
    LIST_TITLE = "المفاتيح"
    SEARCH_PLACEHOLDER = "🔍 بحث بالمفتاح أو الحساب (في كل الأقسام)…"
    COLUMNS = [("name_ar", "المفتاح"), ("account", "الحساب"), ("used_in", "بيتستخدم في"), ("status", "الحالة")]
    EXIT_LAST = True
    FORM_STRETCH, LIST_STRETCH = 0, 1

    def __init__(self, defaults: DefaultAccountService | None = None, parent: QWidget | None = None) -> None:
        self.defaults = defaults or DefaultAccountService()
        self.current_group = next(iter(GROUPS))
        super().__init__(parent)
        self.resize(1200, 720)
        self._open_first()

    # ---- البناء ------------------------------------------------------------
    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.suggest_button = self._button(SUGGEST_BUTTON, "toggle", self.suggest_account)
        self.suggest_button.setToolTip("بيحط أول حساب فرعي نشط بدور المفتاح ده في الدليل")
        self.suggest_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.suggest_button)

    def build_form(self, box: QGroupBox) -> None:
        row = QHBoxLayout(box)
        row.setContentsMargins(14, 20, 14, 12)
        row.setSpacing(24)
        right, left = QFormLayout(), QFormLayout()
        for form in (right, left):
            form.setSpacing(8)
            form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        self.key_name = self._readonly()
        self.group_name = self._readonly()
        self.account_combo = QComboBox()
        self.account_combo.setMinimumWidth(320)
        self.account_combo.setToolTip("حساب فرعي نشط من النوع المسموح للمفتاح ده")
        self.type_name = self._readonly()
        self.role_name = self._readonly()
        self.used_in = self._readonly()
        self.balance = self._readonly()
        self.status_label = QLabel("")
        self.status_label.setAlignment(RIGHT)
        right.addRow("المفتاح", self.key_name)
        right.addRow("القسم", self.group_name)
        right.addRow("الحساب *", self.account_combo)
        right.addRow("", self.status_label)
        left.addRow("النوع المسموح", self.type_name)
        left.addRow("الدور (الاقتراح)", self.role_name)
        left.addRow("بيتستخدم في", self.used_in)
        left.addRow("الرصيد", self.balance)
        row.addLayout(right, 1)                                   # RTL: أول عنصر = يمين
        row.addLayout(left, 1)

    @staticmethod
    def _readonly() -> QLineEdit:
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setStyleSheet("QLineEdit { background:#F1F5F9; color:#475569; }")
        return edit

    def _build_side(self) -> QFrame:
        side = QFrame()
        side.setObjectName("groupsSide")
        side.setFixedWidth(210)
        side.setStyleSheet(f"QFrame#groupsSide {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; "
                           "border-radius:8px; }")
        layout = QVBoxLayout(side)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(6)
        title = QLabel("الأقسام")
        title.setStyleSheet(f"color:{GREEN}; font-weight:900; border-bottom:2px solid {GREEN}; padding-bottom:4px;")
        layout.addWidget(title)
        self.groups_list = QListWidget()
        self.groups_list.setStyleSheet(
            "QListWidget { border:0; background:transparent; outline:0; }"
            "QListWidget::item { padding:8px 6px; border-radius:6px; font-weight:800; }"
            f"QListWidget::item:selected {{ background:#ECFDF3; color:{GREEN}; border-right:4px solid {GREEN}; }}")
        for code, (name, icon) in GROUPS.items():
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.UserRole, code)
            self.groups_list.addItem(item)
        self.groups_list.setCurrentRow(0)
        self.groups_list.currentItemChanged.connect(self._group_changed)
        layout.addWidget(self.groups_list, 1)
        self.total_label = QLabel("")
        self.total_label.setWordWrap(True)
        self.total_label.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        layout.addWidget(self.total_label)
        return side

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        content.addWidget(self._build_side())                     # RTL: أول عنصر = يمين
        column = QVBoxLayout()
        column.setSpacing(10)
        column.addWidget(list_box, 1)
        column.addWidget(form_box, 0)
        content.addLayout(column, 1)

    def editable_widgets(self) -> list[QWidget]:
        return [self.account_combo]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        self.groups_list.setEnabled(mode == "view")

    def update_extra_buttons(self) -> None:
        rec = self.current_record or {}
        editing = self.mode == "edit"
        self.suggest_button.setEnabled(editing and bool(rec.get("role_code")) and self.can("edit"))
        self.suggest_button.setToolTip("" if rec.get("role_code") or not editing
                                       else "المفتاح ده مالوش دور في الدليل — اختار الحساب بإيدك")

    # ---- الأقسام -----------------------------------------------------------
    def _group_changed(self, item: QListWidgetItem | None, _previous: Any = None) -> None:
        if item is None:
            return
        self.current_group = item.data(Qt.UserRole)
        if self.search.text().strip():
            self.search.clear()                                 # بيعمل refresh_list لوحده
        else:
            self.refresh_list()
        self._open_first()

    def _open_first(self) -> None:
        """أول مفتاح في القائمة (لو المفتاح المفتوح مش فيها)."""
        rows = self.table.rows
        if not rows or any(r["id"] == self.current_id for r in rows):
            return
        if self.load(rows[0]["id"]):
            self.table.select_id(rows[0]["id"])

    def _update_side(self) -> None:
        try:
            summary = self.defaults.group_summary()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            return
        for i in range(self.groups_list.count()):
            item = self.groups_list.item(i)
            code = item.data(Qt.UserRole)
            name, icon = GROUPS[code]
            s = summary.get(code, {"total": 0, "missing": 0})
            # من غير أقواس مع «ناقص» (القوس بعد الرقم بيتقلب في الـ RTL)
            tail = f"⚠ ناقص {s['missing']}" if s["missing"] else f"({s['total']})"
            item.setText(f"{icon}  {name}   {tail}")
            item.setForeground(QColor(MISSING_COLOR) if s["missing"] else QColor("#111827"))
            item.setToolTip(f"{s['total']} مفتاح — ناقص {s['missing']}" if s["missing"] else f"{s['total']} مفتاح")
        total = sum(s["total"] for s in summary.values())
        missing = sum(s["missing"] for s in summary.values())
        self.total_label.setText(f"{total} مفتاح · {total - missing} متحدد"
                                 + (f"\n⚠ ناقص {missing}" if missing else " ✔"))
        self.total_label.setStyleSheet(f"color:{MISSING_COLOR if missing else TEXT_MUTED}; font-weight:800;")

    # ---- القائمة -----------------------------------------------------------
    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "account":
            return account_label(row) or NOT_SET
        if key == "status":
            return status_text(row)
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        keys = [k for k, _t in self.COLUMNS]
        header = self.table.horizontalHeader()                    # عمود الحساب هو اللي بيتمد (مش الحالة)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(keys.index("account"), QHeaderView.Stretch)
        status_col, account_col = keys.index("status"), keys.index("account")
        for r, row in enumerate(self.table.rows):
            color = QColor(OK_COLOR if row.get("account_id") else MISSING_COLOR)
            for col in (status_col,) + (() if row.get("account_id") else (account_col,)):
                item = self.table.item(r, col)
                if item is not None:
                    item.setForeground(color)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
        self.table.resizeColumnToContents(status_col)              # بعد الخط العريض عشان «⚠ ناقص» مايتقصش
        self.table.setColumnWidth(status_col, self.table.columnWidth(status_col) + 24)
        rows = self.table.rows
        missing = sum(1 for r in rows if not r.get("account_id"))
        where = "نتايج البحث" if self.search.text().strip() else f"«{GROUPS[self.current_group][0]}»"
        self.count_label.setText(f"{where}: {len(rows)} مفتاح · {len(rows) - missing} متحدد · {missing} ناقص")
        self._update_side()

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.defaults.list_keys(self.current_group, keyword)

    def lookup_rows(self) -> list[dict]:
        return self.defaults.list_keys(None, "")

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.defaults.get_key(str(record_id))

    def _fill_choices(self, record: dict) -> None:
        """اختيارات الحساب = فرعي نشط من النوع المسموح (+ الحالي لو مش منهم)."""
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem(NOT_SET, None)
        try:
            choices = self.defaults.account_choices(record["account_type"])
        except Exception:  # noqa: BLE001
            choices = []
        for acc in choices:
            self.account_combo.addItem(f"{ltr(acc['code'])}  {acc['name_ar']}", int(acc["id"]))
        current = record.get("account_id")
        index = self.account_combo.findData(None if current is None else int(current))
        if index < 0 and current is not None:
            self.account_combo.addItem(account_label(record), int(current))
            index = self.account_combo.count() - 1
        self.account_combo.setCurrentIndex(max(0, index))
        self.account_combo.blockSignals(False)

    def fill_form(self, record: dict) -> None:
        name, icon = GROUPS.get(record.get("group_code"), ("", ""))
        self.key_name.setText(record.get("name_ar") or "")
        self.group_name.setText(f"{icon} {name}".strip())
        self._fill_choices(record)
        self.type_name.setText(f"{TYPE_NAMES.get(record.get('account_type'), '')} — فرعي نشط")
        self.role_name.setText(record.get("role_name") or "— مالوش دور (اختار بإيدك)")
        self.used_in.setText(record.get("used_in") or "")
        self.balance.setText(BALANCE_TEXT)
        ok = bool(record.get("account_id"))
        self.status_label.setText(f"{OK_TEXT}" if ok
                                  else f"{MISSING_TEXT} — أي مستند محتاجه هيترفض لحد ما يتحدد")
        self.status_label.setStyleSheet(f"color:{OK_COLOR if ok else MISSING_COLOR}; font-weight:800;")
        for edit in (self.key_name, self.group_name, self.type_name, self.role_name, self.used_in, self.balance):
            edit.setCursorPosition(0)

    def clear_form(self) -> None:
        for edit in (self.key_name, self.group_name, self.type_name, self.role_name, self.used_in, self.balance):
            edit.clear()
        self.account_combo.clear()
        self.status_label.clear()

    def collect(self) -> dict:
        return {"account_id": self.account_combo.currentData()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.defaults.save_key(str(record_id), payload["account_id"], expected_version=self.current_version)

    # ---- «اقترح من الدور» ---------------------------------------------------
    def suggest_account(self) -> None:
        if self.mode != "edit" or self.current_id is None:
            return
        try:
            suggestion = self.defaults.suggested_account(str(self.current_id))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, SUGGEST_BUTTON, exc)
            return
        if suggestion is None:
            messages.warn(self, SUGGEST_BUTTON, "مفيش حساب فرعي نشط بالدور ده في الدليل — اختار الحساب بإيدك.")
            return
        index = self.account_combo.findData(int(suggestion["id"]))
        if index >= 0:
            self.account_combo.setCurrentIndex(index)
