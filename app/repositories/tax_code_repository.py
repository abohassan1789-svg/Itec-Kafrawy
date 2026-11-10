"""SQL أكواد الضريبة (``acc.tax_codes``) — المرحلة 1ب.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ».
TAX_FIELDS = ("code", "name_ar", "kind", "rate", "calc_base", "calc_order", "applies_to", "is_exempt", "is_active",
              "sales_account_id", "purchase_account_id")

_SELECT = """
    SELECT t.id, t.code, t.name_ar, t.kind, t.rate, t.calc_base, t.calc_order, t.applies_to, t.is_exempt,
           t.is_active, t.sales_account_id, sa.code AS sales_account_code, sa.name_ar AS sales_account_name,
           t.purchase_account_id, pa.code AS purchase_account_code, pa.name_ar AS purchase_account_name,
           t.created_at, t.updated_at, t.row_version
    FROM acc.tax_codes t
    LEFT JOIN acc.accounts sa ON sa.id = t.sales_account_id
    LEFT JOIN acc.accounts pa ON pa.id = t.purchase_account_id
"""


class TaxCodeRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def list_codes(self, keyword: str = "", ex: Executor | None = None) -> list[dict]:
        sql = _SELECT
        params: list[Any] = []
        if keyword:
            sql += " WHERE t.code ILIKE %s OR t.name_ar ILIKE %s"
            params = [f"%{keyword}%"] * 2
        return self._x(ex).fetch_all(sql + " ORDER BY t.is_active DESC, t.kind, t.is_exempt, t.calc_order, "
                                            "CASE WHEN t.kind = 'add' THEN -t.rate ELSE t.rate END, t.code", params)

    def get_code(self, tax_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE t.id = %s" + (" FOR UPDATE OF t" if for_update else ""), [tax_id])

    def get_by_codes(self, codes: list[str], ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(_SELECT + " WHERE t.code = ANY(%s)", [list(codes)])

    def code_exists(self, code: str, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value("SELECT EXISTS (SELECT 1 FROM acc.tax_codes WHERE code = %s)", [code]))

    def insert_code(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(TAX_FIELDS)
        marks = ", ".join(["%s"] * len(TAX_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO acc.tax_codes ({cols}) VALUES ({marks}) RETURNING id", [data[f] for f in TAX_FIELDS]))

    def update_code(self, tax_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        fields = [f for f in TAX_FIELDS if f != "code"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE acc.tax_codes SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [tax_id, *extra])

    def delete_code(self, tax_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM acc.tax_codes WHERE id = %s" + cond, [tax_id, *extra])

    # ---- حسابات الضريبة (من الدليل) -------------------------------------------
    def account_choices(self, ex: Executor | None = None) -> list[dict]:
        """الحسابات اللي ينفع تتربط بكود ضريبة: فرعي نشط في الميزانية."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar, account_type, role_code FROM acc.accounts "
            "WHERE is_leaf AND is_active AND statement = 'bs' ORDER BY code")

    def role_account(self, role_code: str, ex: Executor | None = None) -> dict | None:
        """أول حساب فرعي نشط بالدور ده (ض.ق.م مخرجات / مدخلات، خصم المنبع لينا / علينا)."""
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM acc.accounts WHERE role_code = %s AND is_leaf AND is_active "
            "ORDER BY code LIMIT 1", [role_code])
