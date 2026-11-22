"""«حركات الشركاء» — المرحلة 5، النموذج 1 «الكلاسيك» (اختيار المستخدم 2026-11-22). مكانها «الحسابات» ← «حقوق الملكية».

شاشة واحدة بأربع أنواع:

* **إيداع شريك** (``deposit`` — PD): مدين الخزنة / البنك / دائن 32 جاري الشركاء (الشريك).
* **سحب شريك** (``withdrawal`` — PW): مدين 32 (الشريك) / دائن الخزنة / البنك — أكبر من رصيد جاريه ← **تحذير بس**
  ويبقى «عليه» (رد المستخدم) · ومسموح للشريك اللي خرج (ياخد رصيده).
* **زيادة رأس المال** (``capital`` — PC): مدين الخزنة / البنك **أو** 32 لو «تحويل من جاري الشريك» / دائن 31
  رأس المال (الشريك) — المدفوع أكبر من المكتتب ← تحذير بس.
* **توزيع أرباح / خسارة** (``distribution`` — PF): الربح المتاح = «الأرباح المحتجزة» + ربح السنة من القيود ·
  يوزّع كله أو جزء · **قبل إقفال السنة عادي** · النصيب بنسبة الربح (أو نسبة الخسارة) للشركاء النشطين ومجموعها
  لازم 100% · الأنصبة بتتحسب **وقت الترحيل** جوه قفل التوزيع وقفل صفوف الشركاء (جهازين مايوزّعوش نفس الربح مرتين).

مسودة + ترحيل + إلغاء ترحيل بالصلاحية زي السندات · الرقم الرسمي مع أول ترحيل · العملة الأساسية بس.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.partner_transaction_repository import STATUSES, PartnerTransactionRepository
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
CENT = Decimal("0.01")
PERM = "acc.partner_transactions"
# النوع ← (الاسم، نوع المستند في القيود)
KINDS = {"deposit": ("إيداع شريك", "partner_dep"), "withdrawal": ("سحب شريك", "partner_wd"),
         "capital": ("زيادة رأس المال", "partner_cap"), "distribution": ("توزيع أرباح / خسارة", "profit_dist")}
DIST_KINDS = {"profit": "توزيع ربح", "loss": "تحميل خسارة"}
SOURCE_KINDS = ("treasury", "bank", "current")
TXN_ENTITY, DIST_ENTITY = "cash.partner_transactions", "acc.profit_distributions"


def doc_label(row: dict[str, Any]) -> str:
    return row.get("txn_no") or row.get("dist_no") or row.get("doc_no") or f"مسودة {row.get('draft_no')}"


def fmt(amount: Any) -> str:
    return f"{Decimal(amount or 0):,.2f}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def _id(value: Any) -> int | None:
    return int(value) if value not in (None, "", 0) else None


def _text(value: Any, label: str, limit: int) -> str | None:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        raise BusinessError(f"«{label}» أطول من المسموح ({limit} حرف).")
    return text or None


def _date(value: Any, label: str) -> dt.date:
    if isinstance(value, dt.datetime):
        value = value.date()
    if not isinstance(value, dt.date):
        raise BusinessError(f"{label} مطلوب.")
    return value


def split_shares(amount: Decimal, partners: list[dict[str, Any]], dist_kind: str) -> list[dict[str, Any]]:
    """المبلغ على الشركاء بنسبهم (نسبة الربح أو الخسارة) — القرش الزيادة من التقريب لآخر شريك.

    النسب لازم مجموعها 100% (قرار شاشة الشركاء: المنع وقت التوزيع).
    """
    key = "profit_percent" if dist_kind == "profit" else "loss_percent"
    rows = [p for p in partners if Decimal(p[key] or 0) > 0]
    total_pct = sum((Decimal(p[key]) for p in rows), ZERO)
    what = "الربح" if dist_kind == "profit" else "الخسارة"
    if not rows:
        raise BusinessError(f"مفيش شركاء نشطين ليهم نسبة {what} — حدد النسب من شاشة الشركاء.")
    if total_pct != 100:
        raise BusinessError(f"مجموع نسب {what} للشركاء النشطين {total_pct.normalize():f}% مش 100% — "
                            "التوزيع ممنوع لحد ما النسب تتظبط من شاشة الشركاء.")
    amount = Decimal(amount)
    out, left = [], amount
    for i, p in enumerate(rows):
        pct = Decimal(p[key])
        share = left if i == len(rows) - 1 else (amount * pct / 100).quantize(CENT, ROUND_HALF_UP)
        left -= share
        out.append({"partner_id": int(p["id"]), "partner_code": p["code"], "partner_name": p["name_ar"],
                    "share_percent": pct, "amount": share})
    return out


class PartnerTransactionService:
    def __init__(self, db: Database | None = None, repository: PartnerTransactionRepository | None = None,
                 cash: VoucherRepository | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or PartnerTransactionRepository(self.db)
        self.cash = cash or VoucherRepository(self.db)          # الخزائن والبنوك وأرصدتها (نفس دوال السندات)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- الاختيارات والملخصات ------------------------------------------------------------
    def choices(self) -> dict[str, Any]:
        return {"partners": self.repository.partner_choices(), "treasuries": self.cash.treasury_choices(),
                "banks": self.cash.bank_account_choices()}

    def partner_summary(self, partner_id: Any, on: dt.date | None = None) -> dict[str, Any] | None:
        """كارت الشريك: رأس المال المدفوع من المكتتب · رصيد الجاري · إيداعات ومسحوبات السنة."""
        partner_id = _id(partner_id)
        if partner_id is None:
            return None
        partner = next((p for p in self.repository.partner_choices() if p["id"] == partner_id), None)
        if partner is None:
            return None
        balances = self.repository.partner_balances(partner_id)
        return {**partner, **balances, "year": self.repository.partner_year_totals(partner_id, on or dt.date.today())}

    def available_profit(self, on: dt.date, ex: Executor | None = None) -> dict[str, Decimal]:
        row = self.repository.available_profit(on, ex=ex)
        return {**row, "available": row["retained"] + row["income"]}

    def distribution_preview(self, dist_kind: str, amount: Any, on: dt.date) -> dict[str, Any]:
        """الربح المتاح + نصيب كل شريك (أو رسالة الغلط — النسب مش 100%)."""
        out: dict[str, Any] = {**self.available_profit(on), "shares": [], "error": None}
        try:
            value = parse_money(amount, "المبلغ") if str(amount or "").strip() else ZERO
            out["shares"] = split_shares(value, self.repository.share_partners(self.db), dist_kind)
        except BusinessError as exc:
            out["error"] = str(exc)
        return out

    # ---- القوائم والمستندات -------------------------------------------------------------
    @staticmethod
    def _kind(kind: str) -> str:
        if kind not in KINDS:
            raise BusinessError("نوع الحركة لازم إيداع أو سحب أو زيادة رأس مال أو توزيع.")
        return kind

    def list_docs(self, kind: str, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                  keyword: str = "", partner_id: int | None = None, page: int = 1,
                  page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_docs(self._kind(kind), date_from, date_to, status, keyword.strip(),
                                         _id(partner_id), limit=page_size, offset=offset)

    def count_docs(self, kind: str, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                   keyword: str = "", partner_id: int | None = None) -> dict[str, Any]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_docs(self._kind(kind), date_from, date_to, status, keyword.strip(), _id(partner_id))
        return {"total": int(row["total"]), "drafts": int(row["drafts"]), "posted": int(row["posted"]),
                "posted_amount": Decimal(row["posted_amount"])}

    def get_txn(self, txn_id: int) -> dict[str, Any] | None:
        row = self.repository.get_txn(int(txn_id))
        if row is not None:
            row["label"] = doc_label(row)
        return row

    def get_distribution(self, dist_id: int) -> dict[str, Any] | None:
        row = self.repository.get_dist(int(dist_id))
        if row is not None:
            row["label"] = doc_label(row)
            row["lines"] = self.repository.dist_lines(int(dist_id))
        return row

    def entry_for(self, kind: str, doc_id: int, ex: Executor | None = None) -> dict[str, Any] | None:
        if kind == "distribution":
            return self.posting.entry_for("profit_dist", int(doc_id))
        row = self.repository.get_txn(int(doc_id), ex=ex)
        return self.posting.entry_for(KINDS[row["txn_kind"]][1], int(doc_id)) if row else None

    def _names(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.cash.accounts([ln["account_id"] for ln in lines])
        return [{"account_id": ln["account_id"], "account_code": names[ln["account_id"]]["code"],
                 "account_name": names[ln["account_id"]]["name_ar"], "debit": ln["debit"], "credit": ln["credit"],
                 "role_name": ln["role_name"], "description": ln["description"]} for ln in lines]

    def _record(self, tx: Executor, kind: str, action: str, doc_id: int, before: dict | None, after: dict | None,
                lines: dict | None = None) -> None:
        row = after or before or {}
        who = row.get("partner_name") or DIST_KINDS.get(row.get("dist_kind") or "", "")
        self.audit.record(tx, action=action, entity=DIST_ENTITY if kind == "distribution" else TXN_ENTITY,
                          entity_id=doc_id, entity_label=KINDS[kind][0],
                          record_label=f"{doc_label(row)} — {who} — {fmt(row.get('amount'))}",
                          before=before, after=after, lines=lines)

    # ==== الإيداع / السحب / زيادة رأس المال ==================================================
    def _clean_txn(self, data: dict[str, Any]) -> dict[str, Any]:
        kind = data.get("txn_kind")
        if kind not in ("deposit", "withdrawal", "capital"):
            raise BusinessError("نوع الحركة لازم إيداع أو سحب أو زيادة رأس مال.")
        txn_date = _date(data.get("txn_date"), "التاريخ")
        partner_id = _id(data.get("partner_id"))
        if partner_id is None:
            raise BusinessError("اختار الشريك.")
        source_kind = data.get("source_kind") or "treasury"
        if source_kind not in SOURCE_KINDS or (source_kind == "current" and kind != "capital"):
            raise BusinessError("اختار خزنة أو بنك.")
        treasury_id = _id(data.get("treasury_id")) if source_kind == "treasury" else None
        bank_id = _id(data.get("bank_account_id")) if source_kind == "bank" else None
        if source_kind == "treasury" and treasury_id is None:
            raise BusinessError("اختار الخزنة.")
        if source_kind == "bank" and bank_id is None:
            raise BusinessError("اختار الحساب البنكي.")
        amount = parse_money(data.get("amount"), "المبلغ")
        if amount <= 0:
            raise BusinessError("اكتب المبلغ.")
        return {"txn_kind": kind, "txn_date": txn_date, "partner_id": partner_id, "source_kind": source_kind,
                "treasury_id": treasury_id, "bank_account_id": bank_id, "amount": amount,
                "description": _text(data.get("description"), "البيان", 500) or KINDS[kind][0],
                "reference_no": _text(data.get("reference_no"), "رقم الإيصال", 40)}

    @staticmethod
    def _txn_lines(v: dict[str, Any]) -> list[PostingLine]:
        amount = Decimal(v["amount"])
        partner = ("partner", int(v["partner_id"]))
        if v["source_kind"] == "treasury":
            money_line = PostingLine("cash", amount, treasury_id=v["treasury_id"])
        elif v["source_kind"] == "bank":
            money_line = PostingLine("bank", amount, bank_account_id=v["bank_account_id"])
        else:
            money_line = PostingLine("current", amount, party=partner, description="تحويل من الجاري لرأس المال")
        if v["txn_kind"] == "deposit":
            return [money_line, PostingLine("current", amount, party=partner)]
        if v["txn_kind"] == "withdrawal":
            return [PostingLine("current", amount, party=partner), money_line]
        return [money_line, PostingLine("capital", amount, party=partner)]

    def preview_txn(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """سطور القيد اللي هيتعمل — أي نقص ← ``BusinessError`` بالعربي."""
        v = self._clean_txn(data)
        return self._names(self.posting.build(KINDS[v["txn_kind"]][1], self._txn_lines(v)))

    def source_balance(self, kind: str, source_id: Any) -> dict[str, Any] | None:
        source_id = _id(source_id)
        if source_id is None or kind not in ("treasury", "bank"):
            return None
        rows = self.cash.treasury_choices() if kind == "treasury" else self.cash.bank_account_choices()
        row = next((r for r in rows if r["id"] == source_id), None)
        if row is None:
            return None
        return {"name": row.get("name_ar") or f"{row.get('bank_name')} — {row.get('code')}",
                "balance": self.cash.account_balance(int(row["account_id"])),
                "overdraft": Decimal(row.get("overdraft_limit") or 0)}

    def txn_warnings(self, data: dict[str, Any]) -> list[str]:
        """تحذيرات بس (الترحيل مسموح): السحب أكبر من الجاري · الخزنة مش مكفية · المدفوع أكبر من المكتتب."""
        v = self._clean_txn(data)
        out: list[str] = []
        amount = Decimal(v["amount"])
        summary = self.partner_summary(v["partner_id"], v["txn_date"]) or {}
        current = Decimal(summary.get("current") or 0)
        name = summary.get("name_ar") or ""
        if v["txn_kind"] == "withdrawal" and amount > current:
            out.append(f"السحب ({fmt(amount)}) أكبر من رصيد جاري الشريك «{name}» ({fmt(current)}) — "
                       f"هيبقى عليه {fmt(amount - max(current, ZERO))}.")
        if v["txn_kind"] == "capital":
            paid, subscribed = Decimal(summary.get("capital") or 0), Decimal(summary.get("subscribed_capital") or 0)
            if paid + amount > subscribed:
                out.append(f"رأس مال «{name}» المدفوع هيبقى {fmt(paid + amount)} وهو أكبر من المكتتب "
                           f"({fmt(subscribed)}) — حدّث «رأس المال المكتتب» من شاشة الشركاء.")
            if v["source_kind"] == "current" and amount > current:
                out.append(f"التحويل ({fmt(amount)}) أكبر من رصيد جاري الشريك ({fmt(current)}) — هيبقى عليه "
                           f"{fmt(amount - max(current, ZERO))}.")
        if v["txn_kind"] == "withdrawal":
            b = self.source_balance(v["source_kind"], v["treasury_id"] or v["bank_account_id"])
            if b is not None and b["balance"] + b["overdraft"] - amount < 0:
                what = "الخزنة" if v["source_kind"] == "treasury" else "الحساب البنكي"
                extra = f" (+ حد الجاري المدين {fmt(b['overdraft'])})" if b["overdraft"] else ""
                out.append(f"رصيد {what} «{b['name']}» {fmt(b['balance'])}{extra} — والسحب {fmt(amount)}، "
                           f"فالرصيد هيبقى {fmt(b['balance'] - amount)}.")
        return out

    def _lock_txn(self, tx: Executor, txn_id: int, expected_version: int | None, *, draft_only: bool = False,
                  posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_txn(int(txn_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=TXN_ENTITY, entity_id=txn_id)
        if before is None:
            raise BusinessError("الحركة دي مش موجودة (ممكن تكون اتمسحت).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — مستنداتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"الحركة {doc_label(before)} مرحّلة — الغي ترحيلها الأول عشان تعدّلها.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"الحركة {doc_label(before)} مش مرحّلة.")
        return before

    def _post_txn(self, tx: Executor, txn_id: int, before: dict[str, Any],
                  expected_version: int | None = None) -> dict[str, Any]:
        updated = self.repository.set_txn_status(txn_id, "posted", SESSION.user_id, ex=tx,
                                                 expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=TXN_ENTITY, entity_id=txn_id)
        v = self.repository.get_txn(txn_id, ex=tx)
        name, doc_type = KINDS[v["txn_kind"]]
        self.posting.post_document(tx, doc_type=doc_type, source_id=txn_id, source_no=v["txn_no"],
                                   entry_date=v["txn_date"],
                                   description=f"{name} — {v['partner_name']} — {v['description']}",
                                   reference=v["reference_no"], lines=self._txn_lines(v))
        self._record(tx, v["txn_kind"], "post", txn_id, before, v)
        return v

    def save_txn(self, txn_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                 post: bool = False) -> int:
        """يحفظ المسودة (ولو ``post`` يرحّلها في نفس الـ transaction)."""
        PermissionService.require(f"{PERM}.create" if txn_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        clean = self._clean_txn(data)
        try:
            with self.db.atomic() as tx:
                before = None
                if txn_id is None:
                    txn_id = self.repository.insert_txn({**clean, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock_txn(tx, int(txn_id), expected_version, draft_only=True)
                    updated = self.repository.update_txn(int(txn_id), clean, ex=tx, expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=TXN_ENTITY, entity_id=txn_id)
                after = self.repository.get_txn(int(txn_id), ex=tx)
                self._record(tx, clean["txn_kind"], "create" if before is None else "update", int(txn_id),
                             before, after)
                if post:
                    self._post_txn(tx, int(txn_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(txn_id)

    def post_txn(self, txn_id: int, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock_txn(tx, int(txn_id), expected_version, draft_only=True)
                after = self._post_txn(tx, int(txn_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["txn_no"]

    def unpost_txn(self, txn_id: int, expected_version: int | None = None) -> None:
        """القيد بيتمسح والحركة بترجع مسودة برقمها."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock_txn(tx, int(txn_id), expected_version, posted_only=True)
                self.posting.unpost_document(tx, doc_type=KINDS[before["txn_kind"]][1], source_id=int(txn_id))
                updated = self.repository.set_txn_status(int(txn_id), "draft", None, ex=tx,
                                                         expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=TXN_ENTITY, entity_id=txn_id)
                after = self.repository.get_txn(int(txn_id), ex=tx)
                self._record(tx, before["txn_kind"], "unpost", int(txn_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_txn(self, txn_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock_txn(tx, int(txn_id), expected_version, draft_only=True)
                if before["txn_no"]:
                    raise BusinessError(f"الحركة {before['txn_no']} خدت رقم رسمي قبل كده — ماتتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).")
                deleted = self.repository.delete_txn(int(txn_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=TXN_ENTITY, entity_id=txn_id)
                self._record(tx, before["txn_kind"], "delete", int(txn_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    # ==== توزيع الأرباح / الخسارة =============================================================
    def _clean_dist(self, data: dict[str, Any]) -> dict[str, Any]:
        dist_kind = data.get("dist_kind") or "profit"
        if dist_kind not in DIST_KINDS:
            raise BusinessError("النوع لازم «توزيع ربح» أو «تحميل خسارة».")
        dist_date = _date(data.get("dist_date"), "التاريخ")
        amount = parse_money(data.get("amount"), "المبلغ")
        if amount <= 0:
            raise BusinessError("اكتب المبلغ اللي هيتوزّع.")
        default = "توزيع أرباح على الشركاء" if dist_kind == "profit" else "تحميل خسارة على الشركاء"
        return {"dist_kind": dist_kind, "dist_date": dist_date, "amount": amount,
                "description": _text(data.get("description"), "البيان", 500) or default,
                "reference_no": _text(data.get("reference_no"), "رقم الإيصال", 40)}

    @staticmethod
    def _dist_lines(v: dict[str, Any], shares: list[dict[str, Any]]) -> list[PostingLine]:
        sign = Decimal(1) if v["dist_kind"] == "profit" else Decimal(-1)      # الخسارة = نفس القواعد بالسالب
        out = [PostingLine("retained", sign * Decimal(v["amount"]))]
        for s in shares:
            if Decimal(s["amount"]) > 0:
                out.append(PostingLine("current", sign * Decimal(s["amount"]), party=("partner", int(s["partner_id"])),
                                       description=f"نصيبه {Decimal(s['share_percent']).normalize():f}%"))
        return out

    def preview_distribution(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        v = self._clean_dist(data)
        shares = split_shares(v["amount"], self.repository.share_partners(self.db), v["dist_kind"])
        return self._names(self.posting.build("profit_dist", self._dist_lines(v, shares)))

    def dist_warnings(self, data: dict[str, Any]) -> list[str]:
        """تحذيرات بس: تحميل خسارة والسنة كسبانة."""
        v = self._clean_dist(data)
        avail = self.available_profit(v["dist_date"])
        if v["dist_kind"] == "loss" and avail["available"] > 0:
            return [f"المتاح للتوزيع لحد التاريخ ربح ({fmt(avail['available'])}) مش خسارة — متأكد إنه تحميل خسارة؟"]
        return []

    def _check_available(self, tx: Executor, v: dict[str, Any]) -> None:
        if v["dist_kind"] != "profit":
            return
        avail = self.available_profit(v["dist_date"], ex=tx)
        if Decimal(v["amount"]) > avail["available"]:
            raise BusinessError(f"المبلغ ({fmt(v['amount'])}) أكبر من الربح المتاح للتوزيع لحد "
                                f"{v['dist_date']:%d/%m/%Y} ({fmt(avail['available'])}) — "
                                f"المحتجزة {fmt(avail['retained'])} + ربح الفترة {fmt(avail['income'])}.")

    def _lock_dist(self, tx: Executor, dist_id: int, expected_version: int | None, *, draft_only: bool = False,
                   posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_dist(int(dist_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=DIST_ENTITY, entity_id=dist_id)
        if before is None:
            raise BusinessError("التوزيع ده مش موجود (ممكن يكون اتمسح).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — مستنداتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"التوزيع {doc_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"التوزيع {doc_label(before)} مش مرحّل.")
        return before

    def _snapshot(self, tx: Executor, dist_id: int) -> dict[str, Any]:
        row = self.repository.get_dist(dist_id, ex=tx)
        row["lines"] = self.repository.dist_lines(dist_id, ex=tx)
        return row

    def _record_dist(self, tx: Executor, action: str, dist_id: int, before: dict | None, after: dict | None) -> None:
        def head(row: dict | None) -> dict | None:
            return {k: v for k, v in row.items() if k != "lines"} if row else None

        self._record(tx, "distribution", action, dist_id, head(before), head(after),
                     lines={"acc.profit_distribution_lines": {"before": (before or {}).get("lines") or [],
                                                              "after": (after or {}).get("lines") or []}})

    def _post_dist(self, tx: Executor, dist_id: int, before: dict[str, Any],
                   expected_version: int | None = None) -> dict[str, Any]:
        """قفل التوزيع ← الربح المتاح ← نسب الشركاء بقفل ← الأنصبة ← الحالة ← القيد — كله في نفس الـ transaction."""
        self.repository.lock_distributions(tx)
        self._check_available(tx, before)
        shares = split_shares(Decimal(before["amount"]), self.repository.share_partners(tx, lock=True),
                              before["dist_kind"])
        self.repository.replace_dist_lines(dist_id, shares, tx)
        updated = self.repository.set_dist_status(dist_id, "posted", SESSION.user_id, ex=tx,
                                                  expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=DIST_ENTITY, entity_id=dist_id)
        v = self._snapshot(tx, dist_id)
        self.posting.post_document(tx, doc_type="profit_dist", source_id=dist_id, source_no=v["dist_no"],
                                   entry_date=v["dist_date"],
                                   description=f"{DIST_KINDS[v['dist_kind']]} على الشركاء — {v['description']}",
                                   reference=v["reference_no"], lines=self._dist_lines(v, shares))
        self._record_dist(tx, "post", dist_id, before, v)
        return v

    def save_distribution(self, dist_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                          post: bool = False) -> int:
        PermissionService.require(f"{PERM}.create" if dist_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        clean = self._clean_dist(data)
        try:
            with self.db.atomic() as tx:
                before = None
                if dist_id is None:
                    dist_id = self.repository.insert_dist({**clean, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock_dist(tx, int(dist_id), expected_version, draft_only=True)
                    updated = self.repository.update_dist(int(dist_id), clean, ex=tx,
                                                          expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=DIST_ENTITY, entity_id=dist_id)
                after = self._snapshot(tx, int(dist_id))
                self._record_dist(tx, "create" if before is None else "update", int(dist_id), before, after)
                if post:
                    self._post_dist(tx, int(dist_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(dist_id)

    def post_distribution(self, dist_id: int, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                self._lock_dist(tx, int(dist_id), expected_version, draft_only=True)
                before = self._snapshot(tx, int(dist_id))
                after = self._post_dist(tx, int(dist_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["dist_no"]

    def unpost_distribution(self, dist_id: int, expected_version: int | None = None) -> None:
        """القيد والأنصبة بيتمسحوا والتوزيع بيرجع مسودة برقمه."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                self._lock_dist(tx, int(dist_id), expected_version, posted_only=True)
                self.repository.lock_distributions(tx)
                before = self._snapshot(tx, int(dist_id))
                self.posting.unpost_document(tx, doc_type="profit_dist", source_id=int(dist_id))
                updated = self.repository.set_dist_status(int(dist_id), "draft", None, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=DIST_ENTITY, entity_id=dist_id)
                self.repository.replace_dist_lines(int(dist_id), [], tx)
                after = self._snapshot(tx, int(dist_id))
                self._record_dist(tx, "unpost", int(dist_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_distribution(self, dist_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock_dist(tx, int(dist_id), expected_version, draft_only=True)
                if before["dist_no"]:
                    raise BusinessError(f"التوزيع {before['dist_no']} خد رقم رسمي قبل كده — مايتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
                deleted = self.repository.delete_dist(int(dist_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=DIST_ENTITY, entity_id=dist_id)
                self._record_dist(tx, "delete", int(dist_id), {**before, "lines": []}, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
