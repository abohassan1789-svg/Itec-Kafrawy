"""SQL مراكز التكلفة (``acc.cost_centers`` — سكريبت 0013) — المرحلة 2.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
المستوى بيتحسب في القاعدة (trigger) — البرنامج مابيكتبوش.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ».
CENTER_FIELDS = ("code", "name_ar", "name_en", "parent_id", "is_leaf", "center_type", "manager_name", "notes",
                 "is_active")

_SELECT = """
    SELECT c.id, c.code, c.name_ar, c.name_en, c.parent_id, p.code AS parent_code, p.name_ar AS parent_name,
           c.level, c.is_leaf, c.center_type, c.manager_name, c.notes, c.is_active,
           c.created_at, c.updated_at, c.row_version,
           EXISTS (SELECT 1 FROM acc.cost_centers k WHERE k.parent_id = c.id) AS has_children
    FROM acc.cost_centers c
    LEFT JOIN acc.cost_centers p ON p.id = c.parent_id
"""


class CostCenterRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def list_centers(self, ex: Executor | None = None) -> list[dict]:
        """كل المراكز بترتيب الكود (الشجرة بتتبني منه في الشاشة — الأب دايماً قبل اللي تحته)."""
        return self._x(ex).fetch_all(_SELECT + " ORDER BY c.code")

    def get_center(self, center_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        sql = _SELECT + " WHERE c.id = %s"
        if for_update:
            sql += " FOR UPDATE OF c"
        return self._x(ex).fetch_one(sql, [center_id])

    def code_exists(self, code: str, exclude_id: int | None = None, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM acc.cost_centers WHERE code = %s AND id IS DISTINCT FROM %s)",
            [code, exclude_id]))

    def child_codes(self, parent_id: int | None, ex: Executor | None = None) -> list[str]:
        """أكواد اللي تحت الأب (None = المراكز الرئيسية أول مستوى)."""
        return [r["code"] for r in self._x(ex).fetch_all(
            "SELECT code FROM acc.cost_centers WHERE parent_id IS NOT DISTINCT FROM %s ORDER BY code", [parent_id])]

    def has_active_children(self, center_id: int, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM acc.cost_centers WHERE parent_id = %s AND is_active)", [center_id]))

    def codes_with_prefix(self, prefix: str, length: int, ex: Executor | None = None) -> list[str]:
        """الأكواد اللي بتبدأ بـ ``prefix`` وطولها ``length`` (في كل المراكز — مش تحت الأب بس)."""
        return [r["code"] for r in self._x(ex).fetch_all(
            "SELECT code FROM acc.cost_centers WHERE left(code, %s) = %s AND length(code) = %s ORDER BY code",
            [len(prefix), prefix, length])]

    def subtree_ids(self, center_id: int, ex: Executor | None = None) -> list[int]:
        """فلتر الأب بيجيب الأبناء: المركز نفسه + كل اللي تحته (دالة acc.cost_center_subtree)."""
        return [int(r["id"]) for r in self._x(ex).fetch_all(
            "SELECT id FROM acc.cost_center_subtree(%s) ORDER BY id", [center_id])]

    def insert_center(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(CENTER_FIELDS)
        marks = ", ".join(["%s"] * len(CENTER_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO acc.cost_centers ({cols}) VALUES ({marks}) RETURNING id",
            [data.get(f) for f in CENTER_FIELDS]))

    def update_center(self, center_id: int, data: dict[str, Any], ex: Executor | None = None,
                      expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        sets = ", ".join(f"{f} = %s" for f in CENTER_FIELDS)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE acc.cost_centers SET {sets} WHERE id = %s{cond}",
                                   [data.get(f) for f in CENTER_FIELDS] + [center_id, *extra])

    def delete_center(self, center_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM acc.cost_centers WHERE id = %s" + cond, [center_id, *extra])

    def split_center(self, center_id: int, general_code: str | None, ex: Executor | None = None) -> int | None:
        """تفريع مركز فرعي (0013): بيتحوّل رئيسي + «— عام» تحته. بيرجّع رقم «العام»."""
        value = self._x(ex).fetch_value("SELECT acc.split_cost_center(%s, %s)", [center_id, general_code])
        return None if value is None else int(value)

    def accounts_requiring(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات اللي «لازم مركز تكلفة» مع القيد (من الدليل) — للعرض في الشاشة."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_active FROM acc.accounts WHERE requires_cost_center ORDER BY code")
