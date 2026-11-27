"""شاشة «فاتورة الشراء» — النموذج 9 «بيانات الفاتورة بتبويبات» بشكل «فاتورة المبيعات السعودية» (اختيار المستخدم
2026-11-27 — mockups/phase7/02_purchase_invoice_ksa.html · المرجع D:\\PHASEINV2\\InvoicesKSA_publish قراية بس).

```
🧾 فاتورة الشراء [مسودة]                                              المستخدم · التاريخ
[🔍][＋ جديد][⧉ تكرار][💾 حفظ][✎ تعديل][⟳ تحديث][✔✔ ترحيل][↩ إلغاء الترحيل][🗑 حذف][✕ إلغاء][⇥ خروج] ☐ مخزن ☐ وحدة ☐ خصم
┌ 📅 بيانات الفاتورة  [الأساسية][الدفع والعملة][إضافية] ───────────────────────────────────────────────┐
│  رقم الفاتورة · التاريخ · المورد 🔍 · الرقم الضريبي · رقم فاتورة المورد · النوع · المخزن · أمر التوريد  │
└──────────────────────────────────────────────────────────────────────────────────────────────────────┘
┌ ◔ الإجماليات (340) ┐ ┌ ☰ تفاصيل الفاتورة ─────────────────────────────────────────────────────────┐
│ قبل الضريبة         │ │ رقم الصنف │ اسم الصنف │ المخزن │ الوحدة │ الكمية │ السعر │ الخصم │ … │ الإجمالي │
│ خصم % · قيمة الخصم  │ │                                                                              │
│ بعد الخصم · الضريبة │ │                                                                              │
│ ■ النهائي (بالجنيه) │ │ إجمالي الأصناف قبل الضريبة                                           0.00    │
└────────────────────┘ └──────────────────────────────────────────────────────────────────────────────┘
┌ 🏷 إضافة صنف: [الصنف 🔍][رقم الصنف][المخزن][الوحدة][الكمية][السعر][خصم %][الضريبة] [＋ إضافة إلى الفاتورة][تفريغ][🗑 حذف الصنف] ┐
│ المتاح في «المخزن» · آخر سعر شراء · الوحدة الأساسية · Enter في الكمية أو السعر = إضافة                         │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```
* الضغط على سطر في الجدول بينزّله تحت ويبقى «تحديث السطر» · نفس الصنف بنفس المخزن والوحدة والسعر بيتجمّع.
* الإجماليات والمراجعة بتتحسب وانت بتكتب (✔ جاهز / ✖ مشاكل — وتحت كل سطر غلطه أو تنبيهه) · «ترحيل» بيوقف لو فيه ✖.
* العملة: أي عملة (عملة واحدة بسعرها بتاريخ الفاتورة) أو «🌐 متعدد العملات» (كل سطر بعملته وسعره).
* «بحث» (F1) = نافذة الفواتير بفلتر فترة وحالة وصفحات · «تصدير» = أصناف الفاتورة المفتوحة لإكسل.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.security.session import SESSION
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.purchase_invoice_service import (
    PAGE_SIZE,
    PurchaseInvoiceService,
    fmt_rate,
    invoice_label,
)
from app.services.stock_ledger_service import fmt_qty
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, MODE_NAMES, BaseScreen, align_form_right, export_rows_to_excel
from app.ui.common.data_table import DataTable
from app.ui.common.lookup_edit import LookupEdit

GREEN, GREEN_DARK = "#137A38", "#0F6B30"      # أخضر البرنامج (اقتراح سؤال الألوان — مش #00843D)
FONT = "'Segoe UI', 'Tahoma', 'Arial'"
MULTI = "__multi__"
# (الخلفية، الكلام، الحد، hover) لكل نوع زرار — زي الفاتورة السعودية بأخضر البرنامج.
BTN_KINDS: dict[str, tuple[str, str, str, str]] = {
    "green": (GREEN, "#FFFFFF", GREEN, GREEN_DARK),
    "blue": ("#2563EB", "#FFFFFF", "#2563EB", "#1D4ED8"),
    "red": ("#DC2626", "#FFFFFF", "#DC2626", "#B91C1C"),
    "white": ("#FFFFFF", "#374151", "#D7DEE7", "#F3F4F6"),
    "gray": ("#4B5563", "#FFFFFF", "#4B5563", "#374151"),
}
STATUS_STYLE = {"draft": ("مسودة", "#FEF3C7", "#92400E"), "posted": ("مرحّلة ✔", "#E7F6EE", "#087443"),
                "new": ("جديدة", "#EFF6FF", "#1D4ED8")}
# أعمدة جدول التفاصيل
(C_CODE, C_NAME, C_WH, C_UNIT, C_QTY, C_PRICE, C_DISC, C_CUR, C_PRE, C_TAXR, C_VAT, C_TOTAL) = range(12)
HEADERS = ["رقم الصنف", "اسم الصنف", "المخزن", "الوحدة", "الكمية", "السعر", "الخصم %", "العملة",
           "إجمالي قبل الضريبة", "نسبة الضريبة", "قيمة الضريبة", "إجمالي شامل الضريبة"]
MARK_COLORS = {"no": "#B91C1C", "warn": "#B45309"}

QSS = f"""
QWidget#purchaseInvoiceScreen {{ background:#F5F7FA; }}
QFrame#card {{ background:#FFFFFF; border:1px solid #E5E7EB; border-radius:12px; }}
QLabel {{ font-family:{FONT}; font-weight:700; background:transparent; }}
QLabel#titleLabel {{ color:#1F2D3D; font-size:21px; font-weight:800; }}
QLabel#subtitleLabel, QLabel#metaLabel {{ color:#6B7280; font-size:12px; font-weight:700; }}
QLabel#sectionTitle {{ color:{GREEN}; font-size:15px; font-weight:800; }}
QLabel#fieldLabel {{ color:#111827; font-size:12px; font-weight:700; }}
QLabel#infoLabel {{ color:#475569; font-size:12px; font-weight:700; }}
QLineEdit, QComboBox, QDateEdit {{ min-height:26px; max-height:30px; border:1px solid #D7DEE7; border-radius:6px;
    background:#FFFFFF; padding:1px 8px; color:#111827; font-size:12px; font-weight:700; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus {{ border:1px solid {GREEN}; }}
QLineEdit:read-only {{ background:#F4F6F9; }}
QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{ background:#F4F6F9; color:#4B5563; }}
QTableWidget {{ border:1px solid #E5E7EB; border-radius:8px; background:#FFFFFF; gridline-color:#EEF1F4;
    font-size:13px; font-weight:700; }}
QTableWidget::item {{ padding:3px; color:#374151; }}
QTableWidget::item:selected {{ background:#E7F6EE; color:#0B3B23; }}
QHeaderView::section {{ background:{GREEN}; color:#FFFFFF; font-size:12px; font-weight:800; border:none; padding:6px 4px; }}
QFrame#tableFooter {{ background:#EAF6EF; border:1px solid #D7EBDF; border-radius:8px; }}
QFrame#summaryRow {{ background:#F9FAFB; border:1px solid #EEF1F4; border-radius:8px; }}
QFrame#netBox {{ background:#E8F5EC; border:1px solid #BFE6CF; border-radius:10px; }}
QFrame#egpBox {{ background:#EFF6FF; border:1px solid #BFDBFE; border-radius:10px; }}
QLabel#netLabel {{ color:{GREEN}; font-size:12px; font-weight:800; }}
QLabel#netValue {{ color:{GREEN}; font-size:17px; font-weight:900; }}
QLabel#sumValue {{ color:#111827; font-size:14px; font-weight:800; }}
QPushButton#tabChip {{ background:#F9FAFB; color:#374151; border:1px solid #E5E7EB; border-radius:8px;
    padding:5px 16px; font-weight:800; font-size:12px; }}
QPushButton#tabChip:checked {{ background:{GREEN}; color:#FFFFFF; border-color:{GREEN}; }}
QPushButton#lookupButton {{ background:{GREEN}; color:#FFFFFF; border:none; border-radius:6px; font-weight:800; }}
QPushButton#lookupButton:hover {{ background:{GREEN_DARK}; }}
QPushButton#lookupButton:disabled {{ background:#CBD5E1; }}
QCheckBox {{ color:#111827; font-weight:800; font-size:12px; }}
"""


def button_qss(kind: str) -> str:
    bg, fg, border, hover = BTN_KINDS.get(kind, BTN_KINDS["gray"])
    return (f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {border}; border-radius:8px; "
            f"padding:3px 9px; font-family:{FONT}; font-size:12px; font-weight:800; }}"
            f"QPushButton:hover {{ background:{hover}; }}"
            "QPushButton:disabled { background:#EAECEF; color:#AEB6C0; border-color:#EAECEF; }")


def money(value: Any) -> str:
    if value in (None, ""):
        return ""
    return f"{Decimal(str(value)):,.2f}"


def num(value: Any) -> str:
    """رقم للخانات (من غير فواصل ولا أصفار زيادة)."""
    if value in (None, ""):
        return ""
    text = f"{Decimal(str(value)):f}"
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


def _dec(text: Any) -> Decimal:
    try:
        return Decimal(str(text or "0").replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal("0")


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


def _date_edit() -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("dd/MM/yyyy")
    edit.setLayoutDirection(Qt.LeftToRight)
    edit.setDate(_qdate(dt.date.today()))
    return edit


def _cell(text: str, *, number: bool = False, color: str | None = None, bold: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment((Qt.AlignLeft if number else Qt.AlignCenter) | Qt.AlignAbsolute | Qt.AlignVCenter)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    if color:
        item.setForeground(QColor(color))
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    return item


class PurchaseInvoicesScreen(BaseScreen):
    MODULE = "purch"
    TARGET = "purchase_invoices"
    TITLE = "فاتورة الشراء"
    ICON = "🧾"
    NEW_TEXT = "جديد"
    COLUMNS = [("invoice_no", "رقم الفاتورة"), ("invoice_date", "التاريخ"), ("supplier_name", "المورد"),
               ("supplier_invoice_no", "رقم فاتورة المورد"), ("total", "الإجمالي"), ("status", "الحالة")]

    def __init__(self, service: PurchaseInvoiceService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or PurchaseInvoiceService()
        self._choices: dict[str, Any] = {}
        self._lines: list[dict[str, Any]] = []
        self._sel = -1
        self._filling = False
        self._discount = Decimal("0")
        self._review: dict[str, Any] | None = None
        self._terms = 0
        self._supplier_tax: int | None = None
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh_review)
        self._load_choices()
        super().__init__(parent)
        latest = self.service.list_invoices(None, None, page_size=1)
        if latest:
            self.load(latest[0]["id"])
        else:
            self._refresh_review()

    # ---- البناء ------------------------------------------------------------
    def _load_choices(self) -> None:
        try:
            self._choices = self.service.choices()
        except Exception as exc:  # noqa: BLE001
            self._choices = {"suppliers": [], "warehouses": [], "items": [], "units": {}, "tax_codes": [],
                             "currencies": [], "treasuries": [], "banks": [], "projects": [], "base_currency": "EGP"}
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
        self._items = {int(i["id"]): i for i in self._choices["items"]}
        self._by_code = {str(i["code"]).upper(): int(i["id"]) for i in self._choices["items"]}
        self._whs = {int(w["id"]): w for w in self._choices["warehouses"]}
        self._taxes = {int(t["id"]): t for t in self._choices["tax_codes"]}
        self._base = self._choices.get("base_currency") or "EGP"

    def _build_ui(self) -> None:
        self.setObjectName("purchaseInvoiceScreen")
        self.setStyleSheet(QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 6, 12, 8)
        root.setSpacing(6)
        root.addWidget(self._build_title())
        root.addWidget(self._build_toolbar())
        self.msg_label = QLabel("")
        self.msg_label.setWordWrap(True)
        self.msg_label.setVisible(False)
        root.addWidget(self.msg_label)
        form_box = QGroupBox()
        form_box.setStyleSheet("QGroupBox { border:none; margin:0; padding:0; background:transparent; }")
        self.build_form(form_box)
        align_form_right(form_box)
        root.addWidget(form_box)
        root.addLayout(self._build_details_row(), 1)
        root.addWidget(self._build_add_card())
        hidden = self._build_list_box()          # الـ Base محتاجها (البحث F1 بنافذة لوحدها) — مابتبانش
        hidden.setParent(self)
        hidden.hide()

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout, QHBoxLayout]:
        card = QFrame()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 10)
        layout.setSpacing(6)
        head = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("sectionTitle")
        head.addWidget(label)
        head.addStretch(1)
        layout.addLayout(head)
        return card, layout, head

    def _build_title(self) -> QWidget:
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(4, 0, 4, 0)
        left = QVBoxLayout()
        left.setSpacing(0)
        top = QHBoxLayout()
        title = QLabel(f"{self.ICON}  {self.TITLE}")
        title.setObjectName("titleLabel")
        top.addWidget(title)
        self.status_badge = QLabel("")
        top.addWidget(self.status_badge)
        top.addStretch(1)
        left.addLayout(top)
        sub = QLabel("تسجيل فواتير الموردين — البضاعة بتدخل المخزن والقيد بيتعمل لوحده")
        sub.setObjectName("subtitleLabel")
        left.addWidget(sub)
        row.addLayout(left, 1)
        user = (SESSION.user or {}).get("full_name") or SESSION.username or ""
        meta = QLabel(f"المستخدم: {user}\nالتاريخ: {dt.date.today():%d-%m-%Y}")
        meta.setObjectName("metaLabel")
        meta.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        row.addWidget(meta)
        return box

    def _tb(self, text: str, kind: str, slot, tip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setFixedHeight(34)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(button_qss(kind))
        font = QFont(button.font())
        font.setPixelSize(12)                    # نفس مقاس الـ QSS عشان العرض يتحسب صح ومايتقصّش
        font.setBold(True)
        button.setFont(font)
        button.setMinimumWidth(QFontMetrics(font).horizontalAdvance(text) + 20)
        if tip:
            button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def _build_toolbar(self) -> QFrame:
        """نفس أسماء أزرار الـ Base (الأوضاع والصلاحيات من هناك) — بألوان وأيقونات الفاتورة السعودية."""
        bar = QFrame()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.find_button = self._tb("🔍", "green", self.open_lookup, "بحث عن فاتورة (F1)")
        self.find_button.setMinimumWidth(40)
        self.new_button = self._tb("＋ جديد", "green", self.new_record)
        self.dup_button = self._tb("⧉ تكرار الفاتورة", "green", self.duplicate_invoice,
                                   "فاتورة جديدة بنفس المورد والأصناف")
        self.save_button = self._tb("💾 حفظ", "green", self.save, "حفظ مسودة")
        self.edit_button = self._tb("✎ تعديل", "blue", self.edit_record)
        self.refresh_button = self._tb("⟳ تحديث", "blue", self.refresh_screen)
        self.post_button = self._tb("✔✔ ترحيل", "blue", self.post_invoice,
                                    "البضاعة تدخل المخزن والقيد يتعمل (وانت بتعدّل = حفظ + ترحيل)")
        self.unpost_button = self._tb("↩ إلغاء الترحيل", "blue", self.unpost_invoice)
        self.delete_button = self._tb("🗑 حذف", "red", self.delete)
        self.cancel_button = self._tb("✕ إلغاء", "white", self.cancel)
        self.export_button = self._tb("⇩ تصدير", "gray", self.export_excel, "أصناف الفاتورة المفتوحة لإكسل")
        self.exit_button = self._tb("⇥ خروج", "gray", self.close)
        shown = [(self.find_button, True), (self.new_button, self.has_action("create")),
                 (self.dup_button, self.has_action("create")), (self.save_button, self.has_action("save")),
                 (self.edit_button, self.has_action("edit")), (self.refresh_button, True),
                 (self.post_button, self.has_action("post")), (self.unpost_button, self.has_action("unpost")),
                 (self.delete_button, self.has_action("delete")),
                 (self.cancel_button, self.has_action("create") or self.has_action("edit")),
                 (self.export_button, self.has_action("export")), (self.exit_button, True)]
        for button, visible in shown:
            layout.addWidget(button)
            button.setVisible(visible)
        layout.addSpacing(8)
        show = QLabel("إظهار:")
        show.setObjectName("fieldLabel")
        layout.addWidget(show)
        self.show_wh = QCheckBox("المخزن")
        self.show_unit = QCheckBox("الوحدة")
        self.show_disc = QCheckBox("الخصم")
        for box, tip in ((self.show_wh, "إظهار عمود المخزن"), (self.show_unit, "إظهار عمود الوحدة"),
                         (self.show_disc, "إظهار الخصم")):
            box.setToolTip(tip)
            box.setChecked(True)
            box.toggled.connect(self._apply_columns)
            layout.addWidget(box)
        layout.addStretch(1)
        self.mode_badge = QLabel(MODE_NAMES["view"])
        self.mode_badge.setAlignment(Qt.AlignCenter)
        self.mode_badge.setMinimumWidth(54)
        self.mode_badge.setStyleSheet("background:#E7F6EE; color:#087443; border:1px solid #BFE6CF; "
                                      "border-radius:8px; padding:5px 8px; font-weight:900; font-size:12px;")
        layout.addWidget(self.mode_badge)
        return bar

    def _field(self, label: str, widget: QWidget, button=None) -> QWidget:
        box = QWidget()
        lay = QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        lay.addWidget(caption)
        if button is None:
            lay.addWidget(widget)
        else:
            row = QHBoxLayout()
            row.setSpacing(4)
            row.addWidget(widget, 1)
            row.addWidget(button)
            lay.addLayout(row)
        return box

    def _lookup_button(self, slot) -> QPushButton:
        button = QPushButton("🔍")
        button.setObjectName("lookupButton")
        button.setFixedSize(30, 28)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def build_form(self, box: QGroupBox) -> None:
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        card, layout, head = self._card("📅 بيانات الفاتورة")
        outer.addWidget(card)
        self.tab_group = QButtonGroup(self)
        self.tab_group.setExclusive(True)
        self.pages = QStackedWidget()
        for n, title in enumerate(("الأساسية", "الدفع والعملة", "إضافية")):
            chip = QPushButton(title)
            chip.setObjectName("tabChip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setChecked(n == 0)
            self.tab_group.addButton(chip, n)
            head.insertWidget(head.count() - 1, chip)
        self.tab_group.idClicked.connect(self.pages.setCurrentIndex)

        # --- الأساسية
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (PUR-2026-00001) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = _date_edit()
        self.date_edit.dateChanged.connect(self._on_date)
        self.supplier_edit = LookupEdit("اكتب كود أو اسم المورد…")
        self.supplier_edit.value_changed.connect(self._on_supplier)
        self.supplier_button = self._lookup_button(self._find_supplier)
        self.tax_no_edit = QLineEdit()
        self.tax_no_edit.setReadOnly(True)
        self.sinv_edit = QLineEdit()
        self.sinv_edit.setPlaceholderText("رقمها عند المورد")
        self.sinv_edit.textChanged.connect(self._changed)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("فاتورة", "invoice")
        self.kind_combo.addItem("بيان", "statement")
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self.wh_combo = QComboBox()
        self.wh_combo.setToolTip("المخزن اللي الأصناف بتنزل فيه (وكل سطر يتغيّر) — الوهمي مالوش مشتريات")
        self.wh_combo.currentIndexChanged.connect(self._on_header_wh)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._changed)
        page1 = self._grid([self._field("رقم الفاتورة", self.number_edit), self._field("التاريخ", self.date_edit),
                            self._field("اسم المورد", self.supplier_edit, self.supplier_button),
                            self._field("الرقم الضريبي للمورد", self.tax_no_edit),
                            self._field("رقم فاتورة المورد", self.sinv_edit), self._field("النوع", self.kind_combo),
                            self._field("المخزن الافتراضي", self.wh_combo),
                            self._field("أمر التوريد", self.project_combo)])
        # --- الدفع والعملة
        self.pay_combo = QComboBox()
        self.pay_combo.addItem("آجل", "credit")
        self.pay_combo.addItem("نقدي", "cash")
        self.pay_combo.currentIndexChanged.connect(self._on_pay)
        self.due_edit = _date_edit()
        self.due_edit.dateChanged.connect(self._changed)
        self.source_combo = QComboBox()
        self.source_combo.setToolTip("«نقدي» = قيد صرف لوحده من الخزنة / البنك بيترحّل مع الفاتورة")
        self.source_combo.currentIndexChanged.connect(self._changed)
        self.due_box = self._field("تاريخ الاستحقاق", self.due_edit)
        self.source_box = self._field("الدفع من", self.source_combo)
        self.currency_combo = QComboBox()
        self.currency_combo.setToolTip("عملة واحدة للفاتورة كلها (بسعرها بتاريخ الفاتورة) — أو «متعدد العملات»")
        self.currency_combo.currentIndexChanged.connect(self._on_currency)
        self.rate_edit = QLineEdit()
        self.rate_edit.setPlaceholderText("سعر الصرف")
        self.rate_edit.setToolTip("1 من العملة = كام جنيه (من «أسعار الصرف» بتاريخ الفاتورة ويتعدّل)")
        self.rate_edit.textChanged.connect(self._changed)
        cur_row = QWidget()
        cur_lay = QHBoxLayout(cur_row)
        cur_lay.setContentsMargins(0, 0, 0, 0)
        cur_lay.setSpacing(4)
        cur_lay.addWidget(self.currency_combo, 2)
        cur_lay.addWidget(self.rate_edit, 1)
        self.incl_combo = QComboBox()
        self.incl_combo.addItem("غير شاملة الضريبة", False)
        self.incl_combo.addItem("شاملة الضريبة", True)
        self.incl_combo.currentIndexChanged.connect(self._changed)
        page2 = self._grid([self._field("نوع الدفع", self.pay_combo), self.due_box, self.source_box,
                            self._field("العملة وسعر الصرف", cur_row), self._field("الأسعار", self.incl_combo)])
        # --- إضافية
        self.einv_edit = QLineEdit()
        self.einv_edit.setPlaceholderText("اختياري")
        self.einv_edit.textChanged.connect(self._changed)
        self.note_edit = QLineEdit()
        self.note_edit.textChanged.connect(self._changed)
        page3 = self._grid([self._field("الرقم الإلكتروني", self.einv_edit), self._field("ملاحظات", self.note_edit)])
        for page in (page1, page2, page3):
            self.pages.addWidget(page)
        layout.addWidget(self.pages)
        self._fill_header_choices()

    @staticmethod
    def _grid(fields: list[QWidget]) -> QWidget:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for n, field in enumerate(fields):
            grid.addWidget(field, n // 4, n % 4)
        for c in range(4):
            grid.setColumnStretch(c, 1)
        return page

    def _build_details_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        # الإجماليات (340 على اليمين)
        totals, tl, _h = self._card("◔ الإجماليات")
        totals.setFixedWidth(340)
        totals.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.pre_value = self._summary(tl, "إجمالي الفاتورة قبل الضريبة")
        self.disc_pct_edit = QLineEdit("0.00")
        self.disc_pct_edit.setMaximumWidth(90)
        self.disc_pct_edit.editingFinished.connect(self._on_disc_pct)
        self._summary(tl, "نسبة خصم الفاتورة %", self.disc_pct_edit)
        self.disc_amount_edit = QLineEdit("0.00")
        self.disc_amount_edit.setMaximumWidth(130)
        self.disc_amount_edit.setToolTip("خصم الفاتورة بيتوزّع على الأصناف بالقيمة ويقلّل تكلفتها")
        self.disc_amount_edit.editingFinished.connect(self._on_disc_amount)
        self._summary(tl, "قيمة الخصم", self.disc_amount_edit)
        self.after_value = self._summary(tl, "الإجمالي بعد الخصم")
        self.vat_value = self._summary(tl, "قيمة الضريبة")
        net = QFrame()
        net.setObjectName("netBox")
        nl = QHBoxLayout(net)
        nl.setContentsMargins(12, 6, 12, 6)
        self.net_caption = QLabel("الإجمالي النهائي شامل الضريبة")
        self.net_caption.setObjectName("netLabel")
        self.net_caption.setWordWrap(True)
        self.net_value = QLabel("0.00")
        self.net_value.setObjectName("netValue")
        self.net_value.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        nl.addWidget(self.net_caption, 1)
        nl.addWidget(self.net_value)
        tl.addWidget(net)
        self.egp_box = QFrame()
        self.egp_box.setObjectName("egpBox")
        el = QVBoxLayout(self.egp_box)
        el.setContentsMargins(12, 5, 12, 5)
        el.setSpacing(1)
        self.egp_label = QLabel("")
        self.egp_label.setWordWrap(True)
        self.egp_label.setStyleSheet("color:#1D4ED8; font-weight:800;")
        el.addWidget(self.egp_label)
        tl.addWidget(self.egp_box)
        self.review_label = QLabel("")
        self.review_label.setWordWrap(True)
        self.review_label.setStyleSheet("font-weight:800; padding:2px 4px;")
        tl.addWidget(self.review_label)
        tl.addStretch(1)
        row.addWidget(totals)
        # التفاصيل
        details, dl, dh = self._card("☰ تفاصيل الفاتورة")
        self.lines_hint = QLabel("")
        self.lines_hint.setObjectName("infoLabel")
        dh.addWidget(self.lines_hint)
        self.lines_table = QTableWidget(0, len(HEADERS))
        self.lines_table.setHorizontalHeaderLabels(HEADERS)
        self.lines_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.lines_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.lines_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.lines_table.setAlternatingRowColors(True)
        self.lines_table.verticalHeader().setVisible(False)
        self.lines_table.verticalHeader().setDefaultSectionSize(30)
        header = self.lines_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(C_NAME, QHeaderView.Stretch)
        header.setMinimumSectionSize(60)
        self.lines_table.setMinimumHeight(140)
        self.lines_table.cellClicked.connect(self._on_row)
        dl.addWidget(self.lines_table, 1)
        footer = QFrame()
        footer.setObjectName("tableFooter")
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(14, 4, 14, 4)
        cap = QLabel("إجمالي الأصناف قبل الضريبة")
        cap.setStyleSheet("color:#0B3B23; font-weight:800;")
        self.items_total_label = QLabel("0.00")
        self.items_total_label.setObjectName("sumValue")
        fl.addWidget(cap)
        fl.addStretch(1)
        fl.addWidget(self.items_total_label)
        dl.addWidget(footer)
        row.addWidget(details, 1)
        return row

    def _summary(self, layout: QVBoxLayout, label: str, editor: QLineEdit | None = None) -> QLabel | QLineEdit:
        frame = QFrame()
        frame.setObjectName("summaryRow")
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 3, 10, 3)
        h.addWidget(QLabel(label), 1)
        if editor is None:
            editor = QLabel("0.00")
            editor.setObjectName("sumValue")
        editor.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        h.addWidget(editor)
        layout.addWidget(frame)
        return editor

    def _build_add_card(self) -> QFrame:
        card, layout, _h = self._card("🏷 إضافة صنف")
        self.add_title = card.findChild(QLabel, "sectionTitle")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.item_edit = LookupEdit("اكتب كود أو اسم الصنف…")
        self.item_edit.value_changed.connect(self._on_item)
        self.item_button = self._lookup_button(self._find_item)
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText("ITM-0001")
        self.code_edit.setMaximumWidth(100)
        self.code_edit.editingFinished.connect(self._on_code)
        self.add_wh = QComboBox()
        self.add_wh.currentIndexChanged.connect(self._update_info)
        self.add_unit = QComboBox()
        self.add_unit.currentIndexChanged.connect(self._on_unit)
        self.add_qty = QLineEdit("1")
        self.add_price = QLineEdit()
        self.add_disc = QLineEdit()
        self.add_disc.setPlaceholderText("0")
        for edit in (self.add_qty, self.add_price, self.add_disc):
            edit.setMaximumWidth(95)
            edit.returnPressed.connect(self.add_line)
        self.add_tax = QComboBox()
        self.add_cur = QComboBox()
        self.add_cur.currentIndexChanged.connect(self._on_line_currency)
        self.add_rate = QLineEdit()
        self.add_rate.setMaximumWidth(80)
        self.add_rate.setPlaceholderText("السعر")
        self.add_button = QPushButton("＋ إضافة إلى الفاتورة")
        self.add_button.setStyleSheet(button_qss("green"))
        self.add_button.setFixedHeight(34)
        self.add_button.clicked.connect(self.add_line)
        self.clear_button = QPushButton("✎ تفريغ")
        self.clear_button.setStyleSheet(button_qss("white"))
        self.clear_button.setFixedHeight(34)
        self.clear_button.clicked.connect(self.clear_entry)
        self.remove_button = QPushButton("🗑 حذف الصنف")
        self.remove_button.setStyleSheet(button_qss("white") + "QPushButton { color:#DC2626; }")
        self.remove_button.setFixedHeight(34)
        self.remove_button.clicked.connect(self.remove_line)
        item_box = self._field("الصنف", self.item_edit, self.item_button)
        item_box.setMinimumWidth(240)
        row.addWidget(item_box, 3)
        row.addWidget(self._field("رقم الصنف", self.code_edit))
        self.add_wh_box = self._field("المخزن", self.add_wh)
        row.addWidget(self.add_wh_box, 1)
        self.add_unit_box = self._field("الوحدة", self.add_unit)
        row.addWidget(self.add_unit_box, 1)
        row.addWidget(self._field("الكمية", self.add_qty))
        row.addWidget(self._field("السعر", self.add_price))
        self.add_disc_box = self._field("الخصم %", self.add_disc)
        row.addWidget(self.add_disc_box)
        row.addWidget(self._field("الضريبة", self.add_tax), 1)
        self.add_cur_box = QWidget()
        cl = QHBoxLayout(self.add_cur_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(4)
        cl.addWidget(self._field("العملة", self.add_cur))
        cl.addWidget(self._field("سعر الصرف", self.add_rate))
        row.addWidget(self.add_cur_box)
        buttons = QVBoxLayout()
        buttons.addStretch(1)
        brow = QHBoxLayout()
        brow.setSpacing(6)
        brow.addWidget(self.add_button)
        brow.addWidget(self.clear_button)
        brow.addWidget(self.remove_button)
        buttons.addLayout(brow)
        row.addLayout(buttons)
        layout.addLayout(row)
        self.info_label = QLabel("")
        self.info_label.setObjectName("infoLabel")
        layout.addWidget(self.info_label)
        self._fill_add_choices()
        return card

    # ---- الاختيارات ---------------------------------------------------------
    def _fill_header_choices(self) -> None:
        self.supplier_edit.set_items([(int(s["id"]), s["code"], f"{s['code']} — {s['name_ar']}")
                                      for s in self._choices["suppliers"] if s["is_active"]])
        self.wh_combo.blockSignals(True)
        self.wh_combo.clear()
        for w in self._choices["warehouses"]:
            if w["is_active"] and not w["is_virtual"]:
                self.wh_combo.addItem(f"{w['code']} — {w['name_ar']}", int(w["id"]))
        self.wh_combo.blockSignals(False)
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        self.project_combo.addItem("— من غير —", None)
        for p in self._choices["projects"]:
            if p["status"] == "open":
                self.project_combo.addItem(f"{p['code']} · {p['name']}", int(p["id"]))
        self.project_combo.blockSignals(False)
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        for t in self._choices["treasuries"]:
            if t["is_active"] and t["is_base_currency"]:
                self.source_combo.addItem(f"💵 {t['name_ar']}", f"treasury:{int(t['id'])}")
        for b in self._choices["banks"]:
            if b["is_active"] and b["is_base_currency"]:
                self.source_combo.addItem(f"🏦 {b['bank_name']} — {b['code']}", f"bank:{int(b['id'])}")
        self.source_combo.blockSignals(False)
        self.currency_combo.blockSignals(True)
        self.currency_combo.clear()
        for c in self._choices["currencies"]:
            if c["is_active"]:
                self.currency_combo.addItem(f"{c['code']} — {c['name_ar']}", c["code"])
        self.currency_combo.addItem("🌐 متعدد العملات (كل صنف بعملته)", MULTI)
        self.currency_combo.blockSignals(False)

    def _fill_add_choices(self) -> None:
        self.item_edit.set_items([(int(i["id"]), i["code"], f"{i['code']} — {i['name']}")
                                  for i in self._choices["items"] if i["is_active"]])
        self.add_wh.blockSignals(True)
        self.add_wh.clear()
        for w in self._choices["warehouses"]:
            if w["is_active"] and not w["is_virtual"]:
                self.add_wh.addItem(w["name_ar"], int(w["id"]))
        self.add_wh.blockSignals(False)
        self.add_tax.clear()
        self.add_tax.addItem("— من غير —", None)
        for t in self._choices["tax_codes"]:
            if t["is_active"]:
                self.add_tax.addItem(t["name_ar"], int(t["id"]))
        self.add_cur.blockSignals(True)
        self.add_cur.clear()
        for c in self._choices["currencies"]:
            if c["is_active"]:
                self.add_cur.addItem(c["code"], c["code"])
        self.add_cur.blockSignals(False)

    @staticmethod
    def _set_combo(combo: QComboBox, value: Any, text: str | None = None) -> None:
        index = combo.findData(value)
        if index < 0 and value is not None and text:
            combo.addItem(f"{text} (موقوف)", value)
            index = combo.count() - 1
        combo.setCurrentIndex(max(index, 0) if combo.count() else -1)

    # ---- الأحداث (الرأس) -----------------------------------------------------
    def _editing(self) -> bool:
        return self.mode in EDITING and not self._filling

    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _mode(self) -> str:
        return "multi" if self.currency_combo.currentData() == MULTI else "single"

    def _currency(self) -> str:
        data = self.currency_combo.currentData()
        return self._base if data in (None, MULTI) else str(data)

    def _on_supplier(self, supplier_id: Any) -> None:
        sup = next((s for s in self._choices["suppliers"] if s["id"] == supplier_id), None)
        self.tax_no_edit.setText((sup or {}).get("tax_reg_no") or ("—" if sup else ""))
        self._terms = int((sup or {}).get("payment_terms_days") or 0)
        self._supplier_tax = None
        if sup and sup.get("vat_tax_code_id") and Decimal(sup.get("vat_rate") or 0) == 0:
            self._supplier_tax = int(sup["vat_tax_code_id"])
        if self._editing() and sup:
            if self._mode() == "single":
                self._set_combo(self.currency_combo, sup["currency_code"])
            self.due_edit.setDate(self.date_edit.date().addDays(self._terms))
        self._changed()

    def _on_date(self, *_args: Any) -> None:
        if self._editing():
            self.due_edit.setDate(self.date_edit.date().addDays(self._terms))
            if self._mode() == "single" and self._currency() != self._base:
                self._refresh_rate()
        self._changed()

    def _refresh_rate(self) -> None:
        rate = self.service.rate_on(self._currency(), _pydate(self.date_edit.date()))
        self.rate_edit.setText(num(rate) if rate else "")

    def _on_currency(self, *_args: Any) -> None:
        multi = self._mode() == "multi"
        base = self._currency() == self._base
        self.rate_edit.setEnabled(self.mode in EDITING and not multi and not base)
        if self._editing():
            if multi or base:
                self.rate_edit.setText("" if multi else "1")
            else:
                self._refresh_rate()
            if multi:
                self._discount = Decimal("0")
        self._apply_multi()
        self._changed()

    def _apply_multi(self) -> None:
        multi = self._mode() == "multi"
        self.add_cur_box.setVisible(multi)
        self.lines_table.setColumnHidden(C_CUR, not multi)
        editing = self.mode in EDITING
        for edit in (self.disc_pct_edit, self.disc_amount_edit):
            edit.setEnabled(editing and not multi)
            edit.setToolTip("«متعدد العملات»: الخصم على السطر بس" if multi else "")

    def _on_kind(self, *_args: Any) -> None:
        statement = self.kind_combo.currentData() == "statement"
        self.add_tax.setEnabled(self.mode in EDITING and not statement)
        self.incl_combo.setEnabled(self.mode in EDITING and not statement)
        self._update_info()
        self._changed()

    def _on_pay(self, *_args: Any) -> None:
        cash = self.pay_combo.currentData() == "cash"
        self.due_box.setVisible(not cash)
        self.source_box.setVisible(cash)
        self._changed()

    def _on_header_wh(self, *_args: Any) -> None:
        if self._editing() and self._sel < 0:
            self._set_combo(self.add_wh, self.wh_combo.currentData())
        self._changed()

    def _on_disc_pct(self) -> None:
        if not self._editing():
            return
        base = (self._review or {}).get("calc") or {}
        pct = _dec(self.disc_pct_edit.text())
        self._discount = (Decimal(base.get("items_total") or 0) * pct / 100).quantize(Decimal("0.01"))
        self._refresh_review()

    def _on_disc_amount(self) -> None:
        if not self._editing():
            return
        self._discount = _dec(self.disc_amount_edit.text()).quantize(Decimal("0.01"))
        self._refresh_review()

    # ---- «إضافة صنف» -------------------------------------------------------
    def _units(self, item_id: Any) -> list[tuple[int, str, Decimal]]:
        return self._choices["units"].get(int(item_id), []) if item_id else []

    def _on_item(self, item_id: Any) -> None:
        item = self._items.get(int(item_id)) if item_id else None
        self.code_edit.setText(item["code"] if item else "")
        self.add_unit.blockSignals(True)
        self.add_unit.clear()
        for unit_id, name, factor in self._units(item_id):
            self.add_unit.addItem(name if factor == 1 else f"{name} ({fmt_qty(factor)})", unit_id)
        self.add_unit.blockSignals(False)
        service = bool(item and item["is_service"])
        self.add_wh.setEnabled(self.mode in EDITING and not service)
        if item and self._sel < 0 and self.mode in EDITING:
            wh = item.get("default_warehouse_id")
            if not wh or wh not in self._whs or self._whs[wh]["is_virtual"]:
                wh = self.wh_combo.currentData()
            self._set_combo(self.add_wh, wh)
            self._set_combo(self.add_tax, self._supplier_tax or item.get("tax_code_id"))
            self._prefill_price()
        self._update_info()

    def _on_code(self) -> None:
        item_id = self._by_code.get(self.code_edit.text().strip().upper())
        if item_id and item_id != self.item_edit.currentData():
            self.item_edit.set_value(item_id)

    def _on_unit(self, *_args: Any) -> None:
        if self.mode in EDITING and self._sel < 0:
            self._prefill_price()
        self._update_info()

    def _on_line_currency(self, *_args: Any) -> None:
        code = self.add_cur.currentData()
        if not code:
            return
        if code == self._base:
            self.add_rate.setText("1")
            self.add_rate.setEnabled(False)
        else:
            self.add_rate.setEnabled(self.mode in EDITING)
            rate = self.service.rate_on(code, _pydate(self.date_edit.date()))
            self.add_rate.setText(num(rate) if rate else "")

    def _factor(self) -> Decimal:
        unit_id = self.add_unit.currentData()
        return next((f for u, _n, f in self._units(self.item_edit.currentData()) if u == unit_id), Decimal("1"))

    def _entry_rate(self) -> Decimal:
        if self._mode() == "multi":
            return Decimal("1") if self.add_cur.currentData() == self._base else _dec(self.add_rate.text())
        return Decimal("1") if self._currency() == self._base else _dec(self.rate_edit.text())

    def _info(self) -> dict[str, Any] | None:
        item_id = self.item_edit.currentData()
        if not item_id:
            return None
        try:
            return self.service.item_info(int(item_id), self.add_wh.currentData(),
                                          self.kind_combo.currentData() or "invoice", exclude_id=self.current_id)
        except Exception:  # noqa: BLE001
            return None

    def _prefill_price(self) -> None:
        info = self._info()
        rate = self._entry_rate()
        if info and info.get("last_cost") and rate > 0:
            self.add_price.setText(num((info["last_cost"] * self._factor() / rate).quantize(Decimal("0.01"))))

    def _update_info(self, *_args: Any) -> None:
        item = self._items.get(int(self.item_edit.currentData())) if self.item_edit.currentData() else None
        if item is None:
            self.info_label.setText("اختار الصنف — Enter في الكمية أو السعر = إضافة")
            return
        parts: list[str] = []
        if item["is_service"]:
            parts.append("خدمي — مالوش مخزون (مصروف على «تكلفة» نوع الصنف)")
        else:
            info = self._info() or {}
            if info.get("available") is not None:
                parts.append(f"المتاح في «{self.add_wh.currentText()}»: {fmt_qty(info['available'])} {item['unit_name']}")
            if info.get("last_cost"):
                parts.append(f"آخر سعر شراء: {money(info['last_cost'])} ج / {item['unit_name']}")
            parts.append(f"المتوسط: {money(info['avg'])}" if info.get("avg") else "مالوش متوسط")
        factor = self._factor()
        parts.append(f"الوحدة الأساسية: {item['unit_name']}" +
                     (f" · «{self.add_unit.currentText()}» = {fmt_qty(factor)} {item['unit_name']}" if factor != 1 else ""))
        parts.append("Enter في الكمية أو السعر = إضافة")
        self.info_label.setText("   ·   ".join(parts))

    def _entry(self) -> dict[str, Any]:
        item_id = self.item_edit.currentData()
        if not item_id:
            raise BusinessError("اختار الصنف.")
        item = self._items[int(item_id)]
        qty = _dec(self.add_qty.text())
        if qty <= 0:
            raise BusinessError("اكتب كمية أكبر من صفر.")
        price = _dec(self.add_price.text())
        if price < 0:
            raise BusinessError("السعر مايبقاش بالسالب.")
        line = {"item_id": int(item_id), "warehouse_id": None if item["is_service"] else self.add_wh.currentData(),
                "unit_id": self.add_unit.currentData() or int(item["unit_id"]), "qty": qty, "unit_price": price,
                "discount_pct": _dec(self.add_disc.text()),
                "tax_code_id": None if self.kind_combo.currentData() == "statement" else self.add_tax.currentData(),
                "currency_code": self.add_cur.currentData() if self._mode() == "multi" else self._currency(),
                "fx_rate": self._entry_rate()}
        if not item["is_service"] and not line["warehouse_id"]:
            raise BusinessError("اختار المخزن.")
        return line

    def add_line(self) -> None:
        if self.mode not in EDITING:
            return
        try:
            line = self._entry()
        except BusinessError as exc:
            messages.warn(self, "إضافة صنف", str(exc))
            return
        if 0 <= self._sel < len(self._lines):
            self._lines[self._sel] = line
        else:
            same = next((ln for ln in self._lines if all(ln.get(k) == line.get(k) for k in (
                "item_id", "warehouse_id", "unit_id", "unit_price", "discount_pct", "tax_code_id", "currency_code",
                "fx_rate"))), None)
            if same is not None:
                same["qty"] = _dec(same["qty"]) + line["qty"]
            else:
                self._lines.append(line)
        self.clear_entry()
        self._show_lines()
        self._refresh_review()
        self.item_edit.setFocus()

    def clear_entry(self) -> None:
        self._sel = -1
        self.lines_table.clearSelection()
        keep_wh = self.wh_combo.currentData()
        self.item_edit.set_value(None)
        self.code_edit.clear()
        self.add_qty.setText("1")
        self.add_price.clear()
        self.add_disc.clear()
        self._set_combo(self.add_wh, keep_wh)
        self._set_combo(self.add_cur, self._base)
        self._entry_buttons()
        self._update_info()

    def remove_line(self) -> None:
        if self.mode not in EDITING or not 0 <= self._sel < len(self._lines):
            return
        del self._lines[self._sel]
        self.clear_entry()
        self._show_lines()
        self._refresh_review()

    def _on_row(self, row: int, _col: int = 0) -> None:
        if not 0 <= row < len(self._lines):
            return
        self._sel = row
        ln = self._lines[row]
        self.item_edit.set_value(ln["item_id"])
        self._set_combo(self.add_unit, ln["unit_id"])
        self._set_combo(self.add_wh, ln.get("warehouse_id") or self.wh_combo.currentData())
        self.add_qty.setText(num(ln["qty"]))
        self.add_price.setText(num(ln["unit_price"]))
        self.add_disc.setText(num(ln.get("discount_pct")) if _dec(ln.get("discount_pct")) else "")
        self._set_combo(self.add_tax, ln.get("tax_code_id"))
        if self._mode() == "multi":
            self._set_combo(self.add_cur, ln.get("currency_code"))
            self.add_rate.setText(num(ln.get("fx_rate")))
        self._entry_buttons()
        self._update_info()

    def _entry_buttons(self) -> None:
        editing = self.mode in EDITING
        updating = 0 <= self._sel < len(self._lines)
        self.add_button.setText("✎ تحديث السطر" if updating else "＋ إضافة إلى الفاتورة")
        self.add_button.setStyleSheet(button_qss("blue" if updating else "green"))
        self.clear_button.setText("＋ سطر جديد" if updating else "✎ تفريغ")
        self.add_button.setEnabled(editing)
        self.clear_button.setEnabled(editing)
        self.remove_button.setEnabled(editing and updating)
        if self.add_title is not None:
            self.add_title.setText(f"🏷 تعديل السطر {self._sel + 1}" if updating else "🏷 إضافة صنف")

    # ---- الجدول والإجماليات ----------------------------------------------------
    def _apply_columns(self, *_args: Any) -> None:
        self.lines_table.setColumnHidden(C_WH, not self.show_wh.isChecked())
        self.lines_table.setColumnHidden(C_UNIT, not self.show_unit.isChecked())
        self.lines_table.setColumnHidden(C_DISC, not self.show_disc.isChecked())
        self.add_wh_box.setVisible(self.show_wh.isChecked())
        self.add_unit_box.setVisible(self.show_unit.isChecked())
        self.add_disc_box.setVisible(self.show_disc.isChecked())

    def _show_lines(self) -> None:
        calc = {c["line"]: c for c in ((self._review or {}).get("calc") or {}).get("lines") or []}
        marks = (self._review or {}).get("lines") or {}
        statement = self.kind_combo.currentData() == "statement"
        t = self.lines_table
        t.setRowCount(len(self._lines))
        for r, ln in enumerate(self._lines):
            item = self._items.get(int(ln["item_id"])) or {"code": "", "name": "", "is_service": False}
            c = calc.get(r + 1)
            unit = next((n for u, n, _f in self._units(ln["item_id"]) if u == ln["unit_id"]), "")
            wh = "—" if item["is_service"] else (self._whs.get(ln.get("warehouse_id") or 0) or {}).get("name_ar", "")
            tax = self._taxes.get(ln.get("tax_code_id") or 0)
            name = item["name"] + (" (خدمي)" if item["is_service"] else "")
            mark = marks.get(r + 1)
            t.setItem(r, C_CODE, _cell(item["code"]))
            t.setItem(r, C_NAME, _cell(name + (f"\n{'✖' if mark[0] == 'no' else '⚠'} {mark[1]}" if mark else ""),
                                       color=MARK_COLORS.get(mark[0]) if mark else None))
            t.setItem(r, C_WH, _cell(wh))
            t.setItem(r, C_UNIT, _cell(unit))
            t.setItem(r, C_QTY, _cell(fmt_qty(_dec(ln["qty"])), number=True))
            t.setItem(r, C_PRICE, _cell(money(ln["unit_price"]), number=True))
            t.setItem(r, C_DISC, _cell(f"{num(ln.get('discount_pct'))}%" if _dec(ln.get("discount_pct")) else "—"))
            t.setItem(r, C_CUR, _cell(f"{ln.get('currency_code')} × {num(ln.get('fx_rate'))}"))
            pre = (c["gross_amount"] - c["line_discount"]) if c else None
            t.setItem(r, C_PRE, _cell(money(pre), number=True))
            t.setItem(r, C_TAXR, _cell("—" if statement or not tax else f"{num(tax['rate'])}%"))
            t.setItem(r, C_VAT, _cell(money(c["vat_amount"]) if c else "", number=True))
            t.setItem(r, C_TOTAL, _cell(money(c["total_amount"]) if c else "", number=True, bold=True))
            if mark:
                t.setRowHeight(r, 44)
        if self._sel >= 0 and self._sel < len(self._lines):
            t.selectRow(self._sel)
        self.lines_hint.setText(f"{len(self._lines)} صنف · اضغط على سطر عشان تعدّله تحت")

    def _refresh_review(self) -> None:
        self._timer.stop()
        try:
            data = self.collect()
            review = self.service.review(data, self.current_id if self.mode != "new" else None)
        except Exception as exc:  # noqa: BLE001
            review = {"checks": [("no", messages.error_text(exc))], "calc": None, "lines": {}, "ready": False}
        self._review = review
        self._show_totals(review.get("calc"))
        self._show_lines()
        checks = review.get("checks") or []
        problems = [c[1] for c in checks if c[0] == "no"]
        warns = [c[1] for c in checks if c[0] == "warn"]
        if not self._lines:
            text, color = "ضيف الأصناف من «إضافة صنف» تحت", "#64748B"
        elif problems:
            text, color = f"✖ {len(problems)} مشكلة قبل الترحيل — {problems[0]}", "#B91C1C"
        elif self.current_record and self.current_record.get("status") == "posted" and self.mode == "view":
            text, color = "✔ مرحّلة — البضاعة في المخزن والقيد اتعمل", "#087443"
        else:
            text, color = ("✔ جاهزة للترحيل" + (f" · ⚠ {len(warns)} تنبيه" if warns else "")), "#087443"
        self.review_label.setText(text)
        self.review_label.setStyleSheet(f"font-weight:800; padding:2px 4px; color:{color};")
        self.review_label.setToolTip("\n".join(f"{ {'ok': '✔', 'no': '✖', 'warn': '⚠', 'info': 'ℹ'}[c[0]]} {c[1]}"
                                               for c in checks))

    def _show_totals(self, calc: dict[str, Any] | None) -> None:
        calc = calc or {}
        z = Decimal("0")
        multi = self._mode() == "multi"
        cur = self._base if multi else self._currency()
        self.pre_value.setText(money(calc.get("items_total", z)))
        self.items_total_label.setText(money(calc.get("items_total", z)))
        if not self.disc_pct_edit.hasFocus():
            self.disc_pct_edit.setText(money(calc.get("discount_pct", z)).replace(",", ""))
        if not self.disc_amount_edit.hasFocus():
            self.disc_amount_edit.setText(money(calc.get("discount_amount", self._discount)).replace(",", ""))
        self.after_value.setText(money(calc.get("net_amount", z)))
        self.vat_value.setText(money(calc.get("vat_amount", z)))
        self.net_caption.setText(f"الإجمالي النهائي شامل الضريبة ({cur})")
        self.net_value.setText(money(calc.get("total_amount", z)))
        if multi and calc.get("by_currency"):
            rows = [f"{code}: {money(v['total'])} ← {money(v['total_base'])} ج" for code, v in calc["by_currency"].items()]
            self.egp_label.setText("تفصيل العملات:\n" + "\n".join(rows))
            self.egp_box.setVisible(True)
        elif cur != self._base and calc:
            self.egp_label.setText(f"بالجنيه × {fmt_rate(calc.get('rate'))} = {money(calc.get('total_base'))}")
            self.egp_box.setVisible(True)
        else:
            self.egp_box.setVisible(False)

    # ---- الأوضاع -----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.supplier_edit, self.supplier_button, self.sinv_edit, self.kind_combo,
                self.wh_combo, self.project_combo, self.pay_combo, self.due_edit, self.source_combo,
                self.currency_combo, self.incl_combo, self.einv_edit, self.note_edit, self.item_edit,
                self.item_button, self.code_edit, self.add_wh, self.add_unit, self.add_qty, self.add_price,
                self.add_disc, self.add_tax, self.add_cur, self.add_rate]

    def _record_status(self) -> str | None:
        return (self.current_record or {}).get("status") if self.current_id is not None else None

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        status = self._record_status()
        if mode == "view" and status == "posted":
            self.edit_button.setEnabled(False)               # المرحّلة ماتتعدّلش إلا بإلغاء الترحيل
        if mode == "view" and (self.current_record or {}).get("invoice_no"):
            self.delete_button.setEnabled(False)             # خدت رقم ← ماتتمسحش
        self.rate_edit.setEnabled(mode in EDITING and self._mode() == "single" and self._currency() != self._base)
        self._on_kind()
        self._apply_multi()
        self._entry_buttons()
        key = "new" if mode == "new" else (status or "new")
        text, bg, fg = STATUS_STYLE.get(key, STATUS_STYLE["new"])
        self.status_badge.setText(text)
        self.status_badge.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:10px; padding:3px 12px; "
                                        "font-weight:800; font-size:12px;")

    def update_extra_buttons(self) -> None:
        editing = self.mode in EDITING
        status = self._record_status()
        self.find_button.setEnabled(not editing)
        self.dup_button.setEnabled(not editing and self.current_id is not None and self.can("create"))
        self.post_button.setEnabled(self.can("post") and (editing and self.can("save") or status == "draft"))
        self.unpost_button.setEnabled(not editing and status == "posted" and self.can("unpost"))

    # ---- الأزرار -----------------------------------------------------------
    def new_record(self) -> None:
        super().new_record()
        if self.mode == "new":
            self.supplier_edit.setFocus()

    def duplicate_invoice(self) -> None:
        """«تكرار الفاتورة»: فاتورة جديدة بنفس المورد والأصناف (من غير الأرقام) بتاريخ النهارده."""
        if self.current_record is None or not self.can("create"):
            return
        source = dict(self.current_record)
        lines = [dict(ln) for ln in self._lines]
        super().new_record()
        self._filling = True
        try:
            self._fill_header(source)
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.due_edit.setDate(self.date_edit.date().addDays(self._terms))
            self.sinv_edit.clear()
            self.einv_edit.clear()
            self.number_edit.setText("بيتولّد مع الترحيل")
            self._lines = lines
            self._discount = Decimal(source.get("discount_amount") or 0)
        finally:
            self._filling = False
        self.set_mode("new")
        self._refresh_review()

    def _problems(self) -> str:
        review = self._review or {}
        bad = [c[1] for c in review.get("checks") or [] if c[0] == "no"]
        return "\n".join(f"✖ {b}" for b in bad)

    def post_invoice(self) -> None:
        if not self.post_button.isEnabled():
            return
        self._refresh_review()
        problems = self._problems()
        if problems:
            messages.warn(self, "الترحيل", f"الفاتورة فيها مشاكل لازم تتصلّح قبل الترحيل:\n{problems}")
            return
        total = ((self._review or {}).get("calc") or {}).get("total_base")
        if not messages.ask_yes_no(self, "ترحيل الفاتورة",
                                   f"ترحيل الفاتورة؟ البضاعة هتدخل المخزن والقيد هيتعمل ({money(total)} ج)."):
            return
        try:
            if self.mode in EDITING:
                saved = self.service.save_draft(None if self.mode == "new" else self.current_id, self.collect(),
                                                expected_version=None if self.mode == "new" else self.current_version,
                                                post=True)
                self.current_id = saved
                self.mode = "view"
            else:
                self.service.post(self.current_id, expected_version=self.current_version)
        except ConcurrentEditError as exc:
            if self.mode in EDITING:
                self.on_conflict(exc)
            else:
                self.report_error("تعذّر الترحيل", exc)
            return
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر الترحيل", exc)
            return
        self.load(self.current_id)
        messages.info(self, "الترحيل", f"اترحّلت الفاتورة {invoice_label(self.current_record or {})} ✔")

    def unpost_invoice(self) -> None:
        if not self.unpost_button.isEnabled():
            return
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   "إلغاء ترحيل الفاتورة؟ البضاعة هتطلع من المخزن والقيد هيتمسح "
                                   "(والفاتورة هترجع مسودة برقمها)."):
            return
        try:
            self.service.unpost(self.current_id, expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self.load(self.current_id)

    def refresh_screen(self) -> None:
        self._load_choices()
        self._filling = True
        try:
            self._fill_header_choices()
            self._fill_add_choices()
        finally:
            self._filling = False
        super().refresh_screen()
        if self.current_id is None:
            self._refresh_review()

    def open_lookup(self) -> None:
        if self.mode in EDITING:
            return
        dialog = InvoiceSearchDialog(self.service, self)
        if dialog.exec() and dialog.selected_id is not None:
            self.load(dialog.selected_id)

    def _find_supplier(self) -> None:
        from app.ui.dialogs.lookup_dialog import LookupDialog

        rows = [{"id": s["id"], "code": s["code"], "name_ar": s["name_ar"], "currency_code": s["currency_code"]}
                for s in self._choices["suppliers"] if s["is_active"]]
        dialog = LookupDialog("الموردين", [("code", "الكود"), ("name_ar", "الاسم"), ("currency_code", "العملة")],
                              rows, parent=self)
        if dialog.exec() and dialog.selected_id is not None:
            self.supplier_edit.set_value(dialog.selected_id)

    def _find_item(self) -> None:
        from app.ui.dialogs.lookup_dialog import LookupDialog

        rows = [{"id": i["id"], "code": i["code"], "name": i["name"], "unit_name": i["unit_name"],
                 "kind": "خدمي" if i["is_service"] else "مخزني"} for i in self._choices["items"] if i["is_active"]]
        dialog = LookupDialog("الأصناف", [("code", "الكود"), ("name", "الاسم"), ("unit_name", "الوحدة"),
                                          ("kind", "النوع")], rows, parent=self)
        if dialog.exec() and dialog.selected_id is not None:
            self.item_edit.set_value(dialog.selected_id)

    def export_excel(self) -> None:
        if not self.can("export") or not self._lines:
            return
        path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل",
                                              f"{invoice_label(self.current_record or {'draft_no': ''})}.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        rows = []
        for r in range(self.lines_table.rowCount()):
            rows.append({str(c): (self.lines_table.item(r, c).text() if self.lines_table.item(r, c) else "")
                         for c in range(len(HEADERS))})
        columns = [(str(c), h) for c, h in enumerate(HEADERS) if not self.lines_table.isColumnHidden(c)]
        try:
            export_rows_to_excel(Path(path), self.TITLE, columns, rows, lambda _k, v, _r: v)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", f"اتصدّر {len(rows)} صنف.")

    # ---- الـ Base -------------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return []                                     # مفيش قايمة جانبية في النموذج 9 — البحث بنافذة (F1)

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_invoice(int(record_id))

    def _fill_header(self, rec: dict[str, Any]) -> None:
        self.date_edit.setDate(_qdate(rec["invoice_date"]))
        self.supplier_edit.set_items([(int(s["id"]), s["code"], f"{s['code']} — {s['name_ar']}")
                                      for s in self._choices["suppliers"] if s["is_active"]],
                                     rec["supplier_id"], f"{rec.get('supplier_code')} — {rec.get('supplier_name')}")
        self._on_supplier(rec["supplier_id"])
        self.sinv_edit.setText(rec.get("supplier_invoice_no") or "")
        self._set_combo(self.kind_combo, rec.get("stock_kind") or "invoice")
        self._set_combo(self.wh_combo, rec.get("warehouse_id"), rec.get("warehouse_name"))
        self._set_combo(self.project_combo, rec.get("project_id"), rec.get("project_code"))
        self._set_combo(self.pay_combo, rec.get("pay_kind") or "credit")
        if rec.get("due_date"):
            self.due_edit.setDate(_qdate(rec["due_date"]))
        if rec.get("pay_kind") == "cash":
            source = f"{rec['source_kind']}:{rec['treasury_id'] or rec['bank_account_id']}"
            self._set_combo(self.source_combo, source, rec.get("treasury_name") or rec.get("bank_name"))
        self._set_combo(self.currency_combo, MULTI if rec.get("currency_mode") == "multi" else rec["currency_code"],
                        rec["currency_code"])
        self.rate_edit.setText(num(rec.get("fx_rate")) if rec.get("currency_mode") != "multi" else "")
        self._set_combo(self.incl_combo, bool(rec.get("prices_include_vat")))
        self.einv_edit.setText(rec.get("e_invoice_no") or "")
        self.note_edit.setText(rec.get("description") or "")
        self._on_pay()
        self._apply_multi()

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self._fill_header(record)
            self.number_edit.setText(invoice_label(record))
            self._discount = Decimal(record.get("discount_amount") or 0)
            self._lines = [{"item_id": int(ln["item_id"]), "warehouse_id": ln["warehouse_id"],
                            "unit_id": int(ln["unit_id"]), "qty": Decimal(ln["qty"]),
                            "unit_price": Decimal(ln["unit_price"]), "discount_pct": Decimal(ln["discount_pct"]),
                            "tax_code_id": ln["tax_code_id"], "currency_code": ln["currency_code"],
                            "fx_rate": Decimal(ln["fx_rate"])} for ln in record.get("lines") or []]
            self._sel = -1
            self.clear_entry()
        finally:
            self._filling = False
        self._refresh_review()

    def clear_form(self) -> None:
        self._filling = True
        try:
            today = dt.date.today()
            self.number_edit.setText("بيتولّد مع الترحيل")
            self.date_edit.setDate(_qdate(today))
            self.supplier_edit.set_value(None)
            self.tax_no_edit.clear()
            self._terms, self._supplier_tax = 0, None
            self.sinv_edit.clear()
            self.kind_combo.setCurrentIndex(0)
            default_wh = next((int(w["id"]) for w in self._choices["warehouses"]
                               if w["is_default"] and w["is_active"] and not w["is_virtual"]), None)
            self._set_combo(self.wh_combo, default_wh)
            self.project_combo.setCurrentIndex(0)
            self.pay_combo.setCurrentIndex(0)
            self.due_edit.setDate(_qdate(today))
            if self.source_combo.count():
                self.source_combo.setCurrentIndex(0)
            self._set_combo(self.currency_combo, self._base)
            self.rate_edit.setText("1")
            self.incl_combo.setCurrentIndex(0)
            self.einv_edit.clear()
            self.note_edit.clear()
            self._discount = Decimal("0")
            self._lines = []
            self.clear_entry()
            self._on_pay()
        finally:
            self._filling = False
        self.pages.setCurrentIndex(0)
        self.tab_group.button(0).setChecked(True)
        self._refresh_review()

    def collect(self) -> dict:
        kind, _sep, sid = str(self.source_combo.currentData() or "").partition(":")
        source = (kind or None, int(sid) if sid else None)
        mode = self._mode()
        return {"invoice_date": _pydate(self.date_edit.date()), "supplier_id": self.supplier_edit.currentData(),
                "supplier_invoice_no": self.sinv_edit.text(), "e_invoice_no": self.einv_edit.text(),
                "stock_kind": self.kind_combo.currentData(), "prices_include_vat": bool(self.incl_combo.currentData()),
                "pay_kind": self.pay_combo.currentData(), "due_date": _pydate(self.due_edit.date()),
                "source_kind": source[0], "treasury_id": source[1] if source[0] == "treasury" else None,
                "bank_account_id": source[1] if source[0] == "bank" else None,
                "warehouse_id": self.wh_combo.currentData(), "project_id": self.project_combo.currentData(),
                "currency_mode": mode, "currency_code": self._currency(),
                "fx_rate": self.rate_edit.text() if mode == "single" else "1",
                "discount_amount": self._discount if mode == "single" else 0,
                "description": self.note_edit.text(),
                "lines": [{**ln, "fx_rate": ln["fx_rate"] if mode == "multi" else self.rate_edit.text()}
                          for ln in self._lines]}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(record_id, payload,
                                       expected_version=None if record_id is None else self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_invoice(int(record_id), expected_version=self.current_version)


class InvoiceSearchDialog(QDialog):
    """البحث عن فاتورة (F1): فترة + حالة + كلمة (رقم / مورد / رقم فاتورة المورد / صنف) + صفحات 50 فاتورة."""

    COLUMNS = [("invoice_no", "رقم الفاتورة"), ("invoice_date", "التاريخ"), ("supplier_name", "المورد"),
               ("supplier_invoice_no", "رقم فاتورة المورد"), ("total", "الإجمالي"), ("status", "الحالة")]

    def __init__(self, service: PurchaseInvoiceService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_id: Any = None
        self.page = 1
        self.setWindowTitle("بحث — فواتير الشراء")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(900, 560)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        today = dt.date.today()
        self.date_from, self.date_to = _date_edit(), _date_edit()
        self.date_from.setDate(_qdate(today.replace(month=1, day=1)))
        self.status = QComboBox()
        for text, value in (("الكل", "all"), ("مسودات", "draft"), ("مرحّلة", "posted")):
            self.status.addItem(text, value)
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("🔍 رقم الفاتورة أو المورد أو رقم فاتورة المورد أو الصنف…")
        for w, label in ((self.date_from, "من"), (self.date_to, "إلى"), (self.status, "الحالة")):
            top.addWidget(QLabel(label))
            top.addWidget(w)
        top.addWidget(self.keyword, 1)
        layout.addLayout(top)
        self.table = DataTable(self.COLUMNS, formatter=self._format)
        layout.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.count = QLabel("")
        bottom.addWidget(self.count, 1)
        self.prev_button = QPushButton("→ السابق")
        self.next_button = QPushButton("التالي ←")
        choose = QPushButton("فتح")
        choose.setStyleSheet(button_qss("green"))
        close = QPushButton("إغلاق")
        close.setStyleSheet(button_qss("gray"))
        for b in (self.prev_button, self.next_button, choose, close):
            bottom.addWidget(b)
        layout.addLayout(bottom)
        self.prev_button.clicked.connect(lambda: self._go(self.page - 1))
        self.next_button.clicked.connect(lambda: self._go(self.page + 1))
        choose.clicked.connect(self._choose)
        close.clicked.connect(self.reject)
        self.table.doubleClicked.connect(lambda _i: self._choose())
        self.keyword.returnPressed.connect(lambda: self._go(1))
        self.keyword.textChanged.connect(lambda _t: self._go(1))
        for w in (self.date_from, self.date_to):
            w.dateChanged.connect(lambda _d: self._go(1))
        self.status.currentIndexChanged.connect(lambda _i: self._go(1))
        self._go(1)
        self.keyword.setFocus()

    @staticmethod
    def _format(key: str, value: Any, row: dict) -> str:
        if key == "invoice_no":
            return invoice_label(row)
        if key == "invoice_date":
            return value.strftime("%d-%m-%Y") if value else ""
        if key == "total":
            return f"{money(row['total_amount'])} {row['currency_code'] if row['currency_mode'] == 'single' else 'ج'}"
        if key == "status":
            return "مرحّلة ✔" if value == "posted" else "مسودة"
        return "" if value is None else str(value)

    def _args(self) -> tuple:
        return (_pydate(self.date_from.date()), _pydate(self.date_to.date()), self.status.currentData(),
                self.keyword.text().strip())

    def _go(self, page: int) -> None:
        counts = self.service.count_invoices(*self._args())
        pages = max(1, -(-counts["total"] // PAGE_SIZE))
        self.page = min(max(1, page), pages)
        rows = self.service.list_invoices(*self._args(), page=self.page)
        self.table.set_rows(rows)
        if rows:
            self.table.select_index(0)
        self.count.setText(f"{counts['total']} فاتورة (مسودات {counts['drafts']} · مرحّلة {counts['posted']}) · "
                           f"صفحة {self.page} من {pages}")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < pages)

    def _choose(self) -> None:
        record_id = self.table.current_id()
        if record_id is None:
            return
        self.selected_id = record_id
        self.accept()
