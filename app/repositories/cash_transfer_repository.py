"""SQL «حركات الخزائن والبنوك» (``cash.cash_transfers`` — سكريبت 0037).

* القائمة **بفلتر فترة وصفحات** وبالنوع (التبويبات — index على ``transfer_type, transfer_date``) · رقم الحركة الكامل
  من الـ unique index.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم CT جوه قفل العدّاد وبتتأكد من السنة و«من» و«إلى».
* أرصدة الخزنة / البنك **من سطور القيود المرحّلة** (نفس دوال السندات في ``VoucherRepository``).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLE = "cash.cash_transfers"
_FULL_NO = re.compile(r"^CT-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
TYPES = ("deposit", "withdraw", "t2t", "b2b")
FIELDS = ("transfer_date", "from_kind", "from_treasury_id", "from_bank_account_id", "to_kind", "to_treasury_id",
          "to_bank_account_id", "amount", "fees", "description", "reference_no")

# اسم الطرف: «الخزنة» أو «البنك — الكود».
_END_NAME = "coalesce({t}.name_ar, {b}.name_ar || ' — ' || {ba}.code)"

_JOINS = """
    LEFT JOIN cash.treasuries ft ON ft.id = v.from_treasury_id
    LEFT JOIN cash.bank_accounts fba ON fba.id = v.from_bank_account_id
    LEFT JOIN cash.banks fb ON fb.id = fba.bank_id
    LEFT JOIN cash.treasuries tt ON tt.id = v.to_treasury_id
    LEFT JOIN cash.bank_accounts tba ON tba.id = v.to_bank_account_id
    LEFT JOIN cash.banks tb ON tb.id = tba.bank_id
"""
_FROM_NAME = _END_NAME.format(t="ft", b="fb", ba="fba")
_TO_NAME = _END_NAME.format(t="tt", b="tb", ba="tba")

_SELECT = f"""
    SELECT v.id, v.transfer_no, v.draft_no, v.transfer_date, v.fiscal_year_id, v.transfer_type,
           v.from_kind, v.from_treasury_id, v.from_bank_account_id, v.to_kind, v.to_treasury_id, v.to_bank_account_id,
           v.amount, v.fees, v.description, v.reference_no, v.status,
           v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status,
           coalesce(ft.code, fba.code) AS from_code, {_FROM_NAME} AS from_name,
           coalesce(tt.code, tba.code) AS to_code, {_TO_NAME} AS to_name,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {TABLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    {_JOINS}
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = f"""
    SELECT v.id, v.transfer_no, v.draft_no, v.transfer_date, v.transfer_type, v.amount, v.fees, v.status,
           v.description, v.reference_no, {_FROM_NAME} AS from_name, {_TO_NAME} AS to_name
    FROM {TABLE} v
    {_JOINS}
"""


class CashTransferRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, keyword: str,
               transfer_type: str | None = None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append("v.transfer_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("v.transfer_date <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        if transfer_type in TYPES:
            conds.append("v.transfer_type = %s")
            params.append(transfer_type)
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.transfer_no = %s")                     # من الـ unique index على طول
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append(f"(v.transfer_no ILIKE %s OR v.description ILIKE %s OR v.reference_no ILIKE %s "
                         f"OR {_FROM_NAME} ILIKE %s OR {_TO_NAME} ILIKE %s OR v.draft_no::text = %s)")
            params += [like, like, like, like, like, keyword]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_transfers(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                       transfer_type: str | None = None, limit: int | None = None, offset: int = 0,
                       ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, keyword, transfer_type)
        sql = _LIST + where + " ORDER BY v.transfer_date DESC, v.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_transfers(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                        transfer_type: str | None = None, ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, keyword, transfer_type)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted, "
            "coalesce(sum(v.amount) FILTER (WHERE v.status = 'posted'), 0) AS posted_amount "
            f"FROM {TABLE} v {_JOINS}" + where, params)

    def type_counts(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                    ex: Executor | None = None) -> dict[str, int]:
        """عدد الحركات لكل نوع (أرقام التبويبات) بنفس الفلاتر."""
        where, params = self._where(date_from, date_to, status, keyword)
        rows = self._x(ex).fetch_all(
            f"SELECT v.transfer_type, count(*) AS n FROM {TABLE} v {_JOINS}" + where + " GROUP BY v.transfer_type",
            params)
        return {r["transfer_type"]: int(r["n"]) for r in rows}

    # ---- الحركة ------------------------------------------------------------------
    def get_transfer(self, transfer_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [transfer_id])

    def insert_transfer(self, data: dict[str, Any], ex: Executor) -> int:
        """``transfer_type`` و``fiscal_year_id`` الحقيقيين بتحطهم القاعدة (التريجر) — هنا قيم مبدئية بس."""
        cols = ", ".join((*FIELDS, "transfer_type", "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}, %s, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in FIELDS], data["transfer_type"], data["transfer_date"], data.get("user_id")]))

    def update_transfer(self, transfer_id: int, data: dict[str, Any], ex: Executor,
                        expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        return ex.execute(f"UPDATE {TABLE} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in FIELDS], transfer_id, *extra])

    def set_status(self, transfer_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, transfer_id, *extra])

    def delete_transfer(self, transfer_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLE} WHERE id = %s" + clause, [transfer_id, *extra])
