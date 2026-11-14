"""SQL الشركاء (``party.partners`` — سكريبت 0022) — المرحلة 3ب.

نفس فكرة ``EmployeeRepository``: الكود ``PRT-001`` محسوب في القاعدة من المسلسل ``seq`` (البرنامج بيكتب المسلسل بس).
``share_totals`` = مجموع نسب الشركاء النشطين (للتنبيه لو مش 100%). كل دالة بتاخد ``ex`` اختياري (``Tx`` من
``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# الأعمدة اللي البرنامج بيكتبها في «حفظ» (المسلسل بيتكتب مع الإضافة بس — والكود بيتحسب منه في القاعدة).
PARTNER_FIELDS = ("seq", "name_ar", "name_en", "partner_type", "mobile", "phone", "email", "address", "national_id",
                  "join_date", "profit_percent", "loss_percent", "subscribed_capital", "notes", "is_active")

CODE_PREFIX = "PRT-"
PARTNER_TYPES = ("general", "limited")

_SELECT = """
    SELECT p.id, p.seq, p.code, p.name_ar, p.name_en, p.partner_type, p.mobile, p.phone, p.email, p.address,
           p.national_id, p.join_date, p.profit_percent, p.loss_percent, p.subscribed_capital, p.notes,
           p.is_active, p.created_at, p.updated_at, p.row_version
    FROM party.partners p
"""

# قفل ترقيم الكود (جهازين بيضيفوا شريك في نفس اللحظة مياخدوش نفس الرقم).
_CODE_LOCK = "SELECT pg_advisory_xact_lock(hashtext('party.partners.code'))"
_NEXT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM party.partners"

STATUSES = ("all", "active", "inactive")


def format_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: PRT-001 … PRT-999 ← PRT-1000."""
    seq = int(seq)
    return f"{CODE_PREFIX}{seq:03d}" if seq < 1000 else f"{CODE_PREFIX}{seq}"


class PartnerRepository:
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
            conds.append("(p.code ILIKE %s OR p.seq::text = %s OR p.name_ar ILIKE %s OR p.name_en ILIKE %s "
                         "OR p.mobile ILIKE %s OR p.phone ILIKE %s OR p.national_id LIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like, like]
        if status == "active":
            conds.append("p.is_active")
        elif status == "inactive":
            conds.append("NOT p.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_partners(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                      ex: Executor | None = None) -> list[dict]:
        where, params = self._where(keyword, status)
        sql = _SELECT + where + " ORDER BY p.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def count_partners(self, keyword: str = "", status: str = "all", ex: Executor | None = None) -> dict:
        """العدد بالفلتر + النشط واللي خرجوا (للسطر اللي تحت القائمة)."""
        where, params = self._where(keyword, status)
        return self._x(ex).fetch_one(
            "SELECT count(*) AS total, count(*) FILTER (WHERE p.is_active) AS active, "
            "count(*) FILTER (WHERE NOT p.is_active) AS inactive FROM party.partners p" + where, params)

    def share_totals(self, exclude_id: int | None = None, ex: Executor | None = None) -> dict:
        """مجموع نسب الربح والخسارة ورأس المال المكتتب للشركاء النشطين (من غير ``exclude_id`` لو اتبعت)."""
        return self._x(ex).fetch_one(
            "SELECT coalesce(sum(profit_percent), 0) AS profit, coalesce(sum(loss_percent), 0) AS loss, "
            "coalesce(sum(subscribed_capital), 0) AS capital, count(*) AS partners "
            "FROM party.partners WHERE is_active AND id IS DISTINCT FROM %s", [exclude_id])

    def get_partner(self, partner_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_SELECT + " WHERE p.id = %s" + (" FOR UPDATE OF p" if for_update else ""),
                                     [partner_id])

    def name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM party.partners WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def national_id_taken(self, national_id: str, exclude_id: int | None = None,
                          ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM party.partners WHERE national_id = %s AND id IS DISTINCT FROM %s",
            [national_id, exclude_id])

    def next_seq(self, ex: Executor) -> int:
        """المسلسل الجاي (جوه قفل الترقيم — لازم ``ex`` يبقى الـ Tx اللي هيعمل الإضافة)."""
        ex.execute(_CODE_LOCK)
        return int(ex.fetch_value(_NEXT_SEQ))

    def suggested_code(self, ex: Executor | None = None) -> str:
        """للعرض بس في وضع «جديد» (الرقم الفعلي بيتحسب وقت الحفظ جوه القفل)."""
        return format_code(int(self._x(ex).fetch_value(_NEXT_SEQ)))

    def insert_partner(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(PARTNER_FIELDS)
        marks = ", ".join(["%s"] * len(PARTNER_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO party.partners ({cols}) VALUES ({marks}) RETURNING id",
            [data[f] for f in PARTNER_FIELDS]))

    def update_partner(self, partner_id: int, data: dict[str, Any], ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        fields = [f for f in PARTNER_FIELDS if f != "seq"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE party.partners SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [partner_id, *extra])

    def set_active(self, partner_id: int, active: bool, ex: Executor | None = None,
                   expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE party.partners SET is_active = %s WHERE id = %s" + cond,
                                   [active, partner_id, *extra])

    def delete_partner(self, partner_id: int, ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM party.partners WHERE id = %s" + cond, [partner_id, *extra])
