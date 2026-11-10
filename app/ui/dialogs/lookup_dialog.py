"""نافذة البحث (F1) — جدول بيتفلتر وأنت بتكتب، والاختيار بالدبل كليك أو Enter."""

from __future__ import annotations

from typing import Any, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from app.ui.common.data_table import Column, DataTable, Formatter
from app.ui.common.theme import TEXT_MUTED, toolbar_button_style


def _matches(row: dict, columns: Sequence[Column], text: str, formatter) -> bool:
    if not text:
        return True
    needle = text.casefold()
    return any(needle in str(formatter(k, row.get(k), row)).casefold() for k, _t in columns)


class LookupDialog(QDialog):
    def __init__(self, title: str, columns: Sequence[Column], rows: Sequence[dict],
                 formatter: Formatter | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"بحث — {title}")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(760, 520)
        self.all_rows = list(rows)
        self.selected_id: Any = None
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 اكتب للبحث…")
        layout.addWidget(self.search)
        self.table = DataTable(columns, formatter=formatter)
        layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        bottom.addWidget(self.count, 1)
        choose = QPushButton("اختيار")
        choose.setStyleSheet(toolbar_button_style("primary"))
        close = QPushButton("إغلاق")
        close.setStyleSheet(toolbar_button_style("exit"))
        bottom.addWidget(choose)
        bottom.addWidget(close)
        layout.addLayout(bottom)

        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._choose)
        self.table.doubleClicked.connect(lambda _i: self._choose())
        choose.clicked.connect(self._choose)
        close.clicked.connect(self.reject)
        self._filter()
        self.search.setFocus()

    def _filter(self) -> None:
        text = self.search.text().strip()
        rows = [r for r in self.all_rows if _matches(r, self.table.columns, text, self.table.formatter)]
        self.table.set_rows(rows)
        if rows:
            self.table.select_index(0)
        self.count.setText(f"عدد السجلات: {len(rows)}")

    def _choose(self) -> None:
        record_id = self.table.current_id()
        if record_id is None:
            return
        self.selected_id = record_id
        self.accept()
