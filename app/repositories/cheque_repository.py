"""SQL «شيكات العملاء» و«شيكات الموردين» (``cash.cheques`` + ``cash.cheque_events`` — سكريبت 0038).

* جدول واحد للاتنين بـ ``direction`` (``in`` وارد من عميل · ``out`` صادر لمورد) — كل دالة بتاخد الاتجاه.
* القائمة **بفلتر فترة وصفحات** (تاريخ الاستلام / الإصدار) · «المفتوحة» من غير فترة · التنبيهات بالاستحقاق
  (index على ``direction, status, due_date``) · رقم المستند الكامل من الـ unique index.
* الخطوات في ``cash.cheque_events`` (صف لكل خطوة — آخر صف بس بيتمسح) والقاعدة بتتأكد من الانتقال والتاريخ والقيد.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

TABLE = "cash.cheques"
EVENTS = "cash.cheque_events"
_FULL_NO = re.compile(r"^CH[RP]-\d{4}-\d+$", re.IGNORECASE)
OPEN = {"in": ("portfolio", "under_collection", "bounced"), "out": ("issued",)}
DONE = {"in": ("collected", "endorsed", "returned"), "out": ("cleared", "cancelled")}
FILTERS = ("all", "draft", "open", "done")
FIELDS = ("cheque_date", "due_date", "cheque_no", "customer_id", "supplier_id", "drawer_bank_id", "bank_account_id",
          "amount", "description", "project_id")

_JOINS = """
    LEFT JOIN party.customers cu ON cu.id = v.customer_id
    LEFT JOIN party.suppliers su ON su.id = v.supplier_id
    LEFT JOIN cash.banks db ON db.id = v.drawer_bank_id
    LEFT JOIN cash.bank_accounts ba ON ba.id = v.bank_account_id
    LEFT JOIN cash.banks bb ON bb.id = ba.bank_id
"""
# الطرف: العميل للوارد · المورد للصادر (المورد في الوارد = المظهَّر له).
_PARTY = "CASE v.direction WHEN 'in' THEN cu.name_ar ELSE su.name_ar END"
_PARTY_CODE = "CASE v.direction WHEN 'in' THEN cu.code ELSE su.code END"
# البنك: بنك الساحب للوارد · حسابنا للصادر.
_BANK = "CASE v.direction WHEN 'in' THEN db.name_ar ELSE bb.name_ar END"

_SELECT = f"""
    SELECT v.id, v.direction, v.cheque_ref, v.draft_no, v.cheque_date, v.due_date, v.fiscal_year_id, v.cheque_no,
           v.customer_id, v.supplier_id, v.drawer_bank_id, v.bank_account_id, v.amount, v.description, v.project_id,
           v.status, v.status_date, v.created_by, v.posted_at, v.posted_by, v.created_at, v.updated_at, v.row_version,
           y.year_code, y.status AS year_status,
           {_PARTY_CODE} AS party_code, {_PARTY} AS party_name,
           CASE v.direction WHEN 'in' THEN v.customer_id ELSE v.supplier_id END AS party_id,
           cu.code AS customer_code, cu.name_ar AS customer_name, su.code AS supplier_code, su.name_ar AS supplier_name,
           db.name_ar AS drawer_bank_name, ba.code AS bank_code, bb.name_ar AS bank_name, ba.account_id AS bank_gl_id,
           {_BANK} AS bank_label, p.code AS project_code, p.name AS project_name,
           cr.username AS created_by_name, pu.username AS posted_by_name
    FROM {TABLE} v
    JOIN core.fiscal_years y ON y.id = v.fiscal_year_id
    {_JOINS}
    LEFT JOIN acc.projects p ON p.id = v.project_id
    LEFT JOIN sec.app_users cr ON cr.id = v.created_by
    LEFT JOIN sec.app_users pu ON pu.id = v.posted_by
"""

_LIST = f"""
    SELECT v.id, v.direction, v.cheque_ref, v.draft_no, v.cheque_date, v.due_date, v.cheque_no, v.amount, v.status,
           v.description, {_PARTY} AS party_name, {_BANK} AS bank_label
    FROM {TABLE} v
    {_JOINS}
"""


class ChequeRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- القائمة ---------------------------------------------------------------
    @staticmethod
    def _where(direction: str, date_from: date | None, date_to: date | None, status: str,
               keyword: str) -> tuple[str, list[Any]]:
        conds: list[str] = ["v.direction = %s"]
        params: list[Any] = [direction]
        if status == "open":                                        # المفتوحة: كلها من غير فترة
            conds.append("v.status = ANY(%s)")
            params.append(list(OPEN[direction]))
        else:
            if date_from is not None:
                conds.append("v.cheque_date >= %s")
                params.append(date_from)
            if date_to is not None:
                conds.append("v.cheque_date <= %s")
                params.append(date_to)
            if status == "draft":
                conds.append("v.status = 'draft'")
            elif status == "done":
                conds.append("v.status = ANY(%s)")
                params.append(list(DONE[direction]))
        keyword = keyword.strip()
        if keyword and _FULL_NO.match(keyword):
            conds.append("v.cheque_ref = %s")                       # من الـ unique index على طول
            params.append(keyword.upper())
        elif keyword:
            like = f"%{keyword}%"
            conds.append(f"(v.cheque_ref ILIKE %s OR v.cheque_no ILIKE %s OR v.description ILIKE %s "
                         f"OR {_PARTY} ILIKE %s OR {_BANK} ILIKE %s OR v.draft_no::text = %s)")
            params += [like, like, like, like, like, keyword]
        return " WHERE " + " AND ".join(conds), params

    def list_cheques(self, direction: str, date_from: date | None, date_to: date | None, status: str = "all",
                     keyword: str = "", limit: int | None = None, offset: int = 0,
                     ex: Executor | None = None) -> list[dict]:
        where, params = self._where(direction, date_from, date_to, status, keyword)
        order = (" ORDER BY v.due_date, v.id" if status == "open"
                 else " ORDER BY v.cheque_date DESC, v.id DESC")
        sql = _LIST + where + order
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_cheques(self, direction: str, date_from: date | None, date_to: date | None, status: str = "all",
                      keyword: str = "", ex: Executor | None = None) -> dict:
        where, params = self._where(direction, date_from, date_to, status, keyword)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE v.status = 'draft') AS drafts, "
            "count(*) FILTER (WHERE v.status = ANY(%s)) AS open, "
            "coalesce(sum(v.amount) FILTER (WHERE v.status = ANY(%s)), 0) AS open_amount, "
            "coalesce(sum(v.amount) FILTER (WHERE v.status <> 'draft'), 0) AS posted_amount "
            f"FROM {TABLE} v {_JOINS}" + where,
            [list(OPEN[direction]), list(OPEN[direction]), *params])

    def due_cheques(self, direction: str, until: date, ex: Executor | None = None) -> list[dict]:
        """الشيكات المفتوحة اللي استحقاقها لحد ``until`` (المتأخر جوّاها) — للكارت والشاشة اللي بتطلع منه."""
        return self._x(ex).fetch_all(
            f"SELECT v.id, v.cheque_ref, v.cheque_no, v.due_date, v.amount, v.status, {_PARTY} AS party_name, "
            f"{_BANK} AS bank_label FROM {TABLE} v {_JOINS} "
            "WHERE v.direction = %s AND v.status = ANY(%s) AND v.due_date <= %s ORDER BY v.due_date, v.id",
            [direction, list(OPEN[direction]), until])

    # ---- الشيك ------------------------------------------------------------------
    def get_cheque(self, cheque_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _SELECT + " WHERE v.id = %s" + (" FOR UPDATE OF v" if for_update else ""), [cheque_id])

    def insert_cheque(self, direction: str, data: dict[str, Any], ex: Executor) -> int:
        """``fiscal_year_id`` الحقيقي بتحطه القاعدة (التريجر) — هنا قيمة مبدئية بس."""
        cols = ", ".join(("direction", *FIELDS, "fiscal_year_id", "created_by"))
        marks = ", ".join(["%s"] * (len(FIELDS) + 1))
        return int(ex.fetch_value(
            f"INSERT INTO {TABLE} ({cols}) VALUES ({marks}, "
            "(SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), %s) RETURNING id",
            [direction, *[data.get(k) for k in FIELDS], data["cheque_date"], data.get("user_id")]))

    def update_cheque(self, cheque_id: int, data: dict[str, Any], ex: Executor,
                      expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        sets = ", ".join(f"{k} = %s" for k in FIELDS)
        return ex.execute(f"UPDATE {TABLE} SET {sets} WHERE id = %s AND status = 'draft'" + clause,
                          [*[data.get(k) for k in FIELDS], cheque_id, *extra])

    def set_state(self, cheque_id: int, status: str, status_date: date | None, ex: Executor, *,
                  bank_account_id: Any = ..., supplier_id: Any = ..., posted_by: Any = ...,
                  expected_version: int | None = None) -> int:
        """الحالة وتاريخها (+ بنك الإيداع / المظهَّر له / اللي رحّل لو اتبعتوا) — مع الخطوة في نفس الـ transaction."""
        sets, params = ["status = %s", "status_date = %s"], [status, status_date]
        for column, value in (("bank_account_id", bank_account_id), ("supplier_id", supplier_id),
                              ("posted_by", posted_by)):
            if value is not ...:
                sets.append(f"{column} = %s")
                params.append(value)
        clause, extra = version_clause(expected_version)
        return ex.execute(f"UPDATE {TABLE} SET {', '.join(sets)} WHERE id = %s" + clause,
                          [*params, cheque_id, *extra])

    def delete_cheque(self, cheque_id: int, ex: Executor, expected_version: int | None = None) -> int:
        clause, extra = version_clause(expected_version)
        return ex.execute(f"DELETE FROM {TABLE} WHERE id = %s" + clause, [cheque_id, *extra])

    def duplicate(self, direction: str, bank_id: int, cheque_no: str, exclude_id: int | None,
                  ex: Executor | None = None) -> dict | None:
        """شيك بنفس الرقم على نفس البنك (بنك الساحب للوارد · حسابنا للصادر)."""
        column = "drawer_bank_id" if direction == "in" else "bank_account_id"
        return self._x(ex).fetch_one(
            f"SELECT id, cheque_ref, draft_no FROM {TABLE} WHERE direction = %s AND {column} = %s "
            "AND upper(btrim(cheque_no)) = upper(btrim(%s)) AND id IS DISTINCT FROM %s",
            [direction, bank_id, cheque_no, exclude_id])

    # ---- الخطوات -------------------------------------------------------------------
    def transitions(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT direction, event_type, from_status, to_status, has_entry, name_ar, sort_order "
            "FROM cash.cheque_transitions ORDER BY direction, sort_order")

    def events(self, cheque_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT e.id, e.seq, e.event_type, e.event_date, e.from_status, e.to_status, e.has_entry, "
            "e.bank_account_id, e.supplier_id, e.fees, e.note, e.created_at, u.username AS created_by_name, "
            "ba.code AS bank_code, b.name_ar AS bank_name, s.code AS supplier_code, s.name_ar AS supplier_name, "
            "t.name_ar, j.id AS entry_id "
            f"FROM {EVENTS} e JOIN {TABLE} c ON c.id = e.cheque_id "
            "JOIN cash.cheque_transitions t ON t.direction = c.direction AND t.event_type = e.event_type "
            "AND t.from_status = e.from_status "
            "LEFT JOIN cash.bank_accounts ba ON ba.id = e.bank_account_id LEFT JOIN cash.banks b ON b.id = ba.bank_id "
            "LEFT JOIN party.suppliers s ON s.id = e.supplier_id "
            "LEFT JOIN sec.app_users u ON u.id = e.created_by "
            "LEFT JOIN acc.journal_entries j ON j.source_type = CASE c.direction WHEN 'in' THEN 'cheque_in' "
            "ELSE 'cheque_out' END AND j.source_id = e.id "
            "WHERE e.cheque_id = %s ORDER BY e.seq", [cheque_id])

    def insert_event(self, data: dict[str, Any], ex: Executor) -> int:
        cols = ("cheque_id", "seq", "event_type", "event_date", "from_status", "to_status", "has_entry",
                "bank_account_id", "supplier_id", "fees", "note", "created_by")
        return int(ex.fetch_value(
            f"INSERT INTO {EVENTS} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            [data.get(k) for k in cols]))

    def delete_event(self, event_id: int, ex: Executor) -> int:
        return ex.execute(f"DELETE FROM {EVENTS} WHERE id = %s", [event_id])

    # ---- الاختيارات -------------------------------------------------------------
    def banks(self, ex: Executor | None = None) -> list[dict]:
        """قايمة البنوك بتاعتنا (بنك الساحب للشيك الوارد)."""
        return self._x(ex).fetch_all("SELECT id, code, name_ar, is_active FROM cash.banks ORDER BY seq")

    def issued_on_account(self, bank_account_id: int, exclude_id: int | None = None,
                          ex: Executor | None = None) -> Any:
        """مجموع الشيكات الصادرة لسه ماتصرفتش من الحساب (عشان تحذير الرصيد)."""
        return self._x(ex).fetch_value(
            f"SELECT coalesce(sum(amount), 0) FROM {TABLE} WHERE direction = 'out' AND status = 'issued' "
            "AND bank_account_id = %s AND id IS DISTINCT FROM %s", [bank_account_id, exclude_id])
