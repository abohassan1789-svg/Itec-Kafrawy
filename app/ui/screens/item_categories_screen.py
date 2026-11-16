"""شاشة «أنواع الأصناف» (الأقسام) — النموذج 2 «شجرة» (اختيار المستخدم 2026-11-16: «ممكن نوع تحته أنواع، وممكن
نوع فرعي أفرّع منه برضه» ← شجرة لحد 5 مستويات).

```
┌ [نوع جديد][+ فرعي تحت المحدد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]       (عرض) ┐
├──────────────────────────────────────────┬───────────────────────────────────────────┤
│ Panel / ABB   نشط                         │ ┌ أنواع الأصناف ┐                             │
│ CAT-006 · في الأكسس: Panel/ABB            │   🔍 اسم النوع أو الكود…   (الكل)(النشطة)…    │
│ ┌ بيانات النوع ┐                            │   [عرض الكل] [طي الكل]                        │
│   الكود · الاسم* · العربي · تحت ▾          │   ▸ BMS                    CAT-001          │
│   ☐ الأصناف الجديدة خدمية · ☑ نشط          │   ▾ Panel                  CAT-005  4 خاصة  │
│ ┌ الحسابات (الفاضي = من الأب أو الافتراضي) ┐│       ABB                  CAT-006          │
│   المخزون ▾ ← هياخد: 1105 المخزون (الافتراضي)│       HIMEL …                                │
│ ┌ الأنواع اللي تحته والاستخدام ┐             │   13 نوع · 7 رئيسي · 6 فرعي                  │
└──────────────────────────────────────────┴───────────────────────────────────────────┘
```
* شمال: الشجرة (البحث بيجيب النوع وآباءه) · «+ فرعي تحت المحدد» بيعمل نوع جديد تحت المحدد.
* يمين: النوع + **الحسابات الأربعة** (كل خانة فاضية بتتورث من أقرب أب، ولو مفيش ← الحساب الافتراضي — وتحتها
  الحساب اللي هيتاخد فعلاً ومنين) + اللي تحته.
* الجدول بتاع الـ Base مستخبي: مصدر «تصدير إكسل» ونافذة البحث F1 (كل الأنواع).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.repositories.item_category_repository import KIND_COLUMN, KIND_LABEL
from app.services.item_category_service import KINDS, USAGE_NOTE, ItemCategoryService, full_path
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import BORDER_CARD, GREEN, ROW_SELECTED, TEXT, toolbar_button_style

ACTIVE_COLOR, MUTED, OFF, OWN_COLOR, SERVICE_COLOR = "#087443", "#64748B", "#94A3B8", "#1D4ED8", "#9D174D"
COL_NAME, COL_CODE, COL_ACCOUNTS, COL_STATUS = range(4)
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
INHERIT_TEXT = "— من الأب / الافتراضي —"
SOURCE_STYLE = {"own": f"color:{OWN_COLOR}; font-weight:800;", "parent": "color:#0E7490; font-weight:700;",
                "default": f"color:{MUTED}; font-weight:600;", "none": "color:#B45309; font-weight:800;"}


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def _form(box: QGroupBox) -> QFormLayout:
    form = QFormLayout(box)
    form.setSpacing(7)
    form.setContentsMargins(14, 18, 14, 10)
    form.setRowWrapPolicy(QFormLayout.DontWrapRows)
    return form


def _readonly(placeholder: str = "", ltr_text: bool = False) -> QLineEdit:
    edit = QLineEdit()
    edit.setReadOnly(True)
    if ltr_text:
        edit.setLayoutDirection(Qt.LeftToRight)
    if placeholder:
        edit.setPlaceholderText(placeholder)
    return edit


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color:{MUTED}; font-weight:600;")
    return label


def account_text(code: Any, name: Any) -> str:
    return f"{ltr(code)} {name}" if code else (name or "")


def effective_text(info: dict[str, Any]) -> str:
    """«← هياخد: 1105 المخزون (الافتراضي)» تحت كل خانة حساب."""
    acc = account_text(info.get("code"), info.get("name"))
    source = info.get("source")
    if source == "own":
        return f"← حساب خاص بالنوع ده: {acc}"
    if source == "parent":
        return f"← هياخد: {acc} (من «{info.get('source_name')}»)"
    if source == "default":
        return f"← هياخد: {acc} (الافتراضي)"
    return "⚠ مفيش حساب — حدّده هنا أو في «الحسابات الافتراضية»"


class ItemCategoriesScreen(BaseScreen):
    MODULE = "inv"
    TARGET = "item_categories"
    TITLE = "أنواع الأصناف"
    ICON = "🗂️"
    FORM_TITLE = "بيانات النوع"
    LIST_TITLE = "أنواع الأصناف (الأقسام)"
    NEW_TEXT = "نوع جديد"
    SEARCH_PLACEHOLDER = "🔍 اسم النوع أو الكود… (F1)"
    COLUMNS = [("code", "الكود"), ("name", "النوع"), ("name_ar", "الاسم العربي"), ("parent_name", "تحت"),
               ("inventory_code", "المخزون"), ("sales_code", "المبيعات"), ("cogs_code", "التكلفة"),
               ("sales_ret_code", "المردودات"), ("new_items_service", "الأصناف الجديدة"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, service: ItemCategoryService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or ItemCategoryService()
        self.categories: list[dict] = []
        self.items: dict[int, QTreeWidgetItem] = {}
        self.defaults: dict[str, dict] = {}
        self._choices: dict[str, dict[int, dict]] = {k: {} for k in KINDS}
        self._parents: dict[int, dict] = {}
        self.account_combos: dict[str, QComboBox] = {}
        self.account_labels: dict[str, QLabel] = {}
        super().__init__(parent)
        if self.categories:
            self.load(self.categories[0]["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = _form(box)
        self.code_edit = _readonly("بيتولّد لوحده", ltr_text=True)
        self.code_edit.setToolTip("كود النوع CAT-001 — بيتولّد وقت الحفظ وثابت")
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.name.setLayoutDirection(Qt.LeftToRight)
        self.name.setPlaceholderText("زي: BMS · MCC · Panel")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("اختياري — زي: لوحات تحكم المحركات")
        self.parent_combo = QComboBox()
        self.parent_combo.setToolTip("النوع بيتفرّع لحد 5 مستويات — الأب لازم نشط")
        self.parent_combo.currentIndexChanged.connect(self._on_parent_changed)
        self.new_items_service = QCheckBox("الأصناف الجديدة تحته خدمية (من غير مخزون)")
        self.new_items_service.setToolTip("الصنف الجديد تحت النوع ده بيطلع «خدمي» افتراضياً — وتقدر تغيّره على الصنف")
        self.is_active = QCheckBox("نشط")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name)
        form.addRow("الاسم العربي", self.name_ar)
        form.addRow("تحت", self.parent_combo)
        form.addRow("", self.new_items_service)
        form.addRow("الحالة", self.is_active)
        form.addRow("ملاحظات", self.notes)

    def _build_header(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("catHeader")
        frame.setStyleSheet(f"QFrame#catHeader {{ background:#F8FAFC; border:1px solid {BORDER_CARD}; "
                            "border-radius:8px; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(2)
        self.path_label = QLabel("")
        self.path_label.setStyleSheet(f"color:{GREEN}; font-weight:900; font-size:15pt;")
        self.path_label.setLayoutDirection(Qt.LeftToRight)
        self.path_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.sub_label = QLabel("")
        self.sub_label.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        self.sub_label.setWordWrap(True)
        layout.addWidget(self.path_label)
        layout.addWidget(self.sub_label)
        return frame

    def _build_accounts(self) -> QGroupBox:
        box = QGroupBox("الحسابات (الفاضي = من الأب أو الافتراضي)")
        form = _form(box)
        for kind in KINDS:
            combo = QComboBox()
            combo.setToolTip(f"حساب {KIND_LABEL[kind]} لأصناف النوع ده — سيبه فاضي ياخد حساب الأب أو الافتراضي")
            combo.currentIndexChanged.connect(self._show_effective)
            label = QLabel("")
            label.setWordWrap(True)
            holder = QWidget()
            col = QVBoxLayout(holder)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            col.addWidget(combo)
            col.addWidget(label)
            self.account_combos[kind] = combo
            self.account_labels[kind] = label
            form.addRow(KIND_LABEL[kind], holder)
        form.addRow(_hint("الحساب الخاص لازم يبقى فرعي ونشط (لو عايز حساب زي «110501 مخزون لوحات» اعمله الأول في "
                          "شجرة الحسابات) · الأنواع اللي تحت النوع ده بتاخد حساباته لو حساباتها فاضية."))
        return box

    def _build_usage(self) -> QGroupBox:
        box = QGroupBox("الأنواع اللي تحته والاستخدام")
        form = _form(box)
        self.children_view = _readonly("—")
        self.access_view = _readonly("—", ltr_text=True)
        form.addRow("تحته", self.children_view)
        form.addRow("في الأكسس", self.access_view)
        form.addRow(_hint("النوع بيتختار على الصنف (اختياري) — والتقارير بتجمّع النوع مع كل اللي تحته. " + USAGE_NOTE))
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(self._build_header())
        right.addWidget(form_box)
        for box in (self._build_accounts(), self._build_usage()):
            align_form_right(box)
            right.addWidget(box)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)               # RTL: أول عنصر = يمين

        tree_box = QGroupBox(self.LIST_TITLE)
        layout = QVBoxLayout(tree_box)
        layout.setContentsMargins(10, 16, 10, 8)
        layout.setSpacing(6)
        old = list_box.layout()
        old.removeWidget(self.search)
        old.removeWidget(self.count_label)
        try:
            self.search.textChanged.disconnect(self.refresh_list)
        except (RuntimeError, TypeError):
            pass
        self.search.textChanged.connect(lambda _t: self._build_tree())
        layout.addWidget(self.search)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.status = "all"
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
            chips.addWidget(chip)
        self.status_buttons.buttonClicked.connect(self._on_status)
        chips.addStretch(1)
        self.expand_button = QPushButton("عرض الكل")
        self.collapse_button = QPushButton("طي الكل")
        for button, slot in ((self.expand_button, lambda: self.tree.expandAll()),
                             (self.collapse_button, lambda: self.tree.collapseAll())):
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(toolbar_button_style("refresh"))
            button.clicked.connect(slot)
            chips.addWidget(button)
        layout.addLayout(chips)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["النوع", "الكود", "الحسابات", "الحالة"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(22)
        self.tree.setAnimated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col in (COL_CODE, COL_ACCOUNTS, COL_STATUS):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; border-radius:6px; }}"
            f"QTreeWidget::item {{ padding:4px 2px; border-bottom:1px solid #F1F5F9; }}"
            f"QTreeWidget::item:selected {{ background:{ROW_SELECTED}; color:{TEXT}; }}")
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.count_label)
        content.addWidget(tree_box, self.LIST_STRETCH)
        list_box.setParent(self)
        list_box.hide()
        self._load_choices()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.sub_button = self._button("+ فرعي تحت المحدد", "new", self.new_sub_category)
        self.sub_button.setToolTip("نوع جديد تحت النوع المحدد (بياخد حساباته لو سبت حساباته فاضية)")
        self.sub_button.setVisible(self.has_action("create"))
        layout.addWidget(self.sub_button)
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.toggle_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.toggle_button)

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self, exclude_id: int | None = None, keep: dict | None = None) -> None:
        """بتتحمّل من جديد مع «جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            choices = self.service.account_choices()
            parents = self.service.parent_choices(exclude_id)
            self.defaults = self.service.default_accounts()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل الشجرة
            choices, parents = {k: [] for k in KINDS}, []
        self._choices = {k: {a["id"]: a for a in choices.get(k, [])} for k in KINDS}
        self._parents = {p["id"]: p for p in parents}
        combos = [self.parent_combo, *self.account_combos.values()]
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()
        self.parent_combo.addItem("— نوع رئيسي —", None)
        for p in parents:
            self.parent_combo.addItem(f"{p['path']}  ({p['code']})", p["id"])
        if keep and keep.get("id") is not None and self.parent_combo.findData(keep["id"]) < 0:
            self.parent_combo.addItem(f"{keep.get('name') or ''}  ({keep.get('code') or ''})", keep["id"])
        for kind, combo in self.account_combos.items():
            combo.addItem(INHERIT_TEXT, None)
            for a in choices.get(kind, []):
                combo.addItem(account_text(a["code"], a["name_ar"]), a["id"])
        for combo in combos:
            combo.blockSignals(False)

    def _set_account(self, kind: str, account_id: Any, code: Any = None, name: Any = None) -> None:
        combo = self.account_combos[kind]
        combo.blockSignals(True)
        if account_id is not None and combo.findData(account_id) < 0:      # حساب اتوقف أو اتغيّر نوعه
            combo.addItem(account_text(code, name) + " ⚠", account_id)
        combo.setCurrentIndex(max(0, combo.findData(account_id)))
        combo.blockSignals(False)

    def _on_parent_changed(self, _index: int) -> None:
        """نوع جديد تحت نوع ← «الأصناف الجديدة خدمية» زي أبوه · والحسابات الفعلية بتتحسب تاني."""
        if self.mode == "new":
            parent = self._parents.get(self.parent_combo.currentData())
            if parent is not None:
                self.new_items_service.setChecked(bool(parent["new_items_service"]))
        self._show_effective()

    def _show_effective(self, *_args: Any) -> None:
        """تحت كل خانة: الحساب اللي هيتاخد فعلاً ومنين (خاص / من الأب / الافتراضي)."""
        own: dict[str, Any] = {}
        for kind, combo in self.account_combos.items():
            account_id = combo.currentData()
            own[kind] = account_id
            if account_id is not None:
                choice = self._choices[kind].get(account_id) or {}
                record = self.current_record or {}
                own[f"{kind}_code"] = choice.get("code") or record.get(f"{kind}_code")
                own[f"{kind}_name"] = choice.get("name_ar") or record.get(f"{kind}_name")
        parent_id = self.parent_combo.currentData()
        rows = [c for c in self.categories if c["id"] != self.current_id] if self.mode in EDITING else self.categories
        info = self.service.resolve_accounts(own, parent_id, rows, self.defaults)
        for kind, label in self.account_labels.items():
            label.setText(effective_text(info[kind]))
            label.setStyleSheet(SOURCE_STYLE.get(info[kind]["source"], SOURCE_STYLE["default"]))

    def effective_accounts(self) -> dict[str, str]:
        """النص اللي تحت كل خانة (للاختبارات)."""
        return {kind: label.text() for kind, label in self.account_labels.items()}

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.name, self.name_ar, self.parent_combo, self.new_items_service, self.is_active, self.notes,
                *self.account_combos.values()]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        self.tree.setEnabled(not editing)
        for widget in (*self.status_buttons.buttons(), self.expand_button, self.collapse_button):
            widget.setEnabled(not editing)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))
        self.sub_button.setEnabled(idle and self.can("create") and bool((self.current_record or {}).get("is_active")))

    # ---- الشجرة -------------------------------------------------------------
    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self._build_tree()

    def _matches(self, row: dict, keyword: str) -> bool:
        if self.status == "active" and not row["is_active"]:
            return False
        if self.status == "inactive" and row["is_active"]:
            return False
        if not keyword:
            return True
        key = keyword.lower()
        texts = [row["code"], row["name"], row.get("name_ar"), row.get("access_name")]
        return any(key in str(t).lower() for t in texts if t)

    def _visible(self) -> set[int] | None:
        """الأنواع اللي هتظهر (اللي بتطابق + آباءها) — None = الكل."""
        keyword = self.search.text().strip()
        if not keyword and self.status == "all":
            return None
        by_id = {c["id"]: c for c in self.categories}
        shown: set[int] = set()
        for row in self.categories:
            if not self._matches(row, keyword):
                continue
            node = row
            while node is not None and node["id"] not in shown:
                shown.add(node["id"])
                node = by_id.get(node["parent_id"])
        return shown

    def _build_tree(self) -> None:
        shown = self._visible()
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        bold = self.tree.font()
        bold.setBold(True)
        by_id = {c["id"]: c for c in self.categories}

        def depth(c: dict) -> int:
            level, node = 0, c
            while node.get("parent_id") is not None and level < 50:
                node = by_id.get(node["parent_id"]) or {}
                level += 1
            return level

        for c in sorted(self.categories, key=lambda x: (depth(x), x["name"].lower())):
            if shown is not None and c["id"] not in shown:
                continue
            name = c["name"] + (f"  ·  {c['name_ar']}" if c.get("name_ar") else "")
            if c["new_items_service"]:
                name += "   (خدمي)"
            own = sum(1 for k in KINDS if c[KIND_COLUMN[k]] is not None)
            item = QTreeWidgetItem([name, c["code"], f"{own} خاصة" if own else "", "نشط" if c["is_active"] else "موقوف"])
            item.setData(COL_NAME, Qt.UserRole, c["id"])
            for col in (COL_CODE, COL_ACCOUNTS, COL_STATUS):
                item.setTextAlignment(col, Qt.AlignCenter)
            if c["children_count"]:
                item.setFont(COL_NAME, bold)
            item.setForeground(COL_NAME, QBrush(QColor(TEXT if c["is_active"] else OFF)))
            item.setForeground(COL_ACCOUNTS, QBrush(QColor(OWN_COLOR)))
            item.setForeground(COL_STATUS, QBrush(QColor(ACTIVE_COLOR if c["is_active"] else MUTED)))
            if c["new_items_service"]:
                item.setForeground(COL_NAME, QBrush(QColor(SERVICE_COLOR if c["is_active"] else OFF)))
            tip = full_path(c, by_id)
            if c.get("notes"):
                tip += f"\n{c['notes']}"
            item.setToolTip(COL_NAME, tip)
            parent_item = self.items.get(c["parent_id"])
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            self.items[c["id"]] = item
        self.tree.expandAll()
        if self.current_id is not None:
            self._select_item(self.current_id)
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)
        self._update_count(shown)

    def _update_count(self, shown: set[int] | None) -> None:
        total = len(self.categories)
        roots = sum(1 for c in self.categories if c["parent_id"] is None)
        active = sum(1 for c in self.categories if c["is_active"])
        text = (f"الأنواع: {ltr(total)} · رئيسي {ltr(roots)} · فرعي {ltr(total - roots)} · نشط {ltr(active)} · "
                f"موقوف {ltr(total - active)}")
        if shown is not None:
            matched = sum(1 for c in self.categories if c["id"] in shown and self._matches(c, self.search.text().strip()))
            text = f"نتايج البحث / الفلتر: {ltr(matched)} من {ltr(total)} نوع"
        self.count_label.setText(text)

    def visible_categories(self) -> list[int]:
        """أرقام الأنواع الظاهرة في الشجرة (للاختبارات)."""
        return list(self.items)

    def _select_item(self, category_id: Any) -> None:
        item = self.items.get(category_id)
        if item is None:
            self.tree.clearSelection()
            return
        self.tree.setCurrentItem(item)
        item.setSelected(True)
        self.tree.scrollToItem(item)

    def _tree_selection(self) -> None:
        if self.mode in EDITING:
            return
        item = self.tree.currentItem()
        if item is None or not item.isSelected():
            return
        record_id = item.data(COL_NAME, Qt.UserRole)
        if record_id != self.current_id:
            self.load(record_id)

    # ---- القائمة (الـ Base) --------------------------------------------------
    def refresh_list(self) -> None:
        try:
            rows = self.service.lookup_categories()
            self.defaults = self.service.default_accounts()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
            rows = []
        self.categories = rows
        self.table.blockSignals(True)
        self.table.set_rows(rows)
        self.table.blockSignals(False)
        self._build_tree()

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.service.list_categories(keyword)

    def lookup_rows(self) -> list[dict]:
        return self.service.lookup_categories()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key.endswith("_code") and key != "code":
            return ltr(value) if value else "الافتراضي / الأب"
        if key == "new_items_service":
            return "خدمية" if value else "بضاعة"
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def load(self, record_id: Any) -> bool:
        if not super().load(record_id):
            return False
        if record_id not in self.items:
            if not any(c["id"] == record_id for c in self.categories):     # نوع جديد من جهاز تاني
                self.refresh_list()
            if record_id not in self.items:
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
                self._build_tree()
        self.tree.blockSignals(True)
        self._select_item(record_id)
        self.tree.blockSignals(False)
        return True

    def show_empty(self) -> None:
        super().show_empty()
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    # ---- «جديد» / «تعديل» -----------------------------------------------------
    def new_record(self, *_signal: Any, parent_id: Any = None) -> None:
        """``*_signal``: زرار «جديد» بيبعت ``checked`` — مش رقم."""
        if not self.can("create"):
            return
        super().new_record()
        if self.mode != "new":
            return
        self._load_choices()
        try:
            defaults = self.service.new_defaults(parent_id)
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        self.parent_combo.blockSignals(True)
        self.parent_combo.setCurrentIndex(max(0, self.parent_combo.findData(defaults["parent_id"])))
        self.parent_combo.blockSignals(False)
        self.new_items_service.setChecked(bool(defaults["new_items_service"]))
        parent = self._parents.get(defaults["parent_id"])
        self.path_label.setText(f"{parent['path']} / …" if parent else "نوع جديد")
        self.sub_label.setText("نوع فرعي — حساباته الفاضية بتتورث من الأب" if parent else "نوع رئيسي")
        self.children_view.setText("—")
        self._show_effective()
        self.name.setFocus()

    def new_sub_category(self) -> None:
        """«+ فرعي تحت المحدد»: نوع جديد أبوه النوع المحدد."""
        if self.current_id is None or self.mode in EDITING:
            return
        self.new_record(parent_id=self.current_id)

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        record = self.current_record or {}
        keep = {"id": record.get("parent_id"), "name": record.get("parent_name"), "code": record.get("parent_code")}
        self._load_choices(exclude_id=int(self.current_id), keep=keep)
        super().edit_record()

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل", f"عايز {question} النوع «{self.current_record['name']}»؟"):
            return
        try:
            self.service.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        self.load(self.current_id)
        messages.info(self, "تم", "تم تفعيل النوع." if new_state else
                      "تم إيقاف النوع (مش هيتختار لأي صنف جديد).")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_category(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        self.name.setText(record.get("name") or "")
        self.name_ar.setText(record.get("name_ar") or "")
        parent_id = record.get("parent_id")
        self.parent_combo.blockSignals(True)
        if parent_id is not None and self.parent_combo.findData(parent_id) < 0:
            self.parent_combo.addItem(f"{record.get('parent_name') or ''}  ({record.get('parent_code') or ''})",
                                      parent_id)
        self.parent_combo.setCurrentIndex(max(0, self.parent_combo.findData(parent_id)))
        self.parent_combo.blockSignals(False)
        self.new_items_service.setChecked(bool(record.get("new_items_service")))
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.notes.setText(record.get("notes") or "")
        for kind in KINDS:
            self._set_account(kind, record.get(KIND_COLUMN[kind]), record.get(f"{kind}_code"),
                              record.get(f"{kind}_name"))
        by_id = {c["id"]: c for c in self.categories}
        by_id.setdefault(record["id"], record)
        self.path_label.setText(full_path(record, by_id))
        state = "نشط" if record.get("is_active") else "موقوف"
        kind_text = "خدمي" if record.get("new_items_service") else "بضاعة"
        self.sub_label.setText(f"{ltr(record.get('code'))} · {state} · الأصناف الجديدة: {kind_text}"
                               + (f" · في الأكسس: {ltr(record['access_name'])}" if record.get("access_name") else ""))
        kids = [c["name"] for c in self.categories if c.get("parent_id") == record.get("id")]
        self.children_view.setText("، ".join(kids) if kids else "—")
        self.access_view.setText(record.get("access_name") or "")
        self._show_effective()

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name, self.name_ar, self.notes, self.children_view, self.access_view):
            edit.clear()
        self.parent_combo.blockSignals(True)
        self.parent_combo.setCurrentIndex(0)
        self.parent_combo.blockSignals(False)
        for kind in KINDS:
            self._set_account(kind, None)
        self.new_items_service.setChecked(False)
        self.is_active.setChecked(True)
        self.path_label.setText("")
        self.sub_label.setText("")
        for label in self.account_labels.values():
            label.setText("")

    def collect(self) -> dict:
        payload = {"name": self.name.text(), "name_ar": self.name_ar.text(),
                   "parent_id": self.parent_combo.currentData(), "notes": self.notes.text(),
                   "new_items_service": self.new_items_service.isChecked(), "is_active": self.is_active.isChecked()}
        for kind, combo in self.account_combos.items():
            payload[KIND_COLUMN[kind]] = combo.currentData()
        return payload

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_category(None if record_id is None else int(record_id), payload,
                                          expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_category(int(record_id), expected_version=self.current_version)
