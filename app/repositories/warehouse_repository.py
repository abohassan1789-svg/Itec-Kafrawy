"""SQL المخازن (``inv.warehouses`` — سكريبت 0026) — المرحلة 3د.

نفس فكرة ``TreasuryRepository``: الكود ``WH-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل بس).
القائمة بتجيب الأمين في نفس الاستعلام. مفيش حساب لكل مخزن (كل المخازن على حساب المخزون الواحد).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (المسلسل بيتكتب مع الإضافة بس).
WAREHOUSE_FIELDS = ("seq", "name_ar", "name_en", "is_virtual", "is_default", "keeper_id", "address", "phone",
                    "notes", "is_active")

CODE_PREFIX = "WH-"

_SELECT = """
    SELECT w.id, w.seq, w.code, w.name_ar, w.name_en, w.is_virtual, w.is_default, w.keeper_id, w.address,
           w.phone, w.notes, w.is_active, w.created_at, w.updated_at, w.row_version,
           e.code AS keeper_code, e.name_ar AS keeper_name
    FROM inv.warehouses w
    LEFT JOIN party.employees e ON e.id = w.keeper_id
"""

# قفل ترقيم الكود (جهازين بيضيفوا مخزن في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('inv.warehouses.code'))"
# قفل «الافتراضي» (جهازين بيعلّموا مخزنين مختلفين افتراضي في نفس اللحظة ← واحد يستنى التاني).
_DEFAULT_LOCK = "SELECT pg_advisory_xact_lock(hashtext('inv.warehouses.default'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM inv.warehouses"

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: WH-001 … WH-999 ← WH-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class WarehouseRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    @staticmethod
    def _where(keyword: str, status: str) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            seq = keyword.upper().removeprefix(CODE_PREFIX).strip()
            conds.append("(w.code ILIKE %s OR w.seq::text = %s OR w.name_ar ILIKE %s OR w.name_en ILIKE %s "
                         "OR e.name_ar ILIKE %s OR w.address ILIKE %s OR w.phone LIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like, like]
        if status == "active":
            conds.append("w.is_active")
        elif status == "inactive":
            conds.append("NOT w.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_warehouses(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                        ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status)
        sql = _SELECT + where + " ORDER BY w.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_warehouses(self, keyword: str = "", status: str = "all", ex: Executor | None = None) -> dict:
        """العدد بالفلتر + النشطة والموقوفة (للسطر اللي تحت القائمة)."""
        where, params = self._where(keyword, status)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE w.is_active) AS active, "
            "count(*) FILTER (WHERE NOT w.is_active) AS inactive FROM inv.warehouses w "
            "LEFT JOIN party.employees e ON e.id = w.keeper_id" + where, params)

    def stats(self, ex: Executor | None = None) -> dict:
        """كروت الأرقام اللي فوق (من غير فلتر): النشطة · الوهمية النشطة · العادية النشطة من غير أمين · الافتراضي."""
        return self._x(ex).fetch_one(
            "SELECT count(*) FILTER (WHERE is_active) AS active, "
            "count(*) FILTER (WHERE is_active AND is_virtual) AS virtual, "
            "count(*) FILTER (WHERE is_active AND NOT is_virtual AND keeper_id IS NULL) AS no_keeper, "
            "max(code) FILTER (WHERE is_default) AS default_code, "
            "max(name_ar) FILTER (WHERE is_default) AS default_name "
            "FROM inv.warehouses")

    def get_warehouse(self, warehouse_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE w.id = %s" + (" FOR UPDATE OF w" if for_update else ""),
                                     [warehouse_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM inv.warehouses WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    # ---- الاختيارات -------------------------------------------------------------
    def list_keepers(self, ex: Executor | None = None) -> list[dict]:
        """الموظفين النشطين اللي عليهم دور «أمين مخزن»."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM party.employees WHERE is_storekeeper AND is_active ORDER BY seq")

    def get_employee(self, employee_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar, is_storekeeper, is_active FROM party.employees WHERE id = %s", [employee_id])

    def inventory_account(self, ex: Executor | None = None) -> dict | None:
        """حساب المخزون الواحد لكل المخازن («المخزون» في الحسابات الافتراضية) — للعرض بس."""
        return self._x(ex).fetch_one(
            "SELECT a.code, a.name_ar FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
            "WHERE d.key = 'inventory'")

    # ---- الكتابة ---------------------------------------------------------------
    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def lock_default(self, ex: Executor) -> dict | None:
        """قفل «الافتراضي» + المخزن الافتراضي الحالي مقفول (عشان يتشال منه العلامة في نفس الـ transaction)."""
        ex.execute(_DEFAULT_LOCK)
        return ex.fetch_one(_SELECT + " WHERE w.is_default FOR UPDATE OF w")

    def clear_default(self, warehouse_id: int, ex: Executor) -> int:
        return ex.execute("UPDATE inv.warehouses SET is_default = false WHERE id = %s AND is_default",
                          [warehouse_id])

    def insert_warehouse(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(WAREHOUSE_FIELDS)
        marks = ", ".join(["%s"] * len(WAREHOUSE_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO inv.warehouses ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in WAREHOUSE_FIELDS]))

    def update_warehouse(self, warehouse_id: int, data: dict[str, Any], ex: Executor | None = None,
                         expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني). المسلسل ثابت."""
        fields = [f for f in WAREHOUSE_FIELDS if f != "seq"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE inv.warehouses SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [warehouse_id, *extra])

    def set_active(self, warehouse_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE inv.warehouses SET is_active = %s WHERE id = %s" + cond,
                                   [active, warehouse_id, *extra])

    def delete_warehouse(self, warehouse_id: int, ex: Executor | None = None,
                         expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM inv.warehouses WHERE id = %s" + cond, [warehouse_id, *extra])
