"""تجهيز القاعدة وقت فتح البرنامج: اتصال ← تنفيذ الـ migrations ← المدير الافتراضي ← مزامنة الصلاحيات."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from app.config.db_errors import describe_connection_error
from app.database import migrator
from app.database.db import Database

logger = logging.getLogger(__name__)

StepFn = Callable[[int, str], None]


@dataclass
class StartupResult:
    ok: bool
    message: str = ""
    db_name: str = ""
    server: str = ""
    server_version: str = ""
    schema_version: int = 0
    applied_now: list[str] = field(default_factory=list)
    admin_created: bool = False
    permissions: dict = field(default_factory=dict)


def prepare_database(db: Database, step: StepFn | None = None) -> StartupResult:
    step = step or (lambda _p, _t: None)
    s = db.settings
    try:
        step(15, "الاتصال بقاعدة البيانات…")
        version = db.server_version()

        step(40, "تحديث هيكل قاعدة البيانات…")
        todo = migrator.pending(db)

        def progress(index: int, total: int, name: str) -> None:
            step(40 + int(50 * index / max(total, 1)), f"تنفيذ سكريبت {index} من {total}: {name}")

        applied = migrator.migrate(db, progress=progress) if todo else []
        schema_version = migrator.current_version(db)

        from app.services.auth_service import AuthService
        from app.services.permissions_sync_service import PermissionsSyncService

        step(92, "تجهيز المستخدمين…")
        admin_created = AuthService(db).seed_admin()
        step(96, "مزامنة الصلاحيات…")
        sync = PermissionsSyncService(db).sync()
        logger.info("مزامنة الصلاحيات: %s", sync)
        step(100, "جاهز")
    except migrator.MigrationError as exc:
        logger.exception("فشل تحديث هيكل القاعدة")
        return StartupResult(False, str(exc), s.db_name)
    except Exception as exc:  # noqa: BLE001
        logger.exception("فشل تجهيز القاعدة")
        return StartupResult(False, describe_connection_error(
            exc, host=s.db_host, port=s.db_port, db_name=s.db_name, password=s.db_password,
            fallback_prefix="تعذّر تجهيز قاعدة البيانات",
        ), s.db_name)

    return StartupResult(
        True,
        "تم الاتصال وتجهيز القاعدة.",
        db_name=s.db_name,
        server=f"{s.db_host}:{s.db_port}",
        server_version=version,
        schema_version=schema_version,
        applied_now=[f"{m.version:04d}_{m.name}" for m in applied],
        admin_created=admin_created,
        permissions=sync,
    )
