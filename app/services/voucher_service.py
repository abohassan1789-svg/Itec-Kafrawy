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
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
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
SOURCE_KINDS = ("treasury", "bank")


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


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


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

    @property
    def is_receipt(self) -> bool:
        return self.kind.code == "receipt"

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

    def get_voucher(self, voucher_id: int) -> dict[str, Any] | None:
        row = self.repository.get_voucher(self.kind.code, int(voucher_id))
        if row is not None:
            row["label"] = voucher_label(row)
        return row

    def choices(self) -> dict[str, Any]:
        """العملاء (القبض) أو الموردين (الصرف) · الخزائن · الحسابات البنكية · أوامر التوريد
        (الموقوف بيرجع عشان السند القديم يتعرض)."""
        return {"parties": self.repository.party_choices(self.kind.code),
                "treasuries": self.repository.treasury_choices(),
                "banks": self.repository.bank_account_choices(),
                "projects": self.repository.project_choices()}

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
        return {"voucher_date": voucher_date, "party_id": party_id, "source_kind": source_kind,
                "treasury_id": treasury_id, "bank_account_id": bank_account_id, "amount": amount,
                "description": description, "paper_no": paper_no, "project_id": self._id(data.get("project_id"))}

    def _posting_lines(self, v: dict[str, Any]) -> list[PostingLine]:
        source = (PostingLine("cash", v["amount"], treasury_id=v["treasury_id"]) if v["source_kind"] == "treasury"
                  else PostingLine("bank", v["amount"], bank_account_id=v["bank_account_id"]))
        party = PostingLine(self.kind.party_role, v["amount"], party=(self.kind.party, int(v["party_id"])))
        return [source, party] if self.is_receipt else [party, source]

    # ---- معاينة القيد والأرصدة (للشاشة وهي بتكتب — من غير كتابة) -----------------------------
    def preview(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """سطور القيد اللي هيتعمل (الحساب · مدين · دائن) — أي نقص ← ``BusinessError`` بالعربي."""
        v = self._clean(data)
        lines = self.posting.build(self.kind.doc_type, self._posting_lines(v), project_id=v["project_id"])
        names = self.repository.accounts([ln["account_id"] for ln in lines])
        return [{"account_id": ln["account_id"], "account_code": names[ln["account_id"]]["code"],
                 "account_name": names[ln["account_id"]]["name_ar"], "debit": ln["debit"], "credit": ln["credit"],
                 "role_name": ln["role_name"]} for ln in lines]

    def _source(self, source_kind: str, source_id: int | None) -> dict[str, Any] | None:
        if not source_id:
            return None
        rows = self.repository.treasury_choices() if source_kind == "treasury" else self.repository.bank_account_choices()
        return next((r for r in rows if r["id"] == int(source_id)), None)

    def balances(self, party_id: Any, source_kind: str, source_id: Any, amount: Any,
                 posted: bool = False) -> dict[str, Any]:
        """رصيد الطرف والخزنة / البنك **دلوقتي** وبعد السند (``posted`` = السند نفسه مرحّل وجوه الرصيد خلاص).

        رصيد الطرف بجنبه الطبيعي: العميل = عليه لينا كام (القبض بينقّصه) · المورد = ليه علينا كام (الصرف بينقّصه)."""
        try:
            value = parse_money(amount, "المبلغ")
        except BusinessError:
            value = ZERO
        effect = ZERO if posted else value
        out: dict[str, Any] = {"amount": value, "party": None, "party_after": None, "source": None,
                               "source_after": None, "available_after": None, "source_name": "", "overdraft": None}
        pid = self._id(party_id)
        if pid:
            bal = self.repository.party_balance(self.kind.code, pid)
            out["party"] = bal
            out["party_after"] = bal - effect
        source = self._source(source_kind, self._id(source_id))
        if source:
            bal = self.repository.account_balance(int(source["account_id"]))
            after = bal + effect if self.is_receipt else bal - effect
            limit = Decimal(source.get("overdraft_limit") or 0)
            out.update({"source": bal, "source_after": after, "available_after": after + limit,
                        "overdraft": limit or None,
                        "source_name": source.get("name_ar") or f"{source.get('bank_name')} — {source.get('code')}"})
        return out

    def funds_warning(self, data: dict[str, Any]) -> str | None:
        """سند الصرف أكبر من رصيد الخزنة / البنك (+ حد الجاري المدين) ← رسالة تحذير (مش منع)."""
        if self.is_receipt:
            return None
        v = self._clean(data)
        source_id = v["treasury_id"] or v["bank_account_id"]
        b = self.balances(v["party_id"], v["source_kind"], source_id, v["amount"])
        if b["source"] is None or b["available_after"] >= 0:
            return None
        what = "الخزنة" if v["source_kind"] == "treasury" else "الحساب البنكي"
        extra = f" (+ حد الجاري المدين {fmt(b['overdraft'])})" if b["overdraft"] else ""
        return (f"رصيد {what} «{b['source_name']}» {fmt(b['source'])}{extra} — والسند {fmt(v['amount'])}، "
                f"فالرصيد هيبقى {fmt(b['source_after'])}.")

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, voucher_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_voucher(self.kind.code, int(voucher_id), ex=tx, for_update=True)
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
        self.audit.record(tx, action=action, entity=self.kind.entity, entity_id=voucher_id,
                          entity_label=self.kind.title,
                          record_label=f"{voucher_label(row)} — {row.get('party_name') or ''} — "
                                       f"{fmt(Decimal(row.get('amount') or 0))}",
                          before=before, after=after)

    def _post(self, tx: Executor, voucher_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """الحالة ← مرحّل (القاعدة بتصرف الرقم) ← القيد بمحرك الترحيل — كله في نفس الـ transaction."""
        updated = self.repository.set_status(self.kind.code, voucher_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=self.kind.entity, entity_id=voucher_id)
        v = self.repository.get_voucher(self.kind.code, voucher_id, ex=tx)
        self.posting.post_document(tx, doc_type=self.kind.doc_type, source_id=voucher_id,
                                   source_no=v["voucher_no"], entry_date=v["voucher_date"],
                                   description=f"{v['description']} — {v['party_name']}",
                                   reference=v["paper_no"], lines=self._posting_lines(v),
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
                    updated = self.repository.update_voucher(self.kind.code, int(voucher_id), clean, ex=tx,
                                                             expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=self.kind.entity, entity_id=voucher_id)
                after = self.repository.get_voucher(self.kind.code, int(voucher_id), ex=tx)
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
        """إلغاء الترحيل (بالصلاحية بس): القيد بيتمسح والسند بيرجع مسودة برقمه."""
        PermissionService.require(f"{self.kind.perm}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(voucher_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=self.kind.doc_type, source_id=int(voucher_id))
                updated = self.repository.set_status(self.kind.code, int(voucher_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=self.kind.entity, entity_id=voucher_id)
                after = self.repository.get_voucher(self.kind.code, int(voucher_id), ex=tx)
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
