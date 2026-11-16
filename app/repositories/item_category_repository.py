"""SQL أنواع الأصناف «الأقسام» (``inv.item_categories`` — سكريبت 0028) — المرحلة 3د.

نفس فكرة ``ExpenseTypeRepository``: الكود ``CAT-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل
بس، جوه قفل الترقيم) · شجرة لحد 5 مستويات (``parent_id``).
القائمة بتجيب الأب وعدد اللي تحته والحسابات الأربعة (الخاصة بالنوع — الفعلية بتتحسب في الـ Service أو
``inv.item_category_account`` في القاعدة).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

CATEGORY_FIELDS = ("seq", "name", "name_ar", "parent_id", "inventory_account_id", "sales_account_id",
                   "cogs_account_id", "sales_return_account_id", "new_items_service", "notes", "is_active")
_FIXED = ("seq",)

CODE_PREFIX = "CAT-"
MAX_DEPTH = 5

# (المفتاح في الحسابات الافتراضية، عمود الحساب على النوع، الاسم، شرط الحساب)
ACCOUNT_KINDS = (
    ("inventory", "inventory_account_id", "المخزون",
     "a.account_type = 'asset' AND a.role_code = 'inventory'"),
    ("sales", "sales_account_id", "المبيعات",
     "a.account_type = 'revenue' AND a.classification = 'operating_rev'"),
    ("cogs", "cogs_account_id", "تكلفة المبيعات",
     "a.account_type = 'expense' AND a.classification = 'cogs'"),
    ("sales_ret", "sales_return_account_id", "مردودات المبيعات",
     "a.account_type = 'revenue' AND a.classification = 'returns'"),
)
KIND_COLUMN = {k: col for k, col, _l, _c in ACCOUNT_KINDS}
KIND_LABEL = {k: label for k, _col, label, _c in ACCOUNT_KINDS}
_KIND_COND = {k: cond for k, _col, _l, cond in ACCOUNT_KINDS}

_ACCOUNT_JOINS = "".join(
    f" LEFT JOIN acc.accounts a_{k} ON a_{k}.id = c.{col}" for k, col, _l, _c in ACCOUNT_KINDS)
_ACCOUNT_COLS = "".join(
    f", a_{k}.code AS {k}_code, a_{k}.name_ar AS {k}_name, a_{k}.is_active AS {k}_active"
    for k, _col, _l, _c in ACCOUNT_KINDS)

_SELECT = f"""
    SELECT c.id, c.seq, c.code, c.name, c.name_ar, c.parent_id, c.inventory_account_id, c.sales_account_id,
           c.cogs_account_id, c.sales_return_account_id, c.new_items_service, c.access_name, c.notes, c.is_active,
           c.created_at, c.updated_at, c.row_version,
           p.code AS parent_code, p.name AS parent_name,
           (SELECT count(*) FROM inv.item_categories k WHERE k.parent_id = c.id) AS children_count
           {_ACCOUNT_COLS}
    FROM inv.item_categories c
    LEFT JOIN inv.item_categories p ON p.id = c.parent_id
    {_ACCOUNT_JOINS}
"""

# قفل ترقيم الكود (جهازين بيضيفوا نوع في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('inv.item_categories.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM inv.item_categories"

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: CAT-001 … CAT-999 ← CAT-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class ItemCategoryRepository:
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
            conds.append("(c.code ILIKE %s OR c.seq::text = %s OR c.name ILIKE %s OR c.name_ar ILIKE %s "
                         "OR c.access_name ILIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like]
        if status == "active":
            conds.append("c.is_active")
        elif status == "inactive":
            conds.append("NOT c.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_categories(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                        ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status)
        sql = _SELECT + where + " ORDER BY c.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def get_category(self, category_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE c.id = %s" + (" FOR UPDATE OF c" if for_update else ""),
                                     [category_id])

    def name_taken(self, name: str, parent_id: int | None, exclude_id: int | None = None,
                   ex: Executor | None = None) -> dict | None:
        """نوع بنفس الاسم تحت نفس الأب (من غير حروف كبيرة/صغيرة — الاسم بيوصل متنضف)."""
        return self._x(ex).fetch_one(
            "SELECT id, code, name FROM inv.item_categories WHERE lower(name) = lower(%s) "
            "AND coalesce(parent_id, 0) = coalesce(%s, 0) AND id IS DISTINCT FROM %s",
            [name, parent_id, exclude_id])

    def subtree_ids(self, category_id: int, ex: Executor | None = None) -> list[int]:
        """النوع وكل اللي تحته (فلتر «الأب بيجيب الأبناء» في التقارير)."""
        return [int(r["id"]) for r in self._x(ex).fetch_all(
            "SELECT inv.item_category_subtree(%s) AS id", [category_id])]

    def depth(self, category_id: int, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value("SELECT inv.item_category_depth(%s)", [category_id]))

    def height(self, category_id: int, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value("SELECT inv.item_category_height(%s)", [category_id]))

    def active_children(self, category_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name FROM inv.item_categories WHERE parent_id = %s AND is_active ORDER BY seq",
            [category_id])

    def effective_account(self, category_id: int, kind: str, ex: Executor | None = None) -> int | None:
        """الحساب الفعلي من القاعدة (حسابه ← أقرب أب ← الافتراضي) — نفس اللي الفواتير هتستعمله."""
        value = self._x(ex).fetch_value("SELECT inv.item_category_account(%s, %s)", [category_id, kind])
        return int(value) if value is not None else None

    # ---- الحسابات ----------------------------------------------------------------
    def account_choices(self, kind: str, ex: Executor | None = None) -> list[dict]:
        """الحسابات الفرعية النشطة اللي تنفع لنوع الحساب ده (المخزون / المبيعات / التكلفة / المردودات)."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar FROM acc.accounts a "
            f"WHERE a.is_leaf AND a.is_active AND {_KIND_COND[kind]} ORDER BY a.code")

    def default_accounts(self, ex: Executor | None = None) -> dict[str, dict]:
        """الحسابات الافتراضية للأربعة: ``{kind: {account_id, code, name_ar}}``."""
        rows = self._x(ex).fetch_all(
            "SELECT d.key, d.account_id, a.code, a.name_ar FROM acc.default_accounts d "
            "LEFT JOIN acc.accounts a ON a.id = d.account_id WHERE d.key = ANY(%s)",
            [[k for k, _col, _l, _c in ACCOUNT_KINDS]])
        return {r["key"]: r for r in rows}

    # ---- الكتابة ---------------------------------------------------------------
    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_category(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(CATEGORY_FIELDS)
        marks = ", ".join(["%s"] * len(CATEGORY_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO inv.item_categories ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in CATEGORY_FIELDS]))

    def update_category(self, category_id: int, data: dict[str, Any], ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني). المسلسل ثابت."""
        fields = [f for f in CATEGORY_FIELDS if f not in _FIXED]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE inv.item_categories SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [category_id, *extra])

    def set_active(self, category_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE inv.item_categories SET is_active = %s WHERE id = %s" + cond,
                                   [active, category_id, *extra])

    def delete_category(self, category_id: int, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM inv.item_categories WHERE id = %s" + cond,
                                   [category_id, *extra])
