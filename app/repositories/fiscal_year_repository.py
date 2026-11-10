"""SQL السنين المالية والفترات (``core.fiscal_years`` و``core.fiscal_periods``) — المرحلة 1ب.

الفترات بتتولّد لوحدها في القاعدة (trigger) من «من/إلى» — الـ Repository بيقراها بس.
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ».
YEAR_FIELDS = ("year_code", "name_ar", "start_date", "end_date", "notes")

_YEAR_SELECT = """
    SELECT y.id, y.year_code, y.name_ar, y.start_date, y.end_date, y.status, y.closed_at, y.closed_by,
           u.username AS closed_by_name, y.notes, y.created_at, y.updated_at, y.row_version
    FROM core.fiscal_years y
    LEFT JOIN sec.app_users u ON u.id = y.closed_by
"""


class FiscalYearRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- قراية -------------------------------------------------------------
    def list_years(self, keyword: str = "", ex: Executor | None = None) -> list[dict]:
        sql = _YEAR_SELECT
        params: list[Any] = []
        if keyword:
            sql += " WHERE y.year_code ILIKE %s OR coalesce(y.name_ar, '') ILIKE %s"
            params = [f"%{keyword}%", f"%{keyword}%"]
        return self._x(ex).fetch_all(sql + " ORDER BY y.start_date DESC", params)

    def get_year(self, year_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        if for_update:   # FOR UPDATE مابيتعملش مع LEFT JOIN على الجانب اللي ممكن يبقى NULL
            self._x(ex).execute("SELECT id FROM core.fiscal_years WHERE id = %s FOR UPDATE", [year_id])
        return self._x(ex).fetch_one(_YEAR_SELECT + " WHERE y.id = %s", [year_id])

    def year_spans(self, ex: Executor | None = None) -> list[dict]:
        """كل السنين (id، الكود، من، إلى) بالترتيب — للتحقق من التداخل والفجوات."""
        return self._x(ex).fetch_all(
            "SELECT id, year_code, start_date, end_date FROM core.fiscal_years ORDER BY start_date")

    def code_exists(self, year_code: str, exclude_id: int | None = None, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM core.fiscal_years WHERE lower(year_code) = lower(%s) "
            "AND (%s::int IS NULL OR id <> %s::int))", [year_code, exclude_id, exclude_id]))

    def list_periods(self, year_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, period_no, name_ar, start_date, end_date FROM core.fiscal_periods "
            "WHERE fiscal_year_id = %s ORDER BY period_no", [year_id])

    def has_document_numbers(self, year_id: int, ex: Executor | None = None) -> bool:
        """اتصرف في السنة دي أرقام مستندات؟ (عدّاد ترقيم واحد على الأقل)."""
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM core.document_sequences WHERE fiscal_year_id = %s)", [year_id]))

    def lock_table(self, ex: Executor) -> None:
        """قفل الجدول للكتابة جوه الـ Tx (عشان جهازين مايضيفوش سنين متداخلة في نفس اللحظة)."""
        ex.execute("LOCK TABLE core.fiscal_years IN SHARE ROW EXCLUSIVE MODE")

    # ---- كتابة -------------------------------------------------------------
    def insert_year(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = [c for c in YEAR_FIELDS if c in data]
        return int(self._x(ex).fetch_value(
            f"INSERT INTO core.fiscal_years ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            "RETURNING id", [data[c] for c in cols]))

    def update_year(self, year_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        cols = [c for c in YEAR_FIELDS if c in data]
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(
            f"UPDATE core.fiscal_years SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s{cond}",
            [*(data[c] for c in cols), year_id, *extra])

    def delete_year(self, year_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM core.fiscal_years WHERE id = %s" + cond, [year_id, *extra])

    def set_status(self, year_id: int, closed: bool, user_id: int | None, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        if closed:
            return self._x(ex).execute(
                "UPDATE core.fiscal_years SET status = 'closed', closed_at = now(), closed_by = %s WHERE id = %s"
                + cond, [user_id, year_id, *extra])
        return self._x(ex).execute(
            "UPDATE core.fiscal_years SET status = 'open', closed_at = NULL, closed_by = NULL WHERE id = %s" + cond,
            [year_id, *extra])
