"""SQL «التحويل بين المخازن» (``inv.stock_transfers`` + ``inv.stock_transfer_lines`` — سكريبت 0046).

* القائمة **بفلتر فترة وصفحات** (index على ``transfer_date``) · رقم التحويل الكامل من الـ unique index.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم TR جوه قفل العدّاد وبتتأكد من السنة والمخزنين ومن حركات
  دفتر المخزون والقيد (آخر الـ transaction).
* السطور بتتكتب من جديد مع كل حفظ للمسودة (التحويل نفسه مقفول ``FOR UPDATE`` بالإصدار).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLE = "inv.stock_transfers"
LINES = "inv.stock_transfer_lines"
_FULL_NO = re.compile(r"^TR-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
FIELDS = ("transfer_date", "from_warehouse_id", "to_warehouse_id", "from_kind", "to_kind", "description")

_JOINS = """
    JOIN inv.warehouses fw ON fw.id = v.from_warehouse_id
    JOIN inv.warehouses tw ON tw.id = v.to_warehouse_id
"""

_SELECT = f"""
    SELECT v.id, v.transfer_no, v.draft_no, v.transfer_date, v.fiscal_year_id, v.from_warehouse_id, v.to_warehouse_id,
           v.from_kind, v.to_kind, v.description, v.status, v.created_by, v.posted_at, v.posted_by, v.created_at,
           v.updated_at, v.row_version, y.year_code, y.status AS year_status,
           fw.code AS from_code, fw.name_ar AS from_name, fw.is_virtual AS from_virtual,
           tw.code AS to_code, tw.name_ar AS to_name, tw.is_virtual AS to_virtual,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {TABLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    {_JOINS}
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = f"""
    SELECT v.id, v.transfer_no, v.draft_no, v.transfer_date, v.from_warehouse_id, v.to_warehouse_id, v.from_kind,
           v.to_kind, v.status, v.description, fw.name_ar AS from_name, fw.is_virtual AS from_virtual, tw.name_ar AS to_name, tw.is_virtual AS to_virtual,
           (SELECT count(*) FROM {LINES} l WHERE l.transfer_id = v.id) AS line_count
    FROM {TABLE} v
    {_JOINS}
"""


class StockTransferRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- الاختيارات ----------------------------------------------------------------
    def warehouse_choices(self, ex: Executor | None = None) -> list[dict]:
        """كل المخازن (الموقوف بيرجع عشان التحويل القديم يتعرض)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_virtual, is_active, is_default FROM inv.warehouses ORDER BY seq")

    def item_choices(self, ex: Executor | None = None) -> list[dict]:
        """الأصناف اللي ليها مخزون (مش خدمية) بالوحدة الأساسية."""
        return self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_active, i.unit_id, u.name_ar AS unit_name, i.category_id "
            "FROM inv.items i JOIN inv.units u ON u.id = i.unit_id WHERE NOT i.is_service ORDER BY i.seq")

    def item_units(self, item_ids: list[int] | None = None, ex: Executor | None = None) -> list[dict]:
        """الوحدات التانية (الوحدة = factor × الأساسية) — لكل الأصناف أو لأصناف معيّنة."""
        sql = ("SELECT iu.item_id, iu.unit_id, iu.factor, u.name_ar AS unit_name "
               "FROM inv.item_units iu JOIN inv.units u ON u.id = iu.unit_id")
        if item_ids is None:
            return self._x(ex).fetch_all(sql + " ORDER BY iu.item_id, iu.factor")
        return self._x(ex).fetch_all(sql + " WHERE iu.item_id = ANY(%s) ORDER BY iu.item_id, iu.factor",
                                     [sorted(set(item_ids))])

    def items(self, item_ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_service, i.is_active, i.unit_id, u.name_ar AS unit_name, i.category_id "
            "FROM inv.items i JOIN inv.units u ON u.id = i.unit_id WHERE i.id = ANY(%s)", [sorted(set(item_ids))])
        return {int(r["id"]): r for r in rows}

    def warehouses(self, ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_virtual, is_active FROM inv.warehouses WHERE id = ANY(%s)",
            [sorted({int(i) for i in ids if i})])
        return {int(r["id"]): r for r in rows}

    def year_of(self, day: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT year_code, status FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date", [day])

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, keyword: str,
               warehouse_id: int | None = None) -> tuple[str, list[Any]]:
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
        if warehouse_id:
            conds.append("(v.from_warehouse_id = %s OR v.to_warehouse_id = %s)")
            params += [warehouse_id, warehouse_id]
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.transfer_no = %s")                     # من الـ unique index على طول
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append("(v.transfer_no ILIKE %s OR v.description ILIKE %s OR fw.name_ar ILIKE %s "
                         "OR tw.name_ar ILIKE %s OR v.draft_no::text = %s OR EXISTS (SELECT 1 FROM "
                         f"{LINES} l JOIN inv.items i ON i.id = l.item_id WHERE l.transfer_id = v.id "
                         "AND (i.code ILIKE %s OR i.name ILIKE %s)))")
            params += [like, like, like, like, keyword, like, like]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_transfers(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                       warehouse_id: int | None = None, limit: int | None = None, offset: int = 0,
                       ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, keyword, warehouse_id)
        sql = _LIST + where + " ORDER BY v.transfer_date DESC, v.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_transfers(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                        warehouse_id: int | None = None, ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, keyword, warehouse_id)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted "
            f"FROM {TABLE} v {_JOINS}" + where, params)

    # ---- التحويل ------------------------------------------------------------------
    def get_transfer(self, transfer_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [transfer_id])

    def lines(self, transfer_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT l.id, l.line_no, l.item_id, l.unit_id, l.factor, l.qty, l.base_qty, i.code AS item_code, "
            "i.name AS item_name, i.category_id, i.unit_id AS base_unit_id, bu.name_ar AS base_unit_name, "
            "u.name_ar AS unit_name "
            f"FROM {LINES} l JOIN inv.items i ON i.id = l.item_id JOIN inv.units u ON u.id = l.unit_id "
            "JOIN inv.units bu ON bu.id = i.unit_id WHERE l.transfer_id = %s ORDER BY l.line_no", [transfer_id])

    def insert_transfer(self, data: dict[str, Any], ex: Executor) -> int:
        """``fiscal_year_id`` الحقيقي بتحطه القاعدة (التريجر) — هنا قيمة مبدئية بس."""
        cols = ", ".join((*FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in FIELDS], data["transfer_date"], data.get("user_id")]))

    def update_transfer(self, transfer_id: int, data: dict[str, Any], ex: Executor,
                        expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        # row_version بيزيد بطلب صريح — حتى لو الرأس زي ما هو والسطور بس اللي اتغيّرت (جهاز تاني يعرف).
        return ex.execute(f"UPDATE {TABLE} SET {sets}, row_version = row_version + 1 "
                          "WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in FIELDS], transfer_id, *extra])

    def replace_lines(self, transfer_id: int, lines: list[dict[str, Any]], ex: Executor) -> None:
        ex.execute(f"DELETE FROM {LINES} WHERE transfer_id = %s", [transfer_id])
        for n, line in enumerate(lines, start=1):
            ex.execute(f"INSERT INTO {LINES} (transfer_id, line_no, item_id, unit_id, factor, qty) "
                       "VALUES (%s, %s, %s, %s, %s, %s)",
                       [transfer_id, n, line["item_id"], line["unit_id"], line["factor"], line["qty"]])

    def set_status(self, transfer_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, transfer_id, *extra])

    def delete_transfer(self, transfer_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLE} WHERE id = %s" + clause, [transfer_id, *extra])

    # ---- حركات المخزون بتاعة التحويل المرحّل --------------------------------------------
    def movements(self, transfer_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT line_no, item_id, warehouse_id, stock_kind, qty, unit_cost, value, booked_value, is_virtual "
            "FROM inv.stock_movements WHERE source_type = 'transfer' AND source_id = %s ORDER BY line_no",
            [transfer_id])
