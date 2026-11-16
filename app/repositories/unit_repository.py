"""SQL الوحدات (``inv.units`` — سكريبت 0027) — المرحلة 3د.

الرمز (``code``) بيكتبه المستخدم (PCS · M · KG) — مش مسلسل. «الافتراضية» واحدة بس ونقلها بيتم جوه قفل.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

UNIT_FIELDS = ("code", "name_ar", "name_en", "category", "allows_fraction", "is_default", "notes", "is_active")

# (المفتاح، الاسم) — الترتيب = ترتيب القايمة في الشاشة.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("count", "عدد"), ("length", "طول"), ("weight", "وزن"), ("area", "مساحة"), ("volume", "حجم"), ("time", "وقت"))
CATEGORY_NAMES = dict(CATEGORIES)
_CATEGORY_ORDER = "CASE u.category " + " ".join(
    f"WHEN '{key}' THEN {i}" for i, (key, _name) in enumerate(CATEGORIES)) + " END"

_SELECT = """
    SELECT u.id, u.code, u.name_ar, u.name_en, u.category, u.allows_fraction, u.is_default, u.notes, u.is_active,
           u.created_at, u.updated_at, u.row_version
    FROM inv.units u
"""

# قفل «الافتراضية» (جهازين بيعلّموا وحدتين مختلفتين افتراضية في نفس اللحظة ← واحد يستنى التاني).
_DEFAULT_LOCK = "SELECT pg_advisory_xact_lock(hashtext('inv.units.default'))"

STATUSES = ("all", "active", "inactive")


class UnitRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    @staticmethod
    def _where(keyword: str, status: str, category: str = "") -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            conds.append("(u.code ILIKE %s OR u.name_ar ILIKE %s OR u.name_en ILIKE %s)")
            params += [like, like, like]
        if status == "active":
            conds.append("u.is_active")
        elif status == "inactive":
            conds.append("NOT u.is_active")
        if category:
            conds.append("u.category = %s")
            params.append(category)
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_units(self, keyword: str = "", status: str = "all", category: str = "",
                   ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status, category)
        return self._x(ex).fetch_all(_SELECT + where + f" ORDER BY {_CATEGORY_ORDER}, u.id", params)

    def count_units(self, keyword: str = "", status: str = "all", ex: Executor | None = None) -> dict:
        where, params = self._where(keyword, status)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE u.is_active) AS active, "
            "count(*) FILTER (WHERE NOT u.is_active) AS inactive FROM inv.units u" + where, params)

    def get_unit(self, unit_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE u.id = %s" + (" FOR UPDATE OF u" if for_update else ""),
                                     [unit_id])

    def code_taken(self, code: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM inv.units WHERE code = %s AND id IS DISTINCT FROM %s", [code, exclude_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM inv.units WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    # ---- الكتابة ---------------------------------------------------------------
    def lock_default(self, ex: Executor) -> dict | None:
        """قفل «الافتراضية» + الوحدة الافتراضية الحالية مقفولة (عشان العلامة تتشال منها في نفس الـ transaction)."""
        ex.execute(_DEFAULT_LOCK)
        return ex.fetch_one(_SELECT + " WHERE u.is_default FOR UPDATE OF u")

    def clear_default(self, unit_id: int, ex: Executor) -> int:
        return ex.execute("UPDATE inv.units SET is_default = false WHERE id = %s AND is_default", [unit_id])

    def insert_unit(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(UNIT_FIELDS)
        marks = ", ".join(["%s"] * len(UNIT_FIELDS))
        return int(self._x(ex).fetch_value(f"INSERT INTO inv.units ({cols}) VALUES ({marks}) RETURNING id",
                                           [data[f] for f in UNIT_FIELDS]))

    def update_unit(self, unit_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        sets = ", ".join(f"{f} = %s" for f in UNIT_FIELDS)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE inv.units SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in UNIT_FIELDS] + [unit_id, *extra])

    def set_active(self, unit_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE inv.units SET is_active = %s WHERE id = %s" + cond,
                                   [active, unit_id, *extra])

    def delete_unit(self, unit_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM inv.units WHERE id = %s" + cond, [unit_id, *extra])
