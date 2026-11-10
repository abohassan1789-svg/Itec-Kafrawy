"""اختبارات شاشة «مجموعات الصلاحيات» (النموذج 3) + محرر الصلاحيات + ``save_role``.

* المحرر لوحده (من غير قاعدة): الحالة التلاتية، القسم كله، إلغاء كلمة السر بيشيل العلامة، البحث.
* الخدمة على قاعدة مؤقتة: عملية واحدة، رفض كلمة السر الناقصة من غير ما يتحفظ أي حاجة، الـ audit.
* الشاشة على قاعدة مؤقتة: العرض، إضافة مجموعة بصلاحياتها، التعديل/الإلغاء، الحذف، المزامنة، الصلاحيات.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services import permission_registry as reg
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService, group_like_sidebar, matrix_rows
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.user_service import UserService
from app.ui.common import messages

FAST = PasswordHasher(1_000)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# المحرر لوحده (صلاحيات وهمية فيها «حذف الكل» و«إظهار الإجماليات»)
# ---------------------------------------------------------------------------
def fake_permissions() -> list[dict]:
    """قسم «الحسابات» فيه شاشتين (القيود: عرض/حفظ/حذف الكل، الدليل: عرض/إظهار الإجماليات)."""
    rows, pid = [], 0
    for target, name, actions in (("journal", "القيود اليومية", ("view", "save", "delete_all")),
                                  ("coa", "دليل الحسابات", ("view", "show_totals"))):
        for action in actions:
            pid += 1
            rows.append({"id": pid, "permission_type": "screen", "module_code": "zz", "module_name_ar": "تجربة",
                         "target_code": target, "target_name_ar": name, "target_name_en": target,
                         "action_code": action})
    return rows


class Asker:
    """بديل نافذة كلمة السر: بيرجّع القيم بالترتيب (None = إلغاء) وبيسجل كل سؤال."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked: list[tuple[str, str]] = []

    def __call__(self, where, action):
        self.asked.append((where, action))
        return self.answers.pop(0) if self.answers else "pw"


def make_editor(asker):
    from app.ui.common.permission_tree import PermissionTreeEditor

    editor = PermissionTreeEditor(asker)
    editor.set_entries(group_like_sidebar(matrix_rows(fake_permissions())))
    editor.load({}, set())
    editor.set_editable(True)
    return editor


def items_by_text(editor):
    out = {}
    stack = [editor.tree.topLevelItem(i) for i in range(editor.tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        out[item.text(0).split("  (")[0].strip()] = item
        stack.extend(item.child(i) for i in range(item.childCount()))
    return out


def test_editor_tree_follows_sections_and_tristate(qapp):
    editor = make_editor(Asker())
    items = items_by_text(editor)
    assert "أخرى" in items                                  # قسم مش في السجل ← «أخرى»
    section, journal, coa = items["أخرى"], items["القيود اليومية"], items["دليل الحسابات"]
    assert section.childCount() == 2 and editor.summary.text() == "الصلاحيات الممنوحة: 0 من 5"

    editor.tree.setCurrentItem(journal)                      # خانات الشاشة المختارة بس
    assert set(editor.action_checks) == {"view", "save", "delete_all"}
    editor.action_checks["view"].setChecked(True)
    assert journal.checkState(0) == Qt.PartiallyChecked      # بعضها ← نص
    assert section.checkState(0) == Qt.PartiallyChecked
    assert coa.checkState(0) == Qt.Unchecked
    editor.action_checks["view"].setChecked(False)
    assert journal.checkState(0) == Qt.Unchecked and section.checkState(0) == Qt.Unchecked


def test_editor_section_check_asks_password_once_per_action(qapp):
    asker = Asker("p1", "p2")
    editor = make_editor(asker)
    section = items_by_text(editor)["أخرى"]
    section.setCheckState(0, Qt.Checked)                      # القسم كله مرة واحدة
    assert all(editor.allowed.values()) and section.checkState(0) == Qt.Checked
    assert [a for _w, a in asker.asked] == ["حذف الكل", "إظهار الإجماليات"]
    assert sorted(editor.pending_passwords.values()) == ["p1", "p2"]
    assert editor.missing_passwords() == {}
    assert editor.summary.text() == "الصلاحيات الممنوحة: 5 من 5"

    section.setCheckState(0, Qt.Unchecked)                    # الشيل بيمسح كلمات السر الجديدة
    assert not any(editor.allowed.values()) and editor.pending_passwords == {}


def test_editor_cancel_password_removes_check(qapp):
    asker = Asker(None)                                        # المستخدم داس «إلغاء»
    editor = make_editor(asker)
    journal = items_by_text(editor)["القيود اليومية"]
    journal.setCheckState(0, Qt.Checked)
    delete_all = next(p["id"] for p in fake_permissions() if p["action_code"] == "delete_all")
    assert not editor.allowed[delete_all]                      # من غير كلمة سر ← مايتعلّمش
    assert journal.checkState(0) == Qt.PartiallyChecked        # الباقي اتعلّم
    editor.tree.setCurrentItem(journal)
    assert not editor.action_checks["delete_all"].isChecked()

    editor.action_checks["delete_all"].setChecked(True)        # المرة دي بكلمة سر
    assert editor.allowed[delete_all] and editor.pending_passwords[delete_all] == "pw"
    assert journal.checkState(0) == Qt.Checked


def test_editor_existing_password_not_asked_and_view_mode_locked(qapp):
    asker = Asker()
    editor = make_editor(asker)
    delete_all = next(p["id"] for p in fake_permissions() if p["action_code"] == "delete_all")
    editor.load({}, {delete_all})                              # متحفظلها كلمة سر قبل كده
    editor.set_all(True)
    assert [a for _w, a in asker.asked] == ["إظهار الإجماليات"]
    assert delete_all not in editor.pending_passwords

    editor.set_editable(False)                                  # وضع العرض: مفيش تعديل
    editor.set_all(False)
    assert all(editor.allowed.values())
    assert not editor.check_all_button.isEnabled()
    editor.tree.setCurrentItem(items_by_text(editor)["القيود اليومية"])
    assert not editor.action_checks["view"].isEnabled()


def test_editor_search_hides_other_screens(qapp):
    editor = make_editor(Asker())
    items = items_by_text(editor)
    editor.search.setText("دليل")
    assert items["القيود اليومية"].isHidden() and not items["دليل الحسابات"].isHidden()
    assert not items["أخرى"].isHidden()
    editor.search.clear()
    assert not items["القيود اليومية"].isHidden()


# ---------------------------------------------------------------------------
# الخدمة والشاشة على قاعدة مؤقتة
# ---------------------------------------------------------------------------
@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    answers = {"save": True, "passwords": []}
    monkeypatch.setattr(messages, "ask_save", lambda _p: answers["save"])
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _x: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(str(exc)))

    from app.ui.dialogs import action_password_dialog

    def fake_ask(_parent, _where, _action="حذف الكل"):
        return answers["passwords"].pop(0) if answers["passwords"] else "pw"

    monkeypatch.setattr(action_password_dialog, "ask_new_action_password", fake_ask)
    yield {"db": temp_db, "shown": shown, "answers": answers}
    SESSION.clear()


def add_delete_all_permission(db) -> int:
    """صلاحية «حذف الكل» لشاشة وهمية (شاشات النظام مافيهاش إجراءات بكلمة سر)."""
    from app.repositories.security_repository import SecurityRepository

    repo = SecurityRepository(db)
    repo.upsert_permission({
        "permission_code": "acc.journal.delete_all", "permission_type": "screen", "module_code": "acc",
        "module_name_ar": "الحسابات", "module_name_en": "Accounts", "category_ar": "الشاشات",
        "category_en": "Screens", "target_code": "journal", "target_name_ar": "القيود اليومية",
        "target_name_en": "Journal", "action_code": "delete_all", "action_name_ar": "حذف الكل",
        "action_name_en": "Delete All", "sort_order": 999})
    return next(int(p["id"]) for p in repo.list_permissions() if p["permission_code"] == "acc.journal.delete_all")


def pid_of(perms: PermissionService, code: str) -> int:
    return next(int(p["id"]) for p in perms.repository.list_permissions() if p["permission_code"] == code)


def audit_rows(db, role_id):
    return db.fetch_all("SELECT * FROM sec.audit_log WHERE entity='sec.roles' AND entity_id=%s ORDER BY id",
                        [str(role_id)])


def test_save_role_is_one_transaction_with_audit(env):
    db = env["db"]
    perms = PermissionService(db)
    delete_all = add_delete_all_permission(db)
    view = pid_of(perms, "sys.users.view")

    # من غير كلمة سر ← رفض ومفيش حاجة اتحفظت خالص (لا المجموعة ولا الصلاحيات ولا الـ audit).
    with pytest.raises(BusinessError, match="حذف الكل"):
        perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"}, {view: True, delete_all: True})
    assert perms.list_roles() == []
    assert db.fetch_all("SELECT * FROM sec.audit_log WHERE entity='sec.roles'") == []

    role_id = perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"},
                              {view: True, delete_all: True}, {delete_all: "secret"})
    assert perms.get_role_permission_map(role_id) == {view: True, delete_all: True}
    assert perms.get_role_password_ids(role_id) == {delete_all}
    history = audit_rows(db, role_id)
    assert [h["action"] for h in history] == ["create", "permissions"]
    extra = history[1]["details"]["extra"]
    assert extra["added"] == ["acc.journal.delete_all", "sys.users.view"]
    assert extra["action_password_for"] == ["acc.journal.delete_all"]
    assert "secret" not in str(history)                       # كلمة السر عمرها ما تتسجل

    # من غير تغيير ← مفيش سطر audit جديد · الصلاحية القديمة بكلمة سرها تفضل.
    perms.save_role(role_id, {"role_code": "ACC", "role_name_ar": "المحاسبين"}, {view: True, delete_all: True})
    assert len(audit_rows(db, role_id)) == 2

    # شيل الصلاحية بيمسح كلمة سرها، والاسم الجديد بيتسجل «update».
    perms.save_role(role_id, {"role_code": "ACC", "role_name_ar": "الحسابات"}, {view: True, delete_all: False})
    assert perms.get_role_password_ids(role_id) == set()
    assert [h["action"] for h in audit_rows(db, role_id)][-2:] == ["update", "permissions"]

    # كود متكرر.
    with pytest.raises(BusinessError, match="مستخدم قبل"):
        perms.save_role(None, {"role_code": "acc", "role_name_ar": "تاني"}, {})


def test_save_role_requires_permissions(env):
    db = env["db"]
    perms = PermissionService(db)
    role = perms.create_role({"role_code": "V", "role_name_ar": "عرض بس"})
    perms.save_role_permissions(role, {pid_of(perms, "sys.roles.view"): True})
    UserService(db, hasher=FAST).create_user({"username": "viewer", "role_id": role}, "pw")
    SESSION.clear()
    assert AuthService(db, hasher=FAST).login("viewer", "pw").ok
    from app.services.errors import PermissionDenied

    with pytest.raises(PermissionDenied):
        perms.save_role(None, {"role_code": "X", "role_name_ar": "س"}, {})
    with pytest.raises(PermissionDenied):
        perms.sync_permissions()


def make_screen(db):
    from app.ui.screens.roles_screen import RolesScreen

    return RolesScreen(PermissionService(db))


def tree_item(screen, key):
    return screen.editor._items[key]


def test_screen_is_registered_and_view_mode(env):
    from app.ui.screen_registry import SCREEN_FACTORIES

    assert "roles" in SCREEN_FACTORIES
    perms = PermissionService(env["db"])
    role = perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"},
                           {pid_of(perms, "sys.users.view"): True})
    screen = make_screen(env["db"])
    assert screen.table.rowCount() == 1 and screen.count_label.text() == "عدد السجلات المعروضة: 1"
    screen.table.select_index(0)
    assert screen.current_id == role and screen.role_name_ar.text() == "المحاسبين"
    assert screen.mode == "view" and not screen.role_code.isEnabled()
    assert not screen.editor.editable and not screen.editor.check_all_button.isEnabled()
    assert tree_item(screen, "sys.users").checkState(0) == Qt.PartiallyChecked
    assert screen.editor.summary.text().startswith("الصلاحيات الممنوحة: 1 من ")
    assert screen.sync_button.isEnabled() and not screen.delete_button.isHidden()
    assert screen.table.rows[0]["users_count"] == 0 and screen.format_cell("is_active", True, {}) == "نشطة"


def test_create_role_with_permissions_from_screen(env):
    db = env["db"]
    screen = make_screen(db)
    screen.new_record()
    assert screen.mode == "new" and screen.editor.editable and screen.is_active.isChecked()
    assert not screen.sync_button.isEnabled()                  # المزامنة في العرض بس
    screen.role_code.setText("SAL")
    screen.role_name_ar.setText("المبيعات")
    tree_item(screen, "sys.users").setCheckState(0, Qt.Checked)   # الشاشة كلها

    env["answers"]["save"] = False                             # «هل تريد الحفظ؟» ← لا
    screen.save()
    assert screen.mode == "new" and PermissionService(db).list_roles() == []

    env["answers"]["save"] = True
    screen.save()
    assert env["shown"][-1] == "saved" and screen.mode == "view"
    perms = PermissionService(db)
    (role,) = perms.list_roles()
    users_pids = {int(p["id"]) for p in perms.repository.list_permissions() if p["target_code"] == "users"}
    assert {pid for pid, ok in perms.get_role_permission_map(role["id"]).items() if ok} == users_pids
    assert tree_item(screen, "sys.users").checkState(0) == Qt.Checked
    assert screen.table.current_row_data()["role_code"] == "SAL"


def test_screen_password_prompt_for_missing_on_save(env):
    """«تحديد الكل» على صلاحية بكلمة سر اتلغت ← وقت الحفظ بيتسأل تاني، والإلغاء يرفض الحفظ."""
    db = env["db"]
    delete_all = add_delete_all_permission(db)
    screen = make_screen(db)
    screen.new_record()
    screen.role_code.setText("ACC")
    screen.role_name_ar.setText("المحاسبين")
    screen.editor.set_all(True)
    assert delete_all in screen.editor.pending_passwords
    screen.editor.pending_passwords.clear()                    # كأنها اتعلّمت من غير كلمة سر

    from app.ui.dialogs import action_password_dialog

    original = action_password_dialog.ask_new_action_password
    action_password_dialog.ask_new_action_password = lambda *_a, **_k: None
    try:
        screen.save()
    finally:
        action_password_dialog.ask_new_action_password = original
    assert "لازم يكون له كلمة سر" in env["shown"][-1] and screen.mode == "new"
    assert PermissionService(db).list_roles() == []

    env["answers"]["passwords"] = ["s3"]
    screen.save()
    assert env["shown"][-1] == "saved"
    role_id = PermissionService(db).list_roles()[0]["id"]
    assert delete_all in PermissionService(db).get_role_password_ids(role_id)


def test_edit_cancel_delete_and_sync(env):
    db = env["db"]
    perms = PermissionService(db)
    role = perms.save_role(None, {"role_code": "TMP", "role_name_ar": "مؤقتة"}, {})
    screen = make_screen(db)
    screen.load(role)
    screen.edit_record()
    assert screen.mode == "edit" and screen.role_code.isEnabled()
    screen.role_name_ar.setText("اسم اتلغى")
    tree_item(screen, "sys.roles").setCheckState(0, Qt.Checked)
    screen.cancel()
    assert screen.role_name_ar.text() == "مؤقتة"
    assert tree_item(screen, "sys.roles").checkState(0) == Qt.Unchecked
    assert perms.get_role_permission_map(role) == {}

    screen.sync_permissions()
    assert "إجمالي الصلاحيات" in env["shown"][-1]

    screen.delete()
    assert perms.list_roles() == [] and screen.current_id is None and screen.table.rowCount() == 0


def test_cannot_delete_role_with_users(env):
    db = env["db"]
    perms = PermissionService(db)
    role = perms.save_role(None, {"role_code": "R", "role_name_ar": "فيها ناس"}, {})
    UserService(db, hasher=FAST).create_user({"username": "ali", "role_id": role}, "pw")
    screen = make_screen(db)
    screen.load(role)
    screen.delete()
    assert "فيها مستخدمين" in env["shown"][-1] and len(perms.list_roles()) == 1


def test_own_role_change_reloads_session(env):
    db = env["db"]
    perms = PermissionService(db)
    view = pid_of(perms, "sys.roles.view")
    edit_codes = ("sys.roles.view", "sys.roles.edit", "sys.roles.save")
    role = perms.save_role(None, {"role_code": "SEC", "role_name_ar": "الأمان"},
                           {pid_of(perms, c): True for c in edit_codes})
    UserService(db, hasher=FAST).create_user({"username": "sec", "role_id": role}, "pw")
    SESSION.clear()
    assert AuthService(db, hasher=FAST).login("sec", "pw").ok
    screen = make_screen(db)
    assert not screen.new_button.isEnabled()                    # مالوش «إضافة»
    screen.load(role)
    screen.edit_record()
    assert screen.mode == "edit"
    tree_item(screen, "sys.users").setCheckState(0, Qt.Checked)
    screen.save()
    assert env["shown"][-1] == "saved"
    assert SESSION.can("sys.users.view") and SESSION.can("sys.roles.view")
    assert perms.get_role_permission_map(role)[view]
