"""SQL شجرة الحسابات (``acc.accounts`` + ``acc.account_roles``) — المرحلة 2.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
المستوى والنوع والقائمة بيتحسبوا في القاعدة (trigger) — البرنامج مابيكتبهمش في التعديل.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (account_type بيتكتب للجذر بس — للباقي بيتورث في الـ trigger).
ACCOUNT_FIELDS = ("code", "name_ar", "name_en", "parent_id", "is_leaf", "nature", "classification", "cash_flow",
                  "role_code", "party_kind", "requires_cost_center", "currency_code", "is_active")

_SELECT = """
    SELECT a.id, a.code, a.name_ar, a.name_en, a.parent_id, p.code AS parent_code, p.name_ar AS parent_name,
           a.level, a.is_leaf, a.account_type, a.nature, a.statement, a.classification, a.cash_flow,
           a.role_code, r.name_ar AS role_name, a.party_kind, a.requires_cost_center, a.currency_code,
           a.is_active, a.is_system, a.created_at, a.updated_at, a.row_version,
           EXISTS (SELECT 1 FROM acc.accounts c WHERE c.parent_id = a.id) AS has_children
    FROM acc.accounts a
    LEFT JOIN acc.accounts p ON p.id = a.parent_id
    LEFT JOIN acc.account_roles r ON r.code = a.role_code
"""


class AccountRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- الحسابات ------------------------------------------------------------
    def list_accounts(self, ex: Executor | None = None) -> list[dict]:
        """الدليل كله بترتيب الكود (الشجرة بتتبني منه في الشاشة)."""
        return self._x(ex).fetch_all(_SELECT + " ORDER BY a.code")

    def get_account(self, account_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        sql = _SELECT + " WHERE a.id = %s"
        if for_update:
            sql += " FOR UPDATE OF a"
        return self._x(ex).fetch_one(sql, [account_id])

    def code_exists(self, code: str, exclude_id: int | None = None, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM acc.accounts WHERE code = %s AND id IS DISTINCT FROM %s)",
            [code, exclude_id]))

    def child_codes(self, parent_id: int, ex: Executor | None = None) -> list[str]:
        return [r["code"] for r in self._x(ex).fetch_all(
            "SELECT code FROM acc.accounts WHERE parent_id = %s ORDER BY code", [parent_id])]

    def has_children(self, account_id: int, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM acc.accounts WHERE parent_id = %s)", [account_id]))

    def insert_account(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        fields = (*ACCOUNT_FIELDS, "account_type")
        cols = ", ".join(fields)
        marks = ", ".join(["%s"] * len(fields))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO acc.accounts ({cols}) VALUES ({marks}) RETURNING id", [data.get(f) for f in fields]))

    def update_account(self, account_id: int, data: dict[str, Any], ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        sets = ", ".join(f"{f} = %s" for f in ACCOUNT_FIELDS)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE acc.accounts SET {sets} WHERE id = %s{cond}",
                                   [data.get(f) for f in ACCOUNT_FIELDS] + [account_id, *extra])

    def delete_account(self, account_id: int, ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM acc.accounts WHERE id = %s" + cond, [account_id, *extra])

    def codes_with_prefix(self, prefix: str, length: int, ex: Executor | None = None) -> list[str]:
        """الأكواد اللي بتبدأ بـ ``prefix`` وطولها ``length`` (في الدليل كله — مش تحت الأب بس)."""
        return [r["code"] for r in self._x(ex).fetch_all(
            "SELECT code FROM acc.accounts WHERE left(code, %s) = %s AND length(code) = %s ORDER BY code",
            [len(prefix), prefix, length])]

    def split_account(self, account_id: int, general_code: str | None, ex: Executor | None = None) -> int | None:
        """تفريع حساب فرعي (سكريبت 0011): بيتحوّل رئيسي + «— عام» تحته. بيرجّع رقم «العام»."""
        value = self._x(ex).fetch_value("SELECT acc.split_account(%s, %s)", [account_id, general_code])
        return None if value is None else int(value)

    def tax_codes_of(self, account_id: int, ex: Executor | None = None) -> list[dict]:
        """أكواد الضريبة المربوطة بالحساب (الصف كله — للـ audit)."""
        return self._x(ex).fetch_all(
            "SELECT * FROM acc.tax_codes WHERE %s IN (sales_account_id, purchase_account_id) ORDER BY code",
            [account_id])

    def tax_codes_by_ids(self, ids: list[int], ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all("SELECT * FROM acc.tax_codes WHERE id = ANY(%s) ORDER BY code", [list(ids)])

    def tax_links(self, account_id: int, ex: Executor | None = None) -> list[str]:
        """أكواد الضريبة المربوطة بالحساب ده (سكريبت 0010)."""
        return [r["code"] for r in self._x(ex).fetch_all(
            "SELECT code FROM acc.tax_codes WHERE %s IN (sales_account_id, purchase_account_id) ORDER BY code",
            [account_id])]

    def default_keys(self, account_id: int, ex: Executor | None = None) -> list[str]:
        """أسماء المفاتيح اللي الحساب ده افتراضي ليها (سكريبت 0012)."""
        return [r["name_ar"] for r in self._x(ex).fetch_all(
            "SELECT name_ar FROM acc.default_accounts WHERE account_id = %s ORDER BY sort_order", [account_id])]

    def default_rows_of(self, account_id: int, ex: Executor | None = None) -> list[dict]:
        """صفوف الحسابات الافتراضية للحساب ده (الصف كله — للـ audit)."""
        return self._x(ex).fetch_all(
            "SELECT * FROM acc.default_accounts WHERE account_id = %s ORDER BY sort_order", [account_id])

    def default_rows_by_keys(self, keys: list[str], ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT * FROM acc.default_accounts WHERE key = ANY(%s) ORDER BY sort_order", [list(keys)])

    # ---- الأدوار والعملات (للاختيارات) ---------------------------------------
    def list_roles(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, name_en, account_type, default_party_kind, sort_order "
            "FROM acc.account_roles ORDER BY sort_order")

    def list_currencies(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, is_base, is_active FROM core.currencies ORDER BY is_base DESC, code")
