"""إدارة المستخدمين (شاشة «المستخدمين») — قواعد Hr GAD + سجل التعديلات.

* اسم المستخدم مايتكررش (من غير حساسية لحروف كبيرة/صغيرة).
* مايتقفلش ولا يتلغي آخر مدير فعّال (هنا + Trigger في القاعدة).
* كلمة المرور فاضية وقت التعديل = من غير تغيير.
* المستخدم العادي مايقدرش يدّي حد (أو نفسه) «مدير» أو «صلاحية كاملة»، ولا يعدّل مستخدم مدير
  — ده بتاع المدير بس (عشان محدش يرفع صلاحياته بنفسه).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.security_repository import SecurityRepository
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError, PermissionDenied
from app.services.permission_service import PermissionService

MAX_PHOTO_BYTES = 2 * 1024 * 1024
_TEXT_FIELDS = ("full_name", "job_title", "email", "phone", "notes")
ENTITY = "sec.app_users"
USER_ACTIONS = ("create", "update", "activate", "deactivate")   # اللي بيزوّد row_version (مش الصلاحيات)


class UserService:
    def __init__(self, db: Database | None = None, repository: SecurityRepository | None = None,
                 hasher: PasswordHasher | None = None, audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SecurityRepository(self.db)
        self.hasher = hasher or PasswordHasher()
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_users(self, keyword: str = "", include_inactive: bool = False) -> list[dict[str, Any]]:
        return [self._public(u) for u in self.repository.list_users(keyword, include_inactive)]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        user = self.repository.get_user(user_id)
        return self._public(user) if user else None

    def get_user_photo(self, user_id: int) -> tuple[bytes | None, str | None]:
        return self.repository.get_user_photo(user_id)

    def list_login_logs(self, **filters) -> list[dict[str, Any]]:
        PermissionService.require("sys.login_logs.view")
        return self.repository.list_login_logs(**filters)

    def login_log_rows(self, user_id: int | None = None, status: str | None = None, date_from=None,
                       date_to=None, keyword: str = "", limit: int = 1000) -> list[dict[str, Any]]:
        """شاشة «سجل الدخول»: الصفوف + الحالة بالعربي + المدة. status: success / failed / open / None."""
        rows = self.list_login_logs(user_id=user_id, status=status, date_from=date_from, date_to=date_to,
                                    keyword=keyword, limit=limit)
        for row in rows:
            row["state"] = ("failed" if row["status"] != "success"
                            else "open" if row.get("logout_time") is None else "closed")
            row["status_ar"] = {"failed": "✖ فشل", "open": "● مفتوحة", "closed": "✔ ناجح"}[row["state"]]
            row["duration"] = login_duration(row.get("login_time"), row.get("logout_time"))
        return rows

    def list_user_logins(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return self.repository.list_login_logs(user_id=user_id, limit=limit)

    def list_roles_for_combo(self) -> list[dict[str, Any]]:
        return self.repository.list_roles(active_only=True)

    @staticmethod
    def _public(user: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in user.items() if k != "password_hash"}

    # ---- تجهيز البيانات ----------------------------------------------------
    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, Any] = {k: ((data.get(k) or "").strip() or None) for k in _TEXT_FIELDS}
        clean["role_id"] = int(data["role_id"]) if data.get("role_id") else None
        clean["language"] = data.get("language") or "ar"
        if clean["language"] not in ("ar", "en"):
            raise BusinessError("اللغة لازم تبقى عربي أو إنجليزي.")
        for flag, default in (("is_active", True), ("is_admin", False), ("full_access", False)):
            clean[flag] = bool(data.get(flag, default))
        if "photo" in data:
            photo = data.get("photo")
            if photo and len(photo) > MAX_PHOTO_BYTES:
                raise BusinessError("الصورة أكبر من 2 ميجا.")
            clean["photo"] = bytes(photo) if photo else None
            clean["photo_mime"] = data.get("photo_mime") if photo else None
        if clean["role_id"] is not None:
            role = self.repository.get_role(clean["role_id"])
            if role is None:
                raise BusinessError("المجموعة المختارة غير موجودة.")
        return clean

    @staticmethod
    def _guard_admin_flags(clean: dict[str, Any], current: dict[str, Any] | None) -> None:
        if SESSION.is_admin:
            return
        wants = clean["is_admin"] or clean["full_access"]
        had = bool(current and (current.get("is_admin") or current.get("full_access")))
        if wants or had:
            raise PermissionDenied("تعديل المديرين أو منح «مدير / صلاحية كاملة» للمدير بس.")

    # ---- إضافة / تعديل -----------------------------------------------------
    def create_user(self, data: dict[str, Any], password: str) -> int:
        PermissionService.require("sys.users.create")
        username = (data.get("username") or "").strip()
        if not username:
            raise BusinessError("اسم المستخدم مطلوب.")
        if len(username) > 60:
            raise BusinessError("اسم المستخدم أطول من المسموح (60 حرف).")
        if not password:
            raise BusinessError("كلمة المرور مطلوبة.")
        clean = self._clean(data)
        self._guard_admin_flags(clean, None)
        if self.repository.get_user_by_username(username) is not None:
            raise BusinessError("اسم المستخدم مستخدم بالفعل.")
        clean["username"] = username
        clean["password_hash"] = self.hasher.hash(password)
        with self.db.atomic() as tx:
            user_id = self.repository.create_user(clean, ex=tx)
            self.audit.record(tx, action="create", entity="sec.app_users", entity_id=user_id,
                              entity_label="مستخدم", record_label=username,
                              after=self.repository.get_user(user_id, ex=tx))
        return user_id

    def update_user(self, user_id: int, data: dict[str, Any], password: str | None = None,
                    expected_version: int | None = None) -> None:
        """``expected_version`` = الإصدار اللي الشاشة فتحت بيه المستخدم (لو جهاز تاني حفظ قبلك ← تعارض)."""
        PermissionService.require("sys.users.edit")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            current = self.repository.get_user(user_id, ex=tx, for_update=True)
            self.guard.check(tx, current, expected_version, entity=ENTITY, entity_id=user_id, actions=USER_ACTIONS)
            if current is None:
                raise BusinessError("المستخدم غير موجود.")
            self._guard_admin_flags(clean, current)
            was_admin = current["is_active"] and (current["is_admin"] or current["full_access"])
            stays_admin = clean["is_active"] and (clean["is_admin"] or clean["full_access"])
            if was_admin and not stays_admin and self.repository.count_active_admins(user_id, ex=tx) == 0:
                raise BusinessError("لا يمكن إلغاء آخر مستخدم مدير فعّال.")
            updated = self.repository.update_user(user_id, clean, ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=user_id, actions=USER_ACTIONS)
            if password:
                self.repository.update_password(user_id, self.hasher.hash(password), ex=tx)
            after = self.repository.get_user(user_id, ex=tx)
            self.audit.record(tx, action="update", entity="sec.app_users", entity_id=user_id,
                              entity_label="مستخدم", record_label=current["username"],
                              before=current, after=after,
                              extra={"password_changed": True} if password else None)
        if SESSION.user_id == user_id:
            SESSION.refresh_user(self.repository.get_user(user_id) or after)

    def set_user_active(self, user_id: int, active: bool, expected_version: int | None = None) -> None:
        PermissionService.require("sys.users.edit")
        with self.db.atomic() as tx:
            current = self.repository.get_user(user_id, ex=tx, for_update=True)
            self.guard.check(tx, current, expected_version, entity=ENTITY, entity_id=user_id, actions=USER_ACTIONS)
            if current is None:
                raise BusinessError("المستخدم غير موجود.")
            if not SESSION.is_admin and (current["is_admin"] or current["full_access"]):
                raise PermissionDenied("إيقاف أو تفعيل المديرين للمدير بس.")
            if not active and SESSION.user_id == user_id:
                raise BusinessError("مينفعش توقف نفسك.")
            if not active and (current["is_admin"] or current["full_access"]) \
                    and self.repository.count_active_admins(user_id, ex=tx) == 0:
                raise BusinessError("لا يمكن إيقاف آخر مستخدم مدير فعّال.")
            if bool(current["is_active"]) == bool(active):
                return
            changed = self.repository.set_user_active(user_id, active, ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, changed, expected_version, entity=ENTITY, entity_id=user_id, actions=USER_ACTIONS)
            self.audit.record(tx, action="activate" if active else "deactivate", entity="sec.app_users",
                              entity_id=user_id, entity_label="مستخدم", record_label=current["username"],
                              before=current, after=self.repository.get_user(user_id, ex=tx))


def login_duration(login_time, logout_time) -> str:
    """مدة الجلسة «س:دد» (فاضية لو لسه مفتوحة أو فشل)."""
    if login_time is None or logout_time is None:
        return ""
    minutes = max(0, int((logout_time - login_time).total_seconds() // 60))
    return f"{minutes // 60}:{minutes % 60:02d}"
