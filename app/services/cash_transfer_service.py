"""«حركات الخزائن والبنوك» — المرحلة 5، النموذج 2 «تبويب لكل نوع حركة» (اختيار المستخدم 2026-11-17 + ردوده).

* الحركة = نقل فلوس بين الخزائن والبنوك بتوعنا: **من** (خزنة / حساب بنك) ← **إلى** (خزنة / حساب بنك) والنوع
  بيطلع لوحده: ``deposit`` إيداع في البنك · ``withdraw`` سحب من البنك · ``t2t`` بين خزنتين · ``b2b`` بين بنكين.
  رد المستخدم: «ممكن نحوّل من خزنة إلى بنك أو من بنك إلى خزنة» · **مفيش «إضافة أموال للخزينة»** خالص.
* **مصاريف بنكية** اختيارية في نفس الحركة ← على «مصاريف بنكية» في الحسابات الافتراضية، وبتخرج من «من» زيادة.
* **«حفظ مسودة»** · **«ترحيل»** (أو حفظ + ترحيل في نفس الـ transaction) ← القاعدة بتصرف رقم ``CT-2026-00001``
  **أول مرة بس** جوه قفل العدّاد، و**محرك الترحيل** بيعمل القيد ويرحّله في نفس الـ transaction:
  مدين «إلى» بالمبلغ · مدين مصاريف بنكية · دائن «من» بالمبلغ + المصاريف.
* **«إلغاء الترحيل»** بالصلاحية في السنة المفتوحة: القيد بيتمسح والحركة ترجع مسودة **برقمها** وتتعدّل.
* الحذف للمسودة اللي **ماخدتش رقم** بس · كل حاجة في ``sec.audit_log`` قبل/بعد + ``row_version``.
* **الرصيد مش مكفي** في «من»: ``funds_warning`` بيرجّع رسالة **تحذير بس** (رد المستخدم) — حد الجاري المدين بيتحسب.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.cash_transfer_repository import STATUSES, TYPES, CashTransferRepository
from app.repositories.voucher_repository import VoucherRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.journal_service import parse_money
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService

PAGE_SIZE = 50
ZERO = Decimal("0")
DOC_TYPE = "cash_transfer"
ENTITY, ENTITY_LABEL = "cash.cash_transfers", "حركة خزائن وبنوك"
PERM = "cash.cash_transfers"
KINDS = ("treasury", "bank")
# النوع: (الاسم، اللون، «من»، «إلى»)
TRANSFER_TYPES: dict[str, tuple[str, str, str, str]] = {
    "deposit": ("إيداع في البنك", "#2563EB", "treasury", "bank"),
    "withdraw": ("سحب من البنك", "#B45309", "bank", "treasury"),
    "t2t": ("تحويل بين خزنتين", "#137A38", "treasury", "treasury"),
    "b2b": ("تحويل بين بنكين", "#6D28D9", "bank", "bank"),
}


def type_of(from_kind: str, to_kind: str) -> str:
    """نوع الحركة من «من» و«إلى» (زي القاعدة بالظبط)."""
    if from_kind == "treasury":
        return "deposit" if to_kind == "bank" else "t2t"
    return "withdraw" if to_kind == "treasury" else "b2b"


def transfer_label(row: dict[str, Any]) -> str:
    """«CT-2026-00012» أو «مسودة 7»."""
    return row.get("transfer_no") or f"مسودة {row.get('draft_no')}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


class CashTransferService:
    def __init__(self, db: Database | None = None, repository: CashTransferRepository | None = None,
                 cash: VoucherRepository | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or CashTransferRepository(self.db)
        self.cash = cash or VoucherRepository(self.db)          # الخزائن والبنوك وأرصدتها (نفس دوال السندات)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def _filters(status: str, transfer_type: str | None) -> tuple[str, str | None]:
        return (status if status in STATUSES else "all"), (transfer_type if transfer_type in TYPES else None)

    def list_transfers(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                       keyword: str = "", transfer_type: str | None = None, page: int = 1,
                       page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status, transfer_type = self._filters(status, transfer_type)
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_transfers(date_from, date_to, status, keyword.strip(), transfer_type,
                                              limit=page_size, offset=offset)

    def count_transfers(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                        keyword: str = "", transfer_type: str | None = None) -> dict[str, Any]:
        status, transfer_type = self._filters(status, transfer_type)
        row = self.repository.count_transfers(date_from, date_to, status, keyword.strip(), transfer_type)
        types = self.repository.type_counts(date_from, date_to, status, keyword.strip())
        return {"total": int(row["total"]), "drafts": int(row["drafts"]), "posted": int(row["posted"]),
                "posted_amount": Decimal(row["posted_amount"]),
                "types": {k: types.get(k, 0) for k in TYPES}, "all": sum(types.values())}

    def get_transfer(self, transfer_id: int) -> dict[str, Any] | None:
        row = self.repository.get_transfer(int(transfer_id))
        if row is not None:
            row["label"] = transfer_label(row)
        return row

    def choices(self) -> dict[str, Any]:
        """الخزائن والحسابات البنكية (الموقوف بيرجع عشان الحركة القديمة تتعرض)."""
        return {"treasuries": self.cash.treasury_choices(), "banks": self.cash.bank_account_choices()}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    def _end(self, data: dict[str, Any], side: str, label: str) -> tuple[str, int | None, int | None]:
        kind = data.get(f"{side}_kind") or "treasury"
        if kind not in KINDS:
            raise BusinessError(f"«{label}»: اختار خزنة أو بنك.")
        treasury_id = self._id(data.get(f"{side}_treasury_id")) if kind == "treasury" else None
        bank_id = self._id(data.get(f"{side}_bank_account_id")) if kind == "bank" else None
        if kind == "treasury" and treasury_id is None:
            raise BusinessError(f"«{label}»: اختار الخزنة.")
        if kind == "bank" and bank_id is None:
            raise BusinessError(f"«{label}»: اختار الحساب البنكي.")
        return kind, treasury_id, bank_id

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        transfer_date = data.get("transfer_date")
        if isinstance(transfer_date, dt.datetime):
            transfer_date = transfer_date.date()
        if not isinstance(transfer_date, dt.date):
            raise BusinessError("تاريخ الحركة مطلوب.")
        from_kind, from_treasury, from_bank = self._end(data, "from", "من")
        to_kind, to_treasury, to_bank = self._end(data, "to", "إلى")
        if (from_kind, from_treasury, from_bank) == (to_kind, to_treasury, to_bank):
            raise BusinessError("«من» و«إلى» نفس الحساب — اختار خزنة أو بنك تاني.")
        amount = parse_money(data.get("amount"), "المبلغ")
        if amount <= 0:
            raise BusinessError("اكتب مبلغ الحركة.")
        fees = parse_money(data.get("fees") or "0", "المصاريف البنكية")
        if fees < 0:
            raise BusinessError("المصاريف البنكية مينفعش تبقى بالسالب.")
        description = " ".join(str(data.get("description") or "").split())
        if not description:
            raise BusinessError("البيان مطلوب (الحركة دي عن إيه).")
        if len(description) > 500:
            raise BusinessError("«البيان» أطول من المسموح (500 حرف).")
        reference_no = " ".join(str(data.get("reference_no") or "").split()) or None
        if reference_no and len(reference_no) > 40:
            raise BusinessError("«رقم الإيصال» أطول من المسموح (40 حرف).")
        return {"transfer_date": transfer_date, "from_kind": from_kind, "from_treasury_id": from_treasury,
                "from_bank_account_id": from_bank, "to_kind": to_kind, "to_treasury_id": to_treasury,
                "to_bank_account_id": to_bank, "amount": amount, "fees": fees, "description": description,
                "reference_no": reference_no, "transfer_type": type_of(from_kind, to_kind)}

    @staticmethod
    def _posting_lines(v: dict[str, Any]) -> list[PostingLine]:
        amount, fees = Decimal(v["amount"]), Decimal(v.get("fees") or 0)
        to = (PostingLine("to_cash", amount, treasury_id=v["to_treasury_id"]) if v["to_kind"] == "treasury"
              else PostingLine("to_bank", amount, bank_account_id=v["to_bank_account_id"]))
        role = "from_cash" if v["from_kind"] == "treasury" else "from_bank"
        ids = {"treasury_id": v["from_treasury_id"]} if v["from_kind"] == "treasury" else \
            {"bank_account_id": v["from_bank_account_id"]}
        lines = [to]
        if fees:
            lines.append(PostingLine("fees", fees))
        lines.append(PostingLine(role, amount + fees, **ids))
        return lines

    # ---- معاينة القيد والأرصدة (للشاشة وهي بتكتب — من غير كتابة) -----------------------------
    def preview(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """سطور القيد اللي هيتعمل (الحساب · مدين · دائن) — أي نقص ← ``BusinessError`` بالعربي."""
        v = self._clean(data)
        lines = self.posting.build(DOC_TYPE, self._posting_lines(v))
        names = self.cash.accounts([ln["account_id"] for ln in lines])
        return [{"account_id": ln["account_id"], "account_code": names[ln["account_id"]]["code"],
                 "account_name": names[ln["account_id"]]["name_ar"], "debit": ln["debit"], "credit": ln["credit"],
                 "role_name": ln["role_name"]} for ln in lines]

    def _endpoint(self, kind: str, endpoint_id: int | None) -> dict[str, Any] | None:
        if not endpoint_id:
            return None
        rows = self.cash.treasury_choices() if kind == "treasury" else self.cash.bank_account_choices()
        return next((r for r in rows if r["id"] == int(endpoint_id)), None)

    def balances(self, from_kind: str, from_id: Any, to_kind: str, to_id: Any, amount: Any, fees: Any = 0,
                 posted: bool = False) -> dict[str, Any]:
        """رصيد «من» و«إلى» **دلوقتي** وبعد الحركة (``posted`` = الحركة نفسها مرحّلة وجوه الرصيد خلاص)."""
        def num(value: Any, label: str) -> Decimal:
            try:
                return parse_money(value or "0", label)
            except BusinessError:
                return ZERO

        value, extra = num(amount, "المبلغ"), num(fees, "المصاريف")
        out_effect = ZERO if posted else value + extra
        in_effect = ZERO if posted else value
        out: dict[str, Any] = {"amount": value, "fees": extra}
        for side, kind, endpoint_id, effect in (("from", from_kind, from_id, -out_effect),
                                                ("to", to_kind, to_id, in_effect)):
            row = self._endpoint(kind, self._id(endpoint_id))
            if row is None:
                out.update({side: None, f"{side}_after": None, f"{side}_available": None,
                            f"{side}_name": "", f"{side}_overdraft": None})
                continue
            bal = self.cash.account_balance(int(row["account_id"]))
            limit = Decimal(row.get("overdraft_limit") or 0)
            out.update({side: bal, f"{side}_after": bal + effect, f"{side}_available": bal + effect + limit,
                        f"{side}_overdraft": limit or None,
                        f"{side}_name": row.get("name_ar") or f"{row.get('bank_name')} — {row.get('code')}"})
        return out

    def funds_warning(self, data: dict[str, Any]) -> str | None:
        """«من» مش مكفي (+ حد الجاري المدين) ← رسالة تحذير (مش منع — رد المستخدم)."""
        v = self._clean(data)
        b = self.balances(v["from_kind"], v["from_treasury_id"] or v["from_bank_account_id"], v["to_kind"],
                          v["to_treasury_id"] or v["to_bank_account_id"], v["amount"], v["fees"])
        if b["from"] is None or b["from_available"] >= 0:
            return None
        what = "الخزنة" if v["from_kind"] == "treasury" else "الحساب البنكي"
        extra = f" (+ حد الجاري المدين {fmt(b['from_overdraft'])})" if b["from_overdraft"] else ""
        out = v["amount"] + v["fees"]
        return (f"رصيد {what} «{b['from_name']}» {fmt(b['from'])}{extra} — والحركة {fmt(out)}، "
                f"فالرصيد هيبقى {fmt(b['from_after'])}.")

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, transfer_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_transfer(int(transfer_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=transfer_id)
        if before is None:
            raise BusinessError("الحركة دي مش موجودة (ممكن تكون اتمسحت).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — حركاتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"الحركة {transfer_label(before)} مرحّلة — الغي ترحيلها الأول عشان تعدّلها.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"الحركة {transfer_label(before)} مش مرحّلة.")
        return before

    def _record(self, tx: Executor, action: str, transfer_id: int, before: dict | None, after: dict | None) -> None:
        row = after or before or {}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=transfer_id, entity_label=ENTITY_LABEL,
                          record_label=f"{transfer_label(row)} — {row.get('from_name') or ''} ← "
                                       f"{row.get('to_name') or ''} — {fmt(Decimal(row.get('amount') or 0))}",
                          before=before, after=after)

    def _post(self, tx: Executor, transfer_id: int, before: dict[str, Any],
              expected_version: int | None = None) -> dict[str, Any]:
        """الحالة ← مرحّلة (القاعدة بتصرف الرقم) ← القيد بمحرك الترحيل — كله في نفس الـ transaction."""
        updated = self.repository.set_status(transfer_id, "posted", SESSION.user_id, ex=tx,
                                             expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=transfer_id)
        v = self.repository.get_transfer(transfer_id, ex=tx)
        self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=transfer_id, source_no=v["transfer_no"],
                                   entry_date=v["transfer_date"],
                                   description=f"{v['description']} — {v['from_name']} ← {v['to_name']}",
                                   reference=v["reference_no"], lines=self._posting_lines(v))
        self._record(tx, "post", transfer_id, before, v)
        return v

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, transfer_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة (ولو ``post`` يرحّلها في نفس الـ transaction — يا الاتنين يا ولا حاجة)."""
        PermissionService.require(f"{PERM}.create" if transfer_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        clean = self._clean(data)
        try:
            with self.db.atomic() as tx:
                before = None
                if transfer_id is None:
                    transfer_id = self.repository.insert_transfer({**clean, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock(tx, int(transfer_id), expected_version, draft_only=True)
                    updated = self.repository.update_transfer(int(transfer_id), clean, ex=tx,
                                                              expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=transfer_id)
                after = self.repository.get_transfer(int(transfer_id), ex=tx)
                self._record(tx, "create" if before is None else "update", int(transfer_id), before, after)
                if post:
                    self._post(tx, int(transfer_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(transfer_id)

    def post(self, transfer_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(transfer_id), expected_version, draft_only=True)
                after = self._post(tx, int(transfer_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["transfer_no"]

    def unpost(self, transfer_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): القيد بيتمسح والحركة بترجع مسودة برقمها."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(transfer_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=int(transfer_id))
                updated = self.repository.set_status(int(transfer_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=transfer_id)
                after = self.repository.get_transfer(int(transfer_id), ex=tx)
                self._record(tx, "unpost", int(transfer_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_transfer(self, transfer_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(transfer_id), expected_version, draft_only=True)
                if before["transfer_no"]:
                    raise BusinessError(f"الحركة {before['transfer_no']} خدت رقم رسمي قبل كده — ماتتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).")
                deleted = self.repository.delete_transfer(int(transfer_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=transfer_id)
                self._record(tx, "delete", int(transfer_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def entry_for(self, transfer_id: int) -> dict[str, Any] | None:
        """قيد الحركة المرحّلة بسطوره (لمعاينة القيد في الشاشة)."""
        return self.posting.entry_for(DOC_TYPE, int(transfer_id))
