"""«العهد» — المرحلة 5، النموذج 1 «الكلاسيك» + تعديلات المستخدم (2026-11-18).

* **صرف عهدة** لموظف عليه دور «عهدة» من خزنة أو حساب بنك ← مدين «عهد الموظفين» (بُعد الموظف) / دائن الخزنة أو البنك.
  ونوع تاني **«سداد اللي ليه»**: الموظف صرف من جيبه في تسوية قبل كده ← بيتصرفله بنفس القيد، ومايعدّيش اللي ليه.
* **تسوية عهدة** = طريقين (رد المستخدم): **بنود مصروفات** (نوع المصروف · البيان · أمر التوريد · المبلغ) و/أو
  **فلوس راجعة** لخزنة أو حساب بنك — والموظف بيعلّم على **العهد المفتوحة** اللي بيسوّيها (واحدة أو أكتر).
  القيد: مدين حساب كل نوع مصروف (بأمر التوريد ومركز تكلفته) + مدين الخزنة / البنك بالمرتجع / دائن «عهد الموظفين».
* **وقت ترحيل التسوية** الإجمالي بيتوزّع على العهد المختارة **الأقدم الأول** لحد الباقي من كل واحدة،
  و**الزيادة = «ليه»** (رد المستخدم: بتتسجل وتتحسب للموظف في كشف حسابه) · المرتجع مايعدّيش اللي اتوزّع.
* مسودة + ترحيل + إلغاء ترحيل زي السندات · الرقم ``CD-2026-00001`` / ``CS-2026-00001`` مع أول ترحيل ·
  صرف عهدة اتسوّى منها مايتلغاش ترحيله · **قفل لكل موظف** (advisory) + قفل صفوف العهد جوه نفس الـ transaction
  عشان جهازين يرحّلوا تسويتين لنفس الموظف في نفس اللحظة من غير ما العهدة تتسوّى مرتين.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.custody_repository import STATUSES, CustodyRepository
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
DOC_DISB, DOC_SETTLE = "custody_disb", "custody_settle"
PERM = "hr.custody"
KINDS = {"disb": ("صرف عهدة", "cash.custody_disbursements", "صرف عهدة"),
         "settle": ("تسوية عهدة", "cash.custody_settlements", "تسوية عهدة")}
PURPOSES = {"custody": "عهدة جديدة", "payback": "سداد اللي ليه"}
SOURCE_KINDS = ("treasury", "bank")
LINE_KINDS = {"expense": "مصروف", "return": "مرتجع"}


def disb_label(row: dict[str, Any]) -> str:
    return row.get("disb_no") or f"مسودة {row.get('draft_no')}"


def settle_label(row: dict[str, Any]) -> str:
    return row.get("settle_no") or f"مسودة {row.get('draft_no')}"


def fmt(amount: Any) -> str:
    return f"{Decimal(amount or 0):,.2f}"


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


def _id(value: Any) -> int | None:
    return int(value) if value not in (None, "", 0) else None


def _text(value: Any, label: str, limit: int, required: bool = False) -> str | None:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise BusinessError(f"«{label}» مطلوب.")
    if len(text) > limit:
        raise BusinessError(f"«{label}» أطول من المسموح ({limit} حرف).")
    return text or None


def _date(value: Any, label: str) -> dt.date:
    if isinstance(value, dt.datetime):
        value = value.date()
    if not isinstance(value, dt.date):
        raise BusinessError(f"{label} مطلوب.")
    return value


class CustodyService:
    def __init__(self, db: Database | None = None, repository: CustodyRepository | None = None,
                 cash: VoucherRepository | None = None, audit: AuditService | None = None,
                 posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or CustodyRepository(self.db)
        self.cash = cash or VoucherRepository(self.db)          # الخزائن والبنوك وأرصدتها (نفس دوال السندات)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- الاختيارات والملخص ----------------------------------------------------------
    def choices(self) -> dict[str, Any]:
        """الموظفين (اللي عليهم دور «عهدة») · الخزائن · الحسابات البنكية · أنواع المصروفات · أوامر التوريد."""
        return {"employees": self.repository.employee_choices(), "treasuries": self.cash.treasury_choices(),
                "banks": self.cash.bank_account_choices(), "expense_types": self.repository.expense_type_choices(),
                "projects": self.repository.project_choices()}

    def open_custodies(self, employee_id: int) -> list[dict[str, Any]]:
        """العهد المفتوحة بس (الأقدم الأول) + الباقي من كل واحدة — للواجهة المنبثقة."""
        rows = self.repository.open_custodies(int(employee_id))
        for row in rows:
            row["remaining"] = Decimal(row["amount"]) - Decimal(row["used_amount"])
        return rows

    def employee_summary(self, employee_id: int | None) -> dict[str, Any] | None:
        """كارت الموظف: العهد المفتوحة ومجموعها · اللي ليه · رصيده في القيود."""
        if not employee_id:
            return None
        rows = self.open_custodies(int(employee_id))
        return {"open": rows, "open_total": sum((r["remaining"] for r in rows), ZERO),
                "credit_due": self.repository.credit_due(int(employee_id)),
                "balance": self.repository.ledger_balance(int(employee_id))}

    # ---- القوائم والمستندات ---------------------------------------------------------
    def list_docs(self, kind: str, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                  keyword: str = "", employee_id: int | None = None, page: int = 1,
                  page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_docs(self._kind(kind), date_from, date_to, status, keyword.strip(),
                                         _id(employee_id), limit=page_size, offset=offset)

    def count_docs(self, kind: str, date_from: dt.date | None, date_to: dt.date | None, status: str = "all",
                   keyword: str = "", employee_id: int | None = None) -> dict[str, Any]:
        status = status if status in STATUSES else "all"
        row = self.repository.count_docs(self._kind(kind), date_from, date_to, status, keyword.strip(),
                                         _id(employee_id))
        return {"total": int(row["total"]), "drafts": int(row["drafts"]), "posted": int(row["posted"]),
                "posted_amount": Decimal(row["posted_amount"])}

    @staticmethod
    def _kind(kind: str) -> str:
        if kind not in KINDS:
            raise BusinessError("نوع المستند لازم «صرف عهدة» أو «تسوية عهدة».")
        return kind

    def get_disbursement(self, disb_id: int) -> dict[str, Any] | None:
        row = self.repository.get_disbursement(int(disb_id))
        if row is not None:
            row["label"] = disb_label(row)
            row["settlements"] = self.repository.disbursement_settlements(int(disb_id))
        return row

    def get_settlement(self, settle_id: int) -> dict[str, Any] | None:
        row = self.repository.get_settlement(int(settle_id))
        if row is not None:
            row["label"] = settle_label(row)
            row["lines"] = self.repository.settlement_lines(int(settle_id))
            row["links"] = self.repository.settlement_links(int(settle_id))
        return row

    def entry_for(self, kind: str, doc_id: int) -> dict[str, Any] | None:
        return self.posting.entry_for(DOC_DISB if self._kind(kind) == "disb" else DOC_SETTLE, int(doc_id))

    def _names(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        names = self.cash.accounts([ln["account_id"] for ln in lines])
        return [{"account_id": ln["account_id"], "account_code": names[ln["account_id"]]["code"],
                 "account_name": names[ln["account_id"]]["name_ar"], "debit": ln["debit"], "credit": ln["credit"],
                 "role_name": ln["role_name"], "description": ln["description"]} for ln in lines]

    def _record(self, tx: Executor, kind: str, action: str, doc_id: int, before: dict | None, after: dict | None,
                lines: dict | None = None) -> None:
        row = after or before or {}
        label = disb_label(row) if kind == "disb" else settle_label(row)
        _name, entity, entity_label = KINDS[kind]
        self.audit.record(tx, action=action, entity=entity, entity_id=doc_id, entity_label=entity_label,
                          record_label=f"{label} — {row.get('employee_name') or ''} — {fmt(row.get('amount'))}",
                          before=before, after=after, lines=lines)

    # ==== صرف العهدة ===================================================================
    def _clean_disb(self, data: dict[str, Any]) -> dict[str, Any]:
        disb_date = _date(data.get("disb_date"), "تاريخ الصرف")
        employee_id = _id(data.get("employee_id"))
        if employee_id is None:
            raise BusinessError("اختار الموظف.")
        purpose = data.get("purpose") or "custody"
        if purpose not in PURPOSES:
            raise BusinessError("نوع الصرف لازم «عهدة جديدة» أو «سداد اللي ليه».")
        source_kind = data.get("source_kind") or "treasury"
        if source_kind not in SOURCE_KINDS:
            raise BusinessError("«الصرف من»: اختار خزنة أو بنك.")
        treasury_id = _id(data.get("treasury_id")) if source_kind == "treasury" else None
        bank_id = _id(data.get("bank_account_id")) if source_kind == "bank" else None
        if source_kind == "treasury" and treasury_id is None:
            raise BusinessError("«الصرف من»: اختار الخزنة.")
        if source_kind == "bank" and bank_id is None:
            raise BusinessError("«الصرف من»: اختار الحساب البنكي.")
        amount = parse_money(data.get("amount"), "المبلغ")
        if amount <= 0:
            raise BusinessError("اكتب مبلغ الصرف.")
        description = _text(data.get("description"), "البيان", 500) or PURPOSES[purpose]
        return {"disb_date": disb_date, "employee_id": employee_id, "purpose": purpose, "source_kind": source_kind,
                "treasury_id": treasury_id, "bank_account_id": bank_id, "amount": amount,
                "project_id": _id(data.get("project_id")) if purpose == "custody" else None,
                "description": description, "reference_no": _text(data.get("reference_no"), "رقم الإيصال", 40)}

    @staticmethod
    def _disb_lines(v: dict[str, Any]) -> list[PostingLine]:
        amount = Decimal(v["amount"])
        source = (PostingLine("cash", amount, treasury_id=v["treasury_id"]) if v["source_kind"] == "treasury"
                  else PostingLine("bank", amount, bank_account_id=v["bank_account_id"]))
        return [PostingLine("custody", amount, party=("employee", int(v["employee_id"]))), source]

    def preview_disbursement(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """سطور القيد اللي هيتعمل — أي نقص ← ``BusinessError`` بالعربي."""
        v = self._clean_disb(data)
        return self._names(self.posting.build(DOC_DISB, self._disb_lines(v)))

    def source_balance(self, kind: str, source_id: Any) -> dict[str, Any] | None:
        """رصيد الخزنة / الحساب البنكي دلوقتي (+ حد الجاري المدين)."""
        source_id = _id(source_id)
        if source_id is None:
            return None
        rows = self.cash.treasury_choices() if kind == "treasury" else self.cash.bank_account_choices()
        row = next((r for r in rows if r["id"] == source_id), None)
        if row is None:
            return None
        return {"name": row.get("name_ar") or f"{row.get('bank_name')} — {row.get('code')}",
                "balance": self.cash.account_balance(int(row["account_id"])),
                "overdraft": Decimal(row.get("overdraft_limit") or 0)}

    def funds_warning(self, data: dict[str, Any]) -> str | None:
        """الخزنة / البنك مش مكفي ← رسالة تحذير بس (زي السندات)."""
        v = self._clean_disb(data)
        b = self.source_balance(v["source_kind"], v["treasury_id"] or v["bank_account_id"])
        if b is None or b["balance"] + b["overdraft"] - v["amount"] >= 0:
            return None
        what = "الخزنة" if v["source_kind"] == "treasury" else "الحساب البنكي"
        extra = f" (+ حد الجاري المدين {fmt(b['overdraft'])})" if b["overdraft"] else ""
        return (f"رصيد {what} «{b['name']}» {fmt(b['balance'])}{extra} — والصرف {fmt(v['amount'])}، "
                f"فالرصيد هيبقى {fmt(b['balance'] - v['amount'])}.")

    def _check_payback(self, tx: Executor, v: dict[str, Any]) -> None:
        if v["purpose"] != "payback":
            return
        due = self.repository.credit_due(int(v["employee_id"]), ex=tx)
        if due <= 0:
            raise BusinessError("الموظف ده مالوش حاجة عندنا — «سداد اللي ليه» بيبقى بعد تسوية صرف فيها من جيبه.")
        if Decimal(v["amount"]) > due:
            raise BusinessError(f"اللي ليه عند الموظف {fmt(due)} بس — مينفعش يتسددله {fmt(v['amount'])}.")

    def _lock_disb(self, tx: Executor, disb_id: int, expected_version: int | None, *, draft_only: bool = False,
                   posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_disbursement(int(disb_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=KINDS["disb"][1], entity_id=disb_id)
        if before is None:
            raise BusinessError("صرف العهدة ده مش موجود (ممكن يكون اتمسح).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — مستنداتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"صرف العهدة {disb_label(before)} مرحّل — الغي ترحيله الأول عشان تعدّله.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"صرف العهدة {disb_label(before)} مش مرحّل.")
        return before

    def _post_disb(self, tx: Executor, disb_id: int, before: dict[str, Any],
                   expected_version: int | None = None) -> dict[str, Any]:
        self.repository.lock_employee(int(before["employee_id"]), tx)
        self._check_payback(tx, before)
        updated = self.repository.set_disbursement_status(disb_id, "posted", SESSION.user_id, ex=tx,
                                                          expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=KINDS["disb"][1], entity_id=disb_id)
        v = self.repository.get_disbursement(disb_id, ex=tx)
        what = PURPOSES[v["purpose"]]
        self.posting.post_document(tx, doc_type=DOC_DISB, source_id=disb_id, source_no=v["disb_no"],
                                   entry_date=v["disb_date"],
                                   description=f"{what} — {v['employee_name']} — {v['description']}",
                                   reference=v["reference_no"], lines=self._disb_lines(v))
        self._record(tx, "disb", "post", disb_id, before, v)
        return v

    def save_disbursement(self, disb_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                          post: bool = False) -> int:
        """يحفظ المسودة (ولو ``post`` يرحّلها في نفس الـ transaction)."""
        PermissionService.require(f"{PERM}.create" if disb_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        clean = self._clean_disb(data)
        try:
            with self.db.atomic() as tx:
                before = None
                if clean["purpose"] == "payback":
                    self._check_payback(tx, clean)
                if disb_id is None:
                    disb_id = self.repository.insert_disbursement({**clean, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock_disb(tx, int(disb_id), expected_version, draft_only=True)
                    updated = self.repository.update_disbursement(int(disb_id), clean, ex=tx,
                                                                  expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=KINDS["disb"][1], entity_id=disb_id)
                after = self.repository.get_disbursement(int(disb_id), ex=tx)
                self._record(tx, "disb", "create" if before is None else "update", int(disb_id), before, after)
                if post:
                    self._post_disb(tx, int(disb_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(disb_id)

    def post_disbursement(self, disb_id: int, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock_disb(tx, int(disb_id), expected_version, draft_only=True)
                after = self._post_disb(tx, int(disb_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["disb_no"]

    def unpost_disbursement(self, disb_id: int, expected_version: int | None = None) -> None:
        """القيد بيتمسح والصرف بيرجع مسودة برقمه — ممنوع لو اتسوّى منه بتسوية مرحّلة."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                before = self._lock_disb(tx, int(disb_id), expected_version, posted_only=True)
                self.repository.lock_employee(int(before["employee_id"]), tx)
                used = [s for s in self.repository.disbursement_settlements(int(disb_id), ex=tx)
                        if s["status"] == "posted"]
                if used:
                    raise BusinessError(f"صرف العهدة {disb_label(before)} اتسوّى منه بالتسوية "
                                        f"{used[0]['settle_no']} — الغي ترحيل التسوية الأول.")
                self.posting.unpost_document(tx, doc_type=DOC_DISB, source_id=int(disb_id))
                updated = self.repository.set_disbursement_status(int(disb_id), "draft", None, ex=tx,
                                                                  expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=KINDS["disb"][1], entity_id=disb_id)
                after = self.repository.get_disbursement(int(disb_id), ex=tx)
                self._record(tx, "disb", "unpost", int(disb_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_disbursement(self, disb_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                before = self._lock_disb(tx, int(disb_id), expected_version, draft_only=True)
                if before["disb_no"]:
                    raise BusinessError(f"صرف العهدة {before['disb_no']} خد رقم رسمي قبل كده — مايتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّله ورحّله تاني).")
                deleted = self.repository.delete_disbursement(int(disb_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=KINDS["disb"][1], entity_id=disb_id)
                self._record(tx, "disb", "delete", int(disb_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    # ==== التسوية ======================================================================
    def _clean_line(self, line: dict[str, Any], n: int, types: dict[int, dict] | None = None) -> dict[str, Any]:
        kind = line.get("line_kind")
        if kind not in LINE_KINDS:
            raise BusinessError(f"السطر {n}: لازم «بند مصروف» أو «فلوس راجعة».")
        amount = parse_money(line.get("amount"), f"مبلغ السطر {n}")
        if amount <= 0:
            raise BusinessError(f"السطر {n}: اكتب المبلغ.")
        out: dict[str, Any] = {"line_no": n, "line_kind": kind, "amount": amount, "expense_type_id": None,
                               "project_id": None, "return_kind": None, "treasury_id": None, "bank_account_id": None,
                               "description": _text(line.get("description"), f"بيان السطر {n}", 300)}
        if kind == "expense":
            type_id = _id(line.get("expense_type_id"))
            if type_id is None:
                raise BusinessError(f"السطر {n}: اختار نوع المصروف.")
            out["expense_type_id"] = type_id
            out["project_id"] = _id(line.get("project_id"))
            if types is not None:
                t = types.get(type_id)
                if t is None:
                    raise BusinessError(f"السطر {n}: نوع المصروف مش موجود (ممكن يكون اتمسح).")
                if t["requires_cost_center"] and out["project_id"] is None:
                    raise BusinessError(f"السطر {n}: حساب «{t['name_ar']}» ({t['account_code']}) لازم يتكتب عليه "
                                        "مركز تكلفة — اختار أمر التوريد.")
        else:
            rk = line.get("return_kind") or "treasury"
            if rk not in SOURCE_KINDS:
                raise BusinessError(f"السطر {n}: الفلوس راجعة لخزنة ولا بنك؟")
            out["return_kind"] = rk
            if rk == "treasury":
                out["treasury_id"] = _id(line.get("treasury_id"))
                if out["treasury_id"] is None:
                    raise BusinessError(f"السطر {n}: اختار الخزنة اللي الفلوس رجعت فيها.")
            else:
                out["bank_account_id"] = _id(line.get("bank_account_id"))
                if out["bank_account_id"] is None:
                    raise BusinessError(f"السطر {n}: اختار الحساب البنكي اللي الفلوس رجعت فيه.")
        return out

    def check_line(self, line: dict[str, Any]) -> dict[str, Any]:
        """تحقق سطر واحد من الواجهة المنبثقة قبل ما يتضاف (نوع المصروف · مركز التكلفة · المبلغ)."""
        types = {int(t["id"]): t for t in self.repository.expense_type_choices()}
        return self._clean_line(line, 1, types)

    def _clean_settle(self, data: dict[str, Any]) -> dict[str, Any]:
        settle_date = _date(data.get("settle_date"), "تاريخ التسوية")
        employee_id = _id(data.get("employee_id"))
        if employee_id is None:
            raise BusinessError("اختار الموظف.")
        types = {int(t["id"]): t for t in self.repository.expense_type_choices()}
        lines = [self._clean_line(line, n, types) for n, line in enumerate(data.get("lines") or [], start=1)]
        if not lines:
            raise BusinessError("ضيف بند مصروف أو فلوس راجعة — التسوية فاضية.")
        if len(lines) > 500:
            raise BusinessError("سطور التسوية كتير أوي (500 بالكتير).")
        ids: list[int] = []
        for value in data.get("custody_ids") or []:
            value = _id(value)
            if value is not None and value not in ids:
                ids.append(value)
        expense = sum((ln["amount"] for ln in lines if ln["line_kind"] == "expense"), ZERO)
        returned = sum((ln["amount"] for ln in lines if ln["line_kind"] == "return"), ZERO)
        description = _text(data.get("description"), "البيان", 500) or "تسوية عهدة"
        return {"settle_date": settle_date, "employee_id": employee_id, "description": description,
                "reference_no": _text(data.get("reference_no"), "رقم الإيصال", 40), "lines": lines,
                "custody_ids": ids, "expense_total": expense, "return_total": returned}

    def _settle_lines(self, tx: Executor | None, v: dict[str, Any]) -> list[PostingLine]:
        lines = v["lines"]
        centers = self.repository.project_cost_centers(
            sorted({ln["project_id"] for ln in lines if ln.get("project_id")}), ex=tx)
        out: list[PostingLine] = []
        for ln in lines:
            amount = Decimal(ln["amount"])
            if ln["line_kind"] == "expense":
                out.append(PostingLine("expense", amount, expense_type_id=ln["expense_type_id"],
                                       project_id=ln.get("project_id"),
                                       cost_center_id=centers.get(ln.get("project_id")) if ln.get("project_id") else None,
                                       description=ln.get("description")))
            elif ln["return_kind"] == "treasury":
                out.append(PostingLine("cash", amount, treasury_id=ln["treasury_id"],
                                       description=ln.get("description") or "مرتجع عهدة"))
            else:
                out.append(PostingLine("bank", amount, bank_account_id=ln["bank_account_id"],
                                       description=ln.get("description") or "مرتجع عهدة"))
        total = sum((Decimal(ln["amount"]) for ln in lines), ZERO)
        out.append(PostingLine("custody", total, party=("employee", int(v["employee_id"]))))
        return out

    @staticmethod
    def _allocate(rows: list[dict[str, Any]], total: Decimal) -> tuple[list[dict[str, Any]], Decimal]:
        """الإجمالي على العهد المختارة الأقدم الأول لحد الباقي من كل واحدة ← (التوزيع، الزيادة = «ليه»)."""
        left = Decimal(total)
        out = []
        for row in sorted(rows, key=lambda r: (r["disb_date"], r["id"])):
            remaining = Decimal(row["amount"]) - Decimal(row["used_amount"])
            take = min(max(remaining, ZERO), left)
            out.append({**row, "remaining": remaining, "allocated": take,
                        "closes": remaining > 0 and take == remaining})
            left -= take
        return out, left

    def allocation_preview(self, employee_id: Any, custody_ids: list[int], total: Any,
                           returned: Any = 0) -> dict[str, Any]:
        """إزاي التسوية هتتوزّع على العهد اللي اتعلّم عليها (للكارت وهو بيكتب)."""
        employee_id = _id(employee_id)
        rows = [r for r in (self.open_custodies(employee_id) if employee_id else []) if r["id"] in set(custody_ids)]
        allocation, excess = self._allocate(rows, Decimal(total or 0))
        allocated = sum((r["allocated"] for r in allocation), ZERO)
        return {"rows": allocation, "allocated": allocated, "excess": excess,
                "selected_open": sum((r["remaining"] for r in allocation), ZERO),
                "return_over": max(ZERO, Decimal(returned or 0) - allocated)}

    def preview_settlement(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        v = self._clean_settle(data)
        return self._names(self.posting.build(DOC_SETTLE, self._settle_lines(None, v)))

    def _check_custodies(self, tx: Executor, employee_id: int, ids: list[int]) -> list[dict[str, Any]]:
        rows = self.repository.lock_disbursements(ids, tx)
        found = {int(r["id"]) for r in rows}
        if len(found) != len(ids):
            raise BusinessError("فيه عهدة من اللي اخترتها مش موجودة (ممكن تكون اتمسحت) — اختار العهد تاني.")
        for row in rows:
            label = disb_label(row)
            if int(row["employee_id"]) != int(employee_id):
                raise BusinessError(f"العهدة {label} مش بتاعة الموظف ده.")
            if row["purpose"] != "custody":
                raise BusinessError(f"{label} سداد للموظف مش عهدة — ماتتسوّاش.")
            if row["status"] != "posted":
                raise BusinessError(f"العهدة {label} مش مرحّلة — رحّلها الأول أو شيلها من التسوية.")
            if Decimal(row["amount"]) - Decimal(row["used_amount"]) <= 0:
                raise BusinessError(f"العهدة {label} اتسوّت كلها (ممكن من جهاز تاني) — شيلها من التسوية.")
        return rows

    def _lock_settle(self, tx: Executor, settle_id: int, expected_version: int | None, *, draft_only: bool = False,
                     posted_only: bool = False) -> dict[str, Any]:
        before = self.repository.get_settlement(int(settle_id), ex=tx, for_update=True)
        self.guard.check(tx, before, expected_version, entity=KINDS["settle"][1], entity_id=settle_id)
        if before is None:
            raise BusinessError("التسوية دي مش موجودة (ممكن تكون اتمسحت).")
        if before["year_status"] == "closed":
            raise BusinessError(f"السنة المالية {before['year_code']} مقفولة — مستنداتها ماتتعدّلش.")
        if draft_only and before["status"] != "draft":
            raise BusinessError(f"التسوية {settle_label(before)} مرحّلة — الغي ترحيلها الأول عشان تعدّلها.")
        if posted_only and before["status"] != "posted":
            raise BusinessError(f"التسوية {settle_label(before)} مش مرحّلة.")
        return before

    def _snapshot(self, tx: Executor, settle_id: int) -> dict[str, Any]:
        row = self.repository.get_settlement(settle_id, ex=tx)
        row["lines"] = self.repository.settlement_lines(settle_id, ex=tx)
        row["links"] = self.repository.settlement_links(settle_id, ex=tx)
        return row

    def _record_settle(self, tx: Executor, action: str, settle_id: int, before: dict | None,
                       after: dict | None) -> None:
        def head(row: dict | None) -> dict | None:
            return {k: v for k, v in row.items() if k not in ("lines", "links")} if row else None

        self._record(tx, "settle", action, settle_id, head(before), head(after),
                     lines={"cash.custody_settlement_lines": {"before": (before or {}).get("lines") or [],
                                                              "after": (after or {}).get("lines") or []},
                            "cash.custody_settlement_links": {"before": (before or {}).get("links") or [],
                                                              "after": (after or {}).get("links") or []}})

    def _post_settle(self, tx: Executor, settle_id: int, before: dict[str, Any],
                     expected_version: int | None = None) -> dict[str, Any]:
        """قفل الموظف ← العهد المختارة بقفل صف ← التوزيع الأقدم الأول ← الحالة ← القيد — كله في نفس الـ transaction."""
        employee_id = int(before["employee_id"])
        self.repository.lock_employee(employee_id, tx)
        links = before.get("links")
        if links is None:
            links = self.repository.settlement_links(settle_id, ex=tx)
        rows = self._check_custodies(tx, employee_id, [int(r["id"]) for r in links])
        total = Decimal(before["expense_total"]) + Decimal(before["return_total"])
        allocation, excess = self._allocate(rows, total)
        allocated = sum((r["allocated"] for r in allocation), ZERO)
        if Decimal(before["return_total"]) > allocated:
            raise BusinessError(f"الفلوس الراجعة ({fmt(before['return_total'])}) أكبر من الباقي في العهد اللي "
                                f"اخترتها ({fmt(allocated)}) — الموظف مايرجّعش أكتر من اللي معاه.")
        for row in allocation:
            self.repository.set_allocation(settle_id, int(row["id"]), row["allocated"], tx)
        updated = self.repository.set_settlement_status(settle_id, "posted", SESSION.user_id, ex=tx,
                                                        allocated=allocated, excess=excess,
                                                        expected_version=expected_version)
        self.guard.ensure(tx, updated, expected_version, entity=KINDS["settle"][1], entity_id=settle_id)
        v = self._snapshot(tx, settle_id)
        self.posting.post_document(tx, doc_type=DOC_SETTLE, source_id=settle_id, source_no=v["settle_no"],
                                   entry_date=v["settle_date"],
                                   description=f"تسوية عهدة — {v['employee_name']} — {v['description']}",
                                   reference=v["reference_no"], lines=self._settle_lines(tx, v))
        self._record_settle(tx, "post", settle_id, before, v)
        return v

    def save_settlement(self, settle_id: int | None, data: dict[str, Any], expected_version: int | None = None,
                        post: bool = False) -> int:
        PermissionService.require(f"{PERM}.create" if settle_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        clean = self._clean_settle(data)
        head = {k: clean[k] for k in ("settle_date", "employee_id", "description", "reference_no", "expense_total",
                                      "return_total")}
        try:
            with self.db.atomic() as tx:
                before = None
                if settle_id is None:
                    settle_id = self.repository.insert_settlement({**head, "user_id": SESSION.user_id}, ex=tx)
                else:
                    before = self._lock_settle(tx, int(settle_id), expected_version, draft_only=True)
                    before["lines"] = self.repository.settlement_lines(int(settle_id), ex=tx)
                    before["links"] = self.repository.settlement_links(int(settle_id), ex=tx)
                    updated = self.repository.update_settlement(int(settle_id), head, ex=tx,
                                                                expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=KINDS["settle"][1], entity_id=settle_id)
                if clean["custody_ids"]:
                    self._check_custodies(tx, clean["employee_id"], clean["custody_ids"])
                self.repository.replace_children(int(settle_id), clean["lines"], clean["custody_ids"], ex=tx)
                after = self._snapshot(tx, int(settle_id))
                self._record_settle(tx, "create" if before is None else "update", int(settle_id), before, after)
                if post:
                    self._post_settle(tx, int(settle_id), after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return int(settle_id)

    def post_settlement(self, settle_id: int, expected_version: int | None = None) -> str:
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                self._lock_settle(tx, int(settle_id), expected_version, draft_only=True)
                before = self._snapshot(tx, int(settle_id))
                after = self._post_settle(tx, int(settle_id), before, expected_version)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return after["settle_no"]

    def unpost_settlement(self, settle_id: int, expected_version: int | None = None) -> None:
        """القيد بيتمسح والتسوية ترجع مسودة والعهد اللي قفلتها تتفتح تاني — ممنوع لو الزيادة اتسددت للموظف."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                head = self._lock_settle(tx, int(settle_id), expected_version, posted_only=True)
                self.repository.lock_employee(int(head["employee_id"]), tx)
                excess = Decimal(head["excess_amount"])
                if excess > 0 and self.repository.credit_due(int(head["employee_id"]), ex=tx) - excess < 0:
                    raise BusinessError(f"الزيادة اللي في التسوية {settle_label(head)} ({fmt(excess)}) اتسددت "
                                        "للموظف — الغي ترحيل «سداد اللي ليه» الأول.")
                before = self._snapshot(tx, int(settle_id))
                self.posting.unpost_document(tx, doc_type=DOC_SETTLE, source_id=int(settle_id))
                updated = self.repository.set_settlement_status(int(settle_id), "draft", None, ex=tx,
                                                                expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=KINDS["settle"][1], entity_id=settle_id)
                self.repository.reset_allocation(int(settle_id), ex=tx)
                after = self._snapshot(tx, int(settle_id))
                self._record_settle(tx, "unpost", int(settle_id), before, after)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def delete_settlement(self, settle_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        try:
            with self.db.atomic() as tx:
                head = self._lock_settle(tx, int(settle_id), expected_version, draft_only=True)
                if head["settle_no"]:
                    raise BusinessError(f"التسوية {head['settle_no']} خدت رقم رسمي قبل كده — ماتتمسحش "
                                        "عشان الأرقام ماتعملش فجوة (عدّلها ورحّلها تاني).")
                before = self._snapshot(tx, int(settle_id))
                deleted = self.repository.delete_settlement(int(settle_id), ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, deleted, expected_version, entity=KINDS["settle"][1], entity_id=settle_id)
                self._record_settle(tx, "delete", int(settle_id), before, None)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
