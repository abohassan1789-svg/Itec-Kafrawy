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
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.voucher_service import KINDS, PAGE_SIZE, VoucherService, voucher_label
from app.services.errors import BusinessError, ConcurrentEditError
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


def _qdate(d: dt.date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> dt.date:
    return dt.date(q.year(), q.month(), q.day())


def signed(value: Decimal | None, natural: str = "مدين") -> str:
    """الرصيد بجنبه: «1,200.00 مدين» / «300.00 دائن» / «0.00».

    ``natural`` = الجنب الطبيعي للرصيد الموجب (المورد رصيده دائن: الموجب = ليه علينا)."""
    if value is None:
        return "—"
    if value == 0:
        return "0.00"
    other = "دائن" if natural == "مدين" else "مدين"
    return f"{ltr(money(abs(value)))} {natural if value > 0 else other}"


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
        self._choices: dict[str, Any] = {"parties": [], "treasuries": [], "banks": [], "projects": []}
        self._filling = False
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
        form.setSpacing(7)
        form.setContentsMargins(14, 18, 14, 10)
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
        top = QHBoxLayout()
        top.addWidget(self.number_edit, 2)
        top.addWidget(QLabel("التاريخ *"))
        top.addWidget(self.date_edit, 1)
        top.addWidget(QLabel("الحالة"))
        top.addWidget(self.status_label)
        form.addRow("رقم السند", top)

        self.party_edit = LookupEdit(f"اكتب كود {self.kind.party_label} أو اسمه… *")
        self.party_edit.value_changed.connect(self._changed)
        form.addRow(f"{self.kind.party_verb} *", self.party_edit)

        self.source_kind = QComboBox()
        self.source_kind.addItem("💵 خزنة", "treasury")
        self.source_kind.addItem("🏦 بنك", "bank")
        self.source_kind.currentIndexChanged.connect(self._on_source_kind)
        self.source_edit = LookupEdit("اختار الخزنة… *")
        self.source_edit.value_changed.connect(self._changed)
        src = QHBoxLayout()
        src.addWidget(self.source_kind)
        src.addWidget(self.source_edit, 1)
        form.addRow(f"{self.kind.source_verb} *", src)

        self.amount_edit = QLineEdit()
        self.amount_edit.setLayoutDirection(Qt.LeftToRight)
        self.amount_edit.setPlaceholderText("0.00")
        self.amount_edit.textChanged.connect(self._changed)
        form.addRow("المبلغ *", self.amount_edit)
        self.description = QLineEdit()
        self.description.setMaxLength(500)
        self.description.setPlaceholderText("السند ده عن إيه (زي: تحصيل دفعة من الحساب)" if self.KIND == "receipt"
                                            else "السند ده عن إيه (زي: سداد دفعة من حساب المورد)")
        form.addRow("البيان *", self.description)
        self.paper_no = QLineEdit()
        self.paper_no.setMaxLength(30)
        self.paper_no.setPlaceholderText("اختياري")
        self.project_edit = LookupEdit("اختياري")
        self.project_edit.value_changed.connect(self._changed)
        extra = QHBoxLayout()
        extra.addWidget(self.paper_no, 1)
        extra.addWidget(QLabel("أمر التوريد"))
        extra.addWidget(self.project_edit, 2)
        form.addRow("رقم الدفتر الورقي", extra)
        self.year_note = QLabel("")
        self.year_note.setWordWrap(True)
        self.year_note.hide()
        form.addRow(self.year_note)

    def _build_kpis(self) -> QWidget:
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.kpi_values: dict[str, QLabel] = {}
        self.kpi_captions: dict[str, QLabel] = {}
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
            col.addWidget(caption)
            col.addWidget(value)
            row.addWidget(card, 1 if key == "amount" else 2)
            self.kpi_values[key] = value
            self.kpi_captions[key] = caption
        return bar

    def _build_entry(self) -> QGroupBox:
        self.entry_box = QGroupBox("القيد اللي هيتعمل لما يترحّل")
        col = QVBoxLayout(self.entry_box)
        col.setContentsMargins(10, 18, 10, 8)
        col.setSpacing(6)
        self.entry_table = QTableWidget(0, 3)
        self.entry_table.setHorizontalHeaderLabels(["الحساب", "مدين", "دائن"])
        self.entry_table.verticalHeader().setVisible(False)
        self.entry_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.entry_table.setSelectionMode(QAbstractItemView.NoSelection)
        header = self.entry_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            self.entry_table.setColumnWidth(c, 130)
        self.entry_table.setMinimumHeight(110)
        self.entry_table.setMaximumHeight(170)
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
        right.addWidget(self._build_kpis())
        self.funds_label = QLabel("")
        self.funds_label.setWordWrap(True)
        self.funds_label.setStyleSheet(WARN_STYLE)
        self.funds_label.hide()
        right.addWidget(self.funds_label)
        right.addWidget(self._build_entry())
        right.addStretch(1)
        content.addWidget(holder, self.FORM_STRETCH)                # RTL: أول عنصر = يمين
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
    def _load_choices(self) -> None:
        """بتتحمّل من جديد مع «سند جديد» و«تعديل» (عشان اللي اتضاف من جهاز تاني يظهر)."""
        try:
            self._choices = self.service.choices()
        except Exception:  # noqa: BLE001 - الرسالة بتطلع مع تحميل القائمة
            self._choices = {"parties": [], "treasuries": [], "banks": [], "projects": []}

    def _party_rows(self) -> list[tuple[int, str, str]]:
        return [(c["id"], c["code"], f"{c['code']} — {c['name_ar']}") for c in self._choices["parties"]
                if c["is_active"]]

    def _source_rows(self, kind: str) -> list[tuple[int, str, str]]:
        if kind == "treasury":
            return [(t["id"], t["code"], f"{t['code']} — {t['name_ar']}") for t in self._choices["treasuries"]
                    if t["is_active"] and t["is_base_currency"]]
        return [(b["id"], b["code"], f"{b['bank_name']} — {b['code']}" +
                 (f" ({b['account_number']})" if b.get("account_number") else ""))
                for b in self._choices["banks"] if b["is_active"] and b["is_base_currency"]]

    def _project_rows(self) -> list[tuple[int, str, str]]:
        return [(p["id"], p["code"], f"{p['code']} — {p['name']}") for p in self._choices["projects"]
                if p["status"] == "open"]

    def _set_lookups(self, record: dict | None = None) -> None:
        rec = record or {}
        self._filling = True
        try:
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

    # ---- الكروت ومعاينة القيد -------------------------------------------------
    def _changed(self, *_args: Any) -> None:
        if not self._filling:
            self._timer.start()

    def _data(self) -> dict[str, Any]:
        kind = self.source_kind.currentData()
        source = self.source_edit.currentData()
        return {"voucher_date": _pydate(self.date_edit.date()), "party_id": self.party_edit.currentData(),
                "source_kind": kind, "treasury_id": source if kind == "treasury" else None,
                "bank_account_id": source if kind == "bank" else None, "amount": self.amount_edit.text(),
                "description": self.description.text() or self.kind.title, "paper_no": self.paper_no.text(),
                "project_id": self.project_edit.currentData()}

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
        self.kpi_captions["source"].setText(f"رصيد {what} {when}")
        self.kpi_captions["party"].setText(f"رصيد {self.kind.party_label} {when}")
        try:
            b = self.service.balances(data["party_id"], kind, data["treasury_id"] or data["bank_account_id"],
                                      data["amount"], posted=posted)
        except Exception:  # noqa: BLE001 - الكروت للعرض بس
            b = None
        if b is None:
            self._clear_side(keep_entry=True)
        else:
            self.kpi_values["amount"].setText(ltr(money(b["amount"])) if b["amount"] else "—")
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
                self.funds_label.setText(f"⚠️ رصيد {what} «{b['source_name']}» مش هيكفي ({ltr(money(b['source']))}) — "
                                         "تقدر ترحّل بس هيجيلك تحذير.")
            self.funds_label.setVisible(short)
        self._refresh_entry(data, posted)

    def _clear_side(self, keep_entry: bool = False) -> None:
        for key in ("amount", "party", "source"):
            self.kpi_values[key].setText("—")
            self.kpi_values[key].setStyleSheet(VALUE_STYLE)
        self.funds_label.hide()
        if not keep_entry:
            self.entry_table.setRowCount(0)
            self.entry_label.setText("")
            self.entry_label.setStyleSheet("")
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")

    def _refresh_entry(self, data: dict[str, Any], posted: bool) -> None:
        rows: list[dict[str, Any]] = []
        error = ""
        if posted:
            self.entry_box.setTitle("القيد المرحّل")
            entry = self.service.entry_for(int(self.current_id)) if self.current_id is not None else None
            for line in (entry or {}).get("lines") or []:
                party = f" — {line['party_name']}" if line.get("party_name") else ""
                rows.append({"account": f"{line['account_code']} {line['account_name']}{party}",
                             "debit": line["debit"], "credit": line["credit"]})
        else:
            self.entry_box.setTitle("القيد اللي هيتعمل لما يترحّل")
            try:
                for line in self.service.preview(data):
                    rows.append({"account": f"{line['account_code']} {line['account_name']}",
                                 "debit": line["debit"], "credit": line["credit"]})
            except BusinessError as exc:
                error = str(exc)
            except Exception:  # noqa: BLE001
                error = "مش قادر أجهّز القيد دلوقتي."
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

    def _paint_entry(self, ok: bool) -> None:
        fg, bg, bd = (OK_COLOR, "#ECFDF3", "#B7E4C7") if ok else (BAD, "#FEE2E2", "#FECACA")
        self.entry_label.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:6px; "
                                       "padding:6px 10px; font-weight:900;")

    # ---- الأوضاع ----------------------------------------------------------
    def editable_widgets(self) -> list[QWidget]:
        return [self.date_edit, self.party_edit, self.source_kind, self.source_edit, self.amount_edit,
                self.description, self.paper_no, self.project_edit]

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
        self._set_lookups()
        rows = self._source_rows("treasury")
        if rows:
            self.source_edit.set_value(rows[0][0])
        self.set_mode("new")
        self.party_edit.setFocus()

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
            return money(value)
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
        self._set_lookups(record)
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
        self._set_lookups()
        if hasattr(self, "entry_table"):
            self._clear_side()

    @staticmethod
    def _record_data(rec: dict[str, Any]) -> dict[str, Any]:
        return {k: rec.get(k) for k in ("voucher_date", "party_id", "source_kind", "treasury_id",
                                        "bank_account_id", "amount", "description", "paper_no", "project_id")}

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
    """سند قبض — من العميل."""
    TARGET = "customer_receipts"
    KIND = "receipt"
    TITLE = "سند قبض"
    ICON = "📥"
    FORM_TITLE = "سند قبض"
    SEARCH_PLACEHOLDER = "🔍 رقم السند أو العميل أو البيان… (F1)"


class PaymentsScreen(VoucherScreen):
    """سند صرف — للمورد."""
    TARGET = "supplier_payments"
    KIND = "payment"
    TITLE = "سند صرف"
    ICON = "📤"
    FORM_TITLE = "سند صرف"
    SEARCH_PLACEHOLDER = "🔍 رقم السند أو المورد أو البيان… (F1)"
    COLUMNS = [("voucher_no", "رقم السند"), ("voucher_date", "التاريخ"), ("party_name", "المورد"),
               ("amount", "المبلغ"), ("status", "الحالة")]
