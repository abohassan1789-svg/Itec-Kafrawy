"""«سند قبض» (للعميل) و«سند صرف» (للمورد) — المرحلة 5، النموذج 5، اختيار المستخدم 2026-11-17 + ردوده.

* **شاشتين وجدولين منفصلين** — الـ Service واحد بـ ``kind`` (``receipt`` / ``payment``) وصلاحية لكل شاشة.
  قرار المستخدم: «سند القبض للعميل وسند الصرف للمورد» (هما دول سندات العملاء والموردين — مفيش تكرار).
  الطرف في البيانات اسمه ``party_id`` للنوعين (عميل في القبض · مورد في الصرف).
* السند **سطر واحد**: الطرف + خزنة أو حساب بنك + المبلغ + البيان (+ أمر التوريد ورقم الدفتر الورقي اختياري) ·
  **مفيش خصم منبع ولا خصم مسموح / مكتسب** (رد المستخدم).
* **«حفظ مسودة»** · **«ترحيل»** (أو حفظ + ترحيل في نفس الـ transaction) ← القاعدة بتصرف رقم ``RV/PV-2026-00001``
  **أول مرة بس** جوه قفل العدّاد، و**محرك الترحيل** بيعمل القيد ويرحّله في نفس الـ transaction:
  القبض = مدين الخزنة / البنك · دائن العملاء · الصرف = مدين الموردين · دائن الخزنة / البنك.
* **«إلغاء الترحيل»** بالصلاحية في السنة المفتوحة: القيد بيتمسح والسند يرجع مسودة **برقمه** ويتعدّل.
* الحذف للمسودة اللي **ماخدتش رقم** بس · كل حاجة في ``sec.audit_log`` قبل/بعد + ``row_version``.
* **الخزنة مش مكفية** (سند الصرف): ``funds_warning`` بيرجّع رسالة تحذير **بس** — الشاشة بتسأل والمستخدم يكمّل
  (رد المستخدم: «يجيلي رسالة تحذير بس»).

**«سداد الموردين» (المرحلة 7 — سكريبت 0050، ردود المستخدم 2026-11-28) — سند الصرف بس:**

* **السند بعملة** (``currency_code`` + ``fx_rate`` بتاريخ السند ويتعدّل) و«صرف من» = خزنة أو حساب بنك **بنفس العملة**.
* **التوزيع على فواتير الشراء** (``allocations`` = [{purchase_invoice_id, amount}]): كل فاتورة بتتسدد **بعملتها بس** ·
  المتبقي = الإجمالي − المرتجع الآجل المربوط − السداد **المرحّل** (المسودات مابتخصمش) · اللي مااتوزّعش = دفعة مقدمة
  على حساب المورد بنفس العملة · ``auto_allocate`` = الأقدم الأول (بالاستحقاق).
* **فرق العملة مع السند**: المورد بيتقفل بسعر الفاتورة (والباقي غير الموزّع بسعر السند) · الخزنة / البنك بسعر السند ·
  الفرق على «فروق عملة مدينة / دائنة».
* وقت الترحيل: **قفل الفواتير** ``FOR UPDATE`` بترتيب ثابت ← المتبقي بيتحسب تاني ← القاعدة بتتأكد آخر الـ transaction
  (جهازين بيدفعوا نفس الفاتورة ← التاني يترفض). توزيع المرحّل مايتغيّرش غير بـ «إلغاء الترحيل» (القيد بيتبني عليه).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.voucher_repository import STATUSES, VoucherRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.journal_service import parse_money
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService

PAGE_SIZE = 50
ZERO = Decimal("0")
CENT = Decimal("0.01")
RATE = Decimal("0.000001")
SOURCE_KINDS = ("treasury", "bank")
ALLOCATION_ORDERS = ("due", "date")


@dataclass(frozen=True)
class VoucherKind:
    code: str            # receipt / payment
    doc_type: str        # نوع المستند في الترقيم وقواعد الترحيل
    entity: str          # الجدول (سجل التعديلات + الصلاحية)
    target: str          # الشاشة في الصلاحيات
    title: str           # «سند قبض» / «سند صرف»
    short: str           # «سند القبض»
    prefix: str          # RV / PV
    party_verb: str      # «استلمنا من» / «صرفنا إلى»
    source_verb: str     # «في» / «من»
    party: str           # customer / supplier (بُعد الطرف في سطر القيد)
    party_label: str     # «العميل» / «المورد»
    party_role: str      # receivable / payable (دور سطر الطرف في قواعد الترحيل)

    @property
    def perm(self) -> str:
        return f"cash.{self.target}"


KINDS = {
    "receipt": VoucherKind("receipt", "customer_receipt", "cash.customer_receipts", "customer_receipts",
                           "سند قبض", "سند القبض", "RV", "استلمنا من", "في", "customer", "العميل", "receivable"),
    "payment": VoucherKind("payment", "supplier_payment", "cash.supplier_payments", "supplier_payments",
                           "سند صرف", "سند الصرف", "PV", "صرفنا إلى", "من", "supplier", "المورد", "payable"),
}


def voucher_label(row: dict[str, Any]) -> str:
    """«RV-2026-00012» أو «مسودة 7»."""
    return row.get("voucher_no") or f"مسودة {row.get('draft_no')}"


def invoice_label(row: dict[str, Any]) -> str:
    return row.get("invoice_no") or f"مسودة {row.get('draft_no')}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


def _money2(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


class VoucherService:
    def __init__(self, kind: str, db: Database | None = None, repository: VoucherRepository | None = None,
                 audit: AuditService | None = None, posting: PostingService | None = None) -> None:
        if kind not in KINDS:
            raise ValueError(f"نوع سند مش معروف: {kind}")
        self.kind = KINDS[kind]
        self.db = db or get_db()
        self.repository = repository or VoucherRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)
        self._base: str | None = None

    @property
    def is_receipt(self) -> bool:
        return self.kind.code == "receipt"

    @property
    def base_currency(self) -> str:
        if self._base is None:
            self._base = self.repository.base_currency()
        return self._base

    # ---- قراية -------------------------------------------------------------
    def list_vouchers(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                      keyword: str = "", page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_vouchers(self.kind.code, date_from, date_to, status, keyword.strip(),
                                             limit=page_size, offset=offset)

    def count_vouchers(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                       keyword: str = "") -> dict[str, Any]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_vouchers(self.kind.code, date_from, date_to, status, keyword.strip())
        return {"total": int(row["total"]), "drafts": int(row["drafts"]), "posted": int(row["posted"]),
                "posted_amount": Decimal(row["posted_amount"])}

    def _load(self, voucher_id: int, ex: Executor | None = None, for_update: bool = False) -> dict[str, Any] | None:
        """السند (+ سطور توزيعه لو سند صرف)."""
        row = self.repository.get_voucher(self.kind.code, int(voucher_id), ex=ex, for_update=for_update)
        if row is not None and not self.is_receipt:
            row["allocations"] = self.repository.allocations(int(voucher_id), ex=ex)
        return row

    def get_voucher(self, voucher_id: int) -> dict[str, Any] | None:
        row = self._load(int(voucher_id))
        if row is not None:
            row["label"] = voucher_label(row)
            if not self.is_receipt:
                allocated = sum((Decimal(a["amount"]) for a in row["allocations"]), ZERO)
                row["allocated"] = allocated
                row["unallocated"] = Decimal(row["amount"]) - allocated
        return row

    def choices(self) -> dict[str, Any]:
        """العملاء (القبض) أو الموردين (الصرف) · الخزائن · الحسابات البنكية · أوامر التوريد · العملات
        (الموقوف بيرجع عشان السند القديم يتعرض)."""
        return {"parties": self.repository.party_choices(self.kind.code),
                "treasuries": self.repository.treasury_choices(),
                "banks": self.repository.bank_account_choices(),
                "projects": self.repository.project_choices(),
                "currencies": self.repository.currencies(),
                "base_currency": self.base_currency}

    def rate_for(self, currency_code: str, day: dt.date) -> Decimal | None:
        """سعر العملة بتاريخ السند (الأساسية = 1 · مفيش سعر متسجل = ``None``)."""
        code = str(currency_code or "").strip().upper()
        if not code or code == self.base_currency:
            return Decimal("1")
        return self.repository.rate_on(code, day)

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        voucher_date = data.get("voucher_date")
        if isinstance(voucher_date, dt.datetime):
            voucher_date = voucher_date.date()
        if not isinstance(voucher_date, dt.date):
            raise BusinessError("تاريخ السند مطلوب.")
        party_id = self._id(data.get("party_id"))
        if party_id is None:
            raise BusinessError(f"اختار {self.kind.party_label}.")
        source_kind = data.get("source_kind") or "treasury"
        if source_kind not in SOURCE_KINDS:
            raise BusinessError("اختار خزنة أو بنك.")
        treasury_id = self._id(data.get("treasury_id")) if source_kind == "treasury" else None
        bank_account_id = self._id(data.get("bank_account_id")) if source_kind == "bank" else None
        if source_kind == "treasury" and treasury_id is None:
            raise BusinessError("اختار الخزنة.")
        if source_kind == "bank" and bank_account_id is None:
            raise BusinessError("اختار الحساب البنكي.")
        amount = parse_money(data.get("amount"), "المبلغ")
        if amount <= 0:
            raise BusinessError("اكتب مبلغ السند.")
        description = " ".join(str(data.get("description") or "").split())
        if not description:
            raise BusinessError("البيان مطلوب (السند ده عن إيه).")
        if len(description) > 500:
            raise BusinessError("«البيان» أطول من المسموح (500 حرف).")
        paper_no = " ".join(str(data.get("paper_no") or "").split()) or None
        if paper_no and len(paper_no) > 30:
            raise BusinessError("«رقم الدفتر الورقي» أطول من المسموح (30 حرف).")
        out = {"voucher_date": voucher_date, "party_id": party_id, "source_kind": source_kind,
               "treasury_id": treasury_id, "bank_account_id": bank_account_id, "amount": amount,
               "description": description, "paper_no": paper_no, "project_id": self._id(data.get("project_id"))}
        if not self.is_receipt:
            currency, rate = self._clean_currency(data, voucher_date)
            source = self._source(source_kind, treasury_id or bank_account_id)
            if source is not None and source["currency_code"] != currency:
                what = "الخزنة" if source_kind == "treasury" else "الحساب البنكي"
                raise BusinessError(f"{what} «{self._source_name(source)}» بعملة {source['currency_code']} — والسند "
                                    f"بعملة {currency}؛ اختار خزنة أو بنك بنفس عملة السند.")
            out.update(currency_code=currency, fx_rate=rate,
                       allocations=self._clean_allocations(data.get("allocations"), amount))
        return out

    def _clean_currency(self, data: dict[str, Any], voucher_date: dt.date) -> tuple[str, Decimal]:
        base = self.base_currency
        currency = str(data.get("currency_code") or base).strip().upper()
        if currency == base:
            return currency, Decimal("1")
        raw = data.get("fx_rate")
        if raw in (None, ""):
            rate = self.repository.rate_on(currency, voucher_date)
        else:
            try:
                rate = Decimal(str(raw).strip().replace(",", ""))
            except InvalidOperation:
                raise BusinessError("«سعر الصرف» لازم يبقى رقم.") from None
        if rate is None or not rate.is_finite() or rate <= 0:
            raise BusinessError(f"اكتب سعر صرف {currency} بتاريخ السند (مفيش سعر متسجل).")
        return currency, rate.quantize(RATE, rounding=ROUND_HALF_UP)

    def _clean_allocations(self, raw: Any, amount: Decimal) -> list[dict[str, Any]]:
        """[{purchase_invoice_id, amount}] — الفاضي والصفر بيتشالوا · الفاتورة مرة واحدة · المجموع ≤ مبلغ السند."""
        out: dict[int, Decimal] = {}
        for item in raw or []:
            invoice_id = self._id(item.get("purchase_invoice_id"))
            value = parse_money(item.get("amount"), "المبلغ المدفوع على الفاتورة")
            if invoice_id is None or value <= 0:
                continue
            if invoice_id in out:
                raise BusinessError("الفاتورة متكررة في التوزيع — كل فاتورة سطر واحد.")
            out[invoice_id] = value
        total = sum(out.values(), ZERO)
        if total > amount:
            raise BusinessError(f"الموزّع على الفواتير ({fmt(total)}) أكتر من مبلغ السند ({fmt(amount)}).")
        return [{"purchase_invoice_id": k, "amount": v} for k, v in out.items()]

    # ---- التوزيع على الفواتير (سند الصرف) ------------------------------------------------
    def _allocation_rows(self, v: dict[str, Any], ex: Executor | None = None) -> list[dict[str, Any]]:
        """سطور التوزيع النهائية: سعر الفاتورة والمبلغ بالجنيه بسعرها — وبتتأكد إن الفاتورة مرحّلة آجلة لنفس المورد
        وفيها عملة السند والمبلغ مايعدّيش المتبقي (المرحّل بس). لازم الفواتير تتقفل الأول لو ده وقت الترحيل."""
        allocations = v.get("allocations") or []
        if not allocations:
            return []
        currency = v["currency_code"]
        ids = [int(a["purchase_invoice_id"]) for a in allocations]
        dues = self.repository.dues_for(ids, currency, ex=ex)
        missing = [i for i in ids if i not in dues]
        if missing:
            inv = self.repository.invoice_labels(missing, ex=ex).get(missing[0])
            if inv is None:
                raise BusinessError("فاتورة في التوزيع مش موجودة (ممكن تكون اتمسحت) — دوس «تحديث».")
            label = invoice_label(inv)
            if inv["status"] != "posted":
                raise BusinessError(f"فاتورة الشراء {label} مش مرحّلة — مايتسددش عليها.")
            if inv["pay_kind"] != "credit":
                raise BusinessError(f"فاتورة الشراء {label} نقدية — اتسددت مع الفاتورة.")
            raise BusinessError(f"فاتورة الشراء {label} مافيهاش مبلغ بعملة {currency} — كل فاتورة بتتسدد بعملتها.")
        rows: list[dict[str, Any]] = []
        for a in allocations:
            d = dues[int(a["purchase_invoice_id"])]
            amount = Decimal(a["amount"])
            if int(d["supplier_id"]) != int(v["party_id"]):
                raise BusinessError(f"فاتورة الشراء {d['invoice_no']} لمورد تاني — السند بيتوزّع على فواتير مورده بس.")
            remaining = Decimal(d["remaining_amount"])
            if amount > remaining:
                raise BusinessError(f"فاتورة الشراء {d['invoice_no']}: المبلغ {fmt(amount)} أكتر من المتبقي عليها "
                                    f"({fmt(max(remaining, ZERO))} {currency}) — ممكن تكون اتسددت من جهاز تاني.")
            total, total_base = Decimal(d["total_amount"]), Decimal(d["total_base"])
            rate = (total_base / total).quantize(RATE, rounding=ROUND_HALF_UP) if total > 0 else Decimal("1")
            # آخر دفعة بتقفل الفاتورة بالظبط بالجنيه (من غير كسور تقريب متعلّقة على المورد).
            amount_base = (max(Decimal(d["remaining_base"]), ZERO) if amount == remaining
                           else _money2(amount * rate))
            rows.append({"purchase_invoice_id": int(d["invoice_id"]), "currency_code": currency, "amount": amount,
                         "invoice_rate": rate if rate > 0 else Decimal("1"), "amount_base": amount_base,
                         "invoice_no": d["invoice_no"]})
        return rows

    def open_invoices(self, supplier_id: Any, currency_code: str, keyword: str = "", page: int = 1,
                      page_size: int | None = PAGE_SIZE, order: str = "due") -> list[dict[str, Any]]:
        """فواتير المورد الآجلة المرحّلة اللي لسه عليها متبقي **بعملة السند** (بالتوتال) — الأقدم الأول."""
        sid = self._id(supplier_id)
        if sid is None or not currency_code:
            return []
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.open_dues(sid, str(currency_code).upper(), keyword,
                                         order if order in ALLOCATION_ORDERS else "due",
                                         limit=page_size, offset=offset)

    def count_open_invoices(self, supplier_id: Any, currency_code: str, keyword: str = "") -> dict[str, Any]:
        sid = self._id(supplier_id)
        if sid is None or not currency_code:
            return {"total": 0, "remaining": ZERO}
        row = self.repository.count_open_dues(sid, str(currency_code).upper(), keyword)
        return {"total": int(row["total"]), "remaining": Decimal(row["remaining"])}

    def auto_allocate(self, supplier_id: Any, currency_code: str, amount: Any, order: str = "due",
                      keep: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """«وزّع الأقدم الأول»: المبلغ بيتوزّع على فواتير المورد المفتوحة بعملة السند بالترتيب (الاستحقاق أو
        تاريخ الفاتورة) لحد ما يخلص. ``keep`` = توزيع موجود بيفضل زي ما هو والباقي بس اللي بيتوزّع."""
        left = parse_money(amount, "المبلغ")
        kept = {int(k["purchase_invoice_id"]): Decimal(str(k["amount"])) for k in (keep or [])
                if self._id(k.get("purchase_invoice_id")) and Decimal(str(k.get("amount") or 0)) > 0}
        left -= sum(kept.values(), ZERO)
        out = [{"purchase_invoice_id": k, "amount": v} for k, v in kept.items()]
        for d in self.open_invoices(supplier_id, currency_code, page_size=None, order=order):
            if left <= 0:
                break
            if int(d["invoice_id"]) in kept:
                continue
            take = min(left, Decimal(d["remaining_amount"]))
            if take > 0:
                out.append({"purchase_invoice_id": int(d["invoice_id"]), "amount": take})
                left -= take
        return out

    def allocation_details(self, supplier_id: Any, currency_code: str,
                           allocations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """بيانات الفواتير اللي في التوزيع للعرض تحت السند (الرقم · الاستحقاق · الإجمالي · المرتجع · المسدد · المتبقي)."""
        ids = [int(a["purchase_invoice_id"]) for a in allocations if self._id(a.get("purchase_invoice_id"))]
        dues = self.repository.dues_for(ids, str(currency_code or "").upper())
        labels = self.repository.invoice_labels([i for i in ids if i not in dues])
        out = []
        for a in allocations:
            iid = self._id(a.get("purchase_invoice_id"))
            if iid is None:
                continue
            d = dues.get(iid)
            row = {"purchase_invoice_id": iid, "amount": a.get("amount")}
            if d is None:
                inv = labels.get(iid) or {}
                row.update({"invoice_no": invoice_label(inv) if inv else "؟", "missing": True})
            else:
                row.update({k: d[k] for k in ("invoice_no", "invoice_date", "due_date", "supplier_invoice_no",
                                               "total_amount", "returned_amount", "paid_amount", "remaining_amount")})
            out.append(row)
        return out

    # ---- سطور القيد ---------------------------------------------------------------
    def _posting_lines(self, v: dict[str, Any], rows: list[dict[str, Any]] | None = None) -> list[PostingLine]:
        party = (self.kind.party, int(v["party_id"]))
        if self.is_receipt:
            source = (PostingLine("cash", v["amount"], treasury_id=v["treasury_id"])
                      if v["source_kind"] == "treasury"
                      else PostingLine("bank", v["amount"], bank_account_id=v["bank_account_id"]))
            return [source, PostingLine(self.kind.party_role, v["amount"], party=party)]
        # سند الصرف: المورد بيتقفل بسعر الفاتورة (والباقي غير الموزّع بسعر السند) · الخزنة بسعر السند · الفرق فروق عملة.
        currency, base = v["currency_code"], self.base_currency
        amount, rate = Decimal(v["amount"]), Decimal(v["fx_rate"])

        def fc(line_rate: Decimal, value: Decimal) -> dict[str, Any]:
            return {} if currency == base else {"currency_code": currency, "fx_rate": line_rate, "amount_fc": value}

        lines: list[PostingLine] = []
        closed = allocated = ZERO
        for r in rows or []:
            lines.append(PostingLine(self.kind.party_role, r["amount_base"], party=party,
                                     **fc(Decimal(r["invoice_rate"]), Decimal(r["amount"]))))
            closed += Decimal(r["amount_base"])
            allocated += Decimal(r["amount"])
        rest = amount - allocated
        if rest > 0:
            rest_base = _money2(rest * rate)
            lines.append(PostingLine(self.kind.party_role, rest_base, party=party, **fc(rate, rest)))
            closed += rest_base
        amount_base = _money2(amount * rate)
        lines.append(PostingLine("cash", amount_base, treasury_id=v["treasury_id"], **fc(rate, amount))
                     if v["source_kind"] == "treasury"
                     else PostingLine("bank", amount_base, bank_account_id=v["bank_account_id"], **fc(rate, amount)))
        diff = amount_base - closed
        if diff > 0:                                       # دفعنا جنيه أكتر من اللي متسجل على المورد ← خسارة
            lines.append(PostingLine("fx_loss", diff))
        elif diff < 0:
            lines.append(PostingLine("fx_gain", -diff))
        return lines

    # ---- معاينة القيد والأرصدة (للشاشة وهي بتكتب — من غير كتابة) -----------------------------
    def preview(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """سطور القيد اللي هيتعمل (الحساب · مدين · دائن) — أي نقص ← ``BusinessError`` بالعربي."""
        v = self._clean(data)
        rows = None if self.is_receipt else self._allocation_rows(v)
        lines = self.posting.build(self.kind.doc_type, self._posting_lines(v, rows), project_id=v["project_id"])
        names = self.repository.accounts([ln["account_id"] for ln in lines])
        return [{"account_id": ln["account_id"], "account_code": names[ln["account_id"]]["code"],
                 "account_name": names[ln["account_id"]]["name_ar"], "debit": ln["debit"], "credit": ln["credit"],
                 "role": ln["role"], "role_name": ln["role_name"], "currency_code": ln.get("currency_code"),
                 "fx_rate": ln.get("fx_rate"), "amount_fc": ln.get("amount_fc")} for ln in lines]

    def _source(self, source_kind: str, source_id: int | None) -> dict[str, Any] | None:
        if not source_id:
            return None
        rows = self.repository.treasury_choices() if source_kind == "treasury" else self.repository.bank_account_choices()
        return next((r for r in rows if r["id"] == int(source_id)), None)

    @staticmethod
    def _source_name(source: dict[str, Any]) -> str:
        return source.get("name_ar") or f"{source.get('bank_name')} — {source.get('code')}"

    def balances(self, party_id: Any, source_kind: str, source_id: Any, amount: Any,
                 posted: bool = False, currency_code: str | None = None, fx_rate: Any = None,
                 party_effect: Decimal | None = None) -> dict[str, Any]:
        """رصيد الطرف والخزنة / البنك **دلوقتي** وبعد السند (``posted`` = السند نفسه مرحّل وجوه الرصيد خلاص).

        رصيد الطرف بجنبه الطبيعي: العميل = عليه لينا كام (القبض بينقّصه) · المورد = ليه علينا كام (الصرف بينقّصه).

        **الأرصدة كلها بالعملة الأساسية (الجنيه) — نفس أرقام كشف الحساب ودفتر الأستاذ بالظبط** (طلب المستخدم
        2026-11-28: «عشان كله يكون مطابق»): ``party`` / ``source`` = مدين − دائن من سطور القيود المرحّلة بكل عملاتها.
        سند بعملة تانية: المبلغ بيتحوّل بسعره (``amount_base``) ومعاه **للعلم بس** رصيد المورد والخزنة بالعملة دي
        (``party_fc`` / ``source_fc``). ``party_effect`` = اللي هيتقفل من حساب المورد بالجنيه (من معاينة القيد —
        التوزيع بسعر الفواتير)؛ من غيره = المبلغ × سعر السند."""
        try:
            value = parse_money(amount, "المبلغ")
        except BusinessError:
            value = ZERO
        base = self.base_currency
        currency = base if self.is_receipt else str(currency_code or base).upper()
        foreign = currency != base
        rate = Decimal("1")
        if foreign:
            try:
                rate = Decimal(str(fx_rate).strip().replace(",", "")) if fx_rate not in (None, "") else ZERO
            except InvalidOperation:
                rate = ZERO
            if not rate.is_finite() or rate < 0:
                rate = ZERO
        value_base = _money2(value * rate)
        effect = ZERO if posted else value_base
        party_drop = ZERO if posted else (Decimal(party_effect) if party_effect is not None else value_base)
        out: dict[str, Any] = {"amount": value, "amount_base": value_base, "party": None, "party_after": None,
                               "source": None, "source_after": None, "available_after": None, "source_name": "",
                               "overdraft": None, "currency_code": currency, "foreign": foreign,
                               "party_fc": None, "party_fc_after": None, "source_fc": None, "source_fc_after": None}
        pid = self._id(party_id)
        if pid:
            bal = self.repository.party_balance(self.kind.code, pid)            # بالجنيه — زي كشف الحساب
            out["party"] = bal
            out["party_after"] = bal - party_drop
            if foreign:
                fc = self.repository.party_balance(self.kind.code, pid, currency=currency, base=False)
                out["party_fc"] = fc
                out["party_fc_after"] = fc - (ZERO if posted else value)
        source = self._source(source_kind, self._id(source_id))
        if source:
            bal = self.repository.account_balance(int(source["account_id"]))    # بالجنيه — زي دفتر الأستاذ
            after = bal + effect if self.is_receipt else bal - effect
            limit = _money2(Decimal(source.get("overdraft_limit") or 0) * (rate if foreign else Decimal("1")))
            out.update({"source": bal, "source_after": after, "available_after": after + limit,
                        "overdraft": limit or None, "source_name": self._source_name(source)})
            if foreign and source["currency_code"] == currency:
                fc = self.repository.account_balance(int(source["account_id"]), currency=currency, base=False)
                out["source_fc"] = fc
                out["source_fc_after"] = fc - (ZERO if posted else value)
        return out

    def funds_warning(self, data: dict[str, Any]) -> str | None:
        """سند الصرف أكبر من رصيد الخزنة / البنك (+ حد الجاري المدين) ← رسالة تحذير (مش منع)."""
        if self.is_receipt:
            return None
        v = self._clean(data)
        source_id = v["treasury_id"] or v["bank_account_id"]
        b = self.balances(v["party_id"], v["source_kind"], source_id, v["amount"], currency_code=v["currency_code"],
                          fx_rate=v["fx_rate"])
        if b["source"] is None or b["available_after"] >= 0:
            return None
        what = "الخزنة" if v["source_kind"] == "treasury" else "الحساب البنكي"
        extra = f" (+ حد الجاري المدين {fmt(b['overdraft'])})" if b["overdraft"] else ""
        note = f" ({fmt(v['amount'])} {v['currency_code']} × {v['fx_rate'].normalize():f})" if b["foreign"] else ""
        return (f"رصيد {what} «{b['source_name']}» {fmt(b['source'])}{extra} — والسند {fmt(b['amount_base'])}{note}، "
                f"فالرصيد هيبقى {fmt(b['source_after'])}.")

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, voucher_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self._load(int(voucher_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=self.kind.entity, entity_id=voucher_id)
        if before is None:
            raise BusinessError(f"{self.kind.short} ده مش موجود (ممكن يكون اتمسح).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — سنداتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"{self.kind.short} {voucher_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"{self.kind.short} {voucher_label(before)} مش مرحّل.")
        return before

    def _record(self, tx: Executor, action: str, voucher_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}

        def head(r: dict | None) -> dict | None:
            return {k: v for k, v in r.items() if k != "allocations"} if r else None

        lines = None
        if not self.is_receipt:
            lines = {"cash.voucher_allocations": {"before": (before or {}).get("allocations") or [],
                                                  "after": (after or {}).get("allocations") or []}}
        self.audit.record(tx, action=action, entity=self.kind.entity, entity_id=voucher_id,
                          entity_label=self.kind.title,
                          record_label=f"{voucher_label(row)} — {row.get('party_name') or ''} — "
                                       f"{fmt(Decimal(row.get('amount') or 0))}",
                          before=head(before), after=head(after), lines=lines)

    def _write_allocations(self, tx: Executor, voucher_id: int, v: dict[str, Any], *,
                           lock: bool = False) -> list[dict[str, Any]]:
        """بيكتب سطور التوزيع (بسعر الفاتورة والمبلغ بالجنيه) — ``lock`` = قفل الفواتير الأول (وقت الترحيل)."""
        if lock:
            self.repository.lock_invoices([int(a["purchase_invoice_id"]) for a in v.get("allocations") or []], tx)
        rows = self._allocation_rows(v, ex=tx)
        self.repository.replace_allocations(voucher_id, rows, SESSION.user_id, ex=tx)
        return rows

    def _post(self, tx: Executor, voucher_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """الحالة ← مرحّل (القاعدة بتصرف الرقم) ← القيد بمحرك الترحيل — كله في نفس الـ transaction."""
        rows = None
        if not self.is_receipt:
            # قفل الفواتير ← المتبقي من جديد ← سطور التوزيع النهائية (قبل ما السند يتقفل على التعديل).
            rows = self._write_allocations(tx, voucher_id, before, lock=True)
        updated = self.repository.set_status(self.kind.code, voucher_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=self.kind.entity, entity_id=voucher_id)
        v = self._load(voucher_id, ex=tx)
        self.posting.post_document(tx, doc_type=self.kind.doc_type, source_id=voucher_id,
                                   source_no=v["voucher_no"], entry_date=v["voucher_date"],
                                   description=f"{v['description']} — {v['party_name']}",
                                   reference=v["paper_no"], lines=self._posting_lines(v, rows),
                                   project_id=v["project_id"])
        self._record(tx, "post", voucher_id, before, v)
        return v

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, voucher_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة (ولو ``post`` يرحّلها في نفس الـ transaction — يا الاتنين يا ولا حاجة)."""
        perm = self.kind.perm
        PermissionService.require(f"{perm}.create" if voucher_id is None else f"{perm}.edit")
        PermissionService.require(f"{perm}.save")
        if post:
            PermissionService.require(f"{perm}.post")
        clean = self._clean(data)
        try:
            with self.db.atomic() as tx:
                before = None
                if voucher_id is None:
                    voucher_id = self.repository.insert_voucher(self.kind.code, {**clean, "user_id": SESSION.user_id},
                                                                ex=tx)
                else:
                    before = self._lock(tx, int(voucher_id), expected_version, draft_only=True)
                    if not self.is_receipt and before.get("allocations"):
                        # التوزيع القديم بيتشال الأول عشان المورد / العملة يتغيّروا من غير ما القاعدة ترفض.
                        self.repository.replace_allocations(int(voucher_id), [], SESSION.user_id, ex=tx)
                    updated = self.repository.update_voucher(self.kind.code, int(voucher_id), clean, ex=tx,
                                                             expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=self.kind.entity, entity_id=voucher_id)
                if not self.is_receipt:
                    self._write_allocations(tx, int(voucher_id), clean)
                after = self._load(int(voucher_id), ex=tx)
                self._record(tx, "create" if before is None else "update", int(voucher_id), before, after)
                if post:
                    self._post(tx, int(voucher_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(voucher_id)

    def post(self, voucher_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{self.kind.perm}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(voucher_id), expected_version, draft_only=True)
                after = self._post(tx, int(voucher_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["voucher_no"]

    def unpost(self, voucher_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): القيد بيتمسح والسند بيرجع مسودة برقمه (وتوزيعه بيبطّل يخصم من الفواتير)."""
        PermissionService.require(f"{self.kind.perm}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(voucher_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=self.kind.doc_type, source_id=int(voucher_id))
                updated = self.repository.set_status(self.kind.code, int(voucher_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=self.kind.entity, entity_id=voucher_id)
                after = self._load(int(voucher_id), ex=tx)
                self._record(tx, "unpost", int(voucher_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_voucher(self, voucher_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{self.kind.perm}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(voucher_id), expected_version, draft_only=True)
                if before["voucher_no"]:
                    raise BusinessError(f"{self.kind.short} {before['voucher_no']} خد رقم رسمي قبل كده — مايتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
                deleted = self.repository.delete_voucher(self.kind.code, int(voucher_id), ex=tx,
                                                         expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=self.kind.entity, entity_id=voucher_id)
                self._record(tx, "delete", int(voucher_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def entry_for(self, voucher_id: int) -> dict[str, Any] | None:
        """قيد السند المرحّل بسطوره (لمعاينة القيد في الشاشة)."""
        return self.posting.entry_for(self.kind.doc_type, int(voucher_id))
