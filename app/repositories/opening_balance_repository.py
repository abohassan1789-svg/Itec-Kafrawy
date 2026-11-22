"""SQL الأرصدة الافتتاحية (``acc.opening_balances`` + ``acc.opening_balance_lines`` — سكريبت 0033) — المرحلة 4.

* **القسم** بتاع كل سطر بيتحسب هنا (الخزائن · البنوك · العملاء · الموردين · عهد الموظفين · الشركاء · حسابات تانية):
  - الخزنة / الحساب البنكي ← سطر من غير طرف على حسابه.
  - العميل / المورد ← سطر بالطرف على حساب رقابة أي مجموعة (عملاء / موردين).
  - الموظف ← سطر بالطرف على حساب «عهد الموظفين» الافتراضي · الشريك ← على «رأس المال» الافتراضي.
  - غير كده ← «حسابات تانية».
* قوائم الأقسام = **كل** الخزائن / البنوك / العملاء … ومع كل واحد سطره (لو ليه رصيد).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db

PARTY_COLUMNS = {"customer": "customer_id", "supplier": "supplier_id", "employee": "employee_id",
                 "partner": "partner_id"}

# القسم بتاع السطر (l = السطر)
_SECTION = """
    CASE
      WHEN num_nonnulls(l.customer_id, l.supplier_id, l.employee_id, l.partner_id) = 0
           AND l.account_id IN (SELECT account_id FROM cash.treasuries) THEN 'trs'
      WHEN num_nonnulls(l.customer_id, l.supplier_id, l.employee_id, l.partner_id) = 0
           AND l.account_id IN (SELECT account_id FROM cash.bank_accounts) THEN 'bnk'
      WHEN l.customer_id IS NOT NULL
           AND l.account_id IN (SELECT control_account_id FROM party.customer_groups) THEN 'cus'
      WHEN l.supplier_id IS NOT NULL
           AND l.account_id IN (SELECT control_account_id FROM party.supplier_groups) THEN 'sup'
      WHEN l.employee_id IS NOT NULL
           AND l.account_id = (SELECT account_id FROM acc.default_accounts WHERE key = 'custody') THEN 'emp'
      WHEN l.partner_id IS NOT NULL
           AND l.account_id = (SELECT account_id FROM acc.default_accounts WHERE key = 'capital') THEN 'prt'
      ELSE 'oth'
    END"""

_LINES = f"""
    SELECT l.id, l.account_id, l.customer_id, l.supplier_id, l.employee_id, l.partner_id, l.debit, l.credit,
           l.cost_center_id, l.description, {_SECTION} AS section,
           a.code AS account_code, a.name_ar AS account_name, a.account_type, a.party_kind,
           a.requires_cost_center, a.is_active AS account_active,
           cc.code AS cost_center_code, cc.name_ar AS cost_center_name,
           coalesce(c.code, s.code, em.code, pr.code) AS party_code,
           coalesce(c.name_ar, s.name_ar, em.name_ar, pr.name_ar) AS party_name
    FROM acc.opening_balance_lines l
    JOIN acc.accounts a ON a.id = l.account_id
    LEFT JOIN acc.cost_centers cc ON cc.id = l.cost_center_id
    LEFT JOIN party.customers c ON c.id = l.customer_id
    LEFT JOIN party.suppliers s ON s.id = l.supplier_id
    LEFT JOIN party.employees em ON em.id = l.employee_id
    LEFT JOIN party.partners pr ON pr.id = l.partner_id
"""

_CUSTOMER_ORDER = "CASE WHEN p.code ~ '^[0-9]+$' THEN lpad(p.code, 10, '0') ELSE p.code END"

# صفوف الأقسام: كل طرف / خزنة / بنك + سطره (لو موجود)
_SECTION_ROWS = {
    "trs": f"""
        SELECT t.id AS ref_id, t.code, t.name_ar AS name, t.is_active, t.account_id,
               a.code AS account_code, a.name_ar AS account_name, NULL::text AS party_kind, NULL::integer AS party_id,
               l.id AS line_id, coalesce(l.debit, 0) AS debit, coalesce(l.credit, 0) AS credit
        FROM cash.treasuries t JOIN acc.accounts a ON a.id = t.account_id
        LEFT JOIN acc.opening_balance_lines l ON l.account_id = t.account_id
             AND num_nonnulls(l.customer_id, l.supplier_id, l.employee_id, l.partner_id) = 0
        ORDER BY t.seq""",
    "bnk": f"""
        SELECT b.id AS ref_id, b.code, a.name_ar AS name, b.is_active, b.account_id,
               a.code AS account_code, a.name_ar AS account_name, NULL::text AS party_kind, NULL::integer AS party_id,
               l.id AS line_id, coalesce(l.debit, 0) AS debit, coalesce(l.credit, 0) AS credit
        FROM cash.bank_accounts b JOIN acc.accounts a ON a.id = b.account_id
        LEFT JOIN acc.opening_balance_lines l ON l.account_id = b.account_id
             AND num_nonnulls(l.customer_id, l.supplier_id, l.employee_id, l.partner_id) = 0
        ORDER BY b.code""",
    "cus": f"""
        SELECT p.id AS ref_id, p.code, p.name_ar AS name, p.is_active, g.control_account_id AS account_id,
               a.code AS account_code, a.name_ar AS account_name, 'customer' AS party_kind, p.id AS party_id,
               l.id AS line_id, coalesce(l.debit, 0) AS debit, coalesce(l.credit, 0) AS credit
        FROM party.customers p JOIN party.customer_groups g ON g.id = p.group_id
        JOIN acc.accounts a ON a.id = g.control_account_id
        LEFT JOIN acc.opening_balance_lines l ON l.customer_id = p.id
             AND l.account_id IN (SELECT control_account_id FROM party.customer_groups)
        ORDER BY {_CUSTOMER_ORDER}""",
    "sup": """
        SELECT p.id AS ref_id, p.code, p.name_ar AS name, p.is_active, g.control_account_id AS account_id,
               a.code AS account_code, a.name_ar AS account_name, 'supplier' AS party_kind, p.id AS party_id,
               l.id AS line_id, coalesce(l.debit, 0) AS debit, coalesce(l.credit, 0) AS credit
        FROM party.suppliers p JOIN party.supplier_groups g ON g.id = p.group_id
        JOIN acc.accounts a ON a.id = g.control_account_id
        LEFT JOIN acc.opening_balance_lines l ON l.supplier_id = p.id
             AND l.account_id IN (SELECT control_account_id FROM party.supplier_groups)
        ORDER BY p.seq""",
    "emp": """
        SELECT p.id AS ref_id, p.code, p.name_ar AS name, p.is_active, d.account_id,
               a.code AS account_code, a.name_ar AS account_name, 'employee' AS party_kind, p.id AS party_id,
               l.id AS line_id, coalesce(l.debit, 0) AS debit, coalesce(l.credit, 0) AS credit
        FROM party.employees p
        CROSS JOIN (SELECT account_id FROM acc.default_accounts WHERE key = 'custody') d
        LEFT JOIN acc.accounts a ON a.id = d.account_id
        LEFT JOIN acc.opening_balance_lines l ON l.employee_id = p.id AND l.account_id = d.account_id
        ORDER BY p.seq""",
    "prt": """
        SELECT p.id AS ref_id, p.code, p.name_ar AS name, p.is_active, d.account_id,
               a.code AS account_code, a.name_ar AS account_name, 'partner' AS party_kind, p.id AS party_id,
               l.id AS line_id, coalesce(l.debit, 0) AS debit, coalesce(l.credit, 0) AS credit
        FROM party.partners p
        CROSS JOIN (SELECT account_id FROM acc.default_accounts WHERE key = 'capital') d
        LEFT JOIN acc.accounts a ON a.id = d.account_id
        LEFT JOIN acc.opening_balance_lines l ON l.partner_id = p.id AND l.account_id = d.account_id
        ORDER BY p.seq""",
}

_HEADER = """
    SELECT h.id, h.balance_date, h.status, h.notes, h.updated_at, h.posted_at, h.row_version,
           uu.username AS updated_by_name, pu.username AS posted_by_name,
           y.year_code, y.status AS year_status
    FROM acc.opening_balances h
    LEFT JOIN sec.app_users uu ON uu.id = h.updated_by
    LEFT JOIN sec.app_users pu ON pu.id = h.posted_by
    LEFT JOIN core.fiscal_years y ON h.balance_date BETWEEN y.start_date AND y.end_date
    WHERE h.id = 1
"""


class OpeningBalanceRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- الرأس ------------------------------------------------------------------
    def header(self, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_HEADER + (" FOR UPDATE OF h" if for_update else ""))

    def update_header(self, data: dict[str, Any], user_id: int | None, ex: Executor) -> int:
        return ex.execute("UPDATE acc.opening_balances SET balance_date = %s, notes = %s, updated_by = %s WHERE id = 1",
                          [data["balance_date"], data["notes"], user_id])

    def touch(self, user_id: int | None, ex: Executor) -> None:
        """أي تغيير في السطور بيزوّد إصدار الرأس (عشان الشاشات التانية تعرف)."""
        ex.execute("UPDATE acc.opening_balances SET updated_by = %s, row_version = row_version + 1 WHERE id = 1",
                   [user_id])

    def set_status(self, status: str, user_id: int | None, ex: Executor) -> int:
        return ex.execute(
            "UPDATE acc.opening_balances SET status = %s, posted_at = CASE WHEN %s = 'posted' THEN now() END, "
            "posted_by = %s, updated_by = %s WHERE id = 1", [status, status, user_id if status == "posted" else None,
                                                              user_id])

    def year_for(self, d, ex: Executor | None = None) -> dict | None:  # noqa: ANN001 - date
        return self._x(ex).fetch_one(
            "SELECT id, year_code, status FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date", [d])

    # ---- السطور -----------------------------------------------------------------
    def lines(self, ex: Executor | None = None, section: str | None = None) -> list[dict]:
        sql = f"SELECT * FROM ({_LINES}) x"
        params: list[Any] = []
        if section:
            sql += " WHERE x.section = %s"
            params.append(section)
        return self._x(ex).fetch_all(sql + " ORDER BY x.account_code, x.party_code NULLS FIRST, x.id", params)

    def line(self, line_id: int, ex: Executor, for_update: bool = False) -> dict | None:
        return ex.fetch_one(
            "SELECT * FROM acc.opening_balance_lines WHERE id = %s" + (" FOR UPDATE" if for_update else ""), [line_id])

    def find_line(self, account_id: int, party: tuple[str, int] | None, ex: Executor,
                  for_update: bool = False) -> dict | None:
        conds = ["account_id = %s"]
        params: list[Any] = [account_id]
        for kind, column in PARTY_COLUMNS.items():
            if party and party[0] == kind:
                conds.append(f"{column} = %s")
                params.append(party[1])
            else:
                conds.append(f"{column} IS NULL")
        return ex.fetch_one("SELECT * FROM acc.opening_balance_lines WHERE " + " AND ".join(conds)
                            + (" FOR UPDATE" if for_update else ""), params)

    def party_line(self, kind: str, party_id: int, ex: Executor | None = None,
                   for_update: bool = False) -> dict | None:
        """سطر العميل / المورد على حساب رقابة أي مجموعة (السطر اللي خانة «رصيد أول المدة» بتعدّله)."""
        column = PARTY_COLUMNS[kind]
        groups = "party.customer_groups" if kind == "customer" else "party.supplier_groups"
        return self._x(ex).fetch_one(
            f"SELECT * FROM acc.opening_balance_lines WHERE {column} = %s "
            f"AND account_id IN (SELECT control_account_id FROM {groups})" + (" FOR UPDATE" if for_update else ""),
            [party_id])

    def party_control_account(self, kind: str, party_id: int, ex: Executor | None = None) -> int | None:
        table, groups = (("party.customers", "party.customer_groups") if kind == "customer"
                         else ("party.suppliers", "party.supplier_groups"))
        return self._x(ex).fetch_value(
            f"SELECT g.control_account_id FROM {table} p JOIN {groups} g ON g.id = p.group_id WHERE p.id = %s",
            [party_id])

    def insert_line(self, data: dict[str, Any], user_id: int | None, ex: Executor) -> int:
        return int(ex.fetch_value(
            "INSERT INTO acc.opening_balance_lines (account_id, customer_id, supplier_id, employee_id, partner_id, "
            "debit, credit, cost_center_id, description, updated_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            [data["account_id"], *[data.get(c) for c in PARTY_COLUMNS.values()], data["debit"], data["credit"],
             data.get("cost_center_id"), data.get("description"), user_id]))

    def update_line(self, line_id: int, data: dict[str, Any], user_id: int | None, ex: Executor) -> int:
        return ex.execute(
            "UPDATE acc.opening_balance_lines SET account_id = %s, customer_id = %s, supplier_id = %s, "
            "employee_id = %s, partner_id = %s, debit = %s, credit = %s, cost_center_id = %s, description = %s, "
            "updated_by = %s WHERE id = %s",
            [data["account_id"], *[data.get(c) for c in PARTY_COLUMNS.values()], data["debit"], data["credit"],
             data.get("cost_center_id"), data.get("description"), user_id, line_id])

    def delete_line(self, line_id: int, ex: Executor) -> int:
        return ex.execute("DELETE FROM acc.opening_balance_lines WHERE id = %s", [line_id])

    # ---- الأقسام -----------------------------------------------------------------
    def section_rows(self, section: str, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(_SECTION_ROWS[section])

    def summary(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            f"SELECT x.section, count(*) AS lines, coalesce(sum(x.debit), 0) AS debit, "
            f"coalesce(sum(x.credit), 0) AS credit FROM ({_LINES}) x GROUP BY x.section")

    def account_choices(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات الفرعية (والموقوفة عشان السطر القديم يتعرض) بنوعها — الشاشة بتفلتر الميزانية / الكل.
        ``owner_partner_id`` = حساب شريك تحت 32 (0042) ← الشريك بيتملى لوحده."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.account_type, a.party_kind, a.requires_cost_center, a.is_active, "
            "(SELECT pr.id FROM party.partners pr WHERE pr.account_id = a.id) AS owner_partner_id FROM acc.accounts a WHERE a.is_leaf ORDER BY a.code")

    def opening_account(self, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT a.id, a.code, a.name_ar FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
            "WHERE d.key = 'opening'")
