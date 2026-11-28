"""SQL «سند قبض» (العملاء — ``cash.customer_receipts``، سكريبت 0035) و«سند صرف» (الموردين —
``cash.supplier_payments``، سكريبت 0036 + **العملة والتوزيع على فواتير الشراء** — سكريبت 0050).

* الجدولين نفس الشكل بالظبط غير عمود الطرف (``customer_id`` / ``supplier_id``) — كل دالة بتاخد ``kind``
  (``receipt`` / ``payment``) والجدول والطرف من ``TABLES`` / ``PARTIES`` بس (عمرهم ما بييجوا من برّا).
* في الـ Python الطرف اسمه ``party_id`` (والاسم ``party_code`` / ``party_name``) للنوعين.
* القائمة **بفلتر فترة وصفحات** (الـ index على ``voucher_date DESC, id DESC``) · رقم السند الكامل من الـ unique index.
* الترحيل وإلغاؤه = تغيير ``status`` بس، والقاعدة بتصرف رقم RV / PV جوه قفل العدّاد وبتتأكد من السنة والعميل والخزنة.
* أرصدة الطرف والخزنة / البنك **من سطور القيود المرحّلة** (الـ indexes بتوع 0031 / 0034).
* **السندين بعملة** (الصرف 0050 · القبض 0054): ``currency_code`` / ``fx_rate`` / ``amount_base``.
* **التوزيع** (``ALLOC[kind]``): سند الصرف ← ``cash.voucher_allocations`` على فواتير الشراء
  (``purch.purchase_invoice_dues``) · سند القبض ← ``cash.receipt_allocations`` على فواتير البيع
  (``sales.sales_invoice_dues``) — المستحق لكل (فاتورة + عملة) · قفل الفواتير ``FOR UPDATE`` بترتيب ثابت وقت الترحيل.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from dataclasses import dataclass
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLES = {"receipt": "cash.customer_receipts", "payment": "cash.supplier_payments"}
# الطرف: (عموده في السند وفي سطر القيد، جدوله)
PARTIES = {"receipt": ("customer_id", "party.customers"), "payment": ("supplier_id", "party.suppliers")}
_FULL_NO = re.compile(r"^(RV|PV)-\d{4}-\d+$", re.IGNORECASE)
STATUSES = ("all", "draft", "posted")
FIELDS = ("voucher_date", "party_id", "source_kind", "treasury_id", "bank_account_id", "amount", "description",
          "paper_no", "project_id")
CURRENCY_FIELDS = (*FIELDS, "currency_code", "fx_rate")          # السندين بعملة (0050 · 0054)
_CURRENCY_COLS = "v.currency_code, v.fx_rate, v.amount_base"


@dataclass(frozen=True)
class AllocSpec:
    """جدول التوزيع والفواتير بتاعة كل نوع سند (الأسماء ثابتة هنا — عمرها ما بتيجي من برّا)."""
    table: str           # جدول التوزيع
    voucher_col: str     # عمود السند فيه
    invoice_col: str     # عمود الفاتورة فيه (= مفتاح سطر التوزيع في الـ Python)
    invoices: str        # جدول الفواتير
    dues: str            # view المستحق (فاتورة + عملة)
    party_col: str       # عمود الطرف في الفاتورة / الـ view
    ref_col: str         # رقم الفاتورة عند الطرف (رقم فاتورة المورد / أمر شراء العميل)


ALLOC = {
    "payment": AllocSpec("cash.voucher_allocations", "supplier_payment_id", "purchase_invoice_id",
                         "purch.purchase_invoices", "purch.purchase_invoice_dues", "supplier_id", "supplier_invoice_no"),
    "receipt": AllocSpec("cash.receipt_allocations", "customer_receipt_id", "sales_invoice_id",
                         "sales.sales_invoices", "sales.sales_invoice_dues", "customer_id", "customer_ref"),
}

_SELECT = """
    SELECT v.id, v.voucher_no, v.draft_no, v.voucher_date, v.fiscal_year_id, v.{party} AS party_id, v.source_kind,
           v.treasury_id, v.bank_account_id, v.amount, v.description, v.paper_no, v.project_id, v.status,
           {currency},
           v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status,
           c.code AS party_code, c.name_ar AS party_name,
           t.code AS treasury_code, t.name_ar AS treasury_name, t.account_id AS treasury_account_id,
           ba.code AS bank_code, b.name_ar AS bank_name, ba.account_id AS bank_account_account_id,
           p.code AS project_code, p.name AS project_name,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {table} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    JOIN {party_table} c ON c.id = v.{party}
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
    LEFT JOIN acc.projects p ON p.id = v.project_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = """
    SELECT v.id, v.voucher_no, v.draft_no, v.voucher_date, v.amount, v.status, v.description, v.paper_no,
           {currency},
           v.source_kind, c.code AS party_code, c.name_ar AS party_name,
           coalesce(t.name_ar, b.name_ar) AS source_name
    FROM {table} v
    JOIN {party_table} c ON c.id = v.{party}
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
"""


def _sql(template: str, kind: str) -> str:
    party, party_table = PARTIES[kind]
    return template.format(table=TABLES[kind], party=party, party_table=party_table, currency=_CURRENCY_COLS)


def fields(kind: str) -> tuple[str, ...]:
    return CURRENCY_FIELDS


def _cols(kind: str) -> list[str]:
    """أسماء الأعمدة الحقيقية بترتيب ``fields(kind)`` (``party_id`` ← عمود الطرف)."""
    return [PARTIES[kind][0] if k == "party_id" else k for k in fields(kind)]


class VoucherRepository:
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
        sql = _sql(_LIST, kind) + where + " ORDER BY v.voucher_date DESC, v.id DESC"
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
            "coalesce(sum(v.amount_base) "
            "FILTER (WHERE v.status = 'posted'), 0) AS posted_amount "
            + _sql("FROM {table} v JOIN {party_table} c ON c.id = v.{party}", kind) + where, params)

    # ---- السند ------------------------------------------------------------------
    def get_voucher(self, kind: str, voucher_id: int, ex: Executor | None = None,
                    for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _sql(_SELECT, kind) + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""),
            [voucher_id])

    def insert_voucher(self, kind: str, data: dict[str, Any], ex: Executor) -> int:
        names = fields(kind)
        cols = ", ".join((*_cols(kind), "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(names))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLES[kind]} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in names], data["voucher_date"], data.get("user_id")]))

    def update_voucher(self, kind: str, voucher_id: int, data: dict[str, Any], ex: Executor,
                       expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        # سطور التوزيع ممكن تتغيّر والرأس زي ما هو ← الإصدار بيزيد صريح (جهاز تاني بنسخة قديمة يترفض).
        sets = ", ".join(f"{k} = %s" for k in _cols(kind)) + ", row_version = row_version + 1"
        return ex.execute(f"UPDATE {TABLES[kind]} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in fields(kind)], voucher_id, *extra])

    def set_status(self, kind: str, voucher_id: int, status: str, user_id: int | None, ex: Executor,
                   expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLES[kind]} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, voucher_id, *extra])

    def delete_voucher(self, kind: str, voucher_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLES[kind]} WHERE id = %s" + clause, [voucher_id, *extra])

    # ---- الاختيارات -------------------------------------------------------------
    def party_choices(self, kind: str, ex: Executor | None = None) -> list[dict]:
        if kind == "payment":
            return self._x(ex).fetch_all(
                "SELECT id, code, name_ar, is_active, currency_code FROM party.suppliers ORDER BY seq")
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_active, currency_code FROM party.customers "
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
    @staticmethod
    def _amount_expr(side: str, currency: str | None, base: bool) -> tuple[str, str, list[Any]]:
        """المبلغ بجنبه + فلتر العملة: ``None`` = الكل بالجنيه · الأساسية = السطور اللي من غير عملة ·
        عملة تانية = ``amount_fc`` بتاع السطور اللي بالعملة دي (بإشارة السطر)."""
        if currency is None:
            return side, "", []
        if base:
            return side, " AND l.currency_code IS NULL", []
        return f"sign({side}) * l.amount_fc", " AND l.currency_code = %s", [currency]

    def party_balance(self, kind: str, party_id: int, ex: Executor | None = None, *,
                      currency: str | None = None, base: bool = True) -> Decimal:
        """رصيد الطرف على حسابات الرقابة بجنبه الطبيعي: العميل (مدين − دائن) = عليه لينا كام ·
        المورد (دائن − مدين) = ليه علينا كام. ``currency`` = رصيده بالعملة دي بس (للعلم — 0050 / 0054)."""
        side = "l.debit - l.credit" if kind == "receipt" else "l.credit - l.debit"
        expr, cond, params = self._amount_expr(side, currency, base)
        return Decimal(self._x(ex).fetch_value(
            f"SELECT coalesce(sum({expr}), 0) FROM acc.journal_lines l "
            "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' "
            f"WHERE l.{PARTIES[kind][0]} = %s{cond}", [party_id, *params]))

    def account_balance(self, account_id: int, ex: Executor | None = None, *,
                        currency: str | None = None, base: bool = True) -> Decimal:
        """رصيد حساب الخزنة / البنك (مدين − دائن) — بالجنيه، أو بعملة الخزنة لو مش الأساسية."""
        expr, cond, params = self._amount_expr("l.debit - l.credit", None if base else currency, base)
        return Decimal(self._x(ex).fetch_value(
            f"SELECT coalesce(sum({expr}), 0) FROM acc.journal_lines l "
            f"JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.account_id = %s{cond}",
            [account_id, *params]))

    # ---- العملات (0050 / 0054) ---------------------------------------------------
    def currencies(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, symbol, is_base, is_active FROM core.currencies ORDER BY NOT is_base, code")

    def base_currency(self, ex: Executor | None = None) -> str:
        return str(self._x(ex).fetch_value("SELECT code FROM core.currencies WHERE is_base"))

    def rate_on(self, code: str, day: date, ex: Executor | None = None) -> Decimal | None:
        value = self._x(ex).fetch_value("SELECT core.rate_on(%s, %s)", [code, day])
        return Decimal(value) if value is not None else None

    # ---- التوزيع على الفواتير (الصرف 0050 ← فواتير الشراء · القبض 0054 ← فواتير البيع) -----------------
    @staticmethod
    def _due_cols(kind: str) -> str:
        a = ALLOC[kind]
        return ("d.invoice_id, d.invoice_no, d.invoice_date, d.due_date, "
                f"d.{a.ref_col}, d.{a.ref_col} AS ref_no, d.stock_kind, "
                "d.currency_mode, d.currency_code, d.total_amount, d.total_base, d.returned_amount, d.returned_base, "
                "d.paid_amount, d.paid_base, d.remaining_amount, d.remaining_base")

    @staticmethod
    def _dues_where(kind: str, party_id: int, currency: str, keyword: str) -> tuple[str, list[Any]]:
        a = ALLOC[kind]
        conds = [f"d.{a.party_col} = %s", "d.currency_code = %s", "d.remaining_amount > 0"]
        params: list[Any] = [party_id, currency]
        keyword = keyword.strip()
        if keyword:
            conds.append(f"(d.invoice_no ILIKE %s OR d.{a.ref_col} ILIKE %s)")
            params += [f"%{keyword}%", f"%{keyword}%"]
        return " WHERE " + " AND ".join(conds), params

    def open_dues(self, party_id: int, currency: str, keyword: str = "", order: str = "due",
                  limit: int | None = None, offset: int = 0, ex: Executor | None = None, *,
                  kind: str = "payment") -> list[dict]:
        """فواتير الطرف الآجلة المرحّلة اللي لسه عليها متبقي **بالعملة دي** — الأقدم الأول."""
        where, params = self._dues_where(kind, party_id, currency, keyword)
        first = "coalesce(d.due_date, d.invoice_date), d.invoice_date" if order == "due" else "d.invoice_date"
        sql = (f"SELECT {self._due_cols(kind)} FROM {ALLOC[kind].dues} d{where} "
               f"ORDER BY {first}, d.invoice_id")
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_open_dues(self, party_id: int, currency: str, keyword: str = "", ex: Executor | None = None, *,
                        kind: str = "payment") -> dict:
        where, params = self._dues_where(kind, party_id, currency, keyword)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, coalesce(sum(d.remaining_amount), 0) AS remaining "
            f"FROM {ALLOC[kind].dues} d{where}", params)

    def dues_for(self, invoice_ids: list[int], currency: str, ex: Executor | None = None, *,
                 kind: str = "payment") -> dict[int, dict]:
        """المستحق على الفواتير دي بالعملة دي (الفاتورة اللي مش مرحّلة / نقدية / مافيهاش العملة مابترجعش)."""
        if not invoice_ids:
            return {}
        a = ALLOC[kind]
        rows = self._x(ex).fetch_all(
            f"SELECT {self._due_cols(kind)}, d.{a.party_col} AS party_id FROM {a.dues} d "
            "WHERE d.invoice_id = ANY(%s) AND d.currency_code = %s", [list(invoice_ids), currency])
        return {int(r["invoice_id"]): r for r in rows}

    def invoice_labels(self, invoice_ids: list[int], ex: Executor | None = None, *,
                       kind: str = "payment") -> dict[int, dict]:
        if not invoice_ids:
            return {}
        a = ALLOC[kind]
        rows = self._x(ex).fetch_all(
            f"SELECT id, invoice_no, draft_no, status, pay_kind, {a.party_col} AS party_id FROM {a.invoices} "
            "WHERE id = ANY(%s)", [list(invoice_ids)])
        return {int(r["id"]): r for r in rows}

    def lock_invoices(self, invoice_ids: list[int], ex: Executor, *, kind: str = "payment") -> None:
        """قفل الفواتير بترتيب ثابت قبل حساب المتبقي (جهازين بيدفعوا / بيحصّلوا نفس الفاتورة ← واحد بيستنى التاني)."""
        if invoice_ids:
            ex.fetch_all(f"SELECT id FROM {ALLOC[kind].invoices} WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
                         [sorted(set(invoice_ids))])

    def lock_party(self, kind: str, party_id: int, ex: Executor) -> None:
        """قفل صف الطرف ``FOR UPDATE`` وقت الترحيل (بعد الفواتير — نفس ترتيب المرتجع)."""
        ex.fetch_all(f"SELECT id FROM {PARTIES[kind][1]} WHERE id = %s FOR UPDATE", [party_id])

    def allocations(self, voucher_id: int, ex: Executor | None = None, *, kind: str = "payment") -> list[dict]:
        """سطور توزيع السند + المستحق على كل فاتورة **من غير السند ده** (لو مرحّل توزيعه جوه المسدد)."""
        a = ALLOC[kind]
        return self._x(ex).fetch_all(
            f"SELECT a.id, a.{a.invoice_col}, a.currency_code, a.amount, a.invoice_rate, a.amount_base, "
            f"i.invoice_no, i.draft_no, i.invoice_date, i.due_date, i.{a.ref_col}, i.{a.ref_col} AS ref_no, "
            "i.stock_kind, i.status AS invoice_status, d.total_amount, d.returned_amount, "
            "d.paid_amount - CASE WHEN v.status = 'posted' THEN a.amount ELSE 0 END AS paid_amount, "
            "d.remaining_amount + CASE WHEN v.status = 'posted' THEN a.amount ELSE 0 END AS remaining_amount "
            f"FROM {a.table} a "
            f"JOIN {TABLES[kind]} v ON v.id = a.{a.voucher_col} "
            f"JOIN {a.invoices} i ON i.id = a.{a.invoice_col} "
            f"LEFT JOIN {a.dues} d ON d.invoice_id = a.{a.invoice_col} AND d.currency_code = a.currency_code "
            f"WHERE a.{a.voucher_col} = %s ORDER BY coalesce(i.due_date, i.invoice_date), i.id", [voucher_id])

    def replace_allocations(self, voucher_id: int, rows: list[dict[str, Any]], user_id: int | None,
                            ex: Executor, *, kind: str = "payment") -> None:
        a = ALLOC[kind]
        ex.execute(f"DELETE FROM {a.table} WHERE {a.voucher_col} = %s", [voucher_id])
        for r in rows:
            ex.execute(
                f"INSERT INTO {a.table} ({a.voucher_col}, {a.invoice_col}, currency_code, amount, "
                "invoice_rate, amount_base, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [voucher_id, r[a.invoice_col], r["currency_code"], r["amount"], r["invoice_rate"],
                 r["amount_base"], user_id])

    def unallocated_credit(self, party_id: int, currency: str, ex: Executor | None = None, *,
                           kind: str = "payment") -> Decimal:
        """الرصيد غير الموزّع (دفعات مقدمة) من سندات الطرف المرحّلة بالعملة دي."""
        a = ALLOC[kind]
        return Decimal(self._x(ex).fetch_value(
            f"SELECT coalesce(sum(v.amount - coalesce((SELECT sum(x.amount) FROM {a.table} x "
            f"WHERE x.{a.voucher_col} = v.id), 0)), 0) FROM {TABLES[kind]} v "
            f"WHERE v.{PARTIES[kind][0]} = %s AND v.currency_code = %s AND v.status = 'posted'",
            [party_id, currency]))

    def accounts(self, ids: list[int], ex: Executor | None = None) -> dict[int, dict]:
        rows = self._x(ex).fetch_all("SELECT id, code, name_ar FROM acc.accounts WHERE id = ANY(%s)", [list(ids)])
        return {int(r["id"]): r for r in rows}

    def year_for(self, d: date, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, year_code, status, start_date, end_date FROM core.fiscal_years "
            "WHERE %s BETWEEN start_date AND end_date", [d])
