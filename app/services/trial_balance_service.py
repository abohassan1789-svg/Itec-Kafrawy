"""«ميزان المراجعة» (المرحلة 4 — النموذج 4 «تبويب لكل نوع»، اختيار المستخدم وردوده 2026-11-17).

* **8 أعمدة**: رصيد سابق (مدين · دائن) · حركة الفترة (مدين · دائن) · المجاميع (السابق + الحركة) · الرصيد آخر المدة.
* تبويب لكل نوع (أصول · خصوم · حقوق ملكية · إيرادات · مصروفات) بإجمالي النوع، وتبويب «الكل» = سطر لكل نوع +
  إجمالي الميزان.
* **المستوى الافتراضي شجرة بكل المستويات** (الرئيسي = مجموع اللي تحته)، وفلتر المستوى بيعرض حسابات المستوى ده
  + الحساب الفرعي اللي مستواه أعلى منه (عشان مفيش رقم يقع) · أو «الفرعي بس».
* **الفترة الافتراضية** من أول السنة المالية الحالية لحد النهارده (زي كشف الحساب).
* **الرصيد السابق**: حسابات الميزانية = كل اللي قبل «من» · الإيرادات والمصروفات = من أول السنة المالية بتاعة «من»
  (بتتقفل كل سنة في الأرباح المحتجزة). وصافي إيرادات ومصروفات السنين اللي قبل كده (لو لسه ماتقفلتش) بيتحط على حساب
  «الأرباح المحتجزة» في الرصيد السابق — عشان الميزان يفضل متوازن لحد ما قيد الإقفال يتعمل (المرحلة 10).
* **المرحّل بس** · الحسابات الصفرية (مالهاش رصيد ولا حركة) مستخبية إلا لو اتطلبت.
* **الإجماليات** = مجموع الحسابات اللي مابتتكررش في العرض: في الشجرة و«الفرعي بس» ← الحسابات الفرعية · في مستوى
  معيّن ← حسابات المستوى ده (+ الفرعي اللي فوقه). ولازم مدين = دائن في كل عمودين.
* **الشبكة:** قراية بس من غير قفل · استعلام SUM واحد في القاعدة · كله في **transaction قراية واحدة (REPEATABLE
  READ READ ONLY)** فلو جهاز تاني رحّل في نفس اللحظة الميزان بيفضل متوازن.
* صلاحية واحدة: ``acc.trial_balance.view`` (والتصدير معاها).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from app.database.db import Database, get_db
from app.repositories.trial_balance_repository import TrialBalanceRepository
from app.services.permission_service import PermissionService

PERM = "acc.trial_balance"
ZERO = Decimal("0")
CENT = Decimal("0.005")
TYPES: dict[str, tuple[str, str]] = {
    "asset": ("الأصول", "🏦"), "liability": ("الخصوم", "📄"), "equity": ("حقوق الملكية", "🤝"),
    "revenue": ("الإيرادات", "📈"), "expense": ("المصروفات", "📉"),
}
AMOUNTS = ("pd", "pc", "debit", "credit", "td", "tc", "ed", "ec")


def columns8(prev: Decimal, debit: Decimal, credit: Decimal) -> dict[str, Decimal]:
    """الأعمدة الثمانية من الرصيد السابق (موجب = مدين) وحركة الفترة."""
    pd, pc = max(prev, ZERO), max(-prev, ZERO)
    end = prev + debit - credit
    return {"pd": pd, "pc": pc, "debit": debit, "credit": credit, "td": pd + debit, "tc": pc + credit,
            "ed": max(end, ZERO), "ec": max(-end, ZERO), "prev": prev, "end": end}


def add_totals(rows: list[dict]) -> dict[str, Decimal]:
    return {k: sum((Decimal(r[k]) for r in rows), ZERO) for k in AMOUNTS}


def is_balanced(t: dict[str, Decimal]) -> bool:
    return all(abs(t[a] - t[b]) < CENT for a, b in (("pd", "pc"), ("debit", "credit"), ("td", "tc"), ("ed", "ec")))


class TrialBalanceService:
    def __init__(self, db: Database | None = None, repository: TrialBalanceRepository | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or TrialBalanceRepository(self.db)

    def default_period(self, today: dt.date | None = None) -> tuple[dt.date, dt.date]:
        """من أول السنة المالية الحالية (ولو مفيش سنة ← أول يناير) لحد النهارده."""
        today = today or dt.date.today()
        return self.year_start(today), today

    def year_start(self, d: dt.date, ex=None) -> dt.date:
        year = self.repository.year_for(d, ex)
        return year["start_date"] if year else d.replace(month=1, day=1)

    def levels(self) -> list[int]:
        """المستويات الموجودة في الدليل (لفلتر المستوى)."""
        PermissionService.require(f"{PERM}.view")
        return sorted({int(a["level"]) for a in self.repository.accounts()})

    def trial_balance(self, date_from: dt.date, date_to: dt.date, level: Any = "all",
                      show_zero: bool = False) -> dict[str, Any]:
        """الميزان كله في لقطة واحدة من القاعدة.

        ``level``: ``"all"`` (شجرة بكل المستويات) · ``"leaf"`` (الفرعي بس) · رقم المستوى.
        بيرجّع ``accounts`` (صفوف العرض بالترتيب بالكود، كل صف فيه ``account_type``) و``type_totals`` لكل نوع و
        ``grand`` و``balanced`` و``carried`` (أرباح سنين قبل كده على «الأرباح المحتجزة»).
        """
        PermissionService.require(f"{PERM}.view")
        if date_from > date_to:
            date_from, date_to = date_to, date_from
        level = level if level in ("all", "leaf") else int(level)
        with self.db.atomic() as tx:
            # لقطة واحدة: كل الأرصدة من نفس اللحظة حتى لو جهاز تاني بيرحّل دلوقتي — ومن غير أي قفل.
            tx.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            year_start = self.year_start(date_from, tx)
            accounts = self.repository.accounts(tx)
            sums = {int(r["account_id"]): r for r in self.repository.balances(date_from, date_to, year_start, tx)}
            leaves = {int(a["id"]) for a in accounts if a["is_leaf"]}
            carried = sum((Decimal(r["carried"]) for k, r in sums.items() if k in leaves), ZERO)
            retained = self.repository.retained_account(tx) if abs(carried) >= CENT else None
            lift = set(self.repository.ancestors(int(retained["id"]), tx)) if retained else set()

        rows: list[dict[str, Any]] = []
        for a in accounts:
            s = sums.get(int(a["id"]))
            prev = Decimal(s["prev"]) if s else ZERO
            if int(a["id"]) in lift:
                prev += carried
            debit, credit = (Decimal(s["debit"]), Decimal(s["credit"])) if s else (ZERO, ZERO)
            row = {"id": int(a["id"]), "kind": "account", "code": a["code"], "name": a["name"],
                   "level": int(a["level"]), "is_leaf": bool(a["is_leaf"]), "account_type": a["account_type"],
                   "statement": a["statement"], "is_active": bool(a["is_active"]),
                   "carried": carried if retained and int(a["id"]) == int(retained["id"]) else ZERO,
                   **columns8(prev, debit, credit)}
            row["empty"] = abs(prev) < CENT and not debit and not credit
            rows.append(row)

        def in_level(r: dict) -> bool:
            if level == "all":
                return True
            if level == "leaf":
                return r["is_leaf"]
            return r["level"] == level or (r["is_leaf"] and r["level"] < level)

        def counted(r: dict) -> bool:          # الحسابات اللي بتتجمع في الإجمالي (مابتتكررش)
            return r["is_leaf"] if level in ("all", "leaf") else in_level(r)

        shown = [r for r in rows if in_level(r) and (show_zero or not r["empty"])]
        type_totals = {t: add_totals([r for r in rows if counted(r) and r["account_type"] == t]) for t in TYPES}
        grand = add_totals([r for r in rows if counted(r)])
        active = {t: sum(1 for r in rows if r["is_leaf"] and r["account_type"] == t and not r["empty"])
                  for t in TYPES}
        return {
            "date_from": date_from, "date_to": date_to, "year_start": year_start, "level": level,
            "show_zero": show_zero, "accounts": shown, "type_totals": type_totals, "active_counts": active,
            "grand": grand, "balanced": is_balanced(grand), "carried": carried if retained else ZERO,
            "retained": retained,
        }

    @staticmethod
    def rows_for(result: dict[str, Any], account_type: str | None) -> list[dict[str, Any]]:
        """صفوف التبويب: نوع واحد ← حساباته + «إجمالي النوع» · ``None`` («الكل») ← سطر لكل نوع + إجمالي الميزان."""
        if account_type is None:
            rows = [{"id": f"type:{t}", "kind": "type", "account_type": t, "code": "",
                     "name": f"{icon} {name}", **result["type_totals"][t]} for t, (name, icon) in TYPES.items()]
            rows.append({"id": "grand", "kind": "grand_total", "code": "", "name": "إجمالي الميزان",
                         **result["grand"]})
            return rows
        rows = [r for r in result["accounts"] if r["account_type"] == account_type]
        name = TYPES[account_type][0]
        rows.append({"id": f"total:{account_type}", "kind": "type_total", "code": "",
                     "name": f"إجمالي {name}", **result["type_totals"][account_type]})
        return rows
