"""SQL «كشف الحساب ودفتر الأستاذ» (المرحلة 4 — النموذج 1) — قراية بس من سطور القيود **المرحّلة**.

* مفيش جداول للكشف: كله من ``acc.journal_lines`` + ``acc.journal_entries`` (``status = 'posted'`` بس).
* **النطاق** (``scope``) = عمود في السطر + أرقام: ``account_id`` (حساب / خزنة / بنك / كل اللي تحت حساب رئيسي) أو
  عمود الطرف (``customer_id`` / ``supplier_id`` / ``employee_id`` / ``partner_id``).
* ``summary``: **SUM واحد** بيطلّع الرصيد السابق (كل اللي قبل «من») وحركة الفترة — من غير جلب الحركات
  (index-only على ``0034``). ``by_account`` = لكل حساب فرعي لوحده (تجميع «بالحسابات الفرعية»).
* ``lines``: حركات الفترة **بصفحات** (``LIMIT/OFFSET``) والرصيد الجاري جوه كل مجموعة بـ window في القاعدة
  (مش في الكود سطر سطر)، ومعاه رقم السطر في المجموعة وعدد سطورها (عشان الشاشة تعرف أول وآخر المجموعة).
  ``empty_accounts`` = حسابات ليها رصيد سابق ومالهاش حركة في الفترة (بتطلع سطر «مكان» من غير مبالغ).
* الكشف مابيقفلش ولا صف (قراية بس) — الـ Service بيشغّل كل ده في transaction قراية واحدة (REPEATABLE READ).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db

PARTY_TABLES = {"customer": "party.customers", "supplier": "party.suppliers", "employee": "party.employees",
                "partner": "party.partners"}
SCOPE_COLUMNS = ("account_id", "customer_id", "supplier_id", "employee_id", "partner_id")

_POSTED = "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted'"


def _column(column: str) -> str:
    if column not in SCOPE_COLUMNS:
        raise ValueError(f"عمود مش معروف للكشف: {column}")
    return column


class StatementRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- الاختيارات («الاسم») ---------------------------------------------------
    def party_choices(self, kind: str, ex: Executor | None = None) -> list[dict]:
        table = PARTY_TABLES[kind]
        order = "CASE WHEN code ~ '^[0-9]+$' THEN lpad(code, 10, '0') ELSE code END" if kind == "customer" else "seq"
        return self._x(ex).fetch_all(f"SELECT id, code, name_ar AS name, is_active FROM {table} ORDER BY {order}")

    def party_row(self, kind: str, party_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            f"SELECT id, code, name_ar AS name, is_active FROM {PARTY_TABLES[kind]} WHERE id = %s", [party_id])

    def treasury_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT t.id, t.code, t.name_ar AS name, t.is_active, t.account_id FROM cash.treasuries t "
            "ORDER BY t.seq")

    def bank_account_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT b.id, b.code, a.name_ar AS name, b.is_active, b.account_id FROM cash.bank_accounts b "
            "JOIN acc.accounts a ON a.id = b.account_id JOIN cash.banks bk ON bk.id = b.bank_id "
            "ORDER BY bk.seq, b.seq")

    def account_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar AS name, is_active, is_leaf, level FROM acc.accounts ORDER BY code")

    def get_account(self, account_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar AS name, is_leaf FROM acc.accounts WHERE id = %s", [account_id])

    def subtree_ids(self, account_id: int, ex: Executor | None = None) -> list[int]:
        """الحساب وكل اللي تحته (أي مستوى)."""
        rows = self._x(ex).fetch_all(
            "WITH RECURSIVE t AS (SELECT id FROM acc.accounts WHERE id = %s "
            "UNION ALL SELECT a.id FROM acc.accounts a JOIN t ON a.parent_id = t.id) SELECT id FROM t",
            [account_id])
        return [int(r["id"]) for r in rows]

    def year_for(self, d: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, year_code, start_date, end_date FROM core.fiscal_years "
            "WHERE %s BETWEEN start_date AND end_date", [d])

    # ---- الكشف -------------------------------------------------------------------
    def summary(self, column: str, ids: list[int], date_from: date, date_to: date, by_account: bool = False,
                ex: Executor | None = None) -> list[dict]:
        """الرصيد السابق (قبل «من») + مدين ودائن وعدد سطور الفترة — صف واحد (grp = 0) أو صف لكل حساب."""
        col = _column(column)
        grp = "l.account_id" if by_account else "0"
        return self._x(ex).fetch_all(
            f"SELECT {grp} AS grp, "
            "coalesce(sum(l.debit - l.credit) FILTER (WHERE e.entry_date < %(f)s), 0) AS prev, "
            "coalesce(sum(l.debit) FILTER (WHERE e.entry_date >= %(f)s), 0) AS debit, "
            "coalesce(sum(l.credit) FILTER (WHERE e.entry_date >= %(f)s), 0) AS credit, "
            "count(*) FILTER (WHERE e.entry_date >= %(f)s) AS lines "
            f"{_POSTED} WHERE l.{col} = ANY(%(ids)s) AND e.entry_date <= %(t)s GROUP BY 1",
            {"f": date_from, "t": date_to, "ids": list(ids)})

    def lines(self, column: str, ids: list[int], date_from: date, date_to: date, by_account: bool = False,
              empty_accounts: list[int] | None = None, limit: int | None = None, offset: int = 0,
              ex: Executor | None = None) -> list[dict]:
        """حركات الفترة بالترتيب (التاريخ ← القيد ← السطر) والرصيد الجاري جوه المجموعة (من غير الرصيد السابق).

        بـ ``by_account`` المجموعة = الحساب الفرعي (بالترتيب بالكود)، وإلا المجموعة واحدة (grp = 0).
        """
        col = _column(column)
        grp = "l.account_id" if by_account else "0"
        order = "a.code, rn" if by_account else "rn"
        return self._x(ex).fetch_all(
            "WITH mv AS ("
            f"  SELECT {grp} AS grp, e.entry_date, e.id AS entry_id, l.id AS line_id, l.account_id, e.entry_no, "
            "         e.draft_no, e.source_type, e.source_no, "
            "         coalesce(nullif(btrim(l.description), ''), e.description) AS description, l.debit, l.credit, "
            "         false AS placeholder "
            f"  {_POSTED} WHERE l.{col} = ANY(%(ids)s) AND e.entry_date BETWEEN %(f)s AND %(t)s "
            "  UNION ALL "
            "  SELECT x, NULL, NULL, NULL, x, NULL, NULL, NULL, NULL, NULL, 0, 0, true "
            "  FROM unnest(%(empty)s::int[]) AS x"
            "), w AS ("
            "  SELECT mv.*, sum(mv.debit - mv.credit) OVER (PARTITION BY mv.grp ORDER BY mv.entry_date, "
            "         mv.entry_id, mv.line_id ROWS UNBOUNDED PRECEDING) AS run, "
            "         row_number() OVER (PARTITION BY mv.grp ORDER BY mv.entry_date, mv.entry_id, mv.line_id) AS rn, "
            "         count(*) OVER (PARTITION BY mv.grp) AS cnt "
            "  FROM mv"
            ") "
            "SELECT w.*, a.code AS account_code, a.name_ar AS account_name, t.name_ar AS doc_type_name "
            "FROM w JOIN acc.accounts a ON a.id = w.account_id "
            "LEFT JOIN acc.posting_doc_types t ON t.code = w.source_type "
            f"ORDER BY {order} LIMIT %(lim)s OFFSET %(off)s",
            {"f": date_from, "t": date_to, "ids": list(ids), "empty": list(empty_accounts or []),
             "lim": limit, "off": max(0, int(offset))})

    def accounts_by_ids(self, ids: list[int], ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar AS name FROM acc.accounts WHERE id = ANY(%s) ORDER BY code", [list(ids)])
