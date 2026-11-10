"""SQL الحسابات الافتراضية (``acc.default_accounts`` — سكريبت 0012) — المرحلة 2.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

_SELECT = """
    SELECT d.key, d.key AS id, d.group_code, d.name_ar, d.account_type, d.role_code, r.name_ar AS role_name,
           d.used_in, d.sort_order, d.account_id, a.code AS account_code, a.name_ar AS account_name,
           a.is_active AS account_active, a.is_leaf AS account_leaf, a.nature AS account_nature,
           d.updated_at, d.row_version
    FROM acc.default_accounts d
    LEFT JOIN acc.accounts a ON a.id = d.account_id
    LEFT JOIN acc.account_roles r ON r.code = d.role_code
"""


class DefaultAccountRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def list_keys(self, group_code: str | None = None, keyword: str = "", ex: Executor | None = None) -> list[dict]:
        conds: list[str] = []
        params: list[Any] = []
        if group_code:
            conds.append("d.group_code = %s")
            params.append(group_code)
        if keyword:
            conds.append("(d.name_ar ILIKE %s OR d.used_in ILIKE %s OR a.code ILIKE %s OR a.name_ar ILIKE %s)")
            params += [f"%{keyword}%"] * 4
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        return self._x(ex).fetch_all(_SELECT + where + " ORDER BY d.sort_order", params)

    def get_key(self, key: str, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE d.key = %s" + (" FOR UPDATE OF d" if for_update else ""),
                                     [key])

    def group_counts(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT group_code, count(*) AS total, count(*) FILTER (WHERE account_id IS NULL) AS missing "
            "FROM acc.default_accounts GROUP BY group_code")

    def update_account(self, key: str, account_id: int | None, ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE acc.default_accounts SET account_id = %s WHERE key = %s{cond}",
                                   [account_id, key, *extra])

    def account_choices(self, account_type: str, ex: Executor | None = None) -> list[dict]:
        """الحسابات اللي ينفع تبقى افتراضية لمفتاح من النوع ده: فرعي نشط."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, role_code FROM acc.accounts "
            "WHERE is_leaf AND is_active AND account_type = %s ORDER BY code", [account_type])

    def role_account(self, role_code: str, account_type: str, ex: Executor | None = None) -> dict | None:
        """أول حساب فرعي نشط بالدور ده (الاقتراح)."""
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM acc.accounts WHERE role_code = %s AND account_type = %s "
            "AND is_leaf AND is_active ORDER BY code LIMIT 1", [role_code, account_type])

    def keys_of_account(self, account_id: int, ex: Executor | None = None) -> list[dict]:
        """المفاتيح اللي الحساب ده افتراضي ليها (الصف كله — للرسايل والـ audit)."""
        return self._x(ex).fetch_all(_SELECT + " WHERE d.account_id = %s ORDER BY d.sort_order", [account_id])

    def get_keys(self, keys: list[str], ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(_SELECT + " WHERE d.key = ANY(%s) ORDER BY d.sort_order", [list(keys)])
