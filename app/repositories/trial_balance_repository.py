"""SQL «ميزان المراجعة» (المرحلة 4 — النموذج 4) — قراية بس من سطور القيود **المرحّلة**.

* مفيش جداول للميزان: كله من ``acc.journal_lines`` + ``acc.journal_entries`` (``status = 'posted'`` بس).
* ``balances``: **استعلام واحد** بيجمع سطور كل حساب فرعي (``SUM … FILTER … GROUP BY``) وبيطلّعها لفوق لكل
  الحسابات الرئيسية اللي فوقه (شجرة الآباء بـ ``WITH RECURSIVE``) — مفيش تجميع سطر سطر في الكود.
  - ``prev`` = الرصيد السابق: حسابات **الميزانية** كل اللي قبل «من» · حسابات **قائمة الدخل** من أول السنة
    المالية بتاعة «من» بس (رد المستخدم 2026-11-17 — بتتقفل كل سنة في الأرباح المحتجزة).
  - ``debit`` / ``credit`` = حركة الفترة (من «من» لحد «إلى»).
  - ``carried`` = صافي الإيرادات والمصروفات **قبل** أول السنة المالية (أرباح / خسائر سنين قبل كده لسه ماتقفلتش) —
    الـ Service بيحطه على حساب «الأرباح المحتجزة» عشان الميزان يفضل متوازن لحد ما الإقفال يتعمل.
* الميزان مابيقفلش ولا صف (قراية بس) — الـ Service بيشغّله في transaction قراية واحدة (REPEATABLE READ).
  والـ SUM بيتقرا من الـ index ``journal_lines_account_idx`` (الحساب + القيد INCLUDE مدين ودائن — ``0034``).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from datetime import date

from app.database.db import Database, Executor, get_db


class TrialBalanceRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def accounts(self, ex: Executor | None = None) -> list[dict]:
        """الدليل كله بالترتيب بالكود (الشجرة)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar AS name, parent_id, level, is_leaf, account_type, statement, is_active "
            "FROM acc.accounts ORDER BY code")

    def balances(self, date_from: date, date_to: date, year_start: date,
                 ex: Executor | None = None) -> list[dict]:
        """لكل حساب (فرعي ورئيسي) فيه حركة لحد «إلى»: prev · debit · credit · carried — الرئيسي = مجموع اللي تحته."""
        return self._x(ex).fetch_all(
            "WITH RECURSIVE leaf AS ("
            "  SELECT l.account_id, "
            "    coalesce(sum(l.debit - l.credit) FILTER (WHERE e.entry_date < %(f)s "
            "      AND (a.statement = 'bs' OR e.entry_date >= %(ys)s)), 0) AS prev, "
            "    coalesce(sum(l.debit) FILTER (WHERE e.entry_date >= %(f)s), 0) AS debit, "
            "    coalesce(sum(l.credit) FILTER (WHERE e.entry_date >= %(f)s), 0) AS credit, "
            "    coalesce(sum(l.debit - l.credit) FILTER (WHERE a.statement = 'is' "
            "      AND e.entry_date < LEAST(%(ys)s, %(f)s)), 0) AS carried "
            "  FROM acc.journal_lines l "
            "  JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' "
            "  JOIN acc.accounts a ON a.id = l.account_id "
            "  WHERE e.entry_date <= %(t)s "
            "  GROUP BY l.account_id"
            "), up AS ("
            "  SELECT leaf.account_id AS leaf_id, leaf.account_id AS account_id FROM leaf "
            "  UNION ALL "
            "  SELECT up.leaf_id, a.parent_id FROM up JOIN acc.accounts a ON a.id = up.account_id "
            "  WHERE a.parent_id IS NOT NULL"
            ") "
            "SELECT up.account_id, sum(leaf.prev) AS prev, sum(leaf.debit) AS debit, "
            "       sum(leaf.credit) AS credit, sum(leaf.carried) AS carried "
            "FROM up JOIN leaf ON leaf.account_id = up.leaf_id GROUP BY up.account_id",
            {"f": date_from, "t": date_to, "ys": year_start})

    def retained_account(self, ex: Executor | None = None) -> dict | None:
        """حساب «الأرباح المحتجزة» من الحسابات الافتراضية (وإلا أول حساب فرعي بدور «أرباح محتجزة»)."""
        x = self._x(ex)
        row = x.fetch_one(
            "SELECT a.id, a.code, a.name_ar AS name FROM acc.default_accounts d "
            "JOIN acc.accounts a ON a.id = d.account_id WHERE d.key = 'retained'")
        return row or x.fetch_one(
            "SELECT id, code, name_ar AS name FROM acc.accounts WHERE role_code = 'retained' AND is_leaf "
            "ORDER BY code LIMIT 1")

    def ancestors(self, account_id: int, ex: Executor | None = None) -> list[int]:
        """الحساب وكل اللي فوقه لحد الجذر."""
        rows = self._x(ex).fetch_all(
            "WITH RECURSIVE t AS (SELECT id, parent_id FROM acc.accounts WHERE id = %s "
            "UNION ALL SELECT a.id, a.parent_id FROM acc.accounts a JOIN t ON a.id = t.parent_id) SELECT id FROM t",
            [account_id])
        return [int(r["id"]) for r in rows]

    def year_for(self, d: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, year_code, start_date, end_date FROM core.fiscal_years "
            "WHERE %s BETWEEN start_date AND end_date", [d])
