"""كل الـ SQL بتاع نظام الأمان (schema ``sec``) — منقول من Hr GAD.

كل دالة بتاخد ``ex`` اختياري: لو اتبعت ``Tx`` (من ``db.atomic()``) الأمر بيتنفذ جوه
نفس الـ transaction (عشان التعديل وسجله في ``sec.audit_log`` يتحفظوا مع بعض أو يرجعوا مع بعض).
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.versioning import version_clause

# أعمدة المستخدم من غير بايتات الصورة (الصورة بتتجاب لوحدها بـ get_user_photo).
_USER_COLUMNS = (
    "id", "username", "password_hash", "full_name", "job_title", "email", "phone", "role_id", "language",
    "is_active", "is_admin", "full_access", "notes", "photo_mime", "last_login_at", "created_at", "updated_at",
    "row_version", "permissions_version",
)
_USER_SELECT = ", ".join(f"u.{c}" for c in _USER_COLUMNS)


class SecurityRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    # ---- المجموعات --------------------------------------------------------
    def list_roles(self, keyword: str = "", active_only: bool = False, ex: Executor | None = None) -> list[dict]:
        sql = ("SELECT r.*, (SELECT COUNT(*) FROM sec.app_users u WHERE u.role_id = r.id) AS users_count "
               "FROM sec.roles r")
        clauses, params = [], []
        if active_only:
            clauses.append("r.is_active")
        if keyword.strip():
            clauses.append("(r.role_code ILIKE %s OR r.role_name_ar ILIKE %s OR r.role_name_en ILIKE %s)")
            like = f"%{keyword.strip()}%"
            params += [like, like, like]
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY r.role_name_ar, r.id"
        return self._x(ex).fetch_all(sql, params)

    def get_role(self, role_id: int, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT * FROM sec.roles WHERE id = %s", [role_id])

    def get_role_by_code(self, code: str, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one("SELECT * FROM sec.roles WHERE lower(role_code) = lower(%s)", [code])

    def create_role(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        row = self._x(ex).fetch_one(
            "INSERT INTO sec.roles (role_code, role_name_ar, role_name_en, description, is_active) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            [data["role_code"], data["role_name_ar"], data.get("role_name_en"),
             data.get("description"), data.get("is_active", True)],
        )
        return int(row["id"])

    def get_role_for_update(self, role_id: int, ex: Executor) -> dict | None:
        """الصف مقفول لحد آخر الـ Tx (عشان التحقق من الإصدار والحفظ يبقوا مع بعض)."""
        return ex.fetch_one("SELECT * FROM sec.roles WHERE id = %s FOR UPDATE", [role_id])

    def update_role(self, role_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """بيرجّع عدد الصفوف (0 = الإصدار اتغيّر من جهاز تاني)."""
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(
            "UPDATE sec.roles SET role_code=%s, role_name_ar=%s, role_name_en=%s, description=%s, "
            "is_active=%s, updated_at=now() WHERE id=%s" + cond,
            [data["role_code"], data["role_name_ar"], data.get("role_name_en"),
             data.get("description"), data.get("is_active", True), role_id, *extra],
        )

    def touch_role(self, role_id: int, ex: Executor | None = None) -> None:
        """صلاحيات المجموعة اتغيّرت ← إصدار المجموعة يزيد (الصلاحيات بتتحفظ مع المجموعة)."""
        self._x(ex).execute("UPDATE sec.roles SET row_version = row_version + 1, updated_at = now() WHERE id = %s",
                            [role_id])

    def delete_role(self, role_id: int, ex: Executor | None = None, expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("DELETE FROM sec.roles WHERE id = %s" + cond, [role_id, *extra])

    def count_role_users(self, role_id: int, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value("SELECT COUNT(*) FROM sec.app_users WHERE role_id = %s", [role_id]))

    def list_role_users(self, role_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT id, username, full_name, is_active FROM sec.app_users WHERE role_id = %s ORDER BY username",
            [role_id])

    # ---- المستخدمين -------------------------------------------------------
    def count_users(self, ex: Executor | None = None) -> int:
        return int(self._x(ex).fetch_value("SELECT COUNT(*) FROM sec.app_users"))

    def list_users(self, keyword: str = "", include_inactive: bool = True,
                   ex: Executor | None = None) -> list[dict]:
        sql = (f"SELECT {_USER_SELECT}, (u.photo IS NOT NULL) AS has_photo, r.role_name_ar "
               "FROM sec.app_users u LEFT JOIN sec.roles r ON r.id = u.role_id")
        clauses, params = [], []
        if not include_inactive:
            clauses.append("u.is_active")
        if keyword.strip():
            clauses.append("(u.username ILIKE %s OR u.full_name ILIKE %s OR u.email ILIKE %s OR u.phone ILIKE %s)")
            like = f"%{keyword.strip()}%"
            params += [like] * 4
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY lower(u.username)"
        return self._x(ex).fetch_all(sql, params)

    def get_user(self, user_id: int, ex: Executor | None = None, for_update: bool = False) -> dict | None:
        return self._x(ex).fetch_one(
            f"SELECT {_USER_SELECT}, (u.photo IS NOT NULL) AS has_photo, r.role_name_ar "
            "FROM sec.app_users u LEFT JOIN sec.roles r ON r.id = u.role_id WHERE u.id = %s"
            + (" FOR UPDATE OF u" if for_update else ""), [user_id])

    def get_user_by_username(self, username: str, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            f"SELECT {_USER_SELECT}, (u.photo IS NOT NULL) AS has_photo, r.role_name_ar "
            "FROM sec.app_users u LEFT JOIN sec.roles r ON r.id = u.role_id "
            "WHERE lower(u.username) = lower(%s)", [username.strip()])

    def create_user(self, data: dict[str, Any], ex: Executor | None = None) -> int:
        row = self._x(ex).fetch_one(
            "INSERT INTO sec.app_users (username, password_hash, full_name, job_title, email, phone, role_id, "
            "language, is_active, is_admin, full_access, notes, photo, photo_mime) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
            [data["username"], data["password_hash"], data.get("full_name"), data.get("job_title"),
             data.get("email"), data.get("phone"), data.get("role_id"), data.get("language", "ar"),
             data.get("is_active", True), data.get("is_admin", False), data.get("full_access", False),
             data.get("notes"), data.get("photo"), data.get("photo_mime") if data.get("photo") else None],
        )
        return int(row["id"])

    def update_user(self, user_id: int, data: dict[str, Any], ex: Executor | None = None,
                    expected_version: int | None = None) -> int:
        """الصورة بتتغير بس لو المفتاح ``photo`` موجود (None = إزالة)."""
        sets = ("full_name=%s, job_title=%s, email=%s, phone=%s, role_id=%s, language=%s, "
                "is_active=%s, is_admin=%s, full_access=%s, notes=%s")
        params: list[Any] = [data.get("full_name"), data.get("job_title"), data.get("email"), data.get("phone"),
                             data.get("role_id"), data.get("language", "ar"), data.get("is_active", True),
                             data.get("is_admin", False), data.get("full_access", False), data.get("notes")]
        if "photo" in data:
            sets += ", photo=%s, photo_mime=%s"
            params += [data["photo"], data.get("photo_mime") if data["photo"] else None]
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute(f"UPDATE sec.app_users SET {sets}, updated_at=now() WHERE id=%s{cond}",
                                   [*params, user_id, *extra])

    def get_user_photo(self, user_id: int, ex: Executor | None = None) -> tuple[bytes | None, str | None]:
        row = self._x(ex).fetch_one("SELECT photo, photo_mime FROM sec.app_users WHERE id = %s", [user_id])
        if not row or row["photo"] is None:
            return None, None
        return bytes(row["photo"]), row["photo_mime"]

    def update_password(self, user_id: int, password_hash: str, ex: Executor | None = None) -> None:
        self._x(ex).execute("UPDATE sec.app_users SET password_hash=%s, updated_at=now() WHERE id=%s",
                            [password_hash, user_id])

    def set_user_active(self, user_id: int, active: bool, ex: Executor | None = None,
                        expected_version: int | None = None) -> int:
        cond, extra = version_clause(expected_version)
        return self._x(ex).execute("UPDATE sec.app_users SET is_active=%s, updated_at=now() WHERE id=%s" + cond,
                                   [active, user_id, *extra])

    def touch_user_permissions(self, user_id: int, ex: Executor | None = None,
                               expected_version: int | None = None) -> int:
        """صلاحيات المستخدم اتغيّرت ← ``permissions_version`` يزيد (0 = جهاز تاني سبقك)."""
        cond, extra = version_clause(expected_version, "permissions_version")
        return self._x(ex).execute(
            "UPDATE sec.app_users SET permissions_version = permissions_version + 1 WHERE id = %s" + cond,
            [user_id, *extra])

    def update_last_login(self, user_id: int, ex: Executor | None = None) -> None:
        self._x(ex).execute("UPDATE sec.app_users SET last_login_at=now() WHERE id=%s", [user_id])

    def count_active_admins(self, exclude_user_id: int | None = None, ex: Executor | None = None) -> int:
        sql = "SELECT COUNT(*) FROM sec.app_users WHERE is_active AND (is_admin OR full_access)"
        params: list[Any] = []
        if exclude_user_id is not None:
            sql += " AND id <> %s"
            params.append(exclude_user_id)
        return int(self._x(ex).fetch_value(sql, params))

    # ---- الصلاحيات ---------------------------------------------------------
    def list_permissions(self, active_only: bool = True, ex: Executor | None = None) -> list[dict]:
        sql = "SELECT * FROM sec.permissions"
        if active_only:
            sql += " WHERE is_active"
        sql += " ORDER BY sort_order, id"
        return self._x(ex).fetch_all(sql)

    def upsert_permission(self, row: dict[str, Any], ex: Executor | None = None) -> None:
        self._x(ex).execute(
            "INSERT INTO sec.permissions (permission_code, permission_type, module_code, module_name_ar, "
            "module_name_en, category_ar, category_en, target_code, target_name_ar, target_name_en, "
            "action_code, action_name_ar, action_name_en, sort_order, is_active) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true) "
            "ON CONFLICT (permission_code) DO UPDATE SET "
            "permission_type=EXCLUDED.permission_type, module_code=EXCLUDED.module_code, "
            "module_name_ar=EXCLUDED.module_name_ar, module_name_en=EXCLUDED.module_name_en, "
            "category_ar=EXCLUDED.category_ar, category_en=EXCLUDED.category_en, "
            "target_code=EXCLUDED.target_code, target_name_ar=EXCLUDED.target_name_ar, "
            "target_name_en=EXCLUDED.target_name_en, action_code=EXCLUDED.action_code, "
            "action_name_ar=EXCLUDED.action_name_ar, action_name_en=EXCLUDED.action_name_en, "
            "sort_order=EXCLUDED.sort_order, is_active=true, updated_at=now()",
            [row["permission_code"], row["permission_type"], row["module_code"], row["module_name_ar"],
             row["module_name_en"], row["category_ar"], row["category_en"], row["target_code"],
             row["target_name_ar"], row["target_name_en"], row["action_code"], row["action_name_ar"],
             row["action_name_en"], row["sort_order"]],
        )

    def deactivate_permissions_not_in(self, active_codes: list[str], ex: Executor | None = None) -> int:
        """اللي اتشال من السجل **بيتعطّل بس** — عمره ما بيتمسح."""
        rows = self._x(ex).fetch_all(
            "UPDATE sec.permissions SET is_active=false, updated_at=now() "
            "WHERE is_active AND NOT (permission_code = ANY(%s)) RETURNING id", [list(active_codes)])
        return len(rows)

    # ---- صلاحيات المجموعة --------------------------------------------------
    def get_role_permission_map(self, role_id: int, ex: Executor | None = None) -> dict[int, bool]:
        rows = self._x(ex).fetch_all(
            "SELECT permission_id, allowed FROM sec.role_permissions WHERE role_id = %s", [role_id])
        return {int(r["permission_id"]): bool(r["allowed"]) for r in rows}

    def save_role_permissions(self, role_id: int, allowed_map: dict[int, bool], ex: Executor | None = None) -> None:
        x = self._x(ex)
        for permission_id, allowed in allowed_map.items():
            # صلاحية اتشالت ← كلمة سرها بتتمسح (اللي ياخدها تاني يتحطله كلمة سر جديدة).
            x.execute(
                "INSERT INTO sec.role_permissions (role_id, permission_id, allowed) VALUES (%s,%s,%s) "
                "ON CONFLICT (role_id, permission_id) DO UPDATE SET allowed=EXCLUDED.allowed, "
                "action_password_hash = CASE WHEN EXCLUDED.allowed "
                "THEN sec.role_permissions.action_password_hash ELSE NULL END, updated_at=now()",
                [role_id, int(permission_id), bool(allowed)])

    def get_role_password_ids(self, role_id: int, ex: Executor | None = None) -> set[int]:
        rows = self._x(ex).fetch_all(
            "SELECT permission_id FROM sec.role_permissions WHERE role_id=%s AND action_password_hash IS NOT NULL",
            [role_id])
        return {int(r["permission_id"]) for r in rows}

    def set_role_permission_password(self, role_id: int, permission_id: int, password_hash: str,
                                     ex: Executor | None = None) -> None:
        self._x(ex).execute(
            "INSERT INTO sec.role_permissions (role_id, permission_id, allowed, action_password_hash) "
            "VALUES (%s,%s,true,%s) ON CONFLICT (role_id, permission_id) DO UPDATE SET allowed=true, "
            "action_password_hash=EXCLUDED.action_password_hash, updated_at=now()",
            [role_id, permission_id, password_hash])

    # ---- تخصيص المستخدم ----------------------------------------------------
    def get_user_permission_map(self, user_id: int, ex: Executor | None = None) -> dict[int, bool]:
        rows = self._x(ex).fetch_all(
            "SELECT permission_id, allowed FROM sec.user_permissions WHERE user_id = %s", [user_id])
        return {int(r["permission_id"]): bool(r["allowed"]) for r in rows}

    def set_user_permission(self, user_id: int, permission_id: int, allowed: bool | None,
                            ex: Executor | None = None) -> None:
        """True/False = سماح/منع؛ None = وراثة من المجموعة (الصف بيتمسح). المنع بيمسح كلمة السر."""
        x = self._x(ex)
        if allowed is None:
            x.execute("DELETE FROM sec.user_permissions WHERE user_id=%s AND permission_id=%s",
                      [user_id, permission_id])
            return
        x.execute(
            "INSERT INTO sec.user_permissions (user_id, permission_id, allowed) VALUES (%s,%s,%s) "
            "ON CONFLICT (user_id, permission_id) DO UPDATE SET allowed=EXCLUDED.allowed, "
            "action_password_hash = CASE WHEN EXCLUDED.allowed "
            "THEN sec.user_permissions.action_password_hash ELSE NULL END, updated_at=now()",
            [user_id, permission_id, allowed])

    def override_counts(self, ex: Executor | None = None) -> dict[int, dict[str, int]]:
        """عدد تخصيصات كل مستخدم ← {user_id: {"allow": n, "deny": n}} (لقائمة «صلاحيات المستخدم»)."""
        rows = self._x(ex).fetch_all(
            "SELECT up.user_id, COUNT(*) FILTER (WHERE up.allowed) AS allow, "
            "COUNT(*) FILTER (WHERE NOT up.allowed) AS deny FROM sec.user_permissions up "
            "JOIN sec.permissions p ON p.id = up.permission_id AND p.is_active GROUP BY up.user_id")
        return {int(r["user_id"]): {"allow": int(r["allow"]), "deny": int(r["deny"])} for r in rows}

    def reset_user_overrides(self, user_id: int, ex: Executor | None = None) -> int:
        return self._x(ex).execute("DELETE FROM sec.user_permissions WHERE user_id=%s", [user_id])

    def get_user_password_ids(self, user_id: int, ex: Executor | None = None) -> set[int]:
        rows = self._x(ex).fetch_all(
            "SELECT permission_id FROM sec.user_permissions WHERE user_id=%s AND action_password_hash IS NOT NULL",
            [user_id])
        return {int(r["permission_id"]) for r in rows}

    def set_user_permission_password(self, user_id: int, permission_id: int, password_hash: str,
                                     ex: Executor | None = None) -> None:
        """كلمة سر للمستخدم نفسه = سماح خاص بيه."""
        self._x(ex).execute(
            "INSERT INTO sec.user_permissions (user_id, permission_id, allowed, action_password_hash) "
            "VALUES (%s,%s,true,%s) ON CONFLICT (user_id, permission_id) DO UPDATE SET allowed=true, "
            "action_password_hash=EXCLUDED.action_password_hash, updated_at=now()",
            [user_id, permission_id, password_hash])

    def get_action_password_hashes(self, user_id: int, role_id: int | None, permission_code: str,
                                   ex: Executor | None = None) -> dict[str, Any]:
        row = self._x(ex).fetch_one(
            "SELECT p.id, up.allowed AS u_allowed, up.action_password_hash AS u_hash, "
            "rp.allowed AS r_allowed, rp.action_password_hash AS r_hash FROM sec.permissions p "
            "LEFT JOIN sec.user_permissions up ON up.permission_id = p.id AND up.user_id = %s "
            "LEFT JOIN sec.role_permissions rp ON rp.permission_id = p.id AND rp.role_id = %s "
            "WHERE p.permission_code = %s", [user_id, role_id, permission_code])
        return dict(row) if row else {}

    # ---- سجل الدخول --------------------------------------------------------
    def insert_login_log(self, user_id: int | None, username: str, status: str,
                         device_name: str | None = None, ip_address: str | None = None,
                         notes: str | None = None, ex: Executor | None = None) -> int:
        row = self._x(ex).fetch_one(
            "INSERT INTO sec.user_login_logs (user_id, username, status, device_name, ip_address, notes) "
            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
            [user_id, username, status, device_name, ip_address, notes])
        return int(row["id"])

    def set_logout(self, log_id: int, ex: Executor | None = None) -> None:
        self._x(ex).execute(
            "UPDATE sec.user_login_logs SET logout_time=now() WHERE id=%s AND logout_time IS NULL", [log_id])

    def list_login_logs(self, user_id: int | None = None, status: str | None = None,
                        date_from=None, date_to=None, keyword: str = "", limit: int = 500,
                        ex: Executor | None = None) -> list[dict]:
        # المحاولة الفاشلة مالهاش user_id ← بنجيب الاسم باسم المستخدم.
        sql = ("SELECT l.*, u.full_name FROM sec.user_login_logs l "
               "LEFT JOIN sec.app_users u ON u.id = l.user_id "
               "OR (l.user_id IS NULL AND lower(u.username) = lower(l.username))")
        clauses, params = [], []
        if user_id is not None:
            # المحاولات الفاشلة بتتسجل من غير user_id ← بنربطها باسم المستخدم.
            clauses.append("(l.user_id = %s OR (l.user_id IS NULL AND lower(l.username) = "
                           "(SELECT lower(username) FROM sec.app_users WHERE id = %s)))")
            params += [user_id, user_id]
        if status == "open":                      # دخل ولسه ماسجّلش خروج
            clauses.append("l.status = 'success' AND l.logout_time IS NULL")
        elif status:
            clauses.append("l.status = %s")
            params.append(status)
        if date_from is not None:
            clauses.append("l.login_time >= %s")
            params.append(date_from)
        if date_to is not None:
            clauses.append("l.login_time < %s::date + 1")
            params.append(date_to)
        if keyword.strip():
            clauses.append("(l.username ILIKE %s OR u.full_name ILIKE %s OR l.device_name ILIKE %s)")
            like = f"%{keyword.strip()}%"
            params += [like] * 3
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY l.login_time DESC, l.id DESC LIMIT %s"
        params.append(limit)
        return self._x(ex).fetch_all(sql, params)
