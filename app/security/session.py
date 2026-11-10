"""جلسة المستخدم الحالي (SESSION) — منقولة من Hr GAD.

بعد الدخول بتتحمّل كل أكواد الصلاحيات المسموحة للمستخدم، وأي شاشة بتسأل
``SESSION.can("module.target.action")``.

فرق واحد عن Hr GAD: لو مفيش حد داخل، **كل حاجة ممنوعة** (Hr GAD كان بيسمح بكل حاجة
عشان التشغيل القديم من غير دخول). البرنامج ده مفيهوش طريق يفتح من غير دخول.
"""

from __future__ import annotations

from typing import Any


class SessionContext:
    def __init__(self) -> None:
        self.user: dict[str, Any] | None = None
        self.login_log_id: int | None = None
        self._allowed: frozenset[str] = frozenset()

    def start(self, user: dict[str, Any], allowed_codes: set[str], login_log_id: int | None = None) -> None:
        # الصورة وكلمة السر مالهمش لازمة في الجلسة.
        self.user = {k: v for k, v in user.items() if k not in ("password_hash", "photo")}
        self._allowed = frozenset(allowed_codes)
        self.login_log_id = login_log_id

    def refresh_user(self, user: dict[str, Any]) -> None:
        """لما المستخدم يعدّل بياناته هو (الهيدر بيتحدّث)."""
        if self.user and int(user["id"]) == int(self.user["id"]):
            self.user = {k: v for k, v in user.items() if k not in ("password_hash", "photo")}

    def reload_permissions(self, allowed_codes: set[str]) -> None:
        self._allowed = frozenset(allowed_codes)

    def clear(self) -> None:
        self.user = None
        self.login_log_id = None
        self._allowed = frozenset()

    @property
    def is_authenticated(self) -> bool:
        return self.user is not None

    @property
    def user_id(self) -> int | None:
        return int(self.user["id"]) if self.user else None

    @property
    def username(self) -> str:
        return str(self.user.get("username") or "") if self.user else ""

    @property
    def is_admin(self) -> bool:
        return bool(self.user and (self.user.get("is_admin") or self.user.get("full_access")))

    def can(self, permission_code: str) -> bool:
        if self.user is None:
            return False
        if self.is_admin:
            return True
        return permission_code in self._allowed

    def can_any(self, permission_codes: list[str]) -> bool:
        return any(self.can(code) for code in permission_codes)

    @property
    def allowed_codes(self) -> frozenset[str]:
        return self._allowed


SESSION = SessionContext()
