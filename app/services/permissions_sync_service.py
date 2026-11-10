"""مزامنة الصلاحيات (وقت كل فتح للبرنامج) — زي Hr GAD:

* أي شاشة جديدة في ``permission_registry`` ← صلاحياتها تتضاف.
* الأسماء والترتيب بيتحدّثوا.
* اللي اتشال من السجل **بيتعطّل بس** — عمره ما بيتمسح.
* صلاحيات المجموعات والمستخدمين **ماتتلمسش**.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.security_repository import SecurityRepository
from app.services.permission_registry import build_permission_rows


class PermissionsSyncService:
    def __init__(self, db: Database | None = None, repository: SecurityRepository | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SecurityRepository(self.db)

    def sync(self) -> dict[str, Any]:
        rows = build_permission_rows()
        with self.db.atomic() as tx:
            existing = {p["permission_code"] for p in self.repository.list_permissions(False, ex=tx)}
            for row in rows:
                self.repository.upsert_permission(row, ex=tx)
            deactivated = self.repository.deactivate_permissions_not_in([r["permission_code"] for r in rows], ex=tx)
        added = sum(1 for r in rows if r["permission_code"] not in existing)
        return {"added": added, "updated": len(rows) - added, "total": len(rows), "deactivated": deactivated}
