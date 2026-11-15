"""شاشة «البنوك» — النموذج 7 «البنوك في دليل الحسابات» (اختيار المستخدم 2026-11-15 + كل الاقتراحات).

```
┌ [حساب بنكي جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][🏦 البنوك…][تصدير إكسل][خروج]      (عرض) ┐
├────────────────────────────────────────┬─────────────────────────────────────────┤
│ (⚠ الحساب 110207 مش مربوط [🏦 اعمل حساب │ ┌ البنوك في دليل الحسابات ┐                   │
│   بنكي عليه])                            │   🔍 البنك / رقم الحساب / IBAN / الكود…      │
│ ┌ الحساب البنكي ┐                         │   ▾ 1102 البنوك                دور بنك       │
│   الكود BNK-001-01 · البنك ▾ · النوع ▾    │      110201 البنك الأهلي — جاري  🏦 BNK-001-01 │
│   العملة ▾ · رقم الحساب* · IBAN           │      110202 بنك مصر — جاري      🏦 BNK-002-01 │
│   حد التسهيلات / تاريخ الاستحقاق · ⭐      │      110207 حساب قديم           ⚠ مش مربوط   │
│ ┌ البنك ┐ الفرع · العنوان · SWIFT · مسؤول │                                            │
│ ┌ الحساب في الدليل ┐ (•) لوحده ( ) موجود ▾ │   حسابات: … · مربوط … · مش مربوط …          │
│ ┌ الرصيد (توضيحي) ┐                        │                                            │
└────────────────────────────────────────┴─────────────────────────────────────────┘
```
* شمال: شجرة كل الحسابات بدور «بنك» في الدليل (1102 والحسابات تحته)، وجنب كل حساب الحساب البنكي المربوط بيه
  أو «⚠ مش مربوط» — دوس على حساب مش مربوط ← «🏦 اعمل حساب بنكي عليه».
* يمين: الحساب البنكي (البنك، النوع، العملة، الرقم، IBAN) + بيانات البنك + حساب الدليل + الرصيد (توضيحي).
* البنوك نفسها (الاسم، الفرع، SWIFT، مسؤول الحساب) من زرار «🏦 البنوك…» (شباك لوحده بنفس شكل Base Screen).
* الجدول بتاع الـ Base مستخبي: مصدر «تصدير إكسل» ونافذة البحث F1 (كل الحسابات البنكية).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
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

from app.services.bank_service import ACCOUNT_TYPES, BALANCE_NOTE, BankService, fmt_amount
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import BORDER_CARD, GREEN, ROW_SELECTED, TEXT, TEXT_MUTED, toolbar_button_style

ACTIVE_COLOR, MUTED, WARN, OFF = "#087443", "#64748B", "#B45309", "#94A3B8"
COL_ACCOUNT, COL_LINK, COL_CURRENCY, COL_STATUS = range(4)
ROLE_BANK_ACCOUNT = Qt.UserRole + 1
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


def ltr(text: Any) -> str:
    return f"⁦{text}⁩"


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


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


# =============================================================================
# شاشة «البنوك» (الحسابات البنكية في الدليل)
# =============================================================================
class BanksScreen(BaseScreen):
    MODULE = "cash"
    TARGET = "banks"
    TITLE = "البنوك"
    ICON = "🏦"
    FORM_TITLE = "الحساب البنكي"
    LIST_TITLE = "البنوك في دليل الحسابات"
    NEW_TEXT = "حساب بنكي جديد"
    SEARCH_PLACEHOLDER = "🔍 البنك أو رقم الحساب أو IBAN أو الكود… (F1)"
    COLUMNS = [("code", "الكود"), ("bank_name", "البنك"), ("account_type", "النوع"), ("currency_code", "العملة"),
               ("account_number", "رقم الحساب"), ("iban", "IBAN"), ("account_code", "حساب الدليل"),
               ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, banks: BankService | None = None, parent: QWidget | None = None) -> None:
        self.banks = banks or BankService()
        self.chart: list[dict] = []
        self.items: dict[int, QTreeWidgetItem] = {}          # رقم حساب الدليل ← العنصر
        self.by_bank_account: dict[int, int] = {}           # رقم الحساب البنكي ← رقم حساب الدليل
        self.free_account: dict | None = None               # حساب «مش مربوط» المحدد في الشجرة
        self._suggested: dict | None = None
        self._free_accounts: dict[int, dict] = {}
        self._banks: dict[int, dict] = {}
        self.banks_window: BankListScreen | None = None
        super().__init__(parent)
        first = next((r for r in self.chart if r["bank_account_id"]), None)
        if first is not None:
            self.load(first["bank_account_id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        form = _form(box)
        self._main_form = form
        self.code_edit = _readonly("بيتولّد لوحده", ltr_text=True)
        self.code_edit.setToolTip("كود الحساب البنكي = كود البنك + مسلسل (BNK-001-01) — بيتولّد وقت الحفظ وثابت")
        self.bank_combo = QComboBox()
        self.bank_combo.setToolTip("البنوك النشطة — البنك ثابت بعد الحفظ")
        self.bank_combo.currentIndexChanged.connect(self._on_bank_changed)
        self.type_combo = QComboBox()
        for key, text in ACCOUNT_TYPES.items():
            self.type_combo.addItem(text, key)
        self.currency = QComboBox()
        self.currency.setToolTip("عملة الحساب البنكي = عملة حسابه في الدليل (تغييرها بيغيّره معاها)")
        self.number_edit = QLineEdit()
        self.number_edit.setMaxLength(34)
        self.number_edit.setLayoutDirection(Qt.LeftToRight)
        self.number_edit.setPlaceholderText("رقم الحساب في البنك")
        self.iban_edit = QLineEdit()
        self.iban_edit.setMaxLength(42)
        self.iban_edit.setLayoutDirection(Qt.LeftToRight)
        self.iban_edit.setPlaceholderText("اختياري — EG + 27 رقم")
        self.iban_edit.setToolTip("بيتراجع شكله ورقم التحقق بتاعه، ومايتكررش")
        self.overdraft_edit = QLineEdit()
        self.overdraft_edit.setLayoutDirection(Qt.LeftToRight)
        self.overdraft_edit.setPlaceholderText("مطلوب للجاري المدين")
        self.overdraft_edit.setToolTip("الرصيد يقدر ينزل تحت الصفر لحد المبلغ ده (مع السندات — المرحلة 5)")
        self.has_maturity = QCheckBox("محدد")
        self.maturity_date = QDateEdit(QDate.currentDate())
        self.maturity_date.setCalendarPopup(True)
        self.maturity_date.setDisplayFormat("dd/MM/yyyy")
        self.maturity_date.setMinimumDate(QDate(2000, 1, 1))
        self.maturity_date.setMaximumDate(QDate(2100, 12, 31))
        self.maturity_date.setLayoutDirection(Qt.LeftToRight)
        self.has_maturity.toggled.connect(lambda on: self.maturity_date.setEnabled(on and self.mode in EDITING))
        maturity_row = QWidget()
        row = QHBoxLayout(maturity_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.has_maturity)
        row.addWidget(self.maturity_date, 1)
        self.maturity_row = maturity_row
        self.is_active = QCheckBox("نشط")
        self.default_label = QLabel("")
        self.default_label.setStyleSheet("color:#3730A3; font-weight:800;")
        status_row = QWidget()
        row = QHBoxLayout(status_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.is_active)
        row.addWidget(self.default_label, 1)
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الكود", self.code_edit)
        form.addRow("البنك *", self.bank_combo)
        form.addRow("النوع *", self.type_combo)
        form.addRow("العملة *", self.currency)
        form.addRow("رقم الحساب *", self.number_edit)
        form.addRow("IBAN", self.iban_edit)
        form.addRow("حد التسهيلات *", self.overdraft_edit)
        form.addRow("تاريخ الاستحقاق", self.maturity_row)
        form.addRow("الحالة", status_row)
        form.addRow("ملاحظات", self.notes)
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)

    def _build_unlinked(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("unlinked")
        frame.setStyleSheet("QFrame#unlinked { background:#FFFBEB; border:1px solid #FDE68A; border-radius:7px; }")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        self.unlinked_label = QLabel("")
        self.unlinked_label.setWordWrap(True)
        self.unlinked_label.setStyleSheet("color:#92400E; font-weight:800;")
        self.link_button = QPushButton("🏦 اعمل حساب بنكي عليه")
        self.link_button.setCursor(Qt.PointingHandCursor)
        self.link_button.setStyleSheet(toolbar_button_style("new"))
        self.link_button.clicked.connect(self.link_free_account)
        layout.addWidget(self.unlinked_label)
        row = QHBoxLayout()
        row.addWidget(self.link_button)
        row.addStretch(1)
        layout.addLayout(row)
        frame.hide()
        return frame

    def _build_bank_box(self) -> QGroupBox:
        box = QGroupBox("البنك")
        form = _form(box)
        self.bank_branch = _readonly("—")
        self.bank_address = _readonly("—")
        self.bank_swift = _readonly("—", ltr_text=True)
        self.bank_contact = _readonly("—")
        form.addRow("الفرع", self.bank_branch)
        form.addRow("العنوان", self.bank_address)
        form.addRow("SWIFT", self.bank_swift)
        form.addRow("مسؤول الحساب", self.bank_contact)
        form.addRow(_hint("بيانات البنك بتتعدّل من زرار «🏦 البنوك…» فوق."))
        return box

    def _build_account(self) -> QGroupBox:
        box = QGroupBox("الحساب في الدليل")
        form = _form(box)
        self._account_form = form
        self.mode_auto = QRadioButton("يتعمل لوحده تحت «البنوك»")
        self.mode_pick = QRadioButton("أختار حساب موجود")
        self.mode_auto.setChecked(True)
        self.account_modes = QButtonGroup(self)
        self.account_modes.addButton(self.mode_auto)
        self.account_modes.addButton(self.mode_pick)
        self.mode_auto.toggled.connect(self._on_account_mode)
        self.account_pick = QComboBox()
        self.account_pick.setToolTip("الحسابات الفرعية النشطة بدور «بنك» ومش مربوطة بحساب بنكي تاني")
        self.account_pick.currentIndexChanged.connect(self._on_pick_account)
        self.account_view = _readonly("")
        self.account_parent = _readonly("")
        modes = QWidget()
        col = QVBoxLayout(modes)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(self.mode_auto)
        col.addWidget(self.mode_pick)
        self.modes_widget = modes
        form.addRow("الحساب", modes)
        form.addRow("", self.account_pick)
        form.addRow("حساب الدليل", self.account_view)
        form.addRow("تحت", self.account_parent)
        form.addRow(_hint("الحساب بيتعمل لوحده تحت 1102 باسم «البنك — النوع» (أو بيتختار من الدليل) وثابت بعد الحفظ "
                          "— واسمه وعملته بيتغيّروا مع النوع والعملة واسم البنك."))
        return box

    def _build_balance(self) -> QGroupBox:
        box = QGroupBox("الرصيد (توضيحي)")
        form = _form(box)
        self.opening_view = _readonly("— (مستند الأرصدة الافتتاحية — المرحلة 4)")
        self.balance_view = _readonly("— (من القيود — المرحلة 5)")
        self.last_move_view = _readonly("—")
        form.addRow("رصيد أول المدة", self.opening_view)
        form.addRow("الرصيد الحالي", self.balance_view)
        form.addRow("آخر حركة", self.last_move_view)
        form.addRow(_hint(BALANCE_NOTE))
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        self.unlinked_frame = self._build_unlinked()
        right.addWidget(self.unlinked_frame)
        right.addWidget(form_box)
        for box in (self._build_bank_box(), self._build_account(), self._build_balance()):
            align_form_right(box)
            right.addWidget(box)
        right.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)               # RTL: أول عنصر = يمين

        # شمال: شجرة «البنوك» في الدليل (البحث والعداد بتوع الـ Base بيتنقلوا هنا)
        tree_box = QGroupBox(self.LIST_TITLE)
        layout = QVBoxLayout(tree_box)
        layout.setContentsMargins(10, 16, 10, 8)
        layout.setSpacing(6)
        layout.addWidget(_hint("كل حساب بنكي = حساب فرعي تحت «1102 البنوك» (زي الخزائن تحت 1101) — "
                               "وجنب كل حساب في الدليل الحساب البنكي المربوط بيه."))
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
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["الحساب في الدليل", "الحساب البنكي", "العملة", "الحالة"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(22)
        self.tree.setAnimated(False)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setDefaultAlignment(Qt.AlignCenter)
        header.setSectionResizeMode(COL_ACCOUNT, QHeaderView.Stretch)
        for col in (COL_LINK, COL_CURRENCY, COL_STATUS):
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
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.toggle_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.toggle_button)
        self.banks_button = self._button("🏦 البنوك…", "rate", self.open_banks)
        self.banks_button.setToolTip("البنوك نفسها: الاسم والفرع والعنوان وSWIFT ومسؤول الحساب")
        layout.addWidget(self.banks_button)

    # ---- الاختيارات -----------------------------------------------------------
    def _load_choices(self, keep_currency: str | None = None, keep_bank: dict | None = None) -> None:
        """بتتحمّل من جديد مع «جديد» و«تعديل» ورجوع شباك البنوك (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            currencies = self.banks.currencies()
            banks = self.banks.active_banks()
            accounts = self.banks.free_accounts()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل الشجرة
            currencies, banks, accounts = [], [], []
        self.currency.blockSignals(True)
        self.currency.clear()
        for c in currencies:
            self.currency.addItem(f"{c['code']} — {c['name_ar']}", c["code"])
        if keep_currency and self.currency.findData(keep_currency) < 0:
            self.currency.addItem(keep_currency, keep_currency)        # عملة موقوفة على حساب قديم
        self.currency.blockSignals(False)
        self._banks = {b["id"]: b for b in banks}
        self.bank_combo.blockSignals(True)
        self.bank_combo.clear()
        for b in banks:
            self.bank_combo.addItem(f"{b['name_ar']} ({b['code']})", b["id"])
        if keep_bank and self.bank_combo.findData(keep_bank["id"]) < 0:   # بنك موقوف (للعرض بس)
            self.bank_combo.addItem(f"{keep_bank['name_ar']} ({keep_bank['code']})", keep_bank["id"])
            self._banks[keep_bank["id"]] = keep_bank
        self.bank_combo.blockSignals(False)
        self.account_pick.blockSignals(True)
        self.account_pick.clear()
        for a in accounts:
            self.account_pick.addItem(f"⁦{a['code']}⁩ {a['name_ar']} ({a['currency_code']})", a["id"])
        self.account_pick.blockSignals(False)
        self._free_accounts = {a["id"]: a for a in accounts}

    def _show_bank(self, bank: dict | None) -> None:
        bank = bank or {}
        self.bank_branch.setText(bank.get("branch") or "")
        self.bank_address.setText(bank.get("address") or "")
        self.bank_swift.setText(bank.get("swift_code") or "")
        contact = bank.get("contact_name") or ""
        if bank.get("contact_phone"):
            contact = f"{contact} — {ltr(bank['contact_phone'])}".strip(" —")
        self.bank_contact.setText(contact)

    def _on_bank_changed(self, _index: int) -> None:
        if self.mode != "new":
            return
        bank_id = self.bank_combo.currentData()
        self._show_bank(self._banks.get(bank_id))
        try:
            self.code_edit.setText(f"{self.banks.suggested_code(bank_id)} (مقترح)" if bank_id else "")
        except Exception:  # noqa: BLE001
            self.code_edit.clear()

    def _on_type_changed(self, _index: int) -> None:
        kind = self.type_combo.currentData()
        self._main_form.setRowVisible(self.overdraft_edit, kind == "overdraft")
        self._main_form.setRowVisible(self.maturity_row, kind == "deposit")

    def _on_account_mode(self, auto: bool) -> None:
        self.account_pick.setEnabled(self.mode == "new" and not auto)
        if self.mode == "new":
            self._show_new_account()

    def _on_pick_account(self, _index: int) -> None:
        """الحساب الموجود بيحدد العملة (لازم يبقوا زي بعض)."""
        account = self._free_accounts.get(self.account_pick.currentData())
        if self.mode == "new" and self.mode_pick.isChecked() and account is not None:
            self.currency.setCurrentIndex(max(0, self.currency.findData(account["currency_code"])))
            self.account_view.setText(f"⁦{account['code']}⁩ {account['name_ar']}")

    def _show_new_account(self) -> None:
        if self.mode_auto.isChecked():
            s = self._suggested
            if s and s.get("code"):
                self.account_view.setText(f"⁦{s['code']}⁩ (هيتعمل لوحده باسم «البنك — النوع»)")
                self.account_parent.setText(f"⁦{s['parent_code']}⁩ {s['parent_name']}")
            else:
                self.account_view.setText("")
                self.account_parent.setText("مفيش حساب رئيسي بدور «بنك» — اختار حساب موجود")
        else:
            self.account_view.setText("")
            self.account_parent.setText("")
            self._on_pick_account(self.account_pick.currentIndex())

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.type_combo, self.currency, self.number_edit, self.iban_edit, self.overdraft_edit,
                self.has_maturity, self.is_active, self.notes]

    def new_only_widgets(self) -> list[QWidget]:
        return [self.bank_combo, self.mode_auto, self.mode_pick]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        self.maturity_date.setEnabled(editing and self.has_maturity.isChecked())
        self._account_form.setRowVisible(self.modes_widget, mode == "new")    # الاختيار في «جديد» بس
        self._account_form.setRowVisible(self.account_pick, mode == "new")
        self.account_pick.setEnabled(mode == "new" and self.mode_pick.isChecked())
        self.tree.setEnabled(not editing)
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        if editing:
            self.unlinked_frame.hide()
        self._on_type_changed(0)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))
        self.banks_button.setEnabled(self.mode not in EDITING)
        self.link_button.setEnabled(self.mode == "view" and self.can("create"))

    # ---- الشجرة -------------------------------------------------------------
    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self._build_tree()

    def _matches(self, row: dict, keyword: str) -> bool:
        if self.status == "active" and not (row["bank_account_id"] and row["bank_account_active"]):
            return False
        if self.status == "inactive" and not (row["bank_account_id"] and not row["bank_account_active"]):
            return False
        if not keyword:
            return True
        key = keyword.lower()
        compact = "".join(ch for ch in key if ch.isalnum())
        texts = [row["code"], row["name_ar"], row.get("bank_account_code"), row.get("bank_name"),
                 row.get("bank_short")]
        if any(key in str(t).lower() for t in texts if t):
            return True
        number = "".join(ch for ch in str(row.get("account_number") or "").lower() if ch.isalnum())
        return bool(compact) and compact in number

    def _visible_ids(self) -> set[int] | None:
        keyword = self.search.text().strip()
        if not keyword and self.status == "all":
            return None
        by_id = {r["id"]: r for r in self.chart}
        shown: set[int] = set()
        for row in self.chart:
            if row["is_leaf"] and self._matches(row, keyword):
                node = row
                while node is not None and node["id"] not in shown:
                    shown.add(node["id"])
                    node = by_id.get(node["parent_id"])
        return shown

    def _build_tree(self) -> None:
        shown = self._visible_ids()
        self.tree.blockSignals(True)
        self.tree.clear()
        self.items = {}
        bold = self.tree.font()
        bold.setBold(True)
        for row in self.chart:                               # بترتيب الكود ← الأب دايماً قبل اللي تحته
            if shown is not None and row["id"] not in shown:
                continue
            if row["bank_account_id"]:
                link = f"🏦 {row['bank_account_code']}" + (" ⭐" if row["is_default"] else "")
                status = "نشط" if row["bank_account_active"] else "موقوف"
            elif row["is_leaf"]:
                link, status = "⚠ مش مربوط", ("" if row["is_active"] else "موقوف")
            else:
                link, status = "دور «بنك»", ""
            item = QTreeWidgetItem([f"{ltr(row['code'])}  {row['name_ar']}", link,
                                    row["currency_code"] if row["is_leaf"] else "", status])
            item.setData(COL_ACCOUNT, Qt.UserRole, row["id"])
            item.setData(COL_ACCOUNT, ROLE_BANK_ACCOUNT, row["bank_account_id"])
            for col in (COL_LINK, COL_CURRENCY, COL_STATUS):
                item.setTextAlignment(col, Qt.AlignCenter)
            if not row["is_leaf"]:
                item.setFont(COL_ACCOUNT, bold)
                item.setForeground(COL_ACCOUNT, QBrush(QColor(GREEN)))
                item.setForeground(COL_LINK, QBrush(QColor(TEXT_MUTED)))
            elif row["bank_account_id"]:
                item.setForeground(COL_LINK, QBrush(QColor(ACTIVE_COLOR)))
                item.setForeground(COL_STATUS, QBrush(QColor(ACTIVE_COLOR if row["bank_account_active"] else MUTED)))
                item.setToolTip(COL_ACCOUNT, f"{row['bank_name']} — {ACCOUNT_TYPES.get(row['account_type'], '')}"
                                + (f"\nرقم الحساب: {row['account_number']}" if row.get("account_number") else ""))
            else:
                item.setForeground(COL_LINK, QBrush(QColor(WARN)))
            if (row["bank_account_id"] and not row["bank_account_active"]) or not row["is_active"]:
                item.setForeground(COL_ACCOUNT, QBrush(QColor(OFF)))
            parent_item = self.items.get(row["parent_id"])
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            self.items[row["id"]] = item
        self.tree.expandAll()
        target = self.by_bank_account.get(self.current_id) if self.current_id is not None else None
        if target is None and self.free_account is not None:
            target = self.free_account["id"]
        self._select_item(target)
        self.tree.blockSignals(False)
        self._update_count(shown)

    def _update_count(self, shown: set[int] | None) -> None:
        leaves = [r for r in self.chart if r["is_leaf"]]
        linked = sum(1 for r in leaves if r["bank_account_id"])
        text = (f"حسابات البنوك في الدليل: {ltr(len(leaves))} · مربوط {ltr(linked)} · مش مربوط "
                f"{ltr(len(leaves) - linked)}  ·  ⭐ = البنك الافتراضي")
        if shown is not None:
            found = sum(1 for r in leaves if r["id"] in shown)
            text = f"نتايج البحث / الفلتر: {ltr(found)} من {ltr(len(leaves))} حساب"
        if not self.chart:
            text = "مفيش حسابات بدور «بنك» في الدليل — اعمل «1102 البنوك» من شجرة الحسابات"
        self.count_label.setText(text)

    def chart_by_id(self, account_id: Any) -> dict | None:
        """صف الدليل (حساب بدور «بنك») برقمه."""
        return next((r for r in self.chart if r["id"] == account_id), None)

    def _select_item(self, account_id: Any) -> None:
        item = self.items.get(account_id) if account_id is not None else None
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
        bank_account_id = item.data(COL_ACCOUNT, ROLE_BANK_ACCOUNT)
        if bank_account_id:
            self.free_account = None
            self.unlinked_frame.hide()
            if bank_account_id != self.current_id:
                self.load(bank_account_id)
            return
        account_id = item.data(COL_ACCOUNT, Qt.UserRole)
        row = self.chart_by_id(account_id)
        BaseScreen.show_empty(self)                         # من غير ما الشجرة تتمسح
        self._show_unlinked(row)

    def _show_unlinked(self, row: dict | None) -> None:
        if row is None or not row["is_leaf"]:
            self.free_account = None
            self.unlinked_frame.hide()
            return
        self.free_account = row
        state = "" if row["is_active"] else " (موقوف — نشّطه من شجرة الحسابات الأول)"
        self.unlinked_label.setText(f"⚠ الحساب {ltr(row['code'])} «{row['name_ar']}» تحت البنوك ومش مربوط بأي "
                                    f"حساب بنكي{state}.")
        self.link_button.setVisible(self.has_action("create") and row["is_active"])
        self.link_button.setEnabled(self.can("create"))
        self.unlinked_frame.show()

    # ---- القائمة (الـ Base) --------------------------------------------------
    def refresh_list(self) -> None:
        try:
            rows = self.banks.lookup_accounts()
            chart = self.banks.chart_rows()
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
            rows, chart = [], []
        self.chart = chart
        self.by_bank_account = {r["bank_account_id"]: r["id"] for r in chart if r["bank_account_id"]}
        if self.free_account is not None:                   # اتربط أو اتمسح من جهاز تاني
            self.free_account = next((r for r in chart if r["id"] == self.free_account["id"]
                                      and not r["bank_account_id"]), None)
            if self.free_account is None:
                self.unlinked_frame.hide()
        self.table.blockSignals(True)
        self.table.set_rows(rows)
        self.table.blockSignals(False)
        self._build_tree()

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.banks.list_accounts(keyword)

    def lookup_rows(self) -> list[dict]:
        return self.banks.lookup_accounts()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "bank_name":
            return f"{value} ⭐" if row.get("is_default") else (value or "")
        if key == "account_type":
            return ACCOUNT_TYPES.get(value, value or "")
        if key in ("account_code", "account_number", "iban"):
            return ltr(value) if value else ""
        if key == "is_active":
            return "نشط" if value else "موقوف"
        return default_format(key, value, row)

    def load(self, record_id: Any) -> bool:
        if not super().load(record_id):
            return False
        self.free_account = None
        self.unlinked_frame.hide()
        account_id = self.by_bank_account.get(record_id)
        if account_id is None:                              # حساب بنكي جديد من جهاز تاني
            self.refresh_list()
            account_id = self.by_bank_account.get(record_id)
        if account_id is not None and account_id not in self.items:
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self._build_tree()
        self.tree.blockSignals(True)
        self._select_item(account_id)
        self.tree.blockSignals(False)
        return True

    def show_empty(self) -> None:
        super().show_empty()
        self.free_account = None
        self.unlinked_frame.hide()
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        self.tree.blockSignals(False)

    # ---- «جديد» / «تعديل» -----------------------------------------------------
    def new_record(self, *_signal: Any, account_id: Any = None) -> None:
        """``*_signal``: زرار «جديد» بيبعت ``checked`` — مش رقم حساب."""
        if not self.can("create"):
            return
        super().new_record()
        if self.mode != "new":
            return
        self.unlinked_frame.hide()
        self._load_choices()
        try:
            defaults = self.banks.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self._suggested = defaults.get("suggested_account")
        self.is_active.setChecked(True)
        self.currency.setCurrentIndex(max(0, self.currency.findData(defaults["currency_code"])))
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(defaults["account_type"])))
        self.set_mode("new")
        if defaults.get("bank_id") is not None:
            self.bank_combo.setCurrentIndex(max(0, self.bank_combo.findData(defaults["bank_id"])))
        self._on_bank_changed(self.bank_combo.currentIndex())
        if account_id is not None and self.account_pick.findData(account_id) >= 0:
            self.mode_pick.setChecked(True)
            self.account_pick.setCurrentIndex(self.account_pick.findData(account_id))
            self._on_pick_account(self.account_pick.currentIndex())
        else:
            self.mode_auto.setChecked(True)
            self._show_new_account()
        if self.bank_combo.count() == 0:
            messages.warn(self, "البنوك", "مفيش بنوك نشطة — اعمل البنك الأول من زرار «🏦 البنوك…».")
        self.number_edit.setFocus()

    def link_free_account(self) -> None:
        """«🏦 اعمل حساب بنكي عليه»: «جديد» على الحساب اللي مش مربوط («أختار حساب موجود»)."""
        if self.free_account is None or self.mode in EDITING:
            return
        self.new_record(account_id=self.free_account["id"])

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        record = self.current_record or {}
        bank = {"id": record.get("bank_id"), "name_ar": record.get("bank_name"), "code": record.get("bank_code")}
        self._load_choices(keep_currency=record.get("currency_code"), keep_bank=bank)
        super().edit_record()

    # ---- البنوك (شباك لوحده) ------------------------------------------------
    def open_banks(self) -> None:
        if self.mode in EDITING:
            return
        if self.banks_window is not None:
            self.banks_window.raise_()
            self.banks_window.activateWindow()
            return
        window = BankListScreen(self.banks, parent=self)
        window.setWindowFlag(Qt.Window, True)
        window.setWindowModality(Qt.WindowModal)
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.destroyed.connect(self._banks_closed)
        self.banks_window = window
        window.resize(1150, 680)
        window.show()

    def _banks_closed(self, _obj=None) -> None:
        self.banks_window = None
        self._load_choices()
        self.refresh_screen()

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} الحساب البنكي «{self.current_record['code']}»؟"):
            return
        try:
            self.banks.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        self.load(self.current_id)
        messages.info(self, "تم", "تم تفعيل الحساب البنكي." if new_state else
                      "تم إيقاف الحساب البنكي (مش هيتختار في أي سند جديد).")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.banks.get_account(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        bank_id = record.get("bank_id")
        if self.bank_combo.findData(bank_id) < 0:
            self.bank_combo.addItem(f"{record.get('bank_name') or ''} ({record.get('bank_code') or ''})", bank_id)
        self.bank_combo.blockSignals(True)
        self.bank_combo.setCurrentIndex(self.bank_combo.findData(bank_id))
        self.bank_combo.blockSignals(False)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(record.get("account_type"))))
        currency = record.get("currency_code")
        if self.currency.findData(currency) < 0:
            self.currency.addItem(currency, currency)
        self.currency.setCurrentIndex(self.currency.findData(currency))
        self.number_edit.setText(record.get("account_number") or "")
        iban = record.get("iban") or ""
        self.iban_edit.setText(" ".join(iban[i:i + 4] for i in range(0, len(iban), 4)))
        limit = record.get("overdraft_limit")
        self.overdraft_edit.setText(fmt_amount(limit) if limit is not None else "")
        maturity = record.get("maturity_date")
        self.has_maturity.setChecked(maturity is not None)
        self.maturity_date.setDate(_qdate(maturity) if maturity is not None else QDate.currentDate())
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.default_label.setText("⭐ البنك الافتراضي (للإيداع والتحويل البنكي)" if record.get("is_default") else "")
        self.notes.setText(record.get("notes") or "")
        self._show_bank({"branch": record.get("bank_branch"), "address": record.get("bank_address"),
                         "swift_code": record.get("bank_swift"), "contact_name": record.get("bank_contact"),
                         "contact_phone": record.get("bank_phone")})
        self.account_view.setText(f"⁦{record.get('account_code') or ''}⁩ {record.get('account_name') or ''}")
        self.account_parent.setText(f"⁦{record.get('parent_code') or ''}⁩ {record.get('parent_name') or ''}"
                                    if record.get("parent_code") else "")
        for edit in (self.opening_view, self.balance_view, self.last_move_view):
            edit.clear()
        self._on_type_changed(0)

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.number_edit, self.iban_edit, self.overdraft_edit, self.notes,
                     self.account_view, self.account_parent):
            edit.clear()
        self.type_combo.setCurrentIndex(0)
        self.bank_combo.blockSignals(True)
        self.bank_combo.setCurrentIndex(-1)                    # مفيش حساب ← مفيش بنك
        self.bank_combo.blockSignals(False)
        if self.currency.count():
            self.currency.setCurrentIndex(0)
        self.has_maturity.setChecked(False)
        self.is_active.setChecked(True)
        self.default_label.setText("")
        self.mode_auto.setChecked(True)
        self._show_bank(None)

    def collect(self) -> dict:
        payload = {"account_type": self.type_combo.currentData(), "currency_code": self.currency.currentData(),
                   "account_number": self.number_edit.text(), "iban": self.iban_edit.text(),
                   "overdraft_limit": self.overdraft_edit.text(),
                   "maturity_date": _pydate(self.maturity_date.date()) if self.has_maturity.isChecked() else None,
                   "notes": self.notes.text(), "is_active": self.is_active.isChecked()}
        if self.mode == "new":
            payload["bank_id"] = self.bank_combo.currentData()
            payload["account_mode"] = "auto" if self.mode_auto.isChecked() else "pick"
            payload["account_id"] = self.account_pick.currentData()
        return payload

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.banks.save_account(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.banks.delete_account(int(record_id), expected_version=self.current_version)


# =============================================================================
# شباك «البنوك» (البنك نفسه) — Base Screen عادي: فورم يمين + قائمة شمال
# =============================================================================
class BankListScreen(BaseScreen):
    MODULE = "cash"
    TARGET = "banks"
    TITLE = "البنوك — بيانات البنك"
    ICON = "🏦"
    FORM_TITLE = "بيانات البنك"
    NEW_TEXT = "بنك جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الفرع أو SWIFT… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "البنك"), ("swift_code", "SWIFT"), ("branch", "الفرع"),
               ("accounts_count", "الحسابات"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, banks: BankService | None = None, parent: QWidget | None = None) -> None:
        self.banks = banks or BankService()
        super().__init__(parent)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    def build_form(self, box: QGroupBox) -> None:
        form = _form(box)
        self.code_edit = _readonly("بيتولّد لوحده", ltr_text=True)
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("اسم البنك (زي «البنك الأهلي المصري»)")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setLayoutDirection(Qt.LeftToRight)
        self.short_name = QLineEdit()
        self.short_name.setMaxLength(60)
        self.short_name.setPlaceholderText("اختياري — زي «البنك الأهلي»")
        self.short_name.setToolTip("بيتكتب في اسم حساب الدليل: «البنك الأهلي — جاري» (لو فاضي بيتكتب الاسم كله)")
        self.branch = QLineEdit()
        self.branch.setMaxLength(120)
        self.address = QLineEdit()
        self.address.setMaxLength(250)
        self.swift = QLineEdit()
        self.swift.setMaxLength(11)
        self.swift.setLayoutDirection(Qt.LeftToRight)
        self.swift.setPlaceholderText("اختياري — 8 أو 11 حرف")
        self.contact_name = QLineEdit()
        self.contact_name.setMaxLength(120)
        self.contact_phone = QLineEdit()
        self.contact_phone.setMaxLength(30)
        self.contact_phone.setLayoutDirection(Qt.LeftToRight)
        self.is_active = QCheckBox("نشط")
        self.accounts_label = QLabel("")
        self.accounts_label.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        status_row = QWidget()
        row = QHBoxLayout(status_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.is_active)
        row.addWidget(self.accounts_label, 1)
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("الاسم المختصر", self.short_name)
        form.addRow("الفرع", self.branch)
        form.addRow("العنوان", self.address)
        form.addRow("SWIFT", self.swift)
        form.addRow("مسؤول الحساب", self.contact_name)
        form.addRow("تليفونه", self.contact_phone)
        form.addRow("الحالة", status_row)
        form.addRow("ملاحظات", self.notes)
        form.addRow(_hint("الحسابات البنكية نفسها (الرقم، IBAN، النوع، العملة) من الشاشة الأساسية. البنك اللي عليه "
                          "حسابات مايتمسحش، ومايتوقفش وحساب منهم نشط."))

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.toggle_button = self._button("إيقاف / تفعيل", "toggle", self.toggle_active)
        self.toggle_button.setVisible(self.has_action("edit"))
        layout.addWidget(self.toggle_button)

    def update_extra_buttons(self) -> None:
        idle = self.mode == "view" and self.current_id is not None
        self.toggle_button.setEnabled(idle and self.can("edit") and self.can("save"))

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.short_name, self.branch, self.address, self.swift,
                self.contact_name, self.contact_phone, self.is_active, self.notes]

    def new_record(self) -> None:
        super().new_record()
        if self.mode != "new":
            return
        try:
            self.code_edit.setText(f"{self.banks.new_bank_defaults()['code']} (مقترح)")
        except Exception:  # noqa: BLE001
            pass
        self.is_active.setChecked(True)
        self.name_ar.setFocus()

    def fetch_rows(self, keyword: str) -> list[dict]:
        return self.banks.list_banks(keyword)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "is_active":
            return "نشط" if value else "موقوف"
        if key == "swift_code":
            return ltr(value) if value else ""
        return default_format(key, value, row)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.banks.get_bank(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key, edit in (("name_ar", self.name_ar), ("name_en", self.name_en), ("short_name", self.short_name),
                          ("branch", self.branch), ("address", self.address), ("swift_code", self.swift),
                          ("contact_name", self.contact_name), ("contact_phone", self.contact_phone),
                          ("notes", self.notes)):
            edit.setText(record.get(key) or "")
        self.is_active.setChecked(bool(record.get("is_active", True)))
        total, active = int(record.get("accounts_count") or 0), int(record.get("active_accounts") or 0)
        self.accounts_label.setText(f"عليه {total} حساب بنكي ({active} نشط)" if total else "مالوش حسابات لسه")

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.short_name, self.branch, self.address,
                     self.swift, self.contact_name, self.contact_phone, self.notes):
            edit.clear()
        self.is_active.setChecked(True)
        self.accounts_label.setText("")

    def collect(self) -> dict:
        return {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(), "short_name": self.short_name.text(),
                "branch": self.branch.text(), "address": self.address.text(), "swift_code": self.swift.text(),
                "contact_name": self.contact_name.text(), "contact_phone": self.contact_phone.text(),
                "notes": self.notes.text(), "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.banks.save_bank(None if record_id is None else int(record_id), payload,
                                    expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.banks.delete_bank(int(record_id), expected_version=self.current_version)

    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل", f"عايز {question} البنك «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.banks.set_bank_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        self.load(self.current_id)
        messages.info(self, "تم", "تم تفعيل البنك." if new_state else "تم إيقاف البنك.")
