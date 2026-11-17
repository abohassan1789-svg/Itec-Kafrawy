"""«كشف الحساب ودفتر الأستاذ» (المرحلة 4 — النموذج 1 «فلاتر فوق + جدول»، اختيار المستخدم وردوده 2026-11-17).

* **شاشة واحدة لكل الكشوف** بـ «النوع»: عميل · مورد · عهدة موظف · شريك · خزنة · بنك · أي حساب في الدليل
  (= دفتر الأستاذ) ← «الاسم» ← من ← إلى.
* **الفترة الافتراضية** من أول السنة المالية الحالية لحد النهارده · **الرصيد السابق = كل الحركات قبل «من»**
  (لكل الحسابات).
* **المرحّل بس** — المسودات مابتظهرش · كل مستند **سطر واحد** (مفيش أصناف).
* **الحساب الرئيسي** بتجميعين: «بالحسابات الرئيسية» = كشف واحد بكل حركات اللي تحته · «بالحسابات الفرعية» = كل
  حساب فرعي لوحده برصيده السابق وحركاته ورصيد آخره (اللي مالوش رصيد ولا حركة مابيظهرش) + إجمالي الكل.
* السطور: رصيد سابق ← الحركات بالرصيد الجاري ← إجمالي حركة الفترة ← الرصيد آخر المدة.
* رقم المستند = ``entry_no`` للقيد اليدوي و``source_no`` للأوتوماتيك.
* **الشبكة:** قراية بس من غير قفل · الرصيد السابق بـ SUM واحد في القاعدة · الرصيد السابق والحركات في
  **transaction قراية واحدة (REPEATABLE READ READ ONLY)** فلو جهاز تاني رحّل في نفس اللحظة الكشف بيفضل متطابق
  مع نفسه · الحركات **بصفحات** (``PAGE_SIZE`` سطر).
* صلاحية واحدة: ``acc.account_statement.view``.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from app.database.db import Database, get_db
from app.repositories.statement_repository import StatementRepository
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

PERM = "acc.account_statement"
PAGE_SIZE = 1000
ZERO = Decimal("0")

# النوع ← (عربي، أيقونة، عمود السطر للطرف أو None = بالحساب)
KINDS: dict[str, tuple[str, str, str | None]] = {
    "customer": ("عميل", "👥", "customer_id"),
    "supplier": ("مورد", "🚚", "supplier_id"),
    "employee": ("موظف (عهدة)", "🧑‍🔧", "employee_id"),
    "partner": ("شريك", "🤝", "partner_id"),
    "treasury": ("خزنة", "💵", None),
    "bank": ("بنك", "🏦", None),
    "account": ("حساب (دفتر الأستاذ)", "📒", None),
}
GROUPINGS = {"main": "تجميع بالحسابات الرئيسية", "sub": "تجميع بالحسابات الفرعية"}
NO_TARGET = "اختار الاسم الأول."
NOT_FOUND = "{kind} ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث»."


def doc_label(row: dict[str, Any]) -> str:
    """رقم المستند: ``entry_no`` للقيد اليدوي و``source_no`` للأوتوماتيك."""
    if row.get("source_type") not in (None, "manual"):
        return row.get("source_no") or ""
    return row.get("entry_no") or f"مسودة {row.get('draft_no')}"


def balance_text(value: Any) -> str:
    """«88,300.00 مدين» / «1,800.00 دائن» / «0.00»."""
    v = Decimal(value or 0)
    if abs(v) < Decimal("0.005"):
        return "0.00"
    return f"{abs(v):,.2f} {'مدين' if v > 0 else 'دائن'}"


class StatementService:
    def __init__(self, db: Database | None = None, repository: StatementRepository | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or StatementRepository(self.db)

    # ---- الاختيارات --------------------------------------------------------------
    def choices(self, kind: str) -> list[dict[str, Any]]:
        """صفوف «الاسم» للنوع: id · code · name · is_active (+ is_leaf للحسابات)."""
        PermissionService.require(f"{PERM}.view")
        if kind not in KINDS:
            raise BusinessError("نوع الكشف مش معروف.")
        if kind == "treasury":
            return self.repository.treasury_choices()
        if kind == "bank":
            return self.repository.bank_account_choices()
        if kind == "account":
            return self.repository.account_choices()
        return self.repository.party_choices(kind)

    def default_period(self, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
        """من أول السنة المالية الحالية (ولو مفيش سنة ← أول يناير) لحد النهارده."""
        today = today or dt.date.today()
        return self.year_start(today), today

    def year_start(self, d: dt.date) -> dt.date:
        year = self.repository.year_for(d)
        return year["start_date"] if year else d.replace(month=1, day=1)

    # ---- الكشف -------------------------------------------------------------------
    def _scope(self, kind: str, target_id: int, grouping: str, tx) -> tuple[str, list[int], bool, dict]:
        """(العمود، الأرقام، بالحسابات الفرعية؟، بيانات الاسم)."""
        label = KINDS[kind][0]
        party_column = KINDS[kind][2]
        if party_column:
            row = self.repository.party_row(kind, target_id, tx)
            if row is None:
                raise BusinessError(NOT_FOUND.format(kind=label))
            return party_column, [target_id], False, {**row, "is_leaf": True}
        if kind in ("treasury", "bank"):
            rows = (self.repository.treasury_choices(tx) if kind == "treasury"
                    else self.repository.bank_account_choices(tx))
            row = next((r for r in rows if r["id"] == target_id), None)
            if row is None:
                raise BusinessError(NOT_FOUND.format(kind=label))
            return "account_id", [int(row["account_id"])], False, {**row, "is_leaf": True}
        account = self.repository.get_account(target_id, tx)
        if account is None:
            raise BusinessError(NOT_FOUND.format(kind="الحساب"))
        if account["is_leaf"]:
            return "account_id", [target_id], False, account
        return "account_id", self.repository.subtree_ids(target_id, tx), grouping == "sub", account

    def statement(self, kind: str, target_id: int | None, date_from: dt.date, date_to: dt.date,
                  grouping: str = "main", page: int = 1, page_size: int | None = PAGE_SIZE) -> dict[str, Any]:
        """الكشف كله (صفحة واحدة من الحركات) في لقطة واحدة من القاعدة.

        بيرجّع: الأرقام الإجمالية (``prev`` · ``debit`` · ``credit`` · ``end`` · ``lines``) والصفحات و``rows`` =
        صفوف العرض بالترتيب، كل صف ``kind`` واحد من: group · prev · carry · line · total · end.
        ``page_size=None`` = كل الحركات (للتصدير).
        """
        PermissionService.require(f"{PERM}.view")
        if kind not in KINDS:
            raise BusinessError("نوع الكشف مش معروف.")
        if target_id is None:
            raise BusinessError(NO_TARGET)
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        grouping = grouping if grouping in GROUPINGS else "main"
        with self.db.atomic() as tx:
            # لقطة واحدة: الرصيد السابق والحركات من نفس اللحظة حتى لو جهاز تاني بيرحّل دلوقتي — ومن غير أي قفل.
            tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            column, ids, by_account, target = self._scope(kind, int(target_id), grouping, tx)
            groups = self.repository.summary(column, ids, date_from, date_to, by_account, tx)
            if by_account:
                groups = [g for g in groups if g["lines"] or g["prev"] != 0]
            empty = [int(g["grp"]) for g in groups if by_account and not g["lines"]]
            stream = sum(int(g["lines"]) for g in groups) + len(empty)
            pages = max(1, -(-stream // page_size)) if page_size else 1
            page = max(1, min(int(page), pages))
            offset = (page - 1) * page_size if page_size else 0
            lines = self.repository.lines(column, ids, date_from, date_to, by_account, empty,
                                          limit=page_size, offset=offset, ex=tx) if stream else []
            names = ({int(a["id"]): a for a in self.repository.accounts_by_ids([int(g["grp"]) for g in groups], tx)}
                     if by_account else {})
        prev = sum((Decimal(g["prev"]) for g in groups), ZERO)
        debit = sum((Decimal(g["debit"]) for g in groups), ZERO)
        credit = sum((Decimal(g["credit"]) for g in groups), ZERO)
        result = {
            "kind": kind, "target": target, "date_from": date_from, "date_to": date_to,
            "grouping": "sub" if by_account else "main", "is_main_account": kind == "account" and not target["is_leaf"],
            "prev": prev, "debit": debit, "credit": credit, "end": prev + debit - credit,
            "lines": sum(int(g["lines"]) for g in groups), "page": page, "pages": pages,
        }
        result["rows"] = self._rows(result, groups, lines, names, by_account, last_page=page == pages)
        return result

    # ---- صفوف العرض ---------------------------------------------------------------
    @staticmethod
    def _rows(result: dict, groups: list[dict], lines: list[dict], names: dict[int, dict], by_account: bool,
              last_page: bool) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        info = {int(g["grp"]): g for g in groups}
        first_page = result["page"] == 1

        def add(kind: str, **data: Any) -> None:
            rows.append({"id": len(rows) + 1, "kind": kind, **data})

        def closing(g: dict, title_total: str, title_end: str, end: Decimal) -> None:
            add("total", label=title_total, debit=Decimal(g["debit"]), credit=Decimal(g["credit"]),
                count=int(g["lines"]))
            add("end", label=title_end, balance=end)

        to_text = result["date_to"].strftime("%d-%m-%Y")
        from_text = result["date_from"].strftime("%d-%m-%Y")
        if not by_account:
            g = info.get(0, {"prev": ZERO, "debit": ZERO, "credit": ZERO, "lines": 0})
            prev = Decimal(g["prev"])
            if first_page:
                add("prev", label=f"⏮ رصيد سابق (قبل {from_text})", balance=prev)
            elif lines:
                first = lines[0]
                carry = prev + Decimal(first["run"]) - Decimal(first["debit"]) + Decimal(first["credit"])
                add("carry", label="⏮ رصيد منقول من الصفحة اللي قبلها", balance=carry)
            for ln in lines:
                add("line", **_line(ln, prev))
            if last_page:
                closing({**g, "debit": result["debit"], "credit": result["credit"], "lines": result["lines"]},
                        f"إجمالي حركة الفترة ({result['lines']:,} سطر)", f"⏭ الرصيد آخر المدة ({to_text})",
                        result["end"])
            return rows

        current: int | None = None
        for ln in lines:
            grp = int(ln["grp"])
            g = info[grp]
            prev = Decimal(g["prev"])
            if grp != current:
                current = grp
                acc = names.get(grp, {"code": ln.get("account_code"), "name": ln.get("account_name")})
                if int(ln["rn"]) == 1:
                    add("group", label=f"📒 {acc['code']}  {acc['name']}", account_id=grp)
                    add("prev", label=f"⏮ رصيد سابق (قبل {from_text})", balance=prev)
                else:
                    add("group", label=f"📒 {acc['code']}  {acc['name']} (تكملة)", account_id=grp)
                    carry = prev + Decimal(ln["run"]) - Decimal(ln["debit"]) + Decimal(ln["credit"])
                    add("carry", label="⏮ رصيد منقول من الصفحة اللي قبلها", balance=carry)
            if not ln["placeholder"]:
                add("line", **_line(ln, prev))
            if int(ln["rn"]) == int(ln["cnt"]):
                end = prev + Decimal(g["debit"]) - Decimal(g["credit"])
                closing(g, f"إجمالي حركة الفترة ({int(g['lines']):,} سطر)", f"⏭ الرصيد آخر المدة ({to_text})", end)
        if last_page:
            if first_page and not lines:
                add("prev", label=f"⏮ رصيد سابق (قبل {from_text})", balance=result["prev"])
            add("grand_total", label=f"إجمالي حركة الفترة — كل الحسابات الفرعية ({result['lines']:,} سطر)",
                debit=result["debit"], credit=result["credit"])
            add("grand_end", label=f"⏭ الرصيد آخر المدة — كل الحسابات الفرعية ({to_text})",
                balance=result["end"])
        return rows


def _line(ln: dict[str, Any], prev: Decimal) -> dict[str, Any]:
    return {"entry_date": ln["entry_date"], "doc_no": doc_label(ln), "description": ln["description"],
            "debit": Decimal(ln["debit"]), "credit": Decimal(ln["credit"]),
            "balance": prev + Decimal(ln["run"]), "entry_id": int(ln["entry_id"]),
            "source_type": ln["source_type"], "doc_type_name": ln.get("doc_type_name"),
            "account_id": int(ln["account_id"]), "account_code": ln["account_code"],
            "account_name": ln["account_name"]}
