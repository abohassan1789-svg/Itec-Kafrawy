"""SQL الإعدادات العامة (schema ``core``) — المرحلة 1ب.

دلوقتي: بيانات الشركة (``core.company_settings`` — سجل واحد id = 1).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

COMPANY_ID = 1

# الأعمدة اللي البرنامج بيكتبها (من غير id والتواريخ).
COMPANY_FIELDS = (
    "name_ar", "name_en", "activity", "address", "city", "phone", "mobile", "email", "website",
    "commercial_register", "tax_registration_no", "tax_office", "logo", "logo_mime", "base_currency_code",
    "default_vat_rate", "fiscal_year_start_month", "invoice_footer", "print_logo", "print_name_en",
)
# القراية العادية من غير بايتات اللوجو (اللوجو بيتجاب لوحده).
_COMPANY_SELECT = ", ".join(
    ["id", *[c for c in COMPANY_FIELDS if c != "logo"], "(logo IS NOT NULL) AS has_logo", "created_at", "updated_at", "row_version"])


class SettingsRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- بيانات الشركة ----------------------------------------------------
    def get_company(self, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        sql = f"SELECT {_COMPANY_SELECT} FROM core.company_settings WHERE id = %s"
        if for_update:
            sql += " FOR UPDATE"
        return self._x(ex).fetch_one(sql, [COMPANY_ID])

    def get_company_logo(self, ex: Executor | None = None) -> tuple[bytes | None, str | None]:
        row = self._x(ex).fetch_one("SELECT logo, logo_mime FROM core.company_settings WHERE id = %s", [COMPANY_ID])
        if row is None or row["logo"] is None:
            return None, None
        return bytes(row["logo"]), row["logo_mime"]

    def save_company(self, data: dict[str, Any], ex: Executor | None = None,
                     expected_version: int | None = None) -> int:
        """إضافة أول مرة أو تعديل (upsert على id = 1). بيرجّع عدد الصفوف (0 = جهاز تاني سبقك).

        ``expected_version``: None = من غير تحقق · 0 = الشاشة شافت إن مفيش بيانات لسه · N = الإصدار.
        """
        cols = [c for c in COMPANY_FIELDS if c in data]
        values = [data[c] for c in cols]
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        cond, extra = version_clause(expected_version, "company_settings.row_version")
        where = f" WHERE true{cond}" if cond else ""
        return self._x(ex).execute(
            f"INSERT INTO core.company_settings (id, {', '.join(cols)}) "
            f"VALUES (%s, {', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (id) DO UPDATE SET {updates}, updated_at = now(){where}",
            [COMPANY_ID, *values, *extra],
        )
