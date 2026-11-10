"""SQL سجل التعديلات ``sec.audit_log`` (للإضافة والقراءة بس — القاعدة بتمنع التعديل والمسح)."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app.database.db import Database, Executor, get_db


class AuditRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def insert(self, ex: Executor, row: dict[str, Any]) -> int:
        """لازم يتبعت ``Tx`` نفس بتاع العملية (نفس الـ transaction)."""
        result = ex.fetch_one(
            "INSERT INTO sec.audit_log (user_id, username, action, entity, entity_label, entity_id, "
            "record_label, details, device_name) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            [row.get("user_id"), row.get("username"), row["action"], row["entity"], row.get("entity_label"),
             None if row.get("entity_id") is None else str(row["entity_id"]), row.get("record_label"),
             Jsonb(row.get("details") or {}), row.get("device_name")],
        )
        return int(result["id"])

    def list_for(self, entity: str, entity_id: Any, ex: Executor | None = None) -> list[dict]:
        return (ex or self.db).fetch_all(
            "SELECT * FROM sec.audit_log WHERE entity=%s AND entity_id=%s ORDER BY occurred_at DESC, id DESC",
            [entity, str(entity_id)])

    def last_change(self, entity: str, entity_id: Any, actions: tuple[str, ...] | None = None,
                    ex: Executor | None = None) -> dict | None:
        """آخر تعديل على السجل (مين، إمتى، من أنهي جهاز) — لرسالة «اتعدّل من جهاز تاني»."""
        sql = ("SELECT action, username, occurred_at, device_name FROM sec.audit_log "
               "WHERE entity = %s AND entity_id = %s")
        params: list[Any] = [entity, str(entity_id)]
        if actions:
            sql += " AND action = ANY(%s)"
            params.append(list(actions))
        return (ex or self.db).fetch_one(sql + " ORDER BY occurred_at DESC, id DESC LIMIT 1", params)
