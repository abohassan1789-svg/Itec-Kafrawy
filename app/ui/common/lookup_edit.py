"""خانة اختيار بالكتابة (طلب المستخدم 2026-11-16 في «القيود اليومية»: «عايز أكتب جوه الحقل على طول»).

خانة نص عادية (من غير قايمة منسدلة فوقها): بتكتب الكود أو أي جزء من الاسم والاقتراحات بتطلع **تحتها**
وانت بتكتب · Enter أو كليك على اقتراح بيختاره · سهم ↓ في خانة فاضية بيعرض الكل · لو كتبت كود كامل
(زي 5202) أو كلام بيطابق حاجة واحدة بس، بيتختار لوحده لما تسيب الخانة · الكلام اللي مالوش مطابق بيتعلّم أحمر
والقيمة بتبقى فاضية.

واجهة شبه ``QComboBox`` عشان الشاشات: ``currentData()`` · ``set_value(id)`` · ``set_items(rows, value)`` ·
إشارة ``value_changed(object)``.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QStringListModel, Qt, Signal
from PySide6.QtWidgets import QCompleter, QLineEdit, QWidget

BAD_STYLE = "QLineEdit { border:1px solid #DC2626; background:#FEF2F2; }"


class LookupEdit(QLineEdit):
    value_changed = Signal(object)

    def __init__(self, placeholder: str = "اكتب الكود أو الاسم…", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[Any, str, str]] = []      # (القيمة، الكود، النص الظاهر)
        self._value: Any = None
        self._placeholder = placeholder
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self._model = QStringListModel(self)
        self._completer = QCompleter(self._model, self)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.setMaxVisibleItems(12)
        self._completer.activated.connect(self._on_activated)
        self.setCompleter(self._completer)
        self.editingFinished.connect(self._resolve)
        self.textEdited.connect(self._on_edited)

    # ---- البيانات ----------------------------------------------------------
    def set_items(self, rows: list[tuple[Any, str, str]], value: Any = None, missing_text: str | None = None,
                  placeholder: str | None = None) -> None:
        """``rows`` = [(القيمة، الكود، النص)] · قيمة مش في القايمة (موقوفة) بتتضاف للعرض بس."""
        self._rows = list(rows)
        if value is not None and all(r[0] != value for r in self._rows):
            self._rows.append((value, "", f"{missing_text or value} (موقوف)"))
        self._model.setStringList([r[2] for r in self._rows])
        self.setPlaceholderText(placeholder or self._placeholder)
        self._set(value, emit=False)

    def set_value(self, value: Any) -> None:
        self._set(value, emit=True)

    def currentData(self) -> Any:  # noqa: N802 - نفس اسم QComboBox
        return self._value

    def _text_of(self, value: Any) -> str:
        return next((r[2] for r in self._rows if r[0] == value), "") if value is not None else ""

    def _set(self, value: Any, emit: bool) -> None:
        changed = value != self._value
        self._value = value if value is not None and self._text_of(value) else None
        self.blockSignals(True)
        self.setText(self._text_of(self._value))
        self.setCursorPosition(0)
        self.blockSignals(False)
        self.setStyleSheet("")
        if emit and changed:
            self.value_changed.emit(self._value)

    # ---- الكتابة ---------------------------------------------------------------
    def _on_edited(self, text: str) -> None:
        self.setStyleSheet("")
        if not text.strip() and self._value is not None:
            self._value = None
            self.value_changed.emit(None)

    def _on_activated(self, text: str) -> None:
        match = next((r[0] for r in self._rows if r[2] == text), None)
        self._set(match, emit=True)

    def _resolve(self) -> None:
        text = " ".join(self.text().split())
        if not text:
            self._set(None, emit=True)
            return
        if self._value is not None and text == self._text_of(self._value):
            return
        exact = [r for r in self._rows if r[2] == text or (r[1] and r[1].lower() == text.lower())]
        hits = exact or [r for r in self._rows if text.lower() in r[2].lower()]
        if len(hits) == 1:
            self._set(hits[0][0], emit=True)
            return
        old = self._value
        self._value = None
        self.setStyleSheet(BAD_STYLE)
        self.setToolTip("مفيش حاجة مطابقة — اكتب الكود أو اختار من الاقتراحات." if not hits
                        else f"فيه {len(hits)} مطابقين — كمّل الكتابة أو اختار من الاقتراحات.")
        if old is not None:
            self.value_changed.emit(None)

    def keyPressEvent(self, event) -> None:  # noqa: ANN001, N802 - Qt
        if event.key() == Qt.Key_Down and not self._completer.popup().isVisible():
            self._completer.setCompletionPrefix("" if self._value is not None else self.text())
            self._completer.complete()
            return
        super().keyPressEvent(event)
