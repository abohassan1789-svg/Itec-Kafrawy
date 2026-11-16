"""SQL نقل الأصناف والماركات من الأكسس (``AccessImportService``) — كل الكتابة جوه ``Tx`` واحد."""

from __future__ import annotations

from typing import Any

from app.database.db import Executor


class AccessImportRepository:
    @staticmethod
    def lock(ex: Executor) -> None:
        """نفس أقفال الترقيم بتاعة الشاشات (جهاز بيضيف صنف أو ماركة في نفس الوقت بيستنى النقل يخلص)."""
        ex.execute("SELECT pg_advisory_xact_lock(hashtext('inv.item_brands.code'))")
        ex.execute("SELECT pg_advisory_xact_lock(hashtext('inv.items.code'))")

    @staticmethod
    def brands(ex: Executor) -> list[dict]:
        return ex.fetch_all("SELECT id, name FROM inv.item_brands")

    @staticmethod
    def next_brand_seq(ex: Executor) -> int:
        return int(ex.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM inv.item_brands"))

    @staticmethod
    def insert_brand(ex: Executor, seq: int, name: str, notes: str | None) -> int:
        return int(ex.fetch_value(
            "INSERT INTO inv.item_brands (seq, name, notes) VALUES (%s, %s, %s) RETURNING id", [seq, name, notes]))

    @staticmethod
    def categories(ex: Executor) -> list[dict]:
        return ex.fetch_all("SELECT id, name, access_name FROM inv.item_categories")

    @staticmethod
    def units(ex: Executor) -> dict[str, int]:
        return {r["code"]: int(r["id"]) for r in ex.fetch_all("SELECT id, code FROM inv.units")}

    @staticmethod
    def tax_id(ex: Executor, code: str) -> int | None:
        value = ex.fetch_value("SELECT id FROM acc.tax_codes WHERE code = %s", [code])
        return int(value) if value is not None else None

    @staticmethod
    def items(ex: Executor) -> list[dict]:
        """الأصناف اللي موجودة (للاسم المتكرر في نفس النوع ولتعدية اللي اتنقل قبل كده)."""
        return ex.fetch_all("SELECT id, name, category_id, access_id FROM inv.items")

    @staticmethod
    def next_item_seq(ex: Executor) -> int:
        return int(ex.fetch_value("SELECT coalesce(max(seq), 0) + 1 FROM inv.items"))

    @staticmethod
    def insert_item(ex: Executor, row: dict[str, Any]) -> int:
        cols = ("seq", "name", "description", "category_id", "brand_id", "unit_id", "tax_code_id", "notes",
                "is_active", "access_id")
        return int(ex.fetch_value(
            f"INSERT INTO inv.items ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            [row[c] for c in cols]))
