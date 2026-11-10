"""SQL العملاء (``party.customers`` — سكريبت 0015) — المرحلة 3أ.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (الكود بيتكتب مع الإضافة بس).
CUSTOMER_FIELDS = ("code", "name_ar", "name_en", "group_id", "tax_reg_no", "contact_person", "mobile", "phone",
                   "email", "address", "payment_terms_days", "credit_limit", "currency_code", "vat_tax_code_id",
                   "wht_tax_code_id", "notes", "is_active")

_SELECT = """
    SELECT c.id, c.code, c.name_ar, c.name_en, c.group_id, g.code AS group_code, g.name_ar AS group_name,
           g.is_active AS group_active, g.control_account_id, a.code AS account_code, a.name_ar AS account_name,
           c.tax_reg_no, c.contact_person, c.mobile, c.phone, c.email, c.address,
           c.payment_terms_days, c.credit_limit, c.currency_code,
           c.vat_tax_code_id, v.code AS vat_code, v.name_ar AS vat_name,
           c.wht_tax_code_id, w.code AS wht_code, w.name_ar AS wht_name,
           c.notes, c.is_active, c.created_at, c.updated_at, c.row_version
    FROM party.customers c
    JOIN party.customer_groups g ON g.id = c.group_id
    JOIN acc.accounts a ON a.id = g.control_account_id
    LEFT JOIN acc.tax_codes v ON v.id = c.vat_tax_code_id
    LEFT JOIN acc.tax_codes w ON w.id = c.wht_tax_code_id
"""

# قفل ترقيم الكود (جهازين بيضيفوا عميل في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('party.customers.code'))"

STATUSES = ("all", "active", "inactive")


class CustomerRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    @staticmethod
    def _where(keyword: str, status: str, group_id: int | None) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            conds.append("(c.code = %s OR c.name_ar ILIKE %s OR c.name_en ILIKE %s OR c.mobile ILIKE %s "
                         "OR c.phone ILIKE %s OR party.normalize_tax_no(c.tax_reg_no) LIKE "
                         "'%%' || party.normalize_tax_no(%s) || '%%')")
            params += [keyword, like, like, like, like, keyword]
        if status == "active":
            conds.append("c.is_active")
        elif status == "inactive":
            conds.append("NOT c.is_active")
        if group_id is not None:
            conds.append("c.group_id = %s")
            params.append(group_id)
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_customers(self, keyword: str = "", status: str = "all", group_id: int | None = None,
                       limit: int | None = None, offset: int = 0, ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status, group_id)
        sql = _SELECT + where + " ORDER BY c.code::bigint"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_customers(self, keyword: str = "", status: str = "all", group_id: int | None = None,
                        ex: Executor | None = None) -> dict:
        """العدد بالفلتر + النشط والموقوف (للسطر اللي تحت القائمة)."""
        where, params = self._where(keyword, status, group_id)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE c.is_active) AS active, "
            "count(*) FILTER (WHERE NOT c.is_active) AS inactive, "
            "count(*) FILTER (WHERE c.tax_reg_no IS NULL) AS no_tax "
            "FROM party.customers c" + where, params)

    def get_customer(self, customer_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE c.id = %s" + (" FOR UPDATE OF c" if for_update else ""),
                                     [customer_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM party.customers WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def tax_taken(self, tax_no: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar, tax_reg_no FROM party.customers "
            "WHERE party.normalize_tax_no(tax_reg_no) = party.normalize_tax_no(%s) AND id IS DISTINCT FROM %s",
            [tax_no, exclude_id])

    def next_code(self, ex: Executor) -> str:
        """الكود الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return str(int(ex.fetch_value("SELECT coalesce(max(code::bigint), 0) + 1 FROM party.customers")))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return str(int(self._x(ex).fetch_value("SELECT coalesce(max(code::bigint), 0) + 1 FROM party.customers")))

    def insert_customer(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(CUSTOMER_FIELDS)
        marks = ", ".join(["%s"] * len(CUSTOMER_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO party.customers ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in CUSTOMER_FIELDS]))

    def update_customer(self, customer_id: int, data: dict[str, Any], ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        fields = [f for f in CUSTOMER_FIELDS if f != "code"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE party.customers SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [customer_id, *extra])

    def set_active(self, customer_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE party.customers SET is_active = %s WHERE id = %s" + cond,
                                   [active, customer_id, *extra])

    def delete_customer(self, customer_id: int, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM party.customers WHERE id = %s" + cond, [customer_id, *extra])

    # ---- الاختيارات --------------------------------------------------------
    def group_choices(self, ex: Executor | None = None) -> list[dict]:
        """كل المجموعات (الموقوفة كمان — للفلتر) بالافتراضي بتاعها اللي بيتنسخ للعميل الجديد."""
        return self._x(ex).fetch_all(
            "SELECT g.id, g.code, g.name_ar, g.is_active, g.is_system, g.payment_terms_days, g.credit_limit, "
            "g.currency_code, g.vat_tax_code_id, g.wht_tax_code_id, a.code AS account_code, "
            "a.name_ar AS account_name FROM party.customer_groups g "
            "JOIN acc.accounts a ON a.id = g.control_account_id ORDER BY g.code::bigint")

    def currency_choices(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, is_base FROM core.currencies WHERE is_active ORDER BY is_base DESC, code")

    def tax_choices(self, kind: str, ex: Executor | None = None) -> list[dict]:
        """أكواد الضريبة النشطة اللي بتتطبق على المبيعات (kind: add = ض.ق.م · deduct = خصم المنبع)."""
        return self._x(ex).fetch_all(
            "SELECT id, code, name_ar FROM acc.tax_codes WHERE is_active AND kind = %s "
            "AND applies_to IN ('sales', 'both') ORDER BY is_exempt, calc_order, code", [kind])
