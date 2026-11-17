"""شاشتي «شيكات العملاء» و«شيكات الموردين» — النموذج 1 «الكلاسيك: القائمة + الشيك المحدد وخطواته»
+ **كارت تنبيهات الاستحقاق** فوق (اختيار المستخدم 2026-11-17 + ردوده — mockups/phase5/03_cheques.html).

```
┌ [شيك جديد][تعديل][حفظ مسودة][استلام ✔][تراجع عن آخر خطوة][حذف][إلغاء][تحديث][تصدير إكسل][خروج]       (عرض) ┐
├ ⏰ تنبيهات الاستحقاق:  [متأخر 2 · 45,000]  [النهارده 1]  [بكرة 0]  [خلال 3 أيام 3]      عرض الشيكات ‹        ┤
├──────────────────────────────────────────────┬──────────────────────────────────┤
│ ┌ شيك وارد ┐ المستند · التاريخ · الحالة                │ ┌ الشيكات ┐ 🔍 رقم / عميل / بيان     │
│   العميل · رقم الشيك · بنك الساحب                     │   من [..] إلى [..]                 │
│   المبلغ · الاستحقاق · البيان · أمر التوريد             │   [الكل][مسودة][المفتوحة][خلصت]     │
│ ┌ خطوات الشيك ┐ ☐ تم الإيداع  ☐ تم التحصيل               │   المستند│رقم الشيك│العميل│البنك│…  │
│   [↩ رجع من البنك][🔁 تظهير لمورد][⤺ رد للعميل]          │                                    │
│   التاريخ│الخطوة│البنك / المورد│مصاريف│بواسطة           │                                    │
│ ┌ 📒 القيد ┐ الحساب│مدين│دائن  ✔ متوازن               │   › السابق  صفحة 1 من 3  التالي ‹     │
└──────────────────────────────────────────────┴──────────────────────────────────┘
```
* «حفظ مسودة» · «استلام ✔» / «إصدار ✔» (= ترحيل بالقيد والرقم CHR / CHP) · «تراجع عن آخر خطوة» بالصلاحية.
* **التشيك بوكس** (رد المستخدم): «تم الإيداع» · «تم التحصيل» للوارد و«تم الصرف» للصادر — بيعمل الخطوة (أو اللي
  ناقص مرة واحدة) بقيدها، وشيل العلامة = تراجع عن الخطوة دي (لو هي آخر خطوة).
* كارت التنبيهات: الشيكات المفتوحة المتأخرة · النهارده · بكرة · خلال 3 أيام — ودوس عليه ← شاشة بيهم.
* الطباعة والتقارير بعدين (رد المستخدم).
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
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
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
    QVBoxLayout,
    QWidget,
)

from app.services.cheque_service import (
    ALERT_DAYS,
    KINDS,
    PAGE_SIZE,
    STATUSES,
    ChequeService,
    cheque_label,
    due_bucket,
    due_text,
    status_name,
)
from app.services.errors import BusinessError, ConcurrentEditError
from app.ui.common import messages
from app.ui.common.base_screen import EDITING, RIGHT, BaseScreen
from app.ui.common.data_table import default_format
from app.ui.common.lookup_edit import LookupEdit
from app.ui.screens.journal_entries_screen import BADGE, CHIP_STYLE, ltr, money

OK_COLOR, BAD, MUTED, BLUE = "#087443", "#B91C1C", "#64748B", "#1D4ED8"
KIND_COLORS = {"in": "#137A38", "out": "#B91C1C"}
LIST_CHIPS = (("all", "الكل"), ("draft", "مسودة"), ("open", "المفتوحة"), ("done", "خلصت"))
# تنبيهات الاستحقاق: (المفتاح، الاسم، الخلفية، الكلام، الحدود)
BUCKETS = (("overdue", "متأخر", "#FEE2E2", "#B91C1C", "#FECACA"),
           ("today", "النهارده", "#FFEDD5", "#C2410C", "#FED7AA"),
           ("tomorrow", "بكرة", "#FEF3C7", "#92400E", "#FDE68A"),
           ("soon", f"خلال {ALERT_DAYS} أيام", "#EFF6FF", "#1D4ED8", "#BFDBFE"))
BUCKET_COLORS = {k: fg for k, _n, _bg, fg, _bd in BUCKETS}
WARN_STYLE = ("color:#9A3412; background:#FFF7ED; border:1px solid #FED7AA; border-radius:6px; padding:6px 10px; "
              "font-weight:900;")
STEP_STYLE = ("QPushButton { background:#FFFFFF; color:#334155; border:1px solid #CBD5E1; border-radius:7px; "
              "padding:5px 12px; font-weight:800; } QPushButton:hover { background:#F1F5F9; } "
              "QPushButton:disabled { color:#94A3B8; }")
CHECK_STYLE = ("QCheckBox { font-weight:900; spacing:6px; padding:4px 8px; border:1px solid #CBD5E1; "
               "border-radius:7px; background:#FFFFFF; } QCheckBox:checked { color:#087443; background:#ECFDF3; "
               "border-color:#B7E4C7; } QCheckBox:disabled { color:#94A3B8; }")


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


def _date_edit(value: dt.date | None = None) -> QDateEdit:
    edit = QDateEdit()
    edit.setCalendarPopup(True)
    edit.setLayoutDirection(Qt.LeftToRight)
    edit.setDisplayFormat("dd/MM/yyyy")
    edit.setDate(_qdate(value or dt.date.today()))
    return edit


def status_badge(status: str) -> tuple[str, str]:
    name, color, icon = STATUSES.get(status, (status, MUTED, ""))
    return BADGE.format(bg="#FFFFFF", fg=color, bd=color), f"{icon} {name}"


def _entry_table() -> QTableWidget:
    table = QTableWidget(0, 3)
    table.setHorizontalHeaderLabels(["الحساب", "مدين", "دائن"])
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
    for c in (1, 2):
        table.setColumnWidth(c, 120)
    return table


def _fill_entry(table: QTableWidget, rows: list[dict[str, Any]]) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        party = f" — {row['party_name']}" if row.get("party_name") else ""
        table.setItem(r, 0, QTableWidgetItem(f"{row['account_code']} {row['account_name']}{party}"))
        for c, key in ((1, "debit"), (2, "credit")):
            item = QTableWidgetItem(money(row[key]) if row[key] else "")
            item.setTextAlignment(Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter)
            table.setItem(r, c, item)


# ---------------------------------------------------------------------------
# نافذة الخطوة (إيداع · تحصيل · رجوع · تظهير · رد · صرف · إلغاء · «تم التحصيل»)
# ---------------------------------------------------------------------------
class StepDialog(QDialog):
    """التاريخ (+ الحساب البنكي للإيداع · المورد للتظهير · المصاريف للتحصيل والرجوع) + القيد اللي هيتعمل."""

    def __init__(self, screen: "ChequesScreen", event: str, title: str, *, complete: bool = False) -> None:
        super().__init__(screen)
        self.owner = screen
        self.service = screen.service
        self.step = event
        self.complete = complete
        rec = screen.current_record or {}
        self.record = rec
        self.setWindowTitle(title)
        self.setLayoutDirection(Qt.RightToLeft)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        header = QLabel(f"{title} — {cheque_label(rec)} · شيك {rec.get('cheque_no', '')} · "
                        f"{money(rec.get('amount'))} ج.م")
        header.setWordWrap(True)
        header.setStyleSheet(f"background:{KIND_COLORS[screen.DIRECTION]}; color:#FFFFFF; font-size:14px; "
                             "font-weight:900; border-radius:6px; padding:8px 12px;")
        layout.addWidget(header)
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(RIGHT)
        start = max(dt.date.today(), rec.get("status_date") or rec.get("cheque_date") or dt.date.today())
        self.date_edit = _date_edit(start)
        self.date_edit.dateChanged.connect(self._refresh)
        form.addRow("التاريخ *", self.date_edit)
        self.needs_bank = event == "deposit" or (complete and rec.get("status") in ("portfolio", "bounced"))
        self.bank_edit = LookupEdit("اختار الحساب البنكي… *")
        if self.needs_bank:
            rows = screen._bank_account_rows()
            self.bank_edit.set_items(rows, rows[0][0] if rows else None)
            self.bank_edit.value_changed.connect(self._refresh)
            form.addRow("اتودع في *", self.bank_edit)
        self.supplier_edit = LookupEdit("اكتب كود المورد أو اسمه… *")
        if event == "endorse":
            self.supplier_edit.set_items(screen._supplier_rows())
            self.supplier_edit.value_changed.connect(self._refresh)
            form.addRow("للمورد *", self.supplier_edit)
        self.fees_edit = QLineEdit()
        self.fees_edit.setLayoutDirection(Qt.LeftToRight)
        self.fees_edit.setPlaceholderText("0.00 (اختياري)")
        if event in ("collect", "bounce") or (complete and screen.DIRECTION == "in"):
            self.fees_edit.textChanged.connect(self._refresh)
            form.addRow("مصاريف بنكية", self.fees_edit)
        self.note_edit = QLineEdit()
        self.note_edit.setMaxLength(300)
        self.note_edit.setPlaceholderText("اختياري")
        form.addRow("ملاحظة", self.note_edit)
        layout.addLayout(form)
        self.entry_title = QLabel("📒 القيد اللي هيتعمل")
        self.entry_title.setStyleSheet("font-weight:900;")
        layout.addWidget(self.entry_title)
        self.entry = _entry_table()
        self.entry.setMinimumHeight(120)
        layout.addWidget(self.entry)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("✔ نفّذ")
        buttons.button(QDialogButtonBox.Cancel).setText("إلغاء")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def values(self) -> dict[str, Any]:
        return {"event_date": _pydate(self.date_edit.date()),
                "bank_account_id": self.bank_edit.currentData() if self.needs_bank else None,
                "supplier_id": self.supplier_edit.currentData() if self.step == "endorse" else None,
                "fees": self.fees_edit.text() or "0", "note": self.note_edit.text()}

    def _refresh(self, *_args: Any) -> None:
        v = self.values()
        rows: list[dict[str, Any]] = []
        try:
            if self.complete and self.owner.DIRECTION == "in" and self.record.get("status") != "under_collection":
                rows = self.service.step_preview(int(self.record["id"]), "deposit", bank_account_id=v["bank_account_id"])
                self.entry_title.setText("📒 القيدين اللي هيتعملوا: الإيداع ← والتحصيل")
                rows += self.owner._collect_preview(self.record, v)
            else:
                event = "collect" if self.complete and self.owner.DIRECTION == "in" else (
                    "clear" if self.complete else self.step)
                rows = self.service.step_preview(int(self.record["id"]), event, bank_account_id=v["bank_account_id"],
                                                 supplier_id=v["supplier_id"], fees=v["fees"])
            if not rows:
                self.hint.setText("مفيش قيد — العميل اتحمّل الشيك من يوم ما رجع من البنك، والرد تسليم ورقة بس.")
                self.hint.setStyleSheet(f"color:{MUTED}; font-weight:800;")
            else:
                d = sum((Decimal(r["debit"]) for r in rows), Decimal("0"))
                c = sum((Decimal(r["credit"]) for r in rows), Decimal("0"))
                self.hint.setText(f"✔ متوازن · مدين {ltr(money(d))} = دائن {ltr(money(c))}")
                self.hint.setStyleSheet(f"color:{OK_COLOR}; font-weight:900;")
        except BusinessError as exc:
            self.hint.setText(f"✖ {exc}")
            self.hint.setStyleSheet(f"color:{BAD}; font-weight:900;")
        _fill_entry(self.entry, rows)


# ---------------------------------------------------------------------------
# الشاشة اللي بتطلع من كارت التنبيهات
# ---------------------------------------------------------------------------
class DueChequesDialog(QDialog):
    COLUMNS = ("المستند", "رقم الشيك", "الطرف", "البنك", "الاستحقاق", "متى", "المبلغ", "الحالة")

    def __init__(self, screen: "ChequesScreen", alerts: dict[str, Any]) -> None:
        super().__init__(screen)
        self.owner = screen
        self.rows = alerts["rows"]
        self.chosen: int | None = None
        self.done_requested = False
        kind = KINDS[screen.DIRECTION]
        self.setWindowTitle(f"⏰ {kind.title} — المستحق لحد {ALERT_DAYS} أيام")
        self.setLayoutDirection(Qt.RightToLeft)
        self.resize(900, 460)
        layout = QVBoxLayout(self)
        what = "تتحصّل (تتودع البنك)" if screen.DIRECTION == "in" else "تتصرف (لازم رصيد في البنك)"
        header = QLabel(f"⏰ الشيكات اللي المفروض {what} — المتأخر والنهارده وبكرة وخلال {ALERT_DAYS} أيام")
        header.setWordWrap(True)
        header.setStyleSheet(f"background:{KIND_COLORS[screen.DIRECTION]}; color:#FFFFFF; font-size:14px; "
                             "font-weight:900; border-radius:6px; padding:8px 12px;")
        layout.addWidget(header)
        self.table = QTableWidget(len(self.rows), len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for r, row in enumerate(self.rows):
            values = (row["cheque_ref"], row["cheque_no"], row["party_name"], row["bank_label"] or "",
                      row["due_date"].strftime("%d/%m/%Y"), row["due_text"], money(row["amount"]),
                      status_name(row["status"]))
            for c, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if c == 5:
                    item.setForeground(QColor(BUCKET_COLORS.get(row["bucket"], MUTED)))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self.table.setItem(r, c, item)
        self.table.cellDoubleClicked.connect(lambda *_a: self._open())
        if self.rows:
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)
        total = sum((Decimal(r["amount"]) for r in self.rows), Decimal("0"))
        foot = QLabel(f"{len(self.rows)} شيك · الإجمالي {ltr(money(total))} ج.م — دبل كليك على الشيك يفتحه في الشاشة.")
        foot.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        layout.addWidget(foot)
        bar = QHBoxLayout()
        self.open_button = QPushButton("افتح الشيك")
        self.done_button = QPushButton(f"✔ {kind.done_label}")
        self.close_button = QPushButton("إغلاق")
        for b in (self.open_button, self.done_button, self.close_button):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(STEP_STYLE)
            bar.addWidget(b)
        bar.addStretch(1)
        self.open_button.clicked.connect(self._open)
        self.done_button.clicked.connect(self._done)
        self.close_button.clicked.connect(self.reject)
        self.done_button.setEnabled(screen.can("post") and bool(self.rows))
        self.open_button.setEnabled(bool(self.rows))
        layout.addLayout(bar)

    def _selected(self) -> int | None:
        r = self.table.currentRow()
        return int(self.rows[r]["id"]) if 0 <= r < len(self.rows) else None

    def _open(self) -> None:
        self.chosen = self._selected()
        if self.chosen is not None:
            self.accept()

    def _done(self) -> None:
        self.chosen = self._selected()
        if self.chosen is not None:
            self.done_requested = True
            self.accept()


# ---------------------------------------------------------------------------
# الشاشة
# ---------------------------------------------------------------------------
class ChequesScreen(BaseScreen):
    MODULE = "cash"
    DIRECTION = "in"
    NEW_TEXT = "شيك جديد"
    LIST_TITLE = "الشيكات"
    FORM_STRETCH, LIST_STRETCH = 12, 11
    EXIT_LAST = True
    COLUMNS = [("cheque_ref", "المستند"), ("cheque_no", "رقم الشيك"), ("party_name", "العميل"),
               ("bank_label", "البنك"), ("due_date", "الاستحقاق"), ("amount", "المبلغ"), ("status", "الحالة")]

    def __init__(self, service: ChequeService | None = None, parent: QWidget | None = None) -> None:
        self.kind = KINDS[self.DIRECTION]
        self.service = service or ChequeService(self.DIRECTION)
        self.page = 1
        self.pages = 1
        self.status = "all"
        self._last_query: tuple | None = None
        self._choices: dict[str, Any] = {"parties": [], "suppliers": [], "banks": [], "bank_accounts": [],
                                         "projects": []}
        self._filling = False
        self._alerts: dict[str, Any] = {"rows": [], "total": 0}
        today = dt.date.today()
        self._from = today.replace(day=1) - dt.timedelta(days=62)
        self._from = self._from.replace(day=1)
        self._to = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_side)
        super().__init__(parent)
        self.save_button.setText("حفظ مسودة")
        self.save_button.setMinimumWidth(self.save_button.fontMetrics().horizontalAdvance("حفظ مسودة") + 30)
        self.count_label.parentWidget().layout().addLayout(self.paging)
        self.refresh_alerts()
        if self.table.rows:
            first = self.table.rows[0]
            self.load(first["id"])
            self.table.select_id(first["id"])

    # ---- البناء ------------------------------------------------------------
    def _build_ui(self) -> None:
        super()._build_ui()
        self.layout().insertWidget(1, self._build_alerts())

    def _build_alerts(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("alerts")
        bar.setCursor(Qt.PointingHandCursor)
        bar.setStyleSheet("QFrame#alerts { background:#FFFFFF; border:1px solid #E2E8F0; "
                          "border-right:4px solid #C2410C; border-radius:7px; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)
        title = QLabel("⏰ تنبيهات الاستحقاق:")
        title.setStyleSheet("font-weight:900; border:none;")
        row.addWidget(title)
        self.alert_chips: dict[str, QPushButton] = {}
        for key, name, bg, fg, bd in BUCKETS:
            chip = QPushButton(name)
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {bd}; "
                               "border-radius:11px; padding:4px 12px; font-weight:900; }")
            chip.clicked.connect(self.show_due)
            row.addWidget(chip)
            self.alert_chips[key] = chip
        row.addStretch(1)
        self.alert_button = QPushButton("عرض الشيكات ‹")
        self.alert_button.setCursor(Qt.PointingHandCursor)
        self.alert_button.setStyleSheet(STEP_STYLE)
        self.alert_button.clicked.connect(self.show_due)
        row.addWidget(self.alert_button)
        bar.mousePressEvent = lambda _e: self.show_due()      # الكارت كله بيفتح الشاشة
        self.alert_bar = bar
        return bar

    def build_form(self, box: QGroupBox) -> None:
        color = KIND_COLORS[self.DIRECTION]
        box.setStyleSheet(f"QGroupBox#formBox {{ border-top:3px solid {color}; }}"
                          f"QGroupBox#formBox::title {{ color:{color}; }}")
        form = QFormLayout(box)
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
        self.number_edit = QLineEdit()
        self.number_edit.setReadOnly(True)
        self.number_edit.setToolTip(f"الرقم الرسمي ({self.kind.prefix}-2026-00001) بيتاخد مع أول "
                                    f"«{self.kind.start_label}» — والمسودة ليها رقم مؤقت")
        self.date_edit = _date_edit()
        self.date_edit.dateChanged.connect(self._changed)
        self.status_label = QLabel("")
        top = QHBoxLayout()
        top.addWidget(self.number_edit, 2)
        top.addWidget(QLabel("تاريخ الاستلام *" if self.DIRECTION == "in" else "تاريخ الإصدار *"))
        top.addWidget(self.date_edit, 1)
        top.addWidget(self.status_label)
        form.addRow("رقم المستند", top)

        self.party_edit = LookupEdit(f"اكتب كود {self.kind.party_label} أو اسمه… *")
        self.party_edit.value_changed.connect(self._changed)
        form.addRow(f"{self.kind.party_label} *", self.party_edit)

        self.cheque_no = QLineEdit()
        self.cheque_no.setMaxLength(30)
        self.cheque_no.setLayoutDirection(Qt.LeftToRight)
        self.cheque_no.setPlaceholderText("زي ما هو على الشيك")
        self.cheque_no.textChanged.connect(self._changed)
        self.bank_edit = LookupEdit("اختار بنك الساحب… *" if self.DIRECTION == "in" else "اختار الحساب البنكي… *")
        self.bank_edit.value_changed.connect(self._changed)
        line = QHBoxLayout()
        line.addWidget(self.cheque_no, 1)
        line.addWidget(QLabel("بنك الساحب *" if self.DIRECTION == "in" else "من حسابنا *"))
        line.addWidget(self.bank_edit, 2)
        form.addRow("رقم الشيك *", line)

        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        self.amount_edit.textChanged.connect(self._changed)
        self.due_edit = _date_edit()
        self.due_edit.dateChanged.connect(self._changed)
        self.due_label = QLabel("")
        money_row = QHBoxLayout()
        money_row.addWidget(self.amount_edit, 1)
        money_row.addWidget(QLabel("الاستحقاق *"))
        money_row.addWidget(self.due_edit, 1)
        money_row.addWidget(self.due_label)
        form.addRow("المبلغ *", money_row)

        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("الشيك ده عن إيه (زي: دفعة فاتورة 1045)")
        form.addRow("البيان *", self.description)
        self.project_edit = LookupEdit("اختياري")
        self.project_edit.value_changed.connect(self._changed)
        form.addRow("أمر التوريد", self.project_edit)
        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.hide()
        form.addRow(self.info_label)

    def _build_steps(self) -> QGroupBox:
        self.steps_box = QGroupBox("خطوات الشيك")
        col = QVBoxLayout(self.steps_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        checks = QHBoxLayout()
        checks.setSpacing(8)
        self.deposit_check = QCheckBox("تم الإيداع")
        self.deposit_check.setToolTip("علّم لما الشيك يتودع البنك للتحصيل — بيعمل قيد الإيداع")
        self.done_check = QCheckBox(self.kind.done_label)
        self.done_check.setToolTip("علّم لما الشيك يتحصّل — بيعمل الإيداع والتحصيل لو لسه في الحافظة"
                                   if self.DIRECTION == "in" else "علّم لما الشيك يتصرف من البنك — بيعمل قيد الصرف")
        for check in (self.deposit_check, self.done_check):
            check.setStyleSheet(CHECK_STYLE)
            check.setCursor(Qt.PointingHandCursor)
        self.deposit_check.clicked.connect(lambda on: self._on_check("deposit", on))
        self.done_check.clicked.connect(lambda on: self._on_check("done", on))
        if self.DIRECTION == "in":
            checks.addWidget(self.deposit_check)
        else:
            self.deposit_check.hide()
        checks.addWidget(self.done_check)
        checks.addSpacing(10)
        self.step_buttons: dict[str, QPushButton] = {}
        extra = (("bounce", "↩ رجع من البنك"), ("endorse", "🔁 تظهير لمورد"), ("return", "⤺ رد للعميل")) \
            if self.DIRECTION == "in" else (("cancel", "✖ رجع / اتلغى"),)
        for event, text in extra:
            button = QPushButton(text)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(STEP_STYLE)
            button.clicked.connect(lambda _c=False, e=event, t=text.split(" ", 1)[1]: self.run_step(e, t))
            checks.addWidget(button)
            self.step_buttons[event] = button
        checks.addStretch(1)
        col.addLayout(checks)
        self.steps_hint = QLabel("")
        self.steps_hint.setWordWrap(True)
        self.steps_hint.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        col.addWidget(self.steps_hint)
        self.history = QTableWidget(0, 5)
        self.history.setHorizontalHeaderLabels(["التاريخ", "الخطوة", "البنك / المورد", "مصاريف", "بواسطة"])
        self.history.verticalHeader().setVisible(False)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.setSelectionMode(QAbstractItemView.NoSelection)
        self.history.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.history.setMinimumHeight(90)
        self.history.setMaximumHeight(150)
        col.addWidget(self.history)
        return self.steps_box

    def _build_entry(self) -> QGroupBox:
        self.entry_box = QGroupBox("القيد اللي هيتعمل مع الاستلام" if self.DIRECTION == "in"
                                   else "القيد اللي هيتعمل مع الإصدار")
        col = QVBoxLayout(self.entry_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.entry_table = _entry_table()
        self.entry_table.setMinimumHeight(100)
        self.entry_table.setMaximumHeight(160)
        col.addWidget(self.entry_table)
        self.entry_label = QLabel("")
        self.entry_label.setWordWrap(True)
        col.addWidget(self.entry_label)
        return self.entry_box

    def arrange_content(self, content: QHBoxLayout, form_box: QGroupBox, list_box: QGroupBox) -> None:
        holder = QWidget()
        right = QVBoxLayout(holder)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(form_box)
        self.warn_label = QLabel("")
        self.warn_label.setWordWrap(True)
        self.warn_label.setStyleSheet(WARN_STYLE)
        self.warn_label.hide()
        right.addWidget(self.warn_label)
        right.addWidget(self._build_steps())
        right.addWidget(self._build_entry())
        right.addStretch(1)
        form_box.setMinimumHeight(form_box.sizeHint().height())     # الفورم مايتكبسش — الباقي بيتحرك بالسكرول
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        content.addWidget(scroll, self.FORM_STRETCH)                # RTL: أول عنصر = يمين
        content.addWidget(list_box, self.LIST_STRETCH)
        self._load_choices()

    def add_toolbar_buttons(self, layout: QHBoxLayout) -> None:
        self.post_button = self._button(f"{self.kind.start_label} ✔", "primary", self.post_cheque)
        self.post_button.setToolTip(f"«{self.kind.start_label}» = ترحيل: بيعمل القيد وبياخد الرقم الرسمي — "
                                    "وانت بتعدّل = حفظ + ترحيل")
        self.undo_button = self._button("تراجع عن آخر خطوة", "lock", self.undo_step)
        self.undo_button.setToolTip("آخر خطوة وقيدها بيتمسحوا والشيك بيرجع لحالته اللي قبلها "
                                    "(والتراجع عن الاستلام / الإصدار بيرجّعه مسودة برقمه)")
        self.post_button.setVisible(self.has_action("post"))
        self.undo_button.setVisible(self.has_action("unpost"))
        index = layout.indexOf(self.save_button)
        layout.insertWidget(index + 1, self.post_button)
        layout.insertWidget(index + 2, self.undo_button)

    def add_list_filters(self, layout: QVBoxLayout) -> None:
        period = QHBoxLayout()
        self.from_edit, self.to_edit = _date_edit(self._from), _date_edit(self._to)
        for edit in (self.from_edit, self.to_edit):
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
        for key, text in LIST_CHIPS:
            chip = QPushButton(text)
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setStyleSheet(CHIP_STYLE)
            chip.setProperty("value", key)
            if key == "open":
                chip.setToolTip("كل الشيكات المفتوحة من غير فترة (الأقرب استحقاق فوق)")
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
    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «شيك جديد» و«تعديل» والخطوات (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"parties": [], "suppliers": [], "banks": [], "bank_accounts": [], "projects": []}

    def _party_rows(self) -> list[tuple[int, str, str]]:
        return [(c["id"], c["code"], f"{c['code']} — {c['name_ar']}") for c in self._choices["parties"]
                if c["is_active"]]

    def _supplier_rows(self) -> list[tuple[int, str, str]]:
        return [(c["id"], c["code"], f"{c['code']} — {c['name_ar']}") for c in self._choices["suppliers"]
                if c["is_active"]]

    def _bank_account_rows(self) -> list[tuple[int, str, str]]:
        return [(b["id"], b["code"], f"{b['bank_name']} — {b['code']}" +
                 (f" ({b['account_number']})" if b.get("account_number") else ""))
                for b in self._choices["bank_accounts"] if b["is_active"] and b["is_base_currency"]]

    def _bank_rows(self) -> list[tuple[int, str, str]]:
        if self.DIRECTION == "out":
            return self._bank_account_rows()
        return [(b["id"], b["code"], b["name_ar"]) for b in self._choices["banks"] if b["is_active"]]

    def _project_rows(self) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name']}") for p in self._choices["projects"]
                if p["status"] == "open"]

    def _set_lookups(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
            self.party_edit.set_items(self._party_rows(), rec.get("party_id"),
                                      f"{rec.get('party_code', '')} — {rec.get('party_name', '')}")
            if self.DIRECTION == "in":
                self.bank_edit.set_items(self._bank_rows(), rec.get("drawer_bank_id"), rec.get("drawer_bank_name"))
            else:
                self.bank_edit.set_items(self._bank_rows(), rec.get("bank_account_id"), rec.get("bank_label"))
            self.project_edit.set_items(self._project_rows(), rec.get("project_id"), rec.get("project_code"))
        finally:
            self._filling = False

    # ---- التنبيهات ------------------------------------------------------------
    def refresh_alerts(self) -> None:
        try:
            self._alerts = self.service.alerts()
        except Exception:  # noqa: BLE001 - الكارت للعرض بس
            self._alerts = {"rows": [], "total": 0}
        for key, name, *_rest in BUCKETS:
            info = self._alerts.get(key) or {"count": 0, "amount": Decimal("0")}
            text = f"{name} {info['count']}"
            if info["count"]:
                text += f" · {ltr(money(info['amount']))}"
            self.alert_chips[key].setText(text)
        self.alert_button.setEnabled(bool(self._alerts.get("total")))

    def show_due(self) -> None:
        self.refresh_alerts()
        if not self._alerts.get("total"):
            messages.info(self, "تنبيهات الاستحقاق", f"مفيش شيكات مفتوحة مستحقة خلال {ALERT_DAYS} أيام. 👍")
            return
        dialog = self._due_dialog()
        if dialog is None or dialog.chosen is None:
            return
        self.open_cheque(dialog.chosen)
        if dialog.done_requested:
            self._on_check("done", True)

    def _due_dialog(self) -> DueChequesDialog | None:
        """نقطة منفصلة عشان الاختبارات."""
        dialog = DueChequesDialog(self, self._alerts)
        dialog.exec()
        return dialog

    def open_cheque(self, cheque_id: int) -> None:
        """يفتح الشيك في الشاشة (حتى لو برّا الفترة / الفلتر — بيتحوّل على «المفتوحة»)."""
        if self.mode in EDITING:
            return
        if not self.table.select_id(cheque_id):
            for chip in self.status_buttons.buttons():
                chip.setChecked(chip.property("value") == "open")
            self.status = "open"
            self.search.blockSignals(True)
            self.search.clear()
            self.search.blockSignals(False)
            self.page = 1
            self.refresh_list()
            if not self.table.select_id(cheque_id):
                self.load(cheque_id)

    # ---- الكروت ومعاينة القيد -------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        bank = self.bank_edit.currentData()
        return {"cheque_date": _pydate(self.date_edit.date()), "due_date": _pydate(self.due_edit.date()),
                "party_id": self.party_edit.currentData(), "cheque_no": self.cheque_no.text(),
                "drawer_bank_id": bank if self.DIRECTION == "in" else None,
                "bank_account_id": bank if self.DIRECTION == "out" else None,
                "amount": self.amount_edit.text(), "description": self.description.text() or self.kind.title,
                "project_id": self.project_edit.currentData()}

    def _refresh_side(self) -> None:
        """التحذيرات · الاستحقاق · الخطوات · القيد."""
        self._timer.stop()
        rec = self.current_record or {}
        editing = self.mode in EDITING
        due = _pydate(self.due_edit.date())
        status = "draft" if editing else rec.get("status")
        if (editing or rec) and status in ("portfolio", "under_collection", "bounced", "issued"):
            bucket = due_bucket(due, dt.date.today())
            color = BUCKET_COLORS.get(bucket, MUTED)
            self.due_label.setText(due_text(due, dt.date.today()))
            self.due_label.setStyleSheet(f"color:{color}; font-weight:900;")
        else:
            self.due_label.setText("")
        warnings: list[str] = []
        if editing:
            try:
                warnings = self.service.warnings(self._data(), None if self.mode == "new" else self.current_id)
            except Exception:  # noqa: BLE001
                warnings = []
        self.warn_label.setText("\n".join(f"⚠️ {w}" for w in warnings))
        self.warn_label.setVisible(bool(warnings))
        self._refresh_steps(editing)
        self._refresh_entry(editing)

    def _refresh_steps(self, editing: bool) -> None:
        rec = self.current_record or {}
        posted = not editing and rec.get("status") not in (None, "draft")
        status = rec.get("status") if posted else "draft"
        allowed = {e for e, _n in self.service.next_steps({"status": status})} if posted else set()
        can_post = self.can("post") and posted and rec.get("year_status") is not None
        self.steps_box.setEnabled(posted)
        for check in (self.deposit_check, self.done_check):
            check.blockSignals(True)
        if self.DIRECTION == "in":
            self.deposit_check.setChecked(status in ("under_collection", "collected"))
            self.done_check.setChecked(status == "collected")
            self.deposit_check.setText("إيداع تاني" if status == "bounced" else "تم الإيداع")
            self.deposit_check.setEnabled(can_post and (status in ("portfolio", "bounced")
                                                        or (status == "under_collection" and self.can("unpost"))))
            self.done_check.setEnabled(can_post and (status in ("portfolio", "under_collection", "bounced")
                                                     or (status == "collected" and self.can("unpost"))))
        else:
            self.done_check.setChecked(status == "cleared")
            self.done_check.setEnabled(can_post and (status == "issued" or (status == "cleared"
                                                                            and self.can("unpost"))))
        for check in (self.deposit_check, self.done_check):
            check.blockSignals(False)
        for event, button in self.step_buttons.items():
            button.setEnabled(can_post and event in allowed)
        if not posted:
            self.steps_hint.setText(f"الخطوات بتبان بعد «{self.kind.start_label} ✔».")
        elif not allowed:
            self.steps_hint.setText(f"{status_name(status)} — الشيك ده خلص دورته، مفيش خطوات تانية.")
        else:
            names = " · ".join(n for _e, n in self.service.next_steps({"status": status}))
            self.steps_hint.setText(f"الخطوة الجاية: {names}")
        events = self.service.events(int(self.current_id)) if posted and self.current_id is not None else []
        self.history.setRowCount(len(events))
        for r, e in enumerate(events):
            where = (f"{e['bank_name']} — {e['bank_code']}" if e.get("bank_code") else
                     f"{e['supplier_code']} — {e['supplier_name']}" if e.get("supplier_code") else "")
            values = (e["event_date"].strftime("%d/%m/%Y"), e["name_ar"] + ("" if e["has_entry"] else " (من غير قيد)"),
                      where, money(e["fees"]) if e["fees"] else "", e.get("created_by_name") or "")
            for c, value in enumerate(values):
                self.history.setItem(r, c, QTableWidgetItem(str(value)))

    def _refresh_entry(self, editing: bool) -> None:
        rec = self.current_record or {}
        posted = not editing and rec.get("status") not in (None, "draft")
        rows: list[dict[str, Any]] = []
        error = ""
        if posted:
            event, entry = self.service.last_entry(int(self.current_id))
            self.entry_box.setTitle(f"آخر قيد: {event['name_ar']} · {ltr(event['event_date'].strftime('%d/%m/%Y'))}"
                                    if event else "مفيش قيد")
            rows = list((entry or {}).get("lines") or [])
        elif editing or rec:
            self.entry_box.setTitle("القيد اللي هيتعمل مع " + self.kind.start_label)
            try:
                rows = self.service.preview(self._data(), None if self.mode == "new" else self.current_id)
            except BusinessError as exc:
                error = str(exc)
            except Exception:  # noqa: BLE001
                error = "مش قادر أجهّز القيد دلوقتي."
        _fill_entry(self.entry_table, rows)
        if error:
            self._paint_entry(False, f"✖ {error}")
        elif rows:
            d = sum((Decimal(r["debit"]) for r in rows), Decimal("0"))
            c = sum((Decimal(r["credit"]) for r in rows), Decimal("0"))
            self._paint_entry(d == c, f"✔ متوازن · مدين {ltr(money(d))} = دائن {ltr(money(c))}")
        else:
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")

    def _paint_entry(self, ok: bool, text: str) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.entry_label.setText(text)
        self.entry_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                       "padding:6px 10px; font-weight:900;")

    def _collect_preview(self, record: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
        """قيد التحصيل بعد إيداع لسه ماتعملش (للمعاينة في «تم التحصيل» من الحافظة)."""
        c = {**record, "bank_account_id": values.get("bank_account_id")}
        fees = Decimal(str(values.get("fees") or "0").replace(",", "") or "0")
        lines = self.service._lines(c, "collect", "under_collection", bank_account_id=values.get("bank_account_id"),
                                    fees=fees)
        return self.service._named(self.service.posting.build(self.kind.doc_type, lines,
                                                              project_id=record.get("project_id")))

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.party_edit, self.cheque_no, self.bank_edit, self.amount_edit, self.due_edit,
                self.description, self.project_edit]

    def _is_draft(self) -> bool:
        rec = self.current_record or {}
        return rec.get("status") == "draft" and rec.get("year_status") != "closed"

    def set_mode(self, mode: str) -> None:
        super().set_mode(mode)
        editing = mode in EDITING
        if not editing:
            rec = self.current_record or {}
            self.edit_button.setEnabled(self.edit_button.isEnabled() and self._is_draft())
            self.delete_button.setEnabled(self.delete_button.isEnabled() and self._is_draft()
                                          and not rec.get("cheque_ref"))
        for chip in self.status_buttons.buttons():
            chip.setEnabled(not editing)
        self.from_edit.setEnabled(not editing)
        self.to_edit.setEnabled(not editing)
        if hasattr(self, "alert_bar"):
            self.alert_bar.setEnabled(not editing)
        self._update_paging(editing)
        if hasattr(self, "entry_table"):
            self._changed()

    def update_extra_buttons(self) -> None:
        rec = self.current_record or {}
        editing = self.mode in EDITING
        self.post_button.setEnabled(self.can("post") and (
            (editing and self.can("save")) or
            (not editing and self.current_id is not None and self._is_draft())))
        self.undo_button.setEnabled(self.can("unpost") and not editing and self.current_id is not None
                                    and rec.get("status") not in (None, "draft"))

    def new_record(self) -> None:
        self._load_choices()
        super().new_record()
        if self.mode != "new":
            return
        self.date_edit.setDate(_qdate(dt.date.today()))
        self.due_edit.setDate(_qdate(dt.date.today()))
        self.number_edit.setText(f"بيتاخد مع «{self.kind.start_label}»")
        self._show_status(None)
        self._set_lookups()
        self.set_mode("new")
        self.party_edit.setFocus()

    def edit_record(self) -> None:
        if self.current_id is None or not self.can("edit"):
            return
        self._load_choices()
        super().edit_record()                                      # بيجيب الشيك من جديد
        if self.mode == "edit" and not self._is_draft():           # جهاز تاني رحّله في الوقت ده
            self.load(self.current_id)
            messages.warn(self, "تعديل", f"{self.kind.short} ده اترحّل — اعمل «تراجع» لحد ما يرجع مسودة عشان تعدّله.")

    # ---- الترحيل / الخطوات / التراجع --------------------------------------------------
    def _confirm_warnings(self, data: dict[str, Any], cheque_id: int | None) -> bool:
        try:
            warnings = self.service.warnings(data, cheque_id)
        except Exception:  # noqa: BLE001 - الحفظ نفسه هيطلّع الغلط
            return True
        if not warnings:
            return True
        return messages.ask_yes_no(self, "⚠️ تنبيه", "\n".join(warnings) + "\n\nتكمّل؟")

    def post_cheque(self) -> None:
        if not self.post_button.isEnabled():
            return
        start = self.kind.start_label
        if self.mode in EDITING:
            try:
                payload = self.collect()
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, f"تعذّر الـ{start}", exc)
                return
            if not self._confirm_warnings(payload, None if self.mode == "new" else self.current_id):
                return
            if not messages.ask_yes_no(self, start, f"هيتحفظ {self.kind.short} ويتعمله «{start}» بقيد — بعد كده "
                                                    "مايتعدّلش غير بـ «تراجع عن آخر خطوة». تكمّل؟"):
                return
            try:
                saved = self.service.save_draft(None if self.mode == "new" else int(self.current_id), payload,
                                                expected_version=self.current_version, post=True)
            except ConcurrentEditError as exc:
                self.on_conflict(exc)
                return
            except Exception as exc:  # noqa: BLE001
                messages.show_error(self, f"تعذّر الـ{start}", exc)
                return
            self.current_id = saved
            self.mode = "view"
        else:
            rec = self.current_record or {}
            if not self._confirm_warnings(self._record_data(rec), self.current_id):
                return
            if not messages.ask_yes_no(self, start, f"«{start}» {self.kind.short} {cheque_label(rec)}؟"):
                return
            try:
                self.service.post(int(self.current_id), expected_version=self.current_version)
            except Exception as exc:  # noqa: BLE001
                self.report_error(f"تعذّر الـ{start}", exc)
                return
        self._after_action()
        messages.info(self, f"تم الـ{start}",
                      f"اتسجّل {self.kind.short} برقم {ltr((self.current_record or {}).get('cheque_ref'))}.")

    def _ask_step(self, event: str, title: str, complete: bool = False) -> dict[str, Any] | None:
        """نافذة الخطوة ← القيم (أو ``None`` لو اتلغت) — نقطة منفصلة عشان الاختبارات."""
        self._load_choices()
        dialog = StepDialog(self, event, title, complete=complete)
        return dialog.values() if dialog.exec() == QDialog.Accepted else None

    def run_step(self, event: str, title: str) -> None:
        """زرار خطوة (رجع من البنك · تظهير · رد · إلغاء) أو تشيك بوكس «تم الإيداع»."""
        if self.current_id is None or self.mode in EDITING:
            return
        values = self._ask_step(event, title)
        if values is None:
            self._refresh_steps(False)
            return
        try:
            self.service.do_step(int(self.current_id), event, values["event_date"],
                                 expected_version=self.current_version, bank_account_id=values["bank_account_id"],
                                 supplier_id=values["supplier_id"], fees=values["fees"], note=values["note"])
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر تنفيذ الخطوة", exc)
            self._refresh_steps(False)
            return
        self._after_action()

    def complete_cheque(self) -> None:
        """التشيك بوكس «تم التحصيل» / «تم الصرف»."""
        if self.current_id is None or self.mode in EDITING:
            return
        values = self._ask_step("collect" if self.DIRECTION == "in" else "clear", self.kind.done_label,
                                complete=True)
        if values is None:
            self._refresh_steps(False)
            return
        if self.DIRECTION == "out":
            rec = self.current_record or {}
            short = self.service._bank_short(int(rec["bank_account_id"]), Decimal("0"), None)
            if short and not messages.ask_yes_no(self, "⚠️ الرصيد", f"{short}\n\nتكمّل الصرف؟"):
                self._refresh_steps(False)
                return
        try:
            self.service.complete(int(self.current_id), values["event_date"], expected_version=self.current_version,
                                  bank_account_id=values["bank_account_id"], fees=values["fees"])
        except Exception as exc:  # noqa: BLE001
            self.report_error(f"تعذّر «{self.kind.done_label}»", exc)
            self._refresh_steps(False)
            return
        self._after_action()

    def _on_check(self, which: str, on: bool) -> None:
        """التشيك بوكس: علامة = الخطوة · شيل العلامة = تراجع (لو هي آخر خطوة)."""
        rec = self.current_record or {}
        status = rec.get("status")
        if on:
            if which == "deposit":
                self.run_step("deposit", "إيداع تاني" if status == "bounced" else "إيداع للتحصيل")
            elif status == "collected" or status == "cleared":
                self._refresh_steps(False)
            else:
                self.complete_cheque()
            return
        last = {"deposit": "under_collection", "done": "collected" if self.DIRECTION == "in" else "cleared"}[which]
        if status != last:
            messages.warn(self, "تراجع", "التراجع بيبقى عن آخر خطوة بس — اتراجع عن «تم التحصيل» الأول.")
            self._refresh_steps(False)
            return
        self.undo_step()

    def undo_step(self) -> None:
        if self.current_id is None or not self.can("unpost"):
            self._refresh_steps(False)
            return
        rec = self.current_record or {}
        events = self.service.events(int(self.current_id))
        if not events:
            return
        last = events[-1]
        back = "مسودة (برقمه)" if last["from_status"] == "draft" else f"«{status_name(last['from_status'])}»"
        if not messages.ask_yes_no(self, "تراجع عن آخر خطوة",
                                   f"آخر خطوة «{last['name_ar']}» ({last['event_date'].strftime('%d/%m/%Y')}) "
                                   f"وقيدها هيتمسحوا و{self.kind.short} {cheque_label(rec)} هيرجع {back}. تكمّل؟"):
            self._refresh_steps(False)
            return
        try:
            self.service.undo_last(int(self.current_id), expected_version=self.current_version)
        except Exception as exc:  # noqa: BLE001
            self.report_error("تعذّر التراجع", exc)
            self._refresh_steps(False)
            return
        self._after_action()

    def _after_action(self) -> None:
        self.refresh_list()
        if not self.load(self.current_id):
            self.show_empty()
        else:
            self.table.select_id(self.current_id)
            self._refresh_side()                                   # الخطوات والقيد على طول (من غير ما نستنى التايمر)
        self.refresh_alerts()
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
        self._counts = self.service.count_cheques(*query)
        self.pages = max(1, -(-self._counts["total"] // PAGE_SIZE))
        self.page = min(self.page, self.pages)
        return self.service.list_cheques(*query, page=self.page, page_size=PAGE_SIZE)

    def format_cell(self, key: str, value: Any, row: dict) -> str:
        if key == "cheque_ref":
            return value or f"مسودة {row.get('draft_no')}"
        if key == "amount":
            return money(value)
        if key == "status":
            return status_name(value)
        if key == "due_date" and value is not None:
            text = value.strftime("%d/%m/%Y")
            if row.get("status") in ("portfolio", "under_collection", "bounced", "issued"):
                bucket = due_bucket(value, dt.date.today())
                if bucket != "later":
                    text += f"  ({due_text(value, dt.date.today())})"
            return text
        return default_format(key, value, row)

    def refresh_list(self) -> None:
        super().refresh_list()
        cols = [k for k, _t in self.COLUMNS]
        status_col, amount_col, due_col = cols.index("status"), cols.index("amount"), cols.index("due_date")
        today = dt.date.today()
        for r, row in enumerate(self.table.rows):
            item = self.table.item(r, status_col)
            if item is not None:
                item.setForeground(QColor(STATUSES.get(row.get("status"), ("", MUTED, ""))[1]))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            item = self.table.item(r, amount_col)
            if item is not None:
                item.setForeground(QColor(KIND_COLORS[self.DIRECTION]))
            item = self.table.item(r, due_col)
            if item is not None and row.get("status") in ("portfolio", "under_collection", "bounced", "issued"):
                bucket = due_bucket(row["due_date"], today)
                if bucket in BUCKET_COLORS:
                    item.setForeground(QColor(BUCKET_COLORS[bucket]))
        counts = getattr(self, "_counts", None)
        if counts is not None:
            where = "مفتوح (كل الفترات)" if self.status == "open" else "في الفترة"
            self.count_label.setText(f"{counts['total']:,} شيك {where} · مسودة {counts['drafts']:,} · "
                                     f"مفتوح {counts['open']:,} ({money(counts['open_amount'])} جنيه)")
        self._update_paging(self.mode in EDITING)

    def refresh_screen(self) -> None:
        super().refresh_screen()
        self.refresh_alerts()

    # ---- البيانات ----------------------------------------------------------
    def _show_status(self, record: dict | None) -> None:
        if not record:
            self.status_label.setText("جديد")
            self.status_label.setStyleSheet(BADGE.format(bg="#EFF6FF", fg="#1D4ED8", bd="#BFDBFE"))
            self.info_label.hide()
            return
        style, text = status_badge(record["status"])
        self.status_label.setText(text)
        self.status_label.setStyleSheet(style)
        self.status_label.setMinimumWidth(self.status_label.fontMetrics().horizontalAdvance(text) + 30)
        bits = []
        if record.get("direction") == "in" and record.get("bank_code") and record["status"] != "draft":
            bits.append(f"🏦 اتودع في {record['bank_name']} — {ltr(record['bank_code'])}")
        if record.get("direction") == "in" and record["status"] == "endorsed":
            bits.append(f"🔁 اتظهّر لـ {record['supplier_code']} — {record['supplier_name']}")
        if record["status"] != "draft" and record.get("posted_by_name"):
            when = record["posted_at"].astimezone().strftime("%d/%m/%Y %H:%M") if record.get("posted_at") else ""
            bits.append(f"✔ {self.kind.start_label} بواسطة {ltr(record['posted_by_name'])} — {ltr(when)}")
        if record.get("year_status") == "closed" and record["status"] == "draft":
            bits.insert(0, f"🔒 السنة المالية {record['year_code']} مقفولة — الشيك ده مايتعدّلش.")
        self.info_label.setText(" · ".join(bits))
        self.info_label.setStyleSheet(f"color:{MUTED}; font-weight:800;")
        self.info_label.setVisible(bool(bits))

    def fetch_record(self, record_id: Any) -> dict | None:
        return self.service.get_cheque(int(record_id))

    def fill_form(self, record: dict) -> None:
        self._filling = True
        try:
            self.number_edit.setText(record.get("cheque_ref") or
                                     f"مسودة {record.get('draft_no')} — الرقم الرسمي مع «{self.kind.start_label}»")
            self.date_edit.setDate(_qdate(record["cheque_date"]))
            self.due_edit.setDate(_qdate(record["due_date"]))
            self.cheque_no.setText(record.get("cheque_no") or "")
            self.amount_edit.setText(money(record.get("amount")))
            self.description.setText(record.get("description") or "")
            self._show_status(record)
        finally:
            self._filling = False
        self._set_lookups(record)
        self._changed()

    def clear_form(self) -> None:
        self._filling = True
        try:
            for edit in (self.number_edit, self.cheque_no, self.amount_edit, self.description):
                edit.clear()
            self.date_edit.setDate(_qdate(dt.date.today()))
            self.due_edit.setDate(_qdate(dt.date.today()))
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.due_label.setText("")
            self.info_label.hide()
        finally:
            self._filling = False
        self._set_lookups()
        if hasattr(self, "entry_table"):
            self.entry_table.setRowCount(0)
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")
            self.history.setRowCount(0)
            self.warn_label.hide()

    def _record_data(self, rec: dict[str, Any]) -> dict[str, Any]:
        out = {k: rec.get(k) for k in ("cheque_date", "due_date", "party_id", "cheque_no", "drawer_bank_id",
                                       "bank_account_id", "amount", "description", "project_id")}
        return out

    def collect(self) -> dict:
        data = self._data()
        data["description"] = self.description.text()
        return data

    def save_record(self, payload: dict, record_id: Any) -> Any:
        return self.service.save_draft(None if record_id is None else int(record_id), payload,
                                       expected_version=self.current_version)

    def delete_record(self, record_id: Any) -> None:
        self.service.delete_cheque(int(record_id), expected_version=self.current_version)


class CustomerChequesScreen(ChequesScreen):
    """شيكات العملاء (الوارد)."""
    TARGET = "customer_cheques"
    DIRECTION = "in"
    TITLE = "شيكات العملاء"
    ICON = "📩"
    FORM_TITLE = "شيك وارد"
    SEARCH_PLACEHOLDER = "🔍 رقم المستند أو الشيك أو العميل أو البيان… (F1)"


class SupplierChequesScreen(ChequesScreen):
    """شيكات الموردين (الصادر)."""
    TARGET = "supplier_cheques"
    DIRECTION = "out"
    TITLE = "شيكات الموردين"
    ICON = "📨"
    FORM_TITLE = "شيك صادر"
    SEARCH_PLACEHOLDER = "🔍 رقم المستند أو الشيك أو المورد أو البيان… (F1)"
    COLUMNS = [("cheque_ref", "المستند"), ("cheque_no", "رقم الشيك"), ("party_name", "المورد"),
               ("bank_label", "من حسابنا"), ("due_date", "الاستحقاق"), ("amount", "المبلغ"), ("status", "الحالة")]
