"""SQL القيود اليومية (``acc.journal_entries`` + ``acc.journal_lines`` — سكريبت 0031) — المرحلة 4.

* القائمة **بفلتر فترة وصفحات** (مع مليون قيد: الـ index على ``entry_date DESC, id DESC``).
* السطور بتتكتب من جديد مع كل حفظ للمسودة (مسح + إضافة في نفس الـ transaction) — المرحّل القاعدة بتقفله.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة (``journal_entries_guard``) بتتأكد من التوازن والسنة وبتصرف
  الرقم ``JV`` جوه قفل العدّاد.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

_JV_NO = re.compile(r"^JV-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
SOURCES = ("all", "manual", "auto")
PARTY_COLUMNS = {"customer": "customer_id", "supplier": "supplier_id", "employee": "employee_id",
                 "partner": "partner_id"}
LINE_FIELDS = ("line_no", "account_id", "description", "debit", "credit", "cost_center_id", "project_id",
               "customer_id", "supplier_id", "employee_id", "partner_id")

_SELECT = """
    SELECT e.id, e.entry_no, e.draft_no, e.entry_date, e.fiscal_year_id, e.description, e.reference, e.status,
           e.source_type, e.source_id, e.source_no, e.total_debit, e.total_credit, e.lines_count,
           e.created_by, e.posted_at, e.posted_by, e.created_at, e.updated_at, e.row_version,
           y.year_code, y.status AS year_status,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM acc.journal_entries e
    JOIN core.fiscal_years y ON y.id = e.fiscal_year_id
    LEFT JOIN sec.app_users cu ON cu.id = e.created_by
    LEFT JOIN sec.app_users pu ON pu.id = e.posted_by
"""

_LIST = """
    SELECT e.id, e.entry_no, e.draft_no, e.entry_date, e.description, e.reference, e.status, e.source_type,
           e.source_no, e.total_debit, e.total_credit, e.lines_count
    FROM acc.journal_entries e
"""

_LINES = """
    SELECT l.id, l.entry_id, l.line_no, l.account_id, l.description, l.debit, l.credit, l.cost_center_id,
           l.project_id, l.customer_id, l.supplier_id, l.employee_id, l.partner_id,
           a.code AS account_code, a.name_ar AS account_name, a.party_kind, a.requires_cost_center,
           a.is_active AS account_active, a.is_leaf AS account_leaf,
           cc.code AS cost_center_code, cc.name_ar AS cost_center_name,
           p.code AS project_code, p.name AS project_name,
           coalesce(c.code, s.code, em.code, pr.code) AS party_code,
           coalesce(c.name_ar, s.name_ar, em.name_ar, pr.name_ar) AS party_name
    FROM acc.journal_lines l
    JOIN acc.accounts a ON a.id = l.account_id
    LEFT JOIN acc.cost_centers cc ON cc.id = l.cost_center_id
    LEFT JOIN acc.projects p ON p.id = l.project_id
    LEFT JOIN party.customers c ON c.id = l.customer_id
    LEFT JOIN party.suppliers s ON s.id = l.supplier_id
    LEFT JOIN party.employees em ON em.id = l.employee_id
    LEFT JOIN party.partners pr ON pr.id = l.partner_id
"""


class JournalRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, source: str,
               keyword: str) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append("e.entry_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("e.entry_date <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("e.status = %s")
            params.append(status)
        if source == "manual":
            conds.append("e.source_type = 'manual'")
        elif source == "auto":
            conds.append("e.source_type <> 'manual'")
        if keyword and _JV_NO.match(keyword.strip()):
            # رقم قيد كامل (JV-2026-00012) ← من الـ unique index على طول (مع مليون قيد ILIKE بياخد ثواني)
            conds.append("e.entry_no = %s")
            params.append(keyword.strip().upper())
        elif keyword:
            like = f"%{keyword}%"
            digits = keyword.strip()
            conds.append("(e.entry_no ILIKE %s OR e.description ILIKE %s OR e.reference ILIKE %s "
                         "OR e.source_no ILIKE %s OR e.draft_no::text = %s)")
            params += [like, like, like, like, digits]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_entries(self, date_from: date | None, date_to: date | None, status: str = "all",
                     source: str = "all", keyword: str = "", limit: int | None = None, offset: int = 0,
                     ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, source, keyword)
        sql = _LIST + where + " ORDER BY e.entry_date DESC, e.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_entries(self, date_from: date | None, date_to: date | None, status: str = "all",
                      source: str = "all", keyword: str = "", ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, source, keyword)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE e.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE e.status = 'posted') AS posted, "
            "coalesce(sum(e.total_debit) FILTER (WHERE e.status = 'posted'), 0) AS posted_amount "
            "FROM acc.journal_entries e" + where, params)

    # ---- القيد ------------------------------------------------------------------
    def get_entry(self, entry_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE e.id = %s" + (" FOR UPDATE OF e" if for_update else ""),
                                     [entry_id])

    def entry_lines(self, entry_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(_LINES + " WHERE l.entry_id = %s ORDER BY l.line_no", [entry_id])

    def insert_entry(self, data: dict[str, Any], ex: Executor) -> int:
        return int(ex.fetch_value(
            "INSERT INTO acc.journal_entries (entry_date, fiscal_year_id, description, reference, created_by) "
            "VALUES (%s, (SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s, %s, %s) "
            "RETURNING id",
            [data["entry_date"], data["entry_date"], data["description"], data["reference"], data.get("user_id")]))

    def update_entry(self, entry_id: int, data: dict[str, Any], ex: Executor,
                     expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(
            "UPDATE acc.journal_entries SET entry_date = %s, description = %s, reference = %s "
            "WHERE id = %s AND status = 'draft'" + clause,
            [data["entry_date"], data["description"], data["reference"], entry_id, *extra])

    def replace_lines(self, entry_id: int, lines: list[dict[str, Any]], ex: Executor) -> None:
        ex.execute("DELETE FROM acc.journal_lines WHERE entry_id = %s", [entry_id])
        cols = ", ".join(LINE_FIELDS)
        marks = ", ".join(["%s"] * len(LINE_FIELDS))
        for line in lines:
            ex.execute(f"INSERT INTO acc.journal_lines (entry_id, {cols}) VALUES (%s, {marks})",
                       [entry_id, *[line.get(k) for k in LINE_FIELDS]])
        ex.execute(
            "UPDATE acc.journal_entries e SET total_debit = t.d, total_credit = t.c, lines_count = t.n "
            "FROM (SELECT coalesce(sum(debit), 0) AS d, coalesce(sum(credit), 0) AS c, count(*) AS n "
            "      FROM acc.journal_lines WHERE entry_id = %s) t WHERE e.id = %s",
            [entry_id, entry_id])

    def set_status(self, entry_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(
            "UPDATE acc.journal_entries SET status = %s, posted_by = %s WHERE id = %s" + clause,
            [status, user_id if status == "posted" else None, entry_id, *extra])

    def delete_entry(self, entry_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute("DELETE FROM acc.journal_entries WHERE id = %s" + clause, [entry_id, *extra])

    # ---- الاختيارات -------------------------------------------------------------
    def account_choices(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات الفرعية (النشطة وكمان الموقوفة عشان القيد القديم يتعرض)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, party_kind, requires_cost_center, is_active FROM acc.accounts "
            "WHERE is_leaf ORDER BY code")

    def cost_center_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_active FROM acc.cost_centers WHERE is_leaf ORDER BY code")

    def project_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name, status FROM acc.projects ORDER BY order_date DESC NULLS LAST, id DESC")

    def party_choices(self, kind: str, ex: Executor | None = None) -> list[dict]:
        table = {"customer": "party.customers", "supplier": "party.suppliers", "employee": "party.employees",
                 "partner": "party.partners"}[kind]
        order = "code" if kind == "customer" else "seq"
        if kind == "customer":
            order = "CASE WHEN code ~ '^[0-9]+$' THEN lpad(code, 10, '0') ELSE code END"
        return self._x(ex).fetch_all(f"SELECT id, code, name_ar, is_active FROM {table} ORDER BY {order}")

    def year_for(self, d: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, year_code, status, start_date, end_date FROM core.fiscal_years "
            "WHERE %s BETWEEN start_date AND end_date", [d])
