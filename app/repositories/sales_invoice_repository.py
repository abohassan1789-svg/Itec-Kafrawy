"""SQL «فاتورة البيع / إذن الصرف» (``sales.sales_invoices`` + ``sales.sales_invoice_lines`` — سكريبت 0051).

* القائمة **بفلتر فترة وصفحات** (index على ``invoice_date``) · رقم الفاتورة الكامل من الـ unique index.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم INV جوه قفل العدّاد وبتتأكد من السنة والعميل والخزنة ومن
  حركات دفتر المخزون والقيد (آخر الـ transaction).
* ``lock_customer`` = قفل صف العميل ``FOR UPDATE`` وقت الترحيل: جهازين بيرحّلوا فواتير لنفس العميل بيتصفّوا ورا بعض
  عشان حد الائتمان يتحسب صح.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLE = "sales.sales_invoices"
LINES = "sales.sales_invoice_lines"
_FULL_NO = re.compile(r"^INV-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
FIELDS = ("invoice_date", "customer_id", "customer_ref", "e_invoice_no", "stock_kind", "prices_include_vat",
          "pay_kind", "due_date", "source_kind", "treasury_id", "bank_account_id", "warehouse_id", "project_id",
          "employee_id", "currency_mode", "currency_code", "fx_rate", "discount_amount", "gross_amount",
          "line_discount", "net_amount", "vat_amount", "total_amount", "net_base", "vat_base", "total_base",
          "discount_base", "description")
LINE_FIELDS = ("item_id", "warehouse_id", "unit_id", "factor", "qty", "unit_price", "discount_pct", "tax_code_id",
               "tax_rate", "currency_code", "fx_rate", "gross_amount", "line_discount", "header_discount",
               "net_amount", "vat_amount", "total_amount", "net_base", "vat_base", "total_base", "discount_base")

_SELECT = f"""
    SELECT v.*, y.year_code, y.status AS year_status,
           c.code AS customer_code, c.name_ar AS customer_name, c.tax_reg_no AS customer_tax_no,
           c.payment_terms_days AS customer_terms, c.credit_limit AS customer_credit_limit,
           c.is_system AS customer_is_cash,
           w.name_ar AS warehouse_name, t.name_ar AS treasury_name,
           ba.code AS bank_code, b.name_ar AS bank_name, p.code AS project_code, p.name AS project_name,
           p.cost_center_id AS project_cost_center_id,
           em.code AS employee_code, em.name_ar AS employee_name,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {TABLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    JOIN party.customers c ON c.id = v.customer_id
    LEFT JOIN inv.warehouses w ON w.id = v.warehouse_id
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
    LEFT JOIN acc.projects p ON p.id = v.project_id
    LEFT JOIN party.employees em ON em.id = v.employee_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = f"""
    SELECT v.id, v.invoice_no, v.draft_no, v.invoice_date, v.customer_id, v.customer_ref, v.e_invoice_no,
           v.stock_kind, v.pay_kind, v.currency_mode, v.currency_code, v.total_amount, v.total_base, v.status,
           v.description, c.code AS customer_code, c.name_ar AS customer_name,
           (SELECT count(*) FROM {LINES} l WHERE l.invoice_id = v.id) AS line_count
    FROM {TABLE} v
    JOIN party.customers c ON c.id = v.customer_id
"""


class SalesInvoiceRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- الاختيارات (الموقوف بيرجع عشان الفاتورة القديمة تتعرض) ------------------------------
    def customer_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT c.id, c.code, c.name_ar, c.tax_reg_no, c.payment_terms_days, c.currency_code, c.is_active, "
            "c.is_system, c.credit_limit, c.vat_tax_code_id, tc.rate AS vat_rate FROM party.customers c "
            "LEFT JOIN acc.tax_codes tc ON tc.id = c.vat_tax_code_id ORDER BY c.code::bigint")

    def warehouse_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_virtual, is_active, is_default FROM inv.warehouses ORDER BY seq")

    def item_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_active, i.is_service, i.unit_id, u.name_ar AS unit_name, i.category_id, "
            "i.tax_code_id, i.default_warehouse_id, i.sale_price FROM inv.items i "
            "JOIN inv.units u ON u.id = i.unit_id ORDER BY i.seq")

    def item_units(self, item_ids: list[int] | None = None, ex: Executor | None = None) -> list[dict]:
        sql = ("SELECT iu.item_id, iu.unit_id, iu.factor, u.name_ar AS unit_name "
               "FROM inv.item_units iu JOIN inv.units u ON u.id = iu.unit_id")
        if item_ids is None:
            return self._x(ex).fetch_all(sql + " ORDER BY iu.item_id, iu.factor")
        return self._x(ex).fetch_all(sql + " WHERE iu.item_id = ANY(%s) ORDER BY iu.item_id, iu.factor",
                                     [sorted(set(item_ids))])

    def items(self, item_ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.is_service, i.is_active, i.unit_id, u.name_ar AS unit_name, i.category_id, "
            "i.tax_code_id, i.sale_price FROM inv.items i JOIN inv.units u ON u.id = i.unit_id WHERE i.id = ANY(%s)",
            [sorted(set(item_ids))])
        return {int(r["id"]): r for r in rows}

    def tax_codes(self, ex: Executor | None = None) -> list[dict]:
        """أكواد ضريبة الإضافة اللي تنفع للمبيعات (14% · صفري تصدير · معفي …)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, rate, is_exempt, is_active FROM acc.tax_codes "
            "WHERE kind = 'add' AND applies_to IN ('both', 'sales') ORDER BY calc_order, rate DESC, code")

    def currencies(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, decimals, is_base, is_active FROM core.currencies ORDER BY NOT is_base, code")

    def rate_on(self, code: str, day: date, ex: Executor | None = None) -> Decimal | None:
        value = self._x(ex).fetch_value("SELECT core.rate_on(%s, %s)", [code, day])
        return None if value is None else Decimal(value)

    def treasury_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT t.id, t.code, t.name_ar, t.currency_code, t.is_active, "
            "t.currency_code = (SELECT code FROM core.currencies WHERE is_base) AS is_base_currency "
            "FROM cash.treasuries t ORDER BY t.seq")

    def bank_account_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT ba.id, ba.code, b.name_ar AS bank_name, ba.account_number, ba.currency_code, "
            "ba.is_active AND b.is_active AS is_active, "
            "ba.currency_code = (SELECT code FROM core.currencies WHERE is_base) AS is_base_currency "
            "FROM cash.bank_accounts ba JOIN cash.banks b ON b.id = ba.bank_id ORDER BY b.seq, ba.seq")

    def project_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name, status, customer_id, amount, currency_code, cost_center_id FROM acc.projects "
            "ORDER BY order_date DESC NULLS LAST, id DESC")

    def employee_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all("SELECT id, code, name_ar, is_active FROM party.employees ORDER BY seq")

    def project(self, project_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name, status, customer_id, amount, currency_code, cost_center_id FROM acc.projects "
            "WHERE id = %s", [project_id])

    def project_invoiced(self, project_id: int, exclude_id: int | None = None, ex: Executor | None = None) -> Decimal:
        """اللي اتفوتر على الأمر قبل كده (الفواتير المرحّلة — قبل الضريبة بالجنيه) — لتنبيه تجاوز قيمة الأمر."""
        return Decimal(self._x(ex).fetch_value(
            f"SELECT coalesce(sum(net_base), 0) FROM {TABLE} WHERE project_id = %s AND status = 'posted' "
            "AND id IS DISTINCT FROM %s", [project_id, exclude_id]))

    def warehouses(self, ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_virtual, is_active FROM inv.warehouses WHERE id = ANY(%s)",
            [sorted({int(i) for i in ids if i})])
        return {int(r["id"]): r for r in rows}

    def customer(self, customer_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT c.id, c.code, c.name_ar, c.is_active, c.is_system, c.payment_terms_days, c.currency_code, "
            "c.tax_reg_no, c.credit_limit, c.vat_tax_code_id FROM party.customers c WHERE c.id = %s"
            + (" FOR UPDATE" if for_update else ""), [customer_id])

    def year_of(self, day: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT year_code, status FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date", [day])

    def base_currency(self, ex: Executor | None = None) -> str:
        return str(self._x(ex).fetch_value("SELECT code FROM core.currencies WHERE is_base"))

    def own_sales_account(self, category_id: int | None, ex: Executor | None = None) -> bool:
        """نوع الصنف (أو أقرب أب) ليه حساب مبيعات بتاعه (مش الافتراضي) — الخدمي من غيره بيروح «إيراد الخدمات»."""
        if not category_id:
            return False
        return bool(self._x(ex).fetch_value(
            "SELECT inv.item_category_account(%s, 'sales') IS DISTINCT FROM "
            "(SELECT account_id FROM acc.default_accounts WHERE key = 'sales')", [category_id]))

    # ---- مساعدات المراجعة -------------------------------------------------------
    def last_prices(self, customer_id: int, item_ids: list[int], exclude_id: int | None = None,
                    ex: Executor | None = None) -> dict[int, dict]:
        """آخر سعر بيع للعميل ده لكل صنف (الوحدة الأساسية بالجنيه بعد خصم السطر) — لو الصنف مالوش سعر بيع."""
        if not item_ids:
            return {}
        rows = self._x(ex).fetch_all(
            f"SELECT DISTINCT ON (l.item_id) l.item_id, l.net_base / nullif(l.base_qty, 0) AS unit_base "
            f"FROM {LINES} l JOIN {TABLE} v ON v.id = l.invoice_id AND v.status = 'posted' "
            "WHERE v.customer_id = %s AND l.item_id = ANY(%s) AND v.id IS DISTINCT FROM %s "
            "ORDER BY l.item_id, v.invoice_date DESC, v.id DESC, l.line_no DESC",
            [customer_id, sorted(set(item_ids)), exclude_id])
        return {int(r["item_id"]): r for r in rows if r["unit_base"] is not None}

    def customer_balance(self, customer_id: int, ex: Executor | None = None) -> Decimal:
        """رصيد العميل من القيود المرحّلة (مدين − دائن = عليه كام) بالجنيه."""
        return Decimal(self._x(ex).fetch_value(
            "SELECT coalesce(sum(l.debit - l.credit), 0) FROM acc.journal_lines l "
            "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.customer_id = %s",
            [customer_id]))

    def customer_balance_by_currency(self, customer_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT coalesce(l.currency_code, (SELECT code FROM core.currencies WHERE is_base)) AS currency_code, "
            "sum(CASE WHEN l.currency_code IS NULL THEN l.debit - l.credit "
            "         WHEN l.debit > 0 THEN l.amount_fc ELSE -l.amount_fc END) AS amount, "
            "sum(l.debit - l.credit) AS amount_base "
            "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' "
            "WHERE l.customer_id = %s GROUP BY 1 ORDER BY 1", [customer_id])

    def accounts(self, ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all("SELECT id, code, name_ar FROM acc.accounts WHERE id = ANY(%s)",
                                     [sorted(set(ids))])
        return {int(r["id"]): r for r in rows}

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(date_from: date | None, date_to: date | None, status: str, keyword: str,
               customer_id: int | None = None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if date_from is not None:
            conds.append("v.invoice_date >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append("v.invoice_date <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        if customer_id:
            conds.append("v.customer_id = %s")
            params.append(customer_id)
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.invoice_no = %s")
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append("(v.invoice_no ILIKE %s OR v.customer_ref ILIKE %s OR v.e_invoice_no ILIKE %s "
                         "OR v.description ILIKE %s OR c.name_ar ILIKE %s OR c.code = %s OR v.draft_no::text = %s "
                         f"OR EXISTS (SELECT 1 FROM {LINES} l JOIN inv.items i ON i.id = l.item_id "
                         "WHERE l.invoice_id = v.id AND (i.code ILIKE %s OR i.name ILIKE %s)))")
            params += [like, like, like, like, like, keyword, keyword, like, like]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_invoices(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                      customer_id: int | None = None, limit: int | None = None, offset: int = 0,
                      ex: Executor | None = None) -> list[dict]:
        where, params = self._where(date_from, date_to, status, keyword, customer_id)
        sql = _LIST + where + " ORDER BY v.invoice_date DESC, v.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_invoices(self, date_from: date | None, date_to: date | None, status: str = "all", keyword: str = "",
                       customer_id: int | None = None, ex: Executor | None = None) -> dict:
        where, params = self._where(date_from, date_to, status, keyword, customer_id)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted, coalesce(sum(v.total_base), 0) AS total_base "
            f"FROM {TABLE} v JOIN party.customers c ON c.id = v.customer_id" + where, params)

    # ---- الفاتورة ------------------------------------------------------------------
    def get_invoice(self, invoice_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [invoice_id])

    def lines(self, invoice_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT l.*, i.code AS item_code, i.name AS item_name, i.category_id, i.is_service, "
            "i.unit_id AS base_unit_id, bu.name_ar AS base_unit_name, u.name_ar AS unit_name, "
            "w.code AS warehouse_code, w.name_ar AS warehouse_name, tc.code AS tax_code, tc.name_ar AS tax_name "
            f"FROM {LINES} l JOIN inv.items i ON i.id = l.item_id JOIN inv.units u ON u.id = l.unit_id "
            "JOIN inv.units bu ON bu.id = i.unit_id LEFT JOIN inv.warehouses w ON w.id = l.warehouse_id "
            "LEFT JOIN acc.tax_codes tc ON tc.id = l.tax_code_id WHERE l.invoice_id = %s ORDER BY l.line_no",
            [invoice_id])

    def insert_invoice(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in FIELDS], data["invoice_date"], data.get("user_id")]))

    def update_invoice(self, invoice_id: int, data: dict[str, Any], ex: Executor,
                       expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        return ex.execute(f"UPDATE {TABLE} SET {sets}, row_version = row_version + 1 "
                          "WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in FIELDS], invoice_id, *extra])

    def replace_lines(self, invoice_id: int, lines: list[dict[str, Any]], ex: Executor) -> None:
        ex.execute(f"DELETE FROM {LINES} WHERE invoice_id = %s", [invoice_id])
        cols = ", ".join(("invoice_id", "line_no", *LINE_FIELDS))
        marks = ", ".join(["%s"] * (len(LINE_FIELDS) + 2))
        for n, line in enumerate(lines, start=1):
            ex.execute(f"INSERT INTO {LINES} ({cols}) VALUES ({marks})",
                       [invoice_id, n, *[line[k] for k in LINE_FIELDS]])

    def set_status(self, invoice_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, invoice_id, *extra])

    def set_e_invoice_no(self, invoice_id: int, value: str | None, ex: Executor,
                         expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET e_invoice_no = %s WHERE id = %s" + clause,
                          [value, invoice_id, *extra])

    def delete_invoice(self, invoice_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLE} WHERE id = %s" + clause, [invoice_id, *extra])

    # ---- حركات المخزون بتاعة الفاتورة المرحّلة ---------------------------------------------
    def movements(self, invoice_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT line_no, item_id, warehouse_id, stock_kind, qty, unit_cost, value, booked_value "
            "FROM inv.stock_movements WHERE source_type = 'sales_invoice' AND source_id = %s ORDER BY line_no",
            [invoice_id])
