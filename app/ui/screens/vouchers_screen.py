"""شاشتي «سند قبض» (للعميل) و«سند صرف» (للمورد) — النموذج 5 «الفورم + كروت الأرقام + القيد» (اختيار المستخدم 2026-11-17)
من غير خانات الخصومات (رد المستخدم: «شيلهم») · «سند القبض للعميل وسند الصرف للمورد» (قرار المستخدم).

```
┌ [سند جديد][تعديل][حفظ مسودة][ترحيل ✔][إلغاء الترحيل][حذف][إلغاء][تحديث][تصدير إكسل][خروج]          (عرض) ┐
├──────────────────────────────────────────────┬──────────────────────────────────┤
│ ┌ سند قبض ┐ الرقم · التاريخ · الحالة                 │ ┌ السندات ┐ 🔍 رقم / عميل / بيان     │
│   استلمنا من [العميل] / صرفنا إلى [المورد]           │   من [..] إلى [..]                 │
│   المبلغ · البيان · رقم الدفتر · أمر التوريد          │   [الكل][مسودة][مرحّل]              │
│ [المبلغ] [رصيد العميل/المورد ← بعده] [رصيد الخزنة]   │   الرقم│التاريخ│الطرف│المبلغ│الحالة  │
│ ⚠️ الخزنة مش مكفية (الصرف — تحذير بس)                │                                    │
│ ┌ 📒 القيد اللي هيتعمل ┐ الحساب│مدين│دائن  ✔ متوازن   │   › السابق  صفحة 1 من 3  التالي ‹     │
└──────────────────────────────────────────────┴──────────────────────────────────┘
```
* «حفظ مسودة» · «ترحيل» (وانت بتعدّل = حفظ + ترحيل) · «إلغاء الترحيل» بالصلاحية — زي القيود اليومية.
* الرقم RV / PV بيتاخد مع أول ترحيل · الطباعة بعدين (رد المستخدم).

**«سداد الموردين» (المرحلة 7 — 0050) + «تحصيل العملاء» (المرحلة 8 — 0054، قرار المستخدم 2026-11-28: «نفس الفكرة
اللي عملناها في سند الصرف هننفذها في سند القبض») — السندين نفس الشكل:**

* **«عملة السند»** (بتتختار الأول) + سعر الصرف · «صرف من» بيعرض **الخزن / الحسابات البنكية اللي بنفس العملة بس**.
* **أيقونة 🔍** جنب العملة والمورد / العميل والخزنة / البنك ← نافذة تختار منها (طلب المستخدم).
* تبويب **«🧾 التوزيع على الفواتير»**: «📋 الفواتير» (نافذة بفواتير المورد / العميل المفتوحة **بعملة السند —
  بالتوتال**: الإجمالي − المرتجع − المسدد / المحصّل) · «⚡ وزّع الأقدم الأول» · تحت السند سطور اللي اتوزّع عليه بس ·
  اللي مااتوزّعش = دفعة مقدمة. والقيد في تبويب «📒 القيد».
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.services.voucher_service import KINDS, PAGE_SIZE, VoucherService, voucher_label
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.journal_service import parse_money
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, STATUS_BADGES, ltr, money

OK_COLOR, BAD, MUTED, DRAFT_COLOR, BLUE = "#087443", "#B91C1C", "#64748B", "#92400E", "#1D4ED8"
KIND_COLORS = {"receipt": "#137A38", "payment": "#B91C1C"}
STATUS_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("posted", "مرحّل"))
KPI_STYLE = ("QFrame#kpi {{ background:#FFFFFF; border:1px solid #E2E8F0; border-right:4px solid {color}; "
             "border-radius:7px; }}")
VALUE_STYLE = "font-size:14px; font-weight:900; border:none;"
WARN_STYLE = ("color:#9A3412; background:#FFF7ED; border:1px solid #FED7AA; border-radius:6px; padding:6px 10px; "
              "font-weight:900;")
LOOKUP_STYLE = ("QPushButton { background:#137A38; color:#FFFFFF; border:none; border-radius:6px; font-weight:800; }"
                "QPushButton:hover { background:#0F6B30; } QPushButton:disabled { background:#CBD5E1; }")
ALLOC_BUTTON = ("QPushButton {{ background:{bg}; color:{fg}; border:1px solid {bd}; border-radius:6px; "
                "padding:5px 12px; font-weight:800; }} QPushButton:hover {{ background:{hover}; }}"
                "QPushButton:disabled {{ background:#EAECEF; color:#AEB6C0; border-color:#EAECEF; }}")
GREEN_BUTTON = ALLOC_BUTTON.format(bg="#137A38", fg="#FFFFFF", bd="#137A38", hover="#0F6B30")
OUTLINE_BUTTON = ALLOC_BUTTON.format(bg="#FFFFFF", fg="#137A38", bd="#137A38", hover="#E7F6EE")
WHITE_BUTTON = ALLOC_BUTTON.format(bg="#FFFFFF", fg="#374151", bd="#CBD5E1", hover="#F3F4F6")
PAY_BG = "#FFF7ED"
ROW_HEIGHT = 32          # ارتفاع خانات الفورم
ROW_GAP = 10             # المسافة بين سطور الفورم
MID_LABEL = 74           # عرض عنوان الخانة التانية في السطر (سند الصرف)


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


def amount_of(text: Any) -> Decimal:
    """المبلغ المكتوب (الغلط أو الفاضي = صفر — للعرض بس)."""
    try:
        return parse_money(text, "المبلغ")
    except BusinessError:
        return Decimal("0")


def rate_text(rate: Any) -> str:
    """سعر الصرف من غير أصفار زيادة: 48.500000 ← 48.5."""
    if rate in (None, ""):
        return ""
    text = format(Decimal(str(rate)).normalize(), "f")
    return text if "." not in text else text.rstrip("0").rstrip(".") or "0"


def _day(value: Any) -> str:
    return f"{value:%d-%m-%Y}" if value else "—"


def _num_item(text: str, *, bold: bool = False, color: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    if color:
        item.setForeground(QColor(color))
    return item


def _text_item(text: str, *, bold: bool = False, color: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    if bold:
        font = item.font()
        font.setBold(True)
        item.setFont(font)
    if color:
        item.setForeground(QColor(color))
    return item


def signed(value: Decimal | None, natural: str = "مدين") -> str:
    """الرصيد بجنبه: «1,200.00 مدين» / «300.00 دائن» / «0.00».

    ``natural`` = الجنب الطبيعي للرصيد الموجب (المورد رصيده دائن: الموجب = ليه علينا)."""
    if value is None:
        return "—"
    if value == 0:
        return "0.00"
    other = "دائن" if natural == "مدين" else "مدين"
    return f"{ltr(money(abs(value)))} {natural if value > 0 else other}"


class _ColumnLayout(QVBoxLayout):
    """عمود الفورم جوه الـ QScrollArea: من غير «الارتفاع حسب العرض» — العناوين اللي بتلف سطرين كانت بتخلّي
    الـ QScrollArea ياخد الارتفاع المفضّل كله ويطلّع سكرول والشاشة لسه فيها مكان."""

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt
        return False


(A_NO, A_DUE, A_TOTAL, A_RETURNED, A_PAID, A_LEFT, A_PAY, A_AFTER, A_DROP) = range(9)
ALLOC_HEADERS = ["الفاتورة", "الاستحقاق", "الإجمالي", "المرتجع", "مسدد قبل كده", "المتبقي", "يدفع", "الباقي بعد", ""]
RECEIPT_ALLOC_HEADERS = ["الفاتورة", "الاستحقاق", "الإجمالي", "المرتجع", "محصّل قبل كده", "المتبقي", "يحصّل",
                         "الباقي بعد", ""]


class InvoiceDuesDialog(QDialog):
    """«📋 الفواتير» (طلب المستخدم 2026-11-28): فواتير المورد (سند الصرف) أو العميل (سند القبض) الآجلة المرحّلة اللي
    لسه عليها متبقي **بعملة السند** — **بالتوتال بس** (الإجمالي − المرتجع − المسدد / المحصّل = المتبقي). ✔ = المتبقي
    كله (أو اللي فاضل من مبلغ السند) · أو اكتب في «يدفع / يحصّل» · الاختيار بيفضل محفوظ بين الصفحات ·
    «⚡ وزّع الأقدم الأول» بيملاه لوحده."""

    (C_CHECK, C_NO, C_DATE, C_DUE, C_SUP_NO, C_KIND, C_TOTAL, C_RETURNED, C_PAID, C_LEFT, C_PAY) = range(11)
    HEADERS = ["✔", "رقم الفاتورة", "التاريخ", "الاستحقاق", "رقم فاتورة المورد", "النوع", "الإجمالي", "المرتجع",
               "المسدد", "المتبقي", "يدفع"]

    def __init__(self, service: VoucherService, parent: QWidget | None = None, *, supplier_id: int,
                 supplier_name: str, currency_code: str, amount: Decimal, picks: dict[int, Decimal]) -> None:
        super().__init__(parent)
        self.service = service
        self.supplier_id = supplier_id                   # الطرف: المورد (سند الصرف) أو العميل (سند القبض)
        self.receipt = service.kind.code == "receipt"
        self.key = service.ik                            # purchase_invoice_id / sales_invoice_id
        self.currency = currency_code
        self.amount = amount                             # مبلغ السند (صفر = لسه مااتكتبش ← السند = اللي هتختاره)
        self.picks: dict[int, Decimal] = dict(picks)
        self.result: list[dict[str, Any]] = []
        self.page = 1
        self._rows: list[dict] = []
        self._loading = False
        self.setWindowTitle("📋 فواتير العميل المفتوحة — اختار اللي هتتحصّل" if self.receipt
                            else "📋 فواتير المورد المفتوحة — اختار اللي هتتسدد")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(1120, 560)
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel(f"{'👤' if self.receipt else '🏭'} {supplier_name}")
        title.setStyleSheet("font-weight:900; color:#137A38; font-size:14px;")
        top.addWidget(title, 2)
        cur_label = QLabel(f"عملة السند: {currency_code}")
        cur_label.setStyleSheet("font-weight:900; color:#1D4ED8; font-size:13px;")
        top.addWidget(cur_label)
        self.keyword = QLineEdit()
        self.keyword.setPlaceholderText("🔍 رقم الفاتورة أو رقم أمر الشراء عند العميل…" if self.receipt
                                        else "🔍 رقم الفاتورة أو رقمها عند المورد…")
        self.keyword.setClearButtonEnabled(True)
        top.addWidget(self.keyword, 1)
        layout.addLayout(top)
        note = QLabel(f"الفواتير الآجلة المرحّلة اللي عليها متبقي بعملة {currency_code} بس — كل فاتورة "
                      f"{'بتتحصّل' if self.receipt else 'بتتسدد'} بعملتها (المرتجع بينقص من إجمالي الفاتورة).")
        note.setStyleSheet(f"color:{MUTED}; font-weight:700;")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, len(self.HEADERS))
        headers = list(self.HEADERS)
        if self.receipt:
            headers[self.C_SUP_NO], headers[self.C_PAID], headers[self.C_PAY] = "أمر شراء العميل", "المحصّل", "يحصّل"
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)          # رقم الفاتورة باين كامل دايماً
        header.setSectionResizeMode(self.C_SUP_NO, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._on_item)
        layout.addWidget(self.table, 1)
        pager = QHBoxLayout()
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        pager.addWidget(self.count, 1)
        self.prev_button = QPushButton("→ السابق")
        self.next_button = QPushButton("التالي ←")
        for b in (self.prev_button, self.next_button):
            b.setStyleSheet(WHITE_BUTTON)
            pager.addWidget(b)
        layout.addLayout(pager)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        bottom = QHBoxLayout()
        self.auto_button = QPushButton("⚡ وزّع الأقدم الأول")
        self.auto_button.setStyleSheet(OUTLINE_BUTTON)
        self.auto_button.setEnabled(amount > 0)
        self.auto_button.setToolTip("مبلغ السند بيتوزّع على الفواتير بتاريخ الاستحقاق" if amount > 0
                                    else "اكتب مبلغ السند الأول (أو علّم على الفواتير والمبلغ يتحسب لوحده)")
        self.clear_button = QPushButton("↺ صفّر")
        self.clear_button.setStyleSheet(WHITE_BUTTON)
        self.ok_button = QPushButton("✔ تمام")
        self.ok_button.setStyleSheet(GREEN_BUTTON)
        close = QPushButton("إغلاق")
        close.setStyleSheet(WHITE_BUTTON)
        bottom.addWidget(self.auto_button)
        bottom.addWidget(self.clear_button)
        bottom.addStretch(1)
        bottom.addWidget(self.ok_button)
        bottom.addWidget(close)
        layout.addLayout(bottom)
        self.prev_button.clicked.connect(lambda: self._go(self.page - 1))
        self.next_button.clicked.connect(lambda: self._go(self.page + 1))
        self.keyword.textChanged.connect(lambda _t: self._go(1))
        self.auto_button.clicked.connect(self.auto_allocate)
        self.clear_button.clicked.connect(self.clear)
        self.ok_button.clicked.connect(self.apply)
        close.clicked.connect(self.reject)
        self._go(1)

    # ---- التحميل ---------------------------------------------------------------
    def _go(self, page: int) -> None:
        keyword = self.keyword.text().strip()
        try:
            counts = self.service.count_open_invoices(self.supplier_id, self.currency, keyword)
            pages = max(1, -(-counts["total"] // PAGE_SIZE))
            self.page = min(max(1, page), pages)
            self._rows = self.service.open_invoices(self.supplier_id, self.currency, keyword, page=self.page)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الفواتير", exc)
            counts, pages, self._rows = {"total": 0, "remaining": Decimal("0")}, 1, []
        self.count.setText(f"{counts['total']} فاتورة مفتوحة · إجمالي المتبقي {ltr(money(counts['remaining']))} "
                           f"{self.currency} · صفحة {self.page} من {pages}")
        self.prev_button.setEnabled(self.page > 1)
        self.next_button.setEnabled(self.page < pages)
        self._fill()

    def _fill(self) -> None:
        self._loading = True
        t = self.table
        t.setRowCount(len(self._rows))
        today = dt.date.today()
        for r, row in enumerate(self._rows):
            iid = int(row["invoice_id"])
            pick = self.picks.get(iid)
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked if pick else Qt.Unchecked)
            t.setItem(r, self.C_CHECK, check)
            late = row["due_date"] is not None and row["due_date"] < today
            t.setItem(r, self.C_NO, _text_item(row["invoice_no"], bold=True))
            t.setItem(r, self.C_DATE, _text_item(_day(row["invoice_date"])))
            due = _text_item(_day(row["due_date"]) + (f"  (متأخرة {(today - row['due_date']).days} يوم)" if late else ""),
                             color=BAD if late else None)
            t.setItem(r, self.C_DUE, due)
            t.setItem(r, self.C_SUP_NO, _text_item(row.get("ref_no") or "—"))
            kind = "فاتورة" if row["stock_kind"] == "invoice" else "بيان"
            t.setItem(r, self.C_KIND, _text_item(kind + (" · متعددة العملات" if row["currency_mode"] == "multi" else "")))
            t.setItem(r, self.C_TOTAL, _num_item(money(row["total_amount"])))
            t.setItem(r, self.C_RETURNED, _num_item(money(row["returned_amount"]) if row["returned_amount"] else "—"))
            t.setItem(r, self.C_PAID, _num_item(money(row["paid_amount"]) if row["paid_amount"] else "—"))
            t.setItem(r, self.C_LEFT, _num_item(money(row["remaining_amount"]), bold=True))
            pay = QTableWidgetItem(money(pick) if pick else "")
            pay.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
            pay.setFlags(Qt.ItemIsEditable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            pay.setBackground(QColor(PAY_BG))
            pay.setToolTip(f"المبلغ اللي {'هيتحصّل' if self.receipt else 'هيتدفع'} على الفاتورة دي — "
                           "الفاضي مع ✔ = المتبقي كله")
            t.setItem(r, self.C_PAY, pay)
        self._loading = False
        self._update_summary()

    # ---- الاختيار --------------------------------------------------------------
    def _picked_total(self, without: int | None = None) -> Decimal:
        return sum((v for k, v in self.picks.items() if k != without), Decimal("0"))

    def _on_item(self, item: QTableWidgetItem) -> None:
        if self._loading or not 0 <= item.row() < len(self._rows):
            return
        row = self._rows[item.row()]
        iid = int(row["invoice_id"])
        remaining = Decimal(row["remaining_amount"])
        error = ""
        if item.column() == self.C_CHECK:
            if item.checkState() == Qt.Checked:
                take = remaining
                if self.amount > 0:                         # مايعدّيش اللي فاضل من مبلغ السند
                    left = self.amount - self._picked_total(iid)
                    take = min(remaining, left)
                    if take <= 0:
                        error = "مبلغ السند اتوزّع كله — كبّر المبلغ أو قلّل من فاتورة تانية."
                if not error:
                    self.picks[iid] = take
            else:
                self.picks.pop(iid, None)
        elif item.column() == self.C_PAY:
            value = amount_of(item.text())
            if value <= 0:
                self.picks.pop(iid, None)
            elif value > remaining:
                error = f"{row['invoice_no']}: المتبقي عليها {money(remaining)} {self.currency} بس."
            else:
                self.picks[iid] = value
        else:
            return
        self._fill()
        if error:
            self.summary.setText(f"✖ {error}")
            self.summary.setStyleSheet(f"color:{BAD}; font-weight:900;")

    def _update_summary(self) -> None:
        total = self._picked_total()
        verb = "هيتحصّل" if self.receipt else "هيتدفع"
        text = f"اتعلّم على {len(self.picks)} فاتورة · {verb} {ltr(money(total))} {self.currency}"
        bad = False
        if self.amount > 0:
            rest = self.amount - total
            text += f" · مبلغ السند {ltr(money(self.amount))}"
            if rest < 0:
                text += f" · ✖ أكتر من السند بـ {ltr(money(-rest))}"
                bad = True
            elif rest > 0:
                text += f" · الباقي {ltr(money(rest))} دفعة مقدمة"
        else:
            text += " · مبلغ السند هيتحسب من اللي اخترته"
        self.summary.setText(text)
        self.summary.setStyleSheet(f"color:{BAD if bad else OK_COLOR}; font-weight:900;")
        self.ok_button.setText(f"✔ تمام ({len(self.picks)})" if self.picks else "✔ تمام")
        self.ok_button.setEnabled(not bad)

    def auto_allocate(self) -> None:
        try:
            picks = self.service.auto_allocate(self.supplier_id, self.currency, self.amount)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التوزيع", exc)
            return
        self.picks = {int(p[self.key]): Decimal(p["amount"]) for p in picks}
        self._fill()

    def clear(self) -> None:
        self.picks = {}
        self._fill()

    def apply(self) -> None:
        self.result = [{self.key: k, "amount": v} for k, v in self.picks.items() if v > 0]
        self.accept()


class VoucherScreen(BaseScreen):
    MODULE = "cash"
    KIND = "receipt"
    NEW_TEXT = "سند جديد"
    LIST_TITLE = "السندات"
    SEARCH_PLACEHOLDER = "🔍 رقم السند أو العميل أو البيان… (F1)"
    COLUMNS = [("voucher_no", "رقم السند"), ("voucher_date", "التاريخ"), ("party_name", "العميل"),
               ("amount", "المبلغ"), ("status", "الحالة")]
    FORM_STRETCH, LIST_STRETCH = 12, 10
    EXIT_LAST = True

    def __init__(self, service: VoucherService | None = None, parent: QWidget | None = None) -> None:
        self.kind = KINDS[self.KIND]
        self.service = service or VoucherService(self.KIND)
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._choices: dict[str, Any] = self._no_choices()
        self._filling = False
        self.is_payment = self.KIND == "payment"
        self._allocs: list[dict[str, Any]] = []        # توزيع السند على الفواتير (بالتفاصيل للعرض)
        self._rate_touched = False                     # المستخدم كتب سعر الصرف بإيده
        self._currency_touched = False                 # المستخدم اختار العملة بإيده
        today = dt.date.today()
        self._from = today.replace(day=1)
        self._to = (self._from + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_side)
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.count_label.parentWidget().layout().addLayout(self.paging)
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def build_form(self, box: QGroupBox) -> None:
        color = KIND_COLORS[self.KIND]
        box.setStyleSheet(f"QGroupBox#formBox {{ border-top:3px solid {color}; }}"
                          f"QGroupBox#formBox::title {{ color:{color}; }}")
        form = QFormLayout(box)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(ROW_GAP)                  # المستخدم: «البنود لازقة في بعضها» ← مسافة واضحة بين السطور
        form.setContentsMargins(14, 20, 14, 12)
        # ---- الخانات ----
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip(f"الرقم الرسمي ({self.kind.prefix}-2026-00001) بيتاخد مع أول ترحيل — "
                                    "والمسودة ليها رقم مؤقت")
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("dd/MM/yyyy")
        self.date_edit.setLayoutDirection(Qt.LeftToRight)
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.status_label = QLabel("")
        # العملة الأول (طلب المستخدم) + سعر الصرف
        self.currency_edit = LookupEdit("اختار عملة السند… *")
        self.currency_edit.value_changed.connect(self._on_currency)
        self.currency_find = self._lookup_button(self._find_currency, "اختار العملة من القايمة")
        self.rate_edit = QLineEdit()
        self.rate_edit.setLayoutDirection(Qt.LeftToRight)
        self.rate_edit.setPlaceholderText("سعر الصرف")
        self.rate_edit.setToolTip("سعر العملة بالجنيه بتاريخ السند (من جدول أسعار الصرف — وتقدر تعدّله)")
        self.rate_edit.textEdited.connect(self._on_rate_edited)
        self.rate_label = QLabel("السعر")
        self.date_edit.dateChanged.connect(self._on_date)
        self.party_edit = LookupEdit(f"اكتب كود {self.kind.party_label} أو اسمه… *")
        self.party_edit.value_changed.connect(self._on_party)
        self.party_find = self._lookup_button(self._find_party, f"اختار {self.kind.party_label} من القايمة")
        self.source_kind = QComboBox()
        self.source_kind.addItem("💵 خزنة", "treasury")
        self.source_kind.addItem("🏦 بنك", "bank")
        self.source_kind.currentIndexChanged.connect(self._on_source_kind)
        self.source_edit = LookupEdit("اختار الخزنة… *")
        self.source_edit.value_changed.connect(self._changed)
        self.source_find = self._lookup_button(self._find_source, "اختار الخزنة أو الحساب البنكي من القايمة")
        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        self.amount_edit.textChanged.connect(self._changed)
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("السند ده عن إيه (زي: تحصيل دفعة من الحساب)" if self.KIND == "receipt"
                                            else "السند ده عن إيه (زي: سداد دفعة من حساب المورد)")
        self.paper_no = QLineEdit()
        self.paper_no.setMaxLength(30)
        self.paper_no.setPlaceholderText("اختياري")
        self.project_edit = LookupEdit("اختياري")
        self.project_edit.value_changed.connect(self._changed)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        # كل خانة بارتفاع ثابت — الفورم مايتضغطش لما الشاشة تبقى صغيرة (العمود كله بيتحرّك بسكرول).
        for widget in (self.number_edit, self.date_edit, self.party_edit, self.source_kind, self.source_edit,
                       self.amount_edit, self.description, self.paper_no, self.project_edit,
                       self.currency_edit, self.rate_edit):
            widget.setMinimumHeight(ROW_HEIGHT)

        # ---- الترتيب ----
        def row(*items: Any) -> QHBoxLayout:
            """سطر: ``(widget, stretch)`` أو widget أو نص (عنوان خانة)."""
            lay = QHBoxLayout()
            lay.setSpacing(8)
            for it in items:
                if isinstance(it, str):
                    lay.addWidget(QLabel(it))
                elif isinstance(it, tuple):
                    lay.addWidget(it[0], it[1])
                else:
                    lay.addWidget(it)
            return lay

        def halves(right: QHBoxLayout, left: QHBoxLayout, title: str) -> QHBoxLayout:
            """خانتين في السطر: النص اليمين (تبع عنوان السطر) + عنوان وخانة النص الشمال — النصّين بنفس العرض."""
            label = QLabel(title)
            label.setMinimumWidth(MID_LABEL)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)       # RTL: جنب خانته
            left.insertWidget(0, label)
            both = QHBoxLayout()
            both.setSpacing(14)
            both.addLayout(right, 10)
            both.addLayout(left, 11)
            return both

        top = row((self.number_edit, 2), "التاريخ *", (self.date_edit, 1), "الحالة", self.status_label)
        form.addRow("رقم السند", top)
        party = row((self.party_edit, 1), self.party_find)
        self.source_kind.setMaximumWidth(96)
        src = row(self.source_kind, (self.source_edit, 1), self.source_find)
        # خانتين في السطر عشان التوزيع والقيد يبانوا من غير سكرول (العملة ← الطرف ← الخزنة / البنك).
        self.rate_edit.setMaximumWidth(92)
        cur = row((self.currency_edit, 1), self.currency_find, self.rate_label, self.rate_edit)
        form.addRow("عملة السند *", halves(cur, party, f"{self.kind.party_verb} *"))
        form.addRow(f"{self.kind.source_verb} *", halves(src, row((self.amount_edit, 1)), "المبلغ *"))
        form.addRow("البيان *", halves(row((self.description, 1)),
                                       row((self.paper_no, 2), "أمر التوريد", (self.project_edit, 3)),
                                       "رقم الدفتر"))
        form.addRow(self.year_note)

    def _lookup_button(self, slot, tip: str) -> QPushButton:
        """أيقونة البحث 🔍 جنب خانة الاختيار — بتفتح نافذة تختار منها (طلب المستخدم 2026-11-28)."""
        button = QPushButton("🔍")
        button.setFixedSize(34, ROW_HEIGHT)
        button.setCursor(Qt.PointingHandCursor)
        button.setStyleSheet(LOOKUP_STYLE)
        button.setToolTip(tip)
        button.clicked.connect(slot)
        return button

    def _build_allocations(self) -> QWidget:
        """تبويب «التوزيع على الفواتير»: الأزرار + سطور الفواتير اللي اتوزّع عليها السند + الملخص."""
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(8, 8, 8, 6)
        col.setSpacing(6)
        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.alloc_pick_button = QPushButton("📋 الفواتير")
        self.alloc_pick_button.setStyleSheet(GREEN_BUTTON)
        self.alloc_pick_button.setToolTip(f"فواتير {self.kind.party_label} المفتوحة بعملة السند (بالتوتال) — "
                                          "علّم واكتب المبلغ")
        self.alloc_pick_button.clicked.connect(self.pick_invoices)
        self.alloc_auto_button = QPushButton("⚡ وزّع الأقدم الأول")
        self.alloc_auto_button.setStyleSheet(OUTLINE_BUTTON)
        self.alloc_auto_button.setToolTip("مبلغ السند بيتوزّع على الفواتير بتاريخ الاستحقاق — وتقدر تعدّل بعده")
        self.alloc_auto_button.clicked.connect(self.auto_allocate)
        self.alloc_clear_button = QPushButton("↺ صفّر التوزيع")
        self.alloc_clear_button.setStyleSheet(WHITE_BUTTON)
        self.alloc_clear_button.clicked.connect(self.clear_allocations)
        for b in (self.alloc_pick_button, self.alloc_auto_button, self.alloc_clear_button):
            b.setCursor(Qt.PointingHandCursor)
            bar.addWidget(b)
        bar.addStretch(1)
        col.addLayout(bar)
        headers = ALLOC_HEADERS if self.is_payment else RECEIPT_ALLOC_HEADERS
        self.alloc_table = QTableWidget(0, len(headers))
        self.alloc_table.setHorizontalHeaderLabels(headers)
        self.alloc_table.verticalHeader().setVisible(False)
        self.alloc_table.verticalHeader().setDefaultSectionSize(28)
        self.alloc_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.alloc_table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        header = self.alloc_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(A_NO, QHeaderView.Stretch)
        self.alloc_table.setMinimumHeight(104)
        self.alloc_table.itemChanged.connect(self._on_alloc_item)
        self.alloc_table.cellClicked.connect(self._on_alloc_click)
        col.addWidget(self.alloc_table, 1)
        self.alloc_summary = QLabel("")
        self.alloc_summary.setWordWrap(True)
        col.addWidget(self.alloc_summary)
        return box

    def _build_kpis(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.kpi_values: dict[str, QLabel] = {}
        self.kpi_captions: dict[str, QLabel] = {}
        self.kpi_notes: dict[str, QLabel] = {}          # سند بعملة تانية: نفس الرقم بالعملة (للعلم — الأرصدة بالجنيه)
        cards = (("amount", "مبلغ السند", BLUE), ("party", f"رصيد {self.kind.party_label} ← بعد السند", "#7C3AED"),
                 ("source", "رصيد الخزنة ← بعد السند", OK_COLOR))
        for key, title, color in cards:
            card = QFrame()
            card.setObjectName("kpi")
            card.setStyleSheet(KPI_STYLE.format(color=color))
            col = QVBoxLayout(card)
            col.setContentsMargins(10, 6, 10, 6)
            col.setSpacing(1)
            caption = QLabel(title)
            caption.setStyleSheet(f"color:{MUTED}; font-weight:800; border:none;")
            value = QLabel("—")
            value.setStyleSheet(VALUE_STYLE)
            note = QLabel("")
            note.setStyleSheet(f"color:{BLUE}; font-weight:700; font-size:11px; border:none;")
            note.hide()
            col.addWidget(caption)
            col.addWidget(value)
            col.addWidget(note)
            row.addWidget(card, 1 if key == "amount" else 2)
            self.kpi_values[key] = value
            self.kpi_captions[key] = caption
            self.kpi_notes[key] = note
        return bar

    def _build_entry(self) -> QGroupBox:
        self.entry_box = QGroupBox("القيد اللي هيتعمل لما يترحّل")
        col = QVBoxLayout(self.entry_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.entry_table = QTableWidget(0, 3)
        self.entry_table.setHorizontalHeaderLabels(["الحساب", "مدين", "دائن"])
        self.entry_table.verticalHeader().setVisible(False)
        self.entry_table.verticalHeader().setDefaultSectionSize(26)
        self.entry_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.entry_table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.entry_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            self.entry_table.setColumnWidth(c, 130)
        self.entry_table.setMinimumHeight(110)                  # القيد في تبويب لوحده وبياخد المساحة كلها
        col.addWidget(self.entry_table)
        self.entry_label = QLabel("")
        self.entry_label.setWordWrap(True)
        col.addWidget(self.entry_label)
        return self.entry_box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = _ColumnLayout(holder)
        right.setContentsMargins(0, 0, 4, 0)
        right.setSpacing(10)
        right.addWidget(form_box)
        right.addWidget(self._build_kpis())
        self.funds_label = QLabel("")
        self.funds_label.setWordWrap(True)
        self.funds_label.setStyleSheet(WARN_STYLE)
        self.funds_label.hide()
        right.addWidget(self.funds_label)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_allocations(), "🧾 التوزيع على الفواتير")
        self.tabs.addTab(self._build_entry(), "📒 القيد")
        right.addWidget(self.tabs, 1)
        scroll = QScrollArea()                                      # الشاشة الصغيرة: سكرول بدل ما البنود تتلزّق
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background:transparent; }")
        holder.setObjectName("voucherHolder")
        holder.setStyleSheet("QWidget#voucherHolder { background:transparent; }")
        scroll.setWidget(holder)
        scroll.viewport().setAutoFillBackground(False)
        self.form_scroll = scroll
        content.addWidget(scroll, self.FORM_STRETCH)                # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)
        self._load_choices()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button("ترحيل ✔", "primary", self.post_voucher)
        self.post_button.setToolTip("بيعمل القيد ويرحّله وبياخد الرقم الرسمي — وانت بتعدّل = حفظ + ترحيل")
        self.unpost_button = self._button("إلغاء الترحيل", "lock", self.unpost_voucher)
        self.unpost_button.setToolTip("القيد بيتمسح والسند بيرجع مسودة برقمه ويتعدّل (السنة لازم مفتوحة)")
        self.post_button.setVisible(self.has_action("post"))
        self.unpost_button.setVisible(self.has_action("unpost"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.unpost_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        period = QHBoxLayout()
        self.from_edit, self.to_edit = QDateEdit(), QDateEdit()
        for edit, value in ((self.from_edit, self._from), (self.to_edit, self._to)):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("dd/MM/yyyy")
            edit.setLayoutDirection(Qt.LeftToRight)
            edit.setDate(_qdate(value))
            edit.dateChanged.connect(self._on_period)
        period.addWidget(QLabel("من"))
        period.addWidget(self.from_edit, 1)
        period.addWidget(QLabel("إلى"))
        period.addWidget(self.to_edit, 1)
        layout.addLayout(period)
        chips = QHBoxLayout()
        chips.setSpacing(5)
        self.status_buttons = QButtonGroup(self)
        self.status_buttons.setExclusive(True)
        for key, text in STATUS_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            self.status_buttons.addButton(chip)
            chips.addWidget(chip)
        self.status_buttons.buttonClicked.connect(self._on_status)
        chips.addStretch(1)
        layout.addLayout(chips)
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

    # ---- الاختيارات -----------------------------------------------------------
    @staticmethod
    def _no_choices() -> dict[str, Any]:
        return {"parties": [], "treasuries": [], "banks": [], "projects": [], "currencies": [], "base_currency": "EGP"}

    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «سند جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = self._no_choices()

    def _base(self) -> str:
        return self._choices.get("base_currency") or "EGP"

    def _currency(self) -> str:
        """عملة السند (الأساسية لحد ما تتختار)."""
        if hasattr(self, "currency_edit"):
            return self.currency_edit.currentData() or self._base()
        return self._base()

    def _currency_rows(self) -> list[tuple[str, str, str]]:
        return [(c["code"], c["code"], f"{c['code']} — {c['name_ar']}") for c in self._choices["currencies"]
                if c["is_active"]]

    def _party_rows(self) -> list[tuple[int, str, str]]:
        return [(c["id"], c["code"], f"{c['code']} — {c['name_ar']}") for c in self._choices["parties"]
                if c["is_active"]]

    def _source_rows(self, kind: str) -> list[tuple[int, str, str]]:
        """الخزن أو الحسابات البنكية **اللي بعملة السند بس** (البنك زي الخزنة بالظبط)."""
        cur = self._currency()
        if kind == "treasury":
            return [(t["id"], t["code"], f"{t['code']} — {t['name_ar']}") for t in self._choices["treasuries"]
                    if t["is_active"] and t["currency_code"] == cur]
        return [(b["id"], b["code"], f"{b['bank_name']} — {b['code']}" +
                 (f" ({b['account_number']})" if b.get("account_number") else ""))
                for b in self._choices["banks"] if b["is_active"] and b["currency_code"] == cur]

    def _project_rows(self) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name']}") for p in self._choices["projects"]
                if p["status"] == "open"]

    def _set_lookups(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
            code = rec.get("currency_code") or self._base()        # العملة الأول — الخزنة / البنك بيتفلتروا بيها
            self.currency_edit.set_items(self._currency_rows(), code, code)
            self.rate_edit.setText(rate_text(rec.get("fx_rate")) if rec.get("fx_rate") is not None else "1")
            self._rate_touched = self._currency_touched = False
            self.party_edit.set_items(self._party_rows(), rec.get("party_id"),
                                      f"{rec.get('party_code', '')} — {rec.get('party_name', '')}")
            kind = rec.get("source_kind") or "treasury"
            self.source_kind.setCurrentIndex(0 if kind == "treasury" else 1)
            self._fill_source(kind, rec)
            self.project_edit.set_items(self._project_rows(), rec.get("project_id"), rec.get("project_code"))
        finally:
            self._filling = False

    def _fill_source(self, kind: str, rec: dict | None = None) -> None:
        rec = rec or {}
        if kind == "treasury":
            value = rec.get("treasury_id")
            text = f"{rec.get('treasury_code', '')} — {rec.get('treasury_name', '')}"
            placeholder = "اختار الخزنة… *"
        else:
            value = rec.get("bank_account_id")
            text = f"{rec.get('bank_name', '')} — {rec.get('bank_code', '')}"
            placeholder = "اختار الحساب البنكي… *"
        self.source_edit.set_items(self._source_rows(kind), value, text, placeholder=placeholder)

    def _on_source_kind(self, *_args: Any) -> None:
        if self._filling:
            return
        kind = self.source_kind.currentData()
        rows = self._source_rows(kind)
        self._fill_source(kind)
        if self.mode in EDITING and rows:
            self.source_edit.set_value(rows[0][0])
        self.kpi_captions["source"].setText(f"رصيد {'الخزنة' if kind == 'treasury' else 'البنك'} ← بعد السند")
        self._changed()

    # ---- أيقونات البحث 🔍 -------------------------------------------------------
    def _pick_from(self, title: str, columns: list[tuple[str, str]], rows: list[dict]) -> Any:
        from app.ui.dialogs.lookup_dialog import LookupDialog

        dialog = LookupDialog(title, columns, rows, parent=self)
        return dialog.selected_id if dialog.exec() and dialog.selected_id is not None else None

    def _find_party(self) -> None:
        if self.mode not in EDITING:
            return
        rows = [{"id": c["id"], "code": c["code"], "name_ar": c["name_ar"], "currency_code": c.get("currency_code", "")}
                for c in self._choices["parties"] if c["is_active"]]
        columns = [("code", "الكود"), ("name_ar", "الاسم"), ("currency_code", "العملة")]
        picked = self._pick_from("الموردين" if self.is_payment else "العملاء", columns, rows)
        if picked is not None:
            self.party_edit.set_value(picked)

    def _find_currency(self) -> None:
        if self.mode not in EDITING:
            return
        rows = [{"id": c["code"], "code": c["code"], "name_ar": c["name_ar"],
                 "kind": "الأساسية" if c["is_base"] else ""} for c in self._choices["currencies"] if c["is_active"]]
        picked = self._pick_from("العملات", [("code", "الكود"), ("name_ar", "العملة"), ("kind", "")], rows)
        if picked is not None:
            self._currency_touched = True
            self.currency_edit.set_value(picked)

    def _find_source(self) -> None:
        """الخزن والحسابات البنكية اللي بعملة السند في قايمة واحدة (البنك زي الخزنة)."""
        if self.mode not in EDITING:
            return
        cur = self._currency()
        rows = [{"id": f"treasury:{t['id']}", "kind": "💵 خزنة", "code": t["code"], "name": t["name_ar"],
                 "currency_code": t["currency_code"]}
                for t in self._choices["treasuries"] if t["is_active"] and t["currency_code"] == cur]
        rows += [{"id": f"bank:{b['id']}", "kind": "🏦 بنك", "code": b["code"],
                  "name": f"{b['bank_name']}" + (f" ({b['account_number']})" if b.get("account_number") else ""),
                  "currency_code": b["currency_code"]}
                 for b in self._choices["banks"] if b["is_active"] and b["currency_code"] == cur]
        picked = self._pick_from(f"الخزن والبنوك ({cur})", [("kind", "النوع"), ("code", "الكود"), ("name", "الاسم"),
                                                           ("currency_code", "العملة")], rows)
        if picked is None:
            return
        kind, _sep, raw = str(picked).partition(":")
        self._filling = True
        try:
            self.source_kind.setCurrentIndex(0 if kind == "treasury" else 1)
            self._fill_source(kind)
        finally:
            self._filling = False
        self.source_edit.set_value(int(raw))
        self._changed()

    # ---- العملة وسعر الصرف ---------------------------------------------
    def _set_rate_from_table(self) -> None:
        cur = self._currency()
        if cur == self._base():
            self.rate_edit.setText("1")
        else:
            try:
                rate = self.service.rate_for(cur, _pydate(self.date_edit.date()))
            except Exception:  # noqa: BLE001 - السعر بيتكتب بالإيد
                rate = None
            self.rate_edit.setText(rate_text(rate))
        self._rate_touched = False
        self._sync_rate_enabled()

    def _sync_rate_enabled(self) -> None:
        foreign = self._currency() != self._base()
        self.rate_edit.setEnabled(self.mode in EDITING and foreign)
        self.rate_edit.setVisible(foreign)
        self.rate_label.setVisible(foreign)

    def _on_rate_edited(self, *_args: Any) -> None:
        self._rate_touched = True
        self._changed()

    def _on_date(self, *_args: Any) -> None:
        if self._filling or self.mode not in EDITING:
            return
        if not self._rate_touched:                               # سعر اليوم الجديد (لو المستخدم ماكتبش سعر)
            self._set_rate_from_table()
        self._changed()

    def _on_currency(self, *_args: Any) -> None:
        """العملة اتغيّرت ← سعرها بتاريخ السند · «صرف من» بخزن / بنوك العملة دي · التوزيع بيتصفّر."""
        if self._filling:
            return
        self._set_rate_from_table()
        kind = self.source_kind.currentData()
        rows = self._source_rows(kind)
        if not rows:                                            # مفيش خزنة بالعملة دي ← جرّب البنوك (والعكس)
            other = "bank" if kind == "treasury" else "treasury"
            if self._source_rows(other):
                kind, rows = other, self._source_rows(other)
        self._filling = True
        try:
            self.source_kind.setCurrentIndex(0 if kind == "treasury" else 1)
            self._fill_source(kind)
        finally:
            self._filling = False
        if self.mode in EDITING and rows:
            self.source_edit.set_value(rows[0][0])
        if self._allocs:
            self._allocs = []
            self._note = f"العملة اتغيّرت ← التوزيع اتصفّر (الفواتير بتتسدد بعملتها بس — اختار فواتير {self._currency()})."
        self._refresh_allocations()
        self._changed()

    def _on_party(self, *_args: Any) -> None:
        if self._filling:
            return
        if self.mode in EDITING:
            if self._allocs:
                self._allocs = []
                own = "عميله" if self.KIND == "receipt" else "مورده"
                self._note = f"{self.kind.party_label} اتغيّر ← التوزيع اتصفّر (السند بيتوزّع على فواتير {own} بس)."
            party = next((c for c in self._choices["parties"] if c["id"] == self.party_edit.currentData()), None)
            code = (party or {}).get("currency_code")
            if (code and not self._currency_touched and code != self._currency()
                    and any(r[0] == code for r in self._currency_rows())):
                self.currency_edit.set_value(code)               # عملة الطرف افتراضي (لحد ما تختار غيرها)
            self._refresh_allocations()
        self._changed()

    # ---- التوزيع على الفواتير --------------------------------------------
    _note = ""

    def _alloc_payload(self) -> list[dict[str, Any]]:
        key = self.service.ik
        return [{key: a[key], "amount": a["amount"]} for a in self._allocs]

    def _set_allocations(self, picks: list[dict[str, Any]]) -> None:
        """التوزيع الجديد (من نافذة الفواتير أو «وزّع الأقدم الأول») + بيانات فواتيره للعرض."""
        try:
            rows = self.service.allocation_details(self.party_edit.currentData(), self._currency(), picks)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر تحميل الفواتير", exc)
            return
        self._allocs = [{**r, "amount": Decimal(str(r["amount"]))} for r in rows if Decimal(str(r["amount"] or 0)) > 0]
        self._note = ""
        self._refresh_allocations()
        self._changed()

    def _can_allocate(self) -> bool:
        if self.party_edit.currentData() is None:
            messages.warn(self, "التوزيع", f"اختار {self.kind.party_label} الأول.")
            return False
        return True

    def pick_invoices(self) -> None:
        """«📋 الفواتير»: فواتير الطرف المفتوحة بعملة السند — بالتوتال (طلب المستخدم)."""
        if self.mode not in EDITING or not self._can_allocate():
            return
        party = next((c for c in self._choices["parties"] if c["id"] == self.party_edit.currentData()), {})
        dialog = InvoiceDuesDialog(self.service, self, supplier_id=int(self.party_edit.currentData()),
                                   supplier_name=f"{party.get('code', '')} — {party.get('name_ar', '')}",
                                   currency_code=self._currency(), amount=amount_of(self.amount_edit.text()),
                                   picks={int(a[self.service.ik]): a["amount"] for a in self._allocs})
        if not dialog.exec():
            return
        self._set_allocations(dialog.result)
        total = sum((a["amount"] for a in self._allocs), Decimal("0"))
        if total > 0 and amount_of(self.amount_edit.text()) == 0:   # ماكتبش مبلغ ← مبلغ السند = اللي اختاره
            self.amount_edit.setText(money(total))

    def auto_allocate(self) -> None:
        if self.mode not in EDITING or not self._can_allocate():
            return
        amount = amount_of(self.amount_edit.text())
        if amount <= 0:
            messages.warn(self, "وزّع الأقدم الأول", "اكتب مبلغ السند الأول.")
            return
        try:
            picks = self.service.auto_allocate(self.party_edit.currentData(), self._currency(), amount)
        except Exception as exc:  # noqa: BLE001
            messages.show_error(self, "تعذّر التوزيع", exc)
            return
        self._set_allocations(picks)
        if not picks:
            self._note = (f"{self.kind.party_label} مالوش فواتير مفتوحة بعملة {self._currency()} — "
                          "المبلغ كله هيبقى دفعة مقدمة.")
            self._refresh_allocations()

    def clear_allocations(self) -> None:
        if self.mode not in EDITING:
            return
        self._allocs = []
        self._note = ""
        self._refresh_allocations()
        self._changed()

    def _refresh_allocations(self) -> None:
        """سطور التوزيع + الملخص (مبلغ السند · الموزّع · غير الموزّع = دفعة مقدمة)."""
        if not hasattr(self, "alloc_table"):
            return
        editing = self.mode in EDITING
        cur = self._currency()
        t = self.alloc_table
        t.blockSignals(True)
        t.setRowCount(len(self._allocs))
        errors = 0
        today = dt.date.today()
        for r, a in enumerate(self._allocs):
            amount = Decimal(a["amount"])
            remaining = a.get("remaining_amount")
            missing = bool(a.get("missing")) or remaining is None
            over = not missing and amount > Decimal(remaining)
            errors += 1 if (over or missing) else 0
            late = a.get("due_date") is not None and a["due_date"] < today and not missing and Decimal(remaining) > 0
            t.setItem(r, A_NO, _text_item(str(a.get("invoice_no") or "؟"), bold=True, color=BAD if missing else None))
            t.setItem(r, A_DUE, _text_item(_day(a.get("due_date")), color=BAD if late else None))
            for c, key in ((A_TOTAL, "total_amount"), (A_RETURNED, "returned_amount"), (A_PAID, "paid_amount"),
                           (A_LEFT, "remaining_amount")):
                value = a.get(key)
                text = "—" if value is None or (key == "returned_amount" and not Decimal(value)) else money(value)
                t.setItem(r, c, _num_item(text, bold=c == A_LEFT))
            pay = QTableWidgetItem(money(amount))
            pay.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
            pay.setFlags((Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable) if editing
                         else (Qt.ItemIsEnabled | Qt.ItemIsSelectable))
            pay.setBackground(QColor("#FEE2E2" if over else PAY_BG))
            if over:
                pay.setToolTip(f"أكتر من المتبقي على الفاتورة ({money(remaining)})")
            t.setItem(r, A_PAY, pay)
            t.setItem(r, A_AFTER, _num_item("—" if missing else money(Decimal(remaining) - amount),
                                            color=BAD if over else None))
            drop = _text_item("✕" if editing else "", color=BAD)
            drop.setTextAlignment(Qt.AlignCenter)
            drop.setToolTip("شيل الفاتورة من التوزيع" if editing else "")
            t.setItem(r, A_DROP, drop)
        t.blockSignals(False)
        amount = amount_of(self.amount_edit.text())
        allocated = sum((Decimal(a["amount"]) for a in self._allocs), Decimal("0"))
        rest = amount - allocated
        if hasattr(self, "tabs"):
            self.tabs.setTabText(0, "🧾 التوزيع على الفواتير" + (f" ({len(self._allocs)})" if self._allocs else ""))
        parts = [f"مبلغ السند {ltr(money(amount))} {cur}", f"الموزّع {ltr(money(allocated))}"]
        if rest < 0:
            parts.append(f"✖ الموزّع أكتر من السند بـ {ltr(money(-rest))}")
        elif rest > 0:
            parts.append(f"غير موزّع {ltr(money(rest))} ← دفعة مقدمة على حساب {self.kind.party_label} ({cur})")
        else:
            parts.append("اتوزّع كله ✔" if allocated else "")
        text = " · ".join(x for x in parts if x)
        if errors:
            text += " · ✖ فيه مبلغ أكتر من المتبقي على فاتورته"
        if self._note:
            text += f"\n💡 {self._note}"
        bad = rest < 0 or errors
        self.alloc_summary.setText(text)
        self.alloc_summary.setStyleSheet(
            f"color:{BAD if bad else OK_COLOR}; background:{'#FEE2E2' if bad else '#ECFDF3'}; "
            f"border:1px solid {'#FECACA' if bad else '#B7E4C7'}; border-radius:6px; padding:5px 10px; font-weight:800;")
        for b in (self.alloc_pick_button, self.alloc_auto_button, self.alloc_clear_button):
            b.setEnabled(editing)
        self.alloc_clear_button.setEnabled(editing and bool(self._allocs))

    def _on_alloc_item(self, item: QTableWidgetItem) -> None:
        """«يدفع» اتعدّل في السطر (صفر أو فاضي = شيل الفاتورة من التوزيع)."""
        if item.column() != A_PAY or self.mode not in EDITING or not 0 <= item.row() < len(self._allocs):
            return
        value = amount_of(item.text())
        if value <= 0:
            del self._allocs[item.row()]
        else:
            self._allocs[item.row()]["amount"] = value
        self._note = ""
        self._refresh_allocations()
        self._changed()

    def _on_alloc_click(self, row: int, column: int) -> None:
        if column == A_DROP and self.mode in EDITING and 0 <= row < len(self._allocs):
            del self._allocs[row]
            self._refresh_allocations()
            self._changed()

    # ---- الكروت ومعاينة القيد -------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        kind = self.source_kind.currentData()
        source = self.source_edit.currentData()
        data = {"voucher_date": _pydate(self.date_edit.date()), "party_id": self.party_edit.currentData(),
                "source_kind": kind, "treasury_id": source if kind == "treasury" else None,
                "bank_account_id": source if kind == "bank" else None, "amount": self.amount_edit.text(),
                "description": self.description.text() or self.kind.title, "paper_no": self.paper_no.text(),
                "project_id": self.project_edit.currentData()}
        data.update(currency_code=self._currency(), fx_rate=self.rate_edit.text(), allocations=self._alloc_payload())
        return data

    def _refresh_side(self) -> None:
        """الكروت (المبلغ · رصيد العميل / المورد · رصيد الخزنة / البنك قبل وبعد) + تحذير الخزنة + القيد."""
        self._timer.stop()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        if not editing and not rec:
            self._clear_side()
            return
        data = self._data()
        posted = not editing and rec.get("status") == "posted"
        kind = data["source_kind"]
        what = "الخزنة" if kind == "treasury" else "البنك"
        when = "دلوقتي (السند جوّاه)" if posted else "← بعد السند"
        cur = self._currency()
        foreign = cur != self._base()
        # الأرصدة **بالجنيه دايماً** — نفس أرقام كشف الحساب ودفتر الأستاذ (طلب المستخدم 2026-11-28).
        tag = " بالجنيه" if foreign else ""
        self.kpi_captions["source"].setText(f"رصيد {what}{tag} {when}")
        self.kpi_captions["party"].setText(f"رصيد {self.kind.party_label}{tag} {when}")
        self._refresh_allocations()
        # معاينة القيد الأول: منها اللي هيتقفل من حساب الطرف بالجنيه بالظبط (التوزيع بسعر الفواتير).
        preview, error = self._preview(data) if not posted else ([], "")
        effect = None
        if preview:
            effect = sum((Decimal(ln["debit"]) - Decimal(ln["credit"]) for ln in preview
                          if ln.get("role") == self.kind.party_role), Decimal("0"))
            effect = -effect if self.kind.party == "customer" else effect
        try:
            b = self.service.balances(data["party_id"], kind, data["treasury_id"] or data["bank_account_id"],
                                      data["amount"], posted=posted, currency_code=cur,
                                      fx_rate=data.get("fx_rate"), party_effect=effect)
        except Exception:  # noqa: BLE001 - الكروت للعرض بس
            b = None
        if b is None:
            self._clear_side(keep_entry=True)
        else:
            self.kpi_values["amount"].setText(
                ltr(money(b["amount_base"] if foreign else b["amount"])) if b["amount"] else "—")
            self._kpi_note("amount", f"{ltr(money(b['amount']))} {cur} × {ltr(rate_text(data.get('fx_rate')) or '؟')}"
                           if foreign and b["amount"] else "")
            natural_fc = "مدين" if self.kind.party == "customer" else "دائن"
            self._kpi_note("party", self._fc_pair(b["party_fc"], b["party_fc_after"], cur, natural_fc, posted))
            self._kpi_note("source", self._fc_pair(b["source_fc"], b["source_fc_after"], cur, "مدين", posted))
            def pair(before: Decimal | None, after: Decimal | None, natural: str = "مدين") -> str:
                if before is None:
                    return "—"
                if posted:
                    return signed(before, natural)
                return f"{signed(before, natural)}  ←  {signed(after, natural)}"

            # العميل رصيده الطبيعي مدين (عليه لينا) · المورد دائن (ليه علينا).
            natural = "مدين" if self.kind.party == "customer" else "دائن"
            self.kpi_values["party"].setText(pair(b["party"], b["party_after"], natural))
            self.kpi_values["source"].setText(pair(b["source"], b["source_after"]))
            low = b["source_after"] is not None and b["source_after"] < 0
            self.kpi_values["source"].setStyleSheet(
                f"{VALUE_STYLE} color:{BAD if low else OK_COLOR};" if b["source"] is not None else VALUE_STYLE)
            short = (not posted and self.KIND == "payment" and b["source"] is not None
                     and b["available_after"] < 0 and b["amount"] > 0)
            if short:
                what = "الخزنة" if kind == "treasury" else "الحساب البنكي"
                self.funds_label.setText(f"⚠️ رصيد {what} «{b['source_name']}» مش هيكفي "
                                         f"({ltr(money(b['source']))}) — تحذير بس.")
            self.funds_label.setVisible(short)
        self._refresh_entry(data, posted, preview, error)

    def _preview(self, data: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        """سطور القيد اللي هيتعمل — أو رسالة الغلط."""
        try:
            return self.service.preview(data), ""
        except BusinessError as exc:
            return [], str(exc)
        except Exception:  # noqa: BLE001
            return [], "مش قادر أجهّز القيد دلوقتي."

    def _kpi_note(self, key: str, text: str) -> None:
        self.kpi_notes[key].setText(text)
        self.kpi_notes[key].setVisible(bool(text))

    @staticmethod
    def _fc_pair(before: Decimal | None, after: Decimal | None, cur: str, natural: str, posted: bool) -> str:
        """«بالدولار: 1,000.00 دائن ← 500.00 دائن» — للعلم بس (الرصيد اللي فوق بالجنيه)."""
        if before is None:
            return ""
        if posted or after is None:
            return f"منه بعملة {cur}: {signed(before, natural)}"
        return f"منه بعملة {cur}: {signed(before, natural)}  ←  {signed(after, natural)}"

    def _clear_side(self, keep_entry: bool = False) -> None:
        for key in ("amount", "party", "source"):
            self.kpi_values[key].setText("—")
            self.kpi_values[key].setStyleSheet(VALUE_STYLE)
            self._kpi_note(key, "")
        self.funds_label.hide()
        if not keep_entry:
            self.entry_table.setRowCount(0)
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")

    def _refresh_entry(self, data: dict[str, Any], posted: bool, preview: list[dict[str, Any]] | None = None,
                       error: str = "") -> None:
        rows: list[dict[str, Any]] = []
        if posted:
            self.entry_box.setTitle("القيد المرحّل")
            entry = self.service.entry_for(int(self.current_id)) if self.current_id is not None else None
            for line in (entry or {}).get("lines") or []:
                party = f" — {line['party_name']}" if line.get("party_name") else ""
                rows.append({"account": f"{line['account_code']} {line['account_name']}{party}{self._fc_note(line)}",
                             "debit": line["debit"], "credit": line["credit"]})
        else:
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")
            if preview is None:
                preview, error = self._preview(data)
            for line in preview:
                rows.append({"account": f"{line['account_code']} {line['account_name']}{self._fc_note(line)}",
                             "debit": line["debit"], "credit": line["credit"]})
        self.entry_table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.entry_table.setItem(r, 0, QTableWidgetItem(row["account"]))
            for c, key in ((1, "debit"), (2, "credit")):
                item = QTableWidgetItem(money(row[key]) if row[key] else "")
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
                self.entry_table.setItem(r, c, item)
        if error:
            self.entry_label.setText(f"✖ {error}")
            self._paint_entry(False)
        elif rows:
            d = sum((Decimal(r["debit"]) for r in rows), Decimal("0"))
            c = sum((Decimal(r["credit"]) for r in rows), Decimal("0"))
            prefix = f"{ltr(voucher_label(self.current_record or {}))} · " if posted else ""
            self.entry_label.setText(f"✔ {prefix}متوازن · مدين {ltr(money(d))} = دائن {ltr(money(c))}")
            self._paint_entry(d == c)
        else:
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")

    @staticmethod
    def _fc_note(line: dict[str, Any]) -> str:
        """السطر بعملة غير الأساسية: المبلغ بالعملة × السعر (القيد نفسه بالجنيه)."""
        if not line.get("currency_code") or line.get("amount_fc") in (None, ""):
            return ""
        return f"  ({ltr(money(line['amount_fc']))} {line['currency_code']} × {ltr(rate_text(line.get('fx_rate')))})"

    def _paint_entry(self, ok: bool) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.entry_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                       "padding:6px 10px; font-weight:900;")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        widgets = [self.date_edit, self.party_edit, self.party_find, self.source_kind, self.source_edit,
                   self.source_find, self.amount_edit, self.description, self.paper_no, self.project_edit]
        return widgets + [self.currency_edit, self.currency_find, self.rate_edit]

    def _is_editable_record(self) -> bool:
        rec = self.current_record or {}
        return rec.get("status") == "draft" and rec.get("year_status") != "closed"

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        if not editing:
            rec = self.current_record or {}
            self.edit_button.setEnabled(self.edit_button.isEnabled() and self._is_editable_record())
            self.delete_button.setEnabled(self.delete_button.isEnabled() and self._is_editable_record()
                                          and not rec.get("voucher_no"))
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.from_edit.setEnabled(not editing)
        self.to_edit.setEnabled(not editing)
        self._update_paging(editing)
        self._sync_rate_enabled()
        self._refresh_allocations()
        if hasattr(self, "entry_table"):
            self._changed()

    def update_extra_buttons(self) -> None:
        rec = self.current_record or {}
        editing = self.mode in EDITING
        year_open = rec.get("year_status") != "closed"
        self.post_button.setEnabled(self.can("post") and (
            (editing and self.can("save")) or
            (not editing and self.current_id is not None and year_open and rec.get("status") == "draft")))
        self.unpost_button.setEnabled(self.can("unpost") and not editing and self.current_id is not None
                                      and year_open and rec.get("status") == "posted")

    def new_record(self) -> None:
        self._load_choices()
        super().new_record()
        if self.mode != "new":
            return
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.number_edit.setText("بيتاخد مع الترحيل")
        self._show_status(None)
        self._allocs = []
        self._note = ""
        self._set_lookups()
        rows = self._source_rows("treasury")
        if rows:
            self.source_edit.set_value(rows[0][0])
        self.set_mode("new")
        self.currency_edit.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب السند من جديد
        if self.mode == "edit" and not self._is_editable_record():  # جهاز تاني رحّله في الوقت ده
            self.load(self.current_id)
            messages.warn(self, "تعديل", f"{self.kind.short} ده مرحّل — الغي ترحيله الأول عشان تعدّله.")

    # ---- الترحيل / إلغاء الترحيل --------------------------------------------------
    def _confirm_funds(self, data: dict[str, Any]) -> bool:
        """الخزنة مش مكفية (سند الصرف) ← تحذير بس والمستخدم يكمّل (رد المستخدم)."""
        try:
            warning = self.service.funds_warning(data)
        except Exception:  # noqa: BLE001 - الحفظ نفسه هيطلّع الغلط
            return True
        if not warning:
            return True
        return messages.ask_yes_no(self, "⚠️ الرصيد مش مكفي", f"{warning}\n\nتكمّل الترحيل؟")

    def post_voucher(self) -> None:
        if not self.post_button.isEnabled():
            return
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            if not self._confirm_funds(payload):
                return
            if not messages.ask_yes_no(self, "ترحيل", f"هيتحفظ {self.kind.short} ويترحّل بقيد — بعد الترحيل "
                                                       "مايتعدّلش غير بـ «إلغاء الترحيل». تكمّل؟"):
                return
            try:
                saved = self.service.save_draft(None if self.mode == "new" else int(self.current_id), payload,
                                                expected_version=self.current_version, post=True)
            except ConcurrentEditError as exc:
                self.on_conflict(exc)
                return
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, "تعذّر الترحيل", exc)
                return
            self.current_id = saved
            self.mode = "view"
        else:
            rec = self.current_record or {}
            if not self._confirm_funds(self._record_data(rec)):
                return
            if not messages.ask_yes_no(self, "ترحيل", f"ترحيل {self.kind.short} {voucher_label(rec)}؟"):
                return
            try:
                self.service.post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error("تعذّر الترحيل", exc)
                return
        self._after_action()
        messages.info(self, "تم الترحيل",
                      f"اترحّل {self.kind.short} برقم {ltr((self.current_record or {}).get('voucher_no'))}.")

    def unpost_voucher(self) -> None:
        if not self.unpost_button.isEnabled() or self.current_id is None:
            return
        label = voucher_label(self.current_record or {})
        if not messages.ask_yes_no(self, "إلغاء الترحيل",
                                   f"قيد {self.kind.short} {label} هيتمسح والسند هيرجع مسودة (برقمه) وتقدر تعدّله. تكمّل؟"):
            return
        try:
            self.service.unpost(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر إلغاء الترحيل", exc)
            return
        self._after_action()
        messages.info(self, "تم", f"{self.kind.short} {label} رجع مسودة — دوس «تعديل» عشان تعدّله.")

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
        self.record_saved.emit(self.current_id)

    # ---- القائمة والصفحات --------------------------------------------------------
    def _query(self, keyword: str) -> tuple:
        return self._from, self._to, self.status, keyword

    def _on_period(self, *_args: Any) -> None:
        self._from, self._to = _pydate(self.from_edit.date()), _pydate(self.to_edit.date())
        self._after_filter()

    def _on_status(self, chip: QPushButton) -> None:
        self.status = chip.property("value")
        self._after_filter()

    def _after_filter(self) -> None:
        self.page = 1
        self.refresh_list()
        if self.mode == "view" and self.current_id is not None and not self.table.select_id(self.current_id):
            self.show_empty()

    def _go_page(self, page: int) -> None:
        self.page = max(1, min(self.pages, page))
        self.refresh_list()

    def _update_paging(self, editing: bool = False) -> None:
        if not hasattr(self, "prev_button"):
            return
        self.prev_button.setEnabled(not editing and self.page > 1)
        self.next_button.setEnabled(not editing and self.page < self.pages)
        self.page_label.setText(f"صفحة {self.page} من {self.pages}")

    def fetch_rows(self, keyword: str) -> list[dict]:
        query = self._query(keyword)
        if query != self._last_query:
            self._last_query = query
            self.page = 1
        self._counts = self.service.count_vouchers(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.service.list_vouchers(*query, page=self.page, page_size=PAGE_SIZE)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "voucher_no":
            return value or f"مسودة {row.get('draft_no')}"
        if key == "amount":
            code = row.get("currency_code")
            return money(value) + (f" {code}" if code and code != self._base() else "")
        if key == "status":
            return "مرحّل" if value == "posted" else "مسودة"
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col, amount_col = cols.index("status"), cols.index("amount")
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(OK_COLOR if row.get("status") == "posted" else DRAFT_COLOR))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            item = self.table.item(r, amount_col)
            if item is not None:
                item.setForeground(QColor(KIND_COLORS[self.KIND]))
        counts = getattr(self, "_counts", None)
        if counts is not None:
            self.count_label.setText(f"{counts['total']:,} سند في الفترة · مسودة {counts['drafts']:,} · "
                                     f"مرحّل {counts['posted']:,} ({money(counts['posted_amount'])} جنيه)")
        self._update_paging(self.mode in EDITING)

    # ---- البيانات ----------------------------------------------------------
    def _show_status(self, record: dict | None) -> None:
        if not record:
            self.status_label.setText("جديد")
            self.status_label.setStyleSheet(BADGE.format(bg="#EFF6FF", fg="#1D4ED8", bd="#BFDBFE"))
            self.year_note.hide()
            return
        text, bg, fg, bd = STATUS_BADGES[record["status"]]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(BADGE.format(bg=bg, fg=fg, bd=bd))
        note = ""
        if record.get("year_status") == "closed":
            note = f"🔒 السنة المالية {record['year_code']} مقفولة — السند ده مايتعدّلش ولا يتلغى ترحيله."
        elif record["status"] == "posted" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            note = f"✔ اترحّل بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}"
        self.year_note.setText(note)
        self.year_note.setStyleSheet(f"color:{'#5B21B6' if record.get('year_status') == 'closed' else MUTED}; "
                                     "font-weight:800;")
        self.year_note.setVisible(bool(note))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_voucher(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.number_edit.setText(record.get("voucher_no") or
                                     f"مسودة {record.get('draft_no')} — الرقم الرسمي بيتاخد مع الترحيل")
            self.date_edit.setDate(_qdate(record["voucher_date"]))
            self.amount_edit.setText(money(record.get("amount")))
            self.description.setText(record.get("description") or "")
            self.paper_no.setText(record.get("paper_no") or "")
            self._show_status(record)
        finally:
            self._filling = False
        self._allocs = [{**a, "amount": Decimal(a["amount"])} for a in record.get("allocations") or []]
        self._note = ""
        self._set_lookups(record)
        self._sync_rate_enabled()
        self._refresh_allocations()
        self._changed()

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.amount_edit, self.description, self.paper_no):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.year_note.hide()
        finally:
            self._filling = False
        self._allocs = []
        self._note = ""
        self._set_lookups()
        self._sync_rate_enabled()
        self._refresh_allocations()
        if hasattr(self, "entry_table"):
            self._clear_side()

    def _record_data(self, rec: dict[str, Any]) -> dict[str, Any]:
        data = {k: rec.get(k) for k in ("voucher_date", "party_id", "source_kind", "treasury_id",
                                        "bank_account_id", "amount", "description", "paper_no", "project_id",
                                        "currency_code", "fx_rate")}
        key = self.service.ik
        data["allocations"] = [{key: a[key], "amount": a["amount"]} for a in rec.get("allocations") or []]
        return data

    def collect(self) -> dict:
        data = self._data()
        data["description"] = self.description.text()
        return data

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_voucher(int(record_id), expected_version=self.current_version)


class ReceiptsScreen(VoucherScreen):
    """سند قبض — من العميل (بعملة + التوزيع على فواتير البيع — 0054)."""
    FORM_STRETCH, LIST_STRETCH = 15, 8
    TARGET = "customer_receipts"
    KIND = "receipt"
    TITLE = "سند قبض"
    ICON = "📥"
    FORM_TITLE = "سند قبض"
    SEARCH_PLACEHOLDER = "🔍 رقم السند أو العميل أو البيان… (F1)"


class PaymentsScreen(VoucherScreen):
    """سند صرف — للمورد."""
    FORM_STRETCH, LIST_STRETCH = 15, 8
    TARGET = "supplier_payments"
    KIND = "payment"
    TITLE = "سند صرف"
    ICON = "📤"
    FORM_TITLE = "سند صرف"
    SEARCH_PLACEHOLDER = "🔍 رقم السند أو المورد أو البيان… (F1)"
    COLUMNS = [("voucher_no", "رقم السند"), ("voucher_date", "التاريخ"), ("party_name", "المورد"),
               ("amount", "المبلغ"), ("status", "الحالة")]
