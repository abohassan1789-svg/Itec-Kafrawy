"""SQL «حركات الشركاء» (``cash.partner_transactions`` · ``acc.profit_distributions`` + أنصبتها — سكريبت 0041).

* الحركات (إيداع / سحب / زيادة رأس مال) والتوزيعات **بفلتر فترة وصفحات** (index على النوع والتاريخ والشريك) ·
  الترحيل وإلغاؤه = تغيير ``status`` والقاعدة بتصرف الرقم PD / PW / PC / PF جوه قفل العدّاد.
* **أرصدة الشريك** من القيود المرحّلة ببُعد الشريك: الجاري = حسابات دور «جاري شريك» (32) · رأس المال = دور
  «رأس المال» (31) — دائن − مدين (موجب = ليه).
* **الربح المتاح للتوزيع** = رصيد «الأرباح المحتجزة» (دور retained) + صافي حسابات قائمة الدخل (إيرادات − مصروفات)
  لحد التاريخ — من القيود المرحّلة بس.
* ``lock_distributions``: قفل واحد (advisory) جوه الـ transaction — ترحيل توزيعين في نفس اللحظة بيمشوا ورا بعض.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TXN = "cash.partner_transactions"
DIST = "acc.profit_distributions"
DIST_LINES = "acc.profit_distribution_lines"
STATUSES = ("all", "draft", "posted")
TXN_KINDS = ("deposit", "withdrawal", "capital")
LOCK_KEY = 41400                     # مفتاح قفل التوزيع (pg_advisory_xact_lock)
_TXN_NO = re.compile(r"^P[DWC]-\d{4}-\d+$", re.IGNORECASE)
_DIST_NO = re.compile(r"^PF-\d{4}-\d+$", re.IGNORECASE)
TXN_FIELDS = ("txn_kind", "txn_date", "partner_id", "source_kind", "treasury_id", "bank_account_id", "amount",
              "description", "reference_no")
DIST_FIELDS = ("dist_kind", "dist_date", "amount", "description", "reference_no")

_SOURCE_NAME = "coalesce(t.name_ar, b.name_ar || ' — ' || ba.code)"
_TXN_JOINS = """
    JOIN party.partners pr ON pr.id = v.partner_id
    LEFT JOIN cash.treasuries t ON t.id = v.treasury_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks b ON b.id = ba.bank_id
"""
_TXN_SELECT = f"""
    SELECT v.id, v.txn_no, v.draft_no, v.txn_kind, v.txn_date, v.fiscal_year_id, v.partner_id, v.source_kind,
           v.treasury_id, v.bank_account_id, v.amount, v.description, v.reference_no, v.status,
           v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status, pr.code AS partner_code, pr.name_ar AS partner_name,
           coalesce(t.code, ba.code) AS source_code, {_SOURCE_NAME} AS source_name,
           cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {TXN} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    {_TXN_JOINS}
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""
_DIST_SELECT = f"""
    SELECT v.id, v.dist_no, v.draft_no, v.dist_kind, v.dist_date, v.fiscal_year_id, v.amount, v.description,
           v.reference_no, v.status, v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status, cu.username AS created_by_name, pu.username AS posted_by_name
    FROM {DIST} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    LEFT JOIN sec.app_users cu ON cu.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""
# رصيد ببُعد الشريك على حسابات دور معيّن (دائن − مدين = ليه).
_ROLE_BALANCE = ("SELECT coalesce(sum(l.credit - l.debit), 0) FROM acc.journal_lines l "
                 "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' "
                 "JOIN acc.accounts a ON a.id = l.account_id WHERE l.partner_id = %s AND a.role_code = %s")


class PartnerTransactionRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def lock_distributions(self, ex: Executor) -> None:
        """قفل التوزيع لحد آخر الـ transaction (أي ترحيل توزيع تاني بيستنى اللي قبله)."""
        ex.fetch_value("SELECT pg_advisory_xact_lock(%s, 0)", [LOCK_KEY])

    # ---- الشركاء وأرصدتهم ----------------------------------------------------------
    def partner_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, partner_type, is_active, profit_percent, loss_percent, subscribed_capital "
            "FROM party.partners ORDER BY seq")

    def share_partners(self, ex: Executor, lock: bool = False) -> list[dict]:
        """الشركاء النشطين بنسبهم (بقفل مشترك وقت الترحيل — النسب ماتتغيّرش لحد ما التوزيع يخلص)."""
        return ex.fetch_all(
            "SELECT id, code, name_ar, profit_percent, loss_percent FROM party.partners WHERE is_active "
            "ORDER BY seq" + (" FOR SHARE" if lock else ""))

    def partner_balances(self, partner_id: int, ex: Executor | None = None) -> dict[str, Decimal]:
        x = self._x(ex)
        return {"current": Decimal(x.fetch_value(_ROLE_BALANCE, [partner_id, "partner"])),
                "capital": Decimal(x.fetch_value(_ROLE_BALANCE, [partner_id, "capital"]))}

    def partner_year_totals(self, partner_id: int, on: date, ex: Executor | None = None) -> dict[str, Decimal]:
        """إيداعات ومسحوبات وزيادات رأس المال المرحّلة في سنة التاريخ."""
        row = self._x(ex).fetch_one(
            f"SELECT coalesce(sum(amount) FILTER (WHERE txn_kind = 'deposit'), 0) AS deposit, "
            "coalesce(sum(amount) FILTER (WHERE txn_kind = 'withdrawal'), 0) AS withdrawal, "
            f"coalesce(sum(amount) FILTER (WHERE txn_kind = 'capital'), 0) AS capital FROM {TXN} "
            "WHERE partner_id = %s AND status = 'posted' AND fiscal_year_id = "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date)", [partner_id, on])
        return {k: Decimal(v) for k, v in (row or {}).items()}

    def available_profit(self, on: date, ex: Executor | None = None) -> dict[str, Decimal]:
        """الأرباح المحتجزة + صافي قائمة الدخل (إيرادات − مصروفات) لحد التاريخ — من القيود المرحّلة."""
        row = self._x(ex).fetch_one(
            "SELECT coalesce(sum(l.credit - l.debit) FILTER (WHERE a.role_code = 'retained'), 0) AS retained, "
            "coalesce(sum(l.credit - l.debit) FILTER (WHERE a.statement = 'is'), 0) AS income "
            "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' "
            "JOIN acc.accounts a ON a.id = l.account_id "
            "WHERE e.entry_date <= %s AND (a.role_code = 'retained' OR a.statement = 'is')", [on])
        return {"retained": Decimal(row["retained"]), "income": Decimal(row["income"])}

    # ---- القوائم ------------------------------------------------------------------
    @staticmethod
    def _where(kind: str, date_from: date | None, date_to: date | None, status: str, keyword: str,
               partner_id: int | None) -> tuple[str, list[Any]]:
        dist = kind == "distribution"
        col, no_col, pattern = ("dist_date", "dist_no", _DIST_NO) if dist else ("txn_date", "txn_no", _TXN_NO)
        conds: list[str] = []
        params: list[Any] = []
        if not dist:
            conds.append("v.txn_kind = %s")
            params.append(kind)
        if date_from is not None:
            conds.append(f"v.{col} >= %s")
            params.append(date_from)
        if date_to is not None:
            conds.append(f"v.{col} <= %s")
            params.append(date_to)
        if status in ("draft", "posted"):
            conds.append("v.status = %s")
            params.append(status)
        if partner_id and not dist:
            conds.append("v.partner_id = %s")
            params.append(partner_id)
        elif partner_id:
            conds.append(f"EXISTS (SELECT 1 FROM {DIST_LINES} x WHERE x.distribution_id = v.id AND x.partner_id = %s)")
            params.append(partner_id)
        keyword = keyword.strip()
        if keyword and pattern.match(keyword):
            conds.append(f"v.{no_col} = %s")
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            party = "" if dist else " OR pr.name_ar ILIKE %s OR pr.code ILIKE %s"
            conds.append(f"(v.{no_col} ILIKE %s OR v.description ILIKE %s OR v.reference_no ILIKE %s{party} "
                         "OR v.draft_no::text = %s)")
            params += [like, like, like] + ([] if dist else [like, like]) + [keyword]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_docs(self, kind: str, date_from: date | None, date_to: date | None, status: str = "all",
                  keyword: str = "", partner_id: int | None = None, limit: int | None = None, offset: int = 0,
                  ex: Executor | None = None) -> list[dict]:
        where, params = self._where(kind, date_from, date_to, status, keyword, partner_id)
        if kind == "distribution":
            sql = ("SELECT v.id, v.dist_no AS doc_no, v.draft_no, v.dist_date AS doc_date, v.dist_kind, v.amount, "
                   "v.status, v.description, NULL AS partner_name, NULL AS source_name, NULL AS source_kind "
                   f"FROM {DIST} v" + where + " ORDER BY v.dist_date DESC, v.id DESC")
        else:
            sql = (f"SELECT v.id, v.txn_no AS doc_no, v.draft_no, v.txn_date AS doc_date, v.txn_kind, v.amount, "
                   f"v.status, v.description, pr.name_ar AS partner_name, {_SOURCE_NAME} AS source_name, "
                   f"v.source_kind FROM {TXN} v {_TXN_JOINS}" + where + " ORDER BY v.txn_date DESC, v.id DESC")
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_docs(self, kind: str, date_from: date | None, date_to: date | None, status: str = "all",
                   keyword: str = "", partner_id: int | None = None, ex: Executor | None = None) -> dict:
        where, params = self._where(kind, date_from, date_to, status, keyword, partner_id)
        source = f"{DIST} v" if kind == "distribution" else f"{TXN} v {_TXN_JOINS}"
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = 'posted') AS posted, "
            f"coalesce(sum(v.amount) FILTER (WHERE v.status = 'posted'), 0) AS posted_amount FROM {source}" + where,
            params)

    # ---- الحركات --------------------------------------------------------------------
    def get_txn(self, txn_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_TXN_SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""),
                                     [txn_id])

    def insert_txn(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*TXN_FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(TXN_FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {TXN} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in TXN_FIELDS], data["txn_date"], data.get("user_id")]))

    def update_txn(self, txn_id: int, data: dict[str, Any], ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in TXN_FIELDS)
        return ex.execute(f"UPDATE {TXN} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in TXN_FIELDS], txn_id, *extra])

    def set_txn_status(self, txn_id: int, status: str, user_id: int | None, ex: Executor,
                       expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TXN} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, txn_id, *extra])

    def delete_txn(self, txn_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TXN} WHERE id = %s" + clause, [txn_id, *extra])

    # ---- التوزيع --------------------------------------------------------------------
    def get_dist(self, dist_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_DIST_SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""),
                                     [dist_id])

    def dist_lines(self, dist_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            f"SELECT l.partner_id, l.share_percent, l.amount, p.code AS partner_code, p.name_ar AS partner_name "
            f"FROM {DIST_LINES} l JOIN party.partners p ON p.id = l.partner_id WHERE l.distribution_id = %s "
            "ORDER BY p.seq", [dist_id])

    def insert_dist(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ", ".join((*DIST_FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * len(DIST_FIELDS))
        return int(ex.fetch_value(
            f"INSERT INTO {DIST} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [*[data[k] for k in DIST_FIELDS], data["dist_date"], data.get("user_id")]))

    def update_dist(self, dist_id: int, data: dict[str, Any], ex: Executor,
                    expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in DIST_FIELDS)
        return ex.execute(f"UPDATE {DIST} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data[k] for k in DIST_FIELDS], dist_id, *extra])

    def replace_dist_lines(self, dist_id: int, shares: list[dict[str, Any]], ex: Executor) -> None:
        ex.execute(f"DELETE FROM {DIST_LINES} WHERE distribution_id = %s", [dist_id])
        for s in shares:
            ex.execute(f"INSERT INTO {DIST_LINES} (distribution_id, partner_id, share_percent, amount) "
                       "VALUES (%s, %s, %s, %s)", [dist_id, s["partner_id"], s["share_percent"], s["amount"]])

    def set_dist_status(self, dist_id: int, status: str, user_id: int | None, ex: Executor,
                        expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {DIST} SET status = %s, posted_by = %s WHERE id = %s" + clause,
                          [status, user_id if status == "posted" else None, dist_id, *extra])

    def delete_dist(self, dist_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {DIST} WHERE id = %s" + clause, [dist_id, *extra])
