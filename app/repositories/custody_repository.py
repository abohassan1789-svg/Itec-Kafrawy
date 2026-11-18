"""SQL «العهد» (``cash.custody_disbursements`` · ``cash.custody_settlements`` + سطورها وربطها بالعهد — سكريبت 0040).

* القائمتين **بفلتر فترة وصفحات** (index على التاريخ والموظف) · الترحيل وإلغاؤه = تغيير ``status`` والقاعدة بتصرف
  الرقم CD / CS جوه قفل العدّاد وبتتأكد من السنة والموظف والخزنة.
* **العهد المفتوحة** = صرف عهدة مرحّل (مش «سداد») − اللي اتوزّع عليه من التسويات المرحّلة (الموزّع في المسودة صفر).
* **اللي ليه** = زيادات التسويات المرحّلة − «سداد اللي ليه» المرحّل.
* ``lock_employee``: قفل لكل موظف (advisory) جوه الـ transaction — ترحيل تسويتين لنفس الموظف من جهازين بيمشوا ورا بعض.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

DISB = "cash.custody_disbursements"
SETTLE = "cash.custody_settlements"
LINES = "cash.custody_settlement_lines"
LINKS = "cash.custody_settlement_links"
STATUSES = ("all", "draft", "posted")
LOCK_KEY = 40400                     # مفتاح قفل الموظف (pg_advisory_xact_lock)
_DISB_NO = re.compile(r"^CD-\d{4}-\d+$", re.IGNORECASE)
_SETTLE_NO = re.compile(r"^CS-\d{4}-\d+$", re.IGNORECASE)
DISB_FIELDS = ("disb_date", "employee_id", "purpose", "source_kind", "treasury_id", "bank_account_id", "amount",
               "project_id", "description", "reference_no")
SETTLE_FIELDS = ("settle_date", "employee_id", "description", "reference_no", "expense_total", "return_total")
LINE_FIELDS = ("line_no", "line_kind", "expense_type_id", "project_id", "return_kind", "treasury_id",
               "bank_account_id", "amount", "description")

_SOURCE_NAME = "coalesce(t.name_ar, b.name_ar || ' — ' || ba.code)"
_DISB_JOINS = """
    JOIN party.employees em ON em.id = v.employee_id
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
    LEFT JOIN acc.projects p ON p.id = v.project_id
"""
_DISB_SELECT = f"""
    SELECT v.id, v.disb_no, v.draft_no, v.disb_date, v.fiscal_year_id, v.employee_id, v.purpose, v.source_kind,
           v.treasury_id, v.bank_account_id, v.amount, v.project_id, v.description, v.reference_no, v.status,
           v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status, em.code AS employee_code, em.name_ar AS employee_name,
           coalesce(t.code, ba.code) AS source_code, {_SOURCE_NAME} AS source_name,
           p.code AS project_code, p.name AS project_name,
           (SELECT coalesce(sum(l.allocated_amount), 0) FROM {LINKS} l WHERE l.disbursement_id = v.id) AS used_amount,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {DISB} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    {_DISB_JOINS}
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""
_SETTLE_SELECT = f"""
    SELECT v.id, v.settle_no, v.draft_no, v.settle_date, v.fiscal_year_id, v.employee_id, v.description,
           v.reference_no, v.expense_total, v.return_total, v.allocated_total, v.excess_amount, v.status,
           v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           v.expense_total + v.return_total AS amount,
           y.year_code, y.status AS year_status, em.code AS employee_code, em.name_ar AS employee_name,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {SETTLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    JOIN party.employees em ON em.id = v.employee_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""


class CustodyRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def lock_employee(self, employee_id: int, ex: Executor) -> None:
        """قفل الموظف لحد آخر الـ transaction (أي ترحيل / إلغاء ترحيل على عهده بيستنى اللي قبله)."""
        ex.fetch_value("SELECT pg_advisory_xact_lock(%s, %s)", [LOCK_KEY, int(employee_id)])

    # ---- الاختيارات ---------------------------------------------------------------
    def employee_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, job_title, is_active, takes_custody FROM party.employees ORDER BY seq")

    def expense_type_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT x.id, x.code, x.name_ar, x.is_active, a.code AS account_code, a.name_ar AS account_name, "
            "a.requires_cost_center, a.is_active AS account_active FROM exp.expense_types x "
            "JOIN acc.accounts a ON a.id = x.account_id ORDER BY x.seq")

    def project_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name, status, cost_center_id FROM acc.projects ORDER BY order_date DESC NULLS LAST, id DESC")

    def project_cost_centers(self, ids: list[int], ex: Executor | None = None) -> dict[int, int]:
        if not ids:
            return {}
        rows = self._x(ex).fetch_all("SELECT id, cost_center_id FROM acc.projects WHERE id = ANY(%s)", [list(ids)])
        return {int(r["id"]): r["cost_center_id"] for r in rows}

    # ---- العهد المفتوحة واللي ليه ---------------------------------------------------
    def open_custodies(self, employee_id: int, ex: Executor | None = None) -> list[dict]:
        """صرف العهد المرحّلة اللي لسه فاضل منها (الأقدم الأول)."""
        return self._x(ex).fetch_all(
            f"SELECT * FROM (SELECT c.id, c.disb_no, c.disb_date, c.amount, c.description, c.project_id, "
            f"p.code AS project_code, (SELECT coalesce(sum(l.allocated_amount), 0) FROM {LINKS} l "
            f"WHERE l.disbursement_id = c.id) AS used_amount FROM {DISB} c "
            "LEFT JOIN acc.projects p ON p.id = c.project_id "
            "WHERE c.employee_id = %s AND c.status = 'posted' AND c.purpose = 'custody') q "
            "WHERE q.amount > q.used_amount ORDER BY q.disb_date, q.id", [employee_id])

    def lock_disbursements(self, ids: list[int], ex: Executor) -> list[dict]:
        """العهد المختارة بقفل صف + اللي اتوزّع عليها قبل كده (الأقدم الأول)."""
        if not ids:
            return []
        return ex.fetch_all(
            f"SELECT c.id, c.disb_no, c.draft_no, c.disb_date, c.amount, c.status, c.purpose, c.employee_id, "
            f"(SELECT coalesce(sum(l.allocated_amount), 0) FROM {LINKS} l WHERE l.disbursement_id = c.id) AS used_amount "
            f"FROM {DISB} c WHERE c.id = ANY(%s) ORDER BY c.disb_date, c.id FOR UPDATE OF c", [list(ids)])

    def credit_due(self, employee_id: int, ex: Executor | None = None) -> Decimal:
        """اللي ليه عند الموظف: زيادات التسويات المرحّلة − «سداد اللي ليه» المرحّل."""
        return Decimal(self._x(ex).fetch_value(
            f"SELECT (SELECT coalesce(sum(excess_amount), 0) FROM {SETTLE} WHERE employee_id = %s AND status = 'posted') "
            f"- (SELECT coalesce(sum(amount), 0) FROM {DISB} WHERE employee_id = %s AND status = 'posted' "
            "AND purpose = 'payback')", [employee_id, employee_id]))

    def ledger_balance(self, employee_id: int, ex: Executor | None = None) -> Decimal:
        """رصيد الموظف في القيود المرحّلة (مدين − دائن): موجب = عليه · سالب = ليه."""
        return Decimal(self._x(ex).fetch_value(
            "SELECT coalesce(sum(l.debit - l.credit), 0) FROM acc.journal_lines l "
            "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.employee_id = %s",
            [employee_id]))

    # ---- القوائم ------------------------------------------------------------------
    @staticmethod
    def _where(kind: str, date_from: date | None, date_to: date | None, status: str, keyword: str,
               employee_id: int | None) -> tuple[str, list[Any]]:
        col, no_col, pattern = (("disb_date", "disb_no", _DISB_NO) if kind == "disb"
                                else ("settle_date", "settle_no", _SETTLE_NO))
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append(f"v.{col} >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append(f"v.{col} <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        if employee_id:
            conds.append("v.employee_id = %s")
            params.append(employee_id)
        keyword = keyword.strip()
        if keyword and pattern.match(keyword):
            conds.append(f"v.{no_col} = %s")
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append(f"(v.{no_col} ILIKE %s OR v.description ILIKE %s OR v.reference_no ILIKE %s "
                         "OR em.name_ar ILIKE %s OR em.code ILIKE %s OR v.draft_no::text = %s)")
            params += [like, like, like, like, like, keyword]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_docs(self, kind: str, date_from: date | None, date_to: date | None, status: str = "all",
                  keyword: str = "", employee_id: int | None = None, limit: int | None = None, offset: int = 0,
                  ex: Executor | None = None) -> list[dict]:
        where, params = self._where(kind, date_from, date_to, status, keyword, employee_id)
        if kind == "disb":
            sql = (f"SELECT v.id, v.disb_no AS doc_no, v.draft_no, v.disb_date AS doc_date, v.purpose, v.amount, "
                   f"v.status, v.description, em.name_ar AS employee_name, {_SOURCE_NAME} AS source_name "
                   f"FROM {DISB} v {_DISB_JOINS}" + where + " ORDER BY v.disb_date DESC, v.id DESC")
        else:
            sql = ("SELECT v.id, v.settle_no AS doc_no, v.draft_no, v.settle_date AS doc_date, "
                   "v.expense_total + v.return_total AS amount, v.expense_total, v.return_total, v.excess_amount, "
                   "v.status, v.description, em.name_ar AS employee_name "
                   f"FROM {SETTLE} v JOIN party.employees em ON em.id = v.employee_id" + where +
                   " ORDER BY v.settle_date DESC, v.id DESC")
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_docs(self, kind: str, date_from: date | None, date_to: date | None, status: str = "all",
                   keyword: str = "", employee_id: int | None = None, ex: Executor | None = None) -> dict:
        where, params = self._where(kind, date_from, date_to, status, keyword, employee_id)
        amount = "v.amount" if kind == "disb" else "v.expense_total + v.return_total"
        source = f"{DISB} v {_DISB_JOINS}" if kind == "disb" else \
            f"{SETTLE} v JOIN party.employees em ON em.id = v.employee_id"
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted, "
            f"coalesce(sum({amount}) FILTER (WHERE v.status = 'posted'), 0) AS posted_amount FROM {source}" + where,
            params)

    # ---- صرف العهدة ------------------------------------------------------------------
    def get_disbursement(self, disb_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _DISB_SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [disb_id])

    def insert_disbursement(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*DISB_FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(DISB_FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {DISB} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in DISB_FIELDS], data["disb_date"], data.get("user_id")]))

    def update_disbursement(self, disb_id: int, data: dict[str, Any], ex: Executor,
                            expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in DISB_FIELDS)
        return ex.execute(f"UPDATE {DISB} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in DISB_FIELDS], disb_id, *extra])

    def set_disbursement_status(self, disb_id: int, status: str, user_id: int | None, ex: Executor,
                                expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {DISB} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, disb_id, *extra])

    def delete_disbursement(self, disb_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {DISB} WHERE id = %s" + clause, [disb_id, *extra])

    def disbursement_settlements(self, disb_id: int, ex: Executor | None = None) -> list[dict]:
        """التسويات اللي متعلّم فيها على العهدة دي (بالموزّع)."""
        return self._x(ex).fetch_all(
            f"SELECT s.id, s.settle_no, s.draft_no, s.settle_date, s.status, l.allocated_amount FROM {LINKS} l "
            f"JOIN {SETTLE} s ON s.id = l.settlement_id WHERE l.disbursement_id = %s ORDER BY s.settle_date, s.id",
            [disb_id])

    # ---- التسوية ---------------------------------------------------------------------
    def get_settlement(self, settle_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SETTLE_SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [settle_id])

    def settlement_lines(self, settle_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            f"SELECT l.id, l.line_no, l.line_kind, l.expense_type_id, l.project_id, l.return_kind, l.treasury_id, "
            "l.bank_account_id, l.amount, l.description, x.code AS expense_code, x.name_ar AS expense_name, "
            f"p.code AS project_code, coalesce(t.code, ba.code) AS source_code, {_SOURCE_NAME} AS source_name "
            f"FROM {LINES} l LEFT JOIN exp.expense_types x ON x.id = l.expense_type_id "
            "LEFT JOIN acc.projects p ON p.id = l.project_id "
            "LEFT JOIN cash.treasuries t ON t.id = l.treasury_id "
            "LEFT JOIN cash.bank_accounts ba ON ba.id = l.bank_account_id "
            "LEFT JOIN cash.banks b ON b.id = ba.bank_id "
            "WHERE l.settlement_id = %s ORDER BY l.line_no", [settle_id])

    def settlement_links(self, settle_id: int, ex: Executor | None = None) -> list[dict]:
        """العهد المتعلّم عليها + مبلغها واللي اتسوّى منها من تسويات تانية (للعرض)."""
        return self._x(ex).fetch_all(
            f"SELECT c.id, c.disb_no, c.draft_no, c.disb_date, c.amount, c.description, c.status, "
            "l.allocated_amount, "
            f"(SELECT coalesce(sum(x.allocated_amount), 0) FROM {LINKS} x WHERE x.disbursement_id = c.id "
            "AND x.settlement_id <> l.settlement_id) AS other_used "
            f"FROM {LINKS} l JOIN {DISB} c ON c.id = l.disbursement_id WHERE l.settlement_id = %s "
            "ORDER BY c.disb_date, c.id", [settle_id])

    def insert_settlement(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*SETTLE_FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(SETTLE_FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {SETTLE} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in SETTLE_FIELDS], data["settle_date"], data.get("user_id")]))

    def update_settlement(self, settle_id: int, data: dict[str, Any], ex: Executor,
                          expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in SETTLE_FIELDS)
        return ex.execute(f"UPDATE {SETTLE} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in SETTLE_FIELDS], settle_id, *extra])

    def replace_children(self, settle_id: int, lines: list[dict[str, Any]], custody_ids: list[int],
                         ex: Executor) -> None:
        """سطور التسوية والعهد المتعلّم عليها من جديد (المسودة بس — القاعدة بتتأكد)."""
        ex.execute(f"DELETE FROM {LINES} WHERE settlement_id = %s", [settle_id])
        ex.execute(f"DELETE FROM {LINKS} WHERE settlement_id = %s", [settle_id])
        cols = ", ".join(("settlement_id", *LINE_FIELDS))
        marks = ", ".join(["%s"] * (len(LINE_FIELDS) + 1))
        for line in lines:
            ex.execute(f"INSERT INTO {LINES} ({cols}) VALUES ({marks})",
                       [settle_id, *[line.get(k) for k in LINE_FIELDS]])
        for disb_id in custody_ids:
            ex.execute(f"INSERT INTO {LINKS} (settlement_id, disbursement_id) VALUES (%s, %s)", [settle_id, disb_id])

    def set_allocation(self, settle_id: int, disb_id: int, amount: Decimal, ex: Executor) -> int:
        return ex.execute(f"UPDATE {LINKS} SET allocated_amount = %s WHERE settlement_id = %s AND disbursement_id = %s",
                          [amount, settle_id, disb_id])

    def reset_allocation(self, settle_id: int, ex: Executor) -> int:
        return ex.execute(f"UPDATE {LINKS} SET allocated_amount = 0 WHERE settlement_id = %s", [settle_id])

    def set_settlement_status(self, settle_id: int, status: str, user_id: int | None, ex: Executor,
                              allocated: Decimal = Decimal("0"), excess: Decimal = Decimal("0"),
                              expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(
            f"UPDATE {SETTLE} SET status = %s, posted_by = %s, allocated_total = %s, excess_amount = %s "
            "WHERE id = %s" + clause,
            [status, user_id if status == "posted" else None, allocated, excess, settle_id, *extra])

    def delete_settlement(self, settle_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {SETTLE} WHERE id = %s" + clause, [settle_id, *extra])
