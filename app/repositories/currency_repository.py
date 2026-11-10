"""SQL العملات وأسعار الصرف (``core.currencies`` و``core.exchange_rates``) — المرحلة 1ب.

كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (is_base ثابتة من القاعدة).
CURRENCY_FIELDS = ("code", "name_ar", "name_en", "symbol", "decimals", "fraction_name", "is_active")

# كل عملة + آخر سعر ليها والسعر اللي قبله (للتغيير).
_CURRENCY_SELECT = """
    SELECT c.id, c.code, c.name_ar, c.name_en, c.symbol, c.decimals, c.fraction_name, c.is_base, c.is_active,
           c.created_at, c.updated_at, c.row_version,
           last.rate AS last_rate, last.rate_date AS last_rate_date, prev.rate AS prev_rate,
           (SELECT count(*) FROM core.exchange_rates r WHERE r.currency_id = c.id) AS rates_count
    FROM core.currencies c
    LEFT JOIN LATERAL (SELECT rate, rate_date FROM core.exchange_rates r WHERE r.currency_id = c.id
                       ORDER BY rate_date DESC LIMIT 1) last ON true
    LEFT JOIN LATERAL (SELECT rate FROM core.exchange_rates r WHERE r.currency_id = c.id
                       ORDER BY rate_date DESC OFFSET 1 LIMIT 1) prev ON true
"""


class CurrencyRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- العملات -----------------------------------------------------------
    def list_currencies(self, keyword: str = "", ex: Executor | None = None) -> list[dict]:
        sql = _CURRENCY_SELECT
        params: list[Any] = []
        if keyword:
            sql += " WHERE c.code ILIKE %s OR c.name_ar ILIKE %s OR coalesce(c.name_en, '') ILIKE %s"
            params = [f"%{keyword}%"] * 3
        return self._x(ex).fetch_all(sql + " ORDER BY c.is_base DESC, c.is_active DESC, c.code", params)

    def get_currency(self, currency_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        if for_update:
            self._x(ex).execute("SELECT id FROM core.currencies WHERE id = %s FOR UPDATE", [currency_id])
        return self._x(ex).fetch_one(_CURRENCY_SELECT + " WHERE c.id = %s", [currency_id])

    def code_exists(self, code: str, exclude_id: int | None = None, ex: Executor | None = None) -> bool:
        return bool(self._x(ex).fetch_value(
            "SELECT EXISTS (SELECT 1 FROM core.currencies WHERE code = %s AND (%s::int IS NULL OR id <> %s::int))",
            [code, exclude_id, exclude_id]))

    def base_currency(self, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT id, code, name_ar, decimals FROM core.currencies WHERE is_base")

    def insert_currency(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = [c for c in CURRENCY_FIELDS if c in data]
        return int(self._x(ex).fetch_value(
            f"INSERT INTO core.currencies ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            [data[c] for c in cols]))

    def update_currency(self, currency_id: int, data: dict[str, Any], ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        cols = [c for c in CURRENCY_FIELDS if c in data and c != "code"]
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(
            f"UPDATE core.currencies SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s{cond}",
            [*(data[c] for c in cols), currency_id, *extra])

    def delete_currency(self, currency_id: int, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM core.currencies WHERE id = %s" + cond, [currency_id, *extra])

    # ---- أسعار الصرف -------------------------------------------------------
    def list_rates(self, currency_id: int, limit: int = 50, ex: Executor | None = None) -> list[dict]:
        """أسعار العملة (الأحدث فوق) + السعر اللي قبل كل واحد (للتغيير)."""
        return self._x(ex).fetch_all(
            """SELECT r.id, r.rate_date, r.rate, r.row_version, u.username AS created_by_name,
                      lead(r.rate) OVER (ORDER BY r.rate_date DESC) AS prev_rate
               FROM core.exchange_rates r
               LEFT JOIN sec.app_users u ON u.id = r.created_by
               WHERE r.currency_id = %s
               ORDER BY r.rate_date DESC LIMIT %s""", [currency_id, limit])

    def get_rate(self, rate_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, currency_id, rate_date, rate, row_version FROM core.exchange_rates WHERE id = %s"
            + (" FOR UPDATE" if for_update else ""), [rate_id])

    def rate_on_day(self, currency_id: int, rate_date: date, ex: Executor | None = None) -> dict | None:
        """السعر المتسجل في اليوم ده بالظبط (لو فيه)."""
        return self._x(ex).fetch_one(
            "SELECT id, currency_id, rate_date, rate, row_version FROM core.exchange_rates "
            "WHERE currency_id = %s AND rate_date = %s", [currency_id, rate_date])

    def rate_before(self, currency_id: int, rate_date: date, inclusive: bool = True,
                    ex: Executor | None = None) -> dict | None:
        """آخر سعر في اليوم ده أو قبله (``inclusive=False`` = قبله بس)."""
        op = "<=" if inclusive else "<"
        return self._x(ex).fetch_one(
            f"SELECT id, rate_date, rate FROM core.exchange_rates WHERE currency_id = %s AND rate_date {op} %s "
            "ORDER BY rate_date DESC LIMIT 1", [currency_id, rate_date])

    def rate_on(self, code: str, on_date: date, ex: Executor | None = None) -> Decimal | None:
        return self._x(ex).fetch_value("SELECT core.rate_on(%s, %s)", [code, on_date])

    def upsert_rate(self, currency_id: int, rate_date: date, rate: Decimal, user_id: int | None,
                    ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value(
            """INSERT INTO core.exchange_rates (currency_id, rate_date, rate, created_by) VALUES (%s, %s, %s, %s)
               ON CONFLICT (currency_id, rate_date) DO UPDATE SET rate = EXCLUDED.rate, created_by = EXCLUDED.created_by
               RETURNING id""", [currency_id, rate_date, rate, user_id]))

    def delete_rate(self, rate_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM core.exchange_rates WHERE id = %s" + cond, [rate_id, *extra])
