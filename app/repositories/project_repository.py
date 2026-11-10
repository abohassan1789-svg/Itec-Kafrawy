"""SQL أوامر التوريد (``acc.projects`` — سكريبت 0016) — المرحلة 3أ.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان الحفظ وسجله يبقوا في transaction واحدة.
الترقيم (``acc.next_project_no``) بيقفل صف العدّاد لحد آخر الـ transaction ← جهازين مايخدوش نفس الرقم.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ».
PROJECT_FIELDS = ("code", "name", "customer_id", "cost_center_id", "amount", "currency_code", "tax_inclusive",
                  "doc_kind", "order_date", "start_date", "end_date", "status", "notes")

STATUSES = ("open", "closed", "cancelled")
FILTERS = ("all", *STATUSES)

_SELECT = """
    SELECT p.id, p.code, p.name, p.customer_id, cu.code AS customer_code, cu.name_ar AS customer_name,
           cu.is_active AS customer_active,
           p.cost_center_id, cc.code AS center_code, cc.name_ar AS center_name, cc.is_active AS center_active,
           cc.is_leaf AS center_leaf,
           p.amount, p.currency_code, p.tax_inclusive, p.doc_kind, p.order_date, p.start_date, p.end_date,
           p.status, p.notes, p.created_at, p.updated_at, p.row_version
    FROM acc.projects p
    JOIN acc.cost_centers cc ON cc.id = p.cost_center_id
    LEFT JOIN party.customers cu ON cu.id = p.customer_id
"""


class ProjectRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    @staticmethod
    def _where(keyword: str, status: str, center_id: int | None, year: int | None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            conds.append("(p.code ILIKE %s OR p.name ILIKE %s OR cu.name_ar ILIKE %s OR cu.name_en ILIKE %s "
                         "OR cu.code = %s)")
            params += [like, like, like, like, keyword]
        if status in STATUSES:
            conds.append("p.status = %s")
            params.append(status)
        if center_id is not None:                               # فلتر الأب بيجيب الأبناء
            conds.append("p.cost_center_id IN (SELECT id FROM acc.cost_center_subtree(%s))")
            params.append(center_id)
        if year is not None:                                    # فلتر الفترة (سنة تاريخ الأمر)
            conds.append("p.order_date >= %s AND p.order_date < %s")
            params += [date(year, 1, 1), date(year + 1, 1, 1)]
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_projects(self, keyword: str = "", status: str = "all", center_id: int | None = None,
                      year: int | None = None, limit: int | None = None, offset: int = 0,
                      ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status, center_id, year)
        sql = _SELECT + where + " ORDER BY p.order_date DESC, p.id DESC"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_projects(self, keyword: str = "", status: str = "all", center_id: int | None = None,
                       year: int | None = None, ex: Executor | None = None) -> dict:
        """العدد بالفلتر + كل حالة (للسطر اللي تحت القائمة)."""
        where, params = self._where(keyword, status, center_id, year)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE p.status = 'open') AS open, "
            "count(*) FILTER (WHERE p.status = 'closed') AS closed, "
            "count(*) FILTER (WHERE p.status = 'cancelled') AS cancelled "
            "FROM acc.projects p LEFT JOIN party.customers cu ON cu.id = p.customer_id" + where, params)

    def years(self, ex: Executor | None = None) -> list[int]:
        """السنين اللي فيها أوامر (لفلتر الفترة) — الأحدث الأول."""
        return [int(r["y"]) for r in self._x(ex).fetch_all(
            "SELECT DISTINCT extract(year FROM order_date)::int AS y FROM acc.projects ORDER BY y DESC")]

    def get_project(self, project_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE p.id = %s" + (" FOR UPDATE OF p" if for_update else ""),
                                     [project_id])

    def code_taken(self, code: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name FROM acc.projects WHERE lower(code) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [code, exclude_id])

    def next_code(self, order_date: date, ex: Executor) -> str:
        """بيصرف الرقم الجاي (جوه قفل العدّاد — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        return ex.fetch_value("SELECT acc.next_project_no(%s)", [order_date])

    def suggested_code(self, order_date: date | None = None, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتصرف وقت الحفظ)."""
        return self._x(ex).fetch_value("SELECT acc.peek_project_no(%s)", [order_date or date.today()])

    def numbering(self, ex: Executor | None = None) -> dict:
        return self._x(ex).fetch_one("SELECT pattern, digits, last_no FROM acc.project_numbering WHERE id = 1")

    def insert_project(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(PROJECT_FIELDS)
        marks = ", ".join(["%s"] * len(PROJECT_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO acc.projects ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in PROJECT_FIELDS]))

    def update_project(self, project_id: int, data: dict[str, Any], ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        sets = ", ".join(f"{f} = %s" for f in PROJECT_FIELDS)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE acc.projects SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in PROJECT_FIELDS] + [project_id, *extra])

    def delete_project(self, project_id: int, ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM acc.projects WHERE id = %s" + cond, [project_id, *extra])

    # ---- الاختيارات --------------------------------------------------------
    def customer_choices(self, ex: Executor | None = None) -> list[dict]:
        """العملاء النشطين (للأمر الجديد) بعملتهم الافتراضية."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, currency_code FROM party.customers WHERE is_active ORDER BY code::bigint")

    def center_choices(self, ex: Executor | None = None) -> list[dict]:
        """كل المراكز (الرئيسية للفلتر، والفرعية النشطة للأمر)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, level, is_leaf, is_active FROM acc.cost_centers ORDER BY code")

    def currency_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, is_base FROM core.currencies WHERE is_active ORDER BY is_base DESC, code")
