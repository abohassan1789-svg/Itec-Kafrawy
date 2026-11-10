"""منطق الصلاحيات: حساب الصلاحية الفعلية، المصفوفة، مجموعات الصلاحيات، التخصيص، كلمة سر الإجراء.

حساب الصلاحية الفعلية (الأدق يكسب — زي Hr GAD):
    1. مدير أو صلاحية كاملة          ← كل حاجة مسموحة.
    2. غير كده نبدأ من صلاحيات المجموعة.
    3. تخصيص المستخدم (سماح/منع)     ← يغلب على المجموعة. مفيش تخصيص ← يورث.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.security_repository import SecurityRepository
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services import permission_registry as reg
from app.services.audit_service import AuditService, changed_fields, snapshot
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError, PermissionDenied

INHERIT, ALLOW, DENY = "inherit", "allow", "deny"
ROLE_ENTITY, USER_ENTITY = "sec.roles", "sec.app_users"
USER_PERMISSION_ACTIONS = ("permissions", "password")   # اللي بيزوّد permissions_version


def snapshot_changed(before: dict | None, after: dict | None) -> bool:
    return bool(changed_fields(snapshot(before), snapshot(after)))


def resolve_effective(permissions: list[dict[str, Any]], role_map: dict[int, bool],
                      user_map: dict[int, bool], is_full_access: bool) -> dict[int, dict[str, Any]]:
    """دالة صافية (من غير قاعدة) ← {permission_id: {allowed, source}}."""
    result: dict[int, dict[str, Any]] = {}
    for perm in permissions:
        pid = int(perm["id"])
        if is_full_access:
            result[pid] = {"allowed": True, "source": "full_access"}
        elif pid in user_map:
            result[pid] = {"allowed": bool(user_map[pid]), "source": "override"}
        else:
            result[pid] = {"allowed": bool(role_map.get(pid, False)), "source": "role"}
    return result


def matrix_rows(permissions: list[dict[str, Any]], scope: str = "all") -> list[dict[str, Any]]:
    """صف لكل شاشة/تقرير، وفيه id الصلاحية لكل إجراء."""
    rows: dict[str, dict[str, Any]] = {}
    for perm in permissions:
        if scope in ("screen", "report") and perm["permission_type"] != scope:
            continue
        key = f"{perm['module_code']}.{perm['target_code']}"
        row = rows.setdefault(key, {
            "key": key,
            "permission_type": perm["permission_type"],
            "module_code": perm["module_code"],
            "module_name_ar": perm["module_name_ar"],
            "target_code": perm["target_code"],
            "target_name_ar": perm["target_name_ar"],
            "target_name_en": perm["target_name_en"],
            "actions": {},
        })
        row["actions"][perm["action_code"]] = int(perm["id"])
    return list(rows.values())


def group_like_sidebar(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ترتيب صفوف المصفوفة زي القائمة الجانبية بالظبط بعناوين الأقسام والأقسام الفرعية.

    ``{"kind": "header", "gid", "level" (0 قسم / 1 فرعي), "title", "count"}`` أو
    ``{"kind": "target", "row", "gids": [...]}``. القسم الفاضي مابيظهرش. الصلاحيات اللي مش في
    القائمة (اتشالت من السجل ولسه نشطة) بتظهر تحت «أخرى».
    """
    by_key = {r["key"]: r for r in rows}
    used: set[str] = set()
    entries: list[dict[str, Any]] = []
    for s in reg.SECTIONS:
        block: list[dict[str, Any]] = []
        total = 0
        for index, group in enumerate(s.groups):
            found = [by_key[f"{s.id}.{t.code}"] for t in group.targets if f"{s.id}.{t.code}" in by_key]
            if not found:
                continue
            used.update(r["key"] for r in found)
            total += len(found)
            sub_gid = f"{s.id}/{index}" if group.title else None
            if sub_gid:
                block.append({"kind": "header", "gid": sub_gid, "level": 1,
                              "title": f"{group.icon}  {group.title}".strip(), "count": len(found)})
            block += [{"kind": "target", "row": r, "gids": [s.id] + ([sub_gid] if sub_gid else [])}
                      for r in found]
        if total:
            entries.append({"kind": "header", "gid": s.id, "level": 0,
                            "title": f"{s.icon}  {s.title}", "count": total})
            entries.extend(block)
    rest = [r for r in rows if r["key"] not in used]
    if rest:
        entries.append({"kind": "header", "gid": "other", "level": 0, "title": "أخرى", "count": len(rest)})
        entries += [{"kind": "target", "row": r, "gids": ["other"]} for r in rest]
    return entries


class PermissionService:
    def __init__(self, db: Database | None = None, repository: SecurityRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SecurityRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- التحقق من صلاحية المستخدم الحالي ----------------------------------
    @staticmethod
    def require(code: str) -> None:
        if not SESSION.can(code):
            raise PermissionDenied()

    # ---- حساب الصلاحية الفعلية ---------------------------------------------
    def resolve_user(self, user: dict[str, Any]) -> dict[int, dict[str, Any]]:
        permissions = self.repository.list_permissions(active_only=True)
        is_full = bool(user.get("is_admin") or user.get("full_access"))
        role_map = self.repository.get_role_permission_map(user["role_id"]) if user.get("role_id") else {}
        # مجموعة موقوفة = كأنها مش موجودة.
        if user.get("role_id"):
            role = self.repository.get_role(user["role_id"])
            if role is None or not role["is_active"]:
                role_map = {}
        user_map = self.repository.get_user_permission_map(user["id"])
        return resolve_effective(permissions, role_map, user_map, is_full)

    def effective_codes(self, user: dict[str, Any]) -> set[str]:
        by_id = {int(p["id"]): p["permission_code"] for p in self.repository.list_permissions(active_only=True)}
        return {by_id[pid] for pid, info in self.resolve_user(user).items() if info["allowed"]}

    def preview_user(self, user_id: int) -> list[dict[str, Any]]:
        """«معاينة الصلاحيات»: كل صلاحية ونتيجتها ومصدرها (صلاحية كاملة / تخصيص / المجموعة)."""
        user = self.repository.get_user(user_id)
        if user is None:
            raise BusinessError("المستخدم غير موجود.")
        resolved = self.resolve_user(user)
        sources = {"full_access": "صلاحية كاملة", "override": "تخصيص", "role": "المجموعة"}
        out = []
        for p in self.repository.list_permissions(active_only=True):
            info = resolved[int(p["id"])]
            out.append({"id": int(p["id"]), "section": p["module_name_ar"], "target": p["target_name_ar"],
                        "action": p["action_name_ar"], "allowed": info["allowed"],
                        "result": "مسموح" if info["allowed"] else "ممنوع", "source": sources[info["source"]]})
        return out

    # ---- المصفوفة ----------------------------------------------------------
    @staticmethod
    def actions_for_type(permission_type: str) -> list[tuple[str, str, str]]:
        return list(reg.REPORT_ACTIONS if permission_type == "report" else reg.SCREEN_ACTIONS)

    def build_matrix(self, scope: str = "all") -> list[dict[str, Any]]:
        return matrix_rows(self.repository.list_permissions(active_only=True), scope)

    def grouped_matrix(self, scope: str = "all") -> list[dict[str, Any]]:
        return group_like_sidebar(self.build_matrix(scope))

    def _active_role_maps(self, user: dict[str, Any], ex=None) -> tuple[dict[int, bool], set[int]]:
        """صلاحيات مجموعة المستخدم وكلمات سرها (المجموعة الموقوفة = ولا حاجة)."""
        role_id = user.get("role_id")
        if not role_id:
            return {}, set()
        role = self.repository.get_role(role_id, ex=ex)
        if role is None or not role["is_active"]:
            return {}, set()
        return (self.repository.get_role_permission_map(role_id, ex=ex),
                self.repository.get_role_password_ids(role_id, ex=ex))

    def user_matrix(self, user_id: int) -> dict[int, dict[str, Any]]:
        """لشاشة «صلاحيات المستخدم»: لكل صلاحية ← قيمة المجموعة، التخصيص، الفعلية، وفيه كلمة سر؟"""
        user = self.repository.get_user(user_id)
        if user is None:
            raise BusinessError("المستخدم غير موجود.")
        permissions = self.repository.list_permissions(active_only=True)
        role_map, role_passwords = self._active_role_maps(user)
        user_map = self.repository.get_user_permission_map(user_id)
        effective = self.resolve_user(user)
        passwords = self.repository.get_user_password_ids(user_id)
        out = {}
        for p in permissions:
            pid = int(p["id"])
            state = INHERIT if pid not in user_map else (ALLOW if user_map[pid] else DENY)
            out[pid] = {"role": bool(role_map.get(pid, False)), "override": state,
                        "effective": effective[pid]["allowed"], "source": effective[pid]["source"],
                        "has_password": pid in passwords, "role_has_password": pid in role_passwords}
        return out

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        return self.repository.get_user(user_id)

    def list_users_for_permissions(self, keyword: str = "") -> list[dict[str, Any]]:
        """قائمة شاشة «صلاحيات المستخدم»: المستخدمين + عدد تخصيصات كل واحد."""
        counts = self.repository.override_counts()
        rows = self.repository.list_users(keyword)
        for row in rows:
            c = counts.get(int(row["id"]), {"allow": 0, "deny": 0})
            row["overrides_allow"], row["overrides_deny"] = c["allow"], c["deny"]
            row["overrides_count"] = c["allow"] + c["deny"]
        return rows

    def save_user_permissions(self, user_id: int, states: dict[int, str],
                              passwords: dict[int, str] | None = None,
                              expected_version: int | None = None) -> None:
        """شاشة «صلاحيات المستخدم»: التخصيصات (وراثة/سماح/منع) + كلمات سر الإجراءات في **عملية واحدة**.

        أي إجراء حساس (حذف الكل / إظهار الإجماليات) هيبقى مسموح للمستخدم لازم يبقى له كلمة سر —
        بتاعته هو، أو بتاعة مجموعته لو جاي منها بالوراثة — وإلا مفيش حاجة بتتحفظ.
        """
        self.require("sys.user_permissions.edit")
        self.require("sys.user_permissions.save")
        mapping = {INHERIT: None, ALLOW: True, DENY: False}
        if any(s not in mapping for s in states.values()):
            raise BusinessError("قيمة تخصيص غير معروفة.")
        hashed = {int(pid): self._hash_action_password(pw) for pid, pw in (passwords or {}).items()
                  if states.get(int(pid)) == ALLOW}
        with self.db.atomic() as tx:
            user = self.repository.get_user(user_id, ex=tx, for_update=True)
            self.guard.check(tx, user, expected_version, entity=USER_ENTITY, entity_id=user_id,
                             field="permissions_version", actions=USER_PERMISSION_ACTIONS)
            if user is None:
                raise BusinessError("المستخدم غير موجود.")
            if user.get("is_admin") or user.get("full_access"):
                raise BusinessError("المستخدم ده مدير/صلاحية كاملة — كل حاجة مسموحة له ومفيش تخصيص.")
            perms = {int(p["id"]): p for p in self.repository.list_permissions(False, ex=tx)}
            before = self.repository.get_user_permission_map(user_id, ex=tx)
            before_pw = self.repository.get_user_password_ids(user_id, ex=tx)
            for pid, state in states.items():
                if int(pid) in perms:
                    self.repository.set_user_permission(user_id, int(pid), mapping[state], ex=tx)
            for pid, password_hash in hashed.items():
                self.repository.set_user_permission_password(user_id, pid, password_hash, ex=tx)
            after = self.repository.get_user_permission_map(user_id, ex=tx)
            user_pw = self.repository.get_user_password_ids(user_id, ex=tx)
            role_map, role_pw = self._active_role_maps(user, tx)
            missing = set()
            for pid, perm in perms.items():
                if not perm["is_active"] or perm["action_code"] not in reg.PASSWORD_ACTIONS:
                    continue
                if pid in after:
                    if after[pid] and pid not in user_pw and not (role_map.get(pid) and pid in role_pw):
                        missing.add(perm["action_name_ar"])
                elif role_map.get(pid) and pid not in role_pw:
                    missing.add(perm["action_name_ar"])
            if missing:
                raise BusinessError("مااتحفظش — لازم كلمة سر لـ: " + "، ".join(f"«{m}»" for m in sorted(missing)))
            if before != after or hashed:
                by_id = {pid: p["permission_code"] for pid, p in perms.items()}

                def describe(m: dict[int, bool]) -> dict[str, str]:
                    return {by_id[pid]: (ALLOW if ok else DENY) for pid, ok in m.items() if pid in by_id}

                extra: dict[str, Any] = {"overrides_before": describe(before), "overrides_after": describe(after)}
                if hashed:
                    extra["action_password_for"] = sorted(by_id[pid] for pid in hashed)
                touched = self.repository.touch_user_permissions(user_id, tx, expected_version)
                self.guard.ensure(tx, touched, expected_version, entity=USER_ENTITY, entity_id=user_id,
                                  actions=USER_PERMISSION_ACTIONS)
                self.audit.record(tx, action="permissions", entity="sec.app_users", entity_id=user_id,
                                  entity_label="مستخدم", record_label=user["username"], extra=extra)

    # ---- المجموعات ---------------------------------------------------------
    def list_roles(self, keyword: str = "", active_only: bool = False) -> list[dict[str, Any]]:
        return self.repository.list_roles(keyword, active_only)

    def get_role(self, role_id: int) -> dict[str, Any] | None:
        return self.repository.get_role(role_id)

    def list_role_users(self, role_id: int) -> list[dict[str, Any]]:
        return self.repository.list_role_users(role_id)

    @staticmethod
    def _clean_role(data: dict[str, Any]) -> dict[str, Any]:
        clean = {
            "role_code": (data.get("role_code") or "").strip(),
            "role_name_ar": (data.get("role_name_ar") or "").strip(),
            "role_name_en": (data.get("role_name_en") or "").strip() or None,
            "description": (data.get("description") or "").strip() or None,
            "is_active": bool(data.get("is_active", True)),
        }
        if not clean["role_code"]:
            raise BusinessError("كود المجموعة مطلوب.")
        if not clean["role_name_ar"]:
            raise BusinessError("اسم المجموعة بالعربي مطلوب.")
        return clean

    def create_role(self, data: dict[str, Any]) -> int:
        self.require("sys.roles.create")
        clean = self._clean_role(data)
        if self.repository.get_role_by_code(clean["role_code"]):
            raise BusinessError("كود المجموعة مستخدم قبل كده.")
        with self.db.atomic() as tx:
            role_id = self.repository.create_role(clean, ex=tx)
            self.audit.record(tx, action="create", entity="sec.roles", entity_id=role_id,
                              entity_label="مجموعة صلاحيات", record_label=clean["role_name_ar"],
                              after=self.repository.get_role(role_id, ex=tx))
        return role_id

    def update_role(self, role_id: int, data: dict[str, Any], expected_version: int | None = None) -> None:
        self.require("sys.roles.edit")
        clean = self._clean_role(data)
        other = self.repository.get_role_by_code(clean["role_code"])
        if other and int(other["id"]) != int(role_id):
            raise BusinessError("كود المجموعة مستخدم قبل كده.")
        with self.db.atomic() as tx:
            before = self.repository.get_role_for_update(role_id, tx)
            self.guard.check(tx, before, expected_version, entity=ROLE_ENTITY, entity_id=role_id)
            if before is None:
                raise BusinessError("المجموعة غير موجودة.")
            updated = self.repository.update_role(role_id, clean, ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ROLE_ENTITY, entity_id=role_id)
            self.audit.record(tx, action="update", entity="sec.roles", entity_id=role_id,
                              entity_label="مجموعة صلاحيات", record_label=clean["role_name_ar"],
                              before=before, after=self.repository.get_role(role_id, ex=tx))

    def save_role(self, role_id: int | None, data: dict[str, Any], allowed_map: dict[int, bool],
                  passwords: dict[int, str] | None = None, expected_version: int | None = None) -> int:
        """شاشة «مجموعات الصلاحيات»: بيانات المجموعة + صلاحياتها + كلمات سر الإجراءات في **عملية واحدة**.

        أي إجراء حساس (حذف الكل / إظهار الإجماليات) متعلّم عليه لازم يبقى له كلمة سر (قديمة أو جديدة)،
        وإلا مفيش حاجة بتتحفظ.
        """
        self.require("sys.roles.save")
        self.require("sys.roles.create" if role_id is None else "sys.roles.edit")
        clean = self._clean_role(data)
        other = self.repository.get_role_by_code(clean["role_code"])
        if other and (role_id is None or int(other["id"]) != int(role_id)):
            raise BusinessError("كود المجموعة مستخدم قبل كده.")
        hashed = {int(pid): self._hash_action_password(pw) for pid, pw in (passwords or {}).items()
                  if allowed_map.get(int(pid))}
        with self.db.atomic() as tx:
            perms = {int(p["id"]): p for p in self.repository.list_permissions(False, ex=tx)}
            role_changed = True                   # مجموعة جديدة أو اتعدّلت ← الإصدار زاد لوحده
            if role_id is None:
                role_id = self.repository.create_role(clean, ex=tx)
                self.audit.record(tx, action="create", entity="sec.roles", entity_id=role_id,
                                  entity_label="مجموعة صلاحيات", record_label=clean["role_name_ar"],
                                  after=self.repository.get_role(role_id, ex=tx))
            else:
                before_role = self.repository.get_role_for_update(role_id, tx)
                self.guard.check(tx, before_role, expected_version, entity=ROLE_ENTITY, entity_id=role_id)
                if before_role is None:
                    raise BusinessError("المجموعة غير موجودة.")
                updated = self.repository.update_role(role_id, clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ROLE_ENTITY, entity_id=role_id)
                after_role = self.repository.get_role(role_id, ex=tx)
                role_changed = snapshot_changed(before_role, after_role)
                if role_changed:
                    self.audit.record(tx, action="update", entity="sec.roles", entity_id=role_id,
                                      entity_label="مجموعة صلاحيات", record_label=clean["role_name_ar"],
                                      before=before_role, after=after_role)
            before = self._codes_of(self.repository.get_role_permission_map(role_id, ex=tx), tx)
            self.repository.save_role_permissions(role_id, {int(k): bool(v) for k, v in allowed_map.items()}, ex=tx)
            for pid, password_hash in hashed.items():
                self.repository.set_role_permission_password(role_id, pid, password_hash, ex=tx)
            after_map = self.repository.get_role_permission_map(role_id, ex=tx)
            with_password = self.repository.get_role_password_ids(role_id, ex=tx)
            missing = sorted({perms[pid]["action_name_ar"] for pid, ok in after_map.items()
                              if ok and pid in perms and perms[pid]["action_code"] in reg.PASSWORD_ACTIONS
                              and pid not in with_password})
            if missing:
                raise BusinessError("مااتحفظش — لازم كلمة سر لـ: " + "، ".join(f"«{m}»" for m in missing))
            after = self._codes_of(after_map, tx)
            if before != after or hashed:
                extra: dict[str, Any] = {"added": sorted(set(after) - set(before)),
                                         "removed": sorted(set(before) - set(after))}
                if hashed:
                    extra["action_password_for"] = sorted(perms[pid]["permission_code"] for pid in hashed)
                if not role_changed:              # الصلاحيات بس اتغيّرت ← إصدار المجموعة يزيد برضه
                    self.repository.touch_role(role_id, ex=tx)
                self.audit.record(tx, action="permissions", entity="sec.roles", entity_id=role_id,
                                  entity_label="مجموعة صلاحيات", record_label=clean["role_name_ar"], extra=extra)
        return int(role_id)

    def sync_permissions(self) -> dict[str, Any]:
        """زرار «مزامنة الصلاحيات» (بيحصل لوحده كمان وقت فتح البرنامج)."""
        self.require("sys.roles.save")
        from app.services.permissions_sync_service import PermissionsSyncService

        return PermissionsSyncService(self.db, self.repository).sync()

    def delete_role(self, role_id: int, expected_version: int | None = None) -> None:
        self.require("sys.roles.delete")
        if self.repository.count_role_users(role_id):
            raise BusinessError("مينفعش تحذف مجموعة فيها مستخدمين — انقلهم لمجموعة تانية أو أوقف المجموعة.")
        with self.db.atomic() as tx:
            before = self.repository.get_role_for_update(role_id, tx)
            self.guard.check(tx, before, expected_version, entity=ROLE_ENTITY, entity_id=role_id)
            if before is None:
                raise BusinessError("المجموعة غير موجودة.")
            grants = self._codes_of(self.repository.get_role_permission_map(role_id, ex=tx), tx)
            deleted = self.repository.delete_role(role_id, ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ROLE_ENTITY, entity_id=role_id)
            self.audit.record(tx, action="delete", entity="sec.roles", entity_id=role_id,
                              entity_label="مجموعة صلاحيات", record_label=before["role_name_ar"],
                              before=before, extra={"granted": grants})

    def _codes_of(self, allowed_map: dict[int, bool], ex) -> list[str]:
        by_id = {int(p["id"]): p["permission_code"] for p in self.repository.list_permissions(False, ex=ex)}
        return sorted(by_id[pid] for pid, ok in allowed_map.items() if ok and pid in by_id)

    # ---- صلاحيات المجموعة --------------------------------------------------
    def get_role_permission_map(self, role_id: int) -> dict[int, bool]:
        return self.repository.get_role_permission_map(role_id)

    def save_role_permissions(self, role_id: int, allowed_map: dict[int, bool]) -> None:
        self.require("sys.roles.save")
        with self.db.atomic() as tx:
            role = self.repository.get_role(role_id, ex=tx)
            if role is None:
                raise BusinessError("المجموعة غير موجودة.")
            before = self._codes_of(self.repository.get_role_permission_map(role_id, ex=tx), tx)
            self.repository.save_role_permissions(role_id, allowed_map, ex=tx)
            after = self._codes_of(self.repository.get_role_permission_map(role_id, ex=tx), tx)
            if before != after:
                self.repository.touch_role(role_id, ex=tx)
                self.audit.record(tx, action="permissions", entity="sec.roles", entity_id=role_id,
                                  entity_label="مجموعة صلاحيات", record_label=role["role_name_ar"],
                                  extra={"added": sorted(set(after) - set(before)),
                                         "removed": sorted(set(before) - set(after))})

    # ---- تخصيص المستخدم ----------------------------------------------------
    def get_user_permission_map(self, user_id: int) -> dict[int, bool]:
        return self.repository.get_user_permission_map(user_id)

    def set_user_overrides(self, user_id: int, states: dict[int, str]) -> None:
        """states: {permission_id: inherit/allow/deny} — بيتحفظوا كلهم مع بعض."""
        self.require("sys.user_permissions.save")
        mapping = {INHERIT: None, ALLOW: True, DENY: False}
        if any(s not in mapping for s in states.values()):
            raise BusinessError("قيمة تخصيص غير معروفة.")
        with self.db.atomic() as tx:
            user = self.repository.get_user(user_id, ex=tx)
            if user is None:
                raise BusinessError("المستخدم غير موجود.")
            before = self.repository.get_user_permission_map(user_id, ex=tx)
            for pid, state in states.items():
                self.repository.set_user_permission(user_id, int(pid), mapping[state], ex=tx)
            after = self.repository.get_user_permission_map(user_id, ex=tx)
            if before != after:
                self.repository.touch_user_permissions(user_id, ex=tx)
                self._audit_overrides(tx, user, before, after)

    def reset_user_overrides(self, user_id: int) -> None:
        self.require("sys.user_permissions.save")
        with self.db.atomic() as tx:
            user = self.repository.get_user(user_id, ex=tx)
            if user is None:
                raise BusinessError("المستخدم غير موجود.")
            before = self.repository.get_user_permission_map(user_id, ex=tx)
            self.repository.reset_user_overrides(user_id, ex=tx)
            if before:
                self.repository.touch_user_permissions(user_id, ex=tx)
                self._audit_overrides(tx, user, before, {})

    def _audit_overrides(self, tx, user, before: dict[int, bool], after: dict[int, bool]) -> None:
        by_id = {int(p["id"]): p["permission_code"] for p in self.repository.list_permissions(False, ex=tx)}

        def describe(m: dict[int, bool]) -> dict[str, str]:
            return {by_id[pid]: (ALLOW if ok else DENY) for pid, ok in m.items() if pid in by_id}

        self.audit.record(tx, action="permissions", entity="sec.app_users", entity_id=user["id"],
                          entity_label="مستخدم", record_label=user["username"],
                          extra={"overrides_before": describe(before), "overrides_after": describe(after)})

    # ---- كلمة سر الإجراء (حذف الكل / إظهار الإجماليات) -----------------------
    @staticmethod
    def _hash_action_password(password: str) -> str:
        password = (password or "").strip()
        if not password:
            raise BusinessError("كلمة السر مطلوبة.")
        return PasswordHasher().hash(password)

    def _check_password_action(self, permission_id: int, ex=None) -> dict:
        perm = next((p for p in self.repository.list_permissions(False, ex=ex) if int(p["id"]) == int(permission_id)), None)
        if perm is None:
            raise BusinessError("الصلاحية غير موجودة.")
        if perm["action_code"] not in reg.PASSWORD_ACTIONS:
            raise BusinessError("الصلاحية دي مالهاش كلمة سر إجراء.")
        return perm

    def get_role_password_ids(self, role_id: int) -> set[int]:
        return self.repository.get_role_password_ids(role_id)

    def set_role_action_password(self, role_id: int, permission_id: int, password: str) -> None:
        self.require("sys.roles.save")
        hashed = self._hash_action_password(password)
        with self.db.atomic() as tx:
            perm = self._check_password_action(permission_id, tx)
            role = self.repository.get_role(role_id, ex=tx)
            if role is None:
                raise BusinessError("المجموعة غير موجودة.")
            self.repository.set_role_permission_password(role_id, permission_id, hashed, ex=tx)
            self.repository.touch_role(role_id, ex=tx)
            self.audit.record(tx, action="password", entity="sec.roles", entity_id=role_id,
                              entity_label="مجموعة صلاحيات", record_label=role["role_name_ar"],
                              extra={"action_password_for": perm["permission_code"]})

    def get_user_password_ids(self, user_id: int) -> set[int]:
        return self.repository.get_user_password_ids(user_id)

    def set_user_action_password(self, user_id: int, permission_id: int, password: str) -> None:
        self.require("sys.user_permissions.save")
        hashed = self._hash_action_password(password)
        with self.db.atomic() as tx:
            perm = self._check_password_action(permission_id, tx)
            user = self.repository.get_user(user_id, ex=tx)
            if user is None:
                raise BusinessError("المستخدم غير موجود.")
            self.repository.set_user_permission_password(user_id, permission_id, hashed, ex=tx)
            self.repository.touch_user_permissions(user_id, ex=tx)
            self.audit.record(tx, action="password", entity="sec.app_users", entity_id=user_id,
                              entity_label="مستخدم", record_label=user["username"],
                              extra={"action_password_for": perm["permission_code"]})

    def set_user_delete_all_password(self, user_id: int, password: str) -> int:
        """كلمة سر «حذف الكل» لكل الشاشات اللي فيها الإجراء ده مرة واحدة. بيرجع العدد."""
        self.require("sys.user_permissions.save")
        hashed = self._hash_action_password(password)
        with self.db.atomic() as tx:
            user = self.repository.get_user(user_id, ex=tx)
            if user is None:
                raise BusinessError("المستخدم غير موجود.")
            perms = [p for p in self.repository.list_permissions(True, ex=tx) if p["action_code"] == "delete_all"]
            for p in perms:
                self.repository.set_user_permission_password(user_id, int(p["id"]), hashed, ex=tx)
            if perms:
                self.repository.touch_user_permissions(user_id, ex=tx)
                self.audit.record(tx, action="password", entity="sec.app_users", entity_id=user_id,
                                  entity_label="مستخدم", record_label=user["username"],
                                  extra={"action_password_for": [p["permission_code"] for p in perms]})
        return len(perms)

    def verify_action_password(self, user: dict[str, Any], permission_code: str, password: str) -> bool | None:
        """True/False = صح/غلط؛ None = مفيش كلمة سر متحطة للصلاحية دي.
        الأولوية: كلمة سر المستخدم نفسه، وبعدين كلمة سر مجموعته (لو مش ممنوعة عليه)."""
        row = self.repository.get_action_password_hashes(int(user["id"]), user.get("role_id"), permission_code)
        stored = None
        if row.get("u_hash") and row.get("u_allowed") is not False:
            stored = row["u_hash"]
        elif row.get("r_hash") and row.get("r_allowed") and row.get("u_allowed") is not False:
            stored = row["r_hash"]
        if not stored:
            return None
        return PasswordHasher().verify(password or "", stored)
