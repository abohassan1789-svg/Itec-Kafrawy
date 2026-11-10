"""شاشة «شجرة الحسابات» — النموذج 6 «تبويب لكل نوع» + «عرض الكل / طي الكل» (اختيار المستخدم 2026-11-08).

```
┌ [حساب جديد][+ فرعي تحت المحدد][تعديل][حفظ][حذف][إلغاء][تحديث][عرض الكل][طي الكل][تصدير إكسل][خروج] (عرض) ┐
├ [1 الأصول (19)] [2 الخصوم (7)] [3 حقوق الملكية (5)] [4 الإيرادات (7)] [5 المصروفات (13)] ──────┤
│ ┌ دليل الحسابات ─────────────────┐ ┌ بيانات الحساب ─────────────────────────────────┐ │
│ │ [🔍 بحث…]                      │ │ الكود · الاسم ع/E · تحت حساب · المستوى · النوع 🔒 │ │
│ │ 📁 1 الأصول                    │ │ رئيسي/فرعي · الطبيعة                           │ │
│ │   📁 11 الأصول المتداولة       │ ├ التقارير والقيود ──────────────────────────────┤ │
│ │     📄 1103 العملاء   العملاء  │ │ القائمة 🔒 · التصنيف · التدفقات · الدور         │ │
│ │ 19 حساب · 13 فرعي · 6 رئيسي    │ │ لازم مع القيد · العملة · نشط · 🔒 نظامي          │ │
└───────────────────────────────────┴──────────────────────────────────────────────────┘
```
* فوق 5 تبويبات بالأنواع (بعدد حسابات كل نوع وبلونه) — جوه كل تبويب شجرة النوع ده بس يمين والفورم شمال.
* الشجرة: 📁 رئيسي (تجميعي) / 📄 فرعي (بيقبل قيود) + الدور + 🔒 نظامي / موقوف. البحث بيعرض اللي بيطابق وآباءه.
* «حساب جديد»: تحت المحدد لو رئيسي، أو جنبه (تحت أبوه) لو فرعي — والكود بيتقترح لوحده وبيورث من الأب.
* «+ فرعي تحت المحدد» (طلب المستخدم 2026-11-08): رئيسي ← جديد تحته · فرعي ← سؤال «تحوّله رئيسي وتفرّع منه؟»
  ← نعم = «جديد» تحته بالكود المقترح، ومع «حفظ» الحساب بيتحوّل رئيسي والجديد بيتضاف في نفس الـ transaction
  (ومع كل تفريع «<الاسم> — عام» بالدور والنظامي والربط). «إلغاء» = ولا حاجة اتغيّرت.
* F1 بيدوّر في الدليل كله وبينقل للتبويب الصح. الشجرة بتتقفل في «جديد / تعديل» زي الجدول.
* الرصيد قصاد كل حساب بييجي مع القيود (المرحلة 4).
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

from app.services.account_service import (
    BRANCH_BUTTON,
    CASH_FLOWS,
    CLASSIFICATIONS,
    MAX_LEVEL,
    NATURES,
    PARTY_KINDS,
    STATEMENTS,
    TYPES,
    AccountService,
    matching_ids,
    type_of,
    visible_ids,
)
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, RIGHT, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import BORDER_CARD, GREEN, ROW_SELECTED, TEXT, TEXT_MUTED

DEFAULT_OPEN_DEPTH = 3          # أول فتح: المستوى 1 و2 مفتوحين (يبان لحد المستوى 3) زي النموذج
ROLE_COLOR, OFF_COLOR, LEVEL1_BG = "#0F766E", "#94A3B8", "#E8F5EC"
NO_ROLE, NO_PARTY, ROOT_PARENT = "— من غير دور", "— من غير طرف", "— (جذر — مفيش أب)"
COL_NAME, COL_ROLE, COL_STATUS = 0, 1, 2


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def account_label(row: dict) -> str:
    """«📁  1103  العملاء» — الكود معزول عشان مايتقلبش جوه الكلام العربي."""
    return f"{'📄' if row['is_leaf'] else '📁'}  {ltr(row['code'])}  {row['name_ar']}"


def status_text(row: dict) -> str:
    parts = []
    if row.get("is_system"):
        parts.append("🔒 نظامي")
    if not row.get("is_active"):
        parts.append("موقوف")
    return " · ".join(parts)


class ChartOfAccountsScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "chart_of_accounts"
    TITLE = "شجرة الحسابات"
    ICON = "🌳"
    FORM_TITLE = "بيانات الحساب"
    LIST_TITLE = "دليل الحسابات"
    NEW_TEXT = "حساب جديد"
    SEARCH_PLACEHOLDER = "🔍 بحث بالكود / الاسم…"
    # أعمدة التصدير ونافذة البحث F1 (الشجرة نفسها ليها أعمدتها).
    COLUMNS = [("code", "الكود"), ("name_ar", "الحساب"), ("level", "المستوى"), ("account_type", "النوع"),
               ("is_leaf", "رئيسي/فرعي"), ("nature", "الطبيعة"), ("role_name", "الدور"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, accounts: AccountService | None = None, parent: QWidget | None = None) -> None:
        self.accounts = accounts or AccountService()
        self.all_rows: list[dict] = []
        self.by_id: dict[int, dict] = {}
        self.current_type = "asset"
        self.items: dict[int, QTreeWidgetItem] = {}
        self._expanded: dict[str, set[int]] = {}      # الفروع المفتوحة في كل تبويب (بتفضل بعد التحديث)
        self._last_selected: dict[str, int] = {}      # آخر حساب اتفتح في كل تبويب
        self._filling = False
        self._before_new: Any = None                  # الحساب اللي كان مفتوح قبل «جديد» (بيرجعله «إلغاء»)
        self._split: dict | None = None               # «+ فرعي تحت المحدد» على حساب فرعي (بيتحفظ مع «حفظ»)
        try:
            self.roles = self.accounts.list_roles()
            self.currencies = self.accounts.list_currencies()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self.roles, self.currencies = [], []
        super().__init__(parent)
        self.switch_type("asset")

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

    def _pair(self, first: QRadioButton, second: QRadioButton) -> QHBoxLayout:
        group = QButtonGroup(self)
        group.addButton(first)
        group.addButton(second)
        row = QHBoxLayout()
        row.setSpacing(18)
        row.addWidget(first)
        row.addWidget(second)
        row.addStretch(1)
        return row

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setMaxLength(20)
        self.code_edit.setPlaceholderText("أرقام — بيبدأ بكود الأب")
        self.code_edit.setValidator(QRegularExpressionValidator(QRegularExpression("[0-9]{0,20}"), self.code_edit))
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("مثال: بنك القاهرة — جاري")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setPlaceholderText("English name")
        self.parent_combo = QComboBox()
        self.parent_combo.currentIndexChanged.connect(self._parent_changed)
        self.level_edit = self._locked()
        self.type_edit = self._locked()
        info = QHBoxLayout()
        info.setSpacing(8)
        info.addWidget(self.level_edit, 1)
        type_label = QLabel("نوع الحساب")
        type_label.setStyleSheet("font-weight:700;")
        info.addWidget(type_label)
        info.addWidget(self.type_edit, 2)
        self.main_radio = QRadioButton("رئيسي (تجميعي)")
        self.leaf_radio = QRadioButton("فرعي (بيقبل قيود)")
        self.leaf_radio.setChecked(True)
        self.dr_radio = QRadioButton("مدين")
        self.cr_radio = QRadioButton("دائن")
        self.dr_radio.setChecked(True)
        form.addRow("الكود *", self.code_edit)
        form.addRow("الاسم عربي *", self.name_ar)
        form.addRow("الاسم إنجليزي", self.name_en)
        form.addRow("تحت حساب *", self.parent_combo)
        form.addRow("المستوى", info)
        form.addRow("الحساب", self._pair(self.main_radio, self.leaf_radio))
        form.addRow("الطبيعة", self._pair(self.dr_radio, self.cr_radio))
        # سطر التفريع: بيظهر بس في «+ فرعي تحت المحدد» على حساب فرعي.
        self.split_note = QLabel()
        self.split_note.setWordWrap(True)
        self.split_note.setAlignment(RIGHT)
        self.split_note.setStyleSheet("background:#ECFDF5; color:#065F46; border:1px solid #A7F3D0; "
                                      "border-radius:6px; padding:2px 10px; font-weight:700;")
        form.addRow(self.split_note)
        self._split_form = form
        form.setRowVisible(self.split_note, False)

    def _build_reporting(self) -> QGroupBox:
        box = QGroupBox("التقارير والقيود")
        form = self._form(box)
        self.statement_edit = self._locked()
        self.class_combo = QComboBox()
        self.cash_flow_combo = QComboBox()
        for key, text in CASH_FLOWS.items():
            self.cash_flow_combo.addItem(text, key)
        self.role_combo = QComboBox()
        self.role_combo.currentIndexChanged.connect(self._role_changed)
        self.party_combo = QComboBox()
        self.party_combo.addItem(NO_PARTY, None)
        for key, text in PARTY_KINDS.items():
            self.party_combo.addItem(f"طرف ({text})", key)
        self.party_combo.setToolTip("لازم مع القيد على الحساب ده يتحدد طرف (عميل / مورد / موظف / شريك)")
        self.cost_center = QCheckBox("مركز تكلفة")
        posting = QHBoxLayout()
        posting.setSpacing(12)
        posting.addWidget(self.party_combo, 1)
        posting.addWidget(self.cost_center)
        self.currency_combo = QComboBox()
        for cur in self.currencies:
            self.currency_combo.addItem(f"{cur['code']} — {cur['name_ar']}", cur["code"])
        if self.currency_combo.findData("EGP") < 0:
            self.currency_combo.addItem("EGP", "EGP")
        self.is_active = QCheckBox("نشط")
        self.system_label = QLabel("🔒 نظامي (مايتمسحش)")
        self.system_label.setStyleSheet("background:#F1F5F9; color:#475569; border:1px solid #CBD5E1; "
                                        "border-radius:6px; padding:3px 8px; font-weight:800;")
        self.system_label.setToolTip("حساب البرنامج محتاجه: مايتمسحش ولا يتوقف، وكوده ومكانه ودوره ثابتين")
        self.system_label.hide()
        status = QHBoxLayout()
        status.setSpacing(12)
        status.addWidget(self.is_active)
        status.addWidget(self.system_label)
        status.addStretch(1)
        form.addRow("القائمة", self.statement_edit)
        form.addRow("التصنيف", self.class_combo)
        form.addRow("التدفقات النقدية", self.cash_flow_combo)
        form.addRow("الدور", self.role_combo)
        form.addRow("لازم مع القيد", posting)
        form.addRow("العملة", self.currency_combo)
        form.addRow("الحالة", status)
        align_form_right(box)
        return box

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        # «+ فرعي تحت المحدد» جنب «حساب جديد» على طول.
        self.branch_button = self._button(BRANCH_BUTTON, "new", self.branch_record)
        self.branch_button.setToolTip("حساب جديد تحت الحساب المحدد — ولو فرعي بيتحوّل رئيسي ويتفرّع منه")
        layout.insertWidget(layout.indexOf(self.new_button) + 1, self.branch_button)
        self.branch_button.setVisible(self.has_action("create"))
        self.expand_button = self._button("عرض الكل", "rate", self.expand_all)
        self.collapse_button = self._button("طي الكل", "toggle", self.collapse_all)
        self.expand_button.setToolTip("يفتح الشجرة كلها")
        self.collapse_button.setToolTip("يقفل الشجرة (الجذر بس)")
        layout.addWidget(self.expand_button)
        layout.addWidget(self.collapse_button)

    def _build_type_tabs(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(4)
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_tabs: dict[str, QPushButton] = {}
        for key, (title, _short, _code, color) in TYPES.items():
            button = QPushButton(title)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(38)
            button.setStyleSheet(
                f"QPushButton {{ background:#FFFFFF; color:{color}; border:1px solid {BORDER_CARD}; "
                f"border-top:3px solid {color}; border-radius:6px; padding:6px 16px; font-weight:900; }}"
                f"QPushButton:hover {{ background:#F8FAFC; }}"
                f"QPushButton:checked {{ background:{color}; color:#FFFFFF; border:1px solid {color}; }}"
                f"QPushButton:disabled {{ color:{OFF_COLOR}; border-top:3px solid #E2E8F0; }}"
                f"QPushButton:checked:disabled {{ background:{color}; color:#E2E8F0; border:1px solid {color}; }}")
            button.clicked.connect(lambda _checked=False, k=key: self._tab_clicked(k))
            self.type_group.addButton(button)
            self.type_tabs[key] = button
            row.addWidget(button)
        row.addStretch(1)
        return row

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        outer = QVBoxLayout()
        outer.setSpacing(0)
        outer.addLayout(self._build_type_tabs())
        self.type_panel = QFrame()
        self.type_panel.setObjectName("typePanel")
        panel = QHBoxLayout(self.type_panel)
        panel.setContentsMargins(8, 4, 8, 8)
        panel.setSpacing(10)

        # يمين: الشجرة (البحث وعداد الحسابات بتوع الـ Base بيتنقلوا هنا)
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
        self.tree.setHeaderLabels(["الحساب", "الدور", "الحالة"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(22)
        self.tree.setAnimated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        header.setSectionResizeMode(COL_ROLE, QHeaderView.ResizeToContents)
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

        # شمال: الفورم
        forms = QVBoxLayout()
        forms.setSpacing(10)
        forms.addWidget(form_box)
        forms.addWidget(self._build_reporting())
        forms.addStretch(1)
        panel.addLayout(forms, 21)
        outer.addWidget(self.type_panel, 1)
        content.addLayout(outer, 1)
        # الجدول بتاع الـ Base بيفضل مستخبي: مصدر «تصدير إكسل» ونافذة البحث F1 (الدليل كله).
        list_box.setParent(self)
        list_box.hide()

    # ---- الحقول حسب الوضع -------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.code_edit, self.name_ar, self.name_en, self.parent_combo, self.main_radio, self.leaf_radio,
                self.dr_radio, self.cr_radio, self.class_combo, self.cash_flow_combo, self.role_combo,
                self.party_combo, self.cost_center, self.currency_combo, self.is_active]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        rec = self.current_record if mode == "edit" else None
        system = bool(rec and rec.get("is_system"))
        kids = bool(rec and rec.get("has_children"))
        root = bool(rec and rec.get("parent_id") is None)
        self.code_edit.setEnabled(editing and not system and not kids)
        self.parent_combo.setEnabled(editing and not system and not kids and not root)
        self.role_combo.setEnabled(editing and not system)
        self.leaf_radio.setEnabled(editing and not system and not kids)
        self.main_radio.setEnabled(editing and not system and self._form_level() < MAX_LEVEL)
        self.is_active.setEnabled(editing and not system)
        if mode != "new":
            self._split = None
        if self._split is not None:
            self.parent_combo.setEnabled(False)              # التفريع تحت الحساب ده بس
        self._split_form.setRowVisible(self.split_note, self._split is not None)
        self.tree.setEnabled(not editing)
        for button in self.type_tabs.values():
            button.setEnabled(not editing)

    def update_extra_buttons(self) -> None:
        editing = self.mode in EDITING
        self.expand_button.setEnabled(not editing)
        self.collapse_button.setEnabled(not editing)
        rec = self.current_record
        reason = self._branch_blocked(rec)
        self.branch_button.setEnabled(not editing and rec is not None and self.can("create") and not reason)
        self.branch_button.setToolTip(reason or "حساب جديد تحت الحساب المحدد — ولو فرعي بيتحوّل رئيسي ويتفرّع منه")
        if rec and (rec.get("is_system") or rec.get("has_children")):
            self.delete_button.setEnabled(False)
            self.delete_button.setToolTip("الحساب نظامي — مايتمسحش" if rec.get("is_system")
                                          else "الحساب تحته حسابات — امسحهم الأول")
        else:
            self.delete_button.setToolTip("")

    def _branch_blocked(self, rec: dict | None) -> str:
        """ليه «+ فرعي تحت المحدد» مقفول للحساب ده ("" = شغال)."""
        if not rec or not rec.get("is_leaf"):
            return ""
        if int(rec.get("level") or 1) >= MAX_LEVEL:
            return "الحساب في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه"
        if not rec.get("is_active"):
            return "الحساب موقوف — نشّطه الأول وبعدين فرّع منه"
        if not self.can("edit"):
            return "التفريع بيعدّل الحساب (فرعي ← رئيسي) — محتاج صلاحية «تعديل»"
        return ""

    def _form_level(self) -> int:
        try:
            return int(self.level_edit.text())
        except ValueError:
            return 1

    # ---- التبويبات والشجرة -------------------------------------------------
    def _update_tabs(self, keyword: str = "") -> None:
        totals = self.accounts.summary(self.all_rows)
        matches = matching_ids(self.all_rows, keyword) if keyword else None
        for key, button in self.type_tabs.items():
            title, _short, root_code, _color = TYPES[key]
            count = totals[key]["total"]                     # وقت البحث = عدد اللي بيطابق في التبويب ده
            if matches is not None:
                count = sum(1 for r in self.all_rows if type_of(r) == key and r["id"] in matches)
            button.setText(f"{ltr(root_code)} {title} ({ltr(count)})")
            button.setChecked(key == self.current_type)
        color = TYPES[self.current_type][3]
        self.type_panel.setStyleSheet(
            f"QFrame#typePanel {{ background:#FFFFFF; border:1px solid {BORDER_CARD}; "
            f"border-top:3px solid {color}; border-radius:0 0 8px 8px; }}")

    def _type_rows(self) -> list[dict]:
        return [r for r in self.all_rows if type_of(r) == self.current_type]

    def _searching(self) -> bool:
        return bool(self.search.text().strip())

    def _build_tree(self) -> None:
        keyword = self.search.text().strip()
        rows = self._type_rows()
        shown = visible_ids(rows, keyword)
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        bold_font = self.tree.font()
        bold_font.setBold(True)
        for row in rows:                                     # بترتيب الكود ← الأب دايماً قبل اللي تحته
            if shown is not None and row["id"] not in shown:
                continue
            item = QTreeWidgetItem([account_label(row), row.get("role_name") or "", status_text(row)])
            item.setData(COL_NAME, Qt.UserRole, row["id"])
            item.setToolTip(COL_NAME, f"{row['code']} — {row['name_ar']}"
                            + (f" ({row['name_en']})" if row.get("name_en") else ""))
            item.setTextAlignment(COL_ROLE, Qt.AlignCenter)
            item.setTextAlignment(COL_STATUS, Qt.AlignCenter)
            if not row["is_leaf"]:
                item.setFont(COL_NAME, bold_font)
            if row["level"] == 1:
                for col in range(3):
                    item.setBackground(col, QBrush(QColor(LEVEL1_BG)))
                item.setForeground(COL_NAME, QBrush(QColor(GREEN)))
            item.setForeground(COL_ROLE, QBrush(QColor(ROLE_COLOR)))
            item.setFont(COL_ROLE, bold_font)
            item.setForeground(COL_STATUS, QBrush(QColor(TEXT_MUTED)))
            if not row["is_active"]:
                for col in (COL_NAME, COL_ROLE):
                    item.setForeground(col, QBrush(QColor(OFF_COLOR)))
            parent_item = self.items.get(row.get("parent_id"))
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            self.items[row["id"]] = item
        if shown is not None:
            self.tree.expandAll()
        elif self.current_type in self._expanded:
            for record_id in self._expanded[self.current_type]:
                if record_id in self.items:
                    self.items[record_id].setExpanded(True)
        else:
            for record_id, item in self.items.items():
                item.setExpanded(self.by_id[record_id]["level"] < DEFAULT_OPEN_DEPTH)
            self._expanded[self.current_type] = {i for i, it in self.items.items() if it.isExpanded()}
        self._select_item(self.current_id)
        self.tree.blockSignals(False)
        self._remember_expansion()
        self._update_count(rows, shown)

    def _update_count(self, rows: list[dict], shown: set | None) -> None:
        s = self.accounts.summary(rows)[self.current_type]
        text = (f"{ltr(s['total'])} حساب · {ltr(s['leaf'])} فرعي (بيقبل قيود) · {ltr(s['main'])} رئيسي · "
                f"موقوف {ltr(s['inactive'])}")
        if shown is not None:
            found = len(matching_ids(rows, self.search.text()))
            text = f"نتايج البحث: {ltr(found)} من {ltr(s['total'])} حساب (وظاهر معاها الحسابات اللي فوقها)"
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
        self._expanded[self.current_type] = {i for i, it in self.items.items() if it.isExpanded()}

    def expand_all(self) -> None:
        self.tree.expandAll()
        self._remember_expansion()

    def collapse_all(self) -> None:
        self.tree.collapseAll()
        self._remember_expansion()

    def _root_of(self, account_type: str) -> dict | None:
        return next((r for r in self.all_rows if r["parent_id"] is None and type_of(r) == account_type), None)

    def switch_type(self, account_type: str, open_account: bool = True) -> None:
        """يفتح تبويب النوع (والحساب اللي كان مفتوح فيه آخر مرة، أو الجذر)."""
        if self.mode in EDITING:
            return
        self.current_type = account_type
        self._update_tabs(self.search.text().strip())
        self._build_tree()
        if not open_account:
            return
        target = self._last_selected.get(account_type)
        if target not in self.by_id:
            root = self._root_of(account_type)
            target = root["id"] if root else None
        if target is not None:
            self.load(target)
        else:
            self.show_empty()

    def _tab_clicked(self, account_type: str) -> None:
        if account_type == self.current_type or self.mode in EDITING:
            self.type_tabs[self.current_type].setChecked(True)
            return
        self.switch_type(account_type)

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
        """ينقل للتبويب بتاع الحساب ويعلّم عليه في الشجرة (حتى لو البحث كان مخبّيه)."""
        record = self.current_record or {}
        account_type = type_of(record)
        self._last_selected[account_type] = record_id
        rebuild = account_type != self.current_type
        if record_id not in self.items and self._searching():
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            rebuild = True
        if record_id not in self.items and not rebuild and record_id not in self.by_id:
            self.refresh_list()                              # حساب جديد من جهاز تاني
            return
        self.current_type = account_type
        if rebuild:
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

    # ---- «حساب جديد» --------------------------------------------------------
    def new_record(self) -> None:
        if not self.can("create"):
            return
        selected = self.current_record or self._root_of(self.current_type)
        if selected is None:
            return
        if not selected["is_leaf"] and int(selected["level"]) < MAX_LEVEL:
            parent_id = selected["id"]                       # رئيسي ← تحته
        else:
            parent_id = selected["parent_id"]                # فرعي ← جنبه (تحت أبوه)
        if parent_id is None:
            return
        try:
            defaults = self.accounts.new_defaults(parent_id)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر فتح حساب جديد", exc)
            return
        self._before_new = self.current_id
        super().new_record()
        self.fill_form(defaults)
        self.set_mode("new")
        self.name_ar.setFocus()

    # ---- «+ فرعي تحت المحدد» ------------------------------------------------
    @staticmethod
    def branch_question(plan: dict) -> str:
        acc, general = plan["account"], plan["general"]
        text = (f"الحساب «{ltr(acc['code'])} {acc['name_ar']}» فرعي — عايز تحوّله رئيسي وتفرّع منه؟")
        if general:
            moves = []
            if general.get("role_name"):
                moves.append(f"الدور ({general['role_name']})")
            if general.get("is_system"):
                moves.append("🔒 النظامي")
            if general.get("tax_links"):
                moves.append(f"ربط أكواد الضريبة ({'، '.join(ltr(c) for c in general['tax_links'])})")
            if general.get("default_keys"):
                moves.append(f"الحسابات الافتراضية ({'، '.join(general['default_keys'])})")
            text += (f"\n\nالبرنامج هيعمل تحته لوحده «{ltr(general['code'])} {general['name_ar']}»"
                     + (f" وينقل له: {' · '.join(moves)}" if moves else " بنفس إعداداته")
                     + " — عشان المستندات والقيود القديمة تفضل عارفة تقيّد فين.")
        text += f"\n\nوالحساب الجديد اللي هتكتبه كوده «{ltr(plan['defaults']['code'])}»."
        text += "\nمفيش حاجة بتتحفظ غير لما تدوس «حفظ»."
        return text

    def _split_note_text(self, plan: dict) -> str:
        """سطر واحد قصير (الفورم ضيق) — والتفاصيل في الـ tooltip."""
        acc, general = plan["account"], plan["general"]
        text = f"🌿 مع «حفظ»: «{ltr(acc['code'])}» هيتحوّل رئيسي"
        if general:
            text += f" + «{ltr(general['code'])} … — عام»"
        return text + " + الحساب ده تحته"

    def _split_note_tip(self, plan: dict) -> str:
        acc, general = plan["account"], plan["general"]
        tip = f"«{ltr(acc['code'])} {acc['name_ar']}» هيتحوّل رئيسي مع «حفظ» (و«إلغاء» = ولا حاجة اتغيّرت)."
        if general:
            tip += (f"\nوالبرنامج هيعمل «{ltr(general['code'])} {general['name_ar']}» وينقل له الدور والنظامي "
                    "وربط أكواد الضريبة والحسابات الافتراضية.")
        return tip

    def branch_record(self) -> None:
        """رئيسي ← «جديد» تحته · فرعي ← سؤال ← «جديد» تحته (والتحويل لرئيسي بيتحفظ مع «حفظ»)."""
        rec = self.current_record
        if rec is None or self.mode in EDITING or not self.can("create"):
            return
        if not rec["is_leaf"]:
            self.new_record()                                # رئيسي ← تحته على طول
            return
        reason = self._branch_blocked(rec)
        if reason:
            messages.warn(self, "تفريع حساب", reason + ".")
            return
        try:
            plan = self.accounts.branch_plan(int(rec["id"]))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التفريع", exc)
            return
        if not messages.ask_yes_no(self, "تفريع حساب فرعي", self.branch_question(plan)):
            return
        # الإصدار اللي الحساب كان مفتوح بيه (لو جهاز تاني عدّله قبل «حفظ» ← رسالة تعارض).
        version = self.current_version if self.current_version is not None else plan["expected_version"]
        self._before_new = self.current_id
        BaseScreen.new_record(self)
        self._split = {"id": int(rec["id"]), "version": version, "plan": plan}
        self.split_note.setText(self._split_note_text(plan))
        self.split_note.setToolTip(self._split_note_tip(plan))
        self.fill_form(plan["defaults"])
        self.set_mode("new")
        self.name_ar.setFocus()

    def reload_after_conflict(self) -> None:
        """«تحديث» بعد تعارض في التفريع: يفتح الحساب اللي كنا بنفرّع منه بآخر نسخة."""
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
        """«إلغاء» في «جديد» بيرجع للحساب اللي كان مفتوح (مش فورم فاضي)."""
        if self.mode == "new" and self._before_new in self.by_id:
            self.load(self._before_new)
            return
        super().cancel()

    def _parent_changed(self, _index: int) -> None:
        """«تحت حساب» اتغيّر: الكود المقترح والمستوى والنوع — وفي «جديد» كمان اللي بيتورث."""
        if self._filling or self.mode not in EDITING:
            return
        parent_id = self.parent_combo.currentData()
        if parent_id is None:
            return
        try:
            defaults = self.accounts.new_defaults(int(parent_id))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الحساب الأب", exc)
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
                self._fill_inherited(defaults)
        finally:
            self._filling = False
        self.main_radio.setEnabled(int(defaults["level"]) < MAX_LEVEL)
        if int(defaults["level"]) >= MAX_LEVEL:
            self.leaf_radio.setChecked(True)

    def _role_changed(self, _index: int) -> None:
        """الدور اتغيّر: الطرف بتاعه بيتحط لوحده (العملاء ← عميل، الموردين ← مورد…)."""
        if self._filling or self.mode not in EDITING:
            return
        role = next((r for r in self.roles if r["code"] == self.role_combo.currentData()), None)
        if role and role.get("default_party_kind"):
            self.party_combo.setCurrentIndex(max(0, self.party_combo.findData(role["default_party_kind"])))

    # ---- تعبئة الفورم -------------------------------------------------------
    def _fill_parents(self, account_type: str, selected: Any, exclude: Any) -> None:
        self.parent_combo.clear()
        if selected is None:
            self.parent_combo.addItem(ROOT_PARENT, None)
        for row in self.all_rows:
            if type_of(row) != account_type or row["id"] == exclude:
                continue
            usable = not row["is_leaf"] and row["is_active"] and int(row["level"]) < MAX_LEVEL
            if usable or row["id"] == selected:
                self.parent_combo.addItem(f"{row['code']} — {row['name_ar']}", row["id"])
        index = self.parent_combo.findData(selected)
        self.parent_combo.setCurrentIndex(max(0, index))

    def _fill_type_combos(self, account_type: str) -> None:
        self.class_combo.clear()
        for key, text in CLASSIFICATIONS[account_type].items():
            self.class_combo.addItem(text, key)
        self.role_combo.clear()
        self.role_combo.addItem(NO_ROLE, None)
        for role in self.roles:
            if role["account_type"] in (None, account_type):
                self.role_combo.addItem(role["name_ar"], role["code"])

    def _fill_inherited(self, record: dict) -> None:
        (self.dr_radio if record.get("nature") == "dr" else self.cr_radio).setChecked(True)
        self.class_combo.setCurrentIndex(max(0, self.class_combo.findData(record.get("classification"))))
        self.cash_flow_combo.setCurrentIndex(max(0, self.cash_flow_combo.findData(record.get("cash_flow") or "none")))
        self.role_combo.setCurrentIndex(max(0, self.role_combo.findData(record.get("role_code"))))
        self.party_combo.setCurrentIndex(max(0, self.party_combo.findData(record.get("party_kind"))))
        self.cost_center.setChecked(bool(record.get("requires_cost_center")))
        self.currency_combo.setCurrentIndex(max(0, self.currency_combo.findData(record.get("currency_code") or "EGP")))

    def fill_form(self, record: dict) -> None:
        account_type = type_of(record)
        self._filling = True
        try:
            self.code_edit.setText(record.get("code") or "")
            self.name_ar.setText(record.get("name_ar") or "")
            self.name_en.setText(record.get("name_en") or "")
            self._fill_parents(account_type, record.get("parent_id"), record.get("id"))
            self.level_edit.setText(str(record.get("level") or 1))
            self.type_edit.setText(f"{TYPES[account_type][1]} 🔒")
            (self.leaf_radio if record.get("is_leaf", True) else self.main_radio).setChecked(True)
            statement = record.get("statement") or ("bs" if account_type in ("asset", "liability", "equity") else "is")
            self.statement_edit.setText(f"{STATEMENTS[statement]} 🔒")
            self._fill_type_combos(account_type)
            self._fill_inherited(record)
            self.is_active.setChecked(bool(record.get("is_active", True)))
            self.system_label.setVisible(bool(record.get("is_system")))
        finally:
            self._filling = False

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.code_edit, self.name_ar, self.name_en, self.level_edit, self.type_edit,
                         self.statement_edit):
                edit.clear()
            self.parent_combo.clear()
            self.leaf_radio.setChecked(True)
            self.dr_radio.setChecked(True)
            self._fill_type_combos(self.current_type)
            self.cash_flow_combo.setCurrentIndex(max(0, self.cash_flow_combo.findData("none")))
            self.party_combo.setCurrentIndex(0)
            self.cost_center.setChecked(False)
            self.currency_combo.setCurrentIndex(max(0, self.currency_combo.findData("EGP")))
            self.is_active.setChecked(True)
            self.system_label.hide()
        finally:
            self._filling = False

    def collect(self) -> dict:
        return {"code": self.code_edit.text().strip(), "name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                "parent_id": self.parent_combo.currentData(), "is_leaf": self.leaf_radio.isChecked(),
                "nature": "dr" if self.dr_radio.isChecked() else "cr",
                "classification": self.class_combo.currentData(), "cash_flow": self.cash_flow_combo.currentData(),
                "role_code": self.role_combo.currentData(), "party_kind": self.party_combo.currentData(),
                "requires_cost_center": self.cost_center.isChecked(),
                "currency_code": self.currency_combo.currentData(), "is_active": self.is_active.isChecked()}

    # ---- البيانات ----------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.accounts.list_accounts()

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.accounts.get_account(int(record_id))

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "account_type":
            return TYPES.get(value, ("", ""))[1]
        if key == "is_leaf":
            return "فرعي" if value else "رئيسي"
        if key == "nature":
            return NATURES.get(value, "")
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def save_record(self, payload: dict, record_id: Any) -> Any:
        if record_id is None and self._split is not None:   # التفريع: الأب + الجديد في transaction واحدة
            return self.accounts.branch_account(self._split["id"], payload, expected_version=self._split["version"])
        return self.accounts.save_account(None if record_id is None else int(record_id), payload,
                                          expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.accounts.delete_account(int(record_id), expected_version=self.current_version)

    def delete(self) -> None:
        """بعد المسح بيفتح الحساب اللي كان فوقه (مش شاشة فاضية)."""
        parent_id = (self.current_record or {}).get("parent_id")
        super().delete()
        if self.current_id is None and parent_id in self.by_id:
            self.load(parent_id)
