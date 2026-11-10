"""Base Screen — النموذج 1 «زي Hr GAD بالظبط» (اختيار المستخدم 2026-11-07).

```
┌──────────────────────────────────────────────────────────────────────┐
│ [جديد][تعديل][حفظ][حذف][إلغاء][تحديث][خروج] [إضافي…]        (عرض) │ ← شريط أدوات أبيض
├──────────────────────────────────────┬───────────────────────────────┤
│ ┌ بيانات … ──────────────────────┐   │ ┌ البحث والتسجيل ──────────┐ │
│ │ الحقول                          │   │ │ [بحث…] [فلتر]              │ │
│ └─────────────────────────────────┘   │ │ ╔═ هيدر أخضر ═══════════╗ │ │
│                                        │ │ عدد السجلات المعروضة: N   │ │
└──────────────────────────────────────┴───────────────────────────────┘
```
* 3 أوضاع: **عرض / جديد / تعديل** — الحقول مقفولة في العرض.
* **F1** = نافذة بحث عن السجل.
* كل زرار بيتفعّل/يتعطّل حسب الوضع **وحسب صلاحية المستخدم** (``module.target.action``)، والزرار
  اللي إجراؤه مش متسجل للشاشة في ``permission_registry`` مابيظهرش أصلاً.
* قبل الحفظ «هل تريد الحفظ؟» (الافتراضي «لا») ← «تم الحفظ بنجاح».
* **كل حاجة في الفورم محاذية يمين** (اختيار المستخدم 2026-11-07): التسميات + الكلام جوه الخانات حتى لو
  إنجليزي (اسم مستخدم، بريد، هاتف) — بتتعمل لوحدها بعد ``build_form`` في ``align_form_right``.

* **حماية التعديل في نفس الوقت (المرحلة 1ج — أكتر من جهاز على نفس الشاشة):** وقت فتح السجل الـ Base بيحفظ
  الإصدار (``current_version`` = عمود ``VERSION_FIELD`` في السجل، الافتراضي ``row_version``)، و«تعديل»
  بيجيب السجل من جديد الأول عشان تعدّل على آخر نسخة. الشاشة بتبعت ``expected_version=self.current_version``
  للـ Service مع الحفظ والمسح وأي إجراء على السجل. لو جهاز تاني حفظ قبلك الـ Service بيطلّع
  ``ConcurrentEditError`` ← الرسالة بتطلع **والشاشة بتفضل في وضع التعديل باللي كتبته** وتسألك:
  «تحديث» (تشوف التعديل الجديد) ولا «رجوع للتعديل» (تفضل، وتقدر «إلغاء» بعدين). في وضع العرض
  (حذف / قفل / إيقاف…) الرسالة بتطلع والسجل بيتحدّث لوحده (``report_error``).

الشاشة الفعلية بتورث الكلاس ده وبتعرّف: ``MODULE``, ``TARGET``, ``TITLE``, ``COLUMNS`` والدوال
``build_form`` / ``fetch_rows`` / ``fetch_record`` / ``fill_form`` / ``clear_form`` / ``collect`` /
``save_record`` (و``delete_record`` لو الشاشة فيها حذف). الشاشة واجهة بس — كل حاجة عن طريق Service.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.security.session import SESSION
from app.services import permission_registry as reg
from app.services.errors import ConcurrentEditError
from app.ui.common import messages
from app.ui.common.data_table import Column, DataTable, default_format
from app.ui.common.theme import BADGE_BG, BADGE_BORDER, BADGE_TEXT, BORDER_CARD, TEXT_MUTED, toolbar_button_style

MODE_NAMES = {"view": "عرض", "new": "جديد", "edit": "تعديل"}
EDITING = ("new", "edit")
# يمين «مطلق»: من غير AlignAbsolute الـ RTL بيقلب AlignRight لشمال (التسميات)، والنص الإنجليزي
# بيتحاذى حسب اتجاهه هو (الخانات) — فالاتنين كانوا بيطلعوا شمال.
RIGHT = Qt.AlignRight | Qt.AlignAbsolute | Qt.AlignVCenter


def align_form_right(box: QWidget) -> None:
    """التسميات والكلام جوه الخانات كله يمين في الفورم (أي لغة)."""
    for form in box.findChildren(QFormLayout):
        form.setLabelAlignment(RIGHT)
    for edit in box.findChildren(QLineEdit):
        edit.setAlignment(RIGHT)
    for edit in box.findChildren(QPlainTextEdit) + box.findChildren(QTextEdit):
        option = edit.document().defaultTextOption()
        option.setAlignment(Qt.AlignRight | Qt.AlignAbsolute)
        option.setTextDirection(Qt.RightToLeft)
        edit.document().setDefaultTextOption(option)


class BaseScreen(QWidget):
    MODULE = ""
    TARGET = ""
    TITLE = ""
    ICON = ""
    FORM_TITLE = "البيانات"
    LIST_TITLE = "البحث والتسجيل"
    SEARCH_PLACEHOLDER = "🔍 بحث…"
    NEW_TEXT = "جديد"
    COLUMNS: list[Column] = []
    FORM_STRETCH, LIST_STRETCH = 11, 10
    EXIT_LAST = False              # True = «خروج» آخر زرار (بعد الأزرار الإضافية و«تصدير إكسل»)
    VERSION_FIELD = "row_version"  # عمود الإصدار في السجل (Optimistic Locking — المرحلة 1ج)

    record_saved = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.mode = "view"
        self.current_id: Any = None
        self.current_record: dict | None = None
        self.current_version: int | None = None    # الإصدار اللي السجل اتفتح بيه (بيتبعت مع الحفظ والمسح)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle(f"{self.ICON}  {self.TITLE}".strip())
        self.setMinimumSize(1000, 600)
        self._actions = set(reg.find_target(self.MODULE, self.TARGET).action_codes())
        self._build_ui()
        self.lookup_shortcut = QShortcut(QKeySequence("F1"), self)
        self.lookup_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.lookup_shortcut.activated.connect(self.open_lookup)
        self.refresh_list()
        self.set_mode("view")

    # ---- الصلاحيات ---------------------------------------------------------
    def code(self, action: str) -> str:
        return reg.permission_code(self.MODULE, self.TARGET, action)

    def has_action(self, action: str) -> bool:
        return action in self._actions

    def can(self, action: str) -> bool:
        return self.has_action(action) and SESSION.can(self.code(action))

    # ---- البناء ------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        root.addWidget(self._build_toolbar())
        content = QHBoxLayout()
        content.setSpacing(10)
        form_box = QGroupBox(self.FORM_TITLE)
        form_box.setObjectName("formBox")
        self.build_form(form_box)
        align_form_right(form_box)
        self.arrange_content(content, form_box, self._build_list_box())
        root.addLayout(content, 1)

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        """ترتيب الفورم والقائمة (الافتراضي: الفورم يمين والقائمة شمال). الشاشة ممكن تغيّره."""
        content.addWidget(form_box, self.FORM_STRETCH)            # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

    def _button(self, text: str, kind: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setFixedHeight(38)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(toolbar_button_style(kind))
        bold = button.font()
        bold.setBold(True)
        button.setFont(bold)                     # المقاس يتحسب على الخط العريض عشان الكلام مايتقصش
        button.setMinimumWidth(button.fontMetrics().horizontalAdvance(text) + 30)
        button.clicked.connect(slot)
        return button

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setStyleSheet(f"QFrame#toolbar {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; border-radius:7px; }}")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        self.new_button = self._button(self.NEW_TEXT, "new", self.new_record)
        self.edit_button = self._button("تعديل", "edit", self.edit_record)
        self.save_button = self._button("حفظ", "save", self.save)
        self.delete_button = self._button("حذف", "delete", self.delete)
        self.cancel_button = self._button("إلغاء", "cancel", self.cancel)
        self.refresh_button = self._button("تحديث", "refresh", self.refresh_screen)
        self.exit_button = self._button("خروج", "exit", self.close)
        self.export_button = self._button("تصدير إكسل", "export", self.export_excel)
        shown = [
            (self.new_button, self.has_action("create")),
            (self.edit_button, self.has_action("edit")),
            (self.save_button, self.has_action("save")),
            (self.delete_button, self.has_action("delete")),
            (self.cancel_button, self.has_action("create") or self.has_action("edit")),
            (self.refresh_button, True),
        ]
        if not self.EXIT_LAST:
            shown.append((self.exit_button, True))
        for button, visible in shown:
            layout.addWidget(button)
            button.setVisible(visible)
        self.add_toolbar_buttons(layout)
        layout.addWidget(self.export_button)
        self.export_button.setVisible(self.has_action("export"))
        if self.EXIT_LAST:
            layout.addWidget(self.exit_button)
        layout.addStretch(1)
        self.mode_badge = QLabel(MODE_NAMES["view"])
        self.mode_badge.setAlignment(Qt.AlignCenter)
        self.mode_badge.setMinimumWidth(100)
        self.mode_badge.setStyleSheet(
            f"background:{BADGE_BG}; color:{BADGE_TEXT}; border:1px solid {BADGE_BORDER}; "
            "border-radius:7px; padding:8px 12px; font-weight:900;")
        layout.addWidget(self.mode_badge)
        return bar

    def _build_list_box(self) -> QGroupBox:
        box = QGroupBox(self.LIST_TITLE)
        layout = QVBoxLayout(box)
        layout.setContentsMargins(10, 16, 10, 8)
        layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.SEARCH_PLACEHOLDER)
        self.search.textChanged.connect(self.refresh_list)
        layout.addWidget(self.search)
        self.add_list_filters(layout)
        self.table = DataTable(self.COLUMNS, formatter=self.format_cell)
        self.table.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.table, 1)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet(f"color:{TEXT_MUTED}; font-weight:700;")
        layout.addWidget(self.count_label)
        return box

    # ---- نقاط بتعرّفها الشاشة ---------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        raise NotImplementedError

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        """أزرار إضافية للشاشة (بعد الأزرار الأساسية)."""

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        """فلاتر إضافية تحت خانة البحث."""

    def fetch_rows(self, keyword: str) -> list[dict]:
        raise NotImplementedError

    def lookup_rows(self) -> list[dict]:
        """صفوف نافذة البحث F1 (الافتراضي = نفس القائمة من غير كلمة بحث)."""
        return self.fetch_rows("")

    def fetch_record(self, record_id: Any) -> dict | None:
        raise NotImplementedError

    def fill_form(self, record: dict) -> None:
        raise NotImplementedError

    def clear_form(self) -> None:
        raise NotImplementedError

    def collect(self) -> dict:
        raise NotImplementedError

    def save_record(self, payload: dict, record_id: Any) -> Any:
        """بيحفظ ويرجّع id السجل."""
        raise NotImplementedError

    def delete_record(self, record_id: Any) -> None:
        raise NotImplementedError

    def editable_widgets(self) -> list[QWidget]:
        """الحقول اللي بتتفتح في «جديد/تعديل»."""
        return []

    def new_only_widgets(self) -> list[QWidget]:
        """حقول بتتفتح في «جديد» بس (زي اسم المستخدم)."""
        return []

    def update_extra_buttons(self) -> None:
        """تفعيل/تعطيل الأزرار الإضافية حسب الوضع والصلاحية."""

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        return default_format(key, value, row)

    # ---- الأوضاع -----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        self.mode = mode
        editing = mode in EDITING
        for widget in self.editable_widgets():
            widget.setEnabled(editing)
        for widget in self.new_only_widgets():
            widget.setEnabled(mode == "new")
        has_record = self.current_id is not None
        self.new_button.setEnabled(not editing and self.can("create"))
        self.edit_button.setEnabled(not editing and has_record and self.can("edit"))
        self.save_button.setEnabled(editing and self.can("save")
                                    and self.can("create" if mode == "new" else "edit"))
        self.delete_button.setEnabled(not editing and has_record and self.can("delete"))
        self.cancel_button.setEnabled(editing)
        self.refresh_button.setEnabled(not editing)
        self.export_button.setEnabled(not editing and self.can("export"))
        self.search.setEnabled(not editing)
        self.table.setEnabled(not editing)
        self.mode_badge.setText(MODE_NAMES.get(mode, mode))
        self.update_extra_buttons()

    # ---- القائمة -----------------------------------------------------------
    def refresh_list(self) -> None:
        try:
            rows = self.fetch_rows(self.search.text().strip())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
            rows = []
        self.table.blockSignals(True)
        self.table.set_rows(rows)
        self.table.blockSignals(False)
        self.count_label.setText(f"عدد السجلات المعروضة: {len(rows)}")
        if self.current_id is not None and self.mode == "view":
            self.table.blockSignals(True)
            self.table.select_id(self.current_id)
            self.table.blockSignals(False)

    def refresh_screen(self) -> None:
        self.refresh_list()
        if self.current_id is not None and self.mode == "view":
            self.load(self.current_id)

    def _on_selection(self) -> None:
        if self.mode in EDITING:
            return
        record_id = self.table.current_id()
        if record_id is not None and record_id != self.current_id:
            self.load(record_id)

    def load(self, record_id: Any) -> bool:
        try:
            record = self.fetch_record(record_id)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل السجل", exc)
            return False
        if record is None:
            self.show_empty()
            return False
        self.current_id = record_id
        self.current_record = record
        version = record.get(self.VERSION_FIELD)
        self.current_version = None if version is None else int(version)
        self.fill_form(record)
        self.set_mode("view")
        return True

    def show_empty(self) -> None:
        self.current_id = None
        self.current_record = None
        self.current_version = None
        self.clear_form()
        self.table.clearSelection()
        self.set_mode("view")

    # ---- الأزرار -----------------------------------------------------------
    def new_record(self) -> None:
        if not self.can("create"):
            return
        self.current_id = None
        self.current_record = None
        self.current_version = None
        self.table.clearSelection()
        self.clear_form()
        self.set_mode("new")

    def edit_record(self) -> None:
        """بيجيب السجل من جديد الأول (آخر نسخة + آخر إصدار) وبعدين يفتح التعديل."""
        if self.current_id is None or not self.can("edit"):
            return
        if self.load(self.current_id):
            self.set_mode("edit")

    def cancel(self) -> None:
        if self.mode not in EDITING:
            return
        if self.current_id is not None:
            self.load(self.current_id)
        else:
            self.show_empty()

    def save(self) -> None:
        if self.mode not in EDITING or not self.save_button.isEnabled():
            return
        try:
            payload = self.collect()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر الحفظ", exc)
            return
        if not messages.ask_save(self):
            return
        try:
            saved_id = self.save_record(payload, None if self.mode == "new" else self.current_id)
        except ConcurrentEditError as exc:
            self.on_conflict(exc)                      # الفورم زي ما هو (مفيش حاجة اتحفظت ولا اتمسحت)
            return
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر الحفظ", exc)
            return
        self.current_id = saved_id
        self.mode = "view"
        self.refresh_list()
        if not self.load(saved_id):
            self.show_empty()
        messages.show_saved(self)
        self.record_saved.emit(saved_id)

    def delete(self) -> None:
        if self.current_id is None or not self.can("delete"):
            return
        if not messages.ask_yes_no(self, "تأكيد الحذف", "هل تريد حذف السجل المحدد؟"):
            return
        try:
            self.delete_record(self.current_id)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر الحذف", exc)
            return
        self.show_empty()
        self.refresh_list()

    # ---- التعارض (جهاز تاني حفظ قبلك) ---------------------------------------
    def on_conflict(self, exc: ConcurrentEditError) -> None:
        """وقت الحفظ: الرسالة + «تحديث» ولا «رجوع للتعديل». الرجوع = الشاشة في التعديل باللي كتبته."""
        if messages.ask_conflict(self, exc):
            self.reload_after_conflict()

    def reload_after_conflict(self) -> None:
        """«تحديث» بعد التعارض: القائمة + آخر نسخة من السجل (أو فاضي لو اتمسح)."""
        self.mode = "view"
        self.refresh_list()
        if self.current_id is None or not self.load(self.current_id):
            self.show_empty()

    def report_error(self, title: str, exc: BaseException) -> None:
        """أخطاء الإجراءات في وضع العرض (حذف / قفل / إيقاف…): الرسالة، ولو تعارض ← السجل يتحدّث لوحده."""
        if isinstance(exc, ConcurrentEditError) and self.mode not in EDITING:
            self.reload_after_conflict()
            messages.warn(self, title, f"{messages.error_text(exc)}\n\n{messages.REFRESHED_NOTE}")
            return
        messages.show_error(self, title, exc)

    def open_lookup(self) -> None:
        if self.mode in EDITING:
            return
        from app.ui.dialogs.lookup_dialog import LookupDialog

        try:
            rows = self.lookup_rows()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر البحث", exc)
            return
        dialog = LookupDialog(self.TITLE, self.COLUMNS, rows, self.format_cell, self)
        if dialog.exec() and dialog.selected_id is not None:
            self.load(dialog.selected_id)
            if not self.table.select_id(dialog.selected_id):
                self.table.clearSelection()

    def export_excel(self) -> None:
        if not self.can("export"):
            return
        path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل", f"{self.TITLE}.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            export_rows_to_excel(Path(path), self.TITLE, self.COLUMNS, self.table.rows, self.format_cell)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", f"اتصدّر {len(self.table.rows)} سجل.")

    # ---- القفل -------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt
        if self.mode in EDITING and not messages.ask_yes_no(
                self, "خروج", "فيه بيانات لسه متحفظتش. عايز تخرج من غير حفظ؟"):
            event.ignore()
            return
        super().closeEvent(event)


def export_rows_to_excel(path: Path, title: str, columns: list[Column], rows: list[dict], formatter) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = title[:31] or "Sheet1"
    ws.sheet_view.rightToLeft = True
    ws.append([t for _k, t in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="137A38")
        cell.alignment = Alignment(horizontal="center")
    for row in rows:
        ws.append([formatter(k, row.get(k), row) for k, _t in columns])
    for index, (key, t) in enumerate(columns, start=1):
        width = max([len(t)] + [len(str(formatter(key, r.get(key), r))) for r in rows])
        ws.column_dimensions[ws.cell(1, index).column_letter].width = min(max(10, width + 2), 50)
    wb.save(path)
