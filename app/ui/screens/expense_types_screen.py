"""شاشة «أنواع المصروفات» — النموذج 4 «الأنواع في دليل الحسابات» (اختيار المستخدم 2026-11-15 + ردوده).

```
┌ [نوع جديد][+ نوع فرعي تحت المحدد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]   (عرض) ┐
├──────────────────────────────────────────┬───────────────────────────────────────────┤
│ (الحساب 5203 عليه نوعين [🧾 نوع جديد عليه]) │ ┌ أنواع المصروفات في دليل الحسابات ┐            │
│ ┌ نوع المصروف ┐                           │   🔍 النوع أو الحساب أو الكود…  (الكل)(النشطة)…  │
│   الكود EXT-001 · الاسم* · EN              │   ▾ 5 المصروفات والتكاليف                       │
│   النوع الأب ▾ (— نوع رئيسي —) · نشط        │     ▾ 52 مصروفات عمومية وإدارية                  │
│ ┌ الحساب في الدليل ┐                         │        ▾ 5203 كهرباء ومياه        2 نوع          │
│   (•) أختار حساب موجود ▾                    │            🧾 كهرباء           EXT-001  نشط     │
│   ( ) يتعمل حساب جديد باسم النوع تحت ▾ 52    │            🧾 مياه             EXT-007  نشط     │
│ ┌ الأنواع اللي تحته والاستخدام ┐              │        5206 فروق تكلفة المخزون   حساب نظامي     │
└──────────────────────────────────────────┴───────────────────────────────────────────┘
```
* شمال: شجرة كل حسابات المصروفات في الدليل، وتحت كل حساب فرعي الأنواع اللي عليه (والنوع الفرعي تحت أبوه لو على نفس
  الحساب، أو «↳ تحت …» لو على حساب تاني). دوس على حساب ← «🧾 نوع جديد على الحساب ده».
* يمين: النوع (الاسم، الأب، الحالة) + الحساب في الدليل (موجود أو يتعمل جديد — في «جديد» بس) + اللي تحته.
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
    QRadioButton,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.expense_type_service import USAGE_NOTE, ExpenseTypeService
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import BORDER_CARD, GREEN, ROW_SELECTED, TEXT, TEXT_MUTED, toolbar_button_style

ACTIVE_COLOR, MUTED, WARN, OFF, TYPE_COLOR = "#087443", "#64748B", "#B45309", "#94A3B8", "#1D4ED8"
COL_NAME, COL_CODE, COL_STATUS = range(3)
ROLE_KIND = Qt.UserRole + 1                      # "account" / "type"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")
FRAME_STYLE = {
    "warn": "QFrame#accountInfo { background:#FFFBEB; border:1px solid #FDE68A; border-radius:7px; }",
    "info": "QFrame#accountInfo { background:#EFF6FF; border:1px solid #BFDBFE; border-radius:7px; }",
    "muted": "QFrame#accountInfo { background:#F8FAFC; border:1px solid #E2E8F0; border-radius:7px; }",
}
LABEL_COLOR = {"warn": "#92400E", "info": "#1E3A8A", "muted": "#475569"}


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


class ExpenseTypesScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "expense_types"
    TITLE = "أنواع المصروفات"
    ICON = "🧾"
    FORM_TITLE = "نوع المصروف"
    LIST_TITLE = "أنواع المصروفات في دليل الحسابات"
    NEW_TEXT = "نوع جديد"
    SEARCH_PLACEHOLDER = "🔍 النوع أو الحساب أو الكود… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "النوع"), ("parent_name", "النوع الأب"),
               ("account_code", "الحساب"), ("account_name", "اسم الحساب"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, service: ExpenseTypeService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or ExpenseTypeService()
        self.chart: list[dict] = []
        self.types: list[dict] = []
        self.items: dict[tuple[str, int], QTreeWidgetItem] = {}   # ("account"/"type", id) ← العنصر
        self.selected_account: dict | None = None                # حساب محدد في الشجرة (مش نوع)
        self._usable: dict[int, dict] = {}
        self._account_parents: dict[int, dict] = {}
        self._parent_types: dict[int, dict] = {}
        super().__init__(parent)
        if self.types:
            self.load(self.types[0]["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = _form(box)
        self.code_edit = _readonly("بيتولّد لوحده", ltr_text=True)
        self.code_edit.setToolTip("كود النوع EXT-001 — بيتولّد وقت الحفظ وثابت")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("زي: كهرباء، إيجار، سولار")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setLayoutDirection(Qt.LeftToRight)
        self.parent_combo = QComboBox()
        self.parent_combo.setToolTip("النوع بيتفرّع لحد 5 مستويات — الأب لازم نشط")
        self.parent_combo.currentIndexChanged.connect(self._on_parent_changed)
        self.is_active = QCheckBox("نشط")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("النوع الأب", self.parent_combo)
        form.addRow("الحالة", self.is_active)
        form.addRow("ملاحظات", self.notes)

    def _build_info(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("accountInfo")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.link_button = QPushButton("🧾 نوع جديد على الحساب ده")
        self.link_button.setCursor(Qt.PointingHandCursor)
        self.link_button.setStyleSheet(toolbar_button_style("new"))
        self.link_button.clicked.connect(self.new_on_account)
        layout.addWidget(self.info_label)
        row = QHBoxLayout()
        row.addWidget(self.link_button)
        row.addStretch(1)
        layout.addLayout(row)
        frame.hide()
        return frame

    def _build_account(self) -> QGroupBox:
        box = QGroupBox("الحساب في الدليل")
        form = _form(box)
        self._account_form = form
        self.mode_pick = QRadioButton("أختار حساب موجود")
        self.mode_auto = QRadioButton("يتعمل حساب جديد باسم النوع تحت:")
        self.mode_pick.setChecked(True)
        self.account_modes = QButtonGroup(self)
        self.account_modes.addButton(self.mode_pick)
        self.account_modes.addButton(self.mode_auto)
        self.mode_pick.toggled.connect(self._on_account_mode)
        self.account_pick = QComboBox()
        self.account_pick.setToolTip("حسابات المصروفات الفرعية النشطة (من غير الحسابات النظامية) — كذا نوع على نفس "
                                     "الحساب مسموح")
        self.account_pick.currentIndexChanged.connect(self._show_account)
        self.account_parent_combo = QComboBox()
        self.account_parent_combo.setToolTip("الحسابات الرئيسية في المصروفات (52 / 53 / 54…)")
        self.account_parent_combo.currentIndexChanged.connect(self._show_account)
        self.account_view = _readonly("")
        self.account_parent = _readonly("")
        self.shared_view = _readonly("—")
        self.cc_label = QLabel("")
        self.cc_label.setWordWrap(True)
        self.cc_label.setStyleSheet("color:#6D28D9; font-weight:700;")
        form.addRow("", self.mode_pick)
        form.addRow("الحساب", self.account_pick)
        form.addRow("", self.mode_auto)
        form.addRow("تحت", self.account_parent_combo)
        form.addRow("حساب الدليل", self.account_view)
        form.addRow("الحساب الأب", self.account_parent)
        form.addRow("على نفس الحساب", self.shared_view)
        form.addRow(self.cc_label)
        form.addRow(_hint("المصروف من النوع ده بيتقيّد: مدين حساب النوع / دائن الخزنة أو البنك (المرحلة 9). "
                          "الحساب ثابت بعد الحفظ — ولو اتفرّع من شجرة الحسابات النوع بينزل على «— عام»."))
        return box

    def _build_usage(self) -> QGroupBox:
        box = QGroupBox("الأنواع اللي تحته والاستخدام")
        form = _form(box)
        self.children_view = _readonly("—")
        form.addRow("تحته", self.children_view)
        form.addRow(_hint("أمر التوريد اختياري في كل الأنواع (بيتختار في المصروف نفسه). " + USAGE_NOTE))
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        self.info_frame = self._build_info()
        right.addWidget(self.info_frame)
        right.addWidget(form_box)
        for box in (self._build_account(), self._build_usage()):
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
        layout.addWidget(_hint("كل نوع مربوط بحساب مصروف فرعي تحت «5 المصروفات» — وكذا نوع ممكن يبقوا على نفس الحساب."))
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
        layout.addLayout(chips)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["الحساب / النوع", "الكود", "الحالة"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(22)
        self.tree.setAnimated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        for col in (COL_CODE, COL_STATUS):
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
        self.sub_button = self._button("+ نوع فرعي تحت المحدد", "new", self.new_sub_type)
        self.sub_button.setToolTip("نوع جديد تحت النوع المحدد (بياخد حسابه افتراضياً)")
        self.sub_button.setVisible(self.has_action("create"))
        layout.addWidget(self.sub_button)
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.toggle_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.toggle_button)

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self, exclude_id: int | None = None, keep_parent: dict | None = None) -> None:
        """بتتحمّل من جديد مع «جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            accounts = self.service.usable_accounts()
            parents = self.service.account_parents()
            types = self.service.parent_choices(exclude_id)
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل الشجرة
            accounts, parents, types = [], [], []
        self._usable = {a["id"]: a for a in accounts}
        self._account_parents = {p["id"]: p for p in parents}
        self._parent_types = {t["id"]: t for t in types}
        for combo in (self.account_pick, self.account_parent_combo, self.parent_combo):
            combo.blockSignals(True)
            combo.clear()
        for a in accounts:
            self.account_pick.addItem(f"⁦{a['code']}⁩ {a['name_ar']}", a["id"])
        for p in parents:
            self.account_parent_combo.addItem(f"⁦{p['code']}⁩ {p['name_ar']}", p["id"])
        self.parent_combo.addItem("— نوع رئيسي —", None)
        for t in types:
            self.parent_combo.addItem(f"{t['name_ar']} ({t['code']})", t["id"])
        if keep_parent and keep_parent.get("id") is not None and self.parent_combo.findData(keep_parent["id"]) < 0:
            self.parent_combo.addItem(f"{keep_parent['name_ar']} ({keep_parent['code']})", keep_parent["id"])
        for combo in (self.account_pick, self.account_parent_combo, self.parent_combo):
            combo.blockSignals(False)

    def _on_parent_changed(self, _index: int) -> None:
        """نوع جديد تحت نوع ← حساب أبوه افتراضياً («أختار حساب موجود»)."""
        if self.mode != "new":
            return
        parent = self._parent_types.get(self.parent_combo.currentData())
        if parent is not None and self.account_pick.findData(parent["account_id"]) >= 0:
            self.mode_pick.setChecked(True)
            self.account_pick.setCurrentIndex(self.account_pick.findData(parent["account_id"]))
        self._show_account()

    def _on_account_mode(self, _pick: bool) -> None:
        self._sync_account_widgets()
        self._show_account()

    def _sync_account_widgets(self) -> None:
        new = self.mode == "new"
        for widget in (self.mode_pick, self.mode_auto):
            self._account_form.setRowVisible(widget, new)
            widget.setEnabled(new)
        self._account_form.setRowVisible(self.account_pick, new)
        self._account_form.setRowVisible(self.account_parent_combo, new)
        self.account_pick.setEnabled(new and self.mode_pick.isChecked())
        self.account_parent_combo.setEnabled(new and self.mode_auto.isChecked())

    def _show_account(self, *_args: Any) -> None:
        """في «جديد»: الحساب المختار أو اللي هيتعمل + الأنواع التانية عليه + «لازم مركز تكلفة»."""
        if self.mode != "new":
            return
        if self.mode_pick.isChecked():
            account = self._usable.get(self.account_pick.currentData())
            if account is None:
                self.account_view.setText("")
                self.account_parent.setText("مفيش حسابات مصروف فرعية — اختار «يتعمل حساب جديد»")
                self.shared_view.setText("")
                self.cc_label.setText("")
                return
            row = self.chart_row(account["id"]) or {}
            parent = self.chart_row(row.get("parent_id")) or {}
            self.account_view.setText(f"⁦{account['code']}⁩ {account['name_ar']}")
            self.account_parent.setText(f"⁦{parent.get('code', '')}⁩ {parent.get('name_ar', '')}" if parent else "")
            shared = [t["name_ar"] for t in self.types if t["account_id"] == account["id"]]
            self.shared_view.setText("، ".join(shared) if shared else "مفيش — أول نوع عليه")
            self.cc_label.setText("⚠ الحساب ده «لازم مركز تكلفة» ← المركز هيبقى إجباري في المصروف."
                                  if account.get("requires_cost_center") else "")
        else:
            parent_id = self.account_parent_combo.currentData()
            try:
                suggested = self.service.suggested_account(parent_id)
            except Exception:  # noqa: BLE001
                suggested = None
            if suggested and suggested.get("code"):
                self.account_view.setText(f"⁦{suggested['code']}⁩ (هيتعمل لوحده باسم النوع)")
                self.account_parent.setText(f"⁦{suggested['parent_code']}⁩ {suggested['parent_name']}")
            else:
                self.account_view.setText("")
                self.account_parent.setText("اختار الحساب الرئيسي")
            self.shared_view.setText("مفيش — حساب جديد")
            self.cc_label.setText("")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.parent_combo, self.is_active, self.notes]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        self._sync_account_widgets()
        self.tree.setEnabled(not editing)
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        if editing:
            self.info_frame.hide()

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))
        self.sub_button.setEnabled(idle and self.can("create") and bool((self.current_record or {}).get("is_active")))
        self.link_button.setEnabled(self.mode == "view" and self.can("create"))

    # ---- الشجرة -------------------------------------------------------------
    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self._build_tree()

    def chart_row(self, account_id: Any) -> dict | None:
        return next((r for r in self.chart if r["id"] == account_id), None) if account_id is not None else None

    def _type_matches(self, row: dict, keyword: str) -> bool:
        if self.status == "active" and not row["is_active"]:
            return False
        if self.status == "inactive" and row["is_active"]:
            return False
        if not keyword:
            return True
        key = keyword.lower()
        texts = [row["code"], row["name_ar"], row.get("name_en"), row["account_code"], row["account_name"]]
        return any(key in str(t).lower() for t in texts if t)

    def _visible(self) -> tuple[set[int] | None, set[int] | None]:
        """(الحسابات، الأنواع) اللي هتظهر — None = الكل."""
        keyword = self.search.text().strip()
        if not keyword and self.status == "all":
            return None, None
        types = {t["id"] for t in self.types if self._type_matches(t, keyword)}
        by_id = {r["id"]: r for r in self.chart}
        accounts: set[int] = set()
        seeds = {t["account_id"] for t in self.types if t["id"] in types}
        if self.status == "all" and keyword:
            key = keyword.lower()
            seeds |= {r["id"] for r in self.chart if r["is_leaf"]
                      and (key in r["code"].lower() or key in r["name_ar"].lower())}
        for account_id in seeds:
            node = by_id.get(account_id)
            while node is not None and node["id"] not in accounts:
                accounts.add(node["id"])
                node = by_id.get(node["parent_id"])
        return accounts, types

    def _build_tree(self) -> None:
        shown_accounts, shown_types = self._visible()
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        bold = self.tree.font()
        bold.setBold(True)
        counts = {}
        for t in self.types:
            counts[t["account_id"]] = counts.get(t["account_id"], 0) + 1
        for row in self.chart:                               # بترتيب الكود ← الأب دايماً قبل اللي تحته
            if shown_accounts is not None and row["id"] not in shown_accounts:
                continue
            code_text, status = "", ("" if row["is_active"] else "موقوف")
            if row["is_leaf"]:
                if counts.get(row["id"]):
                    code_text = f"{counts[row['id']]} نوع"
                elif row["usable"]:
                    code_text = "⚠ مفيش نوع"
                else:
                    code_text = "حساب نظامي"
            item = QTreeWidgetItem([f"{ltr(row['code'])}  {row['name_ar']}", code_text, status])
            item.setData(COL_NAME, Qt.UserRole, row["id"])
            item.setData(COL_NAME, ROLE_KIND, "account")
            for col in (COL_CODE, COL_STATUS):
                item.setTextAlignment(col, Qt.AlignCenter)
            if not row["is_leaf"]:
                item.setFont(COL_NAME, bold)
                item.setForeground(COL_NAME, QBrush(QColor(GREEN)))
            elif counts.get(row["id"]):
                item.setForeground(COL_CODE, QBrush(QColor(ACTIVE_COLOR)))
            elif row["usable"]:
                item.setForeground(COL_CODE, QBrush(QColor(WARN)))
            else:
                item.setForeground(COL_CODE, QBrush(QColor(TEXT_MUTED)))
                item.setForeground(COL_NAME, QBrush(QColor(MUTED)))
            if not row["is_active"]:
                item.setForeground(COL_NAME, QBrush(QColor(OFF)))
            parent_item = self.items.get(("account", row["parent_id"]))
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            self.items[("account", row["id"])] = item
        # الأنواع: الأب قبل اللي تحته (بالعمق ثم المسلسل)
        by_id = {t["id"]: t for t in self.types}

        def depth(t: dict) -> int:
            level, node = 0, t
            while node.get("parent_id") is not None and level < 50:
                node = by_id.get(node["parent_id"]) or {}
                level += 1
            return level

        for t in sorted(self.types, key=lambda x: (depth(x), x["seq"])):
            if shown_types is not None and t["id"] not in shown_types:
                continue
            account_item = self.items.get(("account", t["account_id"]))
            if account_item is None:
                continue
            parent = by_id.get(t["parent_id"]) if t["parent_id"] is not None else None
            parent_item = self.items.get(("type", parent["id"])) if parent is not None else None
            nested = parent_item is not None and parent["account_id"] == t["account_id"]
            name = f"🧾 {t['name_ar']}"
            if parent is not None and not nested:
                name += f"   ↳ تحت «{parent['name_ar']}»"
            item = QTreeWidgetItem([name, t["code"], "نشط" if t["is_active"] else "موقوف"])
            item.setData(COL_NAME, Qt.UserRole, t["id"])
            item.setData(COL_NAME, ROLE_KIND, "type")
            for col in (COL_CODE, COL_STATUS):
                item.setTextAlignment(col, Qt.AlignCenter)
            item.setForeground(COL_NAME, QBrush(QColor(TYPE_COLOR if t["is_active"] else OFF)))
            item.setForeground(COL_STATUS, QBrush(QColor(ACTIVE_COLOR if t["is_active"] else MUTED)))
            if t.get("notes"):
                item.setToolTip(COL_NAME, t["notes"])
            (parent_item if nested else account_item).addChild(item)
            self.items[("type", t["id"])] = item
        self.tree.expandAll()
        if self.current_id is not None:
            self._select_item(("type", self.current_id))
        elif self.selected_account is not None:
            self._select_item(("account", self.selected_account["id"]))
        else:
            self.tree.clearSelection()
        self.tree.blockSignals(False)
        self._update_count(shown_types)

    def _update_count(self, shown_types: set[int] | None) -> None:
        active = sum(1 for t in self.types if t["is_active"])
        leaves = [r for r in self.chart if r["is_leaf"] and r["usable"]]
        used = len({t["account_id"] for t in self.types})
        text = (f"الأنواع: {ltr(len(self.types))} · نشط {ltr(active)} · موقوف {ltr(len(self.types) - active)}  ·  "
                f"حسابات المصروفات: {ltr(len(leaves))} · عليها أنواع {ltr(used)}")
        if shown_types is not None:
            text = f"نتايج البحث / الفلتر: {ltr(len(shown_types))} من {ltr(len(self.types))} نوع"
        if not self.chart:
            text = "مفيش حسابات مصروفات في الدليل — اعمل «5 المصروفات» من شجرة الحسابات"
        self.count_label.setText(text)

    def visible_types(self) -> list[int]:
        """أرقام الأنواع الظاهرة في الشجرة (للاختبارات)."""
        return [key[1] for key in self.items if key[0] == "type"]

    def _select_item(self, key: tuple[str, Any]) -> None:
        item = self.items.get(key)
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
        if item.data(COL_NAME, ROLE_KIND) == "type":
            self.selected_account = None
            self.info_frame.hide()
            if record_id != self.current_id:
                self.load(record_id)
            return
        BaseScreen.show_empty(self)                         # من غير ما الشجرة تتمسح
        self._show_account_info(self.chart_row(record_id))

    def _show_account_info(self, row: dict | None) -> None:
        self.selected_account = row
        if row is None or (not row["is_leaf"] and row["id"] not in self._account_parents):
            self.info_frame.hide()
            return
        label = f"{ltr(row['code'])} «{row['name_ar']}»"
        names = [t["name_ar"] for t in self.types if t["account_id"] == row["id"]]
        can_add = bool(row["is_active"]) and self.has_action("create")
        if not row["is_leaf"]:
            kind, text = "info", f"الحساب الرئيسي {label} — تقدر تعمل نوع بحساب جديد تحته."
            self.link_button.setText("🧾 نوع جديد بحساب جديد تحته")
        elif not row["usable"]:
            kind, text = "muted", (f"الحساب {label} حساب نظامي (بيتقيّد لوحده من المستندات) — مالوش أنواع مصروفات.")
            can_add = False
        elif names:
            kind, text = "info", f"الحساب {label} عليه {len(names)} نوع: {('، '.join(names))}."
            self.link_button.setText("🧾 نوع جديد على الحساب ده")
        else:
            kind, text = "warn", f"⚠ الحساب {label} مفيش عليه نوع مصروف."
            self.link_button.setText("🧾 نوع جديد على الحساب ده")
        if not row["is_active"]:
            text += " (موقوف — نشّطه من شجرة الحسابات الأول)"
        self.info_frame.setStyleSheet(FRAME_STYLE[kind])
        self.info_label.setStyleSheet(f"color:{LABEL_COLOR[kind]}; font-weight:800;")
        self.info_label.setText(text)
        self.link_button.setVisible(can_add)
        self.link_button.setEnabled(self.can("create"))
        self.info_frame.show()

    # ---- القائمة (الـ Base) --------------------------------------------------
    def refresh_list(self) -> None:
        try:
            types = self.service.lookup_types()
            chart = self.service.chart_rows()
            parents = self.service.account_parents()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
            types, chart, parents = [], [], []
        self.types, self.chart = types, chart
        self._account_parents = {p["id"]: p for p in parents}
        if self.selected_account is not None:
            self.selected_account = self.chart_row(self.selected_account["id"])
            if self.selected_account is None:
                self.info_frame.hide()
        self.table.blockSignals(True)
        self.table.set_rows(types)
        self.table.blockSignals(False)
        self._build_tree()

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.service.list_types(keyword)

    def lookup_rows(self) -> list[dict]:
        return self.service.lookup_types()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "account_code":
            return ltr(value) if value else ""
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def load(self, record_id: Any) -> bool:
        if not super().load(record_id):
            return False
        self.selected_account = None
        self.info_frame.hide()
        if ("type", record_id) not in self.items:
            if not any(t["id"] == record_id for t in self.types):     # نوع جديد من جهاز تاني
                self.refresh_list()
            if ("type", record_id) not in self.items:
                self.search.blockSignals(True)
                self.search.clear()
                self.search.blockSignals(False)
                self._build_tree()
        self.tree.blockSignals(True)
        self._select_item(("type", record_id))
        self.tree.blockSignals(False)
        return True

    def show_empty(self) -> None:
        super().show_empty()
        self.selected_account = None
        self.info_frame.hide()
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    # ---- «جديد» / «تعديل» -----------------------------------------------------
    def new_record(self, *_signal: Any, parent_type_id: Any = None, account_id: Any = None,
                   account_parent_id: Any = None) -> None:
        """``*_signal``: زرار «جديد» بيبعت ``checked`` — مش رقم."""
        if not self.can("create"):
            return
        super().new_record()
        if self.mode != "new":
            return
        self.info_frame.hide()
        self._load_choices()
        try:
            defaults = self.service.new_defaults(parent_type_id)
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        self.parent_combo.blockSignals(True)
        self.parent_combo.setCurrentIndex(max(0, self.parent_combo.findData(defaults["parent_id"])))
        self.parent_combo.blockSignals(False)
        if account_parent_id is not None:
            defaults.update(account_mode="auto", account_parent_id=account_parent_id)
        if account_id is not None:
            defaults.update(account_mode="pick", account_id=account_id)
        if defaults.get("account_parent_id") is not None:
            self.account_parent_combo.setCurrentIndex(
                max(0, self.account_parent_combo.findData(defaults["account_parent_id"])))
        if defaults.get("account_id") is not None and self.account_pick.findData(defaults["account_id"]) >= 0:
            self.account_pick.setCurrentIndex(self.account_pick.findData(defaults["account_id"]))
        (self.mode_auto if defaults["account_mode"] == "auto" else self.mode_pick).setChecked(True)
        self._sync_account_widgets()
        self._show_account()
        self.children_view.setText("—")
        self.name_ar.setFocus()

    def new_sub_type(self) -> None:
        """«+ نوع فرعي تحت المحدد»: نوع جديد أبوه النوع المحدد وحسابه حساب الأب."""
        if self.current_id is None or self.mode in EDITING:
            return
        self.new_record(parent_type_id=self.current_id)

    def new_on_account(self) -> None:
        """«🧾 نوع جديد على الحساب ده» (فرعي ← حساب موجود · رئيسي ← حساب جديد تحته)."""
        row = self.selected_account
        if row is None or self.mode in EDITING:
            return
        if row["is_leaf"]:
            self.new_record(account_id=row["id"])
        else:
            self.new_record(account_parent_id=row["id"])

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        record = self.current_record or {}
        keep = {"id": record.get("parent_id"), "name_ar": record.get("parent_name"), "code": record.get("parent_code")}
        self._load_choices(exclude_id=int(self.current_id), keep_parent=keep)
        super().edit_record()

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل", f"عايز {question} النوع «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.service.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        self.load(self.current_id)
        messages.info(self, "تم", "تم تفعيل النوع." if new_state else
                      "تم إيقاف النوع (مش هيتختار في أي مصروف جديد).")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_type(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        self.name_ar.setText(record.get("name_ar") or "")
        self.name_en.setText(record.get("name_en") or "")
        parent_id = record.get("parent_id")
        self.parent_combo.blockSignals(True)
        if parent_id is not None and self.parent_combo.findData(parent_id) < 0:
            self.parent_combo.addItem(f"{record.get('parent_name') or ''} ({record.get('parent_code') or ''})",
                                      parent_id)
        self.parent_combo.setCurrentIndex(max(0, self.parent_combo.findData(parent_id)))
        self.parent_combo.blockSignals(False)
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.notes.setText(record.get("notes") or "")
        self.account_view.setText(f"⁦{record.get('account_code') or ''}⁩ {record.get('account_name') or ''}")
        self.account_parent.setText(f"⁦{record.get('account_parent_code') or ''}⁩ "
                                    f"{record.get('account_parent_name') or ''}"
                                    if record.get("account_parent_code") else "")
        shared = [t["name_ar"] for t in self.types if t["account_id"] == record.get("account_id")
                  and t["id"] != record.get("id")]
        self.shared_view.setText("، ".join(shared) if shared else "—")
        self.cc_label.setText("⚠ الحساب ده «لازم مركز تكلفة» ← المركز هيبقى إجباري في المصروف."
                              if record.get("requires_cost_center") else "")
        kids = [t["name_ar"] for t in self.types if t.get("parent_id") == record.get("id")]
        self.children_view.setText("، ".join(kids) if kids else "—")

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.notes, self.account_view, self.account_parent,
                     self.shared_view, self.children_view):
            edit.clear()
        self.parent_combo.blockSignals(True)
        self.parent_combo.setCurrentIndex(0)
        self.parent_combo.blockSignals(False)
        self.is_active.setChecked(True)
        self.cc_label.setText("")
        self.mode_pick.setChecked(True)

    def collect(self) -> dict:
        payload = {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                   "parent_id": self.parent_combo.currentData(), "notes": self.notes.text(),
                   "is_active": self.is_active.isChecked()}
        if self.mode == "new":
            payload["account_mode"] = "pick" if self.mode_pick.isChecked() else "auto"
            payload["account_id"] = self.account_pick.currentData()
            payload["account_parent_id"] = self.account_parent_combo.currentData()
        return payload

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_type(None if record_id is None else int(record_id), payload,
                                      expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_type(int(record_id), expected_version=self.current_version)
