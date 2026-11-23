"""SQL «بضاعة أول المدة» (``inv.opening_stock_lines`` — سكريبت 0045) — المرحلة 6.

* جدول الشاشة: **كل الأصناف المخزنية** لمخزن + نوع (فاتورة / بيان) ومع كل صنف سطره (لو ليه رصيد).
* السطر: إضافة / تعديل بالإصدار (``row_version``) / مسح بالإصدار — جهازين بيكتبوا أصناف مختلفة الاتنين بيتحفظوا.
* الملخص · سطور الترحيل (بنوع الصنف عشان حساب المخزون) · مراجع الاستيراد من إكسل.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db

_LINE = """
    SELECT l.id, l.item_id, l.warehouse_id, l.stock_kind, l.qty, l.unit_cost, l.row_version, l.updated_at,
           i.code AS item_code, i.name AS item_name, i.category_id, i.is_active AS item_active,
           u.name_ar AS unit_name, w.code AS warehouse_code, w.name_ar AS warehouse_name, w.is_virtual,
           CASE WHEN w.is_virtual THEN 0 ELSE round(l.qty * l.unit_cost, 2) END AS value
    FROM inv.opening_stock_lines l
    JOIN inv.items i ON i.id = l.item_id
    JOIN inv.units u ON u.id = i.unit_id
    JOIN inv.warehouses w ON w.id = l.warehouse_id
"""


class OpeningStockRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- جدول الشاشة -----------------------------------------------------------
    def grid(self, warehouse_id: int, stock_kind: str, ex: Executor | None = None) -> list[dict]:
        """كل الأصناف المخزنية (والموقوف لو ليه سطر) + سطرها في المخزن والنوع دول."""
        return self._x(ex).fetch_all(
            "SELECT i.id AS item_id, i.code, i.name, i.is_active, u.name_ar AS unit_name, "
            "l.id AS line_id, l.qty, l.unit_cost, l.row_version "
            "FROM inv.items i JOIN inv.units u ON u.id = i.unit_id "
            "LEFT JOIN inv.opening_stock_lines l ON l.item_id = i.id AND l.warehouse_id = %s AND l.stock_kind = %s "
            "WHERE NOT i.is_service AND (i.is_active OR l.id IS NOT NULL) ORDER BY i.seq",
            [warehouse_id, stock_kind])

    def lines(self, ex: Executor | None = None, item_id: int | None = None) -> list[dict]:
        sql = _LINE + (" WHERE l.item_id = %s" if item_id else "") + " ORDER BY i.seq, w.seq, l.stock_kind"
        return self._x(ex).fetch_all(sql, [item_id] if item_id else [])

    def line(self, line_id: int, ex: Executor, for_update: bool = False) -> dict | None:
        return ex.fetch_one(_LINE + " WHERE l.id = %s" + (" FOR UPDATE OF l" if for_update else ""), [line_id])

    def find(self, item_id: int, warehouse_id: int, stock_kind: str, ex: Executor | None = None,
             for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _LINE + " WHERE l.item_id = %s AND l.warehouse_id = %s AND l.stock_kind = %s"
            + (" FOR UPDATE OF l" if for_update else ""), [item_id, warehouse_id, stock_kind])

    def insert(self, data: dict[str, Any], user_id: int | None, ex: Executor) -> int:
        return int(ex.fetch_value(
            "INSERT INTO inv.opening_stock_lines (item_id, warehouse_id, stock_kind, qty, unit_cost, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            [data["item_id"], data["warehouse_id"], data["stock_kind"], data["qty"], data["unit_cost"], user_id]))

    def update(self, line_id: int, qty: Any, unit_cost: Any, user_id: int | None, ex: Executor,
               expected_version: int | None = None) -> int:
        sql = "UPDATE inv.opening_stock_lines SET qty = %s, unit_cost = %s, updated_by = %s WHERE id = %s"
        params: list[Any] = [qty, unit_cost, user_id, line_id]
        if expected_version is not None:
            sql += " AND row_version = %s"
            params.append(expected_version)
        return ex.execute(sql, params)

    def delete(self, line_id: int, ex: Executor, expected_version: int | None = None) -> int:
        sql = "DELETE FROM inv.opening_stock_lines WHERE id = %s"
        params: list[Any] = [line_id]
        if expected_version is not None:
            sql += " AND row_version = %s"
            params.append(expected_version)
        return ex.execute(sql, params)

    def delete_all(self, ex: Executor) -> int:
        return ex.execute("DELETE FROM inv.opening_stock_lines")

    # ---- الملخص -----------------------------------------------------------------
    def summary(self, ex: Executor | None = None) -> dict:
        return self._x(ex).fetch_one(
            f"SELECT count(*) AS lines, count(DISTINCT x.item_id) AS items, "
            f"coalesce(sum(x.value) FILTER (WHERE x.stock_kind = 'invoice'), 0) AS invoice_value, "
            f"coalesce(sum(x.value) FILTER (WHERE x.stock_kind = 'statement'), 0) AS statement_value, "
            f"coalesce(sum(x.value), 0) AS value, "
            f"count(*) FILTER (WHERE NOT x.is_virtual AND x.unit_cost = 0) AS zero_cost FROM ({_LINE}) x")

    def inventory_account(self, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT a.id, a.code, a.name_ar FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
            "WHERE d.key = 'inventory'")

    # ---- مراجع الشاشة والاستيراد ------------------------------------------------------
    def warehouses(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_virtual, is_default, is_active FROM inv.warehouses ORDER BY seq")

    def items_by_code(self, ex: Executor | None = None) -> dict[str, dict]:
        rows = self._x(ex).fetch_all("SELECT id, code, name, is_service, is_active FROM inv.items")
        return {str(r["code"]).upper(): r for r in rows}

    def item(self, item_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT i.id, i.code, i.name, i.is_service, i.default_warehouse_id FROM inv.items i WHERE i.id = %s",
            [item_id])
