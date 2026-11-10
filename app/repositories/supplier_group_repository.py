"""SQL مجموعات الموردين (``party.supplier_groups`` — سكريبت 0017) — المرحلة 3ب.

نفس فكرة ``CustomerGroupRepository`` (مجموعات العملاء) من غير حد ائتمان، وحساب الرقابة من «الموردين» والضرايب
اللي بتتطبق على المشتريات. كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في
transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (الكود بيتكتب مع الإضافة بس).
GROUP_FIELDS = ("code", "name_ar", "name_en", "control_account_id", "payment_terms_days",
                "currency_code", "vat_tax_code_id", "wht_tax_code_id", "notes", "is_active")

_SELECT = """
    SELECT g.id, g.code, g.name_ar, g.name_en, g.control_account_id, a.code AS account_code,
           a.name_ar AS account_name, g.payment_terms_days, g.currency_code,
           g.vat_tax_code_id, v.code AS vat_code, v.name_ar AS vat_name,
           g.wht_tax_code_id, w.code AS wht_code, w.name_ar AS wht_name,
           g.notes, g.is_system, g.is_active, g.created_at, g.updated_at, g.row_version
    FROM party.supplier_groups g
    JOIN acc.accounts a ON a.id = g.control_account_id
    LEFT JOIN acc.tax_codes v ON v.id = g.vat_tax_code_id
    LEFT JOIN acc.tax_codes w ON w.id = g.wht_tax_code_id
"""

# قفل ترقيم الكود (جهازين بيضيفوا مجموعة في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('party.supplier_groups.code'))"


class SupplierGroupRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def list_groups(self, keyword: str = "", ex: Executor | None = None) -> list[dict]:
        sql = _SELECT
        params: list[Any] = []
        if keyword:
            sql += (" WHERE g.code ILIKE %s OR g.name_ar ILIKE %s OR g.name_en ILIKE %s OR a.code ILIKE %s"
                    " OR w.code ILIKE %s")
            params = [f"%{keyword}%"] * 5
        return self._x(ex).fetch_all(sql + " ORDER BY g.is_active DESC, g.code::bigint", params)

    def get_group(self, group_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE g.id = %s" + (" FOR UPDATE OF g" if for_update else ""),
                                     [group_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM party.supplier_groups WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def next_code(self, ex: Executor) -> str:
        """الكود الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return str(int(ex.fetch_value(
            "SELECT coalesce(max(code::bigint), 0) + 1 FROM party.supplier_groups")))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return str(int(self._x(ex).fetch_value("SELECT coalesce(max(code::bigint), 0) + 1 FROM party.supplier_groups")))

    def insert_group(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(GROUP_FIELDS)
        marks = ", ".join(["%s"] * len(GROUP_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO party.supplier_groups ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in GROUP_FIELDS]))

    def update_group(self, group_id: int, data: dict[str, Any], ex: Executor | None = None,
                     expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        fields = [f for f in GROUP_FIELDS if f != "code"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE party.supplier_groups SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [group_id, *extra])

    def delete_group(self, group_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM party.supplier_groups WHERE id = %s" + cond, [group_id, *extra])

    def supplier_count(self, group_id: int, ex: Executor | None = None) -> int:
        """عدد موردين المجموعة — جدول ``party.suppliers`` بيتعمل مع شاشة الموردين (لحد كده 0)."""
        exists = self._x(ex).fetch_value("SELECT to_regclass('party.suppliers') IS NOT NULL")
        if not exists:
            return 0
        return int(self._x(ex).fetch_value("SELECT count(*) FROM party.suppliers WHERE group_id = %s", [group_id]))

    # ---- الاختيارات --------------------------------------------------------
    def account_choices(self, ex: Executor | None = None) -> list[dict]:
        """حسابات الرقابة المسموحة: فرعي نشط من الخصوم بدور «الموردين» (2101 أو فرعي منه بعد التفريع)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM acc.accounts WHERE is_leaf AND is_active AND account_type = 'liability' "
            "AND role_code = 'suppliers' ORDER BY code")

    def default_account_id(self, ex: Executor | None = None) -> int | None:
        """حساب «الموردين» من الحسابات الافتراضية (0012) — ولو مش متحدد أول حساب بالدور."""
        value = self._x(ex).fetch_value(
            "SELECT coalesce("
            " (SELECT d.account_id FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id"
            "   WHERE d.key = 'suppliers' AND a.is_leaf AND a.is_active AND a.role_code = 'suppliers'),"
            " (SELECT id FROM acc.accounts WHERE role_code = 'suppliers' AND is_leaf AND is_active"
            "   ORDER BY code LIMIT 1))")
        return int(value) if value is not None else None

    def currency_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, is_base FROM core.currencies WHERE is_active ORDER BY is_base DESC, code")

    def base_currency(self, ex: Executor | None = None) -> str:
        return str(self._x(ex).fetch_value("SELECT code FROM core.currencies WHERE is_base LIMIT 1") or "EGP")

    def tax_choices(self, kind: str, ex: Executor | None = None) -> list[dict]:
        """أكواد الضريبة النشطة اللي بتتطبق على المشتريات (kind: add = ض.ق.م · deduct = خصم المنبع)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM acc.tax_codes WHERE is_active AND kind = %s "
            "AND applies_to IN ('purch', 'both') ORDER BY is_exempt, calc_order, code", [kind])
