"""مستند «الأرصدة الافتتاحية» (المرحلة 4 — النموذج 2 «شريط الأقسام + كل الأطراف جاهزة»، اختيار المستخدم 2026-11-17).

ردود المستخدم (2026-11-17):
* **مستند واحد بس** للبرنامج كله، بالتاريخ اللي المستخدم بيكتبه ويعدّله (الافتراضي يوم ما اتعمل).
* السطر = حساب فرعي + طرف (لو رقابة) + مدين **أو** دائن · **الفرق لوحده على «34 أرصدة افتتاحية»** مع الترحيل.
* الافتراضي حسابات الميزانية — وتشيك بوكس «كل الحسابات» في الشاشة بيفتح الإيرادات والمصروفات · بالجنيه.
* «حفظ مسودة» + «ترحيل» + «إلغاء الترحيل» **بالصلاحية بس** (زي القيد اليدوي).
* **خانة «رصيد أول المدة» في شاشتي العملاء والموردين** (``set_party_balance``) بتعدّل **نفس السطر** — والطرف ليه
  سطر واحد بس على حساب رقابته (مايتكررش). بعد الترحيل الخانة بتتقفل.

**جهازين على نفس المستند (10–20 جهاز):** كل تغيير في سطر بييجي معاه **القيمة اللي الشاشة شافتها** — لو جهاز تاني
غيّر **نفس السطر** ← تعارض على السطر ده بس (مش المستند كله)، وتعديل الرأس (التاريخ / الملاحظات) بيتأكد من
``row_version``. الرأس مقفول ``FOR UPDATE`` طول الحفظ، والقاعدة (0033) بترفض أي تعديل في سطور المستند المرحّل.

الترحيل = **محرك الترحيل** (``PostingService.post_document``): سطر «line» لكل رصيد + «opening» بالمجموع ← قيد
أوتوماتيك واحد ``OB`` بيظهر في «القيود اليومية» قراية بس · إلغاء الترحيل بيمسح القيد.

**بضاعة أول المدة (المرحلة 6 — 0045، رد المستخدم 2026-11-23):** بتترحّل **مع** المستند ده: كل سطر مش وهمي قيمته
على حساب المخزون (من نوع الصنف ← الافتراضي 1105) في نفس القيد، والسطور كلها بتدخل «دفتر المخزون» (``opening_stock``)
بتاريخ الأرصدة · إلغاء الترحيل بيطلّعها (ولو اللي اتصرف بعدها هيخلّي الرصيد بالسالب ← محتاج صلاحية «من غير رصيد»).
قسم «📦 المخزون» قراية بس هنا (بيتكتب من شاشة «بضاعة أول المدة» أو شاشة الأصناف).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import psycopg

from app.database.db import Database, Executor, get_db
from app.repositories.opening_balance_repository import PARTY_COLUMNS, OpeningBalanceRepository
from app.repositories.opening_stock_repository import OpeningStockRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.journal_service import parse_money
from app.services.permission_service import PermissionService
from app.services.posting_service import PostingLine, PostingService
from app.services.stock_ledger_service import StockLedgerService, StockLine

ENTITY, ENTITY_LABEL = "acc.opening_balances", "الأرصدة الافتتاحية"
PERM = "acc.opening_balances"
DOC_TYPE, SOURCE_ID, SOURCE_NO = "opening_balance", 1, "OB"
DESCRIPTION = "الأرصدة الافتتاحية"
# (المفتاح، الاسم، الأيقونة) — نفس ترتيب شريط الأقسام في النموذج 2
SECTIONS: dict[str, tuple[str, str]] = {
    "trs": ("الخزائن", "💵"), "bnk": ("البنوك", "🏦"), "cus": ("العملاء", "👥"), "sup": ("الموردين", "🚚"),
    "emp": ("عهد الموظفين", "🧑‍🔧"), "prt": ("الشركاء", "🤝"), "inv": ("المخزون", "📦"),
    "oth": ("حسابات تانية", "📒"),
}
STOCK_DOC = "opening_stock"
KIND_NAMES = {"invoice": "فاتورة", "statement": "بيان"}
PARTY_LABELS = {"customer": "العميل", "supplier": "المورد", "employee": "الموظف", "partner": "الشريك"}
BALANCE_SHEET = ("asset", "liability", "equity")
POSTED = "مستند الأرصدة الافتتاحية مرحّل — الغي ترحيله الأول عشان تعدّل الأرصدة."
ZERO = Decimal("0")


def money_pair(debit: Any, credit: Any, label: str) -> tuple[Decimal, Decimal]:
    d, c = parse_money(debit, f"مدين {label}"), parse_money(credit, f"دائن {label}")
    if d and c:
        raise BusinessError(f"{label}: يا مدين يا دائن — مش الاتنين.")
    return d, c


def _db_message(exc: psycopg.Error) -> BusinessError:
    diag = getattr(exc, "diag", None)
    return BusinessError((diag.message_primary if diag else None) or str(exc))


class OpeningBalanceService:
    def __init__(self, db: Database | None = None, repository: OpeningBalanceRepository | None = None,
                 audit: AuditService | None = None, posting: PostingService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or OpeningBalanceRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.posting = posting or PostingService(self.db)
        self.stock = OpeningStockRepository(self.db)
        self.ledger = StockLedgerService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def get_document(self) -> dict[str, Any] | None:
        """الرأس + الإجماليات + ملخص الأقسام (عدد اللي ليهم رصيد · مدين · دائن) + حساب الفرق."""
        header = self.repository.header()
        if header is None:
            return None
        summary = {k: {"lines": 0, "debit": ZERO, "credit": ZERO} for k in SECTIONS}
        for row in self.repository.summary():
            summary[row["section"]] = {"lines": int(row["lines"]), "debit": Decimal(row["debit"]),
                                       "credit": Decimal(row["credit"])}
        stock = self.stock.summary()
        summary["inv"] = {"lines": int(stock["lines"]), "debit": Decimal(stock["value"]), "credit": ZERO}
        debit = sum((s["debit"] for s in summary.values()), ZERO)
        credit = sum((s["credit"] for s in summary.values()), ZERO)
        header.update({"sections": summary, "total_debit": debit, "total_credit": credit,
                       "opening_account": self.repository.opening_account(), "entry_id": None,
                       "inventory_value": Decimal(stock["value"]), "inventory_account": self.stock.inventory_account()})
        if header["status"] == "posted":
            entry = self.posting.entry_for(DOC_TYPE, SOURCE_ID)
            header["entry_id"] = entry["id"] if entry else None
        return header

    def section_rows(self, section: str) -> list[dict[str, Any]]:
        """القسم: كل الخزائن / البنوك / العملاء … برصيدهم (الفاضي = مالوش رصيد) · «حسابات تانية» = سطورها."""
        if section not in SECTIONS:
            raise BusinessError("القسم ده مش معروف.")
        if section == "oth":
            return self.repository.lines(section="oth")
        if section == "inv":
            return self.stock_rows()
        return self.repository.section_rows(section)

    def stock_rows(self) -> list[dict[str, Any]]:
        """قسم «📦 المخزون» قراية بس: سطر لكل (صنف + مخزن + نوع) بقيمته (``account_id`` فاضي ← مايتعدّلش هنا)."""
        account = self.stock.inventory_account() or {}
        rows = []
        for l in self.stock.lines():
            rows.append({"ref_id": int(l["id"]), "code": l["item_code"],
                         "name": f"{l['item_name']} — {l['warehouse_name']} ({KIND_NAMES[l['stock_kind']]})",
                         "is_active": bool(l["item_active"]), "account_id": None,
                         "account_code": account.get("code"), "account_name": account.get("name_ar") or "المخزون",
                         "party_kind": None, "party_id": None, "line_id": None,
                         "debit": Decimal(l["value"]), "credit": ZERO, "qty": l["qty"], "unit_cost": l["unit_cost"],
                         "is_virtual": bool(l["is_virtual"])})
        return rows

    def lines(self) -> list[dict[str, Any]]:
        return self.repository.lines()

    def party_balance(self, kind: str, party_id: int | None) -> dict[str, Any]:
        """لخانة «رصيد أول المدة» في شاشة العميل / المورد: الرصيد + حالة المستند وتاريخه."""
        header = self.repository.header() or {}
        line = self.repository.party_line(kind, int(party_id)) if party_id else None
        return {"debit": Decimal((line or {}).get("debit") or 0), "credit": Decimal((line or {}).get("credit") or 0),
                "status": header.get("status", "draft"), "balance_date": header.get("balance_date")}

    # ---- مساعدات -------------------------------------------------------------
    def _lock(self, tx: Executor, *, draft: bool = True) -> dict[str, Any]:
        header = self.repository.header(ex=tx, for_update=True)
        if header is None:
            raise BusinessError("مستند الأرصدة الافتتاحية مش موجود.")
        if draft and header["status"] == "posted":
            raise BusinessError(POSTED)
        return header

    def _record(self, tx: Executor, action: str, before: dict, after: dict,
                lines_before: list[dict], lines_after: list[dict]) -> None:
        keep = ("balance_date", "status", "notes", "row_version", "posted_at")
        self.audit.record(tx, action=action, entity=ENTITY, entity_id=SOURCE_ID, entity_label=ENTITY_LABEL,
                          record_label=f"{SOURCE_NO} — {ENTITY_LABEL}",
                          before={k: before.get(k) for k in keep}, after={k: after.get(k) for k in keep},
                          lines={"acc.opening_balance_lines": {"before": lines_before, "after": lines_after}})

    @staticmethod
    def _party(change: dict[str, Any]) -> tuple[str, int] | None:
        kind, party_id = change.get("party_kind"), change.get("party_id")
        if kind and party_id:
            if kind not in PARTY_COLUMNS:
                raise BusinessError(f"نوع الطرف «{kind}» مش معروف.")
            return str(kind), int(party_id)
        return None

    @staticmethod
    def _data(account_id: int, party: tuple[str, int] | None, debit: Decimal, credit: Decimal,
              change: dict[str, Any]) -> dict[str, Any]:
        data = {"account_id": int(account_id), "debit": debit, "credit": credit,
                "cost_center_id": int(change["cost_center_id"]) if change.get("cost_center_id") else None,
                "description": (" ".join(str(change.get("description") or "").split())[:300]) or None,
                **{col: None for col in PARTY_COLUMNS.values()}}
        if party:
            data[PARTY_COLUMNS[party[0]]] = party[1]
        return data

    def _conflict(self, tx: Executor, what: str) -> ConcurrentEditError:
        base = self.guard.conflict(tx, ENTITY, SOURCE_ID)
        return ConcurrentEditError(f"{what} اتعدّل من جهاز تاني بعد ما فتحت الشاشة.\n{base}",
                                   changed_by=base.changed_by, changed_at=base.changed_at, device=base.device)

    def _apply(self, tx: Executor, change: dict[str, Any], n: int) -> tuple[dict | None, dict | None]:
        """تغيير سطر واحد ← (قبل، بعد). القيمة القديمة لازم تطابق اللي في القاعدة (وإلا تعارض على السطر ده)."""
        label = change.get("label") or f"السطر {n}"
        debit, credit = money_pair(change.get("debit"), change.get("credit"), f"«{label}»")
        old_d, old_c = money_pair(change.get("old_debit"), change.get("old_credit"), f"«{label}»")
        party = self._party(change)
        line_id = change.get("line_id")
        if line_id:
            current = self.repository.line(int(line_id), ex=tx, for_update=True)
            if current is None:
                raise self._conflict(tx, f"رصيد «{label}»")
        else:
            if not change.get("account_id"):
                if debit or credit:
                    raise BusinessError(f"«{label}»: اختار الحساب.")
                return None, None
            current = self.repository.find_line(int(change["account_id"]), party, ex=tx, for_update=True)
            if current is not None and change.get("new_row"):
                who = f" ({PARTY_LABELS[party[0]]})" if party else ""
                raise BusinessError(f"«{label}»{who} ليه رصيد في المستند على نفس الحساب قبل كده — "
                                    "عدّله من مكانه (مايتكررش).")
        cur_d = Decimal(current["debit"]) if current else ZERO
        cur_c = Decimal(current["credit"]) if current else ZERO
        if (cur_d, cur_c) != (old_d, old_c):
            raise self._conflict(tx, f"رصيد «{label}»")
        account_id = change.get("account_id") or (current or {}).get("account_id")
        if current is not None and not change.get("account_id"):        # المبلغ بس ← الطرف زي ما هو
            party = next(((k, current[c]) for k, c in PARTY_COLUMNS.items() if current.get(c)), None)
        if not debit and not credit:
            if current is not None:
                self.repository.delete_line(int(current["id"]), ex=tx)
            return current, None
        data = self._data(account_id, party, debit, credit, change)
        if current is None:
            new_id = self.repository.insert_line(data, SESSION.user_id, ex=tx)
        else:
            new_id = int(current["id"])
            self.repository.update_line(new_id, data, SESSION.user_id, ex=tx)
        return current, self.repository.line(new_id, ex=tx)

    def _check_year(self, tx: Executor, d: dt.date) -> None:
        year = self.repository.year_for(d, ex=tx)
        if year is None:
            raise BusinessError(f"مفيش سنة مالية فيها تاريخ {d:%d/%m/%Y} — ضيفها الأول من شاشة «السنة المالية والفترات».")
        if year["status"] == "closed":
            raise BusinessError(f"السنة المالية {year['year_code']} مقفولة — اختار تاريخ في سنة مفتوحة.")

    # ---- حفظ مسودة ----------------------------------------------------------
    def save(self, header: dict[str, Any] | None, changes: list[dict[str, Any]],
             expected_version: int | None = None, post: bool = False) -> int:
        """يحفظ الرأس (لو اتغيّر) والسطور اللي اتغيّرت بس — ولو ``post`` يرحّل في نفس الـ transaction.

        كل تغيير: ``line_id`` (لو السطر موجود) · ``account_id`` · ``party_kind`` / ``party_id`` · ``debit`` / ``credit``
        · ``old_debit`` / ``old_credit`` (اللي الشاشة شافته) · ``cost_center_id`` · ``description`` · ``label``
        · ``new_row`` (سطر جديد من «حسابات تانية» — لو الطرف ليه سطر على نفس الحساب ← مايتكررش).
        """
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        if post:
            PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                before = self._lock(tx)
                lines_before: list[dict] = []
                lines_after: list[dict] = []
                head = dict(before)
                if header is not None:
                    d = header.get("balance_date")
                    if isinstance(d, dt.datetime):
                        d = d.date()
                    if not isinstance(d, dt.date):
                        raise BusinessError("تاريخ الأرصدة مطلوب.")
                    notes = " ".join(str(header.get("notes") or "").split()) or None
                    if notes and len(notes) > 500:
                        raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
                    if (d, notes) != (before["balance_date"], before["notes"]):
                        self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=SOURCE_ID)
                        self._check_year(tx, d)
                        self.repository.update_header({"balance_date": d, "notes": notes}, SESSION.user_id, ex=tx)
                for n, change in enumerate(changes or [], start=1):
                    old, new = self._apply(tx, change, n)
                    if old is not None:
                        lines_before.append(old)
                    if new is not None:
                        lines_after.append(new)
                if lines_before or lines_after:
                    self.repository.touch(SESSION.user_id, ex=tx)
                head = self.repository.header(ex=tx)
                if head["row_version"] != before["row_version"]:
                    self._record(tx, "update", before, head, lines_before, lines_after)
                if post:
                    self._post(tx, head)
                return int(self.repository.header(ex=tx)["row_version"])
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError("الرصيد ده اتسجل من جهاز تاني في نفس اللحظة (مايتكررش) — دوس «تحديث».") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    # ---- من شاشة العميل / المورد (جوه transaction الحفظ بتاعها) ------------------------
    def set_party_balance(self, tx: Executor, kind: str, party_id: int, debit: Any, credit: Any,
                          old_debit: Any = None, old_credit: Any = None, label: str = "") -> bool:
        """خانة «رصيد أول المدة»: نفس سطر الطرف على حساب رقابة مجموعته (بيتنقل لو المجموعة اتغيّرت).

        ``old_*`` = اللي الشاشة شافته (``None`` = من غير تحقق). ← ``True`` لو حاجة اتغيّرت.
        """
        if kind not in ("customer", "supplier"):
            raise BusinessError("رصيد أول المدة من الشاشة دي للعملاء والموردين بس.")
        label = label or PARTY_LABELS[kind]
        d, c = money_pair(debit, credit, f"رصيد أول المدة ({label})")
        try:
            header = self._lock(tx, draft=False)
            current = self.repository.party_line(kind, int(party_id), ex=tx, for_update=True)
            account_id = self.repository.party_control_account(kind, int(party_id), ex=tx)
            cur_d = Decimal(current["debit"]) if current else ZERO
            cur_c = Decimal(current["credit"]) if current else ZERO
            same_amount = (cur_d, cur_c) == (d, c)
            if same_amount and (current is None or current["account_id"] == account_id or header["status"] == "posted"):
                return False
            if header["status"] == "posted":
                raise BusinessError("مستند الأرصدة الافتتاحية مرحّل — رصيد أول المدة مايتعدّلش "
                                    "(الغي ترحيل المستند الأول).")
            if old_debit is not None or old_credit is not None:
                old = money_pair(old_debit, old_credit, f"رصيد أول المدة ({label})")
                if old != (cur_d, cur_c):
                    raise self._conflict(tx, f"رصيد أول المدة لـ «{label}»")
            change = {"line_id": current["id"] if current else None, "account_id": account_id,
                      "party_kind": kind, "party_id": party_id, "debit": d, "credit": c,
                      "old_debit": cur_d, "old_credit": cur_c, "label": label,
                      "description": (current or {}).get("description")}
            old_line, new_line = self._apply(tx, change, 1)
            self.repository.touch(SESSION.user_id, ex=tx)
            after = self.repository.header(ex=tx)
            self._record(tx, "update", header, after, [old_line] if old_line else [], [new_line] if new_line else [])
        except psycopg.errors.UniqueViolation as exc:
            raise BusinessError("رصيد أول المدة ده اتسجل من جهاز تاني في نفس اللحظة — دوس «تحديث».") from exc
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc
        return True

    # ---- الترحيل / إلغاء الترحيل -------------------------------------------------
    def _post(self, tx: Executor, head: dict[str, Any]) -> None:
        lines = self.repository.lines(ex=tx)
        stock = self.stock.lines(ex=tx)
        if not lines and not stock:
            raise BusinessError("مفيش ولا رصيد في المستند — اكتب الأرصدة الأول.")
        self._check_year(tx, head["balance_date"])
        posting: list[PostingLine] = []
        total = ZERO
        for s in stock:                                          # بضاعة أول المدة ← حساب المخزون من نوع الصنف
            value = Decimal(s["value"])
            if not s["is_virtual"] and value > 0:
                total += value
                posting.append(PostingLine("inventory", value, category_id=s["category_id"]))
        for line in lines:
            amount = Decimal(line["debit"]) - Decimal(line["credit"])
            total += amount
            party = next(((k, line[c]) for k, c in PARTY_COLUMNS.items() if line.get(c)), None)
            posting.append(PostingLine("line", amount, account_id=line["account_id"], party=party,
                                       cost_center_id=line["cost_center_id"], description=line["description"]))
        posting.append(PostingLine("opening", total))
        if any(Decimal(p.amount) for p in posting):              # كله صفر (بضاعة وهمي / بسعر صفر بس) ← مفيش قيد
            self.posting.post_document(tx, doc_type=DOC_TYPE, source_id=SOURCE_ID, source_no=SOURCE_NO,
                                       entry_date=head["balance_date"], description=DESCRIPTION, lines=posting)
        if stock:
            self.ledger.post(tx, doc_type=STOCK_DOC, source_id=SOURCE_ID, source_no=SOURCE_NO,
                             movement_date=head["balance_date"],
                             lines=[StockLine(int(s["item_id"]), int(s["warehouse_id"]), Decimal(s["qty"]),
                                              s["stock_kind"], unit_cost=None if s["is_virtual"] else s["unit_cost"])
                                    for s in stock])
        self.repository.set_status("posted", SESSION.user_id, ex=tx)
        after = self.repository.header(ex=tx)
        self._record(tx, "post", head, after, [], [])

    def post(self, expected_version: int | None = None) -> None:
        """ترحيل المستند المحفوظ ← قيد أوتوماتيك واحد OB (الفرق على 34)."""
        PermissionService.require(f"{PERM}.post")
        try:
            with self.db.atomic() as tx:
                head = self._lock(tx)
                self.guard.check(tx, head, expected_version, entity=ENTITY, entity_id=SOURCE_ID)
                self._post(tx, head)
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    def unpost(self, expected_version: int | None = None) -> None:
        """إلغاء الترحيل (بالصلاحية بس): القيد بيتمسح والمستند يرجع مسودة ويتعدّل."""
        PermissionService.require(f"{PERM}.unpost")
        try:
            with self.db.atomic() as tx:
                head = self._lock(tx, draft=False)
                self.guard.check(tx, head, expected_version, entity=ENTITY, entity_id=SOURCE_ID)
                if head["status"] != "posted":
                    raise BusinessError("مستند الأرصدة الافتتاحية مش مرحّل.")
                self.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=SOURCE_ID)
                self.ledger.remove(tx, doc_type=STOCK_DOC, source_id=SOURCE_ID,
                                   allow_negative=SESSION.can(f"{PERM}.negative"))
                self.repository.set_status("draft", SESSION.user_id, ex=tx)
                self._record(tx, "unpost", head, self.repository.header(ex=tx), [], [])
        except psycopg.errors.RaiseException as exc:
            raise _db_message(exc) from exc

    # ---- اختيارات «حسابات تانية» ----------------------------------------------------
    def choices(self) -> dict[str, Any]:
        from app.repositories.journal_repository import JournalRepository  # noqa: PLC0415

        journal = JournalRepository(self.db)
        opening = self.repository.opening_account() or {}
        accounts = [a for a in self.repository.account_choices()
                    if a["id"] != opening.get("id") and a.get("role_code") != "inventory"]
        return {"accounts": accounts, "cost_centers": journal.cost_center_choices(),
                "parties": {k: journal.party_choices(k) for k in PARTY_COLUMNS}}
