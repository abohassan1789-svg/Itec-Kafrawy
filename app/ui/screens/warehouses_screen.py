"""شاشة «المخازن» — النموذج 2 «كروت أرقام فوق + قائمة وفورم» (اختيار المستخدم 2026-11-15).

```
┌ [مخزن جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]             (عرض) ┐
├ [مخازن نشطة 2] [المخزن الافتراضي WH-001] [مخازن من غير أمين 1] [قيمة المخزون — المرحلة 6]        ┤
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ البيانات الأساسية ┐                 │ ┌ البحث والتسجيل ┐                       │
│   الكود WH-001 (لوحده) · الاسم* · EN   │   🔍 الاسم / الكود / الأمين / العنوان …     │
│   ☐ مخزن وهمي · ☐ المخزن الافتراضي     │   [الكل][النشطة][الموقوفة]                  │
│   ☐ نشط                               │   الكود│المخزن│الأمين│الحالة                 │
│ ┌ الأمين والمكان ┐                     │   📒 كل المخازن على حساب واحد 1105 …        │
│   أمين المخزن ▾ · العنوان · التليفون    │                                            │
│ ┌ المحاسبة ┐ حساب المخزون 1105 (واحد)   │                                            │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* الشاشة لتعريف المخزن بس — الأرصدة والحركة في المرحلة 6 · مفيش مركز تكلفة (رد المستخدم).
* «الافتراضي» واحد بس (تعليمه بيشيله من التاني) · «وهمي» من غير أثر محاسبي ومايبقاش افتراضي.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.warehouse_service import BALANCE_NOTE, PAGE_SIZE, VIRTUAL_NOTE, WarehouseService
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED, WARN = "#087443", "#64748B", "#B45309"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
NO_KEEPER = "— من غير أمين —"
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
# كروت الأرقام (زي النموذج 2): (المفتاح، العنوان، لون الشريط)
KPI_CARDS = (("active", "مخازن نشطة", GREEN), ("default", "المخزن الافتراضي", "#2563EB"),
             ("no_keeper", "مخازن من غير أمين", GREEN), ("value", "قيمة المخزون (EGP)", "#2563EB"))
KPI_STYLE = ("QFrame#kpi {{ background:#FFFFFF; border:1px solid #E2E8F0; border-right:4px solid {color}; "
             "border-radius:7px; }}")
DIM_NOTE = "📒 كل المخازن (ماعدا الوهمي) على حساب مخزون واحد{acc} — والمخزن «بُعد» على سطر القيد."


class WarehousesScreen(BaseScreen):
    MODULE = "inv"
    TARGET = "warehouses"
    TITLE = "المخازن"
    ICON = "🏬"
    FORM_TITLE = "البيانات الأساسية"
    NEW_TEXT = "مخزن جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الأمين أو العنوان… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "المخزن"), ("keeper_name", "الأمين"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, warehouses: WarehouseService | None = None, parent: QWidget | None = None) -> None:
        self.warehouses = warehouses or WarehouseService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._inventory: dict | None = None
        super().__init__(parent)
        self.layout().insertWidget(1, self.kpi_bar)                # تحت شريط الأدوات
        list_layout = self.count_label.parentWidget().layout()
        list_layout.addLayout(self.paging)
        list_layout.addWidget(self.dim_label)
        self._show_inventory_account()
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    @staticmethod
    def _form(box: QGroupBox) -> QFormLayout:
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        return form

    @staticmethod
    def _readonly(placeholder: str = "", ltr: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setReadOnly(True)
        if ltr:
            edit.setLayoutDirection(Qt.LeftToRight)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        return edit

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        return label

    def _build_kpis(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.kpi_values: dict[str, QLabel] = {}
        self.kpi_frames: dict[str, QFrame] = {}
        for key, title, color in KPI_CARDS:
            card = QFrame()
            card.setObjectName("kpi")
            card.setStyleSheet(KPI_STYLE.format(color=color))
            col = QVBoxLayout(card)
            col.setContentsMargins(10, 6, 10, 6)
            col.setSpacing(1)
            caption = QLabel(title)
            caption.setStyleSheet(f"color:{MUTED}; font-weight:800; border:none;")
            value = QLabel("—")
            value.setStyleSheet("font-size:15px; font-weight:900; border:none;")
            col.addWidget(caption)
            col.addWidget(value)
            row.addWidget(card, 1)
            self.kpi_values[key] = value
            self.kpi_frames[key] = card
        return bar

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = self._readonly("بيتولّد لوحده", ltr=True)
        self.code_edit.setToolTip("كود المخزن بيتولّد لوحده وقت الحفظ (WH-001، WH-002…) وثابت بعد كده")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("اسم المخزن (زي «مخزن الورشة»)")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setLayoutDirection(Qt.LeftToRight)
        self.is_virtual = QCheckBox("مخزن وهمي (من غير أثر محاسبي)")
        self.is_virtual.setToolTip("زي «المخزن الوهمي» في الأكسس: حركته كميات بس من غير قيد ولا تكلفة")
        self.is_virtual.toggled.connect(self._on_virtual)
        self.is_default = QCheckBox("المخزن الافتراضي (بيتملى لوحده في الفواتير)")
        self.is_default.setToolTip("مخزن واحد بس — تعليمه هنا بيشيله من المخزن التاني")
        self.is_active = QCheckBox("نشط")
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("النوع", self.is_virtual)
        form.addRow("الافتراضي", self.is_default)
        form.addRow("الحالة", self.is_active)

    def _build_keeper(self) -> QGroupBox:
        box = QGroupBox("الأمين والمكان")
        form = self._form(box)
        self.keeper = QComboBox()
        self.keeper.setToolTip("الموظفين النشطين اللي عليهم دور «أمين مخزن» (من شاشة الموظفين)")
        self.address = QLineEdit()
        self.address.setMaxLength(200)
        self.address.setPlaceholderText("عنوان المخزن (اختياري)")
        self.phone = QLineEdit()
        self.phone.setMaxLength(30)
        self.phone.setLayoutDirection(Qt.LeftToRight)
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("أمين المخزن", self.keeper)
        form.addRow("العنوان", self.address)
        form.addRow("التليفون", self.phone)
        form.addRow("ملاحظات", self.notes)
        form.addRow(self._hint("الأمين اختياري — موظف عليه دور «أمين مخزن»، والأمين الواحد ممكن يمسك كذا مخزن."))
        return box

    def _build_accounting(self) -> QGroupBox:
        box = QGroupBox("المحاسبة والأرصدة")
        form = self._form(box)
        self.account_view = self._readonly("")
        self.items_view = self._readonly("")
        self.value_view = self._readonly("")
        self.accounting_hint = self._hint("")
        form.addRow("حساب المخزون", self.account_view)
        form.addRow("أصناف عليها رصيد", self.items_view)
        form.addRow("القيمة", self.value_view)
        form.addRow(self.accounting_hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        self.kpi_bar = self._build_kpis()
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_keeper(), self._build_accounting()):
            align_form_right(box)
            right.addWidget(box)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)
        self._load_choices()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.toggle_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.toggle_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        self.status_buttons = QButtonGroup(self)
        self.status_buttons.setExclusive(True)
        for key, text in STATUS_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("status", key)
            self.status_buttons.addButton(chip)
            row.addWidget(chip)
        self.status_buttons.buttonClicked.connect(self._on_status)
        row.addStretch(1)
        layout.addLayout(row)
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
        self.dim_label = QLabel("")
        self.dim_label.setWordWrap(True)
        self.dim_label.setStyleSheet("background:#F5F3FF; border:1px dashed #C4B5FD; border-radius:6px; "
                                     "padding:5px 8px; color:#5B21B6; font-weight:700;")

    # ---- الاختيارات والحساب ----------------------------------------------------
    def _load_choices(self) -> None:
        """الأمناء بيتحمّلوا من جديد مع «جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            keepers = self.warehouses.keepers()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            keepers = []
        self.keeper.clear()
        self.keeper.addItem(NO_KEEPER, None)
        for e in keepers:
            self.keeper.addItem(f"{e['name_ar']} ({e['code']})", e["id"])

    def _show_inventory_account(self) -> None:
        try:
            self._inventory = self.warehouses.inventory_account()
        except Exception:  # noqa: BLE001
            self._inventory = None
        inv = self._inventory
        acc = f" ⁦{inv['code']}⁩ «{inv['name_ar']}»" if inv else ""
        self.dim_label.setText(DIM_NOTE.format(acc=acc))
        self._show_accounting()

    def _show_accounting(self) -> None:
        if self.is_virtual.isChecked():
            self.account_view.setText("— (وهمي: من غير قيد)")
            self.accounting_hint.setText(VIRTUAL_NOTE)
            self.accounting_hint.setStyleSheet(f"color:{WARN}; font-weight:800;")
            return
        inv = self._inventory
        self.account_view.setText(f"⁦{inv['code']}⁩ {inv['name_ar']} (واحد لكل المخازن)" if inv
                                  else "— (حدده في «الحسابات الافتراضية» ← المخزون)")
        self.accounting_hint.setText("كل المخازن على نفس الحساب والمخزن «بُعد» — الميزان فيه رقم واحد والتقرير "
                                     "بيفصّل بالمخزن. " + BALANCE_NOTE)
        self.accounting_hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")

    def _on_virtual(self, checked: bool) -> None:
        """الوهمي مايبقاش افتراضي."""
        if checked and self.mode in ("new", "edit"):
            self.is_default.setChecked(False)
        self.is_default.setEnabled(self.mode in ("new", "edit") and not checked)
        self._show_accounting()

    def _set_keeper(self, record: dict) -> None:
        kid = record.get("keeper_id")
        index = self.keeper.findData(kid) if kid is not None else 0
        if index < 0:                                              # أمين قديم مابقاش في القايمة (للعرض بس)
            self.keeper.addItem(f"{record.get('keeper_name') or ''} ({record.get('keeper_code') or ''})", kid)
            index = self.keeper.count() - 1
        self.keeper.setCurrentIndex(index)

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.is_virtual, self.is_default, self.is_active, self.keeper,
                self.address, self.phone, self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        self.is_default.setEnabled(editing and not self.is_virtual.isChecked())
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self._update_paging(editing)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        self._load_choices()
        try:
            defaults = self.warehouses.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        self.is_virtual.setChecked(False)
        self.is_default.setChecked(False)
        self.keeper.setCurrentIndex(0)
        self.set_mode("new")
        self._show_accounting()
        self.name_ar.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب السجل من جديد ويملا الأمين

    # ---- القائمة والصفحات والأرقام ---------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return keyword, self.status

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self.page = 1
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()                                      # المعروض اتخفى بالفلتر

    def _go_page(self, page: int) -> None:
        self.page = max(1, min(self.pages, page))
        self.refresh_list()

    def _update_paging(self, editing: bool = False) -> None:
        self.prev_button.setEnabled(not editing and self.page > 1)
        self.next_button.setEnabled(not editing and self.page < self.pages)
        self.page_label.setText(f"صفحة {self.page} من {self.pages}")

    def fetch_rows(self, keyword: str) -> list[dict]:
        query = self._query(keyword)
        if query != self._last_query:                              # بحث / فلتر جديد ← أول صفحة
            self._last_query = query
            self.page = 1
        self._counts = self.warehouses.count_warehouses(*query)
        self._stats = self.warehouses.stats()
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.warehouses.list_warehouses(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.warehouses.lookup_warehouses()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "name_ar":
            text = value or ""
            if row.get("is_virtual"):
                text = f"👻 {text}"
            return f"{text} ⭐" if row.get("is_default") else text
        if key == "keeper_name":
            return value or "—"
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def _show_stats(self) -> None:
        stats = getattr(self, "_stats", None)
        if stats is None or not hasattr(self, "kpi_values"):
            return
        virtual = f"  (منهم {stats['virtual']} وهمي)" if stats["virtual"] else ""
        self.kpi_values["active"].setText(f"{stats['active']}{virtual}")
        self.kpi_values["default"].setText(f"⁦{stats['default_code']}⁩ {stats['default_name']}"
                                           if stats["default_code"] else "— مفيش")
        self.kpi_values["no_keeper"].setText(str(stats["no_keeper"]))
        warn = stats["no_keeper"] > 0
        self.kpi_values["no_keeper"].setStyleSheet(
            f"font-size:15px; font-weight:900; border:none; color:{'#B91C1C' if warn else ACTIVE_COLOR};")
        self.kpi_frames["no_keeper"].setStyleSheet(KPI_STYLE.format(color="#DC2626" if warn else GREEN))
        self.kpi_values["value"].setText(f"{stats['value']:,.2f}")

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col = cols.index("is_active")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(ACTIVE_COLOR if row.get("is_active") else MUTED))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"عدد المخازن: {counts['total']} · نشطة {counts['active']} · "
                                     f"موقوفة {counts['inactive']}  ·  ⭐ = الافتراضي · 👻 = وهمي")
        self._show_stats()
        if hasattr(self, "prev_button"):
            self._update_paging(self.mode in ("new", "edit"))

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} المخزن «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.warehouses.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل المخزن." if new_state else
                          "تم إيقاف المخزن (مش هيتختار في أي مستند جديد).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفى من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفه.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.warehouses.get_warehouse(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key in ("name_ar", "name_en", "address", "phone", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self.is_virtual.setChecked(bool(record.get("is_virtual")))
        self.is_default.setChecked(bool(record.get("is_default")))
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self._set_keeper(record)
        self._show_accounting()
        stock = self.warehouses.stock_of(record.get("id"))
        self.items_view.setText(f"{stock['items']:,}" if stock else "0")
        self.value_view.setText("— (وهمي: كميات بس)" if record.get("is_virtual")
                                else f"{(stock or {}).get('value') or 0:,.2f}")

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.address, self.phone, self.notes):
            edit.clear()
        self.keeper.setCurrentIndex(0)
        self.is_virtual.setChecked(False)
        self.is_default.setChecked(False)
        self.is_active.setChecked(True)
        self._show_accounting()
        self.items_view.clear()
        self.value_view.clear()

    def collect(self) -> dict:
        return {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(), "address": self.address.text(),
                "phone": self.phone.text(), "notes": self.notes.text(), "keeper_id": self.keeper.currentData(),
                "is_virtual": self.is_virtual.isChecked(), "is_default": self.is_default.isChecked(),
                "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.warehouses.save_warehouse(None if record_id is None else int(record_id), payload,
                                              expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.warehouses.delete_warehouse(int(record_id), expected_version=self.current_version)
