"""جدول البيانات بشكل Hr GAD: هيدر أخضر بنص أبيض عريض، صفوف متبادلة، أول عمود أخضر فاتح،
من غير ترقيم صفوف، والخلايا مش قابلة للتعديل. كل صف شايل الـ id بتاعه."""

from __future__ import annotations

from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem, QWidget

from app.ui.common.theme import FIRST_COLUMN

Column = tuple[str, str]   # (المفتاح في الصف، العنوان)
Formatter = Callable[[str, Any, dict], str]


def default_format(_key: str, value: Any, _row: dict) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "نعم" if value else "لا"
    if hasattr(value, "strftime"):
        return value.strftime("%d-%m-%Y %I:%M %p") if hasattr(value, "hour") else value.strftime("%d-%m-%Y")
    return str(value)


class DataTable(QTableWidget):
    def __init__(self, columns: Sequence[Column], id_key: str = "id",
                 formatter: Formatter | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.columns = list(columns)
        self.id_key = id_key
        self.formatter = formatter or default_format
        self.rows: list[dict] = []
        self.setColumnCount(len(self.columns))
        self.setHorizontalHeaderLabels([title for _k, title in self.columns])
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)

    def set_rows(self, rows: Sequence[dict]) -> None:
        self.rows = list(rows)
        self.setUpdatesEnabled(False)
        self.clearContents()
        self.setRowCount(len(self.rows))
        first_bg = QColor(FIRST_COLUMN)
        for r, row in enumerate(self.rows):
            for c, (key, _title) in enumerate(self.columns):
                item = QTableWidgetItem(self.formatter(key, row.get(key), row))
                item.setTextAlignment(Qt.AlignCenter)
                if c == 0:
                    item.setData(Qt.UserRole, row.get(self.id_key))
                    item.setBackground(first_bg)
                self.setItem(r, c, item)
        self.resizeColumnsToContents()
        # لازم تتقفل وتتفتح تاني: من غيرها آخر عمود مابيتمدّش بعد ما الصفوف تقل (يفضل فراغ رمادي).
        self.horizontalHeader().setStretchLastSection(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.setUpdatesEnabled(True)

    def selected_index(self) -> int:
        """رقم الصف المختار (المختار فعلاً، وإلا الحالي)."""
        selected = self.selectionModel().selectedRows()
        return selected[0].row() if selected else self.currentRow()

    def current_id(self) -> Any:
        row = self.selected_index()
        if row < 0 or self.item(row, 0) is None:
            return None
        return self.item(row, 0).data(Qt.UserRole)

    def current_row_data(self) -> dict | None:
        row = self.selected_index()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def select_index(self, row: int) -> None:
        """يختار صف (``selectRow`` بيفشل أحياناً مع الاتجاه من اليمين، فبنستخدم ``setCurrentCell``)."""
        if 0 <= row < self.rowCount():
            self.setCurrentCell(row, 0)

    def select_id(self, record_id: Any) -> bool:
        for r in range(self.rowCount()):
            item = self.item(r, 0)
            if item is not None and item.data(Qt.UserRole) == record_id:
                self.select_index(r)
                self.scrollToItem(item)
                return True
        return False
