"""الدخول والخروج والمدير الافتراضي — منقول من Hr GAD.

قرار المستخدم: **مفيش إجبار** على تغيير كلمة سر المدير الافتراضي.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.database.db import Database, get_db
from app.repositories.security_repository import SecurityRepository
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services.audit_service import device_name
from app.services.permission_service import PermissionService

logger = logging.getLogger(__name__)

# المدير الافتراضي (زي Hr GAD) — بيتعمل أول تشغيل بس لو مفيش ولا مستخدم.
DEFAULT_ADMIN_USERNAME = "user"
DEFAULT_ADMIN_PASSWORD = "123123"


@dataclass
class LoginResult:
    ok: bool
    message: str = ""
    user: dict[str, Any] | None = None


class AuthService:
    def __init__(self, db: Database | None = None, repository: SecurityRepository | None = None,
                 hasher: PasswordHasher | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SecurityRepository(self.db)
        self.hasher = hasher or PasswordHasher()
        self.permissions = PermissionService(self.db, self.repository)

    # ---- أول تشغيل ---------------------------------------------------------
    def seed_admin(self) -> bool:
        """يعمل المدير الافتراضي لو مفيش ولا مستخدم. بيرجع True لو اتعمل."""
        if self.repository.count_users() > 0:
            return False
        self.repository.create_user({
            "username": DEFAULT_ADMIN_USERNAME,
            "password_hash": self.hasher.hash(DEFAULT_ADMIN_PASSWORD),
            "full_name": "مدير النظام",
            "job_title": "مدير النظام",
            "language": "ar",
            "is_active": True,
            "is_admin": True,
            "full_access": True,
            "notes": "حساب المدير الافتراضي",
        })
        logger.info("اتعمل المدير الافتراضي (%s)", DEFAULT_ADMIN_USERNAME)
        return True

    # ---- الدخول ------------------------------------------------------------
    def authenticate(self, username: str, password: str) -> tuple[dict[str, Any] | None, str | None]:
        username = (username or "").strip()
        if not username:
            return None, "اكتب اسم المستخدم"
        if not password:
            return None, "اكتب كلمة المرور"
        user = self.repository.get_user_by_username(username)
        if user is None:
            return None, "اسم المستخدم غير موجود"
        if not user.get("is_active"):
            return None, "هذا المستخدم موقوف، تواصل مع المدير"
        if not self.hasher.verify(password, user.get("password_hash")):
            return None, "كلمة المرور غير صحيحة"
        return user, None

    def login(self, username: str, password: str) -> LoginResult:
        """تحقق ← تسجيل في سجل الدخول ← تحميل الصلاحيات في SESSION."""
        user, error = self.authenticate(username, password)
        device = device_name()
        if user is None:
            if (username or "").strip():
                self.repository.insert_login_log(None, username.strip()[:60], "failed",
                                                 device_name=device, notes=error)
            return LoginResult(False, error or "تعذّر الدخول")
        with self.db.atomic() as tx:
            self.repository.update_last_login(user["id"], ex=tx)
            log_id = self.repository.insert_login_log(user["id"], user["username"], "success",
                                                      device_name=device, ex=tx)
        user = self.repository.get_user(user["id"]) or user
        SESSION.start(user, self.permissions.effective_codes(user), log_id)
        logger.info("دخول المستخدم %s", user["username"])
        return LoginResult(True, "", user)

    def logout(self) -> None:
        if SESSION.login_log_id is not None:
            try:
                self.repository.set_logout(SESSION.login_log_id)
            except Exception:  # noqa: BLE001 — الخروج مايتوقفش على السجل
                logger.exception("تعذّر تسجيل وقت الخروج")
        logger.info("خروج المستخدم %s", SESSION.username)
        SESSION.clear()

    def reload_session_permissions(self) -> None:
        """بعد تعديل الصلاحيات: المستخدم الحالي ياخد صلاحياته الجديدة على طول."""
        if SESSION.user_id is None:
            return
        user = self.repository.get_user(SESSION.user_id)
        if user is not None:
            SESSION.refresh_user(user)
            SESSION.reload_permissions(self.permissions.effective_codes(user))
