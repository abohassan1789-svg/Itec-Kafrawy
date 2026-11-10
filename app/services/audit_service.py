"""سجل التعديلات (قرار المستخدم — docs/03 القسم 8 بند 34):
مين عمل إيه وإمتى، و**قبل/بعد بسطور المستند**، جوه **نفس الـ transaction** بتاعة العملية
(لو الحفظ رجع، سطر السجل بيرجع معاه).

شكل ``details`` (نفس فكرة E3marEG)::

    {"v": 1, "before": {...}|null, "after": {...}|null,
     "lines": {"<جدول السطور>": {"before": [...], "after": [...]}}}

الأعمدة السرية والكبيرة (كلمات السر، الصور) عمرها ما بتتسجل.
"""

from __future__ import annotations

import datetime as dt
import decimal
import socket
from typing import Any

from app.database.db import Database, Executor
from app.repositories.audit_repository import AuditRepository
from app.security.session import SESSION

SKIP_COLUMNS = frozenset({"password_hash", "action_password_hash", "photo", "logo", "updated_at",
                          "row_version", "permissions_version"})   # الإصدار بيزيد لوحده (0008)
_MAX_TEXT = 500


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value[:_MAX_TEXT] if isinstance(value, str) else value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return str(value)[:_MAX_TEXT]


def snapshot(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """صورة السجل اللي بتتحفظ (من غير الأعمدة السرية)."""
    if row is None:
        return None
    return {k: _json_value(v) for k, v in row.items() if k not in SKIP_COLUMNS}


def changed_fields(before: dict | None, after: dict | None) -> list[str]:
    before, after = before or {}, after or {}
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def device_name() -> str:
    try:
        return socket.gethostname()[:120]
    except OSError:
        return ""


class AuditService:
    def __init__(self, db: Database | None = None, repository: AuditRepository | None = None) -> None:
        self.repository = repository or AuditRepository(db)

    def record(self, ex: Executor, *, action: str, entity: str, entity_id: Any,
               entity_label: str = "", record_label: str = "",
               before: dict | None = None, after: dict | None = None,
               lines: dict[str, dict[str, list[dict]]] | None = None,
               extra: dict[str, Any] | None = None) -> int:
        """يسجل سطر في ``sec.audit_log`` جوه نفس الـ transaction (``ex`` = Tx)."""
        b, a = snapshot(before), snapshot(after)
        details: dict[str, Any] = {"v": 1, "before": b, "after": a}
        if b is not None and a is not None:
            details["changed"] = changed_fields(b, a)
        if lines:
            details["lines"] = {
                table: {"before": [snapshot(r) for r in (pair.get("before") or [])],
                        "after": [snapshot(r) for r in (pair.get("after") or [])]}
                for table, pair in lines.items()
            }
        if extra:
            details["extra"] = _json_value(extra)
        return self.repository.insert(ex, {
            "user_id": SESSION.user_id,
            "username": SESSION.username or None,
            "action": action,
            "entity": entity,
            "entity_label": entity_label or None,
            "entity_id": entity_id,
            "record_label": (record_label or "")[:200] or None,
            "details": details,
            "device_name": device_name(),
        })

    def history(self, entity: str, entity_id: Any) -> list[dict]:
        return self.repository.list_for(entity, entity_id)
