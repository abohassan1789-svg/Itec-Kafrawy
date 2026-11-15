"""SQL البنوك والحسابات البنكية (``cash.banks`` + ``cash.bank_accounts`` — سكريبت 0024) — المرحلة 3ج.

* البنك: الكود ``BNK-001`` محسوب في القاعدة من ``seq``.
* الحساب البنكي: الكود ``BNK-001-01`` بيتحسب في الـ trigger من كود البنك والمسلسل جوه البنك (البرنامج بيكتب المسلسل بس).
* «شجرة البنوك» (النموذج 7): كل الحسابات اللي بدور «بنك» في الدليل وجنب كل واحد الحساب البنكي المربوط بيه (لو فيه).
كل دالة بتاخد ``ex`` اختياري (``Tx`` من ``db.atomic()``) عشان التعديل وسجله يبقوا في transaction واحدة.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

BANK_FIELDS = ("seq", "name_ar", "name_en", "short_name", "branch", "address", "swift_code", "contact_name",
               "contact_phone", "notes", "is_active")
ACCOUNT_FIELDS = ("bank_id", "seq", "account_type", "currency_code", "account_number", "iban", "overdraft_limit",
                  "maturity_date", "account_id", "owns_account", "notes", "is_active")
_ACCOUNT_FIXED = ("bank_id", "seq", "account_id", "owns_account")

BANK_PREFIX = "BNK-"
BANK_ROLE = "bank"

_BANK_SELECT = """
    SELECT b.id, b.seq, b.code, b.name_ar, b.name_en, b.short_name, b.branch, b.address, b.swift_code,
           b.contact_name, b.contact_phone, b.notes, b.is_active, b.created_at, b.updated_at, b.row_version,
           (SELECT count(*) FROM cash.bank_accounts x WHERE x.bank_id = b.id) AS accounts_count,
           (SELECT count(*) FROM cash.bank_accounts x WHERE x.bank_id = b.id AND x.is_active) AS active_accounts
    FROM cash.banks b
"""

_ACCOUNT_SELECT = """
    SELECT ba.id, ba.bank_id, ba.seq, ba.code, ba.account_type, ba.currency_code, ba.account_number, ba.iban,
           ba.overdraft_limit, ba.maturity_date, ba.account_id, ba.owns_account, ba.notes, ba.is_active,
           ba.created_at, ba.updated_at, ba.row_version,
           b.code AS bank_code, b.name_ar AS bank_name, b.short_name AS bank_short, b.branch AS bank_branch,
           b.address AS bank_address, b.swift_code AS bank_swift, b.contact_name AS bank_contact,
           b.contact_phone AS bank_phone, b.is_active AS bank_active,
           a.code AS account_code, a.name_ar AS account_name, p.code AS parent_code, p.name_ar AS parent_name,
           (d.key IS NOT NULL) AS is_default
    FROM cash.bank_accounts ba
    JOIN cash.banks b ON b.id = ba.bank_id
    JOIN acc.accounts a ON a.id = ba.account_id
    LEFT JOIN acc.accounts p ON p.id = a.parent_id
    LEFT JOIN acc.default_accounts d ON d.key = 'bank' AND d.account_id = ba.account_id
"""

# قفل ترقيم البنوك، وقفل ترقيم الحسابات جوه كل بنك (جهازين بيضيفوا في نفس اللحظة مياخدوش نفس الرقم).
_BANK_LOCK = "SELECT pg_advisory_xact_lock(hashtext('cash.banks.code'))"
_NEXT_BANK_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM cash.banks"
_ACCOUNT_LOCK = "SELECT pg_advisory_xact_lock(hashtext('cash.bank_accounts.code'), %s)"
_NEXT_ACCOUNT_SEQ = "SELECT coalesce(max(seq), 0) + 1 FROM cash.bank_accounts WHERE bank_id = %s"


def format_bank_code(seq: int) -> str:
    """نفس معادلة العمود المحسوب في القاعدة: BNK-001 … BNK-999 ← BNK-1000."""
    seq = int(seq)
    return f"{BANK_PREFIX}{seq:03d}" if seq < 1000 else f"{BANK_PREFIX}{seq}"


def format_account_code(bank_code: str, seq: int) -> str:
    """نفس الـ trigger: BNK-001-01 … BNK-001-99 ← BNK-001-100."""
    seq = int(seq)
    return f"{bank_code}-{seq:02d}" if seq < 100 else f"{bank_code}-{seq}"


class BankRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- البنوك --------------------------------------------------------------
    def list_banks(self, keyword: str = "", status: str = "all", ex: Executor | None = None) -> list[dict]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            seq = keyword.upper().removeprefix(BANK_PREFIX).strip()
            conds.append("(b.code ILIKE %s OR b.seq::text = %s OR b.name_ar ILIKE %s OR b.name_en ILIKE %s "
                         "OR b.short_name ILIKE %s OR b.branch ILIKE %s OR b.swift_code ILIKE %s)")
            params += [like, seq.lstrip("0") or seq, like, like, like, like, like]
        if status == "active":
            conds.append("b.is_active")
        elif status == "inactive":
            conds.append("NOT b.is_active")
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        return self._x(ex).fetch_all(_BANK_SELECT + where + " ORDER BY b.seq", params)

    def get_bank(self, bank_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(_BANK_SELECT + " WHERE b.id = %s" + (" FOR UPDATE OF b" if for_update else ""),
                                     [bank_id])

    def bank_name_taken(self, name: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code, name_ar FROM cash.banks WHERE lower(btrim(name_ar)) = lower(btrim(%s)) "
            "AND id IS DISTINCT FROM %s", [name, exclude_id])

    def next_bank_seq(self, ex: Executor) -> int:
        ex.execute(_BANK_LOCK)
        return int(ex.fetch_value(_NEXT_BANK_SEQ))

    def suggested_bank_code(self, ex: Executor | None = None) -> str:
        return format_bank_code(int(self._x(ex).fetch_value(_NEXT_BANK_SEQ)))

    def insert_bank(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(BANK_FIELDS)
        marks = ", ".join(["%s"] * len(BANK_FIELDS))
        return int(self._x(ex).fetch_value(f"INSERT INTO cash.banks ({cols}) VALUES ({marks}) RETURNING id",
                                           [data[f] for f in BANK_FIELDS]))

    def update_bank(self, bank_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        fields = [f for f in BANK_FIELDS if f != "seq"]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE cash.banks SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [bank_id, *extra])

    def set_bank_active(self, bank_id: int, active: bool, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE cash.banks SET is_active = %s WHERE id = %s" + cond,
                                   [active, bank_id, *extra])

    def delete_bank(self, bank_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM cash.banks WHERE id = %s" + cond, [bank_id, *extra])

    # ---- الحسابات البنكية -----------------------------------------------------
    @staticmethod
    def _account_where(keyword: str, status: str) -> tuple[str, list[Any]]:
        conds: list[str] = []
        params: list[Any] = []
        if keyword:
            like = f"%{keyword}%"
            digits = "".join(ch for ch in keyword if ch.isalnum())
            conds.append("(ba.code ILIKE %s OR b.name_ar ILIKE %s OR b.short_name ILIKE %s OR a.code LIKE %s "
                         "OR a.name_ar ILIKE %s OR ba.account_number ILIKE %s "
                         "OR (%s <> '' AND (regexp_replace(coalesce(ba.account_number, ''), '[^0-9A-Za-z]', '', 'g') "
                         "ILIKE %s OR ba.iban ILIKE %s)))")
            params += [like, like, like, like, like, like, digits, f"%{digits}%", f"%{digits}%"]
        if status == "active":
            conds.append("ba.is_active")
        elif status == "inactive":
            conds.append("NOT ba.is_active")
        return (" WHERE " + " AND ".join(conds)) if conds else "", params

    def list_accounts(self, keyword: str = "", status: str = "all", limit: int | None = None, offset: int = 0,
                      ex: Executor | None = None) -> list[dict]:
        where, params = self._account_where(keyword, status)
        sql = _ACCOUNT_SELECT + where + " ORDER BY b.seq, ba.seq"
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            params += [limit, offset]
        return self._x(ex).fetch_all(sql, params)

    def get_account(self, bank_account_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            _ACCOUNT_SELECT + " WHERE ba.id = %s" + (" FOR UPDATE OF ba" if for_update else ""), [bank_account_id])

    def number_taken(self, bank_id: int, number: str, exclude_id: int | None = None,
                     ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code FROM cash.bank_accounts WHERE bank_id = %s AND account_number IS NOT NULL "
            "AND upper(regexp_replace(account_number, '[^0-9A-Za-z]', '', 'g')) = "
            "upper(regexp_replace(%s, '[^0-9A-Za-z]', '', 'g')) AND id IS DISTINCT FROM %s",
            [bank_id, number, exclude_id])

    def iban_taken(self, iban: str, exclude_id: int | None = None, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT id, code FROM cash.bank_accounts WHERE iban = %s AND id IS DISTINCT FROM %s", [iban, exclude_id])

    def linked_account_ids(self, bank_id: int, ex: Executor | None = None) -> list[int]:
        """حسابات الدليل المربوطة بحسابات البنك ده."""
        return [int(r["account_id"]) for r in self._x(ex).fetch_all(
            "SELECT account_id FROM cash.bank_accounts WHERE bank_id = %s ORDER BY seq", [bank_id])]

    def bank_account_of(self, account_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT id, code FROM cash.bank_accounts WHERE account_id = %s", [account_id])

    def next_account_seq(self, bank_id: int, ex: Executor) -> int:
        """المسلسل الجاي جوه البنك (جوه قفل الترقيم بتاع البنك ده — ``ex`` لازم يبقى الـ Tx اللي هيضيف)."""
        ex.execute(_ACCOUNT_LOCK, [bank_id])
        return int(ex.fetch_value(_NEXT_ACCOUNT_SEQ, [bank_id]))

    def suggested_account_seq(self, bank_id: int, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value(_NEXT_ACCOUNT_SEQ, [bank_id]))

    def insert_account(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        cols = ", ".join(ACCOUNT_FIELDS)
        marks = ", ".join(["%s"] * len(ACCOUNT_FIELDS))
        return int(self._x(ex).fetch_value(
            f"INSERT INTO cash.bank_accounts ({cols}, code) VALUES ({marks}, '') RETURNING id",
            [data[f] for f in ACCOUNT_FIELDS]))

    def update_account(self, bank_account_id: int, data: dict[str, Any], ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        fields = [f for f in ACCOUNT_FIELDS if f not in _ACCOUNT_FIXED]
        sets = ", ".join(f"{f} = %s" for f in fields)
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE cash.bank_accounts SET {sets} WHERE id = %s{cond}",
                                   [data[f] for f in fields] + [bank_account_id, *extra])

    def set_account_active(self, bank_account_id: int, active: bool, ex: Executor | None = None,
                           expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE cash.bank_accounts SET is_active = %s WHERE id = %s" + cond,
                                   [active, bank_account_id, *extra])

    def delete_account(self, bank_account_id: int, ex: Executor | None = None,
                       expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM cash.bank_accounts WHERE id = %s" + cond, [bank_account_id, *extra])

    # ---- الدليل والاختيارات -----------------------------------------------------
    def chart_rows(self, ex: Executor | None = None) -> list[dict]:
        """كل الحسابات بدور «بنك» (الرئيسي والفرعي) + الحساب البنكي المربوط بكل واحد — لشجرة النموذج 7."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.parent_id, a.level, a.is_leaf, a.is_active, a.currency_code, "
            "ba.id AS bank_account_id, ba.code AS bank_account_code, ba.account_type, ba.account_number, "
            "ba.is_active AS bank_account_active, b.name_ar AS bank_name, b.short_name AS bank_short, "
            "(d.key IS NOT NULL) AS is_default "
            "FROM acc.accounts a "
            "LEFT JOIN cash.bank_accounts ba ON ba.account_id = a.id "
            "LEFT JOIN cash.banks b ON b.id = ba.bank_id "
            "LEFT JOIN acc.default_accounts d ON d.key = 'bank' AND d.account_id = a.id "
            "WHERE a.role_code = %s ORDER BY a.code", [BANK_ROLE])

    def list_currencies(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, is_base, is_active FROM core.currencies ORDER BY is_base DESC, code")

    def free_bank_accounts(self, ex: Executor | None = None) -> list[dict]:
        """حسابات الدليل الفرعية النشطة بدور «بنك» ومش مربوطة بحساب بنكي (لـ «أختار حساب موجود»)."""
        return self._x(ex).fetch_all(
            "SELECT a.id, a.code, a.name_ar, a.name_en, a.currency_code FROM acc.accounts a "
            "WHERE a.is_leaf AND a.is_active AND a.role_code = %s "
            "AND NOT EXISTS (SELECT 1 FROM cash.bank_accounts b WHERE b.account_id = a.id) ORDER BY a.code",
            [BANK_ROLE])

    def bank_parent(self, ex: Executor, for_update: bool = False) -> dict | None:
        """الحساب الرئيسي اللي الحسابات الجديدة بتتعمل تحته = أعلى حساب رئيسي نشط بدور «بنك» (1102)."""
        return ex.fetch_one(
            "SELECT id FROM acc.accounts WHERE role_code = %s AND NOT is_leaf AND is_active "
            "ORDER BY level, code LIMIT 1" + (" FOR UPDATE" if for_update else ""), [BANK_ROLE])

    def account_title(self, bank_label: str, account_type: str, currency: str, ex: Executor | None = None) -> str:
        """اسم حساب الدليل المتولّد — من نفس دالة القاعدة (عشان الاسم يبقى واحد في الحتتين)."""
        return str(self._x(ex).fetch_value("SELECT cash.bank_account_title(%s, %s, %s)",
                                           [bank_label, account_type, currency]))
