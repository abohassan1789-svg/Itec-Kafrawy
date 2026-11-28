"""شاشة «مرتجع المشتريات» — النموذج 1 «زي فاتورة الشراء بالظبط» (اختيار المستخدم 2026-11-28 —
mockups/phase7/03_purchase_return.html) بشكل الفاتورة السعودية وأخضر البرنامج.

```
↩ مرتجع المشتريات [مسودة]                                              المستخدم · التاريخ
[🔍][＋ جديد][💾 حفظ][✎ تعديل][⟳ تحديث][✔✔ ترحيل][↩ إلغاء الترحيل][🗑 حذف][✕ إلغاء][⇩ تصدير][⇥ خروج] ☐ مخزن ☐ وحدة
┌ 📅 بيانات المرتجع  [الأساسية][الدفع والعملة][إضافية] ─────────────────────────────────────────────┐
│  رقم المرتجع · التاريخ · المورد 🔍 · الرقم الضريبي · رقم إشعار المورد · النوع · المخزن · السبب ⚙   │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
┌ ◔ الإجماليات (340) ┐ ┌ ☰ تفاصيل المرتجع ───────────────────────────────────────────────────────┐
│ قبل الضريبة · ض.ق.م │ │ رقم الصنف │ اسم الصنف │ المخزن │ الوحدة │ الكمية │ السعر │ الفاتورة │ … │ الإجمالي│
│ ■ الإجمالي          │ │                                                                           │
│ رصيد المورد قبل←بعد │ │ إجمالي المرتجع قبل الضريبة                                      0.00     │
└────────────────────┘ └──────────────────────────────────────────────────────────────────────────┘
┌ 🏷 إضافة صنف: [من فاتورة ▾][الصنف من الفاتورة ▾ / الصنف 🔍][المخزن][الوحدة][الكمية][السعر][الضريبة]
│   [＋ إضافة للمرتجع][تفريغ][🗑 حذف الصنف][⤺ إرجاع كل الفاتورة]                                     │
│ اتشرى · رجع قبل كده · فاضل · المتاح في المخزن · سعر الفاتورة                                        │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```
* «من فاتورة» = فواتير المورد المرحّلة (بنفس النوع والعملة) — أو «من غير فاتورة» (الصنف من كل الأصناف بآخر سعر
  شراء). المرتجع الواحد ممكن يبقى من أكتر من فاتورة.
* السعر بيتملى بسعر الفاتورة (بعد الخصومات — قبل الضريبة) ويتغيّر · البضاعة بتطلع بنفس السعر.
* الضغط على سطر بينزّله تحت ويبقى «تحديث السطر» · المراجعة بتتحسب وانت بتكتب و«ترحيل» بيوقف لو فيه ✖.
* «⚙» جنب «سبب المرتجع» = نافذة الأسباب (إضافة · تعديل · إيقاف · مسح).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.security.session import SESSION
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.purchase_return_service import PAGE_SIZE, PurchaseReturnService, fmt_rate, return_label
from app.services.stock_ledger_service import fmt_qty
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, MODE_NAMES, BaseScreen, align_form_right, export_rows_to_excel
from app.ui.common.data_table import DataTable
from app.ui.common.lookup_edit import LookupEdit
from app.ui.screens.purchase_invoices_screen import (
    QSS as INVOICE_QSS,
)
from app.ui.screens.purchase_invoices_screen import (
    _cell,
    _date_edit,
    _dec,
    _pydate,
    _qdate,
    button_qss,
    money,
    num,
)

QSS = INVOICE_QSS.replace("purchaseInvoiceScreen", "purchaseReturnScreen")
STATUS_STYLE = {"draft": ("مسودة", "#FEF3C7", "#92400E"), "posted": ("مرحّل ✔", "#E7F6EE", "#087443"),
                "new": ("جديد", "#EFF6FF", "#1D4ED8")}
(C_CODE, C_NAME, C_WH, C_UNIT, C_QTY, C_PRICE, C_SRC, C_PRE, C_TAXR, C_VAT, C_TOTAL) = range(11)
HEADERS = ["رقم الصنف", "اسم الصنف", "المخزن", "الوحدة", "الكمية المرتجعة", "السعر", "الفاتورة الأصلية",
           "إجمالي قبل الضريبة", "نسبة الضريبة", "قيمة الضريبة", "إجمالي شامل الضريبة"]
MARK_COLORS = {"no": "#B91C1C", "warn": "#B45309"}
FREE = "__free__"


class PurchaseReturnsScreen(BaseScreen):
    MODULE = "purch"
    TARGET = "purchase_returns"
    TITLE = "مرتجع المشتريات"
    ICON = "↩"
    NEW_TEXT = "جديد"
    COLUMNS = [("return_no", "رقم المرتجع"), ("return_date", "التاريخ"), ("supplier_name", "المورد"),
               ("supplier_note_no", "رقم إشعار المورد"), ("total", "الإجمالي"), ("status", "الحالة")]

    def __init__(self, service: PurchaseReturnService | None = None, parent: QWidget | None = None) -> None:
        self.service = service or PurchaseReturnService()
        self._choices: dict[str, Any] = {}
        self._lines: list[dict[str, Any]] = []
        self._sel = -1
        self._filling = False
        self._review: dict[str, Any] | None = None
        self._src_lines: dict[int, dict] = {}            # سطور الفاتورة المختارة في «إضافة صنف» (id ← السطر)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh_review)
        self._load_choices()
        super().__init__(parent)
        latest = self.service.list_returns(None, None, page_size=1)
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
                             "currencies": [], "treasuries": [], "banks": [], "projects": [], "base_currency": "EGP",
                             "reasons": []}
            messages.show_error(self, "تعذّر تحميل البيانات", exc)
        self._items = {int(i["id"]): i for i in self._choices["items"]}
        self._whs = {int(w["id"]): w for w in self._choices["warehouses"]}
        self._taxes = {int(t["id"]): t for t in self._choices["tax_codes"]}
        self._base = self._choices.get("base_currency") or "EGP"

    def _build_ui(self) -> None:
        self.setObjectName("purchaseReturnScreen")
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
        hidden = self._build_list_box()
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
        sub = QLabel("رجوع بضاعة للمورد — بتطلع من المخزن بسعر الشراء والقيد بيتعمل لوحده")
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
        font.setPixelSize(12)
        font.setBold(True)
        button.setFont(font)
        button.setMinimumWidth(QFontMetrics(font).horizontalAdvance(text) + 20)
        if tip:
            button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.find_button = self._tb("🔍", "green", self.open_lookup, "بحث عن مرتجع (F1)")
        self.find_button.setMinimumWidth(40)
        self.new_button = self._tb("＋ جديد", "green", self.new_record)
        self.save_button = self._tb("💾 حفظ", "green", self.save, "حفظ مسودة")
        self.edit_button = self._tb("✎ تعديل", "blue", self.edit_record)
        self.refresh_button = self._tb("⟳ تحديث", "blue", self.refresh_screen)
        self.post_button = self._tb("✔✔ ترحيل", "blue", self.post_return,
                                    "البضاعة تطلع من المخزن والقيد يتعمل (وانت بتعدّل = حفظ + ترحيل)")
        self.unpost_button = self._tb("↩ إلغاء الترحيل", "blue", self.unpost_return)
        self.delete_button = self._tb("🗑 حذف", "red", self.delete)
        self.cancel_button = self._tb("✕ إلغاء", "white", self.cancel)
        self.export_button = self._tb("⇩ تصدير", "gray", self.export_excel, "أصناف المرتجع المفتوح لإكسل")
        self.exit_button = self._tb("⇥ خروج", "gray", self.close)
        shown = [(self.find_button, True), (self.new_button, self.has_action("create")),
                 (self.save_button, self.has_action("save")), (self.edit_button, self.has_action("edit")),
                 (self.refresh_button, True), (self.post_button, self.has_action("post")),
                 (self.unpost_button, self.has_action("unpost")), (self.delete_button, self.has_action("delete")),
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
        for box in (self.show_wh, self.show_unit):
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

    def _small_button(self, text: str, slot, tip: str = "") -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("lookupButton")
        button.setFixedSize(30, 28)
        button.setCursor(Qt.PointingHandCursor)
        if tip:
            button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def build_form(self, box: QGroupBox) -> None:
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        card, layout, head = self._card("📅 بيانات المرتجع")
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

        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip("الرقم الرسمي (PRT-2026-0001) بيتاخد مع أول ترحيل — والمسودة ليها رقم مؤقت")
        self.date_edit = _date_edit()
        self.date_edit.dateChanged.connect(self._on_date)
        self.supplier_edit = LookupEdit("اكتب كود أو اسم المورد…")
        self.supplier_edit.value_changed.connect(self._on_supplier)
        self.supplier_button = self._small_button("🔍", self._find_supplier)
        self.tax_no_edit = QLineEdit()
        self.tax_no_edit.setReadOnly(True)
        self.note_no_edit = QLineEdit()
        self.note_no_edit.setPlaceholderText("Credit Note — اختياري")
        self.note_no_edit.textChanged.connect(self._changed)
        self.kind_combo = QComboBox()
        self.kind_combo.addItem("فاتورة", "invoice")
        self.kind_combo.addItem("بيان", "statement")
        self.kind_combo.currentIndexChanged.connect(self._on_kind)
        self.wh_combo = QComboBox()
        self.wh_combo.setToolTip("المخزن للأصناف اللي من غير فاتورة (المربوط بفاتورة بيبدأ بمخزن سطرها)")
        self.wh_combo.currentIndexChanged.connect(self._changed)
        self.reason_combo = QComboBox()
        self.reason_combo.currentIndexChanged.connect(self._changed)
        self.reason_button = self._small_button("⚙", self.manage_reasons, "أسباب المرتجع (إضافة · تعديل · إيقاف)")
        page1 = self._grid([self._field("رقم المرتجع", self.number_edit), self._field("التاريخ", self.date_edit),
                            self._field("اسم المورد", self.supplier_edit, self.supplier_button),
                            self._field("الرقم الضريبي للمورد", self.tax_no_edit),
                            self._field("رقم إشعار المورد", self.note_no_edit), self._field("النوع", self.kind_combo),
                            self._field("المخزن الافتراضي", self.wh_combo),
                            self._field("سبب المرتجع", self.reason_combo, self.reason_button)])
        self.pay_combo = QComboBox()
        self.pay_combo.addItem("آجل (رصيد المورد بيقل)", "credit")
        self.pay_combo.addItem("نقدي (المورد رجّع الفلوس)", "cash")
        self.pay_combo.currentIndexChanged.connect(self._on_pay)
        self.source_combo = QComboBox()
        self.source_combo.setToolTip("«نقدي» = قيد قبض لوحده في الخزنة / البنك بيترحّل مع المرتجع")
        self.source_combo.currentIndexChanged.connect(self._changed)
        self.source_box = self._field("الفلوس دخلت في", self.source_combo)
        self.currency_combo = QComboBox()
        self.currency_combo.setToolTip("عملة المرتجع — الأصناف المربوطة بفاتورة بتاخد سعر صرف فاتورتها")
        self.currency_combo.currentIndexChanged.connect(self._on_currency)
        self.rate_edit = QLineEdit()
        self.rate_edit.setPlaceholderText("سعر الصرف")
        self.rate_edit.setToolTip("للأصناف اللي من غير فاتورة (1 من العملة = كام جنيه)")
        self.rate_edit.textChanged.connect(self._changed)
        cur_row = QWidget()
        cur_lay = QHBoxLayout(cur_row)
        cur_lay.setContentsMargins(0, 0, 0, 0)
        cur_lay.setSpacing(4)
        cur_lay.addWidget(self.currency_combo, 2)
        cur_lay.addWidget(self.rate_edit, 1)
        self.project_combo = QComboBox()
        self.project_combo.currentIndexChanged.connect(self._changed)
        page2 = self._grid([self._field("نوع المرتجع", self.pay_combo), self.source_box,
                            self._field("العملة وسعر الصرف", cur_row), self._field("أمر التوريد", self.project_combo)])
        self.note_edit = QLineEdit()
        self.note_edit.textChanged.connect(self._changed)
        page3 = self._grid([self._field("ملاحظات", self.note_edit)])
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
        totals, tl, _h = self._card("◔ الإجماليات")
        totals.setFixedWidth(340)
        totals.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.pre_value = self._summary(tl, "إجمالي المرتجع قبل الضريبة")
        self.vat_value = self._summary(tl, "قيمة الضريبة (عكس المدخلات)")
        net = QFrame()
        net.setObjectName("netBox")
        nl = QHBoxLayout(net)
        nl.setContentsMargins(12, 6, 12, 6)
        self.net_caption = QLabel("الإجمالي شامل الضريبة")
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
        self.egp_label = QLabel("")
        self.egp_label.setWordWrap(True)
        self.egp_label.setStyleSheet("color:#1D4ED8; font-weight:800;")
        el.addWidget(self.egp_label)
        tl.addWidget(self.egp_box)
        self.balance_value = self._summary(tl, "رصيد المورد (علينا)")
        self.review_label = QLabel("")
        self.review_label.setWordWrap(True)
        self.review_label.setStyleSheet("font-weight:800; padding:2px 4px;")
        tl.addWidget(self.review_label)
        tl.addStretch(1)
        row.addWidget(totals)
        details, dl, dh = self._card("☰ تفاصيل المرتجع")
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
        cap = QLabel("إجمالي المرتجع قبل الضريبة")
        cap.setStyleSheet("color:#0B3B23; font-weight:800;")
        self.items_total_label = QLabel("0.00")
        self.items_total_label.setObjectName("sumValue")
        fl.addWidget(cap)
        fl.addStretch(1)
        fl.addWidget(self.items_total_label)
        dl.addWidget(footer)
        row.addWidget(details, 1)
        return row

    def _summary(self, layout: QVBoxLayout, label: str) -> QLabel:
        frame = QFrame()
        frame.setObjectName("summaryRow")
        h = QHBoxLayout(frame)
        h.setContentsMargins(10, 3, 10, 3)
        h.addWidget(QLabel(label), 1)
        value = QLabel("0.00")
        value.setObjectName("sumValue")
        value.setAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
        h.addWidget(value)
        layout.addWidget(frame)
        return value

    def _build_add_card(self) -> QFrame:
        card, layout, _h = self._card("🏷 إضافة صنف")
        self.add_title = card.findChild(QLabel, "sectionTitle")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.inv_combo = QComboBox()
        self.inv_combo.setToolTip("فواتير المورد المرحّلة (بنفس النوع والعملة) — أو «من غير فاتورة»")
        self.inv_combo.currentIndexChanged.connect(self._on_invoice)
        self.src_combo = QComboBox()
        self.src_combo.currentIndexChanged.connect(self._on_source)
        self.item_edit = LookupEdit("اكتب كود أو اسم الصنف…")
        self.item_edit.value_changed.connect(self._on_item)
        self.item_button = self._small_button("🔍", self._find_item)
        self.add_wh = QComboBox()
        self.add_wh.currentIndexChanged.connect(self._update_info)
        self.add_unit = QComboBox()
        self.add_unit.currentIndexChanged.connect(self._on_unit)
        self.add_qty = QLineEdit("1")
        self.add_price = QLineEdit()
        for edit in (self.add_qty, self.add_price):
            edit.setMaximumWidth(95)
            edit.returnPressed.connect(self.add_line)
        self.add_tax = QComboBox()
        self.add_button = QPushButton("＋ إضافة للمرتجع")
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
        self.all_button = QPushButton("⤺ إرجاع كل الفاتورة")
        self.all_button.setStyleSheet(button_qss("white") + "QPushButton { color:#137A38; border-color:#137A38; }")
        self.all_button.setFixedHeight(34)
        self.all_button.setToolTip("كل اللي فاضل من الفاتورة المختارة ينزل في المرتجع")
        self.all_button.clicked.connect(self.return_all)
        # «📋 فواتير المشتريات» (طلب المستخدم 2026-11-28): واجهة منبثقة — الفواتير فوق وتفاصيل المختارة تحت.
        self.pick_button = QPushButton("📋 فواتير المشتريات")
        self.pick_button.setStyleSheet(button_qss("green"))
        self.pick_button.setFixedHeight(34)
        self.pick_button.setCursor(Qt.PointingHandCursor)
        self.pick_button.setToolTip("اختار الأصناف الراجعة من فواتير الشراء المرحّلة (الفواتير فوق والتفاصيل تحت)")
        self.pick_button.clicked.connect(self.pick_from_invoices)
        self.inv_lookup = self._small_button("📋", self.pick_from_invoices, "فواتير المشتريات")
        inv_box = self._field("من فاتورة", self.inv_combo, self.inv_lookup)
        inv_box.setMinimumWidth(200)
        row.addWidget(inv_box, 2)
        self.src_box = self._field("الصنف من الفاتورة", self.src_combo)
        self.src_box.setMinimumWidth(240)
        row.addWidget(self.src_box, 3)
        self.item_box = self._field("الصنف", self.item_edit, self.item_button)
        self.item_box.setMinimumWidth(240)
        row.addWidget(self.item_box, 3)
        self.add_wh_box = self._field("المخزن", self.add_wh)
        row.addWidget(self.add_wh_box, 1)
        self.add_unit_box = self._field("الوحدة", self.add_unit)
        row.addWidget(self.add_unit_box, 1)
        row.addWidget(self._field("الكمية المرتجعة", self.add_qty))
        row.addWidget(self._field("السعر", self.add_price))
        row.addWidget(self._field("الضريبة", self.add_tax), 1)
        layout.addLayout(row)
        # السطر التاني: المعلومات + الأزرار (عشان خانات الصنف تاخد العرض كله على الشاشات الصغيرة)
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.info_label = QLabel("")
        self.info_label.setObjectName("infoLabel")
        self.info_label.setWordWrap(True)
        row2.addWidget(self.info_label, 1)
        for b in (self.pick_button, self.add_button, self.clear_button, self.remove_button, self.all_button):
            font = QFont(b.font())
            font.setPixelSize(12)
            font.setBold(True)
            b.setFont(font)
            b.setMinimumWidth(QFontMetrics(font).horizontalAdvance("✎ تحديث السطر" if b is self.add_button
                                                                  else b.text()) + 24)
            row2.addWidget(b)
        layout.addLayout(row2)
        self._fill_add_choices()
        return card

    # ---- الاختيارات ---------------------------------------------------------
    def _fill_header_choices(self) -> None:
        self.supplier_edit.set_items([(int(s["id"]), s["code"], f"{s['code']} — {s['name_ar']}")
                                      for s in self._choices["suppliers"] if s["is_active"]])
        for combo, rows, first in (
                (self.wh_combo, [(f"{w['code']} — {w['name_ar']}", int(w["id"])) for w in self._choices["warehouses"]
                                 if w["is_active"] and not w["is_virtual"]], None),
                (self.project_combo, [(f"{p['code']} · {p['name']}", int(p["id"])) for p in self._choices["projects"]
                                      if p["status"] == "open"], "— من غير —")):
            combo.blockSignals(True)
            combo.clear()
            if first:
                combo.addItem(first, None)
            for text, value in rows:
                combo.addItem(text, value)
            combo.blockSignals(False)
        self._fill_reasons()
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
        self.currency_combo.blockSignals(False)

    def _fill_reasons(self, keep: Any = None) -> None:
        current = keep if keep is not None else self.reason_combo.currentData()
        self.reason_combo.blockSignals(True)
        self.reason_combo.clear()
        self.reason_combo.addItem("— من غير —", None)
        for r in self._choices.get("reasons") or []:
            if r["is_active"] or r["id"] == current:
                self.reason_combo.addItem(r["name_ar"] + ("" if r["is_active"] else " (موقوف)"), int(r["id"]))
        self._set_combo(self.reason_combo, current)
        self.reason_combo.blockSignals(False)

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

    def _currency(self) -> str:
        return str(self.currency_combo.currentData() or self._base)

    def _on_supplier(self, supplier_id: Any) -> None:
        sup = next((s for s in self._choices["suppliers"] if s["id"] == supplier_id), None)
        self.tax_no_edit.setText((sup or {}).get("tax_reg_no") or ("—" if sup else ""))
        if self._editing() and sup:
            linked = [ln for ln in self._lines if ln.get("source_line_id")]
            if linked and any(ln.get("supplier_id") != supplier_id for ln in linked):
                self._lines = [ln for ln in self._lines if not ln.get("source_line_id")]
                self._show_lines()
                messages.info(self, "تغيير المورد", f"اتشال {len(linked)} صنف مربوط بفواتير المورد القديم.")
            self._set_combo(self.currency_combo, sup["currency_code"])
        self._fill_invoices()
        self._changed()

    def _on_date(self, *_args: Any) -> None:
        if self._editing() and self._currency() != self._base:
            self._refresh_rate()
        self._changed()

    def _refresh_rate(self) -> None:
        rate = self.service.rate_on(self._currency(), _pydate(self.date_edit.date()))
        self.rate_edit.setText(num(rate) if rate else "")

    def _on_currency(self, *_args: Any) -> None:
        base = self._currency() == self._base
        self.rate_edit.setEnabled(self.mode in EDITING and not base)
        if self._editing():
            if base:
                self.rate_edit.setText("1")
            else:
                self._refresh_rate()
        self._fill_invoices()
        self._changed()

    def _on_kind(self, *_args: Any) -> None:
        statement = self.kind_combo.currentData() == "statement"
        self.add_tax.setEnabled(self.mode in EDITING and not statement and self._free())
        self._fill_invoices()
        self._changed()

    def _on_pay(self, *_args: Any) -> None:
        self.source_box.setVisible(self.pay_combo.currentData() == "cash")
        self._changed()

    # ---- «إضافة صنف» -------------------------------------------------------
    def _free(self) -> bool:
        return self.inv_combo.currentData() in (None, FREE)

    def _fill_invoices(self) -> None:
        """فواتير المورد المرحّلة بنفس النوع والعملة (اللي لسه فيها حاجة ترجع) + «من غير فاتورة»."""
        keep = self.inv_combo.currentData()
        self.inv_combo.blockSignals(True)
        self.inv_combo.clear()
        sid = self.supplier_edit.currentData()
        if sid:
            try:
                rows = self.service.supplier_invoices(int(sid), self.kind_combo.currentData(), self._currency(),
                                                      exclude_return_id=self.current_id)
            except Exception:  # noqa: BLE001
                rows = []
            for r in rows:
                if r["remaining_lines"]:
                    self.inv_combo.addItem(f"{r['invoice_no']} · {r['invoice_date']:%d-%m-%Y}"
                                           + (f" · {r['supplier_invoice_no']}" if r["supplier_invoice_no"] else ""),
                                           int(r["id"]))
        self.inv_combo.addItem("— من غير فاتورة —", FREE)
        if sid != getattr(self, "_inv_supplier", None):      # مورد جديد ← أحدث فاتورة ليه
            keep = None
        self._inv_supplier = sid
        index = self.inv_combo.findData(keep)
        self.inv_combo.setCurrentIndex(index if index >= 0 else 0)
        self.inv_combo.blockSignals(False)
        self._on_invoice()

    def _on_invoice(self, *_args: Any) -> None:
        free = self._free()
        self.src_box.setVisible(not free)
        self.item_box.setVisible(free)
        self.all_button.setVisible(not free)
        self._src_lines = {}
        self.src_combo.blockSignals(True)
        self.src_combo.clear()
        if not free:
            try:
                rows = self.service.invoice_lines(int(self.inv_combo.currentData()), self.current_id)
            except Exception:  # noqa: BLE001
                rows = []
            for r in rows:
                self._src_lines[int(r["id"])] = r
                left = self._left(r)
                self.src_combo.addItem(
                    f"{r['item_code']} — {r['item_name']} · اتشرى {fmt_qty(Decimal(r['qty']))} {r['unit_name']} · "
                    f"فاضل {fmt_qty(left / Decimal(r['factor']))}", int(r["id"]))
        self.src_combo.blockSignals(False)
        if self.mode in EDITING and self._sel < 0:
            self._on_source()
        self._entry_buttons()
        self._update_info()

    def _left(self, src: dict[str, Any], skip: int = -1) -> Decimal:
        """الفاضل من سطر الفاتورة بالوحدة الأساسية بعد اللي في المرتجع ده (غير السطر ``skip``)."""
        used = sum((_dec(ln["qty"]) * self._factor_of(ln["item_id"], ln["unit_id"])
                    for n, ln in enumerate(self._lines) if n != skip and ln.get("source_line_id") == int(src["id"])),
                   Decimal("0"))
        return max(Decimal("0"), Decimal(src["remaining_base"]) - used)

    def _units(self, item_id: Any) -> list[tuple[int, str, Decimal]]:
        return self._choices["units"].get(int(item_id), []) if item_id else []

    def _factor_of(self, item_id: Any, unit_id: Any) -> Decimal:
        return next((f for u, _n, f in self._units(item_id) if u == unit_id), Decimal("1"))

    def _current_item(self) -> int | None:
        if self._free():
            return self.item_edit.currentData()
        src = self._src_lines.get(self.src_combo.currentData() or 0)
        return int(src["item_id"]) if src else None

    def _fill_units(self, item_id: Any, unit_id: Any = None) -> None:
        self.add_unit.blockSignals(True)
        self.add_unit.clear()
        for uid, name, factor in self._units(item_id):
            self.add_unit.addItem(name if factor == 1 else f"{name} ({fmt_qty(factor)})", uid)
        self._set_combo(self.add_unit, unit_id)
        self.add_unit.blockSignals(False)

    def _on_source(self, *_args: Any) -> None:
        src = self._src_lines.get(self.src_combo.currentData() or 0)
        if src is None:
            self._update_info()
            return
        item = self._items.get(int(src["item_id"])) or {}
        self._fill_units(src["item_id"], int(src["unit_id"]))
        self.add_wh.setEnabled(self.mode in EDITING and not item.get("is_service"))
        if self.mode in EDITING and self._sel < 0:
            if src["warehouse_id"]:
                self._set_combo(self.add_wh, int(src["warehouse_id"]))
            self.add_qty.setText(num(self._left(src) / Decimal(src["factor"])))
            self.add_price.setText(num(Decimal(src["source_price"]).quantize(Decimal("0.01"))))
            self._set_combo(self.add_tax, src["tax_code_id"])
        self._update_info()

    def _on_item(self, item_id: Any) -> None:
        item = self._items.get(int(item_id)) if item_id else None
        self._fill_units(item_id)
        self.add_wh.setEnabled(self.mode in EDITING and not (item and item["is_service"]))
        if item and self._sel < 0 and self.mode in EDITING:
            self._set_combo(self.add_wh, self.wh_combo.currentData())
            self._set_combo(self.add_tax, item.get("tax_code_id"))
            self._prefill_price()
        self._update_info()

    def _on_unit(self, *_args: Any) -> None:
        if self.mode in EDITING and self._sel < 0:
            if self._free():
                self._prefill_price()
            else:
                src = self._src_lines.get(self.src_combo.currentData() or 0)
                if src:
                    factor = self._factor_of(src["item_id"], self.add_unit.currentData())
                    self.add_price.setText(num((Decimal(src["base_price"]) * factor).quantize(Decimal("0.01"))))
        self._update_info()

    def _rate(self) -> Decimal:
        return Decimal("1") if self._currency() == self._base else _dec(self.rate_edit.text())

    def _prefill_price(self) -> None:
        item_id = self.item_edit.currentData()
        if not item_id:
            return
        try:
            info = self.service.item_info(int(item_id), self.add_wh.currentData(), self.kind_combo.currentData())
        except Exception:  # noqa: BLE001
            return
        rate = self._rate()
        factor = self._factor_of(item_id, self.add_unit.currentData())
        if info.get("last_cost") and rate > 0:
            self.add_price.setText(num((info["last_cost"] * factor / rate).quantize(Decimal("0.01"))))

    def _update_info(self, *_args: Any) -> None:
        item_id = self._current_item()
        item = self._items.get(int(item_id)) if item_id else None
        if item is None:
            self.info_label.setText("اختار الفاتورة والصنف — Enter في الكمية أو السعر = إضافة")
            return
        parts: list[str] = []
        src = None if self._free() else self._src_lines.get(self.src_combo.currentData() or 0)
        unit = item["unit_name"]
        if src:
            parts.append(f"اتشرى {fmt_qty(Decimal(src['base_qty']))} · رجع قبل كده "
                         f"{fmt_qty(Decimal(src['returned_qty'] or 0))} · فاضل {fmt_qty(self._left(src, self._sel))} {unit}")
            parts.append(f"سعر الفاتورة: {money(src['base_price'])} {self._currency()} / {unit}")
        if item["is_service"]:
            parts.append("خدمي — مالوش مخزون")
        else:
            try:
                info = self.service.item_info(int(item_id), self.add_wh.currentData(), self.kind_combo.currentData())
            except Exception:  # noqa: BLE001
                info = {}
            if info.get("available") is not None:
                parts.append(f"المتاح في «{self.add_wh.currentText()}»: {fmt_qty(info['available'])} {unit}")
            if info.get("avg"):
                parts.append(f"المتوسط: {money(info['avg'])}")
            if not src and info.get("last_cost"):
                parts.append(f"آخر سعر شراء: {money(info['last_cost'])} ج / {unit}")
        parts.append("البضاعة بتطلع بسعر المرتجع · Enter في الكمية = إضافة")
        self.info_label.setText("   ·   ".join(parts))

    def _entry(self) -> dict[str, Any]:
        qty = _dec(self.add_qty.text())
        if qty <= 0:
            raise BusinessError("اكتب كمية أكبر من صفر.")
        price = _dec(self.add_price.text())
        if price < 0:
            raise BusinessError("السعر مايبقاش بالسالب.")
        statement = self.kind_combo.currentData() == "statement"
        if self._free():
            item_id = self.item_edit.currentData()
            if not item_id:
                raise BusinessError("اختار الصنف.")
            item = self._items[int(item_id)]
            line = {"source_line_id": None, "item_id": int(item_id), "unit_id": self.add_unit.currentData()
                    or int(item["unit_id"]), "tax_code_id": None if statement else self.add_tax.currentData(),
                    "invoice_no": None, "supplier_id": None}
        else:
            src = self._src_lines.get(self.src_combo.currentData() or 0)
            if src is None:
                raise BusinessError("اختار الصنف من الفاتورة.")
            item = self._items.get(int(src["item_id"])) or {"is_service": src["is_service"]}
            line = {"source_line_id": int(src["id"]), "item_id": int(src["item_id"]),
                    "unit_id": self.add_unit.currentData() or int(src["unit_id"]),
                    "tax_code_id": None if statement else src["tax_code_id"], "invoice_no": src["invoice_no"],
                    "supplier_id": src["supplier_id"]}
            factor = self._factor_of(line["item_id"], line["unit_id"])
            if qty * factor > self._left(src, self._sel):
                raise BusinessError(f"أكتر من اللي فاضل يرجع من {src['invoice_no']} "
                                    f"(فاضل {fmt_qty(self._left(src, self._sel) / factor)}).")
        line.update({"warehouse_id": None if item.get("is_service") else self.add_wh.currentData(), "qty": qty,
                     "unit_price": price})
        if not item.get("is_service") and not line["warehouse_id"]:
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
                "source_line_id", "item_id", "warehouse_id", "unit_id", "unit_price", "tax_code_id"))), None)
            if same is not None:
                same["qty"] = _dec(same["qty"]) + line["qty"]
            else:
                self._lines.append(line)
        self.clear_entry()
        self._show_lines()
        self._refresh_review()

    def return_all(self) -> None:
        """«⤺ إرجاع كل الفاتورة»: كل الفاضل من الفاتورة المختارة (بوحدة الفاتورة وسعرها ومخزنها)."""
        if self.mode not in EDITING or self._free():
            return
        statement = self.kind_combo.currentData() == "statement"
        added = 0
        for src in self._src_lines.values():
            left = self._left(src)
            if left <= 0:
                continue
            factor = Decimal(src["factor"])
            self._lines.append({"source_line_id": int(src["id"]), "item_id": int(src["item_id"]),
                                "unit_id": int(src["unit_id"]), "warehouse_id": src["warehouse_id"],
                                "qty": (left / factor).normalize(), "unit_price": Decimal(src["source_price"]),
                                "tax_code_id": None if statement else src["tax_code_id"],
                                "invoice_no": src["invoice_no"], "supplier_id": src["supplier_id"]})
            added += 1
        if not added:
            messages.info(self, "إرجاع كل الفاتورة", "مفيش حاجة فاضلة ترجع من الفاتورة دي.")
        self.clear_entry()
        self._show_lines()
        self._refresh_review()

    def pick_from_invoices(self) -> None:
        """«📋 فواتير المشتريات»: الواجهة المنبثقة ← الأصناف المختارة بتنزل في المرتجع (والمورد لو لسه فاضي)."""
        if self.mode not in EDITING:
            return
        used: dict[int, Decimal] = {}
        for ln in self._lines:
            if ln.get("source_line_id"):
                used[int(ln["source_line_id"])] = used.get(int(ln["source_line_id"]), Decimal("0")) +                     _dec(ln["qty"]) * self._factor_of(ln["item_id"], ln["unit_id"])
        supplier = self.supplier_edit.currentData()
        dialog = InvoicePickerDialog(self.service, self, suppliers=self._choices["suppliers"], supplier_id=supplier,
                                     lock_supplier=bool(supplier and self._lines),
                                     stock_kind=self.kind_combo.currentData() or "invoice",
                                     currency_code=self._currency() if supplier else None,
                                     exclude_return_id=self.current_id if self.mode != "new" else None, used=used)
        if not dialog.exec() or not dialog.result:
            return
        if dialog.result_supplier != supplier:
            self.supplier_edit.set_value(dialog.result_supplier)       # بيشيل أصناف فواتير المورد القديم
        if dialog.result_currency and dialog.result_currency != self._currency():
            self._set_combo(self.currency_combo, dialog.result_currency)
        for line in dialog.result:
            same = next((ln for ln in self._lines if all(ln.get(k) == line.get(k) for k in (
                "source_line_id", "warehouse_id", "unit_id", "unit_price"))), None)
            if same is not None:
                same["qty"] = _dec(same["qty"]) + line["qty"]
            else:
                self._lines.append(line)
        self._fill_invoices()
        self.clear_entry()
        self._show_lines()
        self._refresh_review()

    def clear_entry(self) -> None:
        self._sel = -1
        self.lines_table.clearSelection()
        self.item_edit.set_value(None)
        self.add_qty.setText("1")
        self.add_price.clear()
        self._set_combo(self.add_wh, self.wh_combo.currentData())
        if not self._free() and self.mode in EDITING:
            self._on_source()
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
        self._filling = True
        try:
            if ln.get("source_line_id"):
                inv_id = next((s["invoice_id"] for s in self._src_lines.values() if s["id"] == ln["source_line_id"]),
                              None) or self._invoice_of(ln["source_line_id"])
                index = self.inv_combo.findData(inv_id)
                if index < 0 and inv_id:
                    self.inv_combo.addItem(ln.get("invoice_no") or "فاتورة", inv_id)
                    index = self.inv_combo.count() - 1
                self.inv_combo.setCurrentIndex(max(index, 0))
                self._set_combo(self.src_combo, ln["source_line_id"])
                self._fill_units(ln["item_id"], ln["unit_id"])
            else:
                self._set_combo(self.inv_combo, FREE)
                self.item_edit.set_value(ln["item_id"])
                self._fill_units(ln["item_id"], ln["unit_id"])
                self._set_combo(self.add_tax, ln.get("tax_code_id"))
            self._set_combo(self.add_wh, ln.get("warehouse_id") or self.wh_combo.currentData())
            self.add_qty.setText(num(ln["qty"]))
            self.add_price.setText(num(ln["unit_price"]))
        finally:
            self._filling = False
        self._entry_buttons()
        self._update_info()

    def _invoice_of(self, source_line_id: int) -> int | None:
        rec = self.current_record or {}
        for ln in rec.get("lines") or []:
            if ln.get("source_line_id") == source_line_id:
                return ln.get("source_invoice_id")
        return None

    def _entry_buttons(self) -> None:
        editing = self.mode in EDITING
        updating = 0 <= self._sel < len(self._lines)
        self.add_button.setText("✎ تحديث السطر" if updating else "＋ إضافة للمرتجع")
        self.add_button.setStyleSheet(button_qss("blue" if updating else "green"))
        self.clear_button.setText("＋ سطر جديد" if updating else "✎ تفريغ")
        self.add_button.setEnabled(editing)
        self.clear_button.setEnabled(editing)
        self.remove_button.setEnabled(editing and updating)
        self.all_button.setEnabled(editing and not self._free())
        self.pick_button.setEnabled(editing)
        self.inv_lookup.setEnabled(editing)
        free = self._free()
        self.add_tax.setEnabled(editing and free and self.kind_combo.currentData() != "statement")
        self.inv_combo.setEnabled(editing)
        self.src_combo.setEnabled(editing)
        if self.add_title is not None:
            self.add_title.setText(f"🏷 تعديل السطر {self._sel + 1}" if updating else "🏷 إضافة صنف")

    # ---- الجدول والإجماليات ----------------------------------------------------
    def _apply_columns(self, *_args: Any) -> None:
        self.lines_table.setColumnHidden(C_WH, not self.show_wh.isChecked())
        self.lines_table.setColumnHidden(C_UNIT, not self.show_unit.isChecked())
        self.add_wh_box.setVisible(self.show_wh.isChecked())
        self.add_unit_box.setVisible(self.show_unit.isChecked())

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
            t.setItem(r, C_QTY, _cell(fmt_qty(_dec(ln["qty"])), number=True, bold=True))
            t.setItem(r, C_PRICE, _cell(money(ln["unit_price"]), number=True))
            t.setItem(r, C_SRC, _cell(ln.get("invoice_no") or "— من غير —"))
            t.setItem(r, C_PRE, _cell(money(c["net_amount"]) if c else "", number=True))
            t.setItem(r, C_TAXR, _cell("—" if statement or not tax else f"{num(tax['rate'])}%"))
            t.setItem(r, C_VAT, _cell(money(c["vat_amount"]) if c else "", number=True))
            t.setItem(r, C_TOTAL, _cell(money(c["total_amount"]) if c else "", number=True, bold=True))
            if mark:
                t.setRowHeight(r, 44)
        if 0 <= self._sel < len(self._lines):
            t.selectRow(self._sel)
        invoices = sorted({ln["invoice_no"] for ln in self._lines if ln.get("invoice_no")})
        self.lines_hint.setText(f"{len(self._lines)} صنف" + (f" · من {'، '.join(invoices)}" if invoices else "")
                                + " · اضغط على سطر عشان تعدّله تحت")

    def _refresh_review(self) -> None:
        self._timer.stop()
        try:
            data = self.collect()
            review = self.service.review(data, self.current_id if self.mode != "new" else None)
        except Exception as exc:  # noqa: BLE001
            review = {"checks": [("no", messages.error_text(exc))], "calc": None, "lines": {}, "ready": False}
        self._review = review
        self._show_totals(review.get("calc"), review.get("supplier"))
        self._show_lines()
        checks = review.get("checks") or []
        problems = [c[1] for c in checks if c[0] == "no"]
        warns = [c[1] for c in checks if c[0] == "warn"]
        if not self._lines:
            text, color = "اختار الفاتورة والأصناف الراجعة من «إضافة صنف» تحت", "#64748B"
        elif problems:
            text, color = f"✖ {len(problems)} مشكلة قبل الترحيل — {problems[0]}", "#B91C1C"
        elif self.current_record and self.current_record.get("status") == "posted" and self.mode == "view":
            text, color = "✔ مرحّل — البضاعة خرجت من المخزن والقيد اتعمل", "#087443"
        else:
            text, color = ("✔ جاهز للترحيل" + (f" · ⚠ {len(warns)} تنبيه" if warns else "")), "#087443"
        self.review_label.setText(text)
        self.review_label.setStyleSheet(f"font-weight:800; padding:2px 4px; color:{color};")
        self.review_label.setToolTip("\n".join(f"{ {'ok': '✔', 'no': '✖', 'warn': '⚠', 'info': 'ℹ'}[c[0]]} {c[1]}"
                                               for c in checks))

    def _show_totals(self, calc: dict[str, Any] | None, supplier: dict[str, Any] | None) -> None:
        calc = calc or {}
        z = Decimal("0")
        cur = self._currency()
        self.pre_value.setText(money(calc.get("net_amount", z)))
        self.items_total_label.setText(money(calc.get("net_amount", z)))
        self.vat_value.setText(money(calc.get("vat_amount", z)))
        self.net_caption.setText(f"الإجمالي شامل الضريبة ({cur})")
        self.net_value.setText(money(calc.get("total_amount", z)))
        if cur != self._base and calc:
            rates = " · ".join(f"× {r}" for r in calc.get("by_rate") or {})
            self.egp_label.setText(f"بالجنيه ({rates}) = {money(calc.get('total_base'))}")
            self.egp_box.setVisible(True)
        else:
            self.egp_box.setVisible(False)
        if supplier:
            same = supplier["before"] == supplier["after"]
            self.balance_value.setText(money(supplier["after"]) if same else
                                       f"{money(supplier['before'])} ← {money(supplier['after'])}")
        else:
            self.balance_value.setText("—")

    # ---- الأوضاع -----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.supplier_edit, self.supplier_button, self.note_no_edit, self.kind_combo,
                self.wh_combo, self.reason_combo, self.pay_combo, self.source_combo, self.currency_combo,
                self.project_combo, self.note_edit, self.inv_combo, self.src_combo, self.item_edit, self.item_button,
                self.add_wh, self.add_unit, self.add_qty, self.add_price]

    def _record_status(self) -> str | None:
        return (self.current_record or {}).get("status") if self.current_id is not None else None

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        status = self._record_status()
        if mode == "view" and status == "posted":
            self.edit_button.setEnabled(False)
        if mode == "view" and (self.current_record or {}).get("return_no"):
            self.delete_button.setEnabled(False)
        self.rate_edit.setEnabled(mode in EDITING and self._currency() != self._base)
        self.reason_button.setEnabled(self.can("create") or self.can("edit"))
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
        self.post_button.setEnabled(self.can("post") and (editing and self.can("save") or status == "draft"))
        self.unpost_button.setEnabled(not editing and status == "posted" and self.can("unpost"))

    # ---- الأزرار -----------------------------------------------------------
    def new_record(self) -> None:
        super().new_record()
        if self.mode == "new":
            self.supplier_edit.setFocus()

    def _problems(self) -> str:
        review = self._review or {}
        return "\n".join(f"✖ {c[1]}" for c in review.get("checks") or [] if c[0] == "no")

    def post_return(self) -> None:
        if not self.post_button.isEnabled():
            return
        self._refresh_review()
        problems = self._problems()
        if problems:
            messages.warn(self, "الترحيل", f"المرتجع فيه مشاكل لازم تتصلّح قبل الترحيل:\n{problems}")
            return
        total = ((self._review or {}).get("calc") or {}).get("total_base")
        if not messages.ask_yes_no(self, "ترحيل المرتجع",
                                   f"ترحيل المرتجع؟ البضاعة هتطلع من المخزن والقيد هيتعمل ({money(total)} ج)."):
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
        messages.info(self, "الترحيل", f"اترحّل المرتجع {return_label(self.current_record or {})} ✔")

    def unpost_return(self) -> None:
        if not self.unpost_button.isEnabled():
            return
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   "إلغاء ترحيل المرتجع؟ البضاعة هترجع المخزن والقيد هيتمسح "
                                   "(والمرتجع هيرجع مسودة برقمه)."):
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

    def manage_reasons(self) -> None:
        dialog = ReasonsDialog(self.service, self)
        dialog.exec()
        try:
            self._choices["reasons"] = self.service.reasons()
        except Exception:  # noqa: BLE001
            return
        self._fill_reasons(dialog.selected_id or self.reason_combo.currentData())
        self._changed()

    def open_lookup(self) -> None:
        if self.mode in EDITING:
            return
        dialog = ReturnSearchDialog(self.service, self)
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

        rows = [{"id": i["id"], "code": i["code"], "name": i["name"], "unit_name": i["unit_name"]}
                for i in self._choices["items"] if i["is_active"]]
        dialog = LookupDialog("الأصناف", [("code", "الكود"), ("name", "الاسم"), ("unit_name", "الوحدة")], rows,
                              parent=self)
        if dialog.exec() and dialog.selected_id is not None:
            self.item_edit.set_value(dialog.selected_id)

    def export_excel(self) -> None:
        if not self.can("export") or not self._lines:
            return
        path, _ = QFileDialog.getSaveFileName(self, "تصدير إكسل",
                                              f"{return_label(self.current_record or {'draft_no': ''})}.xlsx",
                                              "Excel (*.xlsx)")
        if not path:
            return
        rows = [{str(c): (self.lines_table.item(r, c).text() if self.lines_table.item(r, c) else "")
                 for c in range(len(HEADERS))} for r in range(self.lines_table.rowCount())]
        columns = [(str(c), h) for c, h in enumerate(HEADERS) if not self.lines_table.isColumnHidden(c)]
        try:
            export_rows_to_excel(Path(path), self.TITLE, columns, rows, lambda _k, v, _r: v)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التصدير", exc)
            return
        messages.info(self, "تصدير إكسل", f"اتصدّر {len(rows)} صنف.")

    # ---- الـ Base -------------------------------------------------------------
    def fetch_rows(self, keyword: str) -> list[dict]:
        return []

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_return(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.date_edit.setDate(_qdate(record["return_date"]))
            self.supplier_edit.set_items([(int(s["id"]), s["code"], f"{s['code']} — {s['name_ar']}")
                                          for s in self._choices["suppliers"] if s["is_active"]],
                                         record["supplier_id"],
                                         f"{record.get('supplier_code')} — {record.get('supplier_name')}")
            self.tax_no_edit.setText(record.get("supplier_tax_no") or "—")
            self.number_edit.setText(return_label(record))
            self.note_no_edit.setText(record.get("supplier_note_no") or "")
            self._set_combo(self.kind_combo, record.get("stock_kind") or "invoice")
            self._set_combo(self.wh_combo, record.get("warehouse_id"), record.get("warehouse_name"))
            self._fill_reasons(record.get("reason_id"))
            self._set_combo(self.pay_combo, record.get("pay_kind") or "credit")
            if record.get("pay_kind") == "cash":
                source = f"{record['source_kind']}:{record['treasury_id'] or record['bank_account_id']}"
                self._set_combo(self.source_combo, source, record.get("treasury_name") or record.get("bank_name"))
            self._set_combo(self.currency_combo, record["currency_code"], record["currency_code"])
            self.rate_edit.setText(num(record.get("fx_rate")))
            self._set_combo(self.project_combo, record.get("project_id"), record.get("project_code"))
            self.note_edit.setText(record.get("description") or "")
            self._lines = [{"source_line_id": ln["source_line_id"], "item_id": int(ln["item_id"]),
                            "warehouse_id": ln["warehouse_id"], "unit_id": int(ln["unit_id"]),
                            "qty": Decimal(ln["qty"]), "unit_price": Decimal(ln["unit_price"]),
                            "tax_code_id": ln["tax_code_id"], "invoice_no": ln.get("source_invoice_no"),
                            "supplier_id": record["supplier_id"] if ln["source_line_id"] else None}
                           for ln in record.get("lines") or []]
            self._sel = -1
            self._on_pay()
            self._fill_invoices()
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
            self.note_no_edit.clear()
            self.kind_combo.setCurrentIndex(0)
            default_wh = next((int(w["id"]) for w in self._choices["warehouses"]
                               if w["is_default"] and w["is_active"] and not w["is_virtual"]), None)
            self._set_combo(self.wh_combo, default_wh)
            self._fill_reasons(None)
            self.reason_combo.setCurrentIndex(0)
            self.pay_combo.setCurrentIndex(0)
            if self.source_combo.count():
                self.source_combo.setCurrentIndex(0)
            self._set_combo(self.currency_combo, self._base)
            self.rate_edit.setText("1")
            self.project_combo.setCurrentIndex(0)
            self.note_edit.clear()
            self._lines = []
            self._fill_invoices()
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
        return {"return_date": _pydate(self.date_edit.date()), "supplier_id": self.supplier_edit.currentData(),
                "supplier_note_no": self.note_no_edit.text(), "reason_id": self.reason_combo.currentData(),
                "stock_kind": self.kind_combo.currentData(), "pay_kind": self.pay_combo.currentData(),
                "source_kind": source[0], "treasury_id": source[1] if source[0] == "treasury" else None,
                "bank_account_id": source[1] if source[0] == "bank" else None,
                "warehouse_id": self.wh_combo.currentData(), "project_id": self.project_combo.currentData(),
                "currency_code": self._currency(), "fx_rate": self.rate_edit.text(),
                "description": self.note_edit.text(),
                "lines": [{k: ln.get(k) for k in ("source_line_id", "item_id", "warehouse_id", "unit_id", "qty",
                                                  "unit_price", "tax_code_id")} for ln in self._lines]}

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(record_id, payload,
                                       expected_version=None if record_id is None else self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_return(int(record_id), expected_version=self.current_version)


class InvoicePickerDialog(QDialog):
    """«📋 فواتير المشتريات» (طلب المستخدم 2026-11-28): فوق صفوف الفواتير المرحّلة اللي لسه فيها حاجة ترجع (مورد ·
    فترة · بحث · صفحات) — والضغط على فاتورة بيعرض تفاصيلها تحت: ✔ لكل صنف + «هيرجع» (الفاضي = الفاضل كله).
    الاختيار بيفضل محفوظ وانت بتتنقّل بين الفواتير، و«إضافة المختار» بيرجّع كل الأصناف المختارة للمرتجع."""

    INV_HEADERS = ["رقم الفاتورة", "التاريخ", "المورد", "رقم فاتورة المورد", "النوع", "العملة", "الإجمالي",
                   "أصناف فاضلة"]
    (L_CHECK, L_CODE, L_NAME, L_WH, L_UNIT, L_BOUGHT, L_BEFORE, L_LEFT, L_QTY, L_PRICE) = range(10)
    LINE_HEADERS = ["✔", "رقم الصنف", "اسم الصنف", "المخزن", "الوحدة", "اتشرى", "رجع قبل كده", "فاضل",
                    "↩ هيرجع", "السعر"]

    def __init__(self, service: PurchaseReturnService, parent: QWidget | None = None, *,
                 suppliers: list[dict], supplier_id: int | None, lock_supplier: bool, stock_kind: str,
                 currency_code: str | None, exclude_return_id: int | None, used: dict[int, Decimal]) -> None:
        super().__init__(parent)
        self.service = service
        self.kind = stock_kind
        self.currency = currency_code                  # None = لسه مفيش مورد ← أي عملة (وكل المختار عملة واحدة)
        self.exclude_id = exclude_return_id
        self.used = used                               # اللي في المرتجع المفتوح من كل سطر فاتورة (بالأساسية)
        self.page = 1
        self.picks: dict[int, tuple[dict, Decimal]] = {}   # سطر الفاتورة ← (السطر، الكمية بوحدته)
        self.result: list[dict] = []
        self.result_supplier: int | None = None
        self.result_currency: str | None = None
        self._lines: list[dict] = []
        self._loading = False
        self.setWindowTitle("📋 فواتير المشتريات — اختار الأصناف الراجعة")
        self.setLayoutDirection(Qt.RightToLeft)
        self.setStyleSheet(QSS)
        self.resize(1100, 680)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.supplier = QComboBox()
        self.supplier.addItem("كل الموردين", None)
        for s in suppliers:
            if s["is_active"] or s["id"] == supplier_id:
                self.supplier.addItem(f"{s['code']} — {s['name_ar']}", int(s["id"]))
        index = self.supplier.findData(supplier_id)
        self.supplier.setCurrentIndex(max(index, 0))
        self.supplier.setEnabled(not lock_supplier)
        if lock_supplier:
            self.supplier.setToolTip("المرتجع فيه أصناف من فواتير المورد ده — المورد مايتغيّرش")
        today = dt.date.today()
        self.date_from, self.date_to = _date_edit(), _date_edit()
        self.date_from.setDate(_qdate(today.replace(month=1, day=1)))
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("🔍 رقم الفاتورة أو رقمها عند المورد أو الصنف…")
        for w, label in ((self.supplier, "المورد"), (self.date_from, "من"), (self.date_to, "إلى")):
            top.addWidget(QLabel(label))
            top.addWidget(w, 2 if w is self.supplier else 0)
        top.addWidget(self.keyword, 2)
        layout.addLayout(top)

        split = QSplitter(Qt.Vertical)
        upper = QWidget()
        ul = QVBoxLayout(upper)
        ul.setContentsMargins(0, 0, 0, 0)
        cap = QLabel("🧾 الفواتير (المرحّلة اللي لسه فيها حاجة ترجع) — اضغط على فاتورة تظهر تفاصيلها تحت")
        cap.setObjectName("sectionTitle")
        ul.addWidget(cap)
        self.invoices = QTableWidget(0, len(self.INV_HEADERS))
        self.invoices.setHorizontalHeaderLabels(self.INV_HEADERS)
        self._table_look(self.invoices, stretch=2)
        self.invoices.currentCellChanged.connect(lambda r, _c, _pr, _pc: self._show_invoice(r))
        ul.addWidget(self.invoices, 1)
        pager = QHBoxLayout()
        self.count = QLabel("")
        self.count.setObjectName("infoLabel")
        pager.addWidget(self.count, 1)
        self.prev_button = QPushButton("→ السابق")
        self.next_button = QPushButton("التالي ←")
        for b in (self.prev_button, self.next_button):
            b.setStyleSheet(button_qss("white"))
            pager.addWidget(b)
        ul.addLayout(pager)
        split.addWidget(upper)
        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 0, 0, 0)
        self.detail_title = QLabel("☰ تفاصيل الفاتورة")
        self.detail_title.setObjectName("sectionTitle")
        ll.addWidget(self.detail_title)
        self.lines = QTableWidget(0, len(self.LINE_HEADERS))
        self.lines.setHorizontalHeaderLabels(self.LINE_HEADERS)
        self._table_look(self.lines, stretch=self.L_NAME)
        self.lines.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.lines.itemChanged.connect(self._on_line_item)
        ll.addWidget(self.lines, 1)
        self.hint = QLabel("✔ = يرجع الفاضل كله · أو اكتب في «هيرجع» كمية أقل (بوحدة الفاتورة) — الاختيار بيفضل "
                           "محفوظ لو اتنقلت لفاتورة تانية")
        self.hint.setObjectName("infoLabel")
        self.hint.setWordWrap(True)
        ll.addWidget(self.hint)
        split.addWidget(lower)
        split.setSizes([300, 320])
        layout.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.picked_label = QLabel("")
        self.picked_label.setStyleSheet("font-weight:800; color:#137A38;")
        bottom.addWidget(self.picked_label, 1)
        self.all_button = QPushButton("☑ كل أصناف الفاتورة")
        self.all_button.setStyleSheet(button_qss("white"))
        self.add_button = QPushButton("＋ إضافة المختار للمرتجع")
        self.add_button.setStyleSheet(button_qss("green"))
        close = QPushButton("إغلاق")
        close.setStyleSheet(button_qss("gray"))
        for b in (self.all_button, self.add_button, close):
            b.setFixedHeight(34)
            bottom.addWidget(b)
        layout.addLayout(bottom)
        self.prev_button.clicked.connect(lambda: self._go(self.page - 1))
        self.next_button.clicked.connect(lambda: self._go(self.page + 1))
        self.all_button.clicked.connect(self.pick_all)
        self.add_button.clicked.connect(self.apply)
        close.clicked.connect(self.reject)
        self.keyword.textChanged.connect(lambda _t: self._go(1))
        for w in (self.date_from, self.date_to):
            w.dateChanged.connect(lambda _d: self._go(1))
        self.supplier.currentIndexChanged.connect(lambda _i: self._go(1))
        self._rows: list[dict] = []
        self._go(1)
        self._update_picked()

    @staticmethod
    def _table_look(table: QTableWidget, stretch: int) -> None:
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(stretch, QHeaderView.Stretch)

    # ---- الفواتير ---------------------------------------------------------------
    def _args(self) -> dict[str, Any]:
        return {"supplier_id": self.supplier.currentData(), "stock_kind": self.kind,
                "currency_code": self.currency, "date_from": _pydate(self.date_from.date()),
                "date_to": _pydate(self.date_to.date()), "keyword": self.keyword.text().strip(),
                "exclude_return_id": self.exclude_id}

    def _go(self, page: int) -> None:
        try:
            total = self.service.count_returnable_invoices(**self._args())
            pages = max(1, -(-total // PAGE_SIZE))
            self.page = min(max(1, page), pages)
            self._rows = self.service.returnable_invoices(**self._args(), page=self.page)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الفواتير", exc)
            total, pages, self._rows = 0, 1, []
        t = self.invoices
        t.blockSignals(True)
        t.setRowCount(len(self._rows))
        for r, row in enumerate(self._rows):
            cur = row["currency_code"] if row["currency_mode"] == "single" else "متعدد"
            values = [row["invoice_no"], f"{row['invoice_date']:%d-%m-%Y}", row["supplier_name"],
                      row["supplier_invoice_no"] or "—", "فاتورة" if row["stock_kind"] == "invoice" else "بيان", cur,
                      money(row["total_amount"]), f"{row['remaining_lines']} من {row['line_count']}"]
            for c, value in enumerate(values):
                t.setItem(r, c, _cell(str(value), number=c == 6, bold=c == 0))
        t.blockSignals(False)
        self.count.setText(f"{total} فاتورة · صفحة {self.page} من {pages}")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < pages)
        if self._rows:
            t.selectRow(0)
            self._show_invoice(0)
        else:
            self._show_invoice(-1)

    def _show_invoice(self, row: int) -> None:
        if not 0 <= row < len(self._rows):
            self._lines = []
            self.detail_title.setText("☰ تفاصيل الفاتورة — مفيش فواتير بالفلتر ده")
            self.lines.setRowCount(0)
            return
        inv = self._rows[row]
        try:
            self._lines = self.service.invoice_lines(int(inv["id"]), self.exclude_id)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل التفاصيل", exc)
            self._lines = []
        self.detail_title.setText(f"☰ تفاصيل الفاتورة {inv['invoice_no']} — {inv['supplier_name']} · "
                                  f"{inv['invoice_date']:%d-%m-%Y}")
        self._loading = True
        t = self.lines
        t.setRowCount(len(self._lines))
        for r, ln in enumerate(self._lines):
            factor = Decimal(ln["factor"])
            left = self._left(ln)
            ok = left > 0 and (self.currency is None or ln["currency_code"] == self.currency)
            pick = self.picks.get(int(ln["id"]))
            check = QTableWidgetItem("")
            check.setFlags((Qt.ItemIsUserCheckable | Qt.ItemIsEnabled) if ok else Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if pick else Qt.Unchecked)
            t.setItem(r, self.L_CHECK, check)
            cells = [(self.L_CODE, ln["item_code"]), (self.L_NAME, ln["item_name"] + (" (خدمي)" if ln["is_service"] else "")),
                     (self.L_WH, ln["warehouse_name"] or "—"), (self.L_UNIT, ln["unit_name"]),
                     (self.L_BOUGHT, fmt_qty(Decimal(ln["qty"]))),
                     (self.L_BEFORE, fmt_qty(Decimal(ln["returned_qty"] or 0) / factor)),
                     (self.L_LEFT, fmt_qty(left / factor)),
                     (self.L_PRICE, f"{money(ln['source_price'])} {ln['currency_code']}")]
            for c, text in cells:
                t.setItem(r, c, _cell(str(text), number=c in (self.L_BOUGHT, self.L_BEFORE, self.L_LEFT, self.L_PRICE),
                                      bold=c == self.L_LEFT, color=None if ok else "#94A3B8"))
            qty = QTableWidgetItem(num(pick[1]) if pick else "")
            qty.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
            qty.setFlags((Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable) if ok else Qt.ItemIsSelectable)
            qty.setBackground(QColor("#FFF7ED"))
            qty.setToolTip("الكمية بوحدة الفاتورة — الفاضي مع ✔ = الفاضل كله")
            t.setItem(r, self.L_QTY, qty)
            if not ok:
                t.item(r, self.L_NAME).setToolTip("مفيش حاجة فاضلة ترجع" if left <= 0 else
                                                  f"عملة السطر {ln['currency_code']} غير عملة المرتجع {self.currency}")
        self._loading = False

    def _left(self, ln: dict) -> Decimal:
        return max(Decimal("0"), Decimal(ln["remaining_base"]) - self.used.get(int(ln["id"]), Decimal("0")))

    # ---- الاختيار --------------------------------------------------------------
    def _set_pick(self, ln: dict, qty: Decimal | None) -> str | None:
        """بيرجّع الغلط (لو فيه) — ``qty`` None = الفاضل كله · 0 = شيل الاختيار."""
        lid = int(ln["id"])
        factor = Decimal(ln["factor"])
        left = self._left(ln) / factor
        if qty is None:
            qty = left
        if qty <= 0:
            self.picks.pop(lid, None)
            return None
        if qty > left:
            return f"{ln['item_code']}: الفاضل {fmt_qty(left)} {ln['unit_name']} بس."
        others = {p[0]["supplier_id"] for p in self.picks.values() if int(p[0]["id"]) != lid}
        if others and ln["supplier_id"] not in others:
            return "المرتجع الواحد لمورد واحد — الأصناف المختارة من مورد تاني (شيلها الأول)."
        currencies = {p[0]["currency_code"] for p in self.picks.values() if int(p[0]["id"]) != lid}
        if currencies and ln["currency_code"] not in currencies:
            return "المرتجع الواحد بعملة واحدة — الأصناف المختارة بعملة تانية."
        self.picks[lid] = (ln, qty)
        return None

    def _on_line_item(self, item: QTableWidgetItem) -> None:
        if self._loading or not 0 <= item.row() < len(self._lines):
            return
        ln = self._lines[item.row()]
        error = None
        if item.column() == self.L_CHECK:
            error = self._set_pick(ln, None if item.checkState() == Qt.Checked else Decimal("0"))
        elif item.column() == self.L_QTY:
            text = item.text().strip()
            error = self._set_pick(ln, None if not text and self.lines.item(item.row(), self.L_CHECK).checkState()
                                   == Qt.Checked else _dec(text))
        if error:
            messages.warn(self, "اختيار الأصناف", error)
        # بعد ما الإشارة تخلص: الكمية والعلامة زي ما اتحفظوا
        QTimer.singleShot(0, lambda: (self._show_invoice(self.invoices.currentRow()), self._update_picked()))

    def pick_all(self) -> None:
        """«☑ كل أصناف الفاتورة»: كل اللي فاضل من الفاتورة المعروضة."""
        errors = [e for ln in self._lines if self._left(ln) > 0 and (self.currency is None or ln["currency_code"]
                                                                   == self.currency)
                  for e in [self._set_pick(ln, None)] if e]
        if errors:
            messages.warn(self, "اختيار الأصناف", errors[0])
        self._show_invoice(self.invoices.currentRow())
        self._update_picked()

    def _update_picked(self) -> None:
        invoices = sorted({p[0]["invoice_no"] for p in self.picks.values()})
        self.picked_label.setText(f"المختار: {len(self.picks)} صنف" + (f" من {'، '.join(invoices)}" if invoices else "")
                                  if self.picks else "ماختارتش أصناف لسه")
        self.add_button.setText(f"＋ إضافة المختار للمرتجع ({len(self.picks)})" if self.picks
                                else "＋ إضافة المختار للمرتجع")
        self.add_button.setEnabled(bool(self.picks))

    def apply(self) -> None:
        if not self.picks:
            return
        statement = self.kind == "statement"
        self.result = [{"source_line_id": int(ln["id"]), "item_id": int(ln["item_id"]), "unit_id": int(ln["unit_id"]),
                        "warehouse_id": ln["warehouse_id"], "qty": qty, "unit_price": Decimal(ln["source_price"]),
                        "tax_code_id": None if statement else ln["tax_code_id"], "invoice_no": ln["invoice_no"],
                        "supplier_id": ln["supplier_id"]} for ln, qty in self.picks.values()]
        first = next(iter(self.picks.values()))[0]
        self.result_supplier = int(first["supplier_id"])
        self.result_currency = first["currency_code"]
        self.accept()


class ReasonsDialog(QDialog):
    """«⚙ أسباب المرتجع»: إضافة · تعديل الاسم · إيقاف / تشغيل · مسح (اللي اتستعمل بيتوقف بس)."""

    def __init__(self, service: PurchaseReturnService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_id: int | None = None
        self.setWindowTitle("أسباب المرتجع")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(420, 420)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_pick)
        layout.addWidget(self.list, 1)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("اسم السبب")
        layout.addWidget(self.name_edit)
        self.active_box = QCheckBox("نشط")
        self.active_box.setChecked(True)
        layout.addWidget(self.active_box)
        row = QHBoxLayout()
        self.add_button = QPushButton("＋ إضافة")
        self.add_button.setStyleSheet(button_qss("green"))
        self.save_button = QPushButton("💾 حفظ التعديل")
        self.save_button.setStyleSheet(button_qss("blue"))
        self.delete_button = QPushButton("🗑 مسح")
        self.delete_button.setStyleSheet(button_qss("red"))
        close = QPushButton("إغلاق")
        close.setStyleSheet(button_qss("gray"))
        for b in (self.add_button, self.save_button, self.delete_button, close):
            row.addWidget(b)
        layout.addLayout(row)
        self.add_button.clicked.connect(self.add_reason)
        self.save_button.clicked.connect(self.save_reason)
        self.delete_button.clicked.connect(self.delete_reason)
        close.clicked.connect(self.accept)
        self._reload()

    def _reload(self, select: int | None = None) -> None:
        self.list.clear()
        for r in self.service.reasons():
            item = QListWidgetItem(r["name_ar"] + ("" if r["is_active"] else "  (موقوف)"))
            item.setData(Qt.UserRole, r)
            self.list.addItem(item)
            if r["id"] == select:
                self.list.setCurrentItem(item)

    def _current(self) -> dict | None:
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_pick(self, *_args: Any) -> None:
        row = self._current()
        if row:
            self.name_edit.setText(row["name_ar"])
            self.active_box.setChecked(bool(row["is_active"]))

    def add_reason(self) -> None:
        try:
            self.selected_id = self.service.save_reason(None, self.name_edit.text())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر الإضافة", exc)
            return
        self._reload(self.selected_id)

    def save_reason(self) -> None:
        row = self._current()
        if row is None:
            return
        try:
            self.service.save_reason(int(row["id"]), self.name_edit.text(), self.active_box.isChecked())
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر الحفظ", exc)
            return
        self._reload(int(row["id"]))

    def delete_reason(self) -> None:
        row = self._current()
        if row is None or not messages.ask_yes_no(self, "مسح السبب", f"مسح «{row['name_ar']}»؟"):
            return
        try:
            self.service.delete_reason(int(row["id"]))
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر المسح", exc)
            return
        self._reload()


class ReturnSearchDialog(QDialog):
    """البحث عن مرتجع (F1): فترة + حالة + كلمة (رقم / مورد / إشعار / صنف / فاتورة) + صفحات 50."""

    COLUMNS = [("return_no", "رقم المرتجع"), ("return_date", "التاريخ"), ("supplier_name", "المورد"),
               ("supplier_note_no", "رقم إشعار المورد"), ("total", "الإجمالي"), ("status", "الحالة")]

    def __init__(self, service: PurchaseReturnService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.selected_id: Any = None
        self.page = 1
        self.setWindowTitle("بحث — مرتجعات المشتريات")
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
        self.keyword.setPlaceholderText("🔍 رقم المرتجع أو المورد أو رقم الإشعار أو الصنف أو رقم الفاتورة…")
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
        self.keyword.textChanged.connect(lambda _t: self._go(1))
        for w in (self.date_from, self.date_to):
            w.dateChanged.connect(lambda _d: self._go(1))
        self.status.currentIndexChanged.connect(lambda _i: self._go(1))
        self._go(1)
        self.keyword.setFocus()

    @staticmethod
    def _format(key: str, value: Any, row: dict) -> str:
        if key == "return_no":
            return return_label(row)
        if key == "return_date":
            return value.strftime("%d-%m-%Y") if value else ""
        if key == "total":
            return f"{money(row['total_amount'])} {row['currency_code']}"
        if key == "status":
            return "مرحّل ✔" if value == "posted" else "مسودة"
        return "" if value is None else str(value)

    def _args(self) -> tuple:
        return (_pydate(self.date_from.date()), _pydate(self.date_to.date()), self.status.currentData(),
                self.keyword.text().strip())

    def _go(self, page: int) -> None:
        counts = self.service.count_returns(*self._args())
        pages = max(1, -(-counts["total"] // PAGE_SIZE))
        self.page = min(max(1, page), pages)
        rows = self.service.list_returns(*self._args(), page=self.page)
        self.table.set_rows(rows)
        if rows:
            self.table.select_index(0)
        self.count.setText(f"{counts['total']} مرتجع (مسودات {counts['drafts']} · مرحّلة {counts['posted']}) · "
                           f"صفحة {self.page} من {pages}")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < pages)

    def _choose(self) -> None:
        record_id = self.table.current_id()
        if record_id is None:
            return
        self.selected_id = record_id
        self.accept()


__all__ = ["PurchaseReturnsScreen", "InvoicePickerDialog", "ReasonsDialog", "ReturnSearchDialog", "fmt_rate"]
