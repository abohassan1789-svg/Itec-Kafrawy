"""شاشة «الوحدات والماركات» — النموذج 1 «تبويبين + فورم وقائمة» (اختيار المستخدم 2026-11-15).

```
┌ [📏 الوحدات 10] [🏷️ الماركات 0]                                                              ┐
├ [وحدة جديدة][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]          (عرض) ┤
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ بيانات الوحدة ┐                     │ ┌ البحث والتسجيل ┐                       │
│   الرمز* PCS · الاسم* · EN · النوع ▾    │   🔍 الرمز أو الاسم … (F1)                 │
│   ☐ تقبل كسور · ☐ الافتراضية · ☐ نشط   │   [الكل][النشطة][الموقوفة]                  │
│ ┌ الاستخدام ┐ أصناف عليها — (الأصناف)   │   الرمز│الوحدة│النوع│الكمية│الحالة            │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* كل تبويب Base Screen لوحده (بأزراره) وصلاحية واحدة للشاشة ``inv.units_brands``.
* الوحدة: الرمز بيكتبه المستخدم · «تقبل كسور» · الافتراضية واحدة بس (تعليمها بيشيلها من التانية).
* الماركة: الكود ``BRD-001`` لوحده · الاسم مايتكررش حتى بحروف صغيرة أو مسافات (تنبيه وقت الكتابة).
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.brand_service import PAGE_SIZE, BrandService
from app.services.brand_service import USAGE_NOTE as BRAND_USAGE
from app.services.unit_service import USAGE_NOTE as UNIT_USAGE
from app.services.unit_service import UnitService
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED, BAD = "#087443", "#64748B", "#B91C1C"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
TAB_STYLE = (
    "QTabWidget::pane { border:none; }"
    "QTabBar::tab { background:#E2E8F0; color:#475569; font-weight:800; padding:7px 18px; "
    "border-top-left-radius:6px; border-top-right-radius:6px; margin-left:3px; }"
    f"QTabBar::tab:selected {{ background:{GREEN}; color:#FFFFFF; }}")
MULTI_UNITS_NOTE = ("الوحدات المتعددة للصنف (كرتونة = 12 عدد · بكرة = 100 متر) بتتعمل على الصنف نفسه في شاشة "
                    "الأصناف — هنا أسامي الوحدات بس، والمخزون دايماً بالوحدة الأساسية.")
ACCESS_BRANDS_NOTE = "الماركات الـ 118 اللي في الأكسس بتتنقل مع «نقل بيانات الأكسس» بعد دمج المكرر."


def _form(box: QGroupBox) -> QFormLayout:
    form = QFormLayout(box)
    form.setSpacing(7)
    form.setContentsMargins(14, 18, 14, 10)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return form


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{MUTED}; font-weight:600;")
    return label


def _readonly(placeholder: str = "", ltr: bool = False) -> QLineEdit:
    edit = QLineEdit()
    edit.setReadOnly(True)
    if ltr:
        edit.setLayoutDirection(Qt.LeftToRight)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


class _CatalogPanel(BaseScreen):
    """الحاجات المشتركة بين التبويبين: فلتر الحالة · «إيقاف / تفعيل» · لون الحالة · «خروج» بيقفل الشاشة كلها."""

    MODULE = "inv"
    TARGET = "units_brands"
    EXIT_LAST = True
    WHAT = "السجل"                       # «الوحدة» / «الماركة» في الرسايل

    def __init__(self, parent: QWidget | None = None) -> None:
        self.status = "all"
        self.on_counts = None                                      # الشاشة بتحدّث عنوان التبويب بالعدد
        super().__init__(parent)
        self.exit_button.clicked.disconnect()
        self.exit_button.clicked.connect(lambda: self.window().close())

    # ---- البناء ------------------------------------------------------------
    def arrange_extra(self, right: QVBoxLayout) -> None:
        """جروبات زيادة تحت الفورم."""

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        self.arrange_extra(right)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)               # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)

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

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()                                      # المعروض اتخفى بالفلتر

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        for chip in self.status_buttons.buttons():
            chip.setEnabled(mode not in EDITING)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))

    # ---- القائمة -----------------------------------------------------------
    def counts_text(self, counts: dict) -> str:
        return f"العدد: {counts['total']} · نشطة {counts['active']} · موقوفة {counts['inactive']}"

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
            self.count_label.setText(self.counts_text(counts))
            if self.on_counts is not None:
                self.on_counts(self, counts)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    # ---- إيقاف / تفعيل -----------------------------------------------------
    def label_of(self, record: dict) -> str:
        return record.get("name_ar") or ""

    def apply_active(self, new_state: bool) -> None:
        raise NotImplementedError

    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} {self.WHAT} «{self.label_of(self.current_record)}»؟"):
            return
        try:
            self.apply_active(new_state)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", f"تم تفعيل {self.WHAT}." if new_state else
                          f"تم إيقاف {self.WHAT} (مش هتتختار في أي صنف جديد).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفى من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفه.")


# =============================================================================
# تبويب «الوحدات»
# =============================================================================
class UnitsPanel(_CatalogPanel):
    TITLE = "الوحدات"
    ICON = "📏"
    FORM_TITLE = "بيانات الوحدة"
    NEW_TEXT = "وحدة جديدة"
    WHAT = "الوحدة"
    SEARCH_PLACEHOLDER = "🔍 الرمز أو الاسم… (F1)"
    COLUMNS = [("code", "الرمز"), ("name_ar", "الوحدة"), ("category", "النوع"), ("allows_fraction", "الكمية"),
               ("is_active", "الحالة")]

    def __init__(self, units: UnitService | None = None, parent: QWidget | None = None) -> None:
        self.units = units or UnitService()
        super().__init__(parent)

    def build_form(self, box: QGroupBox) -> None:
        form = _form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(10)
        self.code_edit.setLayoutDirection(Qt.LeftToRight)
        self.code_edit.setPlaceholderText("PCS · M · KG")
        self.code_edit.setToolTip("رمز قصير بيظهر في الفاتورة — بيتحفظ حروف كبيرة من غير مسافات، ومايتكررش")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(60)
        self.name_ar.setPlaceholderText("اسم الوحدة (زي «كرتونة»)")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(60)
        self.name_en.setLayoutDirection(Qt.LeftToRight)
        self.category = QComboBox()
        for key, name in self.units.categories():
            self.category.addItem(name, key)
        self.allows_fraction = QCheckBox("الكمية تقبل كسور (زي 2.5 متر)")
        self.allows_fraction.setToolTip("من غير علامة: الكمية رقم صحيح بس (الفاتورة هترفض 1.5 عدد)")
        self.is_default = QCheckBox("الوحدة الافتراضية (بتتملى لوحدها في الصنف الجديد)")
        self.is_default.setToolTip("وحدة واحدة بس — تعليمها هنا بيشيلها من الوحدة التانية")
        self.is_active = QCheckBox("نشط")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الرمز *", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("النوع", self.category)
        form.addRow("الكمية", self.allows_fraction)
        form.addRow("الافتراضية", self.is_default)
        form.addRow("الحالة", self.is_active)
        form.addRow("ملاحظات", self.notes)

    def arrange_extra(self, right: QVBoxLayout) -> None:
        box = QGroupBox("الاستخدام")
        form = _form(box)
        self.items_view = _readonly("— (مع شاشة الأصناف)")
        form.addRow("أصناف عليها", self.items_view)
        form.addRow(_hint(UNIT_USAGE + " الوحدة اللي عليها أصناف مابتتمسحش — بتتوقف بس."))
        form.addRow(_hint(MULTI_UNITS_NOTE))
        align_form_right(box)
        right.addWidget(box)

    def editable_widgets(self) -> list[QWidget]:
        return [self.code_edit, self.name_ar, self.name_en, self.category, self.allows_fraction, self.is_default,
                self.is_active, self.notes]

    def counts_text(self, counts: dict) -> str:
        return (f"عدد الوحدات: {counts['total']} · نشطة {counts['active']} · موقوفة {counts['inactive']}"
                "  ·  ⭐ = الافتراضية")

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        self._counts = self.units.count_units(keyword, self.status)
        return self.units.list_units(keyword, self.status)

    def lookup_rows(self) -> list[dict]:
        return self.units.lookup_units()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "name_ar":
            return f"{value} ⭐" if row.get("is_default") else (value or "")
        if key == "category":
            return self.units.category_name(value)
        if key == "allows_fraction":
            return "بكسور" if value else "صحيح بس"
        return super().format_cell(key, value, row)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.units.get_unit(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key in ("name_ar", "name_en", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        index = self.category.findData(record.get("category") or "count")
        self.category.setCurrentIndex(max(0, index))
        self.allows_fraction.setChecked(bool(record.get("allows_fraction")))
        self.is_default.setChecked(bool(record.get("is_default")))
        self.is_active.setChecked(bool(record.get("is_active", True)))

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.notes):
            edit.clear()
        defaults = self.units.new_defaults()
        self.category.setCurrentIndex(max(0, self.category.findData(defaults["category"])))
        self.allows_fraction.setChecked(defaults["allows_fraction"])
        self.is_default.setChecked(defaults["is_default"])
        self.is_active.setChecked(defaults["is_active"])

    def new_record(self) -> None:
        super().new_record()
        if self.mode == "new":
            self.code_edit.setFocus()

    def collect(self) -> dict:
        return {"code": self.code_edit.text(), "name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                "category": self.category.currentData(), "allows_fraction": self.allows_fraction.isChecked(),
                "is_default": self.is_default.isChecked(), "is_active": self.is_active.isChecked(),
                "notes": self.notes.text()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.units.save_unit(None if record_id is None else int(record_id), payload,
                                    expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.units.delete_unit(int(record_id), expected_version=self.current_version)

    def apply_active(self, new_state: bool) -> None:
        self.units.set_active(int(self.current_id), new_state, expected_version=self.current_version)


# =============================================================================
# تبويب «الماركات»
# =============================================================================
class BrandsPanel(_CatalogPanel):
    TITLE = "الماركات"
    ICON = "🏷️"
    FORM_TITLE = "بيانات الماركة"
    NEW_TEXT = "ماركة جديدة"
    WHAT = "الماركة"
    SEARCH_PLACEHOLDER = "🔍 اسم الماركة أو الكود أو البلد… (F1)"
    COLUMNS = [("code", "الكود"), ("name", "الماركة"), ("name_ar", "الاسم العربي"), ("country", "البلد"),
               ("is_active", "الحالة")]

    def __init__(self, brands: BrandService | None = None, parent: QWidget | None = None) -> None:
        self.brands = brands or BrandService()
        self.page = 1
        self.pages = 1
        self._last_query: tuple | None = None
        super().__init__(parent)
        self.count_label.parentWidget().layout().addLayout(self.paging)

    def build_form(self, box: QGroupBox) -> None:
        form = _form(box)
        self.code_edit = _readonly("بيتولّد لوحده", ltr=True)
        self.code_edit.setToolTip("كود الماركة بيتولّد لوحده وقت الحفظ (BRD-001، BRD-002…) وثابت بعد كده")
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.name.setLayoutDirection(Qt.LeftToRight)
        self.name.setPlaceholderText("SIEMENS · ABB · Schneider")
        self.name.textEdited.connect(self._check_name)
        self.name_hint = QLabel("")
        self.name_hint.setWordWrap(True)
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("اختياري (زي «سيمنز»)")
        self.country = QLineEdit()
        self.country.setMaxLength(60)
        self.country.setPlaceholderText("اختياري (زي «ألمانيا»)")
        self.is_active = QCheckBox("نشط")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name)
        form.addRow("", self.name_hint)
        form.addRow("الاسم العربي", self.name_ar)
        form.addRow("بلد المنشأ", self.country)
        form.addRow("الحالة", self.is_active)
        form.addRow("ملاحظات", self.notes)

    def arrange_extra(self, right: QVBoxLayout) -> None:
        box = QGroupBox("الاستخدام")
        form = _form(box)
        self.items_view = _readonly("— (مع شاشة الأصناف)")
        form.addRow("أصناف عليها", self.items_view)
        form.addRow(_hint(BRAND_USAGE + " الماركة اللي عليها أصناف مابتتمسحش — بتتوقف بس."))
        form.addRow(_hint("الاسم مايتكررش حتى لو بحروف كبيرة/صغيرة أو مسافات (himel = HIMEL). "
                          + ACCESS_BRANDS_NOTE))
        align_form_right(box)
        right.addWidget(box)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        super().add_list_filters(layout)
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

    def editable_widgets(self) -> list[QWidget]:
        return [self.name, self.name_ar, self.country, self.is_active, self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        if mode not in EDITING:
            self.name_hint.clear()
        self._update_paging(mode in EDITING)

    def counts_text(self, counts: dict) -> str:
        return f"عدد الماركات: {counts['total']} · نشطة {counts['active']} · موقوفة {counts['inactive']}"

    # ---- تنبيه الاسم المتكرر وقت الكتابة ------------------------------------
    def _check_name(self, text: str) -> None:
        if self.mode not in EDITING or not text.strip():
            self.name_hint.clear()
            return
        try:
            found = self.brands.find_by_name(text)
        except Exception:  # noqa: BLE001 - الحفظ بيتأكد تاني
            self.name_hint.clear()
            return
        editing_id = self.current_id if self.mode == "edit" else None     # في «جديد» مفيش سجل حالي
        if found and found["id"] != editing_id:
            self.name_hint.setText(f"✖ الماركة دي موجودة: ⁦{found['code']}⁩ «{found['name']}»")
            self.name_hint.setStyleSheet(f"color:{BAD}; font-weight:800;")
        else:
            self.name_hint.setText("✔ مفيش ماركة بالاسم ده")
            self.name_hint.setStyleSheet(f"color:{ACTIVE_COLOR}; font-weight:800;")

    # ---- الصفحات -----------------------------------------------------------
    def _go_page(self, page: int) -> None:
        self.page = max(1, min(self.pages, page))
        self.refresh_list()

    def _update_paging(self, editing: bool = False) -> None:
        if not hasattr(self, "prev_button"):
            return
        self.prev_button.setEnabled(not editing and self.page > 1)
        self.next_button.setEnabled(not editing and self.page < self.pages)
        self.page_label.setText(f"صفحة {self.page} من {self.pages}")

    def refresh_list(self) -> None:
        super().refresh_list()
        self._update_paging(self.mode in EDITING)

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        query = (keyword, self.status)
        if query != self._last_query:                              # بحث / فلتر جديد ← أول صفحة
            self._last_query = query
            self.page = 1
        self._counts = self.brands.count_brands(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.brands.list_brands(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.brands.lookup_brands()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key in ("name_ar", "country"):
            return value or "—"
        return super().format_cell(key, value, row)

    def label_of(self, record: dict) -> str:
        return record.get("name") or ""

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.brands.get_brand(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key in ("name", "name_ar", "country", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self.is_active.setChecked(bool(record.get("is_active", True)))

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name, self.name_ar, self.country, self.notes):
            edit.clear()
        self.is_active.setChecked(True)

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            defaults = self.brands.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.name.setFocus()

    def collect(self) -> dict:
        return {"name": self.name.text(), "name_ar": self.name_ar.text(), "country": self.country.text(),
                "is_active": self.is_active.isChecked(), "notes": self.notes.text()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.brands.save_brand(None if record_id is None else int(record_id), payload,
                                      expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.brands.delete_brand(int(record_id), expected_version=self.current_version)

    def apply_active(self, new_state: bool) -> None:
        self.brands.set_active(int(self.current_id), new_state, expected_version=self.current_version)


# =============================================================================
# الشاشة نفسها: تبويبين
# =============================================================================
class UnitsBrandsScreen(QWidget):
    TITLE = "الوحدات والماركات"
    ICON = "📏"

    def __init__(self, units: UnitService | None = None, brands: BrandService | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setWindowTitle(f"{self.ICON}  {self.TITLE}")
        self.setMinimumSize(1000, 600)
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 8, 6, 6)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(TAB_STYLE)
        self.tabs.setDocumentMode(True)
        root.addWidget(self.tabs)
        self.units = UnitsPanel(units)
        self.brands = BrandsPanel(brands)
        self.panels = (self.units, self.brands)
        for panel in self.panels:
            panel.on_counts = self._show_count
            self.tabs.addTab(panel, "")
            if panel.table.rows:                                   # أول سجل مفتوح
                first = panel.table.rows[0]["id"]
                panel.load(first)
                panel.table.select_id(first)
            panel.refresh_list()                                   # عشان عنوان التبويب ياخد العدد

    def _show_count(self, panel: _CatalogPanel, counts: dict) -> None:
        index = self.tabs.indexOf(panel)
        if index >= 0:
            self.tabs.setTabText(index, f"{panel.ICON} {panel.TITLE}  ({counts['total']})")

    def closeEvent(self, event) -> None:  # noqa: ANN001 - Qt
        if any(p.mode in EDITING for p in self.panels) and not messages.ask_yes_no(
                self, "خروج", "فيه بيانات لسه متحفظتش. عايز تخرج من غير حفظ؟"):
            event.ignore()
            return
        super().closeEvent(event)
