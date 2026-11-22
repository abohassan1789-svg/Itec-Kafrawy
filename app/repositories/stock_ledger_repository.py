"""SQL «دفتر المخزون» (``inv.stock_movements`` + ``inv.stock_balances`` + ``inv.item_costs`` — سكريبت 0043).

* قفل صفوف الأصناف (``FOR NO KEY UPDATE`` بالترتيب — مفيش deadlock) قبل أي كتابة.
* إضافة / مسح حركات مستند · قراية الحالة قبل يوم معيّن · حركات الصنف من يوم معيّن · حفظ اللي اتحسب.
* الأرصدة الحالية بتتحسب تاني من الدفتر بعد كل كتابة (مصدر الحقيقة = الدفتر).
* قراية: المتاح · المتوسط · ملخص الأصناف والمخازن · فروق التكلفة.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db

_MOVEMENT_COLUMNS = ("source_type", "source_id", "source_no", "line_no", "movement_date", "item_id",
                     "warehouse_id", "stock_kind", "qty", "cost_mode", "given_cost", "pair_id", "is_virtual",
                     "created_by")
_COMPUTED = ("unit_cost", "value", "kind_qty_after", "kind_value_after", "avg_cost_after", "wh_qty_after", "day_seq")


class StockLedgerRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- المراجع -----------------------------------------------------------------
    def doc_type(self, code: str, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT code, name_ar FROM inv.stock_doc_types WHERE code = %s", [code])

    def lock_items(self, item_ids: list[int], ex: Executor) -> dict[int, dict]:
        """قفل الأصناف بالترتيب ← بياناتها (الكود · الاسم · خدمي · الوحدة)."""
        rows = ex.fetch_all(
            "SELECT i.id, i.seq, i.name, i.is_service, u.name_ar AS unit_name "
            "FROM inv.items i JOIN inv.units u ON u.id = i.unit_id "
            "WHERE i.id = ANY(%s) ORDER BY i.id FOR NO KEY UPDATE OF i", [sorted(set(item_ids))])
        return {int(r["id"]): r for r in rows}

    def warehouses(self, warehouse_ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT id, seq, name_ar, is_virtual, is_active FROM inv.warehouses WHERE id = ANY(%s)",
            [sorted(set(warehouse_ids))])
        return {int(r["id"]): r for r in rows}

    # ---- حركات المستند --------------------------------------------------------------
    def source_movements(self, source_type: str, source_id: int, ex: Executor) -> list[dict]:
        return ex.fetch_all(
            "SELECT id, item_id, movement_date FROM inv.stock_movements "
            "WHERE source_type = %s AND source_id = %s ORDER BY line_no", [source_type, source_id])

    def delete_source(self, source_type: str, source_id: int, ex: Executor) -> int:
        return ex.execute("DELETE FROM inv.stock_movements WHERE source_type = %s AND source_id = %s",
                          [source_type, source_id])

    def insert_movement(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join(_MOVEMENT_COLUMNS)
        marks = ", ".join(["%s"] * len(_MOVEMENT_COLUMNS))
        return int(ex.fetch_value(f"INSERT INTO inv.stock_movements ({cols}) VALUES ({marks}) RETURNING id",
                                  [data.get(c) for c in _MOVEMENT_COLUMNS]))

    # ---- إعادة الحساب ----------------------------------------------------------------
    def chain_state_before(self, item_id: int, day: date, ex: Executor) -> dict[str, dict]:
        """حالة كل نوع (فاتورة / بيان) آخر اليوم اللي قبل ``day`` (آخر حركة اتحسبت)."""
        rows = ex.fetch_all(
            "SELECT DISTINCT ON (stock_kind) stock_kind, kind_qty_after AS qty, kind_value_after AS value, "
            "avg_cost_after AS avg FROM inv.stock_movements "
            "WHERE item_id = %s AND movement_date < %s AND NOT is_virtual "
            "ORDER BY stock_kind, movement_date DESC, day_seq DESC, id DESC", [item_id, day])
        return {r["stock_kind"]: r for r in rows}

    def warehouse_qty_before(self, item_id: int, day: date, ex: Executor) -> dict[tuple[int, str], Decimal]:
        rows = ex.fetch_all(
            "SELECT warehouse_id, stock_kind, sum(qty) AS qty FROM inv.stock_movements "
            "WHERE item_id = %s AND movement_date < %s GROUP BY warehouse_id, stock_kind", [item_id, day])
        return {(int(r["warehouse_id"]), r["stock_kind"]): r["qty"] for r in rows}

    def movements_from(self, item_id: int, day: date, ex: Executor) -> list[dict]:
        return ex.fetch_all(
            "SELECT id, source_type, source_id, source_no, line_no, movement_date, warehouse_id, stock_kind, qty, "
            "cost_mode, given_cost, pair_id, is_virtual, unit_cost, value, booked_value, kind_qty_after, "
            "kind_value_after, avg_cost_after, wh_qty_after, day_seq "
            "FROM inv.stock_movements WHERE item_id = %s AND movement_date >= %s ORDER BY movement_date, id",
            [item_id, day])

    def save_computed(self, movement_id: int, values: dict[str, Any], ex: Executor) -> None:
        sets = ", ".join(f"{c} = %s" for c in _COMPUTED)
        ex.execute(f"UPDATE inv.stock_movements SET {sets} WHERE id = %s",
                   [values[c] for c in _COMPUTED] + [movement_id])

    def book(self, movement_ids: list[int], ex: Executor) -> None:
        """القيمة اللي اتحسبت دلوقتي = اللي هتتقيّد (الحركات الجديدة)."""
        if movement_ids:
            ex.execute("UPDATE inv.stock_movements SET booked_value = value WHERE id = ANY(%s)", [movement_ids])

    def refresh_balances(self, item_id: int, ex: Executor) -> None:
        """الأرصدة الحالية من الدفتر نفسه (المخازن · والمتوسط من آخر حركة اتحسبت)."""
        ex.execute("DELETE FROM inv.stock_balances WHERE item_id = %s", [item_id])
        ex.execute(
            "INSERT INTO inv.stock_balances (item_id, warehouse_id, stock_kind, qty) "
            "SELECT item_id, warehouse_id, stock_kind, sum(qty) FROM inv.stock_movements WHERE item_id = %s "
            "GROUP BY item_id, warehouse_id, stock_kind HAVING sum(qty) <> 0", [item_id])
        ex.execute("DELETE FROM inv.item_costs WHERE item_id = %s", [item_id])
        ex.execute(
            "INSERT INTO inv.item_costs (item_id, stock_kind, qty, value, avg_cost, last_date) "
            "SELECT DISTINCT ON (stock_kind) item_id, stock_kind, kind_qty_after, kind_value_after, "
            "coalesce(avg_cost_after, 0), movement_date FROM inv.stock_movements "
            "WHERE item_id = %s AND NOT is_virtual "
            "ORDER BY stock_kind, movement_date DESC, day_seq DESC, id DESC", [item_id])

    # ---- قراية -------------------------------------------------------------------
    def balance_now(self, item_id: int, warehouse_id: int | None, stock_kind: str | None,
                    ex: Executor | None = None) -> Decimal:
        return self._x(ex).fetch_value(
            "SELECT coalesce(sum(b.qty), 0) FROM inv.stock_balances b JOIN inv.warehouses w ON w.id = b.warehouse_id "
            "WHERE b.item_id = %s AND (%s::int IS NULL AND NOT w.is_virtual OR b.warehouse_id = %s) "
            "AND (%s::text IS NULL OR b.stock_kind = %s)",
            [item_id, warehouse_id, warehouse_id, stock_kind, stock_kind])

    def min_balance_from(self, item_id: int, warehouse_id: int, stock_kind: str, day: date,
                         ex: Executor | None = None) -> Decimal:
        """أقل رصيد آخر يوم من ``day`` لقدّام — ده اللي ينفع يخرج يوم ``day`` من غير ما يوم بعده يبقى بالسالب."""
        return self._x(ex).fetch_value(
            "WITH days AS (SELECT movement_date, sum(qty) AS q FROM inv.stock_movements "
            "  WHERE item_id = %s AND warehouse_id = %s AND stock_kind = %s GROUP BY movement_date), "
            "run AS (SELECT movement_date, sum(q) OVER (ORDER BY movement_date) AS bal FROM days) "
            "SELECT least((SELECT coalesce(sum(q), 0) FROM days WHERE movement_date <= %s), "
            "             coalesce((SELECT min(bal) FROM run WHERE movement_date >= %s), 'Infinity'::numeric))",
            [item_id, warehouse_id, stock_kind, day, day])

    def avg_cost(self, item_id: int, stock_kind: str, day: date | None, ex: Executor | None = None) -> Decimal | None:
        if day is None:
            return self._x(ex).fetch_value(
                "SELECT avg_cost FROM inv.item_costs WHERE item_id = %s AND stock_kind = %s", [item_id, stock_kind])
        return self._x(ex).fetch_value(
            "SELECT avg_cost_after FROM inv.stock_movements WHERE item_id = %s AND stock_kind = %s "
            "AND NOT is_virtual AND movement_date <= %s ORDER BY movement_date DESC, day_seq DESC, id DESC LIMIT 1",
            [item_id, stock_kind, day])

    def item_summary(self, item_id: int, ex: Executor | None = None) -> dict:
        x = self._x(ex)
        kinds = x.fetch_all("SELECT stock_kind, qty, value, avg_cost FROM inv.item_costs WHERE item_id = %s "
                            "ORDER BY stock_kind", [item_id])
        warehouses = x.fetch_all(
            "SELECT b.warehouse_id, w.name_ar, w.is_virtual, b.stock_kind, b.qty FROM inv.stock_balances b "
            "JOIN inv.warehouses w ON w.id = b.warehouse_id WHERE b.item_id = %s ORDER BY w.seq, b.stock_kind",
            [item_id])
        moved = bool(x.fetch_value("SELECT EXISTS (SELECT 1 FROM inv.stock_movements WHERE item_id = %s)", [item_id]))
        return {"kinds": kinds, "warehouses": warehouses, "has_movements": moved}

    def below_reorder_count(self, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value(
            "SELECT count(*) FROM inv.items i WHERE i.is_active AND NOT i.is_service AND i.reorder_level IS NOT NULL "
            "AND coalesce((SELECT sum(c.qty) FROM inv.item_costs c WHERE c.item_id = i.id), 0) <= i.reorder_level"))

    def total_value(self, ex: Executor | None = None) -> Decimal:
        return self._x(ex).fetch_value("SELECT coalesce(sum(value), 0) FROM inv.item_costs")

    def warehouse_summary(self, ex: Executor | None = None) -> dict[int, dict]:
        """لكل مخزن: عدد الأصناف اللي ليها رصيد + القيمة (الكمية × متوسط الشركة)."""
        rows = self._x(ex).fetch_all(
            "SELECT b.warehouse_id, count(DISTINCT b.item_id) AS items, "
            "round(sum(CASE WHEN w.is_virtual THEN 0 ELSE b.qty * coalesce(c.avg_cost, 0) END), 2) AS value "
            "FROM inv.stock_balances b JOIN inv.warehouses w ON w.id = b.warehouse_id "
            "LEFT JOIN inv.item_costs c ON c.item_id = b.item_id AND c.stock_kind = b.stock_kind "
            "WHERE b.qty > 0 GROUP BY b.warehouse_id")
        return {int(r["warehouse_id"]): {"items": int(r["items"]), "value": r["value"]} for r in rows}

    def cost_differences(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT m.source_type, t.name_ar AS source_name, m.source_id, m.source_no, "
            "sum(m.value - m.booked_value) AS difference FROM inv.stock_movements m "
            "JOIN inv.stock_doc_types t ON t.code = m.source_type WHERE m.value <> m.booked_value "
            "GROUP BY m.source_type, t.name_ar, m.source_id, m.source_no ORDER BY m.source_type, m.source_id")

    def source_lines(self, source_type: str, source_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id AS movement_id, line_no, item_id, warehouse_id, stock_kind, qty, unit_cost, value, booked_value "
            "FROM inv.stock_movements WHERE source_type = %s AND source_id = %s ORDER BY line_no",
            [source_type, source_id])
