"""SQL «الجرد والتسويات» (``inv.stock_counts`` + ``inv.stock_count_lines`` — سكريبت 0047).

* القائمة **بفلتر فترة وصفحات** (index على ``count_date``) · رقم الجرد الكامل من الـ unique index.
* **الرصيد الدفتري** = مجموع حركات دفتر المخزون للمخزن لحد آخر يوم الجرد (لكل صنف + نوع) · المتوسط = آخر
  ``avg_cost_after`` لحد اليوم ده.
* **كل سطر بإصداره** (``row_version``): السطر بيتضاف / يتعدّل / يتمسح لوحده، والرأس بيتعدّل بس لو بياناته اتغيّرت —
  عشان 2–3 أجهزة يعدّوا نفس المخزن في نفس الجرد.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم SC جوه قفل العدّاد وبتتأكد من الحركات والقيد.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLE = "inv.stock_counts"
LINES = "inv.stock_count_lines"
STOCK_DOC = "stock_adjustment"
_FULL_NO = re.compile(r"^SC-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
FIELDS = ("count_date", "warehouse_id", "count_type", "zero_uncounted", "description")

_SELECT = f"""
    SELECT v.id, v.count_no, v.draft_no, v.count_date, v.fiscal_year_id, v.warehouse_id, v.count_type,
           v.zero_uncounted, v.description, v.status, v.created_by, v.posted_at, v.posted_by, v.created_at,
           v.updated_at, v.row_version, y.year_code, y.status AS year_status,
           w.code AS warehouse_code, w.name_ar AS warehouse_name, w.is_virtual, w.is_active AS warehouse_active,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {TABLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    JOIN inv.warehouses w ON w.id = v.warehouse_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = f"""
    SELECT v.id, v.count_no, v.draft_no, v.count_date, v.warehouse_id, v.count_type, v.status, v.description,
           w.name_ar AS warehouse_name, w.is_virtual,
           (SELECT count(*) FROM {LINES} l WHERE l.count_id = v.id) AS line_count,
           (SELECT coalesce(sum(m.value), 0) FROM inv.stock_movements m
             WHERE m.source_type = '{STOCK_DOC}' AND m.source_id = v.id AND NOT m.is_virtual) AS net_value
    FROM {TABLE} v
    JOIN inv.warehouses w ON w.id = v.warehouse_id
"""

_LINE = f"""
    SELECT l.id, l.count_id, l.item_id, l.stock_kind, l.counted_qty, l.adjust_qty, l.reason, l.unit_cost,
           l.seen_book_qty, l.book_qty, l.diff_qty, l.auto_zero, l.row_version, i.code AS item_code,
           i.name AS item_name, i.is_active AS item_active, i.category_id, u.name_ar AS unit_name,
           c.name AS category_name
    FROM {LINES} l
    JOIN inv.items i ON i.id = l.item_id
    JOIN inv.units u ON u.id = i.unit_id
    LEFT JOIN inv.item_categories c ON c.id = i.category_id
"""


class StockCountRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- الاختيارات ----------------------------------------------------------------
    def warehouse_choices(self, ex: Executor | None = None) -> list[dict]:
        """كل المخازن (الموقوف بيرجع عشان الجرد القديم يتعرض)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_virtual, is_active, is_default FROM inv.warehouses ORDER BY seq")

    def item_choices(self, ex: Executor | None = None) -> list[dict]:
        """الأصناف اللي ليها مخزون (مش خدمية)."""
        return self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_active FROM inv.items i WHERE NOT i.is_service ORDER BY i.seq")

    def items(self, item_ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        if not item_ids:
            return {}
        rows = self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_service, i.is_active, i.category_id, u.name_ar AS unit_name, "
            "c.name AS category_name FROM inv.items i JOIN inv.units u ON u.id = i.unit_id "
            "LEFT JOIN inv.item_categories c ON c.id = i.category_id WHERE i.id = ANY(%s)",
            [sorted({int(i) for i in item_ids})])
        return {int(r["id"]): r for r in rows}

    def items_by_code(self, ex: Executor | None = None) -> dict[str, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_active FROM inv.items i WHERE NOT i.is_service")
        return {str(r["code"]).strip().upper(): r for r in rows}

    def warehouse(self, warehouse_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar, is_virtual, is_active FROM inv.warehouses WHERE id = %s", [warehouse_id])

    def year_of(self, day: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT year_code, status FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date", [day])

    # ---- الرصيد الدفتري والمتوسط -----------------------------------------------------
    def book_balances(self, warehouse_id: int, day: date, exclude_count: int | None = None,
                      ex: Executor | None = None) -> dict[tuple[int, str], Decimal]:
        """رصيد المخزن آخر يوم ``day`` لكل (صنف + نوع) — اللي مش صفر بس · ``exclude_count`` = من غير حركات الجرد ده."""
        sql = ("SELECT item_id, stock_kind, sum(qty) AS qty FROM inv.stock_movements "
               "WHERE warehouse_id = %s AND movement_date <= %s")
        params: list[Any] = [warehouse_id, day]
        if exclude_count:
            sql += " AND NOT (source_type = %s AND source_id = %s)"
            params += [STOCK_DOC, exclude_count]
        rows = self._x(ex).fetch_all(sql + " GROUP BY item_id, stock_kind HAVING sum(qty) <> 0", params)
        return {(int(r["item_id"]), r["stock_kind"]): Decimal(r["qty"]) for r in rows}

    def average_costs(self, item_ids: list[int], day: date, ex: Executor | None = None
                      ) -> dict[tuple[int, str], Decimal]:
        """متوسط التكلفة آخر يوم ``day`` لكل (صنف + نوع) من الأصناف دي (المخازن الحقيقية)."""
        if not item_ids:
            return {}
        rows = self._x(ex).fetch_all(
            "SELECT DISTINCT ON (item_id, stock_kind) item_id, stock_kind, avg_cost_after FROM inv.stock_movements "
            "WHERE item_id = ANY(%s) AND NOT is_virtual AND movement_date <= %s "
            "ORDER BY item_id, stock_kind, movement_date DESC, day_seq DESC, id DESC",
            [sorted({int(i) for i in item_ids}), day])
        return {(int(r["item_id"]), r["stock_kind"]): Decimal(r["avg_cost_after"] or 0) for r in rows}

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, keyword: str) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append("v.count_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("v.count_date <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.count_no = %s")                        # من الـ unique index على طول
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append("(v.count_no ILIKE %s OR v.description ILIKE %s OR w.name_ar ILIKE %s "
                         "OR v.draft_no::text = %s OR EXISTS (SELECT 1 FROM "
                         f"{LINES} l JOIN inv.items i ON i.id = l.item_id WHERE l.count_id = v.id "
                         "AND (i.code ILIKE %s OR i.name ILIKE %s)))")
            params += [like, like, like, keyword, like, like]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_counts(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                    limit: int | None = None, offset: int = 0, ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, keyword)
        sql = _LIST + where + " ORDER BY v.count_date DESC, v.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_counts(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                     ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, keyword)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted "
            f"FROM {TABLE} v JOIN inv.warehouses w ON w.id = v.warehouse_id" + where, params)

    # ---- الجرد (الرأس) ---------------------------------------------------------------
    def get_count(self, count_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [count_id])

    def insert_count(self, data: dict[str, Any], ex: Executor) -> int:
        """``fiscal_year_id`` الحقيقي بتحطه القاعدة (التريجر) — هنا قيمة مبدئية بس."""
        cols = ", ".join((*FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in FIELDS], data["count_date"], data.get("user_id")]))

    def update_count(self, count_id: int, data: dict[str, Any], ex: Executor,
                     expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        return ex.execute(f"UPDATE {TABLE} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in FIELDS], count_id, *extra])

    def set_status(self, count_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, count_id, *extra])

    def delete_count(self, count_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLE} WHERE id = %s" + clause, [count_id, *extra])

    # ---- السطور ---------------------------------------------------------------------
    def lines(self, count_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(_LINE + " WHERE l.count_id = %s ORDER BY i.seq, l.stock_kind", [count_id])

    def find_line(self, count_id: int, item_id: int, stock_kind: str, ex: Executor,
                  for_update: bool = False) -> dict | None:
        return ex.fetch_one(
            _LINE + " WHERE l.count_id = %s AND l.item_id = %s AND l.stock_kind = %s"
            + (" FOR UPDATE OF l" if for_update else ""), [count_id, item_id, stock_kind])

    def insert_line(self, count_id: int, data: dict[str, Any], user_id: int | None, ex: Executor) -> int:
        return int(ex.fetch_value(
            f"INSERT INTO {LINES} (count_id, item_id, stock_kind, counted_qty, adjust_qty, reason, unit_cost, "
            "seen_book_qty, auto_zero, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            [count_id, data["item_id"], data["stock_kind"], data.get("counted_qty"), data.get("adjust_qty"),
             data.get("reason"), data.get("unit_cost"), data.get("seen_book_qty"), bool(data.get("auto_zero")),
             user_id]))

    def update_line(self, line_id: int, data: dict[str, Any], user_id: int | None, ex: Executor,
                    expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(
            f"UPDATE {LINES} SET counted_qty = %s, adjust_qty = %s, reason = %s, unit_cost = %s, "
            "seen_book_qty = %s, updated_by = %s WHERE id = %s" + clause,
            [data.get("counted_qty"), data.get("adjust_qty"), data.get("reason"), data.get("unit_cost"),
             data.get("seen_book_qty"), user_id, line_id, *extra])

    def delete_line(self, line_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {LINES} WHERE id = %s" + clause, [line_id, *extra])

    def delete_lines(self, count_id: int, ex: Executor) -> int:
        return ex.execute(f"DELETE FROM {LINES} WHERE count_id = %s", [count_id])

    def set_posted_values(self, line_id: int, book_qty: Decimal, diff_qty: Decimal, ex: Executor) -> None:
        ex.execute(f"UPDATE {LINES} SET book_qty = %s, diff_qty = %s WHERE id = %s", [book_qty, diff_qty, line_id])

    def clear_posted_values(self, count_id: int, ex: Executor) -> None:
        """إلغاء الترحيل: السطور اللي اتعملت لوحدها بتتمسح، والباقي يرجع من غير الدفتري والفرق."""
        ex.execute(f"DELETE FROM {LINES} WHERE count_id = %s AND auto_zero", [count_id])
        ex.execute(f"UPDATE {LINES} SET seen_book_qty = book_qty, book_qty = NULL, diff_qty = NULL "
                   "WHERE count_id = %s", [count_id])

    # ---- حركات المخزون بتاعة الجرد المرحّل --------------------------------------------
    def movements(self, count_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT line_no, item_id, stock_kind, qty, unit_cost, value, booked_value, is_virtual "
            "FROM inv.stock_movements WHERE source_type = %s AND source_id = %s ORDER BY line_no",
            [STOCK_DOC, count_id])
