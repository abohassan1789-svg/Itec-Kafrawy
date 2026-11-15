"""SQL أنواع المصروفات (``exp.expense_types`` — سكريبت 0025) — المرحلة 3ج.

نفس فكرة ``TreasuryRepository``: الكود ``EXT-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل بس).
القائمة بتجيب الحساب وأبوه والنوع الأب وعدد الأنواع اللي تحته في نفس الاستعلام.
``chart_rows`` = كل حسابات المصروفات في الدليل (للشجرة «الأنواع في دليل الحسابات»).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (المسلسل والحساب بيتكتبوا مع الإضافة بس).
TYPE_FIELDS = ("seq", "name_ar", "name_en", "parent_id", "account_id", "owns_account", "notes", "is_active")
_FIXED = ("seq", "account_id", "owns_account")

CODE_PREFIX = "EXT-"
MAX_DEPTH = 5

_SELECT = """
    SELECT t.id, t.seq, t.code, t.name_ar, t.name_en, t.parent_id, t.account_id, t.owns_account, t.notes,
           t.is_active, t.created_at, t.updated_at, t.row_version,
           a.code AS account_code, a.name_ar AS account_name, a.is_active AS account_active,
           a.requires_cost_center, ap.code AS account_parent_code, ap.name_ar AS account_parent_name,
           p.code AS parent_code, p.name_ar AS parent_name,
           (SELECT count(*) FROM exp.expense_types c WHERE c.parent_id = t.id) AS children_count
    FROM exp.expense_types t
    JOIN acc.accounts a ON a.id = t.account_id
    LEFT JOIN acc.accounts ap ON ap.id = a.parent_id
    LEFT JOIN exp.expense_types p ON p.id = t.parent_id
"""

# قفل ترقيم الكود (جهازين بيضيفوا نوع في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('exp.expense_types.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM exp.expense_types"

# الحساب اللي ينفع يتربط بنوع: فرعي نشط من المصروفات من غير دور نظامي ومش تكلفة مبيعات.
USABLE_ACCOUNT = ("a.is_leaf AND a.is_active AND a.account_type = 'expense' AND a.role_code IS NULL "
                  "AND a.classification <> 'cogs'")

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: EXT-001 … EXT-999 ← EXT-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class ExpenseTypeRepository:
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
            conds.append("(t.code ILIKE %s OR t.seq::text = %s OR t.name_ar ILIKE %s OR t.name_en ILIKE %s "
                         "OR a.code LIKE %s OR a.name_ar ILIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like]
        if status == "active":
            conds.append("t.is_active")
        elif status == "inactive":
            conds.append("NOT t.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_types(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                   ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status)
        sql = _SELECT + where + " ORDER BY t.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def get_type(self, type_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE t.id = %s" + (" FOR UPDATE OF t" if for_update else ""),
                                     [type_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM exp.expense_types WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def subtree_ids(self, type_id: int, ex: Executor | None = None) -> list[int]:
        """النوع وكل اللي تحته (فلتر «الأب بيجيب الأبناء»)."""
        return [int(r["id"]) for r in self._x(ex).fetch_all(
            "SELECT exp.expense_type_subtree(%s) AS id", [type_id])]

    def depth(self, type_id: int, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value("SELECT exp.expense_type_depth(%s)", [type_id]))

    def height(self, type_id: int, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value("SELECT exp.expense_type_height(%s)", [type_id]))

    def active_children(self, type_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM exp.expense_types WHERE parent_id = %s AND is_active ORDER BY seq",
            [type_id])

    def types_on_account(self, account_id: int, exclude_id: int | None = None,
                         ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM exp.expense_types WHERE account_id = %s AND id IS DISTINCT FROM %s "
            "ORDER BY seq", [account_id, exclude_id])

    # ---- الدليل ----------------------------------------------------------------
    def chart_rows(self, ex: Executor | None = None) -> list[dict]:
        """كل حسابات المصروفات بترتيب الكود + ``usable`` (ينفع يتربط بنوع) + عدد الأنواع عليه."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.parent_id, a.level, a.is_leaf, a.is_active, a.role_code, "
            "a.classification, a.requires_cost_center, "
            f"({USABLE_ACCOUNT}) AS usable, "
            "(SELECT count(*) FROM exp.expense_types t WHERE t.account_id = a.id) AS types_count "
            "FROM acc.accounts a WHERE a.account_type = 'expense' ORDER BY a.code")

    def usable_accounts(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات اللي ينفع تتربط بنوع (لـ «أختار حساب موجود»)."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.requires_cost_center FROM acc.accounts a "
            f"WHERE {USABLE_ACCOUNT} ORDER BY a.code")

    def account_parents(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات الرئيسية النشطة في المصروفات (52 / 53 / 54…) اللي الحساب الجديد بيتعمل تحتها."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.level FROM acc.accounts a WHERE a.account_type = 'expense' "
            "AND NOT a.is_leaf AND a.is_active AND a.level >= 2 AND a.level < 5 AND a.role_code IS NULL "
            "AND a.classification <> 'cogs' ORDER BY a.code")

    # ---- الكتابة ---------------------------------------------------------------
    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_type(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(TYPE_FIELDS)
        marks = ", ".join(["%s"] * len(TYPE_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO exp.expense_types ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in TYPE_FIELDS]))

    def update_type(self, type_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني). المسلسل والحساب ثابتين."""
        fields = [f for f in TYPE_FIELDS if f not in _FIXED]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE exp.expense_types SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [type_id, *extra])

    def set_active(self, type_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE exp.expense_types SET is_active = %s WHERE id = %s" + cond,
                                   [active, type_id, *extra])

    def delete_type(self, type_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM exp.expense_types WHERE id = %s" + cond, [type_id, *extra])
