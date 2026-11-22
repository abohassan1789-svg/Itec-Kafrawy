"""شاشة «الشركاء» — النموذج 1 «Base Screen: فورم يمين + قائمة شمال» (اختيار المستخدم 2026-11-14).

```
┌ [شريك جديد][تعديل][حفظ][حذف][إلغاء][تحديث][إيقاف / تفعيل][تصدير إكسل][خروج]           (عرض) ┐
├──────────────────────────────────────┬──────────────────────────────────────┤
│ ┌ البيانات الأساسية ┐                 │ ┌ البحث والتسجيل ┐                       │
│   الكود PRT-001 (لوحده) · الاسم* · EN  │   🔍 الاسم / الكود / الموبايل …            │
│   نوع الشريك ▾ · تاريخ الدخول · الحالة │   [الكل][النشط][اللي خرجوا]                 │
│ ┌ الاتصال والهوية ┐                     │   الكود│الشريك│النوع│ربح│خسارة│المكتتب│…  │
│   الموبايل · … · الرقم القومي          │   عدد الشركاء: … · نشط … · خرجوا …          │
│ ┌ النسب ورأس المال ┐                    │   ✔ / ⚠ مجموع نسب الشركاء النشطين          │
│   ربح % · ☑ الخسارة بنفس النسبة · خسارة % · رأس المال المكتتب                    │
└──────────────────────────────────────┴──────────────────────────────────────┘
```
* الشاشة لتعريف الشريك بس — الجاري على 32 جاري الشركاء ورأس المال على 31 (الاتنين ببُعد الشريك)، والحركات
  (سحب / إيداع / زيادة رأس المال / توزيع الأرباح) في المرحلة 5.
* مجموع النسب مش 100% ← **تنبيه بس** (تحت القائمة وجنب النسب) · الشريك اللي خرج نسبته لازم تبقى 0.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
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

from app.services.partner_service import PAGE_SIZE, TYPE_LABELS, PartnerService, fmt_percent, share_warning
from app.ui.common import messages
from app.ui.common.base_screen import BaseScreen, align_form_right
from app.ui.common.data_table import default_format
from app.ui.common.theme import GREEN

ACTIVE_COLOR, MUTED, WARN = "#087443", "#64748B", "#B45309"
STATUS_CHIPS = (("all", "الكل"), ("active", "النشط"), ("inactive", "اللي خرجوا"))
ACCOUNTS_HINT = ("المدفوع من رأس المال من القيود على «31 رأس المال»، والجاري على «32 جاري الشركاء» — الاتنين "
                 "ببُعد الشريك. السحب والإيداع وتوزيع الأرباح مستندات في الخزينة (المرحلة 5).")
CHIP_STYLE = (
    "QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:11px; "
    "padding:3px 12px; font-weight:800; }"
    f"QPushButton:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}"
    "QPushButton:disabled { color:#94A3B8; }")


def _qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)


def _pydate(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _dec(value: float) -> Decimal:
    return Decimal(str(round(value, 2)))


class PartnersScreen(BaseScreen):
    MODULE = "acc"
    TARGET = "partners"
    TITLE = "الشركاء"
    ICON = "🤝"
    FORM_TITLE = "البيانات الأساسية"
    NEW_TEXT = "شريك جديد"
    SEARCH_PLACEHOLDER = "🔍 الاسم أو الكود أو الموبايل أو الرقم القومي… (F1)"
    COLUMNS = [("code", "الكود"), ("name_ar", "الشريك"), ("partner_type", "النوع"), ("profit_percent", "ربح %"),
               ("loss_percent", "خسارة %"), ("subscribed_capital", "رأس المال المكتتب"), ("mobile", "الموبايل"),
               ("is_active", "الحالة")]
    EXIT_LAST = True

    def __init__(self, partners: PartnerService | None = None, parent: QWidget | None = None) -> None:
        self.partners = partners or PartnerService()
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._others: dict[str, Decimal] = {"profit": Decimal("0"), "loss": Decimal("0")}
        super().__init__(parent)
        list_layout = self.count_label.parentWidget().layout()
        list_layout.addWidget(self.share_label)                    # تحت عدد السجلات
        list_layout.addLayout(self.paging)
        self._update_share_label()
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
    def _ltr(edit: QLineEdit, size: int, placeholder: str = "") -> QLineEdit:
        edit.setMaxLength(size)
        edit.setLayoutDirection(Qt.LeftToRight)
        if placeholder:
            edit.setPlaceholderText(placeholder)
        return edit

    @staticmethod
    def _number(maximum: float, suffix: str = "") -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(0, maximum)
        spin.setDecimals(2)
        spin.setGroupSeparatorShown(True)
        spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        spin.setLayoutDirection(Qt.LeftToRight)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def build_form(self, box: QGroupBox) -> None:
        form = self._form(box)
        self.code_edit = QLineEdit()
        self.code_edit.setReadOnly(True)
        self.code_edit.setLayoutDirection(Qt.LeftToRight)
        self.code_edit.setPlaceholderText("بيتولّد لوحده")
        self.code_edit.setToolTip("كود الشريك بيتولّد لوحده وقت الحفظ (PRT-001، PRT-002…) وثابت بعد كده")
        self.account_edit = QLineEdit()
        self.account_edit.setReadOnly(True)
        self.account_edit.setPlaceholderText("بيتعمل لوحده تحت جاري الشركاء")
        self.account_edit.setToolTip("كل شريك ليه حساب فرعي لوحده تحت «32 جاري الشركاء» بيتعمل لوحده مع الحفظ "
                                     "(3201، 3202…) واسمه بيتغيّر مع اسم الشريك — ورأس المال على «31» ببُعد الشريك")
        self.name_ar = QLineEdit()
        self.name_ar.setMaxLength(200)
        self.name_ar.setPlaceholderText("اسم الشريك")
        self.name_en = self._ltr(QLineEdit(), 200)
        self.partner_type = QComboBox()
        for key, label in TYPE_LABELS.items():
            self.partner_type.addItem(label, key)
        self.has_join = QCheckBox("محدد")
        self.join_date = QDateEdit(QDate.currentDate())
        self.join_date.setCalendarPopup(True)
        self.join_date.setDisplayFormat("dd/MM/yyyy")
        self.join_date.setMinimumDate(QDate(1950, 1, 1))
        self.join_date.setLayoutDirection(Qt.LeftToRight)
        self.has_join.toggled.connect(lambda on: self.join_date.setEnabled(on and self.mode in ("new", "edit")))
        join_row = QWidget()
        row = QHBoxLayout(join_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.has_join)
        row.addWidget(self.join_date, 1)
        self.is_active = QCheckBox("نشط")
        self.is_active.toggled.connect(self._update_share_hint)
        form.addRow("الكود", self.code_edit)
        form.addRow("الحساب", self.account_edit)
        form.addRow("الاسم *", self.name_ar)
        form.addRow("الاسم EN", self.name_en)
        form.addRow("نوع الشريك", self.partner_type)
        form.addRow("تاريخ الدخول", join_row)
        form.addRow("الحالة", self.is_active)

    def _build_contact(self) -> QGroupBox:
        box = QGroupBox("الاتصال والهوية")
        form = self._form(box)
        self.mobile = self._ltr(QLineEdit(), 30)
        self.phone = self._ltr(QLineEdit(), 30)
        self.email = self._ltr(QLineEdit(), 120, "name@company.com")
        self.address = QLineEdit()
        self.address.setMaxLength(300)
        self.national_id = self._ltr(QLineEdit(), 17, "اختياري — 14 رقم")
        self.national_id.setToolTip("الرقم القومي: اختياري، 14 رقم، ومايتكررش لشريكين")
        self.notes = QLineEdit()
        self.notes.setMaxLength(500)
        form.addRow("الموبايل", self.mobile)
        form.addRow("التليفون", self.phone)
        form.addRow("الإيميل", self.email)
        form.addRow("العنوان", self.address)
        form.addRow("الرقم القومي", self.national_id)
        form.addRow("ملاحظات", self.notes)
        return box

    def _build_shares(self) -> QGroupBox:
        box = QGroupBox("النسب ورأس المال")
        form = self._form(box)
        self.profit_percent = self._number(100, " %")
        self.loss_percent = self._number(100, " %")
        self.same_loss = QCheckBox("الخسارة بنفس نسبة الربح")
        self.same_loss.setChecked(True)
        self.subscribed_capital = self._number(9_999_999_999_999.99)
        self.subscribed_capital.setToolTip("رقم مرجعي — المدفوع الفعلي من القيود على 31 رأس المال ببُعد الشريك")
        self.profit_percent.valueChanged.connect(self._on_profit)
        self.loss_percent.valueChanged.connect(self._update_share_hint)
        self.same_loss.toggled.connect(self._on_same_loss)
        self.share_hint = QLabel("")
        self.share_hint.setWordWrap(True)
        form.addRow("نسبة الربح", self.profit_percent)
        form.addRow("", self.same_loss)
        form.addRow("نسبة الخسارة", self.loss_percent)
        form.addRow("رأس المال المكتتب", self.subscribed_capital)
        form.addRow("", self.share_hint)
        hint = QLabel(ACCOUNTS_HINT)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{MUTED}; font-weight:600;")
        form.addRow(hint)
        return box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        for box in (self._build_contact(), self._build_shares()):
            align_form_right(box)
            right.addWidget(box)
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
        # مجموع النسب + الصفحات (بيتحطوا تحت عدد السجلات بعد ما الـ Base يخلص)
        self.share_label = QLabel("")
        self.share_label.setWordWrap(True)
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
        return [self.name_ar, self.name_en, self.partner_type, self.has_join, self.is_active, self.mobile, self.phone,
                self.email, self.address, self.national_id, self.notes, self.profit_percent, self.same_loss,
                self.loss_percent, self.subscribed_capital]

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in ("new", "edit")
        self.join_date.setEnabled(editing and self.has_join.isChecked())
        self.loss_percent.setEnabled(editing and not self.same_loss.isChecked())
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
        try:
            defaults = self.partners.new_defaults()
            self._load_others(None)
        except Exception:  # noqa: BLE001
            return
        self.code_edit.setText(f"{defaults['code']} (مقترح)")
        self.account_edit.setText(self.partners.account_hint())
        self.is_active.setChecked(True)
        self.partner_type.setCurrentIndex(max(0, self.partner_type.findData(defaults["partner_type"])))
        self._update_share_hint()
        self.name_ar.setFocus()

    # ---- النسب ---------------------------------------------------------------
    def _on_profit(self, value: float) -> None:
        if self.same_loss.isChecked():
            self.loss_percent.setValue(value)
        self._update_share_hint()

    def _on_same_loss(self, on: bool) -> None:
        if on:
            self.loss_percent.setValue(self.profit_percent.value())
        self.loss_percent.setEnabled(self.mode in ("new", "edit") and not on)
        self._update_share_hint()

    def _load_others(self, partner_id: Any) -> None:
        """مجموع نسب الشركاء النشطين التانيين (عشان «المجموع مع الشريك ده» يتحسب وانت بتكتب)."""
        try:
            totals = self.partners.share_totals(None if partner_id is None else int(partner_id))
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            totals = {"profit": Decimal("0"), "loss": Decimal("0")}
        self._others = {"profit": totals["profit"], "loss": totals["loss"]}

    def _update_share_hint(self) -> None:
        if not hasattr(self, "share_hint"):
            return
        mine = self.is_active.isChecked()
        profit = self._others["profit"] + (_dec(self.profit_percent.value()) if mine else 0)
        loss = self._others["loss"] + (_dec(self.loss_percent.value()) if mine else 0)
        warning = share_warning(profit, loss)
        if warning is None:
            self.share_hint.setText("✔ مجموع نسب الشركاء النشطين مع الشريك ده: 100%")
            self.share_hint.setStyleSheet(f"color:{ACTIVE_COLOR}; font-weight:700;")
        else:
            self.share_hint.setText("⚠ مع الشريك ده — " + warning)
            self.share_hint.setStyleSheet(f"color:{WARN}; font-weight:700;")

    def _update_share_label(self) -> None:
        if not hasattr(self, "share_label"):
            return
        try:
            totals = self.partners.share_totals()
        except Exception:  # noqa: BLE001
            self.share_label.setText("")
            return
        warning = share_warning(totals["profit"], totals["loss"])
        if totals["partners"] == 0:
            self.share_label.setText("")
        elif warning is None:
            self.share_label.setText(f"✔ مجموع نسب الشركاء النشطين: الربح 100% · الخسارة 100% · "
                                     f"رأس المال المكتتب {totals['capital']:,.2f}")
            self.share_label.setStyleSheet(f"color:{ACTIVE_COLOR}; font-weight:800;")
        else:
            self.share_label.setText("⚠ " + warning)
            self.share_label.setStyleSheet(f"color:{WARN}; font-weight:800;")

    # ---- القائمة والصفحات ----------------------------------------------------
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
        self._counts = self.partners.count_partners(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.partners.list_partners(*query, page=self.page, page_size=PAGE_SIZE)

    def lookup_rows(self) -> list[dict]:
        return self.partners.lookup_partners()

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "partner_type":
            return TYPE_LABELS.get(value, value or "")
        if key in ("profit_percent", "loss_percent"):
            return f"{fmt_percent(value)}%"
        if key == "subscribed_capital":
            return f"{Decimal(value or 0):,.2f}"
        if key == "mobile":
            return f"⁦{value}⁩" if value else ""
        if key == "is_active":
            return "نشط" if value else "خرج"
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
            self.count_label.setText(f"عدد الشركاء: {counts['total']} · نشط {counts['active']} · "
                                     f"خرجوا {counts['inactive']}")
        if hasattr(self, "prev_button"):
            self._update_paging(self.mode in ("new", "edit"))
        self._update_share_label()

    # ---- الأزرار الزيادة ---------------------------------------------------
    def toggle_active(self) -> None:
        if self.current_id is None or not self.current_record:
            return
        new_state = not bool(self.current_record.get("is_active"))
        question = "تفعيل" if new_state else "إيقاف (خروج)"
        if not messages.ask_yes_no(self, "إيقاف / تفعيل",
                                   f"عايز {question} الشريك «{self.current_record['name_ar']}»؟"):
            return
        try:
            self.partners.set_active(int(self.current_id), new_state, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التنفيذ", exc)
            return
        self.refresh_list()
        if self.table.select_id(self.current_id):
            self.load(self.current_id)
            messages.info(self, "تم", "تم تفعيل الشريك." if new_state else
                          "تم إيقاف الشريك (مش هيتختار في أي مستند جديد).")
        else:
            self.show_empty()
            messages.info(self, "تم", "تم واختفى من القائمة بسبب الفلتر — اختار «الكل» عشان تشوفه.")

    # ---- البيانات ----------------------------------------------------------
    def fetch_record(self, record_id: Any) -> dict | None:
        return self.partners.get_partner(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._load_others(record.get("id"))
        self.code_edit.setText(record.get("code") or "")
        self.account_edit.setText(f"⁦{record['account_code']}⁩ {record['account_name']}"
                                  if record.get("account_code") else "")
        for key in ("name_ar", "name_en", "mobile", "phone", "email", "address", "national_id", "notes"):
            getattr(self, key).setText(record.get(key) or "")
        self.partner_type.setCurrentIndex(max(0, self.partner_type.findData(record.get("partner_type"))))
        self.is_active.setChecked(bool(record.get("is_active", True)))
        joined = record.get("join_date")
        self.has_join.setChecked(joined is not None)
        self.join_date.setDate(_qdate(joined) if joined else QDate.currentDate())
        profit, loss = Decimal(record.get("profit_percent") or 0), Decimal(record.get("loss_percent") or 0)
        self.same_loss.setChecked(profit == loss)
        self.profit_percent.setValue(float(profit))
        self.loss_percent.setValue(float(loss))
        self.subscribed_capital.setValue(float(record.get("subscribed_capital") or 0))
        self._update_share_hint()

    def clear_form(self) -> None:
        for edit in (self.code_edit, self.account_edit, self.name_ar, self.name_en, self.mobile, self.phone,
                     self.email, self.address, self.national_id, self.notes):
            edit.clear()
        self.partner_type.setCurrentIndex(0)
        self.is_active.setChecked(True)
        self.has_join.setChecked(False)
        self.join_date.setDate(QDate.currentDate())
        self.same_loss.setChecked(True)
        self.profit_percent.setValue(0)
        self.loss_percent.setValue(0)
        self.subscribed_capital.setValue(0)
        self._others = {"profit": Decimal("0"), "loss": Decimal("0")}
        self.share_hint.setText("")

    def collect(self) -> dict:
        profit = _dec(self.profit_percent.value())
        return {"name_ar": self.name_ar.text(), "name_en": self.name_en.text(),
                "partner_type": self.partner_type.currentData(), "mobile": self.mobile.text(),
                "phone": self.phone.text(), "email": self.email.text(), "address": self.address.text(),
                "national_id": self.national_id.text(), "notes": self.notes.text(),
                "join_date": _pydate(self.join_date.date()) if self.has_join.isChecked() else None,
                "profit_percent": profit,
                "loss_percent": profit if self.same_loss.isChecked() else _dec(self.loss_percent.value()),
                "subscribed_capital": _dec(self.subscribed_capital.value()),
                "is_active": self.is_active.isChecked()}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.partners.save_partner(None if record_id is None else int(record_id), payload,
                                          expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.partners.delete_partner(int(record_id), expected_version=self.current_version)
