"""SQL الموظفين (``party.employees`` — سكريبت 0021) — المرحلة 3ب.

نفس فكرة ``SupplierRepository``: الكود ``EMP-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل بس).
القسم نص حر، وقايمة الاختيار = الأقسام اللي اتكتبت قبل كده. كل دالة بتاخد ``ex`` اختياري (``Tx`` من
``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (المسلسل بيتكتب مع الإضافة بس — والكود بيتحسب منه في القاعدة).
EMPLOYEE_FIELDS = ("seq", "name_ar", "name_en", "job_title", "department", "mobile", "phone", "email", "address",
                   "national_id", "hire_date", "is_sales_rep", "is_treasurer", "is_storekeeper", "takes_custody",
                   "notes", "is_active")

CODE_PREFIX = "EMP-"
# الدور ← العمود (فلتر القايمة + الشاشات اللي هتختار موظف بدور معيّن)
ROLE_COLUMNS = {"sales_rep": "is_sales_rep", "treasurer": "is_treasurer", "storekeeper": "is_storekeeper",
                "custody": "takes_custody"}

_SELECT = """
    SELECT e.id, e.seq, e.code, e.name_ar, e.name_en, e.job_title, e.department, e.mobile, e.phone, e.email,
           e.address, e.national_id, e.hire_date, e.is_sales_rep, e.is_treasurer, e.is_storekeeper,
           e.takes_custody, e.notes, e.is_active, e.created_at, e.updated_at, e.row_version
    FROM party.employees e
"""

# قفل ترقيم الكود (جهازين بيضيفوا موظف في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('party.employees.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM party.employees"

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: EMP-001 … EMP-999 ← EMP-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class EmployeeRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    @staticmethod
    def _where(keyword: str, status: str, role: str | None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            seq = keyword.upper().removeprefix(CODE_PREFIX).strip()
            conds.append("(e.code ILIKE %s OR e.seq::text = %s OR e.name_ar ILIKE %s OR e.name_en ILIKE %s "
                         "OR e.job_title ILIKE %s OR e.department ILIKE %s OR e.mobile ILIKE %s OR e.phone ILIKE %s "
                         "OR e.national_id LIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like, like, like, like]
        if status == "active":
            conds.append("e.is_active")
        elif status == "inactive":
            conds.append("NOT e.is_active")
        if role in ROLE_COLUMNS:
            conds.append(f"e.{ROLE_COLUMNS[role]}")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_employees(self, keyword: str = "", status: str = "all", role: str | None = None,
                       limit: int | None = None, offset: int = 0, ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status, role)
        sql = _SELECT + where + " ORDER BY e.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_employees(self, keyword: str = "", status: str = "all", role: str | None = None,
                        ex: Executor | None = None) -> dict:
        """العدد بالفلتر + النشط والموقوف والمندوبين (للسطر اللي تحت القائمة)."""
        where, params = self._where(keyword, status, role)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE e.is_active) AS active, "
            "count(*) FILTER (WHERE NOT e.is_active) AS inactive, "
            "count(*) FILTER (WHERE e.is_sales_rep) AS sales_reps "
            "FROM party.employees e" + where, params)

    def get_employee(self, employee_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE e.id = %s" + (" FOR UPDATE OF e" if for_update else ""),
                                     [employee_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM party.employees WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def national_id_taken(self, national_id: str, exclude_id: int | None = None,
                          ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM party.employees WHERE national_id = %s AND id IS DISTINCT FROM %s",
            [national_id, exclude_id])

    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_employee(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(EMPLOYEE_FIELDS)
        marks = ", ".join(["%s"] * len(EMPLOYEE_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO party.employees ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in EMPLOYEE_FIELDS]))

    def update_employee(self, employee_id: int, data: dict[str, Any], ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        fields = [f for f in EMPLOYEE_FIELDS if f != "seq"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE party.employees SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [employee_id, *extra])

    def set_active(self, employee_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE party.employees SET is_active = %s WHERE id = %s" + cond,
                                   [active, employee_id, *extra])

    def delete_employee(self, employee_id: int, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM party.employees WHERE id = %s" + cond, [employee_id, *extra])

    # ---- الاختيارات --------------------------------------------------------
    def department_choices(self, ex: Executor | None = None) -> list[str]:
        """الأقسام اللي اتكتبت قبل كده (قايمة الاختيار — أي قسم جديد بيظهر فيها لوحده بعد الحفظ)."""
        rows = self._x(ex).fetch_all(
            "SELECT DISTINCT department FROM party.employees WHERE department IS NOT NULL ORDER BY department")
        return [r["department"] for r in rows]
