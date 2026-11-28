"""«مرتجع المبيعات» — المرحلة 8 (سكريبت 0053).

قرار المستخدم (2026-11-28): «هي هي نفس التصميم ونفس كل حاجة زي مرتجع المشتريات — بس دي تبع العميل» ← نفس أفكار
``purchase_return_service`` بالظبط، بس للعميل وفواتير البيع:

* **الربط بفاتورة البيع الأصلية اختياري** — كل سطر ممكن يتربط بسطر من فاتورة بيع مرحّلة لنفس العميل
  (``source_line_id``) أو يبقى حر · **أكتر من فاتورة في المرتجع الواحد عادي**.
* **السعر = سعر الفاتورة الأصلية ويتغيّر عادي**: ``source_price`` = صافي سعر الوحدة في الفاتورة (بعد خصم السطر ونصيبه
  من خصم الفاتورة — قبل الضريبة) بعملتها وبسعر صرفها · السطر الحر بآخر سعر للعميل ده (أو سعر البيع من الصنف).
* الكمية من فاتورة **مايتعدّاش اللي اتباع ناقص اللي رجع قبل كده** (والقاعدة بتتأكد تاني وقت الترحيل بقفل الفاتورة).
* **البضاعة بترجع المخزن بتكلفتها الأصلية** = تكلفة الوحدة اللي طلعت بيها في فاتورة البيع (``booked_value`` بتاع
  حركة السطر ÷ كميته) — دفتر المخزون «داخل بتكلفة من المستند» · السطر الحر ← **بالمتوسط الحالي** بتاريخ المرتجع.
  التكلفة بتتحسب **وقت الترحيل جوه قفل الفاتورة والصنف**.
* القيد الواحد: مدين «مردودات المبيعات» (من نوع الصنف — الخدمي من غير حساب مبيعات على نوعه ← «إيراد الخدمات» زي
  فاتورة البيع) · مدين ض.ق.م مخرجات / دائن العميل (سطر لكل عملة + سعر) · مدين المخزون / دائن تكلفة المبيعات بتكلفة
  الرجوع. **المردودات بالصافي** (بعد الخصم) — مفيش سطر «خصم مسموح به» (سؤال مفتوح).
* «آجل» (الافتراضي — رصيد العميل بيقل) · «نقدي» = رجّعنا الفلوس ← **قيد صرف لوحده** من خزنة / بنك · «عميل نقدي» نقدي بس.
* سبب المرتجع اختياري من قايمة **المستخدم بيزوّد عليها** (``save_reason``).
* «إلغاء الترحيل» بالصلاحية — الحركات والقيدين بيتمسحوا والمرتجع يرجع مسودة **برقمه** (لو البضاعة اتباعت تاني ←
  صلاحية «من غير رصيد»).
* كل حاجة في ``sec.audit_log`` قبل/بعد (بالسطور) + ``row_version`` (جهازين على نفس المرتجع).
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.sales_invoice_repository import SalesInvoiceRepository
from app.repositories.sales_return_repository import STATUSES, SalesReturnRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService
from app.services.purchase_invoice_service import MAX_PRICE, MAX_QTY, MAX_RATE, fmt, fmt_rate, parse_number
from app.services.purchase_return_service import calculate as _calculate
from app.services.stock_ledger_service import KINDS, StockLedgerService, StockLine, fmt_qty

PAGE_SIZE = 50
ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
STEP6 = Decimal("0.000001")
MAX_LINES = 2000
DOC_TYPE = "sales_return"                # القيد + دفتر المخزون
REFUND_DOC = "sales_refund"              # قيد الصرف النقدي
ENTITY, ENTITY_LABEL = "sales.sales_returns", "مرتجع مبيعات"
PERM = "sales.sales_returns"
SHOW_COST = "sales.sales_invoices.show_cost"      # «إظهار التكلفة والربح» (نفس صلاحية فاتورة البيع)
PAY_KINDS = {"credit": "آجل", "cash": "نقدي"}
SOURCE_KINDS = {"treasury": "خزنة", "bank": "بنك"}
COST_ROLES = ("المخزون", "تكلفة المبيعات")


def return_label(row: dict[str, Any]) -> str:
    """«SRT-2026-0003» أو «مسودة 4»."""
    return row.get("return_no") or f"مسودة {row.get('draft_no')}"


def _r2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def calculate(lines: list[dict[str, Any]], *, statement: bool = False, base_currency: str = "EGP") -> dict[str, Any]:
    """نفس حسبة مرتجع المشتريات (الكمية × السعر ← الضريبة ← بالجنيه) — من غير تكلفة (التكلفة من الفاتورة الأصلية)."""
    calc = _calculate(lines, statement=statement, base_currency=base_currency)
    for r in calc["lines"]:
        r.pop("unit_cost", None)
    calc["sales_base"] = calc.pop("inventory_base")
    return calc


class SalesReturnService:
    def __init__(self, db: Database | None = None, repository: SalesReturnRepository | None = None,
                 invoices: SalesInvoiceRepository | None = None, ledger: StockLedgerService | None = None,
                 audit: AuditService | None = None, posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SalesReturnRepository(self.db)
        self.invoices = invoices or SalesInvoiceRepository(self.db)
        self.ledger = ledger or StockLedgerService(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    @staticmethod
    def can_show_cost() -> bool:
        return SESSION.can(SHOW_COST)

    @staticmethod
    def can_negative() -> bool:
        return SESSION.can(f"{PERM}.negative")

    # ---- قراية -------------------------------------------------------------
    def list_returns(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                     keyword: str = "", customer_id: int | None = None, page: int = 1,
                     page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_returns(date_from, date_to, status, keyword.strip(), customer_id,
                                            limit=page_size, offset=offset)

    def count_returns(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                      keyword: str = "", customer_id: int | None = None) -> dict[str, Any]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_returns(date_from, date_to, status, keyword.strip(), customer_id)
        return {**{k: int(row[k]) for k in ("total", "drafts", "posted")}, "total_base": Decimal(row["total_base"])}

    def get_return(self, return_id: int) -> dict[str, Any] | None:
        row = self.repository.get_return(int(return_id))
        if row is not None:
            row["label"] = return_label(row)
            row["lines"] = self.repository.lines(int(return_id))
        return row

    def choices(self) -> dict[str, Any]:
        """زي فاتورة البيع + أسباب المرتجع."""
        inv = self.invoices
        items = inv.item_choices()
        units: dict[int, list[tuple[int, str, Decimal]]] = {
            int(i["id"]): [(int(i["unit_id"]), i["unit_name"], ONE)] for i in items}
        for u in inv.item_units():
            units.setdefault(int(u["item_id"]), []).append((int(u["unit_id"]), u["unit_name"], Decimal(u["factor"])))
        return {"customers": inv.customer_choices(), "warehouses": inv.warehouse_choices(), "items": items,
                "units": units, "tax_codes": inv.tax_codes(), "currencies": inv.currencies(),
                "treasuries": inv.treasury_choices(), "banks": inv.bank_account_choices(),
                "projects": inv.project_choices(), "base_currency": inv.base_currency(),
                "reasons": self.repository.reasons()}

    def reasons(self) -> list[dict]:
        return self.repository.reasons()

    def rate_on(self, currency_code: str, day: dt.date) -> Decimal | None:
        return self.invoices.rate_on(str(currency_code), day)

    def customer_defaults(self, customer_id: int, day: dt.date) -> dict[str, Any]:
        c = self.invoices.customer(int(customer_id))
        if c is None:
            raise BusinessError("العميل مش موجود (ممكن يكون اتمسح).")
        return {"currency_code": c["currency_code"], "fx_rate": self.rate_on(c["currency_code"], day),
                "tax_reg_no": c["tax_reg_no"], "is_cash": bool(c["is_system"])}

    def customer_invoices(self, customer_id: int, stock_kind: str | None = None,
                          currency_code: str | None = None, exclude_return_id: int | None = None) -> list[dict]:
        """فواتير العميل المرحّلة اللي لسه فيها حاجة ترجع ومعاها ``remaining_lines`` (عدد السطور دي)."""
        return self.repository.returnable_invoices(int(customer_id), stock_kind, currency_code,
                                                   exclude_return_id=exclude_return_id)

    def returnable_invoices(self, customer_id: int | None = None, stock_kind: str | None = None,
                            currency_code: str | None = None, date_from: dt.date | None = None,
                            date_to: dt.date | None = None, keyword: str = "", exclude_return_id: int | None = None,
                            page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict]:
        """نافذة «فواتير المبيعات» في المرتجع: الفواتير المرحّلة اللي لسه فيها حاجة ترجع — بفترة وكلمة وصفحات."""
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.returnable_invoices(customer_id, stock_kind, currency_code, date_from, date_to,
                                                   keyword, exclude_return_id, limit=page_size, offset=offset)

    def count_returnable_invoices(self, customer_id: int | None = None, stock_kind: str | None = None,
                                  currency_code: str | None = None, date_from: dt.date | None = None,
                                  date_to: dt.date | None = None, keyword: str = "",
                                  exclude_return_id: int | None = None) -> int:
        return self.repository.count_returnable_invoices(customer_id, stock_kind, currency_code, date_from, date_to,
                                                         keyword, exclude_return_id)

    def invoice_lines(self, invoice_id: int, exclude_return_id: int | None = None) -> list[dict]:
        """سطور فاتورة بيع للمرتجع: اتباع · رجع قبل كده · فاضل (بالوحدة الأساسية) · ``source_price`` لكل وحدة ·
        ``source_cost`` = تكلفة الوحدة الأساسية اللي طلعت بيها."""
        rows = self.repository.invoice_lines_for(invoice_id=int(invoice_id), exclude_return_id=exclude_return_id)
        return [self._source_info(r) for r in rows]

    @staticmethod
    def _source_info(r: dict[str, Any]) -> dict[str, Any]:
        base_qty, returned = Decimal(r["base_qty"]), Decimal(r["returned_qty"] or 0)
        r["remaining_base"] = max(ZERO, base_qty - returned)
        r["base_price"] = (Decimal(r["net_amount"]) / base_qty) if base_qty else ZERO
        r["source_price"] = (r["base_price"] * Decimal(r["factor"])).quantize(STEP6, rounding=ROUND_HALF_UP)
        if r.get("source_cost") is not None:
            r["source_cost"] = Decimal(r["source_cost"]).quantize(STEP6, rounding=ROUND_HALF_UP)
        return r

    def item_info(self, item_id: int, warehouse_id: int | None, stock_kind: str = "invoice",
                  customer_id: int | None = None) -> dict[str, Any]:
        """لخانة «إضافة صنف»: المتاح · آخر سعر للعميل (بالجنيه للوحدة الأساسية) · سعر البيع · المتوسط (بالصلاحية)."""
        kind = stock_kind if stock_kind in KINDS else "invoice"
        item = self.invoices.items([int(item_id)]).get(int(item_id)) or {}
        last = self.repository.last_prices(int(customer_id), [int(item_id)]).get(int(item_id)) if customer_id else None
        return {"available": self.ledger.available(int(item_id), warehouse_id, kind) if warehouse_id else None,
                "last_price": last, "sale_price": item.get("sale_price"),
                "avg": self.ledger.average_cost(int(item_id), kind) if self.can_show_cost() else None}

    def customer_balance(self, customer_id: int) -> Decimal:
        return self.invoices.customer_balance(int(customer_id))

    # ---- أسباب المرتجع (المستخدم بيزوّد ويعدّل) -------------------------------------
    def save_reason(self, reason_id: int | None, name: str, is_active: bool = True) -> int:
        PermissionService.require(f"{PERM}.create" if reason_id is None else f"{PERM}.edit")
        name = " ".join(str(name or "").split())
        if not name:
            raise BusinessError("اكتب السبب.")
        if len(name) > 80:
            raise BusinessError("السبب أطول من المسموح (80 حرف).")
        try:
            with self.db.atomic() as tx:
                if reason_id is None:
                    reason_id = self.repository.insert_reason(name, tx)
                    action, before = "create", None
                else:
                    before = next((r for r in self.repository.reasons(ex=tx) if r["id"] == int(reason_id)), None)
                    if before is None or not self.repository.update_reason(int(reason_id), name, bool(is_active), tx):
                        raise BusinessError("السبب ده مش موجود (ممكن يكون اتمسح).")
                    action = "update"
                self.audit.record(tx, action=action, entity="sales.return_reasons", entity_id=int(reason_id),
                                  entity_label="سبب مرتجع مبيعات", record_label=name, before=before,
                                  after={"name_ar": name, "is_active": bool(is_active)})
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError(f"السبب «{name}» موجود قبل كده.") from exc
        return int(reason_id)

    def delete_reason(self, reason_id: int) -> None:
        """السبب اللي اتستعمل مايتمسحش (يتوقف بس)."""
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            row = next((r for r in self.repository.reasons(ex=tx) if r["id"] == int(reason_id)), None)
            if row is None:
                return
            if row["used"]:
                raise BusinessError(f"السبب «{row['name_ar']}» متسجل على مرتجعات — اعمله «موقوف» بدل المسح.")
            self.repository.delete_reason(int(reason_id), tx)
            self.audit.record(tx, action="delete", entity="sales.return_reasons", entity_id=int(reason_id),
                              entity_label="سبب مرتجع مبيعات", record_label=row["name_ar"], before=row, after=None)

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
        fixed = {**data, "customer_id": self._id(data.get("customer_id")) or -1, "pay_kind": "credit"}
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
        day = data.get("return_date")
        if isinstance(day, dt.datetime):
            day = day.date()
        if not isinstance(day, dt.date):
            raise BusinessError("تاريخ المرتجع مطلوب.")
        customer_id = self._id(data.get("customer_id"))
        if customer_id is None:
            raise BusinessError("اختار العميل.")
        kind = data.get("stock_kind") or "invoice"
        if kind not in KINDS:
            raise BusinessError("نوع المرتجع لازم «فاتورة» أو «بيان».")
        base = self.invoices.base_currency()
        currency = str(data.get("currency_code") or base).strip().upper()
        rate = ONE if currency == base else parse_number(data.get("fx_rate"), "سعر الصرف", maximum=MAX_RATE)
        if rate <= 0:
            raise BusinessError(f"اكتب سعر صرف {currency} بتاريخ المرتجع (مفيش سعر متسجل).")
        pay = data.get("pay_kind") or "credit"
        if pay not in PAY_KINDS:
            raise BusinessError("نوع المرتجع لازم «آجل» أو «نقدي».")
        source = treasury = bank = None
        if pay == "cash":
            source = data.get("source_kind") or "treasury"
            if source not in SOURCE_KINDS:
                raise BusinessError("الصرف النقدي من خزنة أو بنك.")
            treasury = self._id(data.get("treasury_id")) if source == "treasury" else None
            bank = self._id(data.get("bank_account_id")) if source == "bank" else None
            if source == "treasury" and treasury is None:
                raise BusinessError("اختار الخزنة اللي الفلوس هتطلع منها.")
            if source == "bank" and bank is None:
                raise BusinessError("اختار الحساب البنكي اللي الفلوس هتطلع منه.")
        return {"return_date": day, "customer_id": customer_id,
                "customer_ref": self._text(data.get("customer_ref"), "رقم مرجع العميل", 40),
                "reason_id": self._id(data.get("reason_id")), "stock_kind": kind, "pay_kind": pay,
                "source_kind": source, "treasury_id": treasury, "bank_account_id": bank,
                "warehouse_id": self._id(data.get("warehouse_id")), "project_id": self._id(data.get("project_id")),
                "currency_code": currency, "fx_rate": rate,
                "description": self._text(data.get("description"), "الملاحظات", 500)}

    def _lines(self, raw: list[dict[str, Any]], header: dict[str, Any], *, return_id: int | None = None,
               strict: bool = True, errors: dict[int, str] | None = None) -> list[dict[str, Any]]:
        """السطور (الفاضي بيتشال) — ``strict=False`` (المعاينة) بيتجاهل السطر الناقص (وغلطه في ``errors``)."""
        rows = [r for r in (raw or []) if self._id(r.get("item_id")) or self._id(r.get("source_line_id"))
                or str(r.get("qty") or "").strip()]
        if len(rows) > MAX_LINES:
            raise BusinessError(f"المرتجع الواحد لحد {MAX_LINES:,} صنف.")
        inv = self.invoices
        sources = {int(s["id"]): self._source_info(s) for s in self.repository.invoice_lines_for(
            line_ids=[self._id(r.get("source_line_id")) for r in rows if self._id(r.get("source_line_id"))],
            exclude_return_id=return_id)}
        item_ids = [self._id(r.get("item_id")) for r in rows if self._id(r.get("item_id"))]
        item_ids += [int(s["item_id"]) for s in sources.values()]
        items = inv.items(item_ids)
        units: dict[int, dict[int, Decimal]] = {}
        for u in inv.item_units(list(items)):
            units.setdefault(int(u["item_id"]), {})[int(u["unit_id"])] = Decimal(u["factor"])
        whs = inv.warehouses([self._id(r.get("warehouse_id")) or header["warehouse_id"] for r in rows]
                             + [s["warehouse_id"] for s in sources.values()])
        taxes = {int(t["id"]): t for t in inv.tax_codes()}
        base = inv.base_currency()
        statement = header["stock_kind"] == "statement"
        used: dict[int, Decimal] = {}                       # الكمية الأساسية من كل سطر فاتورة في المرتجع ده
        out: list[dict[str, Any]] = []
        for n, r in enumerate(rows, start=1):
            try:
                source_id = self._id(r.get("source_line_id"))
                src = None
                if source_id is not None:
                    src = sources.get(source_id)
                    if src is None:
                        raise BusinessError(f"سطر {n}: سطر الفاتورة الأصلية مش موجود (ممكن تكون اتعدّلت) — دوس «تحديث».")
                    item_id = int(src["item_id"])
                else:
                    item_id = self._id(r.get("item_id"))
                if item_id is None or item_id not in items:
                    raise BusinessError(f"سطر {n}: اختار الصنف.")
                item = items[item_id]
                label = f"سطر {n} ({item['code']} {item['name']})"
                if src is not None:
                    where = f"فاتورة {src['invoice_no']}"
                    if src["invoice_status"] != "posted":
                        raise BusinessError(f"{label}: {where} مش مرحّلة.")
                    if int(src["customer_id"]) != int(header["customer_id"] or 0):
                        raise BusinessError(f"{label}: {where} لعميل تاني.")
                    if src["stock_kind"] != header["stock_kind"]:
                        raise BusinessError(f"{label}: {where} «{KINDS[src['stock_kind']]}» والمرتجع "
                                            f"«{KINDS[header['stock_kind']]}» — لازم نفس النوع.")
                    if src["currency_code"] != header["currency_code"]:
                        raise BusinessError(f"{label}: {where} بعملة {src['currency_code']} والمرتجع بـ "
                                            f"{header['currency_code']} — لازم نفس العملة.")
                warehouse_id = None
                if not item["is_service"]:
                    default_wh = src["warehouse_id"] if src is not None else header["warehouse_id"]
                    warehouse_id = self._id(r.get("warehouse_id")) or default_wh
                    if warehouse_id is None or warehouse_id not in whs:
                        raise BusinessError(f"{label}: اختار المخزن.")
                    if whs[warehouse_id]["is_virtual"]:
                        raise BusinessError(f"{label}: المخزن «{whs[warehouse_id]['name_ar']}» وهمي — مالوش مرتجع "
                                            "مبيعات (اختار مخزن حقيقي).")
                unit_id = self._id(r.get("unit_id")) or (int(src["unit_id"]) if src else int(item["unit_id"]))
                factor = ONE if unit_id == int(item["unit_id"]) else units.get(item_id, {}).get(unit_id)
                if factor is None:
                    raise BusinessError(f"{label}: الوحدة دي مش من وحدات الصنف.")
                qty = parse_number(r.get("qty"), f"{label} — الكمية", maximum=MAX_QTY)
                if qty <= 0:
                    raise BusinessError(f"{label}: اكتب كمية أكبر من صفر.")
                base_qty = (qty * factor).quantize(STEP6, rounding=ROUND_HALF_UP)
                if base_qty <= 0:
                    raise BusinessError(f"{label}: الكمية صغيرة أوي.")
                if src is not None:
                    used[source_id] = used.get(source_id, ZERO) + base_qty
                    left = src["remaining_base"] - used[source_id] + base_qty
                    if used[source_id] > src["remaining_base"]:
                        raise BusinessError(
                            f"{label}: أكتر من اللي فاضل يرجع من {where} (فاضل {fmt_qty(max(ZERO, left) / factor)} "
                            f"{'' if factor == 1 else 'من الوحدة دي '}— اتباع {fmt_qty(Decimal(src['base_qty']))} "
                            f"ورجع قبل كده {fmt_qty(Decimal(src['returned_qty'] or 0))} {item['unit_name']}).")
                price_raw = r.get("unit_price")
                if src is not None and str(price_raw if price_raw is not None else "").strip() == "":
                    price = (src["base_price"] * factor).quantize(STEP6, rounding=ROUND_HALF_UP)
                else:
                    price = parse_number(price_raw, f"{label} — السعر", maximum=MAX_PRICE)
                if src is not None:
                    tax_id = None if statement else (int(src["tax_code_id"]) if src["tax_code_id"] else None)
                else:
                    tax_id = None if statement else self._id(r.get("tax_code_id"))
                tax_rate = ZERO
                if tax_id is not None:
                    if tax_id not in taxes:
                        raise BusinessError(f"{label}: كود الضريبة مش لضريبة إضافة على المبيعات.")
                    tax_rate = Decimal(taxes[tax_id]["rate"])
                fx = Decimal(src["fx_rate"]) if src is not None else header["fx_rate"]
                currency = header["currency_code"]
                if currency == base:
                    fx = ONE
            except BusinessError as exc:
                if strict:
                    raise
                if errors is not None:
                    errors[n] = str(exc).split(": ", 1)[-1]
                continue
            out.append({"source_line_id": source_id, "item_id": item_id, "warehouse_id": warehouse_id,
                        "unit_id": unit_id, "factor": factor, "qty": qty, "unit_price": price, "tax_code_id": tax_id,
                        "tax_rate": tax_rate, "currency_code": currency, "fx_rate": fx, "item": item,
                        "is_service": bool(item["is_service"]), "category_id": item["category_id"], "line": n,
                        "source": src})
        if strict and not out:
            raise BusinessError("ضيف صنف واحد على الأقل للمرتجع.")
        return out

    def _compute(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, Any]:
        calc = calculate(lines, statement=header["stock_kind"] == "statement",
                         base_currency=self.invoices.base_currency())
        for key in ("net_amount", "vat_amount", "total_amount", "net_base", "vat_base", "total_base"):
            header[key] = calc[key]
        return calc

    def _unit_costs(self, lines: list[dict[str, Any]], kind: str, day: dt.date,
                    ex: Executor | None = None) -> dict[int, Decimal]:
        """تكلفة رجوع الوحدة الأساسية لكل سطر مخزني (رقم السطر ← التكلفة): المربوط = تكلفته في فاتورة البيع ·
        الحر (أو المربوط اللي مالوش حركة) = المتوسط بتاريخ المرتجع."""
        avgs: dict[int, Decimal] = {}
        out: dict[int, Decimal] = {}
        for ln in lines:
            if ln["is_service"]:
                continue
            src = ln.get("source")
            if src is not None and src.get("source_cost") is not None:
                out[ln["line"]] = Decimal(src["source_cost"])
                continue
            if ln["item_id"] not in avgs:
                avgs[ln["item_id"]] = Decimal(self.ledger.average_cost(ln["item_id"], kind, on_date=day, ex=ex))
            out[ln["line"]] = avgs[ln["item_id"]].quantize(STEP6, rounding=ROUND_HALF_UP)
        return out

    def _clean(self, data: dict[str, Any], return_id: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
        header = self._header(data)
        calc = self._compute(header, self._lines(data.get("lines") or [], header, return_id=return_id))
        costs = self._unit_costs(calc["lines"], header["stock_kind"], header["return_date"])
        for ln in calc["lines"]:
            ln["unit_cost"] = costs.get(ln["line"], ZERO)
        return header, calc

    def totals(self, data: dict[str, Any], return_id: int | None = None) -> dict[str, Any]:
        header = self._header(data, relaxed=True)
        return self._compute(header, self._lines(data.get("lines") or [], header, return_id=return_id, strict=False))

    # ---- القيد -------------------------------------------------------------------
    @staticmethod
    def _fc(line: dict[str, Any], amount_fc: Decimal, base: str) -> dict[str, Any]:
        if line["currency_code"] == base or not amount_fc:
            return {}
        return {"currency_code": line["currency_code"], "fx_rate": line["fx_rate"], "amount_fc": amount_fc}

    def _return_role(self, line: dict[str, Any], cache: dict[Any, bool]) -> str:
        """البضاعة على «مردودات المبيعات» من نوعها · الخدمي زي فاتورة البيع: نوعه ليه حساب مبيعات ← المردودات،
        وإلا عكس «إيراد الخدمات» (4102)."""
        if not line.get("is_service"):
            return "sales_return"
        category = line.get("category_id")
        if category not in cache:
            cache[category] = self.invoices.own_sales_account(category)
        return "sales_return" if cache[category] else "service_return"

    def _posting_lines(self, header: dict[str, Any], lines: list[dict[str, Any]],
                       costs: list[tuple[int | None, Decimal]]) -> list[PostingLine]:
        """``costs`` = [(نوع الصنف، تكلفة الرجوع بالجنيه)] لكل حركة دخول (من الدفتر وقت الترحيل أو المتوقعة)."""
        base = self.invoices.base_currency()
        party = ("customer", int(header["customer_id"]))
        cache: dict[Any, bool] = {}
        out: list[PostingLine] = []
        for ln in lines:
            out.append(PostingLine(self._return_role(ln, cache), Decimal(ln["net_base"]),
                                   category_id=ln.get("category_id"),
                                   **self._fc(ln, Decimal(ln["net_amount"]), base)))
            if Decimal(ln["vat_base"]):
                out.append(PostingLine("vat", ln["vat_base"], tax_code_id=ln["tax_code_id"],
                                       **self._fc(ln, Decimal(ln["vat_amount"]), base)))
            out.append(PostingLine("receivable", ln["total_base"], party=party,
                                   **self._fc(ln, Decimal(ln["total_amount"]), base)))
        for category, cost in costs:
            if cost:
                out.append(PostingLine("inventory", cost, category_id=category))
                out.append(PostingLine("cogs", cost, category_id=category))
        return out

    def _refund_lines(self, header: dict[str, Any], lines: list[dict[str, Any]]) -> list[PostingLine]:
        base = self.invoices.base_currency()
        party = ("customer", int(header["customer_id"]))
        out = [PostingLine("receivable", ln["total_base"], party=party,
                           **self._fc(ln, Decimal(ln["total_amount"]), base)) for ln in lines]
        total = sum((Decimal(ln["total_base"]) for ln in lines), ZERO)
        out.append(PostingLine("cash", total, treasury_id=header["treasury_id"]) if header["source_kind"] == "treasury"
                   else PostingLine("bank", total, bank_account_id=header["bank_account_id"]))
        return out

    def _named(self, built: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.invoices.accounts([ln["account_id"] for ln in built])
        return [{"account_code": names.get(ln["account_id"], {}).get("code", ""),
                 "account_name": names.get(ln["account_id"], {}).get("name_ar", ""), "role_name": ln["role_name"],
                 "debit": ln["debit"], "credit": ln["credit"], "currency_code": ln.get("currency_code"),
                 "amount_fc": ln.get("amount_fc"), "fx_rate": ln.get("fx_rate")} for ln in built]

    def _cost_center(self, header: dict[str, Any]) -> int | None:
        """المردودات والتكلفة ببُعد أمر التوريد (مركز تكلفته) — زي فاتورة البيع."""
        if not header.get("project_id"):
            return None
        project = self.invoices.project(int(header["project_id"]))
        return int(project["cost_center_id"]) if project and project["cost_center_id"] else None

    # ---- المراجعة قبل الترحيل -----------------------------------------------------
    def review(self, data: dict[str, Any], return_id: int | None = None) -> dict[str, Any]:
        """``checks`` = [(ok / no / warn / info، النص)] · ``lines`` = غلط / تنبيه كل سطر · ``stock`` = الرصيد والمتوسط
        قبل / بعد · ``entry`` / ``refund`` = القيدين · ``customer`` = رصيده قبل / بعد · ``calc`` · ``ready``."""
        checks: list[tuple[str, str]] = []
        out: dict[str, Any] = {"checks": checks, "lines": {}, "stock": [], "entry": None, "refund": None,
                               "customer": None, "calc": None, "ready": False}
        try:
            header = self._header(data)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            try:
                out["calc"] = self.totals(data, return_id)
            except BusinessError:
                pass
            return out
        errors: dict[int, str] = {}
        try:
            lines = self._lines(data.get("lines") or [], header, return_id=return_id, strict=False, errors=errors)
            calc = self._compute(header, lines)
        except BusinessError as exc:
            checks.append(("no", str(exc)))
            return out
        out["calc"] = calc
        for n, text in errors.items():
            out["lines"][n] = ("no", text)
            checks.append(("no", f"سطر {n}: {text}"))
        rec = self.repository.get_return(int(return_id)) if return_id else None
        posted = bool(rec and rec["status"] == "posted")
        customer = self.invoices.customer(header["customer_id"])
        if customer is None:
            checks.append(("no", "العميل مش موجود (ممكن يكون اتمسح)."))
            return out
        checks.append(("ok", f"العميل: {customer['code']} «{customer['name_ar']}»") if customer["is_active"] else
                      ("no", f"العميل «{customer['code']} {customer['name_ar']}» موقوف — اختار عميل نشط."))
        if customer["is_system"] and header["pay_kind"] != "cash":
            checks.append(("no", f"«{customer['name_ar']}» للبيع الكاش — مرتجعه نقدي بس."))
        if not (data.get("lines") or []):
            checks.append(("no", "ضيف صنف واحد على الأقل للمرتجع."))
        base = self.invoices.base_currency()
        invoices = sorted({ln["source"]["invoice_no"] for ln in calc["lines"] if ln["source"]})
        free = sum(1 for ln in calc["lines"] if not ln["source"])
        if invoices:
            checks.append(("ok", "من فاتورة " + "، ".join(invoices) + (f" + {free} صنف من غير فاتورة" if free else "")))
        elif free:
            checks.append(("info", "من غير فاتورة أصلية — السعر من «آخر سعر للعميل» أو مكتوب باليد، والتكلفة بالمتوسط"))
        kind = header["stock_kind"]
        show_cost = self.can_show_cost()

        # تكلفة الرجوع: المرحّل = اللي اتسجل على الحركات · غير كده = من الفاتورة الأصلية أو المتوسط دلوقتي.
        stock_lines = [ln for ln in calc["lines"] if not ln["is_service"]]
        if posted:
            moved = {int(m["line_no"]): Decimal(m["booked_value"] or 0) for m in self.repository.movements(int(return_id))}
            line_value = {ln["line"]: moved.get(k, ZERO) for k, ln in enumerate(stock_lines, start=1)}
        else:
            unit = self._unit_costs(calc["lines"], kind, header["return_date"])
            line_value = {ln["line"]: _r2(Decimal(ln["base_qty"]) * unit.get(ln["line"], ZERO)) for ln in stock_lines}
        costs = [(ln.get("category_id"), line_value.get(ln["line"], ZERO)) for ln in stock_lines]
        for ln in calc["lines"]:
            item, n = ln["item"], ln["line"]
            src = ln["source"]
            mark = None
            if src is not None:
                sp = (src["base_price"] * ln["factor"]).quantize(STEP6, rounding=ROUND_HALF_UP)
                if _r2(sp) != _r2(Decimal(ln["unit_price"])):
                    mark = ("warn", f"السعر غير سعر الفاتورة ({fmt(sp)}) — رصيد العميل هيقل بالسعر ده")
            elif Decimal(ln["unit_price"]) == 0:
                mark = ("warn", "السعر صفر — العميل مش هيتخصمله حاجة")
            if not mark and not ln["is_service"] and not posted and not line_value.get(n):
                mark = ("warn", "مفيش تكلفة للصنف ده (مااتشراش قبل كده) — هيرجع المخزن بتكلفة صفر")
            if mark and n not in out["lines"]:
                out["lines"][n] = mark
                checks.append((mark[0], f"سطر {n} ({item['code']}): {mark[1]}"))
        if not header["customer_ref"]:
            checks.append(("info", "رقم مرجع العميل فاضي (اختياري)"))
        if header["currency_code"] != base:
            rates = " · ".join(f"× {r}" for r in calc["by_rate"])
            checks.append(("info", f"العملة {header['currency_code']} ({rates}) — القيد بالجنيه ({fmt(calc['total_base'])})"))
        if kind == "statement":
            checks.append(("info", "«بيان»: من غير ض.ق.م، والبضاعة بترجع لرصيد «بيان»"))
        elif calc["vat_base"]:
            checks.append(("info", f"ض.ق.م مخرجات بتتعكس {fmt(calc['vat_base'])}"))
        if header["pay_kind"] == "credit":
            checks.append(("info", f"آجل — رصيد العميل بيقل {fmt(calc['total_base'])}"))
        else:
            checks.append(("info", "نقدي — رجّعنا الفلوس للعميل ← قيد صرف من " +
                           ("الخزنة" if header["source_kind"] == "treasury" else "البنك") + " بيترحّل مع المرتجع"))
        if stock_lines:
            checks.append(("info", "البضاعة بترجع المخزن بتكلفة البيع الأصلية" +
                           (f" ({fmt(sum((c for _k, c in costs), ZERO))} ج)" if show_cost else "") +
                           " — والحر بالمتوسط"))
        if calc["service_base"]:
            checks.append(("info", f"صنف خدمي {fmt(calc['service_base'])} ← بيعكس إيراد الخدمات"))
        if header["project_id"]:
            project = self.invoices.project(header["project_id"])
            if project and project["customer_id"] and int(project["customer_id"]) != header["customer_id"]:
                checks.append(("no", f"أمر التوريد {project['code']} لعميل تاني."))
        year = self.invoices.year_of(header["return_date"])
        if year is None:
            checks.append(("no", f"مفيش سنة مالية فيها تاريخ {header['return_date']:%d/%m/%Y}."))
        elif year["status"] == "closed":
            checks.append(("no", f"السنة المالية {year['year_code']} مقفولة."))

        merged: dict[int, dict[str, Any]] = {}
        for ln in stock_lines:
            m = merged.setdefault(ln["item_id"], {"item_id": ln["item_id"], "item_code": ln["item"]["code"],
                                                  "item_name": ln["item"]["name"],
                                                  "unit_name": ln["item"]["unit_name"], "qty": ZERO, "value": ZERO})
            m["qty"] += ln["base_qty"]
            m["value"] += line_value.get(ln["line"], ZERO)
        for m in merged.values():
            q_now = self.ledger.available(m["item_id"], None, kind)
            a_now = self.ledger.average_cost(m["item_id"], kind)
            if posted:
                q0, a1 = q_now - m["qty"], a_now
                a0 = (q_now * a_now - m["value"]) / q0 if q0 > 0 else a_now
            else:
                q0, a0 = q_now, a_now
                after = q0 + m["qty"]
                a1 = (max(q0, ZERO) * a0 + m["value"]) / after if after > 0 and q0 >= 0 else a0
            row = {**m, "qty_before": q0, "qty_after": q0 + m["qty"]}
            if show_cost:
                row.update({"avg_before": Decimal(a0).quantize(STEP6, rounding=ROUND_HALF_UP),
                            "avg_after": Decimal(a1).quantize(STEP6, rounding=ROUND_HALF_UP)})
            out["stock"].append(row)

        if calc["lines"] and (calc["total_base"] or any(c for _k, c in costs)):
            try:
                out["entry"] = self._named(self.posting.build(
                    DOC_TYPE, self._posting_lines(header, calc["lines"], costs),
                    cost_center_id=self._cost_center(header), project_id=header["project_id"]))
                if not show_cost:                                  # التكلفة مابتبانش من غير الصلاحية
                    out["entry"] = [e for e in out["entry"] if e["role_name"] not in COST_ROLES]
                if header["pay_kind"] == "cash" and calc["total_base"]:
                    out["refund"] = self._named(self.posting.build(REFUND_DOC, self._refund_lines(header, calc["lines"]),
                                                                   project_id=header["project_id"]))
            except BusinessError as exc:
                checks.append(("no", f"القيد: {exc}"))
        elif calc["lines"]:
            checks.append(("info", "إجمالي المرتجع صفر — مفيش قيد"))
        bal = self.invoices.customer_balance(header["customer_id"])
        effect = ZERO if posted or header["pay_kind"] == "cash" else calc["total_base"]
        out["customer"] = {"before": bal, "after": bal - effect}
        if not posted and invoices:
            checks.append(("info", "وقت الترحيل فواتير البيع الأصلية بتتقفل — جهاز تاني بيرجّع من نفس الفاتورة بيستنى"))
        out["ready"] = bool(calc["lines"]) and not any(c[0] == "no" for c in checks)
        return out

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, return_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_return(int(return_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=return_id)
        if before is None:
            raise BusinessError("المرتجع ده مش موجود (ممكن يكون اتمسح).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — مرتجعاتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"المرتجع {return_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"المرتجع {return_label(before)} مش مرحّل.")
        before["lines"] = self.repository.lines(int(return_id), ex=tx)
        return before

    def _snapshot(self, tx: Executor, return_id: int) -> dict[str, Any]:
        row = self.repository.get_return(int(return_id), ex=tx)
        row["lines"] = self.repository.lines(int(return_id), ex=tx)
        return row

    def _record(self, tx: Executor, action: str, return_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=return_id, entity_label=ENTITY_LABEL,
                          record_label=f"{return_label(row)} — {row.get('customer_name') or ''} — "
                                       f"{fmt(row.get('total_amount'))} {row.get('currency_code') or ''}",
                          before=before, after=after)

    def _post(self, tx: Executor, return_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """قفل فواتير البيع الأصلية ← التحقق من الكمية ← قفل الأصناف ← تكلفة الرجوع (الأصلية / المتوسط) ← مرحّل
        (الرقم) ← حركات الدخول ← القيد (+ الصرف) — في نفس الـ transaction."""
        sources = [int(ln["source_line_id"]) for ln in before["lines"] if ln["source_line_id"]]
        self.repository.lock_invoices(sources, tx)
        left: dict[int, dict[str, Any]] = {}
        if sources:                                 # بعد القفل: اللي جهاز تاني رحّله قبلنا بيبان هنا
            left = {int(s["id"]): self._source_info(s) for s in self.repository.invoice_lines_for(
                line_ids=sources, exclude_return_id=int(return_id), ex=tx)}
            used: dict[int, Decimal] = {}
            for ln in before["lines"]:
                sid = ln["source_line_id"]
                if not sid:
                    continue
                used[int(sid)] = used.get(int(sid), ZERO) + Decimal(ln["base_qty"])
                src = left[int(sid)]
                if src["invoice_status"] != "posted":
                    raise BusinessError(f"فاتورة البيع {src['invoice_no'] or ''} اتلغى ترحيلها — دوس «تحديث».")
                if used[int(sid)] > src["remaining_base"]:
                    raise BusinessError(
                        f"الصنف {ln['item_code']} «{ln['item_name']}»: المرتجع من فاتورة {src['invoice_no']} أكتر "
                        f"من اللي فاضل ({fmt_qty(src['remaining_base'])} {ln['base_unit_name']}) — ممكن جهاز تاني "
                        "رحّل مرتجع على نفس الفاتورة. دوس «تحديث» وعدّل الكمية.")
        stock = [ln for ln in before["lines"] if not ln["is_service"]]
        if stock:                                   # التكلفة جوه قفل الصنف (المتوسط مايتغيّرش من جهاز تاني)
            self.ledger.repository.lock_items([int(ln["item_id"]) for ln in stock], tx)
            rows = [{"line": int(ln["line_no"]), "item_id": int(ln["item_id"]), "is_service": False,
                     "source": left.get(int(ln["source_line_id"])) if ln["source_line_id"] else None}
                    for ln in stock]
            unit = self._unit_costs(rows, before["stock_kind"], before["return_date"], ex=tx)
            self.repository.set_line_costs({int(ln["id"]): unit[int(ln["line_no"])] for ln in stock}, tx)
        updated = self.repository.set_status(return_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=return_id)
        v = self._snapshot(tx, return_id)
        stock_rows = [ln for ln in v["lines"] if not ln["is_service"]]
        costs: list[tuple[int | None, Decimal]] = []
        if stock_rows:
            moved = self.ledger.post(
                tx, doc_type=DOC_TYPE, source_id=return_id, source_no=v["return_no"], movement_date=v["return_date"],
                lines=[StockLine(ln["item_id"], ln["warehouse_id"], Decimal(ln["base_qty"]), v["stock_kind"],
                                 unit_cost=Decimal(ln["unit_cost"])) for ln in stock_rows],
                allow_negative=self.can_negative())
            by_no = {int(m["line_no"]): Decimal(m["booked_value"] or 0) for m in moved}
            costs = [(ln["category_id"], by_no.get(n, ZERO)) for n, ln in enumerate(stock_rows, start=1)]
        if Decimal(v["total_base"]) or any(c for _k, c in costs):
            note = v["description"] or "مرتجع مبيعات"
            ref = v["customer_ref"]
            self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=return_id, source_no=v["return_no"],
                                       entry_date=v["return_date"], description=f"{note} — {v['customer_name']}",
                                       reference=ref, lines=self._posting_lines(v, v["lines"], costs),
                                       cost_center_id=v["project_cost_center_id"], project_id=v["project_id"])
            if v["pay_kind"] == "cash" and Decimal(v["total_base"]):
                self.posting.post_document(tx, doc_type=REFUND_DOC, source_id=return_id, source_no=v["return_no"],
                                           entry_date=v["return_date"],
                                           description=f"صرف نقدي لمرتجع المبيعات {v['return_no']} — {v['customer_name']}",
                                           reference=ref, lines=self._refund_lines(v, v["lines"]),
                                           project_id=v["project_id"])
        self._record(tx, "post", return_id, before, v)
        return v

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, return_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        PermissionService.require(f"{PERM}.create" if return_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        header, calc = self._clean(data, return_id)
        lines = [{k: ln[k] for k in ("source_line_id", "item_id", "warehouse_id", "unit_id", "factor", "qty",
                                     "unit_price", "tax_code_id", "tax_rate", "currency_code", "fx_rate", "net_amount",
                                     "vat_amount", "total_amount", "net_base", "vat_base", "total_base", "unit_cost")}
                 for ln in calc["lines"]]
        try:
            with self.db.atomic() as tx:
                before = None
                if return_id is None:
                    return_id = self.repository.insert_return({**header, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock(tx, int(return_id), expected_version, draft_only=True)
                    self.repository.replace_lines(int(return_id), [], ex=tx)       # قبل تغيير العميل
                    updated = self.repository.update_return(int(return_id), header, ex=tx,
                                                            expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=return_id)
                self.repository.replace_lines(int(return_id), lines, ex=tx)
                after = self._snapshot(tx, int(return_id))
                self._record(tx, "create" if before is None else "update", int(return_id), before, after)
                if post:
                    self._post(tx, int(return_id), after)
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError("المرتجع ده اتعدّل من جهاز تاني في نفس اللحظة — دوس «تحديث» وجرّب تاني.") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(return_id)

    def post(self, return_id: int, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(return_id), expected_version, draft_only=True)
                after = self._post(tx, int(return_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["return_no"]

    def unpost(self, return_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل: الحركات والقيدين بيتمسحوا (البضاعة بتطلع تاني) والمرتجع مسودة برقمه."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(return_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=REFUND_DOC, source_id=int(return_id))
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=int(return_id))
                self.ledger.remove(tx, doc_type=DOC_TYPE, source_id=int(return_id), allow_negative=self.can_negative())
                updated = self.repository.set_status(int(return_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=return_id)
                self._record(tx, "unpost", int(return_id), before, self._snapshot(tx, int(return_id)))
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_return(self, return_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(return_id), expected_version, draft_only=True)
                if before["return_no"]:
                    raise BusinessError(f"المرتجع {before['return_no']} خد رقم رسمي قبل كده — مايتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
                deleted = self.repository.delete_return(int(return_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=return_id)
                self._record(tx, "delete", int(return_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def entry_for(self, return_id: int) -> dict[str, Any] | None:
        return self.posting.entry_for(DOC_TYPE, int(return_id))

    def refund_entry_for(self, return_id: int) -> dict[str, Any] | None:
        return self.posting.entry_for(REFUND_DOC, int(return_id))

    def movements(self, return_id: int) -> list[dict]:
        return self.repository.movements(int(return_id))


__all__ = ["SalesReturnService", "calculate", "return_label", "fmt", "fmt_rate", "fmt_qty", "PAGE_SIZE",
           "PAY_KINDS", "SOURCE_KINDS", "PERM"]
