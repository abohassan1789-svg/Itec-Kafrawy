"""ملف الاتصال (connection.json) — المصدر الوحيد لطريقة وصول الجهاز ده للقاعدة.

شكل الملف (JSON)::

    {
      "schema_version": 1,
      "server": {"address": "localhost", "port": 5432},
      "database": {"name": "DBITEC", "user": "postgres",
                   "password": "dpapi:...", "sslmode": "prefer"},
      "updated_at": "2026-11-07T15:00:00"
    }

ترتيب البحث عن الملف (أول واحد موجود يكسب):

1. ``%ITEC_CONFIG_PATH%`` (للاختبارات والتثبيت).
2. ``%ProgramData%\\ITEC\\connection.json`` (على مستوى الجهاز — بيفضل بعد التحديثات).
3. ``<جذر البرنامج>\\config\\connection.json`` (للتطوير / النسخة المحمولة).

الكتابة: لو مفيش ملف، بيتكتب في ``%ProgramData%\\ITEC``، ولو مفيش صلاحية بيتكتب في
``config\\`` جنب البرنامج.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app import APP_DIRNAME
from app.config import crypto
from app.config.paths import app_root

SCHEMA_VERSION = 1
CONFIG_FILENAME = "connection.json"
DEFAULT_PORT = 5432
DEFAULT_DB_NAME = "DBITEC"
SSL_MODES = ("prefer", "require", "disable", "verify-full")


def _programdata_dir() -> Path | None:
    program_data = os.getenv("ProgramData")
    if os.name == "nt" and program_data:
        return Path(program_data) / APP_DIRNAME
    return None


def _local_config_path() -> Path:
    return app_root() / "config" / CONFIG_FILENAME


def _candidate_paths() -> list[Path]:
    paths: list[Path] = []
    machine = _programdata_dir()
    if machine is not None:
        paths.append(machine / CONFIG_FILENAME)
    paths.append(_local_config_path())
    return paths


def config_path() -> Path:
    """مسار ملف الاتصال (من غير ما يعمل أي حاجة على الديسك)."""
    override = os.getenv("ITEC_CONFIG_PATH")
    if override:
        return Path(override)
    for candidate in _candidate_paths():
        if candidate.exists():
            return candidate
    machine = _programdata_dir()
    return (machine / CONFIG_FILENAME) if machine is not None else _local_config_path()


def config_exists() -> bool:
    return config_path().exists()


@dataclass
class ConnectionProfile:
    server_address: str = "localhost"
    server_port: int = DEFAULT_PORT
    db_name: str = DEFAULT_DB_NAME
    db_user: str = "postgres"
    db_password: str = ""  # في الذاكرة بس نص صريح، وبيتشفّر وقت الحفظ
    sslmode: str = "prefer"
    updated_at: str = ""

    def validate(self) -> list[str]:
        """قائمة بالأخطاء بالعربي (فاضية = سليم)."""
        errors: list[str] = []
        if not str(self.server_address).strip():
            errors.append("عنوان الخادم مطلوب.")
        try:
            port = int(self.server_port)
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            errors.append("المنفذ لازم يكون بين 1 و 65535.")
        if not str(self.db_name).strip():
            errors.append("اسم قاعدة البيانات مطلوب.")
        if not str(self.db_user).strip():
            errors.append("اسم مستخدم قاعدة البيانات مطلوب.")
        if self.sslmode not in SSL_MODES:
            errors.append("وضع SSL غير معروف.")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "server": {"address": self.server_address, "port": int(self.server_port)},
            "database": {
                "name": self.db_name,
                "user": self.db_user,
                "password": crypto.encrypt(self.db_password),
                "sslmode": self.sslmode,
            },
            "updated_at": self.updated_at or _now_iso(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConnectionProfile":
        server = data.get("server") or {}
        database = data.get("database") or {}
        try:
            password = crypto.decrypt(database.get("password") or "")
        except Exception:
            # ملف من جهاز تاني (DPAPI مربوط بالجهاز) ← نسيبها فاضية والشاشة تطلبها.
            password = ""
        return cls(
            server_address=server.get("address") or "localhost",
            server_port=int(server.get("port") or DEFAULT_PORT),
            db_name=database.get("name") or DEFAULT_DB_NAME,
            db_user=database.get("user") or "postgres",
            db_password=password,
            sslmode=database.get("sslmode") or "prefer",
            updated_at=data.get("updated_at") or "",
        )

    def conninfo(self) -> dict[str, Any]:
        """بارامترات psycopg.connect (من غير ما كلمة السر تتحط في URL)."""
        return {
            "host": self.server_address,
            "port": int(self.server_port),
            "dbname": self.db_name,
            "user": self.db_user,
            "password": self.db_password,
            "sslmode": self.sslmode,
        }


def _now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def load() -> ConnectionProfile | None:
    """يقرا ملف الاتصال، أو None لو مش موجود أو بايظ."""
    path = config_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return ConnectionProfile.from_dict(data)


def save(profile: ConnectionProfile, path: Path | None = None) -> Path:
    """يحفظ الملف (كتابة ذرّية). لو %ProgramData% مقفول يكتب جنب البرنامج."""
    profile.updated_at = _now_iso()
    payload = json.dumps(profile.to_dict(), indent=2, ensure_ascii=False)
    targets = [path] if path is not None else [config_path(), _local_config_path()]
    last_error: Exception | None = None
    for target in dict.fromkeys(targets):  # من غير تكرار وبنفس الترتيب
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, target)
            return target
        except OSError as exc:
            last_error = exc
    raise OSError(f"تعذّر حفظ ملف الاتصال: {last_error}")
