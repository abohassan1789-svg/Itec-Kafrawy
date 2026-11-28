"""SQL «مرتجع المبيعات» (``sales.sales_returns`` + ``sales.sales_return_lines`` + ``sales.return_reasons`` —
سكريبت 0053) — نفس شكل مرتجع المشتريات (``purchase_return_repository``) بس للعميل وفواتير البيع.

* القائمة **بفلتر فترة وصفحات** (index على ``return_date``) · الرقم الكامل من الـ unique index.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم SRT جوه قفل العدّاد وبتتأكد من حركات المخزون والقيد
  ومن إن الكمية المرتجعة لكل سطر فاتورة ≤ اللي اتباع (آخر الـ transaction).
* ``invoice_lines_for`` = سطور فواتير البيع المرحّلة ومعاها **اللي رجع قبل كده** (من المرتجعات المرحّلة) و**تكلفة
  الوحدة الأساسية اللي طلعت بيها** (``booked_value`` بتاع حركة السطر في دفتر المخزون ÷ كميتها).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLE = "sales.sales_returns"
LINES = "sales.sales_return_lines"
_FULL_NO = re.compile(r"^SRT-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
FIELDS = ("return_date", "customer_id", "customer_ref", "reason_id", "stock_kind", "pay_kind", "source_kind",
          "treasury_id", "bank_account_id", "warehouse_id", "project_id", "currency_code", "fx_rate", "net_amount",
          "vat_amount", "total_amount", "net_base", "vat_base", "total_base", "description")
LINE_FIELDS = ("source_line_id", "item_id", "warehouse_id", "unit_id", "factor", "qty", "unit_price", "tax_code_id",
               "tax_rate", "currency_code", "fx_rate", "net_amount", "vat_amount", "total_amount", "net_base",
               "vat_base", "total_base", "unit_cost")

_SELECT = f"""
    SELECT v.*, y.year_code, y.status AS year_status,
           c.code AS customer_code, c.name_ar AS customer_name, c.tax_reg_no AS customer_tax_no,
           c.is_system AS customer_is_cash,
           rr.name_ar AS reason_name, w.name_ar AS warehouse_name, t.name_ar AS treasury_name,
           ba.code AS bank_code, b.name_ar AS bank_name, p.code AS project_code, p.name AS project_name,
           p.cost_center_id AS project_cost_center_id,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {TABLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    JOIN party.customers c ON c.id = v.customer_id
    LEFT JOIN sales.return_reasons rr ON rr.id = v.reason_id
    LEFT JOIN inv.warehouses w ON w.id = v.warehouse_id
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
    LEFT JOIN acc.projects p ON p.id = v.project_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = f"""
    SELECT v.id, v.return_no, v.draft_no, v.return_date, v.customer_id, v.customer_ref, v.stock_kind,
           v.pay_kind, v.currency_code, v.total_amount, v.total_base, v.status, v.description,
           c.code AS customer_code, c.name_ar AS customer_name,
           (SELECT count(*) FROM {LINES} l WHERE l.return_id = v.id) AS line_count
    FROM {TABLE} v
    JOIN party.customers c ON c.id = v.customer_id
"""

# اللي رجع من سطر فاتورة (المرتجعات المرحّلة — غير المرتجع المفتوح).
_RETURNED = f"""(SELECT coalesce(sum(x.base_qty), 0) FROM {LINES} x
                  JOIN {TABLE} r ON r.id = x.return_id AND r.status = 'posted'
                 WHERE x.source_line_id = sl.id AND r.id IS DISTINCT FROM %s)"""

# تكلفة الوحدة الأساسية اللي السطر طلع بيها: حركة الفاتورة رقمها = ترتيب السطر بين السطور المخزنية (زي الترحيل).
_SOURCE_COST = """(SELECT m.booked_value / m.qty FROM inv.stock_movements m
                    WHERE m.source_type = 'sales_invoice' AND m.source_id = sl.invoice_id AND m.qty <> 0
                      AND m.line_no = (SELECT count(*) FROM sales.sales_invoice_lines x
                                         JOIN inv.items xi ON xi.id = x.item_id
                                        WHERE x.invoice_id = sl.invoice_id AND NOT xi.is_service
                                          AND x.line_no <= sl.line_no))"""


class SalesReturnRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- أسباب المرتجع ---------------------------------------------------------
    def reasons(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT r.id, r.name_ar, r.sort_order, r.is_active, "
            f"EXISTS (SELECT 1 FROM {TABLE} v WHERE v.reason_id = r.id) AS used "
            "FROM sales.return_reasons r ORDER BY r.sort_order, r.id")

    def insert_reason(self, name: str, ex: Executor) -> int:
        return int(ex.fetch_value(
            "INSERT INTO sales.return_reasons (name_ar, sort_order) VALUES "
            "(%s, (SELECT coalesce(max(sort_order), 0) + 1 FROM sales.return_reasons)) RETURNING id", [name]))

    def update_reason(self, reason_id: int, name: str, is_active: bool, ex: Executor) -> int:
        return ex.execute("UPDATE sales.return_reasons SET name_ar = %s, is_active = %s WHERE id = %s",
                          [name, is_active, reason_id])

    def delete_reason(self, reason_id: int, ex: Executor) -> int:
        return ex.execute("DELETE FROM sales.return_reasons WHERE id = %s", [reason_id])

    # ---- فواتير البيع المرحّلة (الأصل) --------------------------------------------
    @staticmethod
    def _returnable(customer_id: int | None, stock_kind: str | None, currency_code: str | None,
                    date_from: date | None, date_to: date | None, keyword: str,
                    exclude_return_id: int | None) -> tuple[str, list[Any]]:
        """فواتير البيع المرحّلة ومعاها عدد السطور اللي لسه فيها حاجة ترجع (``remaining_lines``) — بفلاتر."""
        conds, params = ["v.status = 'posted'"], []
        if customer_id:
            conds.append("v.customer_id = %s")
            params.append(customer_id)
        if stock_kind:
            conds.append("v.stock_kind = %s")
            params.append(stock_kind)
        if currency_code:
            conds.append("EXISTS (SELECT 1 FROM sales.sales_invoice_lines l WHERE l.invoice_id = v.id "
                         "AND l.currency_code = %s)")
            params.append(currency_code)
        if date_from is not None:
            conds.append("v.invoice_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("v.invoice_date <= %s")
            params.append(date_to)
        keyword = (keyword or "").strip()
        if keyword:
            like = f"%{keyword}%"
            conds.append("(v.invoice_no ILIKE %s OR v.customer_ref ILIKE %s OR v.e_invoice_no ILIKE %s "
                         "OR c.name_ar ILIKE %s OR c.code ILIKE %s OR EXISTS (SELECT 1 FROM sales.sales_invoice_lines l "
                         "JOIN inv.items i ON i.id = l.item_id WHERE l.invoice_id = v.id "
                         "AND (i.code ILIKE %s OR i.name ILIKE %s)))")
            params += [like] * 7
        sql = f"""
            WITH inv AS (
                SELECT v.id, v.invoice_no, v.invoice_date, v.customer_id, v.customer_ref, v.stock_kind,
                       v.currency_mode, v.currency_code, v.fx_rate, v.total_amount, v.total_base, v.project_id,
                       c.code AS customer_code, c.name_ar AS customer_name,
                       (SELECT count(*) FROM sales.sales_invoice_lines sl WHERE sl.invoice_id = v.id) AS line_count,
                       (SELECT count(*) FROM sales.sales_invoice_lines sl WHERE sl.invoice_id = v.id
                           AND sl.base_qty > {_RETURNED}) AS remaining_lines
                FROM sales.sales_invoices v JOIN party.customers c ON c.id = v.customer_id
                WHERE {" AND ".join(conds)})
            SELECT * FROM inv WHERE remaining_lines > 0"""
        return sql, [exclude_return_id, *params]

    def returnable_invoices(self, customer_id: int | None = None, stock_kind: str | None = None,
                            currency_code: str | None = None, date_from: date | None = None,
                            date_to: date | None = None, keyword: str = "", exclude_return_id: int | None = None,
                            limit: int | None = None, offset: int = 0, ex: Executor | None = None) -> list[dict]:
        """فواتير البيع المرحّلة اللي لسه فيها حاجة ترجع (الأحدث فوق) — للاختيار في المرتجع."""
        sql, params = self._returnable(customer_id, stock_kind, currency_code, date_from, date_to, keyword,
                                       exclude_return_id)
        sql += " ORDER BY invoice_date DESC, id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_returnable_invoices(self, customer_id: int | None = None, stock_kind: str | None = None,
                                  currency_code: str | None = None, date_from: date | None = None,
                                  date_to: date | None = None, keyword: str = "", exclude_return_id: int | None = None,
                                  ex: Executor | None = None) -> int:
        sql, params = self._returnable(customer_id, stock_kind, currency_code, date_from, date_to, keyword,
                                       exclude_return_id)
        return int(self._x(ex).fetch_value(f"SELECT count(*) FROM ({sql}) q", params))

    def invoice_lines_for(self, *, invoice_id: int | None = None, line_ids: list[int] | None = None,
                          exclude_return_id: int | None = None, ex: Executor | None = None) -> list[dict]:
        """سطور فواتير البيع (فاتورة معيّنة أو سطور معيّنة) + الصنف والوحدة + اللي رجع قبل كده بالوحدة الأساسية +
        تكلفة الوحدة الأساسية اللي طلعت بيها (``source_cost`` — الخدمي / المسودة = NULL)."""
        if invoice_id is None and not line_ids:
            return []
        where, params = (("sl.invoice_id = %s", [invoice_id]) if invoice_id is not None
                         else ("sl.id = ANY(%s)", [sorted(set(int(i) for i in line_ids or []))]))
        return self._x(ex).fetch_all(
            "SELECT sl.id, sl.invoice_id, sl.line_no, sl.item_id, sl.warehouse_id, sl.unit_id, sl.factor, sl.qty, "
            "sl.base_qty, sl.unit_price, sl.discount_pct, sl.tax_code_id, sl.tax_rate, sl.currency_code, sl.fx_rate, "
            "sl.net_amount, sl.net_base, v.invoice_no, v.invoice_date, v.status AS invoice_status, "
            "v.customer_id, v.stock_kind, v.project_id, i.code AS item_code, i.name AS item_name, i.is_service, "
            "i.category_id, i.unit_id AS base_unit_id, bu.name_ar AS base_unit_name, u.name_ar AS unit_name, "
            f"w.name_ar AS warehouse_name, {_RETURNED} AS returned_qty, {_SOURCE_COST} AS source_cost "
            "FROM sales.sales_invoice_lines sl JOIN sales.sales_invoices v ON v.id = sl.invoice_id "
            "JOIN inv.items i ON i.id = sl.item_id JOIN inv.units u ON u.id = sl.unit_id "
            "JOIN inv.units bu ON bu.id = i.unit_id LEFT JOIN inv.warehouses w ON w.id = sl.warehouse_id "
            f"WHERE {where} ORDER BY sl.invoice_id, sl.line_no", [exclude_return_id, *params])

    def lock_invoices(self, line_ids: list[int], ex: Executor) -> None:
        """قفل فواتير البيع الأصلية بالترتيب — جهازين بيرجّعوا من نفس الفاتورة بيتصفّوا ورا بعض."""
        if line_ids:
            ex.fetch_all("SELECT id FROM sales.sales_invoices WHERE id IN (SELECT invoice_id FROM "
                         "sales.sales_invoice_lines WHERE id = ANY(%s)) ORDER BY id FOR UPDATE",
                         [sorted(set(line_ids))])

    def last_prices(self, customer_id: int, item_ids: list[int], ex: Executor | None = None) -> dict[int, Decimal]:
        """آخر سعر بيع للعميل ده لكل صنف (الوحدة الأساسية بالجنيه بعد الخصومات) — للسطر الحر."""
        if not item_ids:
            return {}
        rows = self._x(ex).fetch_all(
            "SELECT DISTINCT ON (l.item_id) l.item_id, l.net_base / nullif(l.base_qty, 0) AS unit_base "
            "FROM sales.sales_invoice_lines l JOIN sales.sales_invoices v ON v.id = l.invoice_id "
            "AND v.status = 'posted' WHERE v.customer_id = %s AND l.item_id = ANY(%s) "
            "ORDER BY l.item_id, v.invoice_date DESC, v.id DESC, l.line_no DESC",
            [customer_id, sorted(set(item_ids))])
        return {int(r["item_id"]): Decimal(r["unit_base"]) for r in rows if r["unit_base"] is not None}

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, keyword: str,
               customer_id: int | None = None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append("v.return_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("v.return_date <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        if customer_id:
            conds.append("v.customer_id = %s")
            params.append(customer_id)
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.return_no = %s")
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append("(v.return_no ILIKE %s OR v.customer_ref ILIKE %s OR v.description ILIKE %s "
                         "OR c.name_ar ILIKE %s OR c.code ILIKE %s OR v.draft_no::text = %s "
                         f"OR EXISTS (SELECT 1 FROM {LINES} l JOIN inv.items i ON i.id = l.item_id "
                         "LEFT JOIN sales.sales_invoice_lines sl ON sl.id = l.source_line_id "
                         "LEFT JOIN sales.sales_invoices sv ON sv.id = sl.invoice_id "
                         "WHERE l.return_id = v.id AND (i.code ILIKE %s OR i.name ILIKE %s OR sv.invoice_no ILIKE %s)))")
            params += [like, like, like, like, like, keyword, like, like, like]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_returns(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                     customer_id: int | None = None, limit: int | None = None, offset: int = 0,
                     ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, keyword, customer_id)
        sql = _LIST + where + " ORDER BY v.return_date DESC, v.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_returns(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                      customer_id: int | None = None, ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, keyword, customer_id)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted, coalesce(sum(v.total_base), 0) AS total_base "
            f"FROM {TABLE} v JOIN party.customers c ON c.id = v.customer_id" + where, params)

    # ---- المرتجع ------------------------------------------------------------------
    def get_return(self, return_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [return_id])

    def lines(self, return_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT l.*, i.code AS item_code, i.name AS item_name, i.category_id, i.is_service, "
            "i.unit_id AS base_unit_id, bu.name_ar AS base_unit_name, u.name_ar AS unit_name, "
            "w.code AS warehouse_code, w.name_ar AS warehouse_name, tc.code AS tax_code, "
            "sv.invoice_no AS source_invoice_no, sl.invoice_id AS source_invoice_id "
            f"FROM {LINES} l JOIN inv.items i ON i.id = l.item_id JOIN inv.units u ON u.id = l.unit_id "
            "JOIN inv.units bu ON bu.id = i.unit_id LEFT JOIN inv.warehouses w ON w.id = l.warehouse_id "
            "LEFT JOIN acc.tax_codes tc ON tc.id = l.tax_code_id "
            "LEFT JOIN sales.sales_invoice_lines sl ON sl.id = l.source_line_id "
            "LEFT JOIN sales.sales_invoices sv ON sv.id = sl.invoice_id "
            "WHERE l.return_id = %s ORDER BY l.line_no", [return_id])

    def insert_return(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in FIELDS], data["return_date"], data.get("user_id")]))

    def update_return(self, return_id: int, data: dict[str, Any], ex: Executor,
                      expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        return ex.execute(f"UPDATE {TABLE} SET {sets}, row_version = row_version + 1 "
                          "WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in FIELDS], return_id, *extra])

    def replace_lines(self, return_id: int, lines: list[dict[str, Any]], ex: Executor) -> None:
        ex.execute(f"DELETE FROM {LINES} WHERE return_id = %s", [return_id])
        cols = ", ".join(("return_id", "line_no", *LINE_FIELDS))
        marks = ", ".join(["%s"] * (len(LINE_FIELDS) + 2))
        for n, line in enumerate(lines, start=1):
            ex.execute(f"INSERT INTO {LINES} ({cols}) VALUES ({marks})",
                       [return_id, n, *[line[k] for k in LINE_FIELDS]])

    def set_line_costs(self, costs: dict[int, Decimal], ex: Executor) -> None:
        """تكلفة رجوع الوحدة الأساسية لكل سطر (وقت الترحيل — والمرتجع لسه مسودة)."""
        for line_id, cost in costs.items():
            ex.execute(f"UPDATE {LINES} SET unit_cost = %s WHERE id = %s", [cost, line_id])

    def set_status(self, return_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, return_id, *extra])

    def delete_return(self, return_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLE} WHERE id = %s" + clause, [return_id, *extra])

    def movements(self, return_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT line_no, item_id, warehouse_id, stock_kind, qty, unit_cost, value, booked_value "
            "FROM inv.stock_movements WHERE source_type = 'sales_return' AND source_id = %s ORDER BY line_no",
            [return_id])
