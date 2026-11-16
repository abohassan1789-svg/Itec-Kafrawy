"""SQL الماركات (``inv.item_brands`` — سكريبت 0027) — المرحلة 3د.

نفس فكرة ``WarehouseRepository``: الكود ``BRD-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل
بس، جوه قفل الترقيم). الاسم بيتخزن متنضف والمقارنة من غير حروف كبيرة/صغيرة.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

BRAND_FIELDS = ("seq", "name", "name_ar", "country", "notes", "is_active")

CODE_PREFIX = "BRD-"

_SELECT = """
    SELECT b.id, b.seq, b.code, b.name, b.name_ar, b.country, b.notes, b.is_active, b.created_at, b.updated_at,
           b.row_version
    FROM inv.item_brands b
"""

# قفل ترقيم الكود (جهازين بيضيفوا ماركة في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('inv.item_brands.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM inv.item_brands"

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: BRD-001 … BRD-999 ← BRD-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class BrandRepository:
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
            conds.append("(b.code ILIKE %s OR b.seq::text = %s OR b.name ILIKE %s OR b.name_ar ILIKE %s "
                         "OR b.country ILIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like]
        if status == "active":
            conds.append("b.is_active")
        elif status == "inactive":
            conds.append("NOT b.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_brands(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                    ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status)
        sql = _SELECT + where + " ORDER BY lower(b.name), b.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_brands(self, keyword: str = "", status: str = "all", ex: Executor | None = None) -> dict:
        where, params = self._where(keyword, status)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE b.is_active) AS active, "
            "count(*) FILTER (WHERE NOT b.is_active) AS inactive FROM inv.item_brands b" + where, params)

    def get_brand(self, brand_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE b.id = %s" + (" FOR UPDATE OF b" if for_update else ""),
                                     [brand_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        """``name`` لازم يكون متنضف (الـ Service بينضفه) — المقارنة من غير حروف كبيرة/صغيرة."""
        return self._x(ex).fetch_one(
            "SELECT id, code, name FROM inv.item_brands WHERE lower(name) = lower(%s) AND id IS DISTINCT FROM %s",
            [name, exclude_id])

    # ---- الكتابة ---------------------------------------------------------------
    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_brand(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(BRAND_FIELDS)
        marks = ", ".join(["%s"] * len(BRAND_FIELDS))
        return int(self._x(ex).fetch_value(f"INSERT INTO inv.item_brands ({cols}) VALUES ({marks}) RETURNING id",
                                           [data[f] for f in BRAND_FIELDS]))

    def update_brand(self, brand_id: int, data: dict[str, Any], ex: Executor | None = None,
                     expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني). المسلسل ثابت."""
        fields = [f for f in BRAND_FIELDS if f != "seq"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE inv.item_brands SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [brand_id, *extra])

    def set_active(self, brand_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE inv.item_brands SET is_active = %s WHERE id = %s" + cond,
                                   [active, brand_id, *extra])

    def delete_brand(self, brand_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM inv.item_brands WHERE id = %s" + cond, [brand_id, *extra])
