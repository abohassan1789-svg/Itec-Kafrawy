"""القيد اليدوي (شاشة «القيود اليومية» — المرحلة 4، النموذج 1 اختيار المستخدم 2026-11-16 + ردوده).

* **«حفظ مسودة»** (``save_draft``): المسودة تتحفظ حتى لو مش متوازنة — سطر واحد على الأقل.
* **«ترحيل»** (``post`` أو ``save_draft(..., post=True)`` = حفظ + ترحيل في نفس الـ transaction): القاعدة بتتأكد من
  التوازن (سطرين على الأقل · المدين = الدائن) والسنة المفتوحة والحسابات الفرعية النشطة، وبتصرف الرقم الرسمي
  ``JV-2026-00001`` **أول مرة بس** جوه قفل العدّاد (المسلسل بيكمّل بعد 99,999 من غير ما يتقص).
* **«إلغاء الترحيل»** (``unpost``) **بالصلاحية بس** (من غير كلمة سر): القيد بيرجع مسودة برقمه وسطوره ويتعدّل —
  في السنة المفتوحة بس. **مفيش عكس** (رد المستخدم).
* **الطرف إجباري** على حسابات الرقابة (العملاء/الموردين/العهد/جاري الشركاء) ومن نوع الحساب بس ·
  **مركز التكلفة** و**أمر التوريد** اختياريين (إلا الحساب «لازم مركز تكلفة») · **بالجنيه بس** (قرشين).
* الحذف للمسودة اللي **ماخدتش رقم رسمي** بس · القيود الأوتوماتيك (من المستندات) **قراية بس** هنا.
* كل حفظ / ترحيل / إلغاء / مسح في ``sec.audit_log`` قبل/بعد **بالسطور** في نفس الـ transaction + ``row_version``.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, get_db
from app.repositories.journal_repository import PARTY_COLUMNS, SOURCES, STATUSES, JournalRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "acc.journal_entries", "قيد يومية"
PERM = "acc.journal_entries"
PAGE_SIZE = 50
MAX_LINES = 500
MAX_AMOUNT = Decimal("9999999999999999.99")
NOT_FOUND = "القيد ده مش موجود (ممكن يكون اتمسح)."
AUTO_READ_ONLY = "القيد ده أوتوماتيك من مستند ({no}) — بيتعدّل من المستند نفسه، مش من هنا."
PARTY_LABELS = {"customer": "العميل", "supplier": "المورد", "employee": "الموظف", "partner": "الشريك"}
STATUS_LABELS = {"draft": "مسودة", "posted": "مرحّل"}
CENT = Decimal("0.01")


def entry_label(row: dict[str, Any]) -> str:
    """«JV-2026-00012» أو «مسودة 7»."""
    return row.get("entry_no") or f"مسودة {row.get('draft_no')}"


def parse_money(value: Any, label: str) -> Decimal:
    """«1,250.5» ← 1250.50 · الفاضي ← 0 · سالب / أكتر من قرشين / مش رقم ← BusinessError."""
    if value is None:
        return Decimal("0")
    text = str(value).strip().replace(",", "").replace("٬", "").replace("٫", ".")
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if text == "":
        return Decimal("0")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise BusinessError(f"«{label}» لازم يبقى رقم.") from None
    if not amount.is_finite():
        raise BusinessError(f"«{label}» لازم يبقى رقم.")
    if amount < 0:
        raise BusinessError(f"«{label}» مينفعش يبقى بالسالب.")
    if amount != amount.quantize(CENT, rounding=ROUND_HALF_UP):
        raise BusinessError(f"«{label}» بالجنيه بقرشين بالكتير (زي 1250.75).")
    if amount > MAX_AMOUNT:
        raise BusinessError(f"«{label}» أكبر من المسموح.")
    return amount.quantize(CENT)


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


class JournalService:
    def __init__(self, db: Database | None = None, repository: JournalRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or JournalRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def _filters(status: str, source: str) -> tuple[str, str]:
        return (status if status in STATUSES else "all", source if source in SOURCES else "all")

    def list_entries(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                     source: str = "all", keyword: str = "", page: int = 1,
                     page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status, source = self._filters(status, source)
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_entries(date_from, date_to, status, source, keyword.strip(),
                                            limit=page_size, offset=offset)

    def count_entries(self, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                      source: str = "all", keyword: str = "") -> dict[str, Any]:
        status, source = self._filters(status, source)
        row = self.repository.count_entries(date_from, date_to, status, source, keyword.strip())
        return {"total": int(row["total"]), "drafts": int(row["drafts"]), "posted": int(row["posted"]),
                "posted_amount": Decimal(row["posted_amount"])}

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = self.repository.get_entry(int(entry_id))
        if row is not None:
            row["lines"] = self.repository.entry_lines(int(entry_id))
            row["label"] = entry_label(row)
        return row

    def choices(self) -> dict[str, Any]:
        """اختيارات السطور (الموقوف بيرجع كمان عشان قيد قديم عليه يتعرض — الشاشة بتعرض النشط بس للجديد)."""
        return {"accounts": self.repository.account_choices(),
                "cost_centers": self.repository.cost_center_choices(),
                "projects": self.repository.project_choices(),
                "parties": {k: self.repository.party_choices(k) for k in PARTY_COLUMNS}}

    def year_for(self, d: dt.date) -> dict[str, Any] | None:
        return self.repository.year_for(d)

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    def _clean(self, data: dict[str, Any], accounts: dict[int, dict]) -> dict[str, Any]:
        entry_date = data.get("entry_date")
        if isinstance(entry_date, dt.datetime):
            entry_date = entry_date.date()
        if not isinstance(entry_date, dt.date):
            raise BusinessError("تاريخ القيد مطلوب.")
        description = " ".join(str(data.get("description") or "").split())
        if not description:
            raise BusinessError("بيان القيد مطلوب (القيد ده عن إيه).")
        if len(description) > 500:
            raise BusinessError("«البيان» أطول من المسموح (500 حرف).")
        reference = " ".join(str(data.get("reference") or "").split()) or None
        if reference and len(reference) > 100:
            raise BusinessError("«المرجع» أطول من المسموح (100 حرف).")
        lines: list[dict[str, Any]] = []
        for raw in data.get("lines") or []:
            account_id = self._id(raw.get("account_id"))
            n = len(lines) + 1
            debit = parse_money(raw.get("debit"), f"مدين السطر {n}")
            credit = parse_money(raw.get("credit"), f"دائن السطر {n}")
            text = " ".join(str(raw.get("description") or "").split()) or None
            if account_id is None and not debit and not credit and not text:
                continue                                           # سطر فاضي خالص ← بيتشال
            if account_id is None:
                raise BusinessError(f"السطر {n}: اختار الحساب.")
            account = accounts.get(account_id)
            if account is None:
                raise BusinessError(f"السطر {n}: الحساب لازم يبقى فرعي (ممكن يكون اتحوّل رئيسي) — دوس «تحديث».")
            if debit and credit:
                raise BusinessError(f"السطر {n}: يا مدين يا دائن — مش الاتنين في نفس السطر.")
            if not debit and not credit:
                raise BusinessError(f"السطر {n}: اكتب المبلغ في المدين أو الدائن.")
            if text and len(text) > 300:
                raise BusinessError(f"السطر {n}: البيان أطول من المسموح (300 حرف).")
            line = {"line_no": n, "account_id": account_id, "description": text, "debit": debit, "credit": credit,
                    "cost_center_id": self._id(raw.get("cost_center_id")),
                    "project_id": self._id(raw.get("project_id")),
                    **{col: None for col in PARTY_COLUMNS.values()}}
            party_id = self._id(raw.get("party_id"))
            kind = account.get("party_kind")
            if kind:
                if party_id is None:
                    raise BusinessError(f"السطر {n}: الحساب «{account['code']} {account['name_ar']}» حساب رقابة — "
                                        f"لازم تختار {PARTY_LABELS[kind]}.")
                line[PARTY_COLUMNS[kind]] = party_id
            if account.get("requires_cost_center") and line["cost_center_id"] is None:
                raise BusinessError(f"السطر {n}: الحساب «{account['code']} {account['name_ar']}» لازم يتكتب عليه "
                                    "مركز تكلفة.")
            lines.append(line)
        if not lines:
            raise BusinessError("ضيف سطر واحد على الأقل في القيد.")
        if len(lines) > MAX_LINES:
            raise BusinessError(f"أقصى عدد سطور في القيد {MAX_LINES}.")
        return {"entry_date": entry_date, "description": description, "reference": reference, "lines": lines}

    @staticmethod
    def totals(lines: list[dict[str, Any]]) -> tuple[Decimal, Decimal]:
        """(إجمالي المدين، إجمالي الدائن) — للشاشة وهي بتكتب."""
        d = sum((parse_money(r.get("debit"), "مدين") for r in lines), Decimal("0"))
        c = sum((parse_money(r.get("credit"), "دائن") for r in lines), Decimal("0"))
        return d, c

    def _lock(self, tx, entry_id: int, expected_version: int | None, *, draft_only: bool = False,
              posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_entry(int(entry_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=entry_id)
        if before is None:
            raise BusinessError(NOT_FOUND)
        if before["source_type"] != "manual":
            raise BusinessError(AUTO_READ_ONLY.format(no=before["source_no"] or before["source_type"]))
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — قيودها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"القيد {entry_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"القيد {entry_label(before)} مش مرحّل.")
        before["lines"] = self.repository.entry_lines(int(entry_id), ex=tx)
        return before

    def _after(self, tx, entry_id: int) -> dict[str, Any]:
        after = self.repository.get_entry(int(entry_id), ex=tx)
        after["lines"] = self.repository.entry_lines(int(entry_id), ex=tx)
        return after

    def _record(self, tx, action: str, entry_id: int, before: dict | None, after: dict | None) -> None:
        label = entry_label(after or before or {})
        desc = (after or before or {}).get("description") or ""
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=entry_id, entity_label=ENTITY_LABEL,
                          record_label=f"{label} — {desc}",
                          before={k: v for k, v in (before or {}).items() if k != "lines"} if before else None,
                          after={k: v for k, v in (after or {}).items() if k != "lines"} if after else None,
                          lines={"acc.journal_lines": {"before": (before or {}).get("lines") or [],
                                                       "after": (after or {}).get("lines") or []}})

    # ---- حفظ مسودة / ترحيل / إلغاء ترحيل / مسح -------------------------------------
    def save_draft(self, entry_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                   post: bool = False) -> int:
        """يحفظ المسودة (ولو ``post`` يرحّلها في نفس الـ transaction — يا الاتنين يا ولا حاجة)."""
        PermissionService.require(f"{PERM}.create" if entry_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        accounts = {a["id"]: a for a in self.repository.account_choices()}
        clean = self._clean(data, accounts)
        try:
            with self.db.atomic() as tx:
                before = None
                if entry_id is None:
                    entry_id = self.repository.insert_entry({**clean, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock(tx, int(entry_id), expected_version, draft_only=True)
                    updated = self.repository.update_entry(int(entry_id), clean, ex=tx,
                                                           expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=entry_id)
                self.repository.replace_lines(int(entry_id), clean["lines"], ex=tx)
                after = self._after(tx, int(entry_id))
                self._record(tx, "create" if before is None else "update", int(entry_id), before, after)
                if post:
                    self.repository.set_status(int(entry_id), "posted", SESSION.user_id, ex=tx)
                    posted = self._after(tx, int(entry_id))
                    self._record(tx, "post", int(entry_id), after, posted)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(entry_id)

    def post(self, entry_id: int, expected_version: int | None = None) -> str:
        """ترحيل مسودة محفوظة ← بيرجّع الرقم الرسمي."""
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(entry_id), expected_version, draft_only=True)
                updated = self.repository.set_status(int(entry_id), "posted", SESSION.user_id, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=entry_id)
                after = self._after(tx, int(entry_id))
                self._record(tx, "post", int(entry_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["entry_no"]

    def unpost(self, entry_id: int, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): القيد بيرجع مسودة برقمه وسطوره."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(entry_id), expected_version, posted_only=True)
                updated = self.repository.set_status(int(entry_id), "draft", None, ex=tx,
                                                     expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=entry_id)
                after = self._after(tx, int(entry_id))
                self._record(tx, "unpost", int(entry_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_entry(self, entry_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx, int(entry_id), expected_version, draft_only=True)
                if before["entry_no"]:
                    raise BusinessError(f"القيد {before['entry_no']} خد رقم رسمي قبل كده — مايتمسحش عشان الأرقام "
                                        "ماتعملش فجوة (عدّله ورحّله تاني).")
                deleted = self.repository.delete_entry(int(entry_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=entry_id)
                self._record(tx, "delete", int(entry_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
