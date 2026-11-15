"""شاشة «الخزائن» — النموذج 1 «Base Screen: فورم يمين + قائمة شمال» (اختيار المستخدم 2026-11-15).

```
┌ [خزنة جديدة][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]            (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ البيانات الأساسية ┐                 │ ┌ البحث والتسجيل ┐                       │
│   الكود TRS-001 (لوحده) · الاسم* · EN  │   🔍 الاسم / الكود / الحساب / الأمين …     │
│   العملة ▾ · المكان · الحالة · ⭐       │   [الكل][النشطة][الموقوفة]                  │
│ ┌ الحساب والأمين ┐                     │   الكود│الخزنة│العملة│الحساب│الأمين│الحالة  │
│   (•) يتعمل لوحده تحت 1101 ( ) موجود ▾ │   عدد الخزائن: … · نشطة … · موقوفة …        │
│   أمين الخزينة ▾ · حد أقصى · ملاحظات   │                                            │
│ ┌ الرصيد (توضيحي) ┐                    │                                            │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* الشاشة لتعريف الخزنة بس — الفلوس اللي بتدخل وتخرج في المرحلة 5، والرصيد من القيود على حساب الخزنة.
* الحساب بيتعمل لوحده (أو يتختار من الدليل) وثابت بعد الحفظ · الأمين من «أمناء الخزينة» بس.
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
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.services.treasury_service import BALANCE_NOTE, PAGE_SIZE, TreasuryService, fmt_amount
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED, WARN = "#087443", "#64748B", "#B45309"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشطة"), ("inactive", "الموقوفة"))
NO_TREASURER = "— من غير أمين —"
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


class TreasuriesScreen(BaseScreen):
    MODULE = "cash"
    TARGET = "treasuries"
    TITLE = "الخزائن"
    ICON = "💵"
    FORM_TITLE = "البيانات الأساسية"
    NEW_TEXT = "خزنة جديدة"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الحساب أو الأمين… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "الخزنة"), ("currency_code", "العملة"), ("account_code", "الحساب"),
               ("treasurer_name", "الأمين"), ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, treasuries: TreasuryService | None = None, parent: QWidget | None = None) -> None:
        self.treasuries = treasuries or TreasuryService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._suggested: dict | None = None
        super().__init__(parent)
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

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = self._readonly("بيتولّد لوحده", ltr=True)
        self.code_edit.setToolTip("كود الخزنة بيتولّد لوحده وقت الحفظ (TRS-001، TRS-002…) وثابت بعد كده")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(120)
        self.name_ar.setPlaceholderText("اسم الخزنة (زي «خزينة الموقع»)")
        self.name_en = QLineEdit()
        self.name_en.setMaxLength(120)
        self.name_en.setLayoutDirection(Qt.LeftToRight)
        self.currency = QComboBox()
        self.currency.setToolTip("عملة الخزنة = عملة حسابها (تغييرها بيغيّر عملة الحساب معاها)")
        self.location = QLineEdit()
        self.location.setMaxLength(200)
        self.location.setPlaceholderText("مكان الخزنة (اختياري)")
        self.is_active = QCheckBox("نشطة")
        self.default_label = QLabel("")
        self.default_label.setStyleSheet("color:#3730A3; font-weight:800;")
        status_row = QWidget()
        row = QHBoxLayout(status_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.is_active)
        row.addWidget(self.default_label, 1)
        form.addRow("الكود", self.code_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("العملة *", self.currency)
        form.addRow("المكان", self.location)
        form.addRow("الحالة", status_row)

    def _build_account(self) -> QGroupBox:
        box = QGroupBox("الحساب والأمين")
        form = self._form(box)
        self._account_form = form
        self.mode_auto = QRadioButton("يتعمل لوحده تحت «النقدية بالخزائن»")
        self.mode_pick = QRadioButton("أختار حساب موجود")
        self.mode_auto.setChecked(True)
        self.account_modes = QButtonGroup(self)
        self.account_modes.addButton(self.mode_auto)
        self.account_modes.addButton(self.mode_pick)
        self.mode_auto.toggled.connect(self._on_account_mode)
        self.account_pick = QComboBox()
        self.account_pick.setToolTip("الحسابات الفرعية النشطة بدور «خزينة» ومش مربوطة بخزنة تانية")
        self.account_pick.currentIndexChanged.connect(self._on_pick_account)
        self.account_view = self._readonly("", ltr=False)
        self.account_parent = self._readonly("")
        self.treasurer = QComboBox()
        self.treasurer.setToolTip("الموظفين النشطين اللي عليهم دور «أمين خزينة» (من شاشة الموظفين)")
        self.max_balance = QLineEdit()
        self.max_balance.setLayoutDirection(Qt.LeftToRight)
        self.max_balance.setPlaceholderText("من غير حد")
        self.max_balance.setToolTip("اختياري — تنبيه بس لو رصيد الخزنة عدّاه (مع السندات)")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        modes = QWidget()
        col = QVBoxLayout(modes)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        col.addWidget(self.mode_auto)
        col.addWidget(self.mode_pick)
        self.modes_widget = modes
        form.addRow("الحساب", modes)
        form.addRow("", self.account_pick)
        form.addRow("حساب الخزنة", self.account_view)
        form.addRow("تحت", self.account_parent)
        form.addRow("أمين الخزينة", self.treasurer)
        form.addRow("حد أقصى للرصيد", self.max_balance)
        form.addRow("ملاحظات", self.notes)
        self.account_hint = self._hint("الحساب بيتعمل لوحده تحت 1101 باسم الخزنة وعملتها (أو بيتختار من الدليل) "
                                       "وثابت بعد الحفظ — واسمه بيتغيّر مع اسم الخزنة لو كان زيه.")
        form.addRow(self.account_hint)
        return box

    def _build_balance(self) -> QGroupBox:
        box = QGroupBox("الرصيد (توضيحي)")
        form = self._form(box)
        self.opening_view = self._readonly("— (مستند الأرصدة الافتتاحية — المرحلة 4)")
        self.balance_view = self._readonly("— (من القيود — المرحلة 5)")
        self.last_move_view = self._readonly("—")
        form.addRow("رصيد أول المدة", self.opening_view)
        form.addRow("الرصيد الحالي", self.balance_view)
        form.addRow("آخر حركة", self.last_move_view)
        form.addRow(self._hint(BALANCE_NOTE))
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_account(), self._build_balance()):
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

    # ---- الاختيارات (العملات / الأمناء / الحسابات الفاضية) ---------------------
    def _load_choices(self, keep_currency: str | None = None) -> None:
        """بتتحمّل من جديد مع «جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            currencies = self.treasuries.currencies()
            treasurers = self.treasuries.treasurers()
            accounts = self.treasuries.free_accounts()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            currencies, treasurers, accounts = [], [], []
        self.currency.blockSignals(True)
        self.currency.clear()
        for c in currencies:
            self.currency.addItem(f"{c['code']} — {c['name_ar']}", c["code"])
        if keep_currency and self.currency.findData(keep_currency) < 0:
            self.currency.addItem(keep_currency, keep_currency)        # عملة موقوفة على خزنة قديمة
        self.currency.blockSignals(False)
        self.treasurer.clear()
        self.treasurer.addItem(NO_TREASURER, None)
        for e in treasurers:
            self.treasurer.addItem(f"{e['name_ar']} ({e['code']})", e["id"])
        self.account_pick.clear()
        for a in accounts:
            self.account_pick.addItem(f"⁦{a['code']}⁩ {a['name_ar']} ({a['currency_code']})", a["id"])
        self._free_accounts = {a["id"]: a for a in accounts}

    def _set_treasurer(self, record: dict) -> None:
        tid = record.get("treasurer_id")
        index = self.treasurer.findData(tid) if tid is not None else 0
        if index < 0:                                              # أمين قديم مابقاش في القايمة (للعرض بس)
            self.treasurer.addItem(f"{record.get('treasurer_name') or ''} ({record.get('treasurer_code') or ''})",
                                   tid)
            index = self.treasurer.count() - 1
        self.treasurer.setCurrentIndex(index)

    def _on_account_mode(self, auto: bool) -> None:
        self.account_pick.setEnabled(self.mode == "new" and not auto)
        if self.mode == "new":
            self._show_new_account()

    def _on_pick_account(self, _index: int) -> None:
        """الحساب الموجود بيحدد عملة الخزنة (لازم يبقوا زي بعض)."""
        account = getattr(self, "_free_accounts", {}).get(self.account_pick.currentData())
        if self.mode == "new" and self.mode_pick.isChecked() and account is not None:
            self.currency.setCurrentIndex(max(0, self.currency.findData(account["currency_code"])))
            if not self.name_ar.text().strip():
                self.name_ar.setText(account["name_ar"])

    def _show_new_account(self) -> None:
        if self.mode_auto.isChecked():
            s = self._suggested
            if s and s.get("code"):
                self.account_view.setText(f"⁦{s['code']}⁩ (هيتعمل لوحده)")
                self.account_parent.setText(f"⁦{s['parent_code']}⁩ {s['parent_name']}")
            else:
                self.account_view.setText("")
                self.account_parent.setText("مفيش حساب رئيسي بدور «خزينة» — اختار حساب موجود")
        else:
            self.account_view.setText("")
            self.account_parent.setText("")
            self._on_pick_account(self.account_pick.currentIndex())

    def editable_widgets(self) -> list[QWidget]:
        return [self.name_ar, self.name_en, self.currency, self.location, self.is_active, self.treasurer,
                self.max_balance, self.notes]

    def new_only_widgets(self) -> list[QWidget]:
        return [self.mode_auto, self.mode_pick]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        self._account_form.setRowVisible(self.modes_widget, mode == "new")    # الاختيار في «جديد» بس
        self._account_form.setRowVisible(self.account_pick, mode == "new")
        self.account_pick.setEnabled(mode == "new" and self.mode_pick.isChecked())
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
            defaults = self.treasuries.new_defaults()
        except Exception:  # noqa: BLE001
            return
        self._suggested = defaults.get("suggested_account")
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.is_active.setChecked(True)
        self.currency.setCurrentIndex(max(0, self.currency.findData(defaults["currency_code"])))
        self.treasurer.setCurrentIndex(0)
        self.mode_auto.setChecked(True)
        self.set_mode("new")
        self._show_new_account()
        self.name_ar.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices(keep_currency=(self.current_record or {}).get("currency_code"))
        super().edit_record()

    # ---- القائمة والصفحات ----------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return keyword, self.status

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("status")
        self.page = 1
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()                                      # المعروضة اتخفت بالفلتر

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
        self._counts = self.treasuries.count_treasuries(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.treasuries.list_treasuries(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.treasuries.lookup_treasuries()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "name_ar":
            return f"{value} ⭐" if row.get("is_default") else (value or "")
        if key == "account_code":
            return f"⁦{value}⁩" if value else ""
        if key == "treasurer_name":
            return value or "—"
        if key == "is_active":
            return "نشطة" if value else "موقوفة"
        return default_format(key, value, row)

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
            self.count_label.setText(f"عدد الخزائن: {counts['total']} · نشطة {counts['active']} · "
                                     f"موقوفة {counts['inactive']}  ·  ⭐ = الخزينة الافتراضية")
        if hasattr(self, "prev_button"):
            self._update_paging(self.mode in ("new", "edit"))

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} الخزنة «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.treasuries.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل الخزنة." if new_state else
                          "تم إيقاف الخزنة (مش هتتختار في أي سند جديد).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفت من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفها.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.treasuries.get_treasury(int(record_id))

    def fill_form(self, record: dict) -> None:
        self.code_edit.setText(record.get("code") or "")
        for key in ("name_ar", "name_en", "location", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        currency = record.get("currency_code")
        if self.currency.findData(currency) < 0:
            self.currency.addItem(currency, currency)
        self.currency.setCurrentIndex(self.currency.findData(currency))
        self.is_active.setChecked(bool(record.get("is_active", True)))
        self.default_label.setText("⭐ الخزينة الافتراضية (لسندات القبض والصرف)" if record.get("is_default") else "")
        self.account_view.setText(f"⁦{record.get('account_code') or ''}⁩ {record.get('account_name') or ''}")
        self.account_parent.setText(f"⁦{record.get('parent_code') or ''}⁩ {record.get('parent_name') or ''}"
                                    if record.get("parent_code") else "")
        self._set_treasurer(record)
        value = record.get("max_balance")
        self.max_balance.setText(fmt_amount(value) if value is not None else "")
        self.opening_view.clear()
        self.balance_view.clear()
        self.last_move_view.clear()

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.name_ar, self.name_en, self.location, self.notes, self.max_balance,
                     self.account_view, self.account_parent):
            edit.clear()
        if self.currency.count():
            self.currency.setCurrentIndex(0)
        self.treasurer.setCurrentIndex(0)
        self.is_active.setChecked(True)
        self.default_label.setText("")
        self.mode_auto.setChecked(True)

    def collect(self) -> dict:
        payload = {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                   "currency_code": self.currency.currentData(), "location": self.location.text(),
                   "notes": self.notes.text(), "treasurer_id": self.treasurer.currentData(),
                   "max_balance": self.max_balance.text(), "is_active": self.is_active.isChecked()}
        if self.mode == "new":
            payload["account_mode"] = "auto" if self.mode_auto.isChecked() else "pick"
            payload["account_id"] = self.account_pick.currentData()
        return payload

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.treasuries.save_treasury(None if record_id is None else int(record_id), payload,
                                             expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.treasuries.delete_treasury(int(record_id), expected_version=self.current_version)
