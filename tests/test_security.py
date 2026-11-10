"""اختبارات نظام الأمان والصلاحيات (المرحلة 1)."""

from __future__ import annotations

import psycopg
import pytest

from app.database import migrator
from app.repositories.security_repository import SecurityRepository
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services import permission_registry as reg
from app.services.audit_service import AuditService, snapshot
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, PermissionDenied
from app.services.permission_service import ALLOW, DENY, INHERIT, PermissionService, resolve_effective
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.user_service import UserService

FAST = PasswordHasher(iterations=1_000)   # أسرع في الاختبارات


@pytest.fixture(autouse=True)
def _clear_session():
    SESSION.clear()
    yield
    SESSION.clear()


@pytest.fixture()
def sec_db(temp_db):
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    return temp_db


def login_admin(db):
    result = AuthService(db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD)
    assert result.ok, result.message
    return result.user


def perm_ids(db, *codes):
    rows = {p["permission_code"]: int(p["id"]) for p in SecurityRepository(db).list_permissions()}
    return [rows[c] for c in codes]


def audit_rows(db, entity, entity_id):
    return AuditService(db).history(entity, entity_id)


# ---- بدون قاعدة ---------------------------------------------------------------

def test_password_hasher_roundtrip():
    stored = FAST.hash("سر123")
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert FAST.verify("سر123", stored)
    assert not FAST.verify("غلط", stored)
    assert not FAST.verify("x", None)
    assert not FAST.verify("x", "garbage")
    assert FAST.hash("a") != FAST.hash("a")          # salt مختلف كل مرة


def test_resolve_effective_most_specific_wins():
    perms = [{"id": 1}, {"id": 2}, {"id": 3}]
    role = {1: True, 2: True}
    user = {2: False, 3: True}
    result = resolve_effective(perms, role, user, is_full_access=False)
    assert result[1] == {"allowed": True, "source": "role"}
    assert result[2] == {"allowed": False, "source": "override"}   # المنع يغلب المجموعة
    assert result[3] == {"allowed": True, "source": "override"}    # السماح من غير المجموعة
    full = resolve_effective(perms, {}, {1: False}, is_full_access=True)
    assert all(v["allowed"] for v in full.values())


def test_registry_rows_are_unique_and_well_formed():
    rows = reg.build_permission_rows()
    codes = [r["permission_code"] for r in rows]
    assert len(codes) == len(set(codes))
    for r in rows:
        assert r["permission_code"] == f"{r['module_code']}.{r['target_code']}.{r['action_code']}"
    assert "sys.users.view" in codes and "sys.login_logs.export" in codes
    assert "sys.login_logs.create" not in codes        # سجل الدخول: عرض/تصدير بس
    assert [r["sort_order"] for r in rows] == sorted(r["sort_order"] for r in rows)


def test_session_denies_everything_without_login():
    assert not SESSION.can("sys.users.view")
    SESSION.start({"id": 5, "username": "x", "is_admin": False, "full_access": False,
                   "password_hash": "secret"}, {"sys.users.view"})
    assert SESSION.can("sys.users.view") and not SESSION.can("sys.users.edit")
    assert "password_hash" not in SESSION.user
    SESSION.start({"id": 1, "username": "a", "is_admin": True}, set())
    assert SESSION.can("anything.at.all")


def test_audit_snapshot_hides_secrets():
    snap = snapshot({"id": 1, "username": "u", "password_hash": "h", "photo": b"x", "notes": "n"})
    assert snap == {"id": 1, "username": "u", "notes": "n"}


# ---- القاعدة ------------------------------------------------------------------

def test_migration_creates_security_tables(sec_db):
    tables = {r["tablename"] for r in sec_db.fetch_all("SELECT tablename FROM pg_tables WHERE schemaname='sec'")}
    assert {"roles", "app_users", "permissions", "role_permissions", "user_permissions",
            "user_login_logs", "audit_log"} <= tables


def test_seed_admin_once(sec_db):
    assert AuthService(sec_db).seed_admin() is False
    users = SecurityRepository(sec_db).list_users()
    assert [u["username"] for u in users] == [DEFAULT_ADMIN_USERNAME]
    assert users[0]["is_admin"] and users[0]["full_access"]
    assert users[0]["password_hash"] != DEFAULT_ADMIN_PASSWORD


def test_sync_adds_deactivates_and_keeps_grants(sec_db, monkeypatch):
    repo = SecurityRepository(sec_db)
    total = len(reg.build_permission_rows())
    assert len(repo.list_permissions()) == total
    again = PermissionsSyncService(sec_db).sync()
    assert again["added"] == 0 and again["deactivated"] == 0

    role_id = repo.create_role({"role_code": "R1", "role_name_ar": "مجموعة"})
    (pid,) = perm_ids(sec_db, "sys.users.view")
    repo.save_role_permissions(role_id, {pid: True})

    rows = [r for r in reg.build_permission_rows() if r["target_code"] != "users"]
    monkeypatch.setattr("app.services.permissions_sync_service.build_permission_rows", lambda: rows)
    summary = PermissionsSyncService(sec_db).sync()
    assert summary["deactivated"] > 0
    assert len(repo.list_permissions(active_only=False)) == total            # مااتمسحش حاجة
    assert repo.get_role_permission_map(role_id) == {pid: True}              # المنح زي ما هو


def test_login_success_failure_and_logs(sec_db):
    auth = AuthService(sec_db, hasher=FAST)
    bad = auth.login(DEFAULT_ADMIN_USERNAME, "wrong")
    assert not bad.ok and bad.message == "كلمة المرور غير صحيحة"
    assert not auth.login("nobody", "x").ok
    assert not SESSION.is_authenticated

    ok = auth.login(DEFAULT_ADMIN_USERNAME.upper(), DEFAULT_ADMIN_PASSWORD)   # من غير حساسية حروف
    assert ok.ok and SESSION.is_admin and SESSION.username == DEFAULT_ADMIN_USERNAME
    logs = SecurityRepository(sec_db).list_login_logs()
    assert [l["status"] for l in logs] == ["success", "failed", "failed"]
    assert logs[1]["notes"] == "اسم المستخدم غير موجود"

    log_id = SESSION.login_log_id
    auth.logout()
    assert not SESSION.is_authenticated
    row = sec_db.fetch_one("SELECT logout_time FROM sec.user_login_logs WHERE id=%s", [log_id])
    assert row["logout_time"] is not None


def test_inactive_user_cannot_login(sec_db):
    login_admin(sec_db)
    users = UserService(sec_db, hasher=FAST)
    uid = users.create_user({"username": "ali"}, "pw")
    users.set_user_active(uid, False)
    SESSION.clear()
    result = AuthService(sec_db, hasher=FAST).login("ali", "pw")
    assert not result.ok and "موقوف" in result.message


def test_user_admin_rules_and_audit(sec_db):
    users = UserService(sec_db, hasher=FAST)
    with pytest.raises(PermissionDenied):                  # من غير دخول: ممنوع
        users.create_user({"username": "x"}, "pw")
    login_admin(sec_db)

    uid = users.create_user({"username": "Mona", "full_name": "منى"}, "pw1")
    with pytest.raises(BusinessError, match="مستخدم بالفعل"):
        users.create_user({"username": "mona"}, "pw2")
    with pytest.raises(BusinessError):
        users.create_user({"username": "  "}, "pw")

    users.update_user(uid, {"full_name": "منى علي", "job_title": "محاسبة"}, password="newpw")
    history = audit_rows(sec_db, "sec.app_users", uid)
    assert [h["action"] for h in history] == ["update", "create"]
    upd = history[0]
    assert upd["username"] == DEFAULT_ADMIN_USERNAME
    assert upd["details"]["before"]["full_name"] == "منى"
    assert upd["details"]["after"]["full_name"] == "منى علي"
    assert {"full_name", "job_title"} <= set(upd["details"]["changed"])
    assert upd["details"]["extra"] == {"password_changed": True}
    assert "password_hash" not in str(upd["details"])
    assert AuthService(sec_db, hasher=FAST).authenticate("mona", "newpw")[0] is not None


def test_last_admin_is_protected(sec_db):
    admin = login_admin(sec_db)
    users = UserService(sec_db, hasher=FAST)
    with pytest.raises(BusinessError, match="آخر مستخدم مدير"):
        users.update_user(admin["id"], {"is_admin": False, "full_access": False, "is_active": True})
    with pytest.raises(BusinessError):
        users.set_user_active(admin["id"], False)
    # والقاعدة نفسها بتمنع لو حد عدّل من برّه البرنامج:
    with pytest.raises(psycopg.errors.RaiseException):
        sec_db.execute("UPDATE sec.app_users SET is_active=false")
    assert audit_rows(sec_db, "sec.app_users", admin["id"]) == []      # مفيش سجل لعملية فشلت


def test_non_admin_cannot_escalate(sec_db):
    login_admin(sec_db)
    users = UserService(sec_db, hasher=FAST)
    perms = PermissionService(sec_db)
    role_id = perms.create_role({"role_code": "HR", "role_name_ar": "شؤون"})
    perms.save_role_permissions(role_id, dict.fromkeys(
        perm_ids(sec_db, "sys.users.view", "sys.users.create", "sys.users.edit"), True))
    uid = users.create_user({"username": "clerk", "role_id": role_id}, "pw")
    SESSION.clear()
    assert AuthService(sec_db, hasher=FAST).login("clerk", "pw").ok
    assert SESSION.can("sys.users.create") and not SESSION.can("sys.roles.view")
    with pytest.raises(PermissionDenied):
        users.create_user({"username": "boss", "is_admin": True}, "pw")
    with pytest.raises(PermissionDenied):
        users.update_user(uid, {"role_id": role_id, "full_access": True})
    users.create_user({"username": "helper", "role_id": role_id}, "pw")      # عادي مسموح


def test_roles_permissions_and_overrides(sec_db):
    login_admin(sec_db)
    perms = PermissionService(sec_db)
    users = UserService(sec_db, hasher=FAST)
    view, edit, logs = perm_ids(sec_db, "sys.users.view", "sys.users.edit", "sys.login_logs.view")

    role_id = perms.create_role({"role_code": "acc", "role_name_ar": "المحاسبين"})
    with pytest.raises(BusinessError, match="مستخدم قبل"):
        perms.create_role({"role_code": "ACC", "role_name_ar": "تاني"})
    perms.save_role_permissions(role_id, {view: True, edit: True, logs: False})
    uid = users.create_user({"username": "ahmed", "role_id": role_id}, "pw")
    user = SecurityRepository(sec_db).get_user(uid)
    assert perms.effective_codes(user) == {"sys.users.view", "sys.users.edit"}

    perms.set_user_overrides(uid, {edit: DENY, logs: ALLOW})
    assert perms.effective_codes(user) == {"sys.users.view", "sys.login_logs.view"}
    matrix = perms.user_matrix(uid)
    assert matrix[edit] == {"role": True, "override": DENY, "effective": False,
                            "source": "override", "has_password": False, "role_has_password": False}
    perms.set_user_overrides(uid, {edit: INHERIT})
    assert "sys.users.edit" in perms.effective_codes(user)
    perms.reset_user_overrides(uid)
    assert perms.effective_codes(user) == {"sys.users.view", "sys.users.edit"}

    perms.update_role(role_id, {"role_code": "acc", "role_name_ar": "المحاسبين", "is_active": False})
    assert perms.effective_codes(user) == set()                       # مجموعة موقوفة = ولا حاجة

    actions = [h["action"] for h in audit_rows(sec_db, "sec.roles", role_id)]
    assert actions == ["update", "permissions", "create"]
    granted = audit_rows(sec_db, "sec.roles", role_id)[1]["details"]["extra"]
    assert granted == {"added": ["sys.users.edit", "sys.users.view"], "removed": []}

    with pytest.raises(BusinessError, match="فيها مستخدمين"):
        perms.delete_role(role_id)


def test_grouped_matrix_follows_sidebar(sec_db):
    entries = PermissionService(sec_db).grouped_matrix()
    assert entries[0] == {"kind": "header", "gid": "acc", "level": 0, "title": "📒  الحسابات", "count": 3}
    sys_header = next(e for e in entries if e["kind"] == "header" and e["gid"] == "sys")
    assert sys_header == {"kind": "header", "gid": "sys", "level": 0, "title": "🔐  النظام والصلاحيات", "count": 8}
    targets = [e["row"]["target_code"] for e in entries if e["kind"] == "target"]
    sales_header = next(e for e in entries if e["kind"] == "header" and e["gid"] == "sales")
    assert sales_header["count"] == 1
    assert targets == ["chart_of_accounts", "default_accounts", "cost_centers", "customer_groups", "company", "fiscal_years", "currencies", "tax_codes", "users", "roles", "user_permissions", "login_logs"]


def test_action_password(sec_db, monkeypatch):
    # نضيف شاشة وهمية فيها «حذف الكل» عشان نجرب كلمة سر الإجراء.
    demo = reg.Section("acc", "الحسابات", "Accounts", "📒", groups=(
        reg.Group(None, (reg.Target("journal", "القيود", "Journal"),)),))
    monkeypatch.setattr(reg, "SECTIONS", (demo, *[s for s in reg.SECTIONS if s.id != "acc"]))
    PermissionsSyncService(sec_db).sync()
    admin = login_admin(sec_db)
    perms = PermissionService(sec_db)
    (delete_all, view) = perm_ids(sec_db, "acc.journal.delete_all", "acc.journal.view")

    role_id = perms.create_role({"role_code": "J", "role_name_ar": "قيود"})
    uid = UserService(sec_db, hasher=FAST).create_user({"username": "j", "role_id": role_id}, "pw")
    user = SecurityRepository(sec_db).get_user(uid)

    with pytest.raises(BusinessError, match="مالهاش كلمة سر"):
        perms.set_role_action_password(role_id, view, "x")
    assert perms.verify_action_password(user, "acc.journal.delete_all", "x") is None
    perms.set_role_action_password(role_id, delete_all, "9999")
    assert perms.get_role_password_ids(role_id) == {delete_all}
    assert perms.verify_action_password(user, "acc.journal.delete_all", "9999") is True
    assert perms.verify_action_password(user, "acc.journal.delete_all", "1") is False

    perms.set_user_overrides(uid, {delete_all: DENY})                  # المنع يلغي كلمة سر المجموعة
    assert perms.verify_action_password(user, "acc.journal.delete_all", "9999") is None
    assert perms.set_user_delete_all_password(uid, "7777") == 1        # كلمة سر المستخدم نفسه
    assert perms.verify_action_password(user, "acc.journal.delete_all", "7777") is True

    perms.save_role_permissions(role_id, {delete_all: False})          # سحب الصلاحية يمسح كلمة سرها
    assert perms.get_role_password_ids(role_id) == set()
    assert admin["is_admin"]


def test_audit_is_in_same_transaction_and_append_only(sec_db):
    login_admin(sec_db)
    audit = AuditService(sec_db)
    with pytest.raises(RuntimeError):
        with sec_db.atomic() as tx:
            audit.record(tx, action="create", entity="demo", entity_id=1, after={"a": 1})
            raise RuntimeError("الحفظ فشل")
    assert audit.history("demo", 1) == []                              # رجع مع الحفظ

    with sec_db.atomic() as tx:
        audit.record(tx, action="update", entity="demo", entity_id=2, before={"a": 1}, after={"a": 2},
                     lines={"demo_lines": {"before": [{"n": 1}], "after": [{"n": 1}, {"n": 2}]}})
    (row,) = audit.history("demo", 2)
    assert row["details"]["lines"]["demo_lines"]["after"] == [{"n": 1}, {"n": 2}]
    assert row["details"]["changed"] == ["a"]
    with pytest.raises(psycopg.errors.RaiseException, match="للقراءة بس"):
        sec_db.execute("UPDATE sec.audit_log SET action='delete'")
    with pytest.raises(psycopg.errors.RaiseException):
        sec_db.execute("DELETE FROM sec.audit_log")
