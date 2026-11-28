"""«فاتورة البيع / إذن الصرف» — المرحلة 8، النموذج 1 (اختيار المستخدم 2026-11-28 — سكريبت 0051).

«زي فاتورة الشراء وعكسها» (``purchase_invoice_service``):

* الفاتورة = عميل + أصناف (صنف · مخزن · وحدة من وحداته · كمية · سعر · خصم % · كود ضريبة) — المخزن الافتراضي من
  الرأس · **المخزن الوهمي ممنوع** · الصنف الخدمي من غير مخزن ومن غير تكلفة.
* **السعر الافتراضي** = «سعر البيع» من الصنف × معامل الوحدة ÷ سعر الصرف (لو الصنف مالوش سعر ← آخر سعر للعميل ده)
  ويتعدّل · البيع بأقل من التكلفة ⚠ تنبيه بس.
* **«ترحيل»** (أو حفظ + ترحيل في نفس الـ transaction) ← القاعدة بتصرف ``INV-2026-00001`` أول مرة بس، و**دفتر
  المخزون** بيطلّع كل صنف مخزني **بالمتوسط المرجّح** (جوه قفل الصنف — البيع أكتر من المتاح ممنوع إلا بصلاحية «من
  غير رصيد»)، و**محرك الترحيل** بيعمل **قيد واحد**: مدين العميل بالإجمالي · مدين «خصم مسموح به» بالخصم قبل الضريبة ·
  دائن المبيعات (من نوع الصنف — الخدمي من غير حساب مبيعات على نوعه ← «إيراد الخدمات») بالإجمالي قبل الخصم · دائن
  ض.ق.م مخرجات · مدين تكلفة المبيعات / دائن المخزون بتكلفة الحركات. «نقدي» = **قيد قبض لوحده** مع الفاتورة.
* **حد الائتمان** (وقت الترحيل وجوه قفل صف العميل): (رصيد العميل بالجنيه + الفاتورة الآجلة) > الحد ← ممنوع إلا
  بصلاحية «تجاوز حد الائتمان» · الحد صفر = مفيش حد · النقدي مابيتحسبش · «عميل نقدي» نقدي بس.
* **العملات:** «عملة واحدة» (عملة العميل بسعرها بتاريخ الفاتورة ويتعدّل) أو «متعدد العملات» (كل سطر بعملته) — دين
  العميل **سطر مدين لكل (عملة + سعر)** ومعاه العملة والسعر والمبلغ بالعملة.
* ``review`` = المراجعة قبل الترحيل (✔ / ✖ / ⚠ / ℹ) + المتاح + حد الائتمان + القيد المتوقع (التكلفة بالمتوسط دلوقتي) +
  **التكلفة والربح بصلاحية «إظهار التكلفة والربح»** (المرحّلة: التكلفة اللي اتجمّدت على الحركة).
* «إلغاء الترحيل» بالصلاحية في السنة المفتوحة: الحركات والقيدين بيتمسحوا والفاتورة ترجع مسودة **برقمها** ·
  الرقم الإلكتروني يتكتب حتى بعد الترحيل.
* كل حاجة في ``sec.audit_log`` قبل/بعد (بالسطور) + ``row_version`` (جهازين على نفس الفاتورة).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.sales_invoice_repository import STATUSES, SalesInvoiceRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService
from app.services.purchase_invoice_service import (
    CURRENCY_MODES,
    MAX_LINES,
    MAX_PRICE,
    MAX_QTY,
    MAX_RATE,
    PAY_KINDS,
    SOURCE_KINDS,
    calculate as _calculate,
    fmt,
    fmt_rate,
    parse_number,
)
from app.services.stock_ledger_service import KINDS, StockLedgerService, StockLine, fmt_qty

PAGE_SIZE = 50
ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
STEP6 = Decimal("0.000001")
DOC_TYPE = "sales_invoice"               # القيد (acc.posting_doc_types) + دفتر المخزون (inv.stock_doc_types)
RECEIPT_DOC = "sales_receipt"            # قيد القبض النقدي
ENTITY, ENTITY_LABEL = "sales.sales_invoices", "فاتورة بيع"
PERM = "sales.sales_invoices"


def invoice_label(row: dict[str, Any]) -> str:
    """«INV-2026-00012» أو «مسودة 7»."""
    return row.get("invoice_no") or f"مسودة {row.get('draft_no')}"


def _r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def calculate(lines: list[dict[str, Any]], *, discount_amount: Any = ZERO, prices_include_vat: bool = False,
              statement: bool = False, currency_mode: str = "single", base_currency: str = "EGP") -> dict[str, Any]:
    """نفس حسبة فاتورة الشراء + **الخصم قبل الضريبة بالجنيه** لكل سطر (``discount_base`` — بيتقيّد على 5303،
    والمبيعات = ``net_base + discount_base``). في «شامل الضريبة» الخصم بيتشال منه نصيب الضريبة."""
    calc = _calculate(lines, discount_amount=discount_amount, prices_include_vat=prices_include_vat,
                      statement=statement, currency_mode=currency_mode, base_currency=base_currency)
    for r in calc["lines"]:
        r.pop("unit_cost", None)
        disc = Decimal(r["line_discount"]) + Decimal(r["header_discount"])
        rate = ZERO if statement else Decimal(r.get("tax_rate") or 0)
        ex_vat = _r2(disc / (1 + rate / 100)) if prices_include_vat and rate and disc else disc
        r["discount_fc"] = ex_vat
        r["discount_base"] = _r2(ex_vat * Decimal(r["fx_rate"]))
        r["revenue_base"] = r["net_base"] + r["discount_base"]
    calc["discount_base"] = sum((r["discount_base"] for r in calc["lines"]), ZERO)
    calc["revenue_base"] = sum((r["revenue_base"] for r in calc["lines"]), ZERO)
    return calc


class SalesInvoiceService:
    def __init__(self, db: Database | None = None, repository: SalesInvoiceRepository | None = None,
                 ledger: StockLedgerService | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SalesInvoiceRepository(self.db)
        self.ledger = ledger or StockLedgerService(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- الصلاحيات الخاصة ---------------------------------------------------
    @staticmethod
    def can_show_cost() -> bool:
        return SESSION.can(f"{PERM}.show_cost")

    @staticmethod
    def can_over_credit() -> bool:
        return SESSION.can(f"{PERM}.over_credit")

    @staticmethod
    def can_negative() -> bool:
        return SESSION.can(f"{PERM}.negative")

    # ---- قراية -------------------------------------------------------------
    def list_invoices(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                      keyword: str = "", customer_id: int | None = None, page: int = 1,
                      page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_invoices(date_from, date_to, status, keyword.strip(), customer_id,
                                             limit=page_size, offset=offset)

    def count_invoices(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                       keyword: str = "", customer_id: int | None = None) -> dict[str, Any]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_invoices(date_from, date_to, status, keyword.strip(), customer_id)
        return {**{k: int(row[k]) for k in ("total", "drafts", "posted")}, "total_base": Decimal(row["total_base"])}

    def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        row = self.repository.get_invoice(int(invoice_id))
        if row is not None:
            row["label"] = invoice_label(row)
            row["lines"] = self.repository.lines(int(invoice_id))
        return row

    def choices(self) -> dict[str, Any]:
        """العملاء · المخازن · الأصناف بوحداتها (``units[item_id]`` = [(unit_id، الاسم، المعامل)] — الأساسية الأول) ·
        أكواد الضريبة · العملات · الخزائن والبنوك · أوامر التوريد · الموظفين."""
        items = self.repository.item_choices()
        units: dict[int, list[tuple[int, str, Decimal]]] = {
            int(i["id"]): [(int(i["unit_id"]), i["unit_name"], ONE)] for i in items}
        for u in self.repository.item_units():
            units.setdefault(int(u["item_id"]), []).append((int(u["unit_id"]), u["unit_name"], Decimal(u["factor"])))
        return {"customers": self.repository.customer_choices(), "warehouses": self.repository.warehouse_choices(),
                "items": items, "units": units, "tax_codes": self.repository.tax_codes(),
                "currencies": self.repository.currencies(), "treasuries": self.repository.treasury_choices(),
                "banks": self.repository.bank_account_choices(), "projects": self.repository.project_choices(),
                "employees": self.repository.employee_choices(), "base_currency": self.repository.base_currency()}

    def rate_on(self, currency_code: str, day: dt.date) -> Decimal | None:
        return self.repository.rate_on(str(currency_code), day)

    def customer_defaults(self, customer_id: int, invoice_date: dt.date) -> dict[str, Any]:
        """لما العميل يتختار: العملة وسعرها · الاستحقاق (التاريخ + مدة السداد) · كود الضريبة لو 0% (تصدير / معفي) ·
        «عميل نقدي» ← نقدي."""
        c = self.repository.customer(int(customer_id))
        if c is None:
            raise BusinessError("العميل مش موجود (ممكن يكون اتمسح).")
        tax = None
        if c["vat_tax_code_id"]:
            codes = {int(t["id"]): t for t in self.repository.tax_codes()}
            code = codes.get(int(c["vat_tax_code_id"]))
            if code is not None and Decimal(code["rate"]) == 0:
                tax = int(code["id"])
        return {"currency_code": c["currency_code"], "fx_rate": self.rate_on(c["currency_code"], invoice_date),
                "due_date": invoice_date + dt.timedelta(days=int(c["payment_terms_days"] or 0)),
                "terms": int(c["payment_terms_days"] or 0), "tax_code_id": tax, "tax_reg_no": c["tax_reg_no"],
                "is_cash": bool(c["is_system"]), "credit_limit": Decimal(c["credit_limit"] or 0)}

    def default_price(self, item_id: int, factor: Any = ONE, fx_rate: Any = ONE,
                      customer_id: int | None = None) -> Decimal | None:
        """السعر الافتراضي للوحدة المختارة بعملة السطر: سعر البيع × المعامل ÷ سعر الصرف — ولو مالوش ← آخر سعر للعميل."""
        rate = Decimal(str(fx_rate or 0))
        if rate <= 0:
            return None
        item = self.repository.items([int(item_id)]).get(int(item_id))
        if item is None:
            return None
        base_price = Decimal(item["sale_price"]) if item["sale_price"] is not None else None
        if base_price is None and customer_id:
            last = self.repository.last_prices(int(customer_id), [int(item_id)]).get(int(item_id))
            base_price = Decimal(last["unit_base"]) if last else None
        if base_price is None:
            return None
        return (base_price * Decimal(str(factor or 1)) / rate).quantize(CENT, rounding=ROUND_HALF_UP)

    def item_info(self, item_id: int, warehouse_id: int | None, stock_kind: str = "invoice") -> dict[str, Any]:
        """لخانة «إضافة صنف»: المتاح في المخزن · سعر البيع · المتوسط (بس لو عنده «إظهار التكلفة والربح»)."""
        kind = stock_kind if stock_kind in KINDS else "invoice"
        item = self.repository.items([int(item_id)]).get(int(item_id)) or {}
        return {"available": self.ledger.available(int(item_id), warehouse_id, kind) if warehouse_id else None,
                "sale_price": item.get("sale_price"),
                "avg": self.ledger.average_cost(int(item_id), kind) if self.can_show_cost() else None}

    def customer_balance(self, customer_id: int) -> dict[str, Any]:
        cid = int(customer_id)
        return {"base": self.repository.customer_balance(cid),
                "by_currency": self.repository.customer_balance_by_currency(cid)}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    @staticmethod
    def _text(value: Any, label: str, limit: int) -> str | None:
        text = " ".join(str(value or "").split()) or None
        if text and len(text) > limit:
            raise BusinessError(f"«{label}» أطول من المسموح ({limit} حرف).")
        return text

    def _header(self, data: dict[str, Any], *, relaxed: bool = False) -> dict[str, Any]:
        try:
            return self._header_strict(data)
        except BusinessError:
            if not relaxed:
                raise
        fixed = {**data, "customer_id": self._id(data.get("customer_id")) or -1, "due_date": None,
                 "pay_kind": "credit"}
        try:
            rate = parse_number(data.get("fx_rate"), "سعر الصرف", maximum=MAX_RATE)
        except BusinessError:
            rate = ZERO
        fixed["fx_rate"] = rate if rate > 0 else Decimal("0.000001")
        header = self._header_strict(fixed)
        if header["customer_id"] == -1:
            header["customer_id"] = None
        return header

    def _header_strict(self, data: dict[str, Any]) -> dict[str, Any]:
        day = data.get("invoice_date")
        if isinstance(day, dt.datetime):
            day = day.date()
        if not isinstance(day, dt.date):
            raise BusinessError("تاريخ الفاتورة مطلوب.")
        customer_id = self._id(data.get("customer_id"))
        if customer_id is None:
            raise BusinessError("اختار العميل.")
        kind = data.get("stock_kind") or "invoice"
        if kind not in KINDS:
            raise BusinessError("نوع الفاتورة لازم «فاتورة» أو «بيان».")
        base = self.repository.base_currency()
        mode = data.get("currency_mode") or "single"
        if mode not in CURRENCY_MODES:
            raise BusinessError("العملة لازم «عملة واحدة» أو «متعدد العملات».")
        if mode == "multi":
            currency, rate = base, ONE
        else:
            currency = str(data.get("currency_code") or base).strip().upper()
            rate = ONE if currency == base else parse_number(data.get("fx_rate"), "سعر الصرف", maximum=MAX_RATE)
            if rate <= 0:
                raise BusinessError(f"اكتب سعر صرف {currency} بتاريخ الفاتورة (مفيش سعر متسجل).")
        pay = data.get("pay_kind") or "credit"
        if pay not in PAY_KINDS:
            raise BusinessError("نوع الدفع لازم «آجل» أو «نقدي».")
        due = source = treasury = bank = None
        if pay == "credit":
            due = data.get("due_date") or day
            if isinstance(due, dt.datetime):
                due = due.date()
            if not isinstance(due, dt.date):
                raise BusinessError("تاريخ الاستحقاق مطلوب.")
            if due < day:
                raise BusinessError("تاريخ الاستحقاق مايبقاش قبل تاريخ الفاتورة.")
        else:
            source = data.get("source_kind") or "treasury"
            if source not in SOURCE_KINDS:
                raise BusinessError("القبض النقدي في خزنة أو بنك.")
            treasury = self._id(data.get("treasury_id")) if source == "treasury" else None
            bank = self._id(data.get("bank_account_id")) if source == "bank" else None
            if source == "treasury" and treasury is None:
                raise BusinessError("اختار الخزنة اللي هتستلم فيها.")
            if source == "bank" and bank is None:
                raise BusinessError("اختار الحساب البنكي اللي هيستلم.")
        discount = parse_number(data.get("discount_amount"), "قيمة خصم الفاتورة", places=2)
        if mode == "multi" and discount:
            raise BusinessError("الفاتورة «متعددة العملات» مالهاش خصم فاتورة — اكتب الخصم % على السطر.")
        return {"invoice_date": day, "customer_id": customer_id,
                "customer_ref": self._text(data.get("customer_ref"), "رقم أمر العميل", 40),
                "e_invoice_no": self._text(data.get("e_invoice_no"), "الرقم الإلكتروني", 60),
                "stock_kind": kind, "prices_include_vat": bool(data.get("prices_include_vat")) and kind == "invoice",
                "pay_kind": pay, "due_date": due, "source_kind": source, "treasury_id": treasury,
                "bank_account_id": bank, "warehouse_id": self._id(data.get("warehouse_id")),
                "project_id": self._id(data.get("project_id")), "employee_id": self._id(data.get("employee_id")),
                "currency_mode": mode, "currency_code": currency, "fx_rate": rate, "discount_amount": discount,
                "description": self._text(data.get("description"), "الملاحظات", 500)}

    def _lines(self, raw: list[dict[str, Any]], header: dict[str, Any], *, strict: bool = True,
               errors: dict[int, str] | None = None) -> list[dict[str, Any]]:
        rows = [r for r in (raw or []) if self._id(r.get("item_id")) or str(r.get("qty") or "").strip()]
        if len(rows) > MAX_LINES:
            raise BusinessError(f"الفاتورة الواحدة لحد {MAX_LINES:,} صنف.")
        items = self.repository.items([self._id(r.get("item_id")) for r in rows if self._id(r.get("item_id"))])
        units: dict[int, dict[int, Decimal]] = {}
        for u in self.repository.item_units(list(items)):
            units.setdefault(int(u["item_id"]), {})[int(u["unit_id"])] = Decimal(u["factor"])
        whs = self.repository.warehouses([self._id(r.get("warehouse_id")) or header["warehouse_id"] for r in rows])
        taxes = {int(t["id"]): t for t in self.repository.tax_codes()}
        currencies = {c["code"]: c for c in self.repository.currencies()}
        base = self.repository.base_currency()
        statement = header["stock_kind"] == "statement"
        out: list[dict[str, Any]] = []
        for n, r in enumerate(rows, start=1):
            item_id = self._id(r.get("item_id"))
            try:
                if item_id is None or item_id not in items:
                    raise BusinessError(f"سطر {n}: اختار الصنف.")
                item = items[item_id]
                label = f"سطر {n} ({item['code']} {item['name']})"
                warehouse_id = None
                if not item["is_service"]:
                    warehouse_id = self._id(r.get("warehouse_id")) or header["warehouse_id"]
                    if warehouse_id is None or warehouse_id not in whs:
                        raise BusinessError(f"{label}: اختار المخزن.")
                    if whs[warehouse_id]["is_virtual"]:
                        raise BusinessError(f"{label}: المخزن «{whs[warehouse_id]['name_ar']}» وهمي — البيع منه في "
                                            "«الصرف الوهمي» (اختار مخزن حقيقي).")
                unit_id = self._id(r.get("unit_id")) or int(item["unit_id"])
                factor = ONE if unit_id == int(item["unit_id"]) else units.get(item_id, {}).get(unit_id)
                if factor is None:
                    raise BusinessError(f"{label}: الوحدة دي مش من وحدات الصنف.")
                qty = parse_number(r.get("qty"), f"{label} — الكمية", maximum=MAX_QTY)
                if qty <= 0:
                    raise BusinessError(f"{label}: اكتب كمية أكبر من صفر.")
                if (qty * factor).quantize(STEP6, rounding=ROUND_HALF_UP) <= 0:
                    raise BusinessError(f"{label}: الكمية صغيرة أوي.")
                price = parse_number(r.get("unit_price"), f"{label} — السعر", maximum=MAX_PRICE)
                pct = parse_number(r.get("discount_pct"), f"{label} — الخصم %", places=4, maximum=Decimal(100))
                tax_id = None if statement else self._id(r.get("tax_code_id"))
                tax_rate = ZERO
                if tax_id is not None:
                    if tax_id not in taxes:
                        raise BusinessError(f"{label}: كود الضريبة مش لضريبة إضافة على المبيعات.")
                    tax_rate = Decimal(taxes[tax_id]["rate"])
                if header["currency_mode"] == "single":
                    currency, rate = header["currency_code"], header["fx_rate"]
                else:
                    currency = str(r.get("currency_code") or base).strip().upper()
                    if currency not in currencies:
                        raise BusinessError(f"{label}: العملة «{currency}» مش موجودة.")
                    rate = ONE if currency == base else parse_number(r.get("fx_rate"), f"{label} — سعر الصرف",
                                                                      maximum=MAX_RATE)
                    if rate <= 0:
                        raise BusinessError(f"{label}: اكتب سعر صرف {currency}.")
            except BusinessError as exc:
                if strict:
                    raise
                if errors is not None:
                    errors[n] = str(exc).split(": ", 1)[-1]
                continue
            out.append({"item_id": item_id, "warehouse_id": warehouse_id, "unit_id": unit_id, "factor": factor,
                        "qty": qty, "unit_price": price, "discount_pct": pct, "tax_code_id": tax_id,
                        "tax_rate": tax_rate, "currency_code": currency, "fx_rate": rate, "item": item,
                        "is_service": bool(item["is_service"]), "category_id": item["category_id"], "line": n})
        if strict and not out:
            raise BusinessError("ضيف صنف واحد على الأقل للفاتورة.")
        return out

    def _compute(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
        calc = calculate(lines, discount_amount=header["discount_amount"],
                         prices_include_vat=header["prices_include_vat"],
                         statement=header["stock_kind"] == "statement", currency_mode=header["currency_mode"],
                         base_currency=self.repository.base_currency())
        for key in ("gross_amount", "line_discount", "net_amount", "vat_amount", "total_amount", "net_base",
                    "vat_base", "total_base", "discount_base"):
            header[key] = calc[key]
        return calc

    def _clean(self, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        header = self._header(data)
        calc = self._compute(header, self._lines(data.get("lines") or [], header))
        return header, calc

    def totals(self, data: dict[str, Any]) -> dict[str, Any]:
        header = self._header(data, relaxed=True)
        return self._compute(header, self._lines(data.get("lines") or [], header, strict=False))

    # ---- القيد -------------------------------------------------------------------
    @staticmethod
    def _fc(line: dict[str, Any], amount_fc: Decimal, base: str) -> dict[str, Any]:
        if line["currency_code"] == base or not amount_fc:
            return {}
        return {"currency_code": line["currency_code"], "fx_rate": line["fx_rate"], "amount_fc": amount_fc}

    def _revenue_role(self, line: dict[str, Any], cache: dict[Any, bool]) -> str:
        """البضاعة على مبيعات نوعها · الخدمي على مبيعات نوعه لو ليه حساب، وإلا «إيراد الخدمات» (4102)."""
        if not line.get("is_service"):
            return "revenue"
        category = line.get("category_id")
        if category not in cache:
            cache[category] = self.repository.own_sales_account(category)
        return "revenue" if cache[category] else "service_revenue"

    def _posting_lines(self, header: dict[str, Any], lines: list[dict[str, Any]],
                       costs: list[tuple[int | None, Decimal]]) -> list[PostingLine]:
        """``costs`` = [(نوع الصنف، التكلفة بالجنيه)] لكل حركة مخزون (من الدفتر وقت الترحيل أو بالمتوسط للمعاينة)."""
        base = self.repository.base_currency()
        party = ("customer", int(header["customer_id"]))
        cache: dict[Any, bool] = {}
        out: list[PostingLine] = []
        for ln in lines:
            fx = Decimal(ln["fx_rate"])
            disc_base = Decimal(ln.get("discount_base") or 0)
            disc_fc = Decimal(ln["discount_fc"]) if "discount_fc" in ln else (_r2(disc_base / fx) if fx else ZERO)
            out.append(PostingLine("receivable", ln["total_base"], party=party,
                                   **self._fc(ln, Decimal(ln["total_amount"]), base)))
            if disc_base:
                out.append(PostingLine("discount", disc_base, **self._fc(ln, disc_fc, base)))
            out.append(PostingLine(self._revenue_role(ln, cache), Decimal(ln["net_base"]) + disc_base,
                                   category_id=ln.get("category_id"),
                                   **self._fc(ln, Decimal(ln["net_amount"]) + disc_fc, base)))
            if Decimal(ln["vat_base"]):
                out.append(PostingLine("vat", ln["vat_base"], tax_code_id=ln["tax_code_id"],
                                       **self._fc(ln, Decimal(ln["vat_amount"]), base)))
        for category, cost in costs:
            if cost:
                out.append(PostingLine("cogs", cost, category_id=category))
                out.append(PostingLine("inventory", cost, category_id=category))
        return out

    def _receipt_lines(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> list[PostingLine]:
        base = self.repository.base_currency()
        party = ("customer", int(header["customer_id"]))
        total = sum((Decimal(ln["total_base"]) for ln in lines), ZERO)
        out = [PostingLine("cash", total, treasury_id=header["treasury_id"]) if header["source_kind"] == "treasury"
               else PostingLine("bank", total, bank_account_id=header["bank_account_id"])]
        out += [PostingLine("receivable", ln["total_base"], party=party,
                            **self._fc(ln, Decimal(ln["total_amount"]), base)) for ln in lines]
        return out

    def _named(self, built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.repository.accounts([ln["account_id"] for ln in built])
        return [{"account_code": names.get(ln["account_id"], {}).get("code", ""),
                 "account_name": names.get(ln["account_id"], {}).get("name_ar", ""), "role_name": ln["role_name"],
                 "debit": ln["debit"], "credit": ln["credit"], "currency_code": ln.get("currency_code"),
                 "amount_fc": ln.get("amount_fc"), "fx_rate": ln.get("fx_rate")} for ln in built]

    def _cost_center(self, header: dict[str, Any]) -> int | None:
        """الإيراد والتكلفة ببُعد أمر التوريد (مركز تكلفته)."""
        if not header.get("project_id"):
            return None
        project = self.repository.project(int(header["project_id"]))
        return int(project["cost_center_id"]) if project and project["cost_center_id"] else None

    # ---- المراجعة قبل الترحيل -----------------------------------------------------
    def review(self, data: dict[str, Any], invoice_id: int | None = None) -> dict[str, Any]:
        """المراجعة (من غير كتابة): ``checks`` = [(ok / no / warn / info، النص)] · ``lines`` = غلط / تنبيه كل سطر ·
        ``stock`` = المتاح قبل / بعد لكل (صنف + مخزن) · ``entry`` / ``receipt`` = القيدين · ``customer`` = رصيده
        وحده · ``profit`` = التكلفة والربح لكل سطر (بالصلاحية بس) · ``calc`` · ``ready`` = مفيش ✖."""
        checks: list[tuple[str, str]] = []
        out: dict[str, Any] = {"checks": checks, "lines": {}, "stock": [], "entry": None, "receipt": None,
                               "customer": None, "profit": None, "calc": None, "ready": False}
        try:
            header = self._header(data)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            try:
                out["calc"] = self.totals(data)
            except BusinessError:
                pass
            return out
        errors: dict[int, str] = {}
        try:
            lines = self._lines(data.get("lines") or [], header, strict=False, errors=errors)
            calc = self._compute(header, lines)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            return out
        out["calc"] = calc
        for n, text in errors.items():
            out["lines"][n] = ("no", text)
        rec = self.repository.get_invoice(int(invoice_id)) if invoice_id else None
        posted = bool(rec and rec["status"] == "posted")
        customer = self.repository.customer(header["customer_id"])
        if customer is None:
            checks.append(("no", "العميل مش موجود (ممكن يكون اتمسح)."))
            return out
        checks.append(("ok", f"العميل: {customer['code']} «{customer['name_ar']}»") if customer["is_active"] else
                      ("no", f"العميل «{customer['code']} {customer['name_ar']}» موقوف — اختار عميل نشط."))
        if customer["is_system"] and header["pay_kind"] != "cash":
            checks.append(("no", f"«{customer['name_ar']}» للبيع الكاش — فاتورته نقدي بس."))
        try:
            self._lines(data.get("lines") or [], header)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
        base = self.repository.base_currency()
        kind = header["stock_kind"]

        # التكلفة (المرحّلة = اللي اتجمّد على الحركات · غير كده = المتوسط دلوقتي) + المتاح.
        moved = self.repository.movements(int(invoice_id)) if posted else []
        avgs: dict[int, Decimal] = {}
        costs: list[tuple[int | None, Decimal]] = []
        line_cost: dict[int, Decimal] = {}
        stock_lines = [ln for ln in calc["lines"] if not ln["is_service"]]
        for n, ln in enumerate(stock_lines, start=1):
            if posted:
                mv = next((m for m in moved if int(m["line_no"]) == n), None)
                cost = -Decimal(mv["booked_value"]) if mv else ZERO
            else:
                if ln["item_id"] not in avgs:
                    avgs[ln["item_id"]] = self.ledger.average_cost(ln["item_id"], kind)
                cost = _r2(Decimal(ln["base_qty"]) * avgs[ln["item_id"]])
            line_cost[ln["line"]] = cost
            costs.append((ln.get("category_id"), cost))
        if not posted:
            need: dict[tuple[int, int], dict[str, Any]] = {}
            for ln in stock_lines:
                key = (ln["item_id"], ln["warehouse_id"])
                m = need.setdefault(key, {"item_id": ln["item_id"], "warehouse_id": ln["warehouse_id"],
                                          "item_code": ln["item"]["code"], "item_name": ln["item"]["name"],
                                          "unit_name": ln["item"]["unit_name"], "qty": ZERO, "lines": []})
                m["qty"] += Decimal(ln["base_qty"])
                m["lines"].append(ln["line"])
            negative = self.can_negative()
            for m in need.values():
                have = self.ledger.available(m["item_id"], m["warehouse_id"], kind)
                out["stock"].append({**m, "qty_before": have, "qty_after": have - m["qty"]})
                if m["qty"] > have:
                    short = f"المتاح {fmt_qty(have)} {m['unit_name']} بس (مطلوب {fmt_qty(m['qty'])})"
                    mark = ("warn", short + " — هيتباع من غير رصيد بالصلاحية") if negative else (
                        "no", short + " — البيع أكتر من المتاح محتاج صلاحية «من غير رصيد»")
                    for n in m["lines"]:
                        out["lines"].setdefault(n, mark)
        show_cost = self.can_show_cost()
        profit_rows: list[dict[str, Any]] = []
        for ln in calc["lines"]:
            cost = line_cost.get(ln["line"], ZERO)
            profit_rows.append({"line": ln["line"], "cost": cost, "revenue": ln["net_base"],
                                "profit": ln["net_base"] - cost})
            if Decimal(ln["unit_price"]) == 0:
                out["lines"].setdefault(ln["line"], ("warn", "السعر صفر"))
            elif not ln["is_service"] and cost and ln["net_base"] < cost:
                out["lines"].setdefault(ln["line"], ("warn", "بيع بخسارة — السعر أقل من التكلفة" +
                                                     (f" ({fmt(cost)} ج)" if show_cost else "")))
        for n in sorted(out["lines"]):
            ln = next((x for x in calc["lines"] if x["line"] == n), None)
            code = ln["item"]["code"] if ln else ""
            checks.append((out["lines"][n][0], f"سطر {n}" + (f" ({code})" if code else "") +
                           f": {out['lines'][n][1]}"))
        if show_cost:
            total_cost = sum((r["cost"] for r in profit_rows), ZERO)
            out["profit"] = {"lines": {r["line"]: r for r in profit_rows}, "cost": total_cost,
                             "revenue": calc["net_base"], "profit": calc["net_base"] - total_cost,
                             "margin": (_r2((calc["net_base"] - total_cost) / calc["net_base"] * 100)
                                        if calc["net_base"] else ZERO)}

        if header["currency_mode"] == "multi":
            parts = " · ".join(f"{c} {fmt(v['total'])}" for c, v in calc["by_currency"].items())
            checks.append(("info", f"متعدد العملات: {parts} — الإجمالي {fmt(calc['total_base'])} {base}"))
        elif header["currency_code"] != base:
            checks.append(("info", f"العملة {header['currency_code']} بسعر {fmt_rate(header['fx_rate'])} — "
                                   f"القيد بالجنيه ({fmt(calc['total_base'])})"))
        if kind == "statement":
            checks.append(("info", "«بيان»: من غير ض.ق.م، والبضاعة بتطلع من رصيد «بيان»"))
        elif not header["e_invoice_no"]:
            checks.append(("info", "الرقم الإلكتروني فاضي — يتكتب بعدين حتى بعد الترحيل"))
        if header["pay_kind"] == "credit":
            checks.append(("info", f"آجل — الاستحقاق {header['due_date']:%d/%m/%Y}"))
        else:
            checks.append(("info", "نقدي — قيد قبض لوحده في " +
                           ("الخزنة" if header["source_kind"] == "treasury" else "البنك") + " بيترحّل مع الفاتورة"))
        if calc["discount_base"]:
            checks.append(("info", f"الخصم {fmt(calc['discount_base'])} ج على «خصم مسموح به» والمبيعات بالإجمالي"))
        if header["project_id"]:
            project = self.repository.project(header["project_id"])
            if project and project["customer_id"] and int(project["customer_id"]) != header["customer_id"]:
                checks.append(("no", f"أمر التوريد {project['code']} لعميل تاني."))
            elif project and Decimal(project["amount"] or 0) > 0 and project["currency_code"] == base:
                done = self.repository.project_invoiced(header["project_id"], exclude_id=invoice_id)
                if done + calc["net_base"] > Decimal(project["amount"]):
                    checks.append(("warn", f"الفواتير على أمر التوريد {project['code']} ({fmt(done + calc['net_base'])}) "
                                           f"هتعدّي قيمته ({fmt(project['amount'])})"))
        year = self.repository.year_of(header["invoice_date"])
        if year is None:
            checks.append(("no", f"مفيش سنة مالية فيها تاريخ {header['invoice_date']:%d/%m/%Y}."))
        elif year["status"] == "closed":
            checks.append(("no", f"السنة المالية {year['year_code']} مقفولة."))

        # حد الائتمان
        bal = self.repository.customer_balance(header["customer_id"])
        effect = ZERO if posted or header["pay_kind"] == "cash" else calc["total_base"]
        limit = Decimal(customer["credit_limit"] or 0)
        out["customer"] = {"before": bal, "after": bal + effect, "limit": limit}
        if effect and limit > 0 and bal + effect > limit:
            text = (f"رصيد العميل بعد الفاتورة {fmt(bal + effect)} أكبر من حد الائتمان {fmt(limit)}")
            checks.append(("warn", text + " — هيعدّي بصلاحية «تجاوز حد الائتمان»") if self.can_over_credit() else
                          ("no", text + " — محتاج صلاحية «تجاوز حد الائتمان»"))

        # القيد (+ القبض النقدي)
        if calc["lines"] and (calc["total_base"] or any(c for _k, c in costs)):
            try:
                center = self._cost_center(header)
                out["entry"] = self._named(self.posting.build(
                    DOC_TYPE, self._posting_lines(header, calc["lines"], costs), cost_center_id=center,
                    project_id=header["project_id"]))
                if not show_cost:                                  # التكلفة مابتبانش من غير الصلاحية
                    out["entry"] = [e for e in out["entry"] if e["role_name"] not in ("تكلفة المبيعات", "المخزون")]
                if header["pay_kind"] == "cash" and calc["total_base"]:
                    out["receipt"] = self._named(self.posting.build(RECEIPT_DOC, self._receipt_lines(header, calc["lines"]),
                                                                    project_id=header["project_id"]))
            except BusinessError as exc:
                checks.append(("no", f"القيد: {exc}"))
        if not posted:
            checks.append(("info", "وقت الترحيل الأصناف والعميل بيتقفلوا — جهاز تاني بيبيع نفس الصنف بيستنى ثواني"))
        out["ready"] = bool(calc["lines"]) and not any(c[0] == "no" for c in checks)
        return out

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, invoice_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_invoice(int(invoice_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=invoice_id)
        if before is None:
            raise BusinessError("الفاتورة دي مش موجودة (ممكن تكون اتمسحت).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — فواتيرها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"الفاتورة {invoice_label(before)} مرحّلة — الغي ترحيلها الأول عشان تعدّلها.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"الفاتورة {invoice_label(before)} مش مرحّلة.")
        before["lines"] = self.repository.lines(int(invoice_id), ex=tx)
        return before

    def _snapshot(self, tx: Executor, invoice_id: int) -> dict[str, Any]:
        row = self.repository.get_invoice(int(invoice_id), ex=tx)
        row["lines"] = self.repository.lines(int(invoice_id), ex=tx)
        return row

    def _record(self, tx: Executor, action: str, invoice_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=invoice_id, entity_label=ENTITY_LABEL,
                          record_label=f"{invoice_label(row)} — {row.get('customer_name') or ''} — "
                                       f"{fmt(row.get('total_amount'))} {row.get('currency_code') or ''}",
                          before=before, after=after)

    def _check_credit(self, tx: Executor, v: dict[str, Any]) -> None:
        """حد الائتمان جوه قفل صف العميل (جهازين بيرحّلوا لنفس العميل بيتصفّوا ورا بعض)."""
        customer = self.repository.customer(int(v["customer_id"]), ex=tx, for_update=True)
        if customer is None or v["pay_kind"] != "credit":
            return
        limit = Decimal(customer["credit_limit"] or 0)
        if limit <= 0:
            return
        bal = self.repository.customer_balance(int(v["customer_id"]), ex=tx)
        after = bal + Decimal(v["total_base"])
        if after > limit and not self.can_over_credit():
            raise BusinessError(f"حد ائتمان العميل «{customer['name_ar']}» {fmt(limit)} — رصيده {fmt(bal)} + الفاتورة "
                                f"{fmt(v['total_base'])} = {fmt(after)} — الترحيل محتاج صلاحية «تجاوز حد الائتمان».")

    def _post(self, tx: Executor, invoice_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """حد الائتمان ← الحالة ← مرحّلة (القاعدة بتصرف الرقم) ← حركات دفتر المخزون بالمتوسط ← القيد الواحد بالتكلفة
        (+ قيد القبض) — في نفس الـ transaction."""
        self._check_credit(tx, before)
        updated = self.repository.set_status(invoice_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=invoice_id)
        v = self._snapshot(tx, invoice_id)
        stock_lines = [ln for ln in v["lines"] if not ln["is_service"]]
        costs: list[tuple[int | None, Decimal]] = []
        if stock_lines:
            moved = self.ledger.post(tx, doc_type=DOC_TYPE, source_id=invoice_id, source_no=v["invoice_no"],
                                     movement_date=v["invoice_date"],
                                     lines=[StockLine(ln["item_id"], ln["warehouse_id"], -Decimal(ln["base_qty"]),
                                                      v["stock_kind"]) for ln in stock_lines],
                                     allow_negative=self.can_negative())
            by_no = {int(m["line_no"]): -Decimal(m["booked_value"]) for m in moved}
            costs = [(ln["category_id"], by_no.get(n, ZERO)) for n, ln in enumerate(stock_lines, start=1)]
        if Decimal(v["total_base"]) or any(c for _k, c in costs):
            note = v["description"] or "فاتورة بيع"
            ref = v["customer_ref"] or v["e_invoice_no"]
            self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=invoice_id, source_no=v["invoice_no"],
                                       entry_date=v["invoice_date"], description=f"{note} — {v['customer_name']}",
                                       reference=ref, lines=self._posting_lines(v, v["lines"], costs),
                                       cost_center_id=v["project_cost_center_id"], project_id=v["project_id"])
            if v["pay_kind"] == "cash" and Decimal(v["total_base"]):
                self.posting.post_document(tx, doc_type=RECEIPT_DOC, source_id=invoice_id, source_no=v["invoice_no"],
                                           entry_date=v["invoice_date"],
                                           description=f"قبض نقدي لفاتورة البيع {v['invoice_no']} — {v['customer_name']}",
                                           reference=ref, lines=self._receipt_lines(v, v["lines"]),
                                           project_id=v["project_id"])
        self._record(tx, "post", invoice_id, before, v)
        return v

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, invoice_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        PermissionService.require(f"{PERM}.create" if invoice_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        header, calc = self._clean(data)
        lines = [{k: ln[k] for k in ("item_id", "warehouse_id", "unit_id", "factor", "qty", "unit_price",
                                     "discount_pct", "tax_code_id", "tax_rate", "currency_code", "fx_rate",
                                     "gross_amount", "line_discount", "header_discount", "net_amount",
                                     "vat_amount", "total_amount", "net_base", "vat_base", "total_base",
                                     "discount_base")}
                 for ln in calc["lines"]]
        try:
            with self.db.atomic() as tx:
                before = None
                if invoice_id is None:
                    invoice_id = self.repository.insert_invoice({**header, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock(tx, int(invoice_id), expected_version, draft_only=True)
                    updated = self.repository.update_invoice(int(invoice_id), header, ex=tx,
                                                             expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=invoice_id)
                self.repository.replace_lines(int(invoice_id), lines, ex=tx)
                after = self._snapshot(tx, int(invoice_id))
                self._record(tx, "create" if before is None else "update", int(invoice_id), before, after)
                if post:
                    self._post(tx, int(invoice_id), after)
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError("الفاتورة دي اتعدّلت من جهاز تاني في نفس اللحظة — دوس «تحديث» وجرّب تاني.") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(invoice_id)

    def post(self, invoice_id: int, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(invoice_id), expected_version, draft_only=True)
                after = self._post(tx, int(invoice_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["invoice_no"]

    def unpost(self, invoice_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل: الحركات والقيدين بيتمسحوا (البضاعة بترجع المخزن) والفاتورة مسودة برقمها."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(invoice_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=RECEIPT_DOC, source_id=int(invoice_id))
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=int(invoice_id))
                self.ledger.remove(tx, doc_type=DOC_TYPE, source_id=int(invoice_id), allow_negative=self.can_negative())
                updated = self.repository.set_status(int(invoice_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=invoice_id)
                self._record(tx, "unpost", int(invoice_id), before, self._snapshot(tx, int(invoice_id)))
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def set_e_invoice_no(self, invoice_id: int, value: Any, expected_version: int | None = None) -> None:
        """الرقم الإلكتروني (من المنظومة) — يتكتب أو يتعدّل حتى بعد الترحيل."""
        PermissionService.require(f"{PERM}.edit")
        text = self._text(value, "الرقم الإلكتروني", 60)
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(invoice_id), expected_version)
                updated = self.repository.set_e_invoice_no(int(invoice_id), text, ex=tx,
                                                           expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=invoice_id)
                self._record(tx, "update", int(invoice_id), before, self._snapshot(tx, int(invoice_id)))
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_invoice(self, invoice_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(invoice_id), expected_version, draft_only=True)
                if before["invoice_no"]:
                    raise BusinessError(f"الفاتورة {before['invoice_no']} خدت رقم رسمي قبل كده — ماتتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).")
                deleted = self.repository.delete_invoice(int(invoice_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=invoice_id)
                self._record(tx, "delete", int(invoice_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def entry_for(self, invoice_id: int) -> dict[str, Any] | None:
        return self.posting.entry_for(DOC_TYPE, int(invoice_id))

    def receipt_entry_for(self, invoice_id: int) -> dict[str, Any] | None:
        return self.posting.entry_for(RECEIPT_DOC, int(invoice_id))

    def movements(self, invoice_id: int) -> list[dict]:
        return self.repository.movements(int(invoice_id))


__all__ = ["SalesInvoiceService", "calculate", "invoice_label", "fmt", "fmt_rate", "fmt_qty", "PAGE_SIZE",
           "PAY_KINDS", "CURRENCY_MODES", "SOURCE_KINDS", "PERM"]
