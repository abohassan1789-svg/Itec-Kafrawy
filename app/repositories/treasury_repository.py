"""SQL الخزائن (``cash.treasuries`` — سكريبت 0023) — المرحلة 3ج.

نفس فكرة ``PartnerRepository``: الكود ``TRS-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل بس).
القائمة بتجيب الحساب والأمين و«الافتراضية» (الخزنة اللي حسابها هو «الخزينة الافتراضية» cash_box) في نفس الاستعلام.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (المسلسل والحساب بيتكتبوا مع الإضافة بس).
TREASURY_FIELDS = ("seq", "name_ar", "name_en", "currency_code", "account_id", "owns_account", "treasurer_id",
                   "location", "max_balance", "notes", "is_active")
_FIXED = ("seq", "account_id", "owns_account")

CODE_PREFIX = "TRS-"
CASH_ROLE = "cash"

_SELECT = """
    SELECT t.id, t.seq, t.code, t.name_ar, t.name_en, t.currency_code, t.account_id, t.owns_account,
           t.treasurer_id, t.location, t.max_balance, t.notes, t.is_active, t.created_at, t.updated_at,
           t.row_version,
           a.code AS account_code, a.name_ar AS account_name, p.code AS parent_code, p.name_ar AS parent_name,
           e.code AS treasurer_code, e.name_ar AS treasurer_name,
           (d.key IS NOT NULL) AS is_default
    FROM cash.treasuries t
    JOIN acc.accounts a ON a.id = t.account_id
    LEFT JOIN acc.accounts p ON p.id = a.parent_id
    LEFT JOIN party.employees e ON e.id = t.treasurer_id
    LEFT JOIN acc.default_accounts d ON d.key = 'cash_box' AND d.account_id = t.account_id
"""

# قفل ترقيم الكود (جهازين بيضيفوا خزنة في نفس اللحظة مياخدوش نفس الرقم ولا نفس كود الحساب).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('cash.treasuries.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM cash.treasuries"

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: TRS-001 … TRS-999 ← TRS-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class TreasuryRepository:
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
                         "OR a.code LIKE %s OR e.name_ar ILIKE %s OR t.location ILIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like, like]
        if status == "active":
            conds.append("t.is_active")
        elif status == "inactive":
            conds.append("NOT t.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    # ---- القراية ---------------------------------------------------------------
    def list_treasuries(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                        ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status)
        sql = _SELECT + where + " ORDER BY t.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_treasuries(self, keyword: str = "", status: str = "all", ex: Executor | None = None) -> dict:
        """العدد بالفلتر + النشطة والموقوفة (للسطر اللي تحت القائمة)."""
        where, params = self._where(keyword, status)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE t.is_active) AS active, "
            "count(*) FILTER (WHERE NOT t.is_active) AS inactive FROM cash.treasuries t "
            "JOIN acc.accounts a ON a.id = t.account_id LEFT JOIN party.employees e ON e.id = t.treasurer_id"
            + where, params)

    def get_treasury(self, treasury_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE t.id = %s" + (" FOR UPDATE OF t" if for_update else ""),
                                     [treasury_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM cash.treasuries WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def treasury_of_account(self, account_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT id, code, name_ar FROM cash.treasuries WHERE account_id = %s",
                                     [account_id])

    # ---- الاختيارات -------------------------------------------------------------
    def list_currencies(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, is_base, is_active FROM core.currencies ORDER BY is_base DESC, code")

    def list_treasurers(self, ex: Executor | None = None) -> list[dict]:
        """الموظفين النشطين اللي عليهم دور «أمين خزينة»."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM party.employees WHERE is_treasurer AND is_active ORDER BY seq")

    def get_employee(self, employee_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar, is_treasurer, is_active FROM party.employees WHERE id = %s", [employee_id])

    def free_cash_accounts(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات الفرعية النشطة بدور «خزينة» ومش مربوطة بخزنة (لـ «أختار حساب موجود»)."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.name_en, a.currency_code FROM acc.accounts a "
            "WHERE a.is_leaf AND a.is_active AND a.role_code = %s "
            "AND NOT EXISTS (SELECT 1 FROM cash.treasuries t WHERE t.account_id = a.id) ORDER BY a.code",
            [CASH_ROLE])

    def cash_parent(self, ex: Executor, for_update: bool = False) -> dict | None:
        """الحساب الرئيسي اللي الخزائن الجديدة بتتعمل تحته = أعلى حساب رئيسي نشط بدور «خزينة» (1101)."""
        return ex.fetch_one(
            "SELECT id FROM acc.accounts WHERE role_code = %s AND NOT is_leaf AND is_active "
            "ORDER BY level, code LIMIT 1" + (" FOR UPDATE" if for_update else ""), [CASH_ROLE])

    # ---- الكتابة ---------------------------------------------------------------
    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_treasury(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(TREASURY_FIELDS)
        marks = ", ".join(["%s"] * len(TREASURY_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO cash.treasuries ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in TREASURY_FIELDS]))

    def update_treasury(self, treasury_id: int, data: dict[str, Any], ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني). المسلسل والحساب ثابتين."""
        fields = [f for f in TREASURY_FIELDS if f not in _FIXED]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE cash.treasuries SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [treasury_id, *extra])

    def set_active(self, treasury_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE cash.treasuries SET is_active = %s WHERE id = %s" + cond,
                                   [active, treasury_id, *extra])

    def delete_treasury(self, treasury_id: int, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM cash.treasuries WHERE id = %s" + cond, [treasury_id, *extra])
