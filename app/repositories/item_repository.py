"""SQL الأصناف (``inv.items`` + ``inv.item_units`` — سكريبت 0029) — المرحلة 3د.

نفس فكرة ``BrandRepository``: الكود ``ITM-0001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل بس،
جوه قفل الترقيم) · القائمة بصفحات (الأصناف بالآلاف) وبتجيب النوع والماركة والوحدة والمخزن والضريبة في نفس الاستعلام.
الوحدات التانية للصنف بتتكتب من جديد مع كل حفظ (مسح + إضافة في نفس الـ transaction).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

ITEM_FIELDS = ("seq", "name", "alt_name", "description", "category_id", "brand_id", "unit_id", "is_service",
               "default_warehouse_id", "tax_code_id", "sale_price", "reorder_level", "notes", "is_active")
_FIXED = ("seq",)

CODE_PREFIX = "ITM-"
STATUSES = ("all", "active", "inactive")
KINDS = ("all", "goods", "service")

_SELECT = """
    SELECT i.id, i.seq, i.code, i.name, i.alt_name, i.description, i.category_id, i.brand_id, i.unit_id,
           i.is_service, i.default_warehouse_id, i.tax_code_id, i.sale_price, i.reorder_level, i.notes, i.is_active,
           i.created_at, i.updated_at, i.row_version,
           c.code AS category_code, c.name AS category_name, c.is_active AS category_active,
           cp.name AS category_parent_name,
           b.name AS brand_name, b.is_active AS brand_active,
           u.code AS unit_code, u.name_ar AS unit_name, u.allows_fraction,
           w.code AS warehouse_code, w.name_ar AS warehouse_name,
           t.code AS tax_code, t.name_ar AS tax_name,
           (SELECT count(*) FROM inv.item_units x WHERE x.item_id = i.id) AS units_count
    FROM inv.items i
    LEFT JOIN inv.item_categories c ON c.id = i.category_id
    LEFT JOIN inv.item_categories cp ON cp.id = c.parent_id
    LEFT JOIN inv.item_brands b ON b.id = i.brand_id
    JOIN inv.units u ON u.id = i.unit_id
    LEFT JOIN inv.warehouses w ON w.id = i.default_warehouse_id
    JOIN acc.tax_codes t ON t.id = i.tax_code_id
"""

# قفل ترقيم الكود (جهازين بيضيفوا صنف في نفس اللحظة مياخدوش نفس الرقم — وبيرتّب فحص الاسم المتكرر).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('inv.items.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM inv.items"


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: ITM-0001 … ITM-9999 ← ITM-10000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:04d}" if seq < 10000 else f"{CODE_PREFIX}{seq}"


class ItemRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    @staticmethod
    def _where(keyword: str, status: str, kind: str = "all", category_ids: list[int] | None = None,
               brand_id: int | None = None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            seq = keyword.upper().removeprefix(CODE_PREFIX).strip()
            conds.append("(i.code ILIKE %s OR i.seq::text = %s OR i.name ILIKE %s OR i.alt_name ILIKE %s "
                         "OR i.description ILIKE %s OR b.name ILIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like]
        if status == "active":
            conds.append("i.is_active")
        elif status == "inactive":
            conds.append("NOT i.is_active")
        if kind == "goods":
            conds.append("NOT i.is_service")
        elif kind == "service":
            conds.append("i.is_service")
        if category_ids is not None:
            conds.append("i.category_id = ANY(%s)")
            params.append(list(category_ids))
        if brand_id is not None:
            conds.append("i.brand_id = %s")
            params.append(brand_id)
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_items(self, keyword: str = "", status: str = "all", kind: str = "all",
                   category_ids: list[int] | None = None, brand_id: int | None = None,
                   limit: int | None = None, offset: int = 0, ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status, kind, category_ids, brand_id)
        sql = _SELECT + where + " ORDER BY i.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_items(self, keyword: str = "", status: str = "all", kind: str = "all",
                    category_ids: list[int] | None = None, brand_id: int | None = None,
                    ex: Executor | None = None) -> dict:
        where, params = self._where(keyword, status, kind, category_ids, brand_id)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE i.is_active) AS active, "
            "count(*) FILTER (WHERE NOT i.is_active) AS inactive FROM inv.items i "
            "LEFT JOIN inv.item_brands b ON b.id = i.brand_id" + where, params)

    def stats(self, ex: Executor | None = None) -> dict:
        """كروت الأرقام (من غير فلتر): النشطة · الخدمية النشطة · النشطة من غير نوع · النشطة بحد طلب."""
        return self._x(ex).fetch_one(
            "SELECT count(*) FILTER (WHERE is_active) AS active, "
            "count(*) FILTER (WHERE is_active AND is_service) AS service, "
            "count(*) FILTER (WHERE is_active AND category_id IS NULL) AS no_category, "
            "count(*) FILTER (WHERE is_active AND reorder_level IS NOT NULL) AS with_reorder "
            "FROM inv.items")

    def get_item(self, item_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE i.id = %s" + (" FOR UPDATE OF i" if for_update else ""),
                                     [item_id])

    def item_units(self, item_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT x.unit_id, x.factor, u.code AS unit_code, u.name_ar AS unit_name, u.is_active "
            "FROM inv.item_units x JOIN inv.units u ON u.id = x.unit_id WHERE x.item_id = %s ORDER BY x.factor, u.code",
            [item_id])

    def same_name(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> list[dict]:
        """كل الأصناف بنفس الاسم (من غير حروف كبيرة/صغيرة) — في نفس النوع ممنوع، وفي نوع تاني تحذير."""
        return self._x(ex).fetch_all(
            "SELECT i.id, i.code, i.name, i.category_id, c.name AS category_name FROM inv.items i "
            "LEFT JOIN inv.item_categories c ON c.id = i.category_id "
            "WHERE lower(i.name) = lower(%s) AND i.id IS DISTINCT FROM %s ORDER BY i.seq", [name, exclude_id])

    # ---- الاختيارات -------------------------------------------------------------
    def category_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT c.id, c.code, c.name, c.parent_id, c.is_active, c.new_items_service FROM inv.item_categories c "
            "ORDER BY c.seq")

    def brand_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all("SELECT id, code, name, is_active FROM inv.item_brands ORDER BY lower(name)")

    def unit_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, allows_fraction, is_default, is_active FROM inv.units ORDER BY id")

    def warehouse_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, is_default, is_active FROM inv.warehouses WHERE NOT is_virtual ORDER BY seq")

    def tax_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, rate, is_active FROM acc.tax_codes WHERE kind = 'add' ORDER BY calc_order, id")

    def effective_accounts(self, category_id: int | None, ex: Executor | None = None) -> list[dict]:
        """حسابات الصنف من نوعه (حسابه ← أقرب أب ← الافتراضي) — ومن غير نوع ← الحسابات الافتراضية."""
        return self._x(ex).fetch_all(
            "SELECT k.kind, a.code, a.name_ar FROM unnest(ARRAY['inventory', 'sales', 'cogs', 'sales_ret']) "
            "WITH ORDINALITY AS k (kind, n) "
            "LEFT JOIN acc.accounts a ON a.id = CASE WHEN %s::int IS NULL "
            "THEN (SELECT account_id FROM acc.default_accounts WHERE key = k.kind) "
            "ELSE inv.item_category_account(%s::int, k.kind) END ORDER BY k.n", [category_id, category_id])

    # ---- الكتابة ---------------------------------------------------------------
    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        self.lock(ex)
        return int(ex.fetch_value(_NEXT_SEQ))

    @staticmethod
    def lock(ex: Executor) -> None:
        ex.execute(_CODE_LOCK)

    def suggested_code(self, ex: Executor | None = None) -> str:
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_item(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(ITEM_FIELDS)
        marks = ", ".join(["%s"] * len(ITEM_FIELDS))
        return int(self._x(ex).fetch_value(f"INSERT INTO inv.items ({cols}) VALUES ({marks}) RETURNING id",
                                           [data[f] for f in ITEM_FIELDS]))

    def update_item(self, item_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني). المسلسل ثابت.
        بيتنفذ حتى لو مفيش تغيير (عشان الإصدار يزيد لما الوحدات التانية بس اللي اتغيّرت)."""
        fields = [f for f in ITEM_FIELDS if f not in _FIXED]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE inv.items SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [item_id, *extra])

    def replace_units(self, item_id: int, units: list[dict[str, Any]], ex: Executor) -> None:
        ex.execute("DELETE FROM inv.item_units WHERE item_id = %s", [item_id])
        for u in units:
            ex.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, %s)",
                       [item_id, u["unit_id"], u["factor"]])

    def set_active(self, item_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE inv.items SET is_active = %s WHERE id = %s" + cond,
                                   [active, item_id, *extra])

    def delete_item(self, item_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM inv.items WHERE id = %s" + cond, [item_id, *extra])
