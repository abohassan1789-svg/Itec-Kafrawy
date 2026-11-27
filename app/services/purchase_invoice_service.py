"""«فاتورة الشراء» — المرحلة 7، النموذج 9 «بيانات الفاتورة بتبويبات» (اختيار المستخدم 2026-11-27 — سكريبت 0048).

* الفاتورة = مورد + أصناف (صنف · مخزن · وحدة من وحداته · كمية · سعر · خصم % · كود ضريبة) — والمخزن الافتراضي من
  الرأس · **المخزن الوهمي ممنوع** · الصنف الخدمي من غير مخزن (مصروف على «تكلفة» نوع الصنف).
* **«حفظ»** = مسودة · **«ترحيل»** (أو حفظ + ترحيل في نفس الـ transaction) ← القاعدة بتصرف ``PUR-2026-00001`` أول مرة
  بس، و**دفتر المخزون** بيدخّل كل صنف مخزني بتكلفته (صافي السطر بالجنيه ÷ الكمية بالوحدة الأساسية — والمتوسط
  بيتحدّث)، و**محرك الترحيل** بيعمل القيد: مدين المخزون / تكلفة الخدمات / ض.ق.م مدخلات — دائن المورد.
  «نقدي» = **قيد صرف لوحده** (مدين المورد / دائن الخزنة أو البنك) بيترحّل ويتلغي مع الفاتورة.
* **الحسبة** (``calculate``): الإجمالي = الكمية × السعر ← − خصم السطر % ← − نصيبه من خصم الفاتورة (بيتوزّع على
  الأصناف بالقيمة — آخر سطر بياخد الباقي) ← الضريبة (شامل: الضريبة جوه المبلغ · غير شامل: فوقه · البيان: صفر) ←
  بالجنيه = كل مبلغ × سعر الصرف (مقرّب لقرشين) — فالقيد متوازن بالظبط من غير فروق تقريب.
* **العملات (طلب المستخدم):** «عملة واحدة» (الافتراضي — عملة المورد بسعرها بتاريخ الفاتورة ويتعدّل) أو
  «متعدد العملات» (كل سطر بعملته وسعره، والإجماليات بالجنيه + تفصيل لكل عملة — من غير خصم فاتورة). دين المورد
  بيتسجل **سطر لكل (عملة + سعر)** بالجنيه ومعاه العملة والسعر والمبلغ بالعملة (0048).
* ``review`` = المراجعة قبل الترحيل: ✔ / ✖ / ⚠ / ℹ (رقم فاتورة المورد المتكرر · الوهمي · السعر صفر · **السعر أبعد
  من آخر سعر شراء بأكتر من 20%** · …) + الرصيد والمتوسط قبل / بعد + القيد + رصيد المورد قبل / بعد.
* **«إلغاء الترحيل»** بالصلاحية في السنة المفتوحة: الحركات والقيدين بيتمسحوا والفاتورة ترجع مسودة **برقمها** — ولو
  البضاعة اتصرفت بعدها ورصيدها هيبقى بالسالب محتاج «من غير رصيد».
* كل حاجة في ``sec.audit_log`` قبل/بعد (بالسطور) + ``row_version`` (جهازين على نفس الفاتورة).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.purchase_invoice_repository import STATUSES, PurchaseInvoiceRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService
from app.services.stock_ledger_service import KINDS, StockLedgerService, StockLine, fmt_qty

PAGE_SIZE = 50
ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
STEP6 = Decimal("0.000001")
MAX_QTY = Decimal("999999999999")
MAX_PRICE = Decimal("999999999999")
MAX_RATE = Decimal("999999")
MAX_LINES = 2000
PRICE_WARN = Decimal("0.20")             # تنبيه السعر: أبعد من آخر سعر شراء بأكتر من 20% (اقتراح سؤال 15)
DOC_TYPE = "purchase_invoice"            # القيد (acc.posting_doc_types) + دفتر المخزون (inv.stock_doc_types)
PAY_DOC = "purchase_payment"             # قيد الصرف النقدي
ENTITY, ENTITY_LABEL = "purch.purchase_invoices", "فاتورة شراء"
PERM = "purch.purchase_invoices"
PAY_KINDS = {"credit": "آجل", "cash": "نقدي"}
SOURCE_KINDS = {"treasury": "خزنة", "bank": "بنك"}
CURRENCY_MODES = {"single": "عملة واحدة", "multi": "متعدد العملات"}


def invoice_label(row: dict[str, Any]) -> str:
    """«PUR-2026-00012» أو «مسودة 7»."""
    return row.get("invoice_no") or f"مسودة {row.get('draft_no')}"


def fmt(amount: Any) -> str:
    return f"{Decimal(amount or 0):,.2f}"


def fmt_rate(value: Any) -> str:
    text = f"{Decimal(value or 0):f}"
    return (text.rstrip("0").rstrip(".") if "." in text else text) or "0"


def _r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def parse_number(value: Any, label: str, *, places: int = 6, maximum: Decimal = MAX_PRICE) -> Decimal:
    """رقم من الشاشة (الفاضي = صفر) — من غير سالب ومن غير كسور أكتر من ``places``."""
    if isinstance(value, Decimal):
        number = value
    else:
        text = str(value if value is not None else "").strip().replace(",", "").replace("٬", "").replace("٫", ".")
        if not text:
            return ZERO
        try:
            number = Decimal(text)
        except InvalidOperation:
            raise BusinessError(f"«{label}»: «{value}» مش رقم.") from None
    if not number.is_finite():
        raise BusinessError(f"«{label}»: «{value}» مش رقم.")
    if number < 0:
        raise BusinessError(f"«{label}»: مينفعش يبقى بالسالب.")
    if number != number.quantize(Decimal(1).scaleb(-places)):
        raise BusinessError(f"«{label}»: فيه كسور أكتر من {places} أرقام.")
    if number > maximum:
        raise BusinessError(f"«{label}»: أكبر من المسموح.")
    return number


def calculate(lines: list[dict[str, Any]], *, discount_amount: Any = ZERO, prices_include_vat: bool = False,
              statement: bool = False, currency_mode: str = "single", base_currency: str = "EGP") -> dict[str, Any]:
    """الحسبة كلها (من غير قاعدة — الشاشة بتستعملها للإجماليات وهي بتكتب، والـ Service وقت الحفظ).

    كل سطر محتاج: ``qty`` · ``factor`` · ``unit_price`` · ``discount_pct`` · ``tax_rate`` · ``currency_code`` ·
    ``fx_rate``. بيرجّع السطور بمبالغها (بعملة السطر وبالجنيه + تكلفة الوحدة الأساسية) والإجماليات وتفصيل كل عملة.
    """
    rows: list[dict[str, Any]] = []
    for line in lines:
        qty, price = Decimal(line["qty"]), Decimal(line["unit_price"])
        pct = Decimal(line.get("discount_pct") or 0)
        gross = _r2(qty * price)
        ld = _r2(gross * pct / 100)
        rows.append({**line, "gross_amount": gross, "line_discount": ld, "net0": gross - ld})
    # خصم الفاتورة بيتوزّع على الأصناف بقيمتها بعد خصم السطر (آخر سطر فيه قيمة بياخد الباقي) — «عملة واحدة» بس.
    total0 = sum((r["net0"] for r in rows), ZERO)
    hd = _r2(Decimal(discount_amount or 0)) if currency_mode == "single" else ZERO
    if hd > total0:
        raise BusinessError(f"خصم الفاتورة ({fmt(hd)}) أكبر من إجمالي الأصناف ({fmt(total0)}).")
    left = hd
    last = max((i for i, r in enumerate(rows) if r["net0"] > 0), default=-1)
    for i, r in enumerate(rows):
        share = left if i == last else (_r2(hd * r["net0"] / total0) if total0 else ZERO)
        share = min(share, r["net0"], left)
        r["header_discount"] = share
        left -= share
    for r in rows:
        na = r.pop("net0") - r["header_discount"]
        rate = ZERO if statement else Decimal(r.get("tax_rate") or 0)
        if prices_include_vat and rate:
            net = _r2(na / (1 + rate / 100))
            vat = na - net
        else:
            net, vat = na, _r2(na * rate / 100)
        fx = ONE if r["currency_code"] == base_currency else Decimal(r["fx_rate"])
        r.update({"net_amount": net, "vat_amount": vat, "total_amount": net + vat, "fx_rate": fx,
                  "net_base": _r2(net * fx), "vat_base": _r2(vat * fx)})
        r["total_base"] = r["net_base"] + r["vat_base"]
        base_qty = (Decimal(r["qty"]) * Decimal(r.get("factor") or 1)).quantize(STEP6, rounding=ROUND_HALF_UP)
        r["base_qty"] = base_qty
        r["unit_cost"] = (r["net_base"] / base_qty).quantize(STEP6, rounding=ROUND_HALF_UP) if base_qty else ZERO

    def s(key: str) -> Decimal:
        return sum((r[key] for r in rows), ZERO)

    by_currency: dict[str, dict[str, Decimal]] = {}
    for r in rows:
        c = by_currency.setdefault(r["currency_code"], {"net": ZERO, "vat": ZERO, "total": ZERO, "total_base": ZERO})
        c["net"] += r["net_amount"]
        c["vat"] += r["vat_amount"]
        c["total"] += r["total_amount"]
        c["total_base"] += r["total_base"]
    if currency_mode == "single":
        rate = rows[0]["fx_rate"] if rows else ONE
        totals = {"gross_amount": s("gross_amount"), "line_discount": s("line_discount"), "discount_amount": hd,
                  "net_amount": s("net_amount"), "vat_amount": s("vat_amount"), "total_amount": s("total_amount")}
    else:                                       # متعدد: إجماليات الفاتورة بالجنيه
        rate = ONE
        totals = {"gross_amount": sum((_r2(r["gross_amount"] * r["fx_rate"]) for r in rows), ZERO),
                  "line_discount": sum((_r2(r["line_discount"] * r["fx_rate"]) for r in rows), ZERO),
                  "discount_amount": ZERO, "net_amount": s("net_base"), "vat_amount": s("vat_base"),
                  "total_amount": s("total_base")}
    pre = totals["gross_amount"] - totals["line_discount"]       # «إجمالي الفاتورة قبل الضريبة» (قبل خصم الفاتورة)
    return {"lines": rows, **totals, "net_base": s("net_base"), "vat_base": s("vat_base"),
            "total_base": s("total_base"), "items_total": pre, "rate": rate,
            "discount_pct": (_r2(hd / pre * 100) if pre else ZERO),
            "by_currency": by_currency,
            "inventory_base": sum((r["net_base"] for r in rows if not r.get("is_service")), ZERO),
            "service_base": sum((r["net_base"] for r in rows if r.get("is_service")), ZERO)}


class PurchaseInvoiceService:
    def __init__(self, db: Database | None = None, repository: PurchaseInvoiceRepository | None = None,
                 ledger: StockLedgerService | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or PurchaseInvoiceRepository(self.db)
        self.ledger = ledger or StockLedgerService(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_invoices(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                      keyword: str = "", supplier_id: int | None = None, page: int = 1,
                      page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_invoices(date_from, date_to, status, keyword.strip(), supplier_id,
                                             limit=page_size, offset=offset)

    def count_invoices(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                       keyword: str = "", supplier_id: int | None = None) -> dict[str, Any]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_invoices(date_from, date_to, status, keyword.strip(), supplier_id)
        return {**{k: int(row[k]) for k in ("total", "drafts", "posted")}, "total_base": Decimal(row["total_base"])}

    def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        row = self.repository.get_invoice(int(invoice_id))
        if row is not None:
            row["label"] = invoice_label(row)
            row["lines"] = self.repository.lines(int(invoice_id))
        return row

    def choices(self) -> dict[str, Any]:
        """كل اللي الشاشة محتاجاه: الموردين · المخازن · الأصناف بوحداتها (``units[item_id]`` = [(unit_id، الاسم،
        المعامل)] — الأساسية الأول) · أكواد الضريبة · العملات · الخزائن والبنوك · أوامر التوريد."""
        items = self.repository.item_choices()
        units: dict[int, list[tuple[int, str, Decimal]]] = {
            int(i["id"]): [(int(i["unit_id"]), i["unit_name"], ONE)] for i in items}
        for u in self.repository.item_units():
            units.setdefault(int(u["item_id"]), []).append((int(u["unit_id"]), u["unit_name"], Decimal(u["factor"])))
        return {"suppliers": self.repository.supplier_choices(), "warehouses": self.repository.warehouse_choices(),
                "items": items, "units": units, "tax_codes": self.repository.tax_codes(),
                "currencies": self.repository.currencies(), "treasuries": self.repository.treasury_choices(),
                "banks": self.repository.bank_account_choices(), "projects": self.repository.project_choices(),
                "base_currency": self.repository.base_currency()}

    def rate_on(self, currency_code: str, day: dt.date) -> Decimal | None:
        """سعر الصرف بتاريخ الفاتورة (آخر سعر في اليوم ده أو قبله — الأساسية = 1)."""
        return self.repository.rate_on(str(currency_code), day)

    def supplier_defaults(self, supplier_id: int, invoice_date: dt.date) -> dict[str, Any]:
        """لما المورد يتختار: العملة وسعرها · الاستحقاق (التاريخ + مدة السداد) · كود الضريبة لو 0% (مستورد / معفي)."""
        s = self.repository.supplier(int(supplier_id))
        if s is None:
            raise BusinessError("المورد مش موجود (ممكن يكون اتمسح).")
        rate = self.rate_on(s["currency_code"], invoice_date)
        tax = None
        if s["vat_tax_code_id"]:
            codes = {int(t["id"]): t for t in self.repository.tax_codes()}
            code = codes.get(int(s["vat_tax_code_id"]))
            if code is not None and Decimal(code["rate"]) == 0:
                tax = int(code["id"])
        return {"currency_code": s["currency_code"], "fx_rate": rate,
                "due_date": invoice_date + dt.timedelta(days=int(s["payment_terms_days"] or 0)),
                "terms": int(s["payment_terms_days"] or 0), "tax_code_id": tax, "tax_reg_no": s["tax_reg_no"]}

    def item_info(self, item_id: int, warehouse_id: int | None, stock_kind: str = "invoice",
                  exclude_id: int | None = None) -> dict[str, Any]:
        """لخانة «إضافة صنف»: المتاح في المخزن · آخر تكلفة شراء والمتوسط (للوحدة الأساسية بالجنيه)."""
        kind = stock_kind if stock_kind in KINDS else "invoice"
        last = self.repository.last_costs([int(item_id)], exclude_id=exclude_id).get(int(item_id))
        return {"available": self.ledger.available(int(item_id), warehouse_id, kind) if warehouse_id else None,
                "last_cost": last, "avg": self.ledger.average_cost(int(item_id), kind)}

    def supplier_balance(self, supplier_id: int) -> dict[str, Any]:
        sid = int(supplier_id)
        return {"base": self.repository.supplier_balance(sid),
                "by_currency": self.repository.supplier_balance_by_currency(sid)}

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
        """``relaxed`` (الإجماليات وانت بتكتب): المورد / سعر الصرف / الخزنة / الاستحقاق الناقصين مايوقفوش الحسبة."""
        try:
            return self._header_strict(data)
        except BusinessError:
            if not relaxed:
                raise
        fixed = {**data, "supplier_id": self._id(data.get("supplier_id")) or -1, "due_date": None,
                 "pay_kind": "credit"}
        try:
            rate = parse_number(data.get("fx_rate"), "سعر الصرف", maximum=MAX_RATE)
        except BusinessError:
            rate = ZERO
        fixed["fx_rate"] = rate if rate > 0 else Decimal("0.000001")
        header = self._header_strict(fixed)
        if header["supplier_id"] == -1:
            header["supplier_id"] = None
        return header

    def _header_strict(self, data: dict[str, Any]) -> dict[str, Any]:
        day = data.get("invoice_date")
        if isinstance(day, dt.datetime):
            day = day.date()
        if not isinstance(day, dt.date):
            raise BusinessError("تاريخ الفاتورة مطلوب.")
        supplier_id = self._id(data.get("supplier_id"))
        if supplier_id is None:
            raise BusinessError("اختار المورد.")
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
                raise BusinessError("الدفع النقدي من خزنة أو بنك.")
            treasury = self._id(data.get("treasury_id")) if source == "treasury" else None
            bank = self._id(data.get("bank_account_id")) if source == "bank" else None
            if source == "treasury" and treasury is None:
                raise BusinessError("اختار الخزنة اللي هتدفع منها.")
            if source == "bank" and bank is None:
                raise BusinessError("اختار الحساب البنكي اللي هتدفع منه.")
        discount = parse_number(data.get("discount_amount"), "قيمة خصم الفاتورة", places=2)
        if mode == "multi" and discount:
            raise BusinessError("الفاتورة «متعددة العملات» مالهاش خصم فاتورة — اكتب الخصم % على السطر.")
        return {"invoice_date": day, "supplier_id": supplier_id,
                "supplier_invoice_no": self._text(data.get("supplier_invoice_no"), "رقم فاتورة المورد", 40),
                "e_invoice_no": self._text(data.get("e_invoice_no"), "الرقم الإلكتروني", 60),
                "stock_kind": kind, "prices_include_vat": bool(data.get("prices_include_vat")) and kind == "invoice",
                "pay_kind": pay, "due_date": due, "source_kind": source, "treasury_id": treasury,
                "bank_account_id": bank, "warehouse_id": self._id(data.get("warehouse_id")),
                "project_id": self._id(data.get("project_id")), "currency_mode": mode, "currency_code": currency,
                "fx_rate": rate, "discount_amount": discount,
                "description": self._text(data.get("description"), "الملاحظات", 500)}

    def _lines(self, raw: list[dict[str, Any]], header: dict[str, Any], *, strict: bool = True,
               errors: dict[int, str] | None = None) -> list[dict[str, Any]]:
        """السطور (الفاضي بيتشال) — ``strict=False`` (المعاينة) بيتجاهل السطر الناقص بدل الغلط (وغلطه في ``errors``)."""
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
                        raise BusinessError(f"{label}: المخزن «{whs[warehouse_id]['name_ar']}» وهمي — مالوش مشتريات "
                                            "(اشتري على مخزن حقيقي وحوّل بعدين).")
                unit_id = self._id(r.get("unit_id")) or int(item["unit_id"])
                factor = ONE if unit_id == int(item["unit_id"]) else units.get(item_id, {}).get(unit_id)
                if factor is None:
                    raise BusinessError(f"{label}: الوحدة دي مش من وحدات الصنف.")
                qty = parse_number(r.get("qty"), f"{label} — الكمية", maximum=MAX_QTY)
                if qty <= 0:
                    raise BusinessError(f"{label}: اكتب كمية أكبر من صفر.")
                if (qty * factor).quantize(STEP6, rounding=ROUND_HALF_UP) <= 0:
                    raise BusinessError(f"{label}: الكمية صغيرة أوي.")
                price = parse_number(r.get("unit_price"), f"{label} — السعر")
                pct = parse_number(r.get("discount_pct"), f"{label} — الخصم %", places=4, maximum=Decimal(100))
                tax_id = None if statement else self._id(r.get("tax_code_id"))
                tax_rate = ZERO
                if tax_id is not None:
                    if tax_id not in taxes:
                        raise BusinessError(f"{label}: كود الضريبة مش لضريبة إضافة على المشتريات.")
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
                        "is_service": bool(item["is_service"]), "line": n})
        if strict and not out:
            raise BusinessError("ضيف صنف واحد على الأقل للفاتورة.")
        return out

    def _compute(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
        calc = calculate(lines, discount_amount=header["discount_amount"],
                         prices_include_vat=header["prices_include_vat"],
                         statement=header["stock_kind"] == "statement", currency_mode=header["currency_mode"],
                         base_currency=self.repository.base_currency())
        for key in ("gross_amount", "line_discount", "net_amount", "vat_amount", "total_amount", "net_base",
                    "vat_base", "total_base"):
            header[key] = calc[key]
        return calc

    def _clean(self, data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        header = self._header(data)
        calc = self._compute(header, self._lines(data.get("lines") or [], header))
        return header, calc

    def totals(self, data: dict[str, Any]) -> dict[str, Any]:
        """الإجماليات وانت بتكتب (السطور الناقصة بتتجاهل) — للعمود اليمين في الشاشة."""
        header = self._header(data, relaxed=True)
        return self._compute(header, self._lines(data.get("lines") or [], header, strict=False))

    # ---- القيد -------------------------------------------------------------------
    def _fc(self, line: dict[str, Any], amount_fc: Decimal, base: str) -> dict[str, Any]:
        if line["currency_code"] == base:
            return {}
        return {"currency_code": line["currency_code"], "fx_rate": line["fx_rate"], "amount_fc": amount_fc}

    def _posting_lines(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> list[PostingLine]:
        base = self.repository.base_currency()
        party = ("supplier", int(header["supplier_id"]))
        out: list[PostingLine] = []
        for ln in lines:
            category = (ln.get("item") or {}).get("category_id", ln.get("category_id"))
            role = "service" if ln.get("is_service") else "inventory"
            out.append(PostingLine(role, ln["net_base"], category_id=category,
                                   **self._fc(ln, Decimal(ln["net_amount"]), base)))
            if Decimal(ln["vat_base"]):
                out.append(PostingLine("vat", ln["vat_base"], tax_code_id=ln["tax_code_id"],
                                       **self._fc(ln, Decimal(ln["vat_amount"]), base)))
            out.append(PostingLine("payable", ln["total_base"], party=party,
                                   **self._fc(ln, Decimal(ln["total_amount"]), base)))
        return out

    def _payment_lines(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> list[PostingLine]:
        base = self.repository.base_currency()
        party = ("supplier", int(header["supplier_id"]))
        out = [PostingLine("payable", ln["total_base"], party=party, **self._fc(ln, Decimal(ln["total_amount"]), base))
               for ln in lines]
        total = sum((Decimal(ln["total_base"]) for ln in lines), ZERO)
        out.append(PostingLine("cash", total, treasury_id=header["treasury_id"]) if header["source_kind"] == "treasury"
                   else PostingLine("bank", total, bank_account_id=header["bank_account_id"]))
        return out

    def _named(self, built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.repository.accounts([ln["account_id"] for ln in built])
        return [{"account_code": names.get(ln["account_id"], {}).get("code", ""),
                 "account_name": names.get(ln["account_id"], {}).get("name_ar", ""), "role_name": ln["role_name"],
                 "debit": ln["debit"], "credit": ln["credit"], "currency_code": ln.get("currency_code"),
                 "amount_fc": ln.get("amount_fc"), "fx_rate": ln.get("fx_rate")} for ln in built]

    # ---- المراجعة قبل الترحيل -----------------------------------------------------
    def review(self, data: dict[str, Any], invoice_id: int | None = None) -> dict[str, Any]:
        """المراجعة (من غير كتابة): ``checks`` = [(ok / no / warn / info، النص)] · ``lines`` = غلط / تنبيه كل سطر ·
        ``stock`` = الرصيد والمتوسط قبل / بعد · ``entry`` / ``payment`` = القيدين · ``supplier`` = رصيده قبل / بعد ·
        ``calc`` = الحسبة · ``ready`` = مفيش ✖."""
        checks: list[tuple[str, str]] = []
        out: dict[str, Any] = {"checks": checks, "lines": {}, "stock": [], "entry": None, "payment": None,
                               "supplier": None, "calc": None, "ready": False}
        try:
            header = self._header(data)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            try:                                    # الإجماليات تبان برضه وهو لسه بيكتب
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
        supplier = self.repository.supplier(header["supplier_id"])
        if supplier is None:
            checks.append(("no", "المورد مش موجود (ممكن يكون اتمسح)."))
            return out
        checks.append(("ok", f"المورد: {supplier['code']} «{supplier['name_ar']}»") if supplier["is_active"] else
                      ("no", f"المورد «{supplier['code']} {supplier['name_ar']}» موقوف — اختار مورد نشط."))
        try:
            self._lines(data.get("lines") or [], header)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
        base = self.repository.base_currency()
        last = self.repository.last_costs([ln["item_id"] for ln in calc["lines"]], exclude_id=invoice_id)
        for ln in calc["lines"]:
            item = ln["item"]
            if Decimal(ln["unit_price"]) == 0:
                out["lines"][ln["line"]] = ("warn", "السعر صفر — الصنف هيدخل من غير تكلفة")
            elif not ln["is_service"] and last.get(ln["item_id"]):
                lp = last[ln["item_id"]]
                if lp and abs(ln["unit_cost"] - lp) / lp > PRICE_WARN:
                    out["lines"][ln["line"]] = (
                        "warn", f"السعر بعيد عن آخر سعر شراء ({fmt(lp)} للـ{item['unit_name']}) بأكتر من 20%")
            if ln["line"] in out["lines"]:
                checks.append((out["lines"][ln["line"]][0],
                               f"سطر {ln['line']} ({item['code']}): {out['lines'][ln['line']][1]}"))
        sinv = header["supplier_invoice_no"]
        if sinv:
            taken = self.repository.supplier_invoice_taken(header["supplier_id"], sinv, exclude_id=invoice_id)
            if taken:
                checks.append(("no", f"رقم فاتورة المورد «{sinv}» اتسجل قبل كده لنفس المورد ({invoice_label(taken)})"))
        else:
            checks.append(("warn", "رقم فاتورة المورد فاضي"))
        if header["currency_mode"] == "multi":
            parts = " · ".join(f"{c} {fmt(v['total'])}" for c, v in calc["by_currency"].items())
            checks.append(("info", f"متعدد العملات: {parts} — الإجمالي {fmt(calc['total_base'])} {base}"))
        elif header["currency_code"] != base:
            checks.append(("info", f"العملة {header['currency_code']} بسعر {fmt_rate(header['fx_rate'])} — "
                                   f"التكلفة والقيد بالجنيه ({fmt(calc['total_base'])})"))
        if header["stock_kind"] == "statement":
            checks.append(("info", "«بيان»: من غير ض.ق.م، والبضاعة بتدخل رصيد «بيان»"))
        elif not header["e_invoice_no"]:
            checks.append(("warn", "الرقم الإلكتروني فاضي"))
        if header["pay_kind"] == "credit":
            checks.append(("info", f"آجل — الاستحقاق {header['due_date']:%d/%m/%Y}"))
        else:
            checks.append(("info", "نقدي — قيد صرف لوحده من " +
                           ("الخزنة" if header["source_kind"] == "treasury" else "البنك") + " بيترحّل مع الفاتورة"))
        if calc["discount_amount"]:
            checks.append(("info", f"خصم الفاتورة {fmt(calc['discount_amount'])} اتوزّع على الأصناف بالقيمة وقلّل تكلفتها"))
        if calc["service_base"]:
            checks.append(("info", f"صنف خدمي {fmt(calc['service_base'])} ← مصروف من غير مخزون"))
        year = self.repository.year_of(header["invoice_date"])
        if year is None:
            checks.append(("no", f"مفيش سنة مالية فيها تاريخ {header['invoice_date']:%d/%m/%Y}."))
        elif year["status"] == "closed":
            checks.append(("no", f"السنة المالية {year['year_code']} مقفولة."))

        # الرصيد والمتوسط قبل / بعد (لكل صنف مخزني — على مستوى الشركة للنوع)
        kind = header["stock_kind"]
        merged: dict[int, dict[str, Any]] = {}
        for ln in calc["lines"]:
            if ln["is_service"]:
                continue
            m = merged.setdefault(ln["item_id"], {"item_id": ln["item_id"], "item_code": ln["item"]["code"],
                                                  "item_name": ln["item"]["name"],
                                                  "unit_name": ln["item"]["unit_name"], "qty": ZERO, "value": ZERO})
            m["qty"] += ln["base_qty"]
            m["value"] += ln["net_base"]
        for m in merged.values():
            q_now = self.ledger.available(m["item_id"], None, kind)
            a_now = self.ledger.average_cost(m["item_id"], kind)
            if posted:
                q0 = q_now - m["qty"]
                a1 = a_now
                a0 = max(ZERO, (q_now * a_now - m["value"]) / q0) if q0 > 0 else ZERO
            else:
                q0, a0 = q_now, a_now
                total = q0 + m["qty"]
                a1 = ((q0 * a0 + m["value"]) / total) if total > 0 else ZERO
            if not a0 and not q0 and not posted:
                checks.append(("info", f"{m['item_code']} مالوش متوسط — هيبقى {fmt(a1)}"))
            out["stock"].append({**m, "qty_before": q0, "qty_after": q0 + m["qty"],
                                 "avg_before": a0.quantize(STEP6, rounding=ROUND_HALF_UP),
                                 "avg_after": Decimal(a1).quantize(STEP6, rounding=ROUND_HALF_UP)})

        # القيد (+ الصرف النقدي)
        if calc["lines"] and calc["total_base"]:
            try:
                out["entry"] = self._named(self.posting.build(DOC_TYPE, self._posting_lines(header, calc["lines"]),
                                                              project_id=header["project_id"]))
                if header["pay_kind"] == "cash":
                    out["payment"] = self._named(self.posting.build(PAY_DOC, self._payment_lines(header, calc["lines"]),
                                                                    project_id=header["project_id"]))
            except BusinessError as exc:
                checks.append(("no", f"القيد: {exc}"))
        elif calc["lines"]:
            checks.append(("info", "إجمالي الفاتورة صفر — مفيش قيد (البضاعة بتدخل من غير تكلفة)"))
        bal = self.repository.supplier_balance(header["supplier_id"])
        effect = ZERO if posted or header["pay_kind"] == "cash" else calc["total_base"]
        out["supplier"] = {"before": bal, "after": bal + effect}
        if not posted:
            checks.append(("info", "وقت الترحيل الأصناف بتتقفل — جهاز تاني بيشتغل على نفس الصنف بيستنى ثواني"))
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
                          record_label=f"{invoice_label(row)} — {row.get('supplier_name') or ''} — "
                                       f"{fmt(row.get('total_amount'))} {row.get('currency_code') or ''}",
                          before=before, after=after)

    def _post(self, tx: Executor, invoice_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """الحالة ← مرحّلة (القاعدة بتصرف الرقم) ← حركات دفتر المخزون ← القيد (+ قيد الصرف) — في نفس الـ transaction."""
        updated = self.repository.set_status(invoice_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=invoice_id)
        v = self._snapshot(tx, invoice_id)
        stock = [StockLine(ln["item_id"], ln["warehouse_id"], Decimal(ln["base_qty"]), v["stock_kind"],
                           unit_cost=Decimal(ln["unit_cost"])) for ln in v["lines"] if not ln["is_service"]]
        if stock:
            self.ledger.post(tx, doc_type=DOC_TYPE, source_id=invoice_id, source_no=v["invoice_no"],
                             movement_date=v["invoice_date"], lines=stock,
                             allow_negative=SESSION.can(f"{PERM}.negative"))
        if Decimal(v["total_base"]):
            note = v["description"] or "فاتورة شراء"
            ref = v["supplier_invoice_no"] or v["e_invoice_no"]
            self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=invoice_id, source_no=v["invoice_no"],
                                       entry_date=v["invoice_date"], description=f"{note} — {v['supplier_name']}",
                                       reference=ref, lines=self._posting_lines(v, v["lines"]),
                                       project_id=v["project_id"])
            if v["pay_kind"] == "cash":
                self.posting.post_document(tx, doc_type=PAY_DOC, source_id=invoice_id, source_no=v["invoice_no"],
                                           entry_date=v["invoice_date"],
                                           description=f"صرف نقدي لفاتورة الشراء {v['invoice_no']} — {v['supplier_name']}",
                                           reference=ref, lines=self._payment_lines(v, v["lines"]),
                                           project_id=v["project_id"])
        self._record(tx, "post", invoice_id, before, v)
        return v

    @staticmethod
    def _unique_message(exc: psycopg.Error, header: dict[str, Any]) -> BusinessError:
        if "supplier_inv" in str(getattr(getattr(exc, "diag", None), "constraint_name", "") or exc):
            return BusinessError(f"رقم فاتورة المورد «{header['supplier_invoice_no']}» اتسجل قبل كده لنفس المورد.")
        return BusinessError("الفاتورة دي اتعدّلت من جهاز تاني في نفس اللحظة — دوس «تحديث» وجرّب تاني.")

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, invoice_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة بسطورها (ولو ``post`` يرحّلها في نفس الـ transaction — يا الاتنين يا ولا حاجة)."""
        PermissionService.require(f"{PERM}.create" if invoice_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        header, calc = self._clean(data)
        lines = [{k: ln[k] for k in ("item_id", "warehouse_id", "unit_id", "factor", "qty", "unit_price",
                                     "discount_pct", "tax_code_id", "tax_rate", "currency_code", "fx_rate",
                                     "gross_amount", "line_discount", "header_discount", "net_amount",
                                     "vat_amount", "total_amount", "net_base", "vat_base", "total_base", "unit_cost")}
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
            raise self._unique_message(exc, header) from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(invoice_id)

    def post(self, invoice_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(invoice_id), expected_version, draft_only=True)
                after = self._post(tx, int(invoice_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["invoice_no"]

    def unpost(self, invoice_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): الحركات والقيدين بيتمسحوا والفاتورة بترجع مسودة برقمها."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(invoice_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=PAY_DOC, source_id=int(invoice_id))
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=int(invoice_id))
                self.ledger.remove(tx, doc_type=DOC_TYPE, source_id=int(invoice_id),
                                   allow_negative=SESSION.can(f"{PERM}.negative"))
                updated = self.repository.set_status(int(invoice_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=invoice_id)
                self._record(tx, "unpost", int(invoice_id), before, self._snapshot(tx, int(invoice_id)))
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
        """قيد الفاتورة المرحّلة بسطوره."""
        return self.posting.entry_for(DOC_TYPE, int(invoice_id))

    def payment_entry_for(self, invoice_id: int) -> dict[str, Any] | None:
        """قيد الصرف النقدي (لو نقدية)."""
        return self.posting.entry_for(PAY_DOC, int(invoice_id))

    def movements(self, invoice_id: int) -> list[dict]:
        return self.repository.movements(int(invoice_id))


__all__ = ["PurchaseInvoiceService", "calculate", "invoice_label", "fmt", "fmt_rate", "fmt_qty", "PAGE_SIZE",
           "PAY_KINDS", "CURRENCY_MODES", "SOURCE_KINDS", "PERM"]
