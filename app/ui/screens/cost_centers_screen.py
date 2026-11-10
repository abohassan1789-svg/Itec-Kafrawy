"""شاشة «مراكز التكلفة» — النموذج 1 «تبويب لكل مركز رئيسي + شجرة + فورم» (اختيار المستخدم 2026-11-09).

```
┌ [مركز جديد][+ فرعي تحت المحدد][تعديل][حفظ][حذف][إلغاء][تحديث][عرض الكل][طي الكل][تصدير إكسل][خروج] (عرض) ┐
├ [1 الإدارة العامة (5)] [2 الأنشطة الفنية (9)] [3 المبيعات والتسويق (4)] [4 المخازن والنقل (3)] ──────┤
│ ┌ مراكز التكلفة ────────────────┐ ┌ بيانات المركز ─────────────────────────────────┐ │
│ │ [🔍 بحث…]                     │ │ الكود · الاسم ع/E · تحت مركز · المستوى 🔒        │ │
│ │ 📁 2 الأنشطة الفنية           │ │ رئيسي/فرعي · النوع · المسؤول · ملاحظات · نشط     │ │
│ │   📁 21 BMS — أنظمة…          │ ├ مربوط بيه ──────────────────────────────────────┤ │
│ │     📄 2101 BMS — مشروعات     │ │ المسار · اللي تحته · أوامر التوريد · القيود        │ │
│ │ 9 مراكز · 7 فرعي · 2 رئيسي     │ │ الحسابات اللي «لازم مركز تكلفة»                  │ │
└───────────────────────────────────┴──────────────────────────────────────────────────┘
```
* نفس شكل «شجرة الحسابات» (النموذج 6): تبويب لكل مركز رئيسي (بعدد اللي تحته) — جوه كل تبويب شجرته بس
  يمين والفورم شمال، و«عرض الكل / طي الكل». التبويبات بتتبني من المراكز الرئيسية (مركز رئيسي جديد = تبويب جديد).
* «مركز جديد»: تحت المحدد لو رئيسي، أو جنبه (تحت أبوه) لو فرعي — والكود بيتقترح لوحده والنوع من الأب.
  «تحت مركز» فيه «— مركز رئيسي (أول مستوى)» عشان تعمل تبويب جديد.
* «+ فرعي تحت المحدد»: رئيسي ← جديد تحته · فرعي ← سؤال «تحوّله رئيسي وتفرّع منه؟» ← «جديد» تحته، ومع «حفظ»
  المركز بيتحوّل رئيسي + «<الاسم> — عام» + الجديد في نفس الـ transaction. «إلغاء» = ولا حاجة اتغيّرت.
* F1 بيدوّر في كل المراكز وبينقل للتبويب الصح. الشجرة بتتقفل في «جديد / تعديل».
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QBrush, QColor, QRegularExpressionValidator
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
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.cost_center_service import (
    BRANCH_BUTTON,
    CENTER_TYPES,
    MAX_LEVEL,
    CostCenterService,
    matching_ids,
    root_id_of,
    visible_ids,
)
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, RIGHT, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import BORDER_CARD, GREEN, ROW_SELECTED, TEXT, TEXT_MUTED

DEFAULT_OPEN_DEPTH = 3          # أول فتح: المستوى 1 و2 مفتوحين (يبان لحد المستوى 3)
TYPE_COLOR, OFF_COLOR, LEVEL1_BG = "#92400E", "#94A3B8", "#E8F5EC"
ROOT_PARENT = "— مركز رئيسي (أول مستوى — تبويب جديد)"
COL_NAME, COL_TYPE, COL_STATUS = 0, 1, 2
INFO_STYLE = (f"background:#F8FAFC; color:{TEXT}; border:1px solid {BORDER_CARD}; border-radius:6px; "
              "padding:4px 8px; font-weight:600;")


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def center_label(row: dict) -> str:
    """«📁  21  BMS — أنظمة إدارة المباني» — الكود معزول عشان مايتقلبش جوه الكلام العربي."""
    return f"{'📄' if row['is_leaf'] else '📁'}  {ltr(row['code'])}  {row['name_ar']}"


class CostCentersScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "cost_centers"
    TITLE = "مراكز التكلفة"
    ICON = "🎯"
    FORM_TITLE = "بيانات المركز"
    LIST_TITLE = "مراكز التكلفة"
    NEW_TEXT = "مركز جديد"
    SEARCH_PLACEHOLDER = "🔍 بحث بالكود / الاسم / المسؤول…"
    # أعمدة التصدير ونافذة البحث F1 (الشجرة نفسها ليها أعمدتها).
    COLUMNS = [("code", "الكود"), ("name_ar", "المركز"), ("parent_code", "تحت"), ("level", "المستوى"),
               ("is_leaf", "رئيسي/فرعي"), ("center_type", "النوع"), ("manager_name", "المسؤول"),
               ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, centers: CostCenterService | None = None, parent: QWidget | None = None) -> None:
        self.centers = centers or CostCenterService()
        self.all_rows: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self.current_root: Any = None
        self.items: dict[int, QTreeWidgetItem] = {}
        self.root_tabs: dict[int, QPushButton] = {}
        self._expanded: dict[Any, set[int]] = {}      # الفروع المفتوحة في كل تبويب (بتفضل بعد التحديث)
        self._last_selected: dict[Any, int] = {}      # آخر مركز اتفتح في كل تبويب
        self._filling = False
        self._before_new: Any = None                  # المركز اللي كان مفتوح قبل «جديد» (بيرجعله «إلغاء»)
        self._split: dict | None = None               # «+ فرعي تحت المحدد» على مركز فرعي (بيتحفظ مع «حفظ»)
        try:
            self.required_accounts = self.centers.accounts_requiring()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self.required_accounts = []
        super().__init__(parent)
        roots = self._roots()
        self.switch_root(roots[0]["id"] if roots else None)

    # ---- البناء ------------------------------------------------------------
    @staticmethod
    def _locked() -> QLineEdit:
        edit = QLineEdit()
        edit.setReadOnly(True)
        edit.setEnabled(False)
        return edit

    @staticmethod
    def _form(box: QGroupBox) -> QFormLayout:
        form = QFormLayout(box)
        form.setSpacing(8)
        form.setContentsMargins(14, 20, 14, 12)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        return form

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(20)
        self.code_edit.setPlaceholderText("أرقام — بيبدأ بكود الأب")
        self.code_edit.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]{0,20}"), self.code_edit))
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("مثال: BMS — مشروعات")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setPlaceholderText("English name")
        self.parent_combo = QComboBox()
        self.parent_combo.currentIndexChanged.connect(self._parent_changed)
        self.level_edit = self._locked()
        self.main_radio = QRadioButton("رئيسي (تجميعي)")
        self.leaf_radio = QRadioButton("فرعي (بيتكتب في القيد)")
        self.leaf_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.main_radio)
        group.addButton(self.leaf_radio)
        kind = QHBoxLayout()
        kind.setSpacing(18)
        kind.addWidget(self.main_radio)
        kind.addWidget(self.leaf_radio)
        kind.addStretch(1)
        self.type_combo = QComboBox()
        for key, text in CENTER_TYPES.items():
            self.type_combo.addItem(text, key)
        self.type_combo.setToolTip("بيتقترح من المركز الأب — وبيفرّق في التقارير (مصروفات الإدارة قصاد التشغيل…)")
        self.manager_edit = QLineEdit()
        self.manager_edit.setMaxLength(120)
        self.manager_edit.setPlaceholderText("اسم المسؤول عن المركز")
        self.notes_edit = QLineEdit()
        self.notes_edit.setMaxLength(500)
        self.is_active = QCheckBox("نشط")
        form.addRow("الكود *", self.code_edit)
        form.addRow("الاسم عربي *", self.name_ar)
        form.addRow("الاسم إنجليزي", self.name_en)
        form.addRow("تحت مركز *", self.parent_combo)
        form.addRow("المستوى", self.level_edit)
        form.addRow("المركز", kind)
        form.addRow("النوع", self.type_combo)
        form.addRow("المسؤول", self.manager_edit)
        form.addRow("ملاحظات", self.notes_edit)
        form.addRow("الحالة", self.is_active)
        # سطر التفريع: بيظهر بس في «+ فرعي تحت المحدد» على مركز فرعي.
        self.split_note = QLabel()
        self.split_note.setWordWrap(True)
        self.split_note.setAlignment(RIGHT)
        self.split_note.setStyleSheet("background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; "
                                      "border-radius:6px; padding:2px 10px; font-weight:700;")
        form.addRow(self.split_note)
        self._split_form = form
        form.setRowVisible(self.split_note, False)

    def _build_links(self) -> QGroupBox:
        box = QGroupBox("مربوط بيه")
        form = self._form(box)
        self.path_label = QLabel()
        self.kids_label = QLabel()
        self.projects_label = QLabel("مع شاشة «أوامر التوريد» (البند الجاي) — كل أمر توريد على مركز فرعي")
        self.journal_label = QLabel("مع القيود (المرحلة 4) — والفلتر على الأب بيجمع كل اللي تحته")
        codes = " · ".join(ltr(a["code"]) for a in self.required_accounts)
        self.accounts_label = QLabel(f"{len(self.required_accounts)} حساب: {codes}" if codes else "مفيش")
        self.accounts_label.setToolTip(
            "الحسابات اللي عليها «لازم مركز تكلفة» في شجرة الحسابات — أي سطر قيد عليها من غير مركز فرعي نشط "
            "هيترفض (المرحلة 4):\n" + "\n".join(f"{a['code']} — {a['name_ar']}" for a in self.required_accounts))
        for label in (self.path_label, self.kids_label, self.projects_label, self.journal_label, self.accounts_label):
            label.setWordWrap(True)
            label.setAlignment(RIGHT)
            label.setStyleSheet(INFO_STYLE)
        form.addRow("المسار", self.path_label)
        form.addRow("اللي تحته", self.kids_label)
        form.addRow("أوامر التوريد", self.projects_label)
        form.addRow("القيود والرصيد", self.journal_label)
        form.addRow("لازم مركز تكلفة", self.accounts_label)
        align_form_right(box)
        return box

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        # «+ فرعي تحت المحدد» جنب «مركز جديد» على طول.
        self.branch_button = self._button(BRANCH_BUTTON, "new", self.branch_record)
        self.branch_button.setToolTip("مركز جديد تحت المحدد — ولو فرعي بيتحوّل رئيسي ويتفرّع منه")
        layout.insertWidget(layout.indexOf(self.new_button) + 1, self.branch_button)
        self.branch_button.setVisible(self.has_action("create"))
        self.expand_button = self._button("عرض الكل", "rate", self.expand_all)
        self.collapse_button = self._button("طي الكل", "toggle", self.collapse_all)
        self.expand_button.setToolTip("يفتح الشجرة كلها")
        self.collapse_button.setToolTip("يقفل الشجرة (المركز الرئيسي بس)")
        layout.addWidget(self.expand_button)
        layout.addWidget(self.collapse_button)

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        outer = QVBoxLayout()
        outer.setSpacing(0)
        self.tabs_row = QHBoxLayout()
        self.tabs_row.setSpacing(4)
        self.tabs_row.addStretch(1)
        self.root_group = QButtonGroup(self)
        self.root_group.setExclusive(True)
        outer.addLayout(self.tabs_row)
        self.root_panel = QFrame()
        self.root_panel.setObjectName("rootPanel")
        self.root_panel.setStyleSheet(
            f"QFrame#rootPanel {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; "
            f"border-top:3px solid {GREEN}; border-radius:0 0 8px 8px; }}")
        panel = QHBoxLayout(self.root_panel)
        panel.setContentsMargins(8, 4, 8, 8)
        panel.setSpacing(10)

        # يمين: الشجرة (البحث وعداد المراكز بتوع الـ Base بيتنقلوا هنا)
        tree_box = QGroupBox(self.LIST_TITLE)
        tree_layout = QVBoxLayout(tree_box)
        tree_layout.setContentsMargins(10, 16, 10, 8)
        tree_layout.setSpacing(6)
        old = list_box.layout()
        old.removeWidget(self.search)
        old.removeWidget(self.count_label)
        try:
            self.search.textChanged.disconnect(self.refresh_list)
        except (RuntimeError, TypeError):
            pass
        self.search.textChanged.connect(self._search_changed)
        tree_layout.addWidget(self.search)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["المركز", "النوع", "الحالة"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(22)
        self.tree.setAnimated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_TYPE, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeToContents)
        self.tree.setStyleSheet(
            f"QTreeWidget {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; border-radius:6px; }}"
            f"QTreeWidget::item {{ padding:4px 2px; border-bottom:1px solid #F1F5F9; }}"
            f"QTreeWidget::item:selected {{ background:{ROW_SELECTED}; color:{TEXT}; }}")
        self.tree.itemSelectionChanged.connect(self._tree_selection)
        self.tree.itemExpanded.connect(lambda _i: self._remember_expansion())
        self.tree.itemCollapsed.connect(lambda _i: self._remember_expansion())
        tree_layout.addWidget(self.tree, 1)
        tree_layout.addWidget(self.count_label)
        panel.addWidget(tree_box, 20)                        # RTL: أول عنصر = يمين

        # شمال: الفورم + «مربوط بيه»
        forms = QVBoxLayout()
        forms.setSpacing(10)
        forms.addWidget(form_box)
        forms.addWidget(self._build_links())
        forms.addStretch(1)
        panel.addLayout(forms, 21)
        outer.addWidget(self.root_panel, 1)
        content.addLayout(outer, 1)
        # الجدول بتاع الـ Base بيفضل مستخبي: مصدر «تصدير إكسل» ونافذة البحث F1 (كل المراكز).
        list_box.setParent(self)
        list_box.hide()

    # ---- الحقول حسب الوضع -------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.code_edit, self.name_ar, self.name_en, self.parent_combo, self.main_radio, self.leaf_radio,
                self.type_combo, self.manager_edit, self.notes_edit, self.is_active]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        rec = self.current_record if mode == "edit" else None
        kids = bool(rec and rec.get("has_children"))
        self.code_edit.setEnabled(editing and not kids)
        self.parent_combo.setEnabled(editing and not kids)
        self.leaf_radio.setEnabled(editing and not kids)
        self.main_radio.setEnabled(editing and self._form_level() < MAX_LEVEL)
        if mode != "new":
            self._split = None
        if self._split is not None:
            self.parent_combo.setEnabled(False)              # التفريع تحت المركز ده بس
        self._split_form.setRowVisible(self.split_note, self._split is not None)
        self.tree.setEnabled(not editing)
        for button in self.root_tabs.values():
            button.setEnabled(not editing)

    def update_extra_buttons(self) -> None:
        editing = self.mode in EDITING
        self.expand_button.setEnabled(not editing)
        self.collapse_button.setEnabled(not editing)
        rec = self.current_record
        reason = self._branch_blocked(rec)
        self.branch_button.setEnabled(not editing and rec is not None and self.can("create") and not reason)
        self.branch_button.setToolTip(reason or "مركز جديد تحت المحدد — ولو فرعي بيتحوّل رئيسي ويتفرّع منه")
        if rec and rec.get("has_children"):
            self.delete_button.setEnabled(False)
            self.delete_button.setToolTip("المركز تحته مراكز — امسحهم الأول (أو وقّفه)")
        else:
            self.delete_button.setToolTip("")

    def _branch_blocked(self, rec: dict | None) -> str:
        """ليه «+ فرعي تحت المحدد» مقفول للمركز ده ("" = شغال)."""
        if not rec or not rec.get("is_leaf"):
            return ""
        if int(rec.get("level") or 1) >= MAX_LEVEL:
            return "المركز في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه"
        if not rec.get("is_active"):
            return "المركز موقوف — نشّطه الأول وبعدين فرّع منه"
        if not self.can("edit"):
            return "التفريع بيعدّل المركز (فرعي ← رئيسي) — محتاج صلاحية «تعديل»"
        return ""

    def _form_level(self) -> int:
        try:
            return int(self.level_edit.text())
        except ValueError:
            return 1

    # ---- التبويبات والشجرة -------------------------------------------------
    def _roots(self) -> list[dict]:
        return [r for r in self.all_rows if r["parent_id"] is None]

    def _root_of(self, row: dict) -> Any:
        return root_id_of(self.by_id, row)

    def _rebuild_tabs(self) -> None:
        """التبويبات = المراكز الرئيسية (بتتبني من جديد لو اتضاف / اتمسح مركز رئيسي)."""
        roots = [r["id"] for r in self._roots()]
        if roots == list(self.root_tabs):
            return
        for button in self.root_tabs.values():
            self.root_group.removeButton(button)
            self.tabs_row.removeWidget(button)
            button.deleteLater()
        self.root_tabs = {}
        for index, root_id in enumerate(roots):
            button = QPushButton()
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(38)
            button.setStyleSheet(
                f"QPushButton {{ background:#FFFFFF; color:{GREEN}; border:1px solid {BORDER_CARD}; "
                f"border-top:3px solid {GREEN}; border-radius:6px; padding:6px 16px; font-weight:900; }}"
                f"QPushButton:hover {{ background:#F8FAFC; }}"
                f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border:1px solid {GREEN}; }}"
                f"QPushButton:disabled {{ color:{OFF_COLOR}; border-top:3px solid #E2E8F0; }}"
                f"QPushButton:checked:disabled {{ background:{GREEN}; color:#E2E8F0; border:1px solid {GREEN}; }}")
            button.clicked.connect(lambda _checked=False, k=root_id: self._tab_clicked(k))
            self.root_group.addButton(button)
            self.tabs_row.insertWidget(index, button)
            self.root_tabs[root_id] = button
            button.setEnabled(self.mode not in EDITING)

    def _update_tabs(self, keyword: str = "") -> None:
        self._rebuild_tabs()
        totals = self.centers.summary(self.all_rows)
        matches = matching_ids(self.all_rows, keyword) if keyword else None
        for root_id, button in self.root_tabs.items():
            root = self.by_id[root_id]
            count = totals.get(root_id, {}).get("total", 0)  # وقت البحث = عدد اللي بيطابق في التبويب ده
            if matches is not None:
                count = sum(1 for r in self.all_rows if r["id"] in matches and self._root_of(r) == root_id)
            button.setText(f"{ltr(root['code'])} {root['name_ar']} ({ltr(count)})")
            button.setToolTip(f"{root['code']} — {root['name_ar']}" + ("" if root["is_active"] else " (موقوف)"))
            button.setChecked(root_id == self.current_root)

    def _root_rows(self) -> list[dict]:
        return [r for r in self.all_rows if self._root_of(r) == self.current_root]

    def _searching(self) -> bool:
        return bool(self.search.text().strip())

    def _build_tree(self) -> None:
        keyword = self.search.text().strip()
        rows = self._root_rows()
        shown = visible_ids(rows, keyword)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        bold_font = self.tree.font()
        bold_font.setBold(True)
        for row in rows:                                     # بترتيب الكود ← الأب دايماً قبل اللي تحته
            if shown is not None and row["id"] not in shown:
                continue
            item = QTreeWidgetItem([center_label(row), CENTER_TYPES.get(row["center_type"], ""),
                                    "" if row["is_active"] else "موقوف"])
            item.setData(COL_NAME, Qt.UserRole, row["id"])
            tip = f"{row['code']} — {row['name_ar']}" + (f" ({row['name_en']})" if row.get("name_en") else "")
            if row.get("manager_name"):
                tip += f"\nالمسؤول: {row['manager_name']}"
            item.setToolTip(COL_NAME, tip)
            item.setTextAlignment(COL_TYPE, Qt.AlignCenter)
            item.setTextAlignment(COL_STATUS, Qt.AlignCenter)
            if not row["is_leaf"]:
                item.setFont(COL_NAME, bold_font)
            if row["level"] == 1:
                for col in range(3):
                    item.setBackground(col, QBrush(QColor(LEVEL1_BG)))
                item.setForeground(COL_NAME, QBrush(QColor(GREEN)))
            item.setForeground(COL_TYPE, QBrush(QColor(TYPE_COLOR)))
            item.setForeground(COL_STATUS, QBrush(QColor(TEXT_MUTED)))
            if not row["is_active"]:
                for col in (COL_NAME, COL_TYPE):
                    item.setForeground(col, QBrush(QColor(OFF_COLOR)))
            parent_item = self.items.get(row.get("parent_id"))
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            self.items[row["id"]] = item
        if shown is not None:
            self.tree.expandAll()
        elif self.current_root in self._expanded:
            for record_id in self._expanded[self.current_root]:
                if record_id in self.items:
                    self.items[record_id].setExpanded(True)
        else:
            for record_id, item in self.items.items():
                item.setExpanded(self.by_id[record_id]["level"] < DEFAULT_OPEN_DEPTH)
            self._expanded[self.current_root] = {i for i, it in self.items.items() if it.isExpanded()}
        self._select_item(self.current_id)
        self.tree.blockSignals(False)
        self._remember_expansion()
        self._update_count(rows, shown)

    def _update_count(self, rows: list[dict], shown: set | None) -> None:
        total = len(rows)
        leaf = sum(1 for r in rows if r["is_leaf"])
        off = sum(1 for r in rows if not r["is_active"])
        text = (f"{ltr(total)} مركز · {ltr(leaf)} فرعي (بيتكتب في القيد) · {ltr(total - leaf)} رئيسي · "
                f"موقوف {ltr(off)}")
        if shown is not None:
            found = len(matching_ids(rows, self.search.text()))
            text = f"نتايج البحث: {ltr(found)} من {ltr(total)} مركز (وظاهر معاها المراكز اللي فوقها)"
        if not self.all_rows:
            text = "مفيش مراكز تكلفة لسه — دوس «مركز جديد» وابدأ بمركز رئيسي"
        self.count_label.setText(text)

    def _select_item(self, record_id: Any) -> None:
        item = self.items.get(record_id) if record_id is not None else None
        if item is None:
            self.tree.clearSelection()
            return
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        item.setSelected(True)
        self.tree.scrollToItem(item)

    def _remember_expansion(self) -> None:
        if self._searching() or not self.items:
            return
        self._expanded[self.current_root] = {i for i, it in self.items.items() if it.isExpanded()}

    def expand_all(self) -> None:
        self.tree.expandAll()
        self._remember_expansion()

    def collapse_all(self) -> None:
        self.tree.collapseAll()
        self._remember_expansion()

    def switch_root(self, root_id: Any, open_center: bool = True) -> None:
        """يفتح تبويب المركز الرئيسي (والمركز اللي كان مفتوح فيه آخر مرة، أو المركز الرئيسي نفسه)."""
        if self.mode in EDITING:
            return
        self.current_root = root_id
        self._update_tabs(self.search.text().strip())
        self._build_tree()
        if not open_center:
            return
        target = self._last_selected.get(root_id)
        if target not in self.by_id:
            target = root_id if root_id in self.by_id else None
        if target is not None:
            self.load(target)
        else:
            self.show_empty()

    def _tab_clicked(self, root_id: Any) -> None:
        if root_id == self.current_root or self.mode in EDITING:
            if self.current_root in self.root_tabs:
                self.root_tabs[self.current_root].setChecked(True)
            return
        self.switch_root(root_id)

    def _search_changed(self, _text: str) -> None:
        self._update_tabs(self.search.text().strip())
        self._build_tree()

    def _tree_selection(self) -> None:
        if self.mode in EDITING:
            return
        item = self.tree.currentItem()
        if item is None or not item.isSelected():
            return
        record_id = item.data(COL_NAME, Qt.UserRole)
        if record_id is not None and record_id != self.current_id:
            self.load(record_id)

    # ---- القائمة (الـ Base) --------------------------------------------------
    def refresh_list(self) -> None:
        try:
            rows = self.fetch_rows("")
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
            rows = []
        self.all_rows = rows
        self.by_id = {r["id"]: r for r in rows}
        if self.current_root not in self.by_id or self.by_id[self.current_root]["parent_id"] is not None:
            roots = self._roots()
            self.current_root = roots[0]["id"] if roots else None
        self.table.blockSignals(True)
        self.table.set_rows(rows)
        self.table.blockSignals(False)
        self._update_tabs(self.search.text().strip())
        self._build_tree()

    def load(self, record_id: Any) -> bool:
        if not super().load(record_id):
            return False
        self._reveal(int(record_id))
        return True

    def _reveal(self, record_id: int) -> None:
        """ينقل للتبويب بتاع المركز ويعلّم عليه في الشجرة (حتى لو البحث كان مخبّيه)."""
        if record_id not in self.by_id:
            self.refresh_list()                              # مركز جديد من جهاز تاني
            if record_id not in self.by_id:
                return
        root_id = self._root_of(self.by_id[record_id])
        self._last_selected[root_id] = record_id
        rebuild = root_id != self.current_root
        if record_id not in self.items and self._searching():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            rebuild = True
        self.current_root = root_id
        if rebuild or record_id not in self.items:
            self._update_tabs(self.search.text().strip())
            self._build_tree()
            return
        self.tree.blockSignals(True)
        self._select_item(record_id)
        self.tree.blockSignals(False)
        self._remember_expansion()

    def show_empty(self) -> None:
        super().show_empty()
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    # ---- «مركز جديد» --------------------------------------------------------
    def new_record(self) -> None:
        if not self.can("create"):
            return
        selected = self.current_record
        if selected is None:
            parent_id = None                                 # مفيش مراكز ← مركز رئيسي
        elif not selected["is_leaf"] and int(selected["level"]) < MAX_LEVEL:
            parent_id = selected["id"]                       # رئيسي ← تحته
        else:
            parent_id = selected["parent_id"]                # فرعي ← جنبه (تحت أبوه)
        self._open_new(parent_id)

    def _open_new(self, parent_id: Any) -> None:
        try:
            defaults = self.centers.new_defaults(parent_id)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر فتح مركز جديد", exc)
            return
        self._before_new = self.current_id
        BaseScreen.new_record(self)
        self.fill_form(defaults)
        self.set_mode("new")
        self.name_ar.setFocus()

    # ---- «+ فرعي تحت المحدد» ------------------------------------------------
    @staticmethod
    def branch_question(plan: dict) -> str:
        center, general = plan["center"], plan["general"]
        return (f"المركز «{ltr(center['code'])} {center['name_ar']}» فرعي — عايز تحوّله رئيسي وتفرّع منه؟"
                f"\n\nالبرنامج هيعمل تحته لوحده «{ltr(general['code'])} {general['name_ar']}» بنفس النوع والمسؤول"
                " — وأوامر التوريد والقيود اللي على المركز هتتنقل له (لما يتعملوا)."
                f"\n\nوالمركز الجديد اللي هتكتبه كوده «{ltr(plan['defaults']['code'])}»."
                "\nمفيش حاجة بتتحفظ غير لما تدوس «حفظ».")

    def branch_record(self) -> None:
        """رئيسي ← «جديد» تحته · فرعي ← سؤال ← «جديد» تحته (والتحويل لرئيسي بيتحفظ مع «حفظ»)."""
        rec = self.current_record
        if rec is None or self.mode in EDITING or not self.can("create"):
            return
        if not rec["is_leaf"]:
            self._open_new(rec["id"])                        # رئيسي ← تحته على طول
            return
        reason = self._branch_blocked(rec)
        if reason:
            messages.warn(self, "تفريع مركز", reason + ".")
            return
        try:
            plan = self.centers.branch_plan(int(rec["id"]))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التفريع", exc)
            return
        if not messages.ask_yes_no(self, "تفريع مركز فرعي", self.branch_question(plan)):
            return
        version = self.current_version if self.current_version is not None else plan["expected_version"]
        self._before_new = self.current_id
        BaseScreen.new_record(self)
        self._split = {"id": int(rec["id"]), "version": version, "plan": plan}
        self.split_note.setText(f"🌿 مع «حفظ»: «{ltr(rec['code'])}» هيتحوّل رئيسي + "
                                f"«{ltr(plan['general']['code'])} … — عام» + المركز ده تحته")
        self.split_note.setToolTip(f"«{ltr(rec['code'])} {rec['name_ar']}» هيتحوّل رئيسي مع «حفظ» "
                                   f"(و«إلغاء» = ولا حاجة اتغيّرت).\nوالبرنامج هيعمل "
                                   f"«{ltr(plan['general']['code'])} {plan['general']['name_ar']}».")
        self.fill_form(plan["defaults"])
        self.set_mode("new")
        self.name_ar.setFocus()

    def reload_after_conflict(self) -> None:
        """«تحديث» بعد تعارض في التفريع: يفتح المركز اللي كنا بنفرّع منه بآخر نسخة."""
        if self._split is not None:
            target = self._split["id"]
            self._split = None
            self.mode = "view"
            self.refresh_list()
            if not self.load(target):
                self.show_empty()
            return
        super().reload_after_conflict()

    def cancel(self) -> None:
        """«إلغاء» في «جديد» بيرجع للمركز اللي كان مفتوح (مش فورم فاضي)."""
        if self.mode == "new" and self._before_new in self.by_id:
            self.load(self._before_new)
            return
        super().cancel()

    def _parent_changed(self, _index: int) -> None:
        """«تحت مركز» اتغيّر: الكود المقترح والمستوى — وفي «جديد» كمان النوع من الأب."""
        if self._filling or self.mode not in EDITING:
            return
        parent_id = self.parent_combo.currentData()
        try:
            defaults = self.centers.new_defaults(parent_id)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل المركز الأب", exc)
            return
        rec = self.current_record or {}
        self._filling = True
        try:
            if self.mode == "edit" and parent_id == rec.get("parent_id"):
                self.code_edit.setText(rec.get("code") or "")
            else:
                self.code_edit.setText(defaults["code"])
            self.level_edit.setText(str(defaults["level"]))
            if self.mode == "new":
                self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(defaults["center_type"])))
                (self.leaf_radio if defaults["is_leaf"] else self.main_radio).setChecked(True)
                self._fill_links({"parent_id": parent_id})
        finally:
            self._filling = False
        self.main_radio.setEnabled(int(defaults["level"]) < MAX_LEVEL)
        if int(defaults["level"]) >= MAX_LEVEL:
            self.leaf_radio.setChecked(True)

    # ---- تعبئة الفورم -------------------------------------------------------
    def _subtree(self, record_id: Any) -> list[dict]:
        out, todo = [], [record_id]
        while todo:
            current = todo.pop()
            kids = [r for r in self.all_rows if r["parent_id"] == current]
            out.extend(kids)
            todo.extend(k["id"] for k in kids)
        return out

    def _fill_parents(self, selected: Any, exclude: Any) -> None:
        self.parent_combo.clear()
        self.parent_combo.addItem(ROOT_PARENT, None)
        for row in self.all_rows:
            if row["id"] == exclude:
                continue
            usable = not row["is_leaf"] and row["is_active"] and int(row["level"]) < MAX_LEVEL
            if usable or row["id"] == selected:
                indent = "    " * (int(row["level"]) - 1)
                self.parent_combo.addItem(f"{indent}{row['code']} — {row['name_ar']}", row["id"])
        self.parent_combo.setCurrentIndex(max(0, self.parent_combo.findData(selected)))

    def _fill_links(self, record: dict) -> None:
        record_id = record.get("id")
        if record_id is None:                                # «جديد»: مسار الأب + المركز الجديد
            chain, node = ["(المركز الجديد)"], self.by_id.get(record.get("parent_id"))
            while node is not None:
                chain.append(f"{ltr(node['code'])} {node['name_ar']}")
                node = self.by_id.get(node.get("parent_id"))
            self.path_label.setText(" ◂ ".join(reversed(chain)))
            self.kids_label.setText("—")
            return
        chain, node = [], record
        while node is not None:
            chain.append(f"{ltr(node['code'])} {node['name_ar']}")
            node = self.by_id.get(node.get("parent_id"))
        self.path_label.setText(" ◂ ".join(reversed(chain)))
        subtree = self._subtree(record_id)
        if subtree:
            leaf = sum(1 for r in subtree if r["is_leaf"])
            self.kids_label.setText(f"{ltr(len(subtree))} مركز ({ltr(leaf)} فرعي) — الفلتر على المركز ده بيجمعهم كلهم")
        else:
            self.kids_label.setText("مفيش — مركز فرعي" if record.get("is_leaf") else "مفيش لسه")

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.code_edit.setText(record.get("code") or "")
            self.name_ar.setText(record.get("name_ar") or "")
            self.name_en.setText(record.get("name_en") or "")
            self._fill_parents(record.get("parent_id"), record.get("id"))
            self.level_edit.setText(str(record.get("level") or 1))
            (self.leaf_radio if record.get("is_leaf", True) else self.main_radio).setChecked(True)
            self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(record.get("center_type") or "admin")))
            self.manager_edit.setText(record.get("manager_name") or "")
            self.notes_edit.setText(record.get("notes") or "")
            self.is_active.setChecked(bool(record.get("is_active", True)))
            self._fill_links(record)
        finally:
            self._filling = False

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.code_edit, self.name_ar, self.name_en, self.level_edit, self.manager_edit,
                         self.notes_edit):
                edit.clear()
            self.parent_combo.clear()
            self.leaf_radio.setChecked(True)
            self.type_combo.setCurrentIndex(0)
            self.is_active.setChecked(True)
            self.path_label.setText("—")
            self.kids_label.setText("—")
        finally:
            self._filling = False

    def collect(self) -> dict:
        return {"code": self.code_edit.text().strip(), "name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                "parent_id": self.parent_combo.currentData(), "is_leaf": self.leaf_radio.isChecked(),
                "center_type": self.type_combo.currentData(), "manager_name": self.manager_edit.text(),
                "notes": self.notes_edit.text(), "is_active": self.is_active.isChecked()}

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.centers.list_centers()

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.centers.get_center(int(record_id))

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "center_type":
            return CENTER_TYPES.get(value, "")
        if key == "is_leaf":
            return "فرعي" if value else "رئيسي"
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def save_record(self, payload: dict, record_id: Any) -> Any:
        if record_id is None and self._split is not None:   # التفريع: الأب + العام + الجديد في transaction واحدة
            return self.centers.branch_center(self._split["id"], payload, expected_version=self._split["version"])
        return self.centers.save_center(None if record_id is None else int(record_id), payload,
                                        expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.centers.delete_center(int(record_id), expected_version=self.current_version)

    def delete(self) -> None:
        """بعد المسح بيفتح المركز اللي كان فوقه (مش شاشة فاضية)."""
        parent_id = (self.current_record or {}).get("parent_id")
        super().delete()
        if self.current_id is None:
            if parent_id in self.by_id:
                self.load(parent_id)
            else:
                roots = self._roots()
                self.switch_root(roots[0]["id"] if roots else None)
