"""«شيكات العملاء» و«شيكات الموردين» — المرحلة 5، النموذج 1 «الكلاسيك» + كارت تنبيهات الاستحقاق
(اختيار المستخدم 2026-11-17 + ردوده — mockups/phase5/03_cheques.html · سكريبت 0038).

* **شاشتين** (زي الأكسس) والـ Service واحد بـ ``direction``: ``in`` وارد من عميل · ``out`` صادر لمورد — وصلاحية لكل شاشة.
* الشيك الجديد **«حفظ مسودة»** ← **«ترحيل»** = استلام (الوارد) / إصدار (الصادر) بقيده ورقم ``CHR/CHP-2026-00001``.
* بعد كده **كل خطوة** قيد مرحّل بتاريخها (محرك الترحيل) — صف في ``cash.cheque_events``:
    وارد:  إيداع للتحصيل · تم التحصيل · رجع من البنك · إيداع تاني · تظهير لمورد · رد للعميل
    صادر:  تم الصرف · رجع / اتلغى
  و**«تم التحصيل» / «تم الصرف»** (التشيك بوكس — رد المستخدم) بيعمل اللي ناقص مرة واحدة (إيداع + تحصيل لو لسه في الحافظة).
* **«تراجع عن آخر خطوة»** بالصلاحية (``unpost``): الخطوة وقيدها بيتمسحوا والشيك يرجع لحالته اللي قبلها
  (والتراجع عن الاستلام / الإصدار بيرجّعه مسودة **برقمه**).
* الحسابات (رد المستخدم: موافق على الجدول): أوراق القبض 1104 · **شيكات تحت التحصيل 1108 (جديد)** · العملاء ·
  الموردين · أوراق الدفع 2102 · البنك · مصاريف بنكية 5402 (اختياري مع التحصيل والرجوع).
  حسابات الشيكات (1104 · 1108 · 2102) **من غير طرف** (0039 — ملاحظة المستخدم: كشف العميل كان بيبيّن الاستلام مرتين)
  ← العميل / المورد على سطر العملاء / الموردين بس، فالشيك بيبان في كشفه مرة واحدة.
* رقم الشيك مطلوب ومايتكررش على نفس البنك · بنك الساحب من قايمة البنوك بتاعتنا · أمر التوريد اختياري ·
  الرصيد مش مكفي للشيك الصادر ← **تحذير بس**.
* التنبيهات: الشيكات المفتوحة **المتأخرة · النهارده · بكرة · خلال 3 أيام** (الكارت فوق الشاشة).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.cheque_repository import DONE, FILTERS, OPEN, ChequeRepository
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
ALERT_DAYS = 3          # رد المستخدم: «متأخر · النهارده · بكرة · 3 أيام»
ENTITY = "cash.cheques"


@dataclass(frozen=True)
class ChequeKind:
    direction: str       # in / out
    doc_type: str        # cheque_in / cheque_out (الترقيم + قواعد الترحيل)
    target: str          # الشاشة في الصلاحيات
    title: str           # «شيكات العملاء»
    short: str           # «الشيك الوارد»
    prefix: str          # CHR / CHP
    party: str           # customer / supplier
    party_label: str     # «العميل» / «المورد»
    start_event: str     # receive / issue
    start_label: str     # «استلام» / «إصدار»
    done_label: str      # «تم التحصيل» / «تم الصرف»

    @property
    def perm(self) -> str:
        return f"cash.{self.target}"


KINDS = {
    "in": ChequeKind("in", "cheque_in", "customer_cheques", "شيكات العملاء", "الشيك الوارد", "CHR", "customer",
                     "العميل", "receive", "استلام", "تم التحصيل"),
    "out": ChequeKind("out", "cheque_out", "supplier_cheques", "شيكات الموردين", "الشيك الصادر", "CHP", "supplier",
                      "المورد", "issue", "إصدار", "تم الصرف"),
}

# الحالة: (الاسم، اللون، الأيقونة)
STATUSES: dict[str, tuple[str, str, str]] = {
    "draft": ("مسودة", "#92400E", "📝"),
    "portfolio": ("في الحافظة", "#2563EB", "📥"),
    "under_collection": ("تحت التحصيل", "#B45309", "🏦"),
    "collected": ("اتحصّل", "#137A38", "✔"),
    "bounced": ("رجع من البنك", "#B91C1C", "↩"),
    "endorsed": ("اتظهّر لمورد", "#6D28D9", "🔁"),
    "returned": ("اترد للعميل", "#64748B", "⤺"),
    "issued": ("صادر", "#2563EB", "📤"),
    "cleared": ("اتصرف", "#137A38", "✔"),
    "cancelled": ("رجع / اتلغى", "#64748B", "✖"),
}

# الدورة (نفس cash.cheque_transitions في القاعدة — الاختبار بيتأكد إنهم زي بعض):
# (الاتجاه، الخطوة، من) ← (لـ، ليها قيد؟، الاسم)
TRANSITIONS: dict[tuple[str, str, str], tuple[str, bool, str]] = {
    ("in", "receive", "draft"): ("portfolio", True, "استلام"),
    ("in", "deposit", "portfolio"): ("under_collection", True, "إيداع للتحصيل"),
    ("in", "deposit", "bounced"): ("under_collection", True, "إيداع تاني"),
    ("in", "collect", "under_collection"): ("collected", True, "تم التحصيل"),
    ("in", "bounce", "under_collection"): ("bounced", True, "رجع من البنك"),
    ("in", "endorse", "portfolio"): ("endorsed", True, "تظهير لمورد"),
    ("in", "return", "portfolio"): ("returned", True, "رد للعميل"),
    ("in", "return", "bounced"): ("returned", False, "رد للعميل"),
    ("out", "issue", "draft"): ("issued", True, "إصدار"),
    ("out", "clear", "issued"): ("cleared", True, "تم الصرف"),
    ("out", "cancel", "issued"): ("cancelled", True, "رجع / اتلغى"),
}
STEP_ORDER = ("deposit", "collect", "bounce", "endorse", "return", "clear", "cancel")


def cheque_label(row: dict[str, Any]) -> str:
    """«CHR-2026-00012» أو «مسودة 7»."""
    return row.get("cheque_ref") or f"مسودة {row.get('draft_no')}"


def status_name(status: str) -> str:
    return STATUSES.get(status, (status, "", ""))[0]


def due_bucket(due: dt.date, today: dt.date) -> str:
    """overdue متأخر · today النهارده · tomorrow بكرة · soon خلال 3 أيام · later بعد كده."""
    days = (due - today).days
    if days < 0:
        return "overdue"
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    return "soon" if days <= ALERT_DAYS else "later"


def due_text(due: dt.date, today: dt.date) -> str:
    days = (due - today).days
    if days < 0:
        return f"متأخر {-days} يوم"
    return {0: "النهارده", 1: "بكرة"}.get(days, f"بعد {days} أيام" if days <= 10 else f"بعد {days} يوم")


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def fmt(amount: Decimal) -> str:
    return f"{amount:,.2f}"


class ChequeService:
    def __init__(self, direction: str, db: Database | None = None, repository: ChequeRepository | None = None,
                 cash: VoucherRepository | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        if direction not in KINDS:
            raise ValueError(f"نوع شيك مش معروف: {direction}")
        self.kind = KINDS[direction]
        self.db = db or get_db()
        self.repository = repository or ChequeRepository(self.db)
        self.cash = cash or VoucherRepository(self.db)          # العملاء / الموردين · الحسابات البنكية · الأرصدة
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    @property
    def is_in(self) -> bool:
        return self.kind.direction == "in"

    # ---- قراية -------------------------------------------------------------
    def list_cheques(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                     keyword: str = "", page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in FILTERS else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_cheques(self.kind.direction, date_from, date_to, status, keyword.strip(),
                                            limit=page_size, offset=offset)

    def count_cheques(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                      keyword: str = "") -> dict[str, Any]:
        status = status if status in FILTERS else "all"
        row = self.repository.count_cheques(self.kind.direction, date_from, date_to, status, keyword.strip())
        return {"total": int(row["total"]), "drafts": int(row["drafts"]), "open": int(row["open"]),
                "open_amount": Decimal(row["open_amount"]), "posted_amount": Decimal(row["posted_amount"])}

    def get_cheque(self, cheque_id: int) -> dict[str, Any] | None:
        row = self.repository.get_cheque(int(cheque_id))
        if row is not None:
            row["label"] = cheque_label(row)
        return row

    def events(self, cheque_id: int) -> list[dict[str, Any]]:
        return self.repository.events(int(cheque_id))

    def choices(self) -> dict[str, Any]:
        """العملاء أو الموردين · البنوك (بنك الساحب) · الحسابات البنكية · الموردين (للتظهير) · أوامر التوريد."""
        parties = self.cash.party_choices("receipt" if self.is_in else "payment")
        return {"parties": parties,
                "suppliers": self.cash.party_choices("payment") if self.is_in else parties,
                "banks": self.repository.banks(),
                "bank_accounts": self.cash.bank_account_choices(),
                "projects": self.cash.project_choices()}

    def alerts(self, today: dt.date | None = None) -> dict[str, Any]:
        """الكارت: الشيكات المفتوحة المتأخرة · النهارده · بكرة · خلال 3 أيام (بعددها ومبلغها) + الصفوف للشاشة."""
        today = today or dt.date.today()
        rows = self.repository.due_cheques(self.kind.direction, today + dt.timedelta(days=ALERT_DAYS))
        out: dict[str, Any] = {k: {"count": 0, "amount": ZERO} for k in ("overdue", "today", "tomorrow", "soon")}
        for r in rows:
            bucket = due_bucket(r["due_date"], today)
            r["bucket"] = bucket
            r["due_text"] = due_text(r["due_date"], today)
            out[bucket]["count"] += 1
            out[bucket]["amount"] += Decimal(r["amount"])
        out["rows"] = rows
        out["total"] = len(rows)
        return out

    def next_steps(self, record: dict[str, Any]) -> list[tuple[str, str]]:
        """الخطوات المسموحة من حالة الشيك دلوقتي: [(الخطوة، الاسم)] بالترتيب."""
        status = record.get("status")
        steps = [(event, TRANSITIONS[(self.kind.direction, event, status)][2]) for event in STEP_ORDER
                 if (self.kind.direction, event, status) in TRANSITIONS]
        return steps

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    @staticmethod
    def _date(value: Any, label: str) -> dt.date:
        if isinstance(value, dt.datetime):
            value = value.date()
        if not isinstance(value, dt.date):
            raise BusinessError(f"{label} مطلوب.")
        return value

    def _clean(self, data: dict[str, Any], cheque_id: int | None = None) -> dict[str, Any]:
        label = "تاريخ الاستلام" if self.is_in else "تاريخ الإصدار"
        cheque_date = self._date(data.get("cheque_date"), label)
        due_date = self._date(data.get("due_date"), "تاريخ الاستحقاق")
        party_id = self._id(data.get("party_id"))
        if party_id is None:
            raise BusinessError(f"اختار {self.kind.party_label}.")
        cheque_no = " ".join(str(data.get("cheque_no") or "").split())
        if not cheque_no:
            raise BusinessError("رقم الشيك مطلوب (زي ما هو مكتوب على الشيك).")
        if len(cheque_no) > 30:
            raise BusinessError("«رقم الشيك» أطول من المسموح (30 حرف).")
        bank_id = self._id(data.get("drawer_bank_id") if self.is_in else data.get("bank_account_id"))
        if bank_id is None:
            raise BusinessError("اختار بنك الساحب." if self.is_in else "اختار الحساب البنكي اللي الشيك طالع منه.")
        amount = parse_money(data.get("amount"), "المبلغ")
        if amount <= 0:
            raise BusinessError("اكتب مبلغ الشيك.")
        description = " ".join(str(data.get("description") or "").split())
        if not description:
            raise BusinessError("البيان مطلوب (الشيك ده عن إيه).")
        if len(description) > 500:
            raise BusinessError("«البيان» أطول من المسموح (500 حرف).")
        dup = self.repository.duplicate(self.kind.direction, bank_id, cheque_no, cheque_id)
        if dup is not None:
            raise BusinessError(f"رقم الشيك {cheque_no} متسجل قبل كده على نفس البنك ({cheque_label(dup)}).")
        return {"cheque_date": cheque_date, "due_date": due_date, "cheque_no": cheque_no,
                "customer_id": party_id if self.is_in else None,
                "supplier_id": None if self.is_in else party_id,
                "drawer_bank_id": bank_id if self.is_in else None,
                "bank_account_id": None if self.is_in else bank_id,
                "amount": amount, "description": description, "project_id": self._id(data.get("project_id"))}

    def warnings(self, data: dict[str, Any], cheque_id: int | None = None) -> list[str]:
        """تحذيرات بس (مش منع): الاستحقاق قبل التاريخ · رصيد البنك مش مكفي الشيكات الصادرة عليه."""
        out: list[str] = []
        try:
            v = self._clean(data, cheque_id)
        except BusinessError:
            return out
        if v["due_date"] < v["cheque_date"]:
            out.append("تاريخ الاستحقاق قبل تاريخ " + ("الاستلام" if self.is_in else "الإصدار") + " — اتأكد منه.")
        if not self.is_in:
            short = self._bank_short(v["bank_account_id"], v["amount"], cheque_id)
            if short:
                out.append(short)
        return out

    def _bank_row(self, bank_account_id: int) -> dict[str, Any] | None:
        return next((b for b in self.cash.bank_account_choices() if b["id"] == int(bank_account_id)), None)

    def _bank_short(self, bank_account_id: int, amount: Decimal, exclude_id: int | None = None) -> str | None:
        """رصيد الحساب (+ حد الجاري المدين) أقل من الشيكات الصادرة عليه + الشيك ده ← رسالة تحذير."""
        row = self._bank_row(bank_account_id)
        if row is None:
            return None
        balance = self.cash.account_balance(int(row["account_id"]))
        pending = Decimal(self.repository.issued_on_account(int(bank_account_id), exclude_id)) + amount
        limit = Decimal(row.get("overdraft_limit") or 0)
        if balance + limit >= pending:
            return None
        extra = f" (+ حد الجاري المدين {fmt(limit)})" if limit else ""
        return (f"رصيد «{row['bank_name']} — {row['code']}» {fmt(balance)}{extra} مش هيكفي الشيكات الصادرة عليه "
                f"({fmt(pending)} بالشيك ده) — تحذير بس.")

    # ---- القيود ---------------------------------------------------------------
    def _lines(self, c: dict[str, Any], event: str, from_status: str, *, bank_account_id: int | None = None,
               supplier_id: int | None = None, fees: Decimal = ZERO) -> list[PostingLine]:
        amount = Decimal(c["amount"])
        if self.is_in:
            cust = ("customer", int(c["customer_id"]))
            if event == "receive":
                return [PostingLine("notes_dr", amount), PostingLine("cust_cr", amount, party=cust)]
            if event == "deposit":
                credit = (PostingLine("cust_cr", amount, party=cust) if from_status == "bounced"
                          else PostingLine("notes_cr", amount))
                return [PostingLine("coll_dr", amount), credit]
            if event == "collect":
                lines = [PostingLine("bank_dr", amount - fees, bank_account_id=bank_account_id)]
                if fees:
                    lines.append(PostingLine("fees", fees))
                return lines + [PostingLine("coll_cr", amount)]
            if event == "bounce":
                lines = [PostingLine("cust_dr", amount, party=cust), PostingLine("coll_cr", amount)]
                if fees:
                    lines += [PostingLine("fees", fees), PostingLine("bank_cr", fees, bank_account_id=bank_account_id)]
                return lines
            if event == "endorse":
                return [PostingLine("supp_dr", amount, party=("supplier", int(supplier_id))),
                        PostingLine("notes_cr", amount)]
            if event == "return":
                return [] if from_status == "bounced" else [PostingLine("cust_dr", amount, party=cust),
                                                            PostingLine("notes_cr", amount)]
        else:
            sup = ("supplier", int(c["supplier_id"]))
            if event == "issue":
                return [PostingLine("supp_dr", amount, party=sup), PostingLine("notes_cr", amount)]
            if event == "clear":
                return [PostingLine("notes_dr", amount),
                        PostingLine("bank_cr", amount, bank_account_id=int(c["bank_account_id"]))]
            if event == "cancel":
                return [PostingLine("notes_dr", amount), PostingLine("supp_cr", amount, party=sup)]
        raise BusinessError(f"الخطوة «{event}» مش معروفة.")

    def _named(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.cash.accounts([ln["account_id"] for ln in lines])
        return [{"account_id": ln["account_id"], "account_code": names[ln["account_id"]]["code"],
                 "account_name": names[ln["account_id"]]["name_ar"], "debit": ln["debit"], "credit": ln["credit"],
                 "role_name": ln["role_name"]} for ln in lines]

    def preview(self, data: dict[str, Any], cheque_id: int | None = None) -> list[dict[str, Any]]:
        """قيد الاستلام / الإصدار اللي هيتعمل مع الترحيل (للفورم وهو بيتكتب) — أي نقص ← ``BusinessError``."""
        v = self._clean(data, cheque_id)
        lines = self.posting.build(self.kind.doc_type, self._lines(v, self.kind.start_event, "draft"),
                                   project_id=v["project_id"])
        return self._named(lines)

    def _step_args(self, c: dict[str, Any], event: str, bank_account_id: Any, supplier_id: Any,
                   fees: Any) -> dict[str, Any]:
        """بيانات الخطوة: الحساب البنكي (الإيداع يختاره · التحصيل والرجوع على بنك الإيداع) · المورد · المصاريف."""
        fee = parse_money(fees or "0", "المصاريف البنكية")
        if fee < 0:
            raise BusinessError("المصاريف البنكية مينفعش تبقى بالسالب.")
        if fee and event not in ("collect", "bounce"):
            fee = ZERO
        if fee and fee >= Decimal(c["amount"]) and event == "collect":
            raise BusinessError("المصاريف البنكية أكبر من مبلغ الشيك.")
        bank = None
        if event == "deposit":
            bank = self._id(bank_account_id)
            if bank is None:
                raise BusinessError("اختار الحساب البنكي اللي هيتودع فيه الشيك.")
        elif event in ("collect", "bounce"):
            bank = c["bank_account_id"]
        elif event in ("clear",):
            bank = c["bank_account_id"]
        supplier = self._id(supplier_id) if event == "endorse" else None
        if event == "endorse" and supplier is None:
            raise BusinessError("اختار المورد اللي الشيك هيتظهّر له.")
        return {"bank_account_id": bank, "supplier_id": supplier, "fees": fee}

    def step_preview(self, cheque_id: int, event: str, bank_account_id: Any = None, supplier_id: Any = None,
                     fees: Any = 0) -> list[dict[str, Any]]:
        """قيد الخطوة اللي هتتعمل (للشاشة قبل التنفيذ) — ``[]`` لو الخطوة مالهاش قيد."""
        c = self.repository.get_cheque(int(cheque_id))
        if c is None:
            raise BusinessError("الشيك ده مش موجود (ممكن يكون اتمسح).")
        key = (self.kind.direction, event, c["status"])
        if key not in TRANSITIONS:
            raise BusinessError(f"الخطوة دي مش مسموحة والشيك «{status_name(c['status'])}».")
        args = self._step_args(c, event, bank_account_id, supplier_id, fees)
        lines = self._lines(c, event, c["status"], **args)
        if not lines:
            return []
        return self._named(self.posting.build(self.kind.doc_type, lines, project_id=c["project_id"]))

    def entry_for(self, event_id: int) -> dict[str, Any] | None:
        """قيد خطوة بسطوره."""
        return self.posting.entry_for(self.kind.doc_type, int(event_id))

    def last_entry(self, cheque_id: int) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """آخر خطوة ليها قيد + قيدها (لمعاينة القيد في الشاشة)."""
        events = [e for e in self.repository.events(int(cheque_id)) if e["entry_id"]]
        if not events:
            return None, None
        return events[-1], self.entry_for(events[-1]["id"])

    # ---- مساعدات الكتابة ----------------------------------------------------------
    def _lock(self, tx: Executor, cheque_id: int, expected_version: int | None, *,
              draft_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_cheque(int(cheque_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=cheque_id)
        if before is None or before["direction"] != self.kind.direction:
            raise BusinessError(f"{self.kind.short} ده مش موجود (ممكن يكون اتمسح).")
        if draft_only:
            if before["year_status"] == "closed":
                raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — شيكاتها ماتتعدّلش.")
            if before["status"] != "draft":
                raise BusinessError(f"{self.kind.short} {cheque_label(before)} اترحّل — اعمل «تراجع» لحد ما يرجع "
                                    "مسودة عشان تعدّله.")
        return before

    def _record(self, tx: Executor, action: str, cheque_id: int, before: dict | None, after: dict | None,
                extra: dict | None = None) -> None:
        row = after or before or {}
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=cheque_id, entity_label=self.kind.title,
                          record_label=f"{cheque_label(row)} — شيك {row.get('cheque_no') or ''} — "
                                       f"{row.get('party_name') or ''} — {fmt(Decimal(row.get('amount') or 0))}",
                          before=before, after=after, extra=extra)

    def _step(self, tx: Executor, c: dict[str, Any], event: str, event_date: dt.date, *,
              bank_account_id: Any = None, supplier_id: Any = None, fees: Any = 0, note: str | None = None,
              expected_version: int | None = None) -> dict[str, Any]:
        """خطوة واحدة جوه الـ transaction: الحالة ← صف الخطوة ← القيد بمحرك الترحيل. بيرجّع الشيك بعدها."""
        key = (self.kind.direction, event, c["status"])
        if key not in TRANSITIONS:
            raise BusinessError(f"{self.kind.short} {cheque_label(c)} «{status_name(c['status'])}» — "
                                "الخطوة دي مش مسموحة دلوقتي (ممكن يكون اتغير من جهاز تاني — دوس «تحديث»).")
        to_status, has_entry, name = TRANSITIONS[key]
        event_date = self._date(event_date, "تاريخ الخطوة")
        args = self._step_args(c, event, bank_account_id, supplier_id, fees)
        note = " ".join(str(note or "").split())[:300] or None
        state: dict[str, Any] = {}
        if self.is_in:
            if event == "deposit":
                state["bank_account_id"] = args["bank_account_id"]
            if event == "endorse":
                state["supplier_id"] = args["supplier_id"]
        if event == self.kind.start_event:
            state["posted_by"] = SESSION.user_id
        updated = self.repository.set_state(int(c["id"]), to_status, event_date, ex=tx,
                                            expected_version=expected_version, **state)
        self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=c["id"])
        after = self.repository.get_cheque(int(c["id"]), ex=tx)            # الرقم الرسمي (أول ترحيل)
        seq = len(self.repository.events(int(c["id"]), ex=tx)) + 1
        event_id = self.repository.insert_event(
            {"cheque_id": c["id"], "seq": seq, "event_type": event, "event_date": event_date,
             "from_status": c["status"], "to_status": to_status, "has_entry": has_entry, "note": note,
             "created_by": SESSION.user_id, **args}, ex=tx)
        if has_entry:
            lines = self._lines(after, event, c["status"], **args)
            self.posting.post_document(
                tx, doc_type=self.kind.doc_type, source_id=event_id, source_no=after["cheque_ref"],
                entry_date=event_date,
                description=f"{name} — شيك {after['cheque_no']} — {after['party_name']} — {after['description']}",
                reference=after["cheque_no"], lines=lines, project_id=after["project_id"])
        self._record(tx, "post" if event == self.kind.start_event else "update", int(c["id"]), c, after,
                     extra={"step": event, "step_name": name, "step_date": event_date.isoformat(), "seq": seq})
        return after

    def _run(self, fn) -> Any:  # noqa: ANN001
        try:
            with self.db.atomic() as tx:
                return fn(tx)
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError("رقم الشيك ده اتسجل على نفس البنك من جهاز تاني في نفس اللحظة — دوس «تحديث».") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    # ---- حفظ مسودة / ترحيل / خطوات / تراجع / مسح -------------------------------------
    def save_draft(self, cheque_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة (ولو ``post`` يعمل الاستلام / الإصدار في نفس الـ transaction — يا الاتنين يا ولا حاجة)."""
        perm = self.kind.perm
        PermissionService.require(f"{perm}.create" if cheque_id is None else f"{perm}.edit")
        PermissionService.require(f"{perm}.save")
        if post:
            PermissionService.require(f"{perm}.post")
        clean = self._clean(data, None if cheque_id is None else int(cheque_id))

        def work(tx: Executor) -> int:
            nonlocal cheque_id
            before = None
            if cheque_id is None:
                cheque_id = self.repository.insert_cheque(self.kind.direction,
                                                          {**clean, "user_id": SESSION.user_id}, ex=tx)
            else:
                before = self._lock(tx, int(cheque_id), expected_version, draft_only=True)
                updated = self.repository.update_cheque(int(cheque_id), clean, ex=tx,
                                                        expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=cheque_id)
            after = self.repository.get_cheque(int(cheque_id), ex=tx)
            self._record(tx, "create" if before is None else "update", int(cheque_id), before, after)
            if post:
                self._step(tx, after, self.kind.start_event, after["cheque_date"])
            return int(cheque_id)

        return self._run(work)

    def post(self, cheque_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة (= استلام / إصدار بتاريخ الشيك) ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{self.kind.perm}.post")

        def work(tx: Executor) -> str:
            c = self._lock(tx, int(cheque_id), expected_version, draft_only=True)
            return self._step(tx, c, self.kind.start_event, c["cheque_date"],
                              expected_version=expected_version)["cheque_ref"]

        return self._run(work)

    def do_step(self, cheque_id: int, event: str, event_date: dt.date, expected_version: int | None = None, *,
                bank_account_id: Any = None, supplier_id: Any = None, fees: Any = 0, note: str | None = None) -> str:
        """خطوة من خطوات الشيك (إيداع · تحصيل · رجوع · تظهير · رد · صرف · إلغاء) بقيدها ← الحالة الجديدة."""
        PermissionService.require(f"{self.kind.perm}.post")
        if event == self.kind.start_event:
            raise BusinessError("الاستلام / الإصدار بيبقى من «ترحيل».")

        def work(tx: Executor) -> str:
            c = self._lock(tx, int(cheque_id), expected_version)
            return self._step(tx, c, event, event_date, bank_account_id=bank_account_id, supplier_id=supplier_id,
                              fees=fees, note=note, expected_version=expected_version)["status"]

        return self._run(work)

    def complete(self, cheque_id: int, event_date: dt.date, expected_version: int | None = None, *,
                 bank_account_id: Any = None, fees: Any = 0) -> str:
        """التشيك بوكس «تم التحصيل» / «تم الصرف» (رد المستخدم): بيعمل الخطوات اللي ناقصة مرة واحدة —
        الوارد في الحافظة أو الراجع ← إيداع + تحصيل · تحت التحصيل ← تحصيل · الصادر ← صرف."""
        PermissionService.require(f"{self.kind.perm}.post")

        def work(tx: Executor) -> str:
            c = self._lock(tx, int(cheque_id), expected_version)
            version = expected_version
            if not self.is_in:
                return self._step(tx, c, "clear", event_date, expected_version=version)["status"]
            if c["status"] in ("portfolio", "bounced"):
                c = self._step(tx, c, "deposit", event_date, bank_account_id=bank_account_id,
                               expected_version=version)
                version = None                                       # الصف مقفول معانا — الإصدار اتغير بالخطوة الأولى
            elif c["status"] != "under_collection":
                raise BusinessError(f"{self.kind.short} {cheque_label(c)} «{status_name(c['status'])}» — "
                                    "مينفعش يتعلّم «تم التحصيل».")
            return self._step(tx, c, "collect", event_date, fees=fees, expected_version=version)["status"]

        return self._run(work)

    def undo_last(self, cheque_id: int, expected_version: int | None = None) -> str:
        """«تراجع عن آخر خطوة» (صلاحية إلغاء الترحيل): الخطوة وقيدها بيتمسحوا والشيك يرجع لحالته اللي قبلها."""
        PermissionService.require(f"{self.kind.perm}.unpost")

        def work(tx: Executor) -> str:
            c = self._lock(tx, int(cheque_id), expected_version)
            events = self.repository.events(int(cheque_id), ex=tx)
            if not events:
                raise BusinessError(f"{self.kind.short} {cheque_label(c)} مسودة — مفيش خطوات تتراجع عنها.")
            last, rest = events[-1], events[:-1]
            if last["has_entry"]:
                self.posting.unpost_document(tx, doc_type=self.kind.doc_type, source_id=int(last["id"]))
            self.repository.delete_event(int(last["id"]), ex=tx)
            back = last["from_status"]
            state: dict[str, Any] = {}
            if self.is_in:
                deposits = [e for e in rest if e["event_type"] == "deposit"]
                keep_bank = back in ("under_collection", "collected", "bounced")
                state["bank_account_id"] = deposits[-1]["bank_account_id"] if keep_bank and deposits else None
                state["supplier_id"] = None
            if back == "draft":
                state["posted_by"] = None
            updated = self.repository.set_state(int(cheque_id), back, rest[-1]["event_date"] if rest else None,
                                                ex=tx, expected_version=expected_version, **state)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=cheque_id)
            after = self.repository.get_cheque(int(cheque_id), ex=tx)
            self._record(tx, "unpost", int(cheque_id), c, after,
                         extra={"undo_step": last["event_type"], "step_name": last["name_ar"], "seq": last["seq"]})
            return back

        return self._run(work)

    def delete_cheque(self, cheque_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{self.kind.perm}.delete")

        def work(tx: Executor) -> None:
            before = self._lock(tx, int(cheque_id), expected_version, draft_only=True)
            if before["cheque_ref"]:
                raise BusinessError(f"{self.kind.short} {before['cheque_ref']} خد رقم رسمي قبل كده — مايتمسحش "
                                    "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
            deleted = self.repository.delete_cheque(int(cheque_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=cheque_id)
            self._record(tx, "delete", int(cheque_id), before, None)

        self._run(work)


__all__ = ["ALERT_DAYS", "DONE", "KINDS", "OPEN", "PAGE_SIZE", "STATUSES", "TRANSITIONS", "ChequeService",
           "cheque_label", "due_bucket", "due_text", "status_name"]
