"""SQL محرك الترحيل المركزي (``acc.posting_rules`` + ``acc.posting_doc_types`` — سكريبت 0032) — المرحلة 4.

* قراية القواعد ومصادر الحسابات (الافتراضي · نوع الصنف · مجموعة الطرف · كود الضريبة · الخزنة · البنك · المصروف).
* القيد الأوتوماتيك بمصدره (``source_type, source_id``) — قفله ``FOR UPDATE`` · إضافته · تعديل رأسه · سطوره.
كل دالة بتاخد ``ex`` (``Tx`` من ``db.atomic()``) — المحرك دايماً جوه transaction المستند.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db

_PARTY_TABLES = {"customer": ("party.customers", "party.customer_groups"),
                 "supplier": ("party.suppliers", "party.supplier_groups"),
                 "employee": ("party.employees", None),
                 "partner": ("party.partners", None)}
_MASTER_TABLES = {"treasury": "cash.treasuries", "bank": "cash.bank_accounts", "expense": "exp.expense_types"}


class PostingRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- القواعد ---------------------------------------------------------------
    def doc_type(self, code: str, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT code, name_ar FROM acc.posting_doc_types WHERE code = %s", [code])

    def rules(self, doc_type: str, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT r.doc_type, r.role, r.name_ar, r.side, r.account_source, r.default_key, r.category_kind, "
            "r.party_kind, r.sort_order FROM acc.posting_rules r WHERE r.doc_type = %s ORDER BY r.sort_order",
            [doc_type])

    # ---- مصادر الحسابات ---------------------------------------------------------
    def default_key(self, key: str, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT key, name_ar, account_id FROM acc.default_accounts WHERE key = %s", [key])

    def category_account(self, category_id: int | None, kind: str, ex: Executor | None = None) -> int | None:
        """النوع ← أقرب أب عنده الحساب ← الافتراضي بنفس المفتاح (inv.item_category_account — 0028)."""
        return self._x(ex).fetch_value("SELECT inv.item_category_account(%s, %s)", [category_id, kind])

    def category_name(self, category_id: int, ex: Executor | None = None) -> str | None:
        return self._x(ex).fetch_value("SELECT name FROM inv.item_categories WHERE id = %s", [category_id])

    def party(self, kind: str, party_id: int, ex: Executor | None = None) -> dict | None:
        """الطرف + حساب الرقابة بتاع مجموعته (العملاء / الموردين بس — الموظف والشريك من المفتاح الافتراضي).

        ``own_account_id`` = حساب الطرف لوحده (الشريك بس — حسابه تحت 32، سكريبت 0042)."""
        table, groups = _PARTY_TABLES[kind]
        if groups:
            return self._x(ex).fetch_one(
                f"SELECT p.id, p.code, p.name_ar, g.control_account_id AS account_id, NULL::integer AS own_account_id "
                f"FROM {table} p JOIN {groups} g ON g.id = p.group_id WHERE p.id = %s", [party_id])
        own = "p.account_id" if kind == "partner" else "NULL::integer"
        return self._x(ex).fetch_one(
            f"SELECT p.id, p.code, p.name_ar, NULL::integer AS account_id, {own} AS own_account_id "
            f"FROM {table} p WHERE p.id = %s", [party_id])

    def tax_account(self, tax_code_id: int, direction: str, ex: Executor | None = None) -> dict | None:
        column = "sales_account_id" if direction == "sales" else "purchase_account_id"
        return self._x(ex).fetch_one(
            f"SELECT id, code, {column} AS account_id FROM acc.tax_codes WHERE id = %s", [tax_code_id])

    def master_account(self, source: str, master_id: int, ex: Executor | None = None) -> dict | None:
        """الخزنة / الحساب البنكي / نوع المصروف ← حسابه."""
        return self._x(ex).fetch_one(
            f"SELECT m.id, m.code, a.name_ar, m.account_id FROM {_MASTER_TABLES[source]} m "
            "JOIN acc.accounts a ON a.id = m.account_id WHERE m.id = %s", [master_id])

    def accounts(self, ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all(
            "SELECT id, code, name_ar, account_type, is_leaf, is_active, party_kind, requires_cost_center "
            "FROM acc.accounts "
            "WHERE id = ANY(%s)", [list(ids)])
        return {int(r["id"]): r for r in rows}

    # ---- القيد الأوتوماتيك -------------------------------------------------------
    def entry_for_source(self, source_type: str, source_id: int, ex: Executor) -> dict | None:
        return ex.fetch_one(
            "SELECT e.id, e.entry_no, e.draft_no, e.entry_date, e.fiscal_year_id, e.description, e.reference, "
            "e.status, e.source_type, e.source_id, e.source_no, e.total_debit, e.total_credit, e.lines_count, "
            "e.posted_at, e.posted_by, e.row_version, y.year_code, y.status AS year_status "
            "FROM acc.journal_entries e JOIN core.fiscal_years y ON y.id = e.fiscal_year_id "
            "WHERE e.source_type = %s AND e.source_id = %s FOR UPDATE OF e", [source_type, source_id])

    def entry_id_for_source(self, source_type: str, source_id: int, ex: Executor | None = None) -> int | None:
        return self._x(ex).fetch_value(
            "SELECT id FROM acc.journal_entries WHERE source_type = %s AND source_id = %s", [source_type, source_id])

    def insert_entry(self, data: dict[str, Any], ex: Executor) -> int:
        return int(ex.fetch_value(
            "INSERT INTO acc.journal_entries (entry_date, fiscal_year_id, description, reference, source_type, "
            "source_id, source_no, created_by) "
            "VALUES (%s, (SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), "
            "%s, %s, %s, %s, %s, %s) RETURNING id",
            [data["entry_date"], data["entry_date"], data["description"], data["reference"], data["source_type"],
             data["source_id"], data["source_no"], data.get("user_id")]))

    def update_header(self, entry_id: int, data: dict[str, Any], ex: Executor) -> int:
        return ex.execute(
            "UPDATE acc.journal_entries SET entry_date = %s, description = %s, reference = %s, source_no = %s "
            "WHERE id = %s AND status = 'draft'",
            [data["entry_date"], data["description"], data["reference"], data["source_no"], entry_id])

    def entry_date_year(self, d: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, year_code, status FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date", [d])
