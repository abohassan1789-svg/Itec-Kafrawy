"""SQL «سند قبض العملاء» و«سند صرف العملاء» (``cash.customer_receipts`` / ``cash.customer_payments`` — سكريبت 0035).

* الجدولين نفس الشكل بالظبط — كل دالة بتاخد ``kind`` (``receipt`` / ``payment``) والجدول من ``TABLES`` بس
  (عمره ما بييجي من برّا).
* القائمة **بفلتر فترة وصفحات** (الـ index على ``voucher_date DESC, id DESC``) · رقم السند الكامل من الـ unique index.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم RV / PV جوه قفل العدّاد وبتتأكد من السنة والعميل والخزنة.
* أرصدة العميل والخزنة / البنك **من سطور القيود المرحّلة** (الـ indexes بتوع 0031 / 0034).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLES = {"receipt": "cash.customer_receipts", "payment": "cash.customer_payments"}
_FULL_NO = re.compile(r"^(RV|PV)-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
FIELDS = ("voucher_date", "customer_id", "source_kind", "treasury_id", "bank_account_id", "amount", "description",
          "paper_no", "project_id")

_SELECT = """
    SELECT v.id, v.voucher_no, v.draft_no, v.voucher_date, v.fiscal_year_id, v.customer_id, v.source_kind,
           v.treasury_id, v.bank_account_id, v.amount, v.description, v.paper_no, v.project_id, v.status,
           v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status,
           c.code AS customer_code, c.name_ar AS customer_name,
           t.code AS treasury_code, t.name_ar AS treasury_name, t.account_id AS treasury_account_id,
           ba.code AS bank_code, b.name_ar AS bank_name, ba.account_id AS bank_account_account_id,
           p.code AS project_code, p.name AS project_name,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {table} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    JOIN party.customers c ON c.id = v.customer_id
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
    LEFT JOIN acc.projects p ON p.id = v.project_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = """
    SELECT v.id, v.voucher_no, v.draft_no, v.voucher_date, v.amount, v.status, v.description, v.paper_no,
           v.source_kind, c.code AS customer_code, c.name_ar AS customer_name,
           coalesce(t.name_ar, b.name_ar) AS source_name
    FROM {table} v
    JOIN party.customers c ON c.id = v.customer_id
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
"""


class CustomerVoucherRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, keyword: str) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append("v.voucher_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("v.voucher_date <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.voucher_no = %s")                      # من الـ unique index على طول
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append("(v.voucher_no ILIKE %s OR v.description ILIKE %s OR v.paper_no ILIKE %s "
                         "OR c.name_ar ILIKE %s OR c.code = %s OR v.draft_no::text = %s)")
            params += [like, like, like, like, keyword, keyword]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_vouchers(self, kind: str, date_from: date | None, date_to: date | None, status: str = "all",
                      keyword: str = "", limit: int | None = None, offset: int = 0,
                      ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, keyword)
        sql = _LIST.format(table=TABLES[kind]) + where + " ORDER BY v.voucher_date DESC, v.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_vouchers(self, kind: str, date_from: date | None, date_to: date | None, status: str = "all",
                       keyword: str = "", ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, keyword)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted, "
            "coalesce(sum(v.amount) FILTER (WHERE v.status = 'posted'), 0) AS posted_amount "
            f"FROM {TABLES[kind]} v JOIN party.customers c ON c.id = v.customer_id" + where, params)

    # ---- السند ------------------------------------------------------------------
    def get_voucher(self, kind: str, voucher_id: int, ex: Executor | None = None,
                    for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT.format(table=TABLES[kind]) + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""),
            [voucher_id])

    def insert_voucher(self, kind: str, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLES[kind]} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in FIELDS], data["voucher_date"], data.get("user_id")]))

    def update_voucher(self, kind: str, voucher_id: int, data: dict[str, Any], ex: Executor,
                       expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        return ex.execute(f"UPDATE {TABLES[kind]} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in FIELDS], voucher_id, *extra])

    def set_status(self, kind: str, voucher_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLES[kind]} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, voucher_id, *extra])

    def delete_voucher(self, kind: str, voucher_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLES[kind]} WHERE id = %s" + clause, [voucher_id, *extra])

    # ---- الاختيارات -------------------------------------------------------------
    def customer_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_active FROM party.customers "
            "ORDER BY CASE WHEN code ~ '^[0-9]+$' THEN lpad(code, 10, '0') ELSE code END")

    def treasury_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT t.id, t.code, t.name_ar, t.currency_code, t.account_id, t.is_active, "
            "t.currency_code = (SELECT code FROM core.currencies WHERE is_base) AS is_base_currency "
            "FROM cash.treasuries t ORDER BY t.seq")

    def bank_account_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT ba.id, ba.code, b.name_ar AS bank_name, ba.account_type, ba.account_number, ba.currency_code, "
            "ba.overdraft_limit, ba.account_id, ba.is_active AND b.is_active AS is_active, "
            "ba.currency_code = (SELECT code FROM core.currencies WHERE is_base) AS is_base_currency "
            "FROM cash.bank_accounts ba JOIN cash.banks b ON b.id = ba.bank_id ORDER BY b.seq, ba.seq")

    def project_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name, status FROM acc.projects ORDER BY order_date DESC NULLS LAST, id DESC")

    # ---- الأرصدة (من القيود المرحّلة) ------------------------------------------------
    def customer_balance(self, customer_id: int, ex: Executor | None = None) -> Decimal:
        """رصيد العميل على حسابات الرقابة (مدين − دائن)."""
        return Decimal(self._x(ex).fetch_value(
            "SELECT coalesce(sum(l.debit - l.credit), 0) FROM acc.journal_lines l "
            "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.customer_id = %s",
            [customer_id]))

    def account_balance(self, account_id: int, ex: Executor | None = None) -> Decimal:
        """رصيد حساب الخزنة / البنك (مدين − دائن)."""
        return Decimal(self._x(ex).fetch_value(
            "SELECT coalesce(sum(l.debit - l.credit), 0) FROM acc.journal_lines l "
            "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.account_id = %s",
            [account_id]))

    def accounts(self, ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all("SELECT id, code, name_ar FROM acc.accounts WHERE id = ANY(%s)", [list(ids)])
        return {int(r["id"]): r for r in rows}

    def year_for(self, d: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, year_code, status, start_date, end_date FROM core.fiscal_years "
            "WHERE %s BETWEEN start_date AND end_date", [d])
