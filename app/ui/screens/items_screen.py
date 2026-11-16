"""شاشة «الأصناف» — النموذج 3 «كروت أرقام فوق + قائمة وفورم» (اختيار المستخدم 2026-11-16 + ردوده).

```
┌ [صنف جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]                  (عرض) ┐
├ [أصناف نشطة 3,117] [أصناف خدمية 12] [من غير نوع 24] [تحت حد الطلب — المرحلة 6]                    ┤
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات الصنف ┐                       │ ┌ البحث والتسجيل ┐                       │
│   الكود ITM-0001 · الاسم* · التاني      │   🔍 الاسم / الكود / الوصف / الماركة…       │
│   الوصف · النوع ▾ · الماركة ▾ · نشط      │   [الكل][النشطة][الموقوفة] [كله][بضاعة][خدمي] │
│ ┌ الوحدات ┐ الأساسية ▾ + كرتونة = 12     │   الكود│الصنف│النوع│الوحدة│الحالة            │
│ ┌ البيع والمخزن ┐ خدمي · مخزن · ضريبة…   │   › السابق  صفحة 1 من 32  التالي ‹          │
│ ┌ الحسابات (من النوع) ┐                  │                                            │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* الاسم مايتكررش في نفس النوع — في نوع تاني **تحذير بس** (تحت الاسم وقت الكتابة + سؤال قبل الحفظ).
* الحسابات من النوع (مفيش حساب على الصنف) · الخدمي مالوش مخزن ولا حد طلب · الرصيد مع دفتر المخزون.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.item_service import BALANCE_NOTE, PAGE_SIZE, ItemService, category_path
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN, toolbar_button_style

ACTIVE_COLOR, MUTED, WARN, BAD, SERVICE_COLOR = "#087443", "#64748B", "#B45309", "#B91C1C", "#9D174D"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
KIND_CHIPS = (("all", "كله"), ("goods", "بضاعة"), ("service", "خدمي"))
NO_CATEGORY, NO_BRAND = "— من غير نوع —", "— من غير ماركة —"
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
KPI_CARDS = (("active", "أصناف نشطة", GREEN), ("service", "أصناف خدمية", "#2563EB"),
             ("no_category", "من غير نوع", GREEN), ("reorder", "تحت حد الطلب", "#DC2626"))
KPI_STYLE = ("QFrame#kpi {{ background:#FFFFFF; border:1px solid #E2E8F0; border-right:4px solid {color}; "
             "border-radius:7px; }}")


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def fmt_amount(value: Any) -> str:
    if value is None or value == "":
        return ""
    d = Decimal(str(value)).normalize()
    text = f"{d:,f}"
    return text


class ItemsScreen(BaseScreen):
    MODULE = "inv"
    TARGET = "items"
    TITLE = "الأصناف"
    ICON = "📦"
    FORM_TITLE = "بيانات الصنف"
    NEW_TEXT = "صنف جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الوصف أو الماركة… (F1)"
    COLUMNS = [("code", "الكود"), ("name", "الصنف"), ("category_name", "النوع"), ("unit_name", "الوحدة"),
               ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, service: ItemService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or ItemService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self.kind = "all"
        self._last_query: tuple | None = None
        self._choices: dict[str, list[dict]] = {}
        super().__init__(parent)
        self.layout().insertWidget(1, self.kpi_bar)                # تحت شريط الأدوات
        list_layout = self.count_label.parentWidget().layout()
        list_layout.addLayout(self.paging)
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
    def _readonly(placeholder: str = "", ltr_text: bool = False) -> QLineEdit:
        edit = QLineEdit()
        edit.setReadOnly(True)
        if ltr_text:
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
        self.code_edit = self._readonly("بيتولّد لوحده", ltr_text=True)
        self.code_edit.setToolTip("كود الصنف بيتولّد لوحده وقت الحفظ (ITM-0001…) وثابت بعد كده")
        self.name = QLineEdit()
        self.name.setMaxLength(200)
        self.name.setPlaceholderText("اسم الصنف — عربي أو إنجليزي")
        self.name.textEdited.connect(self._check_name)
        self.name_hint = QLabel("")
        self.name_hint.setWordWrap(True)
        self.name_hint.hide()
        self.alt_name = QLineEdit()
        self.alt_name.setMaxLength(200)
        self.alt_name.setPlaceholderText("اختياري — الاسم بلغة تانية")
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.category = QComboBox()
        self.category.setToolTip("نوع الصنف (من «أنواع الأصناف») — الحسابات بتيجي منه")
        self.category.currentIndexChanged.connect(self._on_category)
        self.brand = QComboBox()
        self.is_active = QCheckBox("نشط")
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name)
        form.addRow(self.name_hint)
        form.addRow("الاسم التاني", self.alt_name)
        form.addRow("الوصف", self.description)
        form.addRow("النوع", self.category)
        form.addRow("الماركة", self.brand)
        form.addRow("الحالة", self.is_active)

    def _build_units(self) -> QGroupBox:
        box = QGroupBox("الوحدات")
        form = self._form(box)
        self.unit = QComboBox()
        self.unit.setToolTip("المخزون والتكلفة دايماً بالوحدة دي")
        self.unit.currentIndexChanged.connect(self._sync_unit_labels)
        self.units_table = QTableWidget(0, 3)
        self.units_table.setHorizontalHeaderLabels(["الوحدة التانية", "= كام وحدة أساسية", ""])
        self.units_table.verticalHeader().setVisible(False)
        self.units_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.units_table.setMinimumHeight(110)
        header = self.units_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.add_unit_button = QPushButton("+ وحدة تانية")
        self.add_unit_button.setCursor(Qt.PointingHandCursor)
        self.add_unit_button.setStyleSheet(toolbar_button_style("new"))
        self.add_unit_button.clicked.connect(lambda: self._add_unit_row())
        row = QHBoxLayout()
        row.addWidget(self.add_unit_button)
        row.addStretch(1)
        form.addRow("الوحدة الأساسية *", self.unit)
        form.addRow(self.units_table)
        form.addRow(row)
        form.addRow(self._hint("زي: كرتونة = 12 عدد · لفة = 100 متر — المخزون دايماً بالأساسية والفاتورة بتحوّل لوحدها."))
        return box

    def _build_sales(self) -> QGroupBox:
        box = QGroupBox("البيع والمخزن")
        form = self._form(box)
        self.is_service = QCheckBox("صنف خدمي (من غير مخزون — زي ساعة تركيب)")
        self.is_service.toggled.connect(self._on_service)
        self.warehouse = QComboBox()
        self.warehouse.setToolTip("المخزن اللي بيتملى في الفواتير — الفاضي = المخزن الافتراضي العام")
        self.tax = QComboBox()
        self.tax.setToolTip("الافتراضي ض.ق.م 14% — ويتغيّر للصنف (صفري / معفي)")
        self.sale_price = QLineEdit()
        self.sale_price.setLayoutDirection(Qt.LeftToRight)
        self.sale_price.setPlaceholderText("اختياري — بيتملى في الفاتورة ويتغيّر")
        self.reorder_level = QLineEdit()
        self.reorder_level.setLayoutDirection(Qt.LeftToRight)
        self.reorder_level.setPlaceholderText("اختياري — تنبيه لما الرصيد يقل عنه")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("النوع", self.is_service)
        form.addRow("المخزن الافتراضي", self.warehouse)
        form.addRow("كود الضريبة *", self.tax)
        form.addRow("سعر البيع", self.sale_price)
        form.addRow("حد الطلب", self.reorder_level)
        form.addRow("ملاحظات", self.notes)
        form.addRow(self._hint(BALANCE_NOTE))
        return box

    def _build_accounts(self) -> QGroupBox:
        box = QGroupBox("الحسابات (من نوع الصنف)")
        form = self._form(box)
        self.account_views: dict[str, QLineEdit] = {}
        for kind, label in (("inventory", "المخزون"), ("sales", "المبيعات"), ("cogs", "تكلفة المبيعات"),
                            ("sales_ret", "المردودات")):
            view = self._readonly("")
            self.account_views[kind] = view
            form.addRow(label, view)
        form.addRow(self._hint("الحسابات بتتغيّر من شاشة «أنواع الأصناف» (أو «الحسابات الافتراضية» للصنف اللي من غير نوع)."))
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        self.kpi_bar = self._build_kpis()
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_units(), self._build_sales(), self._build_accounts()):
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

    def _chips(self, layout: QHBoxLayout, chips: tuple, current: str, slot) -> QButtonGroup:
        group = QButtonGroup(self)
        group.setExclusive(True)
        for key, text in chips:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == current)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            group.addButton(chip)
            layout.addWidget(chip)
        group.buttonClicked.connect(slot)
        return group

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()
        row.setSpacing(6)
        self.status_buttons = self._chips(row, STATUS_CHIPS, "all", self._on_status)
        row.addSpacing(12)
        self.kind_buttons = self._chips(row, KIND_CHIPS, "all", self._on_kind)
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

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر). الموقوف مابيظهرش للاختيار."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"categories": [], "brands": [], "units": [], "warehouses": [], "taxes": []}
        ch = self._choices
        default_wh = next((w for w in ch["warehouses"] if w["is_default"]), None)
        fill = (
            (self.category, NO_CATEGORY, [(c["id"], c["path"]) for c in ch["categories"] if c["is_active"]]),
            (self.brand, NO_BRAND, [(b["id"], b["name"]) for b in ch["brands"] if b["is_active"]]),
            (self.unit, None, [(u["id"], f"{u['name_ar']} ({u['code']})") for u in ch["units"] if u["is_active"]]),
            (self.warehouse, "— الافتراضي العام" + (f" ({default_wh['name_ar']})" if default_wh else "") + " —",
             [(w["id"], f"{w['name_ar']} ({w['code']})") for w in ch["warehouses"] if w["is_active"]]),
            (self.tax, None, [(t["id"], f"{t['name_ar']} ({t['code']})") for t in ch["taxes"] if t["is_active"]]),
        )
        for combo, empty, rows in fill:
            combo.blockSignals(True)
            combo.clear()
            if empty is not None:
                combo.addItem(empty, None)
            for value, text in rows:
                combo.addItem(text, value)
            combo.blockSignals(False)

    @staticmethod
    def _select(combo: QComboBox, value: Any, text: str | None = None) -> None:
        """يختار القيمة — ولو مش في القايمة (موقوفة) بيضيفها للعرض."""
        combo.blockSignals(True)
        if value is not None and combo.findData(value) < 0:
            combo.addItem(f"{text or value} (موقوف)", value)
        combo.setCurrentIndex(max(0, combo.findData(value)))
        combo.blockSignals(False)

    def _unit_text(self, unit_id: Any) -> str:
        u = next((x for x in self._choices.get("units", []) if x["id"] == unit_id), None)
        return u["name_ar"] if u else ""

    def _add_unit_row(self, unit_id: Any = None, factor: Any = None, unit_text: str = "") -> None:
        r = self.units_table.rowCount()
        self.units_table.insertRow(r)
        combo = QComboBox()
        combo.addItem("— اختار —", None)
        for u in self._choices.get("units", []):
            if u["is_active"]:
                combo.addItem(f"{u['name_ar']} ({u['code']})", u["id"])
        self._select(combo, unit_id, unit_text)
        edit = QLineEdit(fmt_amount(factor))
        edit.setLayoutDirection(Qt.LeftToRight)
        edit.setPlaceholderText("12")
        remove = QPushButton("✕")
        remove.setCursor(Qt.PointingHandCursor)
        remove.setToolTip("شيل الوحدة دي")
        remove.setStyleSheet(toolbar_button_style("delete"))
        remove.clicked.connect(lambda _c=False, w=remove: self._remove_unit_row(w))
        editing = self.mode in EDITING
        for w in (combo, edit, remove):
            w.setEnabled(editing)
        self.units_table.setCellWidget(r, 0, combo)
        self.units_table.setCellWidget(r, 1, edit)
        self.units_table.setCellWidget(r, 2, remove)

    def _remove_unit_row(self, button: QPushButton) -> None:
        for r in range(self.units_table.rowCount()):
            if self.units_table.cellWidget(r, 2) is button:
                self.units_table.removeRow(r)
                return

    def _unit_rows(self) -> list[dict[str, Any]]:
        rows = []
        for r in range(self.units_table.rowCount()):
            combo = self.units_table.cellWidget(r, 0)
            edit = self.units_table.cellWidget(r, 1)
            rows.append({"unit_id": combo.currentData(), "factor": edit.text()})
        return rows

    def _sync_unit_labels(self, *_args: Any) -> None:
        name = self._unit_text(self.unit.currentData()) or "وحدة أساسية"
        self.units_table.setHorizontalHeaderLabels(["الوحدة التانية", f"= كام {name}", ""])

    def _show_accounts(self) -> None:
        try:
            rows = self.service.effective_accounts(self.category.currentData())
        except Exception:  # noqa: BLE001
            rows = []
        source = "من النوع" if self.category.currentData() is not None else "الافتراضي — من غير نوع"
        for r in rows:
            view = self.account_views.get(r["kind"])
            if view is not None:
                view.setText(f"⁦{r['code']}⁩ {r['name_ar']}  ({source})" if r.get("code") else "— مش محدد")

    def _on_category(self, _index: int) -> None:
        """صنف جديد: «خدمي» من النوع · والحسابات والتحذير بيتحسبوا تاني."""
        if self.mode == "new":
            cat = next((c for c in self._choices.get("categories", []) if c["id"] == self.category.currentData()),
                       None)
            self.is_service.setChecked(bool(cat and cat["new_items_service"]))
        self._show_accounts()
        if self.mode in EDITING:
            self._check_name()

    def _on_service(self, checked: bool) -> None:
        editing = self.mode in EDITING
        for w in (self.warehouse, self.reorder_level):
            w.setEnabled(editing and not checked)
        if checked and editing:
            self.warehouse.setCurrentIndex(0)
            self.reorder_level.clear()

    def _check_name(self, *_args: Any) -> None:
        """تحت الاسم: نفس الاسم في نفس النوع ← مش هيتحفظ · في نوع تاني ← تحذير بس."""
        name = self.name.text().strip()
        if self.mode not in EDITING or not name:
            self._hint_name("")
            return
        try:
            rows = self.service.repository.same_name(" ".join(name.split()), self.current_id)
        except Exception:  # noqa: BLE001
            rows = []
        cid = self.category.currentData()
        same = [r for r in rows if r["category_id"] == cid]
        other = [r for r in rows if r["category_id"] != cid]
        if same:
            r = same[0]
            self._hint_name(f"✖ فيه صنف بنفس الاسم في نفس النوع ({ltr(r['code'])}) — مش هيتحفظ.")
            self.name_hint.setStyleSheet(f"color:{BAD}; font-weight:800;")
        elif other:
            where = "، ".join(f"«{r['category_name'] or 'من غير نوع'}» ({ltr(r['code'])})" for r in other[:3])
            self._hint_name(f"⚠ نفس الاسم موجود في نوع تاني: {where} — مسموح، بس اتأكد إنه مش نفس الصنف.")
            self.name_hint.setStyleSheet(f"color:{WARN}; font-weight:800;")
        else:
            self._hint_name("✔ الاسم مفيش زيه.")
            self.name_hint.setStyleSheet(f"color:{ACTIVE_COLOR}; font-weight:700;")

    def _hint_name(self, text: str) -> None:
        self.name_hint.setText(text)
        self.name_hint.setVisible(bool(text))

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.name, self.alt_name, self.description, self.category, self.brand, self.is_active, self.unit,
                self.add_unit_button, self.is_service, self.warehouse, self.tax, self.sale_price,
                self.reorder_level, self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        for r in range(self.units_table.rowCount()):
            for c in range(3):
                w = self.units_table.cellWidget(r, c)
                if w is not None:
                    w.setEnabled(editing)
        self._on_service(self.is_service.isChecked())
        for chip in (*self.status_buttons.buttons(), *self.kind_buttons.buttons()):
            chip.setEnabled(not editing)
        if not editing:
            self._hint_name("")
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
            defaults = self.service.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        self._select(self.unit, defaults["unit_id"])
        self._select(self.tax, defaults["tax_code_id"])
        self.is_service.setChecked(bool(defaults["is_service"]))
        self.set_mode("new")
        self._sync_unit_labels()
        self._show_accounts()
        self.name.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب السجل من جديد ويملا الاختيارات
        if self.mode == "edit":
            self._check_name()

    def save(self) -> None:
        """قبل الحفظ: نفس الاسم في نوع تاني ← رسالة تحذير (تكمّل ولا لأ)."""
        if self.mode in EDITING and self.save_button.isEnabled():
            try:
                others = self.service.same_name_elsewhere(self.name.text(), self.category.currentData(),
                                                          self.current_id if self.mode == "edit" else None)
            except Exception:  # noqa: BLE001
                others = []
            if others:
                where = "، ".join(f"{r['code']} في «{r['category_name'] or 'من غير نوع'}»" for r in others[:3])
                if not messages.ask_yes_no(self, "تحذير — اسم متكرر",
                                           f"فيه صنف بنفس الاسم في نوع تاني ({where}).\nتكمّل الحفظ؟"):
                    return
        super().save()

    # ---- القائمة والصفحات والأرقام ---------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return keyword, self.status, self.kind

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("value")
        self._after_filter()

    def _on_kind(self, chip: QPushButton) -> None:
        self.kind = chip.property("value")
        self._after_filter()

    def _after_filter(self) -> None:
        self.page = 1
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()                                      # المعروض اتخفى بالفلتر

    def _go_page(self, page: int) -> None:
        self.page = max(1, min(self.pages, page))
        self.refresh_list()

    def _update_paging(self, editing: bool = False) -> None:
        if not hasattr(self, "prev_button"):
            return
        self.prev_button.setEnabled(not editing and self.page > 1)
        self.next_button.setEnabled(not editing and self.page < self.pages)
        self.page_label.setText(f"صفحة {self.page} من {self.pages}")

    def fetch_rows(self, keyword: str) -> list[dict]:
        query = self._query(keyword)
        if query != self._last_query:                              # بحث / فلتر جديد ← أول صفحة
            self._last_query = query
            self.page = 1
        self._counts = self.service.count_items(*query)
        self._stats = self.service.stats()
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.service.list_items(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.service.lookup_items()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "name":
            return f"{value}  (خدمي)" if row.get("is_service") else (value or "")
        if key == "category_name":
            return category_path(row) or "—"
        if key == "unit_name":
            return f"{value} ({row.get('unit_code')})" if value else ""
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def _show_stats(self) -> None:
        stats = getattr(self, "_stats", None)
        if stats is None or not hasattr(self, "kpi_values"):
            return
        self.kpi_values["active"].setText(f"{stats['active']:,}")
        self.kpi_values["service"].setText(f"{stats['service']:,}")
        self.kpi_values["no_category"].setText(f"{stats['no_category']:,}")
        warn = stats["no_category"] > 0
        self.kpi_values["no_category"].setStyleSheet(
            f"font-size:15px; font-weight:900; border:none; color:{BAD if warn else ACTIVE_COLOR};")
        self.kpi_frames["no_category"].setStyleSheet(KPI_STYLE.format(color="#DC2626" if warn else GREEN))
        self.kpi_values["reorder"].setText("— (المرحلة 6)")
        self.kpi_frames["reorder"].setToolTip(f"{stats['with_reorder']:,} صنف عليه حد طلب — المقارنة بالرصيد مع "
                                              "«دفتر المخزون»")

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col, name_col = cols.index("is_active"), cols.index("name")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(ACTIVE_COLOR if row.get("is_active") else MUTED))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if row.get("is_service"):
                name_item = self.table.item(r, name_col)
                if name_item is not None:
                    name_item.setForeground(QColor(SERVICE_COLOR))
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"عدد الأصناف: {counts['total']:,} · نشطة {counts['active']:,} · "
                                     f"موقوفة {counts['inactive']:,}")
        self._show_stats()
        self._update_paging(self.mode in EDITING)

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل", f"عايز {question} الصنف «{self.current_record['name']}»؟"):
            return
        try:
            self.service.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل الصنف." if new_state else
                          "تم إيقاف الصنف (مش هيتختار في أي فاتورة جديدة).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفى من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفه.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_item(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key in ("name", "alt_name", "description", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self._select(self.category, record.get("category_id"), category_path(record))
        self._select(self.brand, record.get("brand_id"), record.get("brand_name"))
        self._select(self.unit, record.get("unit_id"), record.get("unit_name"))
        self._select(self.warehouse, record.get("default_warehouse_id"), record.get("warehouse_name"))
        self._select(self.tax, record.get("tax_code_id"), record.get("tax_name"))
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.is_service.blockSignals(True)
        self.is_service.setChecked(bool(record.get("is_service")))
        self.is_service.blockSignals(False)
        self.sale_price.setText(fmt_amount(record.get("sale_price")))
        self.reorder_level.setText(fmt_amount(record.get("reorder_level")))
        self.units_table.setRowCount(0)
        for u in record.get("units") or []:
            self._add_unit_row(u["unit_id"], u["factor"], u.get("unit_name"))
        self._sync_unit_labels()
        self._show_accounts()

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name, self.alt_name, self.description, self.notes, self.sale_price,
                     self.reorder_level):
            edit.clear()
        for combo in (self.category, self.brand, self.warehouse):
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self.units_table.setRowCount(0)
        self.is_service.setChecked(False)
        self.is_active.setChecked(True)
        self._hint_name("")
        for view in self.account_views.values():
            view.clear()

    def collect(self) -> dict:
        return {"name": self.name.text(), "alt_name": self.alt_name.text(), "description": self.description.text(),
                "category_id": self.category.currentData(), "brand_id": self.brand.currentData(),
                "unit_id": self.unit.currentData(), "is_service": self.is_service.isChecked(),
                "default_warehouse_id": self.warehouse.currentData(), "tax_code_id": self.tax.currentData(),
                "sale_price": self.sale_price.text(), "reorder_level": self.reorder_level.text(),
                "notes": self.notes.text(), "is_active": self.is_active.isChecked(), "units": self._unit_rows()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_item(None if record_id is None else int(record_id), payload,
                                      expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_item(int(record_id), expected_version=self.current_version)
