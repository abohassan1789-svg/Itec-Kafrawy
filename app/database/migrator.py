"""مشغّل الـ migrations: سكريبتات SQL مرقّمة في ``app/database/migrations/``.

* اسم الملف: ``NNNN_وصف_إنجليزي.sql`` (مثلاً ``0001_schemas.sql``) — بيتنفذوا بالترتيب.
* كل سكريبت بيتنفذ **مرة واحدة** جوه transaction لوحده، واللي اتنفذ بيتسجل في
  ``core.schema_migrations`` (رقم، اسم، checksum، وقت، مين).
* **ممنوع تعديل سكريبت اتنفذ**: لو الـ checksum اتغيّر البرنامج بيرفض يكمّل —
  التعديل يبقى في سكريبت جديد برقم أكبر.
* Advisory lock عشان لو جهازين فتحوا البرنامج في نفس اللحظة مايشغّلوش نفس السكريبت مرتين.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config.paths import resource_root
from app.database.db import Database

logger = logging.getLogger(__name__)

_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")
_LOCK_KEY = 7_420_260_001  # رقم ثابت للـ advisory lock بتاع الـ migrations

_BOOTSTRAP_SQL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.schema_migrations (
    version     integer PRIMARY KEY,
    name        text        NOT NULL,
    checksum    char(64)    NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL DEFAULT current_user
);
COMMENT ON TABLE core.schema_migrations IS 'سجل سكريبتات قاعدة البيانات اللي اتنفذت';
"""


class MigrationError(RuntimeError):
    """خطأ في الـ migrations (رسالته بالعربي)."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        # بنوحّد نهايات السطور عشان نفس الملف يدي نفس الرقم على أي جهاز.
        normalized = self.sql.replace("\r\n", "\n").encode("utf-8")
        return hashlib.sha256(normalized).hexdigest()


def migrations_dir() -> Path:
    return resource_root() / "app" / "database" / "migrations"


def discover(folder: Path | None = None) -> list[Migration]:
    folder = folder or migrations_dir()
    found: dict[int, Migration] = {}
    for path in sorted(folder.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if not match:
            raise MigrationError(f"اسم ملف migration غير صحيح: {path.name} (لازم NNNN_name.sql)")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"رقم migration متكرر: {version:04d}")
        found[version] = Migration(version, match.group(2), path)
    return [found[v] for v in sorted(found)]


def applied(db: Database) -> dict[int, str]:
    rows = db.fetch_all("SELECT version, checksum FROM core.schema_migrations ORDER BY version")
    return {int(r["version"]): str(r["checksum"]).strip() for r in rows}


def pending(db: Database, folder: Path | None = None) -> list[Migration]:
    db.execute_script(_BOOTSTRAP_SQL)
    done = applied(db)
    return [m for m in discover(folder) if m.version not in done]


ProgressFn = Callable[[int, int, str], None]


def migrate(db: Database, folder: Path | None = None, progress: ProgressFn | None = None) -> list[Migration]:
    """ينفّذ كل السكريبتات اللي لسه ماتنفذتش، ويرجّع اللي اتنفذ."""
    db.execute_script(_BOOTSTRAP_SQL)
    # لو جهاز تاني بيحدّث القاعدة دلوقتي ← نستناه لحد ما يخلص (مش 5 ثواني بس)، وبعدين نرجع المهلة.
    db.execute("SET lock_timeout = 0")
    try:
        db.fetch_one("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    finally:
        db.execute("RESET lock_timeout")
    try:
        all_migrations = discover(folder)
        done = applied(db)

        for m in all_migrations:
            if m.version in done and done[m.version] != m.checksum:
                raise MigrationError(
                    f"السكريبت {m.path.name} اتعدّل بعد ما اتنفذ على القاعدة.\n"
                    "ممنوع تعديل سكريبت اتنفذ — اعمل سكريبت جديد برقم أكبر."
                )
        known = {m.version for m in all_migrations}
        unknown = sorted(v for v in done if v not in known)
        if unknown:
            raise MigrationError(
                "القاعدة فيها سكريبتات أحدث من نسخة البرنامج دي "
                f"({', '.join(f'{v:04d}' for v in unknown)}). حدّث البرنامج."
            )

        todo = [m for m in all_migrations if m.version not in done]
        for index, m in enumerate(todo, start=1):
            if progress:
                progress(index, len(todo), m.name)
            logger.info("تنفيذ migration %04d_%s", m.version, m.name)
            try:
                with db.transaction() as cur:
                    # سكريبت على بيانات كتير ممكن ياخد وقت ← من غير مهلة (جوه الـ Tx دي بس)
                    cur.execute("SET LOCAL statement_timeout = 0; SET LOCAL lock_timeout = 0")
                    cur.execute(m.sql)
                    cur.execute(
                        "INSERT INTO core.schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (m.version, m.name, m.checksum),
                    )
            except MigrationError:
                raise
            except Exception as exc:
                raise MigrationError(f"فشل تنفيذ السكريبت {m.path.name}:\n{exc}") from exc
        return todo
    finally:
        try:
            db.fetch_one("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))
        except Exception:  # الاتصال ممكن يكون وقع — القفل بيتفك لوحده مع قفل الجلسة
            pass


def current_version(db: Database) -> int:
    db.execute_script(_BOOTSTRAP_SQL)
    return int(db.fetch_value("SELECT COALESCE(MAX(version), 0) FROM core.schema_migrations") or 0)
