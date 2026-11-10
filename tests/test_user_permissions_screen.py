"""اختبارات شاشة «صلاحيات المستخدم» (النموذج 1) + مصفوفة التخصيص + ``save_user_permissions``."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService, group_like_sidebar, matrix_rows
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.user_service import UserService
from app.ui.common import messages
from app.ui.common.override_matrix import ALLOW, DENY, INHERIT, OverrideMatrixEditor

FAST = PasswordHasher(1_000)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


# ---------------------------------------------------------------------------
# المصفوفة لوحدها
# ---------------------------------------------------------------------------
def fake_permissions() -> list[dict]:
    rows, pid = [], 0
    for target, name, actions in (("journal", "القيود اليومية", ("view", "delete", "delete_all")),
                                  ("coa", "دليل الحسابات", ("view", "show_totals"))):
        for action in actions:
            pid += 1
            rows.append({"id": pid, "permission_type": "screen", "module_code": "zz", "module_name_ar": "تجربة",
                         "target_code": target, "target_name_ar": name, "target_name_en": target,
                         "action_code": action})
    return rows


# 1 القيود/عرض · 2 القيود/حذف · 3 القيود/حذف الكل · 4 الدليل/عرض · 5 الدليل/إظهار الإجماليات
def make_editor(answers=(), info=None, full=False):
    asked = []
    answers = list(answers)

    def ask(where, action):
        asked.append(action)
        return answers.pop(0) if answers else "pw"

    editor = OverrideMatrixEditor(ask)
    editor.set_entries(group_like_sidebar(matrix_rows(fake_permissions())))
    base = {pid: {"role": pid in (1, 2), "override": INHERIT, "has_password": False, "role_has_password": False}
            for pid in range(1, 6)}
    base.update(info or {})
    editor.load(base, full)
    editor.set_editable(True)
    return editor, asked


def cell(editor, pid):
    r, c = editor._cells[pid]
    return editor.table.item(r, c)


def test_matrix_shows_effective_with_colors_and_cycles(qapp):
    editor, asked = make_editor()
    assert editor.table.columnCount() == 1 + 4             # الشاشة + الإجراءات المستخدمة بس
    assert cell(editor, 1).text() == "✔" and cell(editor, 4).text() == "☐"
    assert editor.summary.text() == "التخصيصات: 0 (0 سماح · 0 منع)"

    r, c = editor._cells[1]
    editor._on_cell_clicked(r, c)                            # وراثة ← سماح
    assert editor.states[1] == ALLOW and cell(editor, 1).foreground().color().name() == "#2563eb"
    editor._on_cell_clicked(r, c)                            # سماح ← منع
    assert editor.states[1] == DENY and cell(editor, 1).text() == "✖" and not editor.effective(1)
    assert "فيه تعديلات لسه ماتحفظتش" in editor.summary.text()
    editor._on_cell_clicked(r, c)                            # منع ← وراثة
    assert editor.states[1] == INHERIT and editor.changed_states() == {} and asked == []


def test_sensitive_allow_asks_password_and_cancel_keeps_state(qapp):
    editor, asked = make_editor(answers=[None, "s1"])
    editor.cycle(3)                                          # «حذف الكل» ← سماح ← اتلغى
    assert editor.states[3] == INHERIT and asked == ["حذف الكل"]
    editor.cycle(3)
    assert editor.states[3] == ALLOW and editor.pending_passwords[3] == "s1"
    assert cell(editor, 3).text() == "✔ 🔑"
    editor.cycle(3)                                          # منع ← كلمة السر الجديدة بتتشال
    assert editor.states[3] == DENY and 3 not in editor.pending_passwords


def test_role_password_inherited_no_prompt_and_missing(qapp):
    info = {3: {"role": True, "override": INHERIT, "has_password": False, "role_has_password": True},
            5: {"role": True, "override": INHERIT, "has_password": False, "role_has_password": False}}
    editor, asked = make_editor(info=info)
    editor.set_states([3], ALLOW)                            # المجموعة عندها كلمة سر ← مايتسألش
    assert asked == [] and editor.states[3] == ALLOW
    assert editor.missing_passwords() == {"show_totals": [5]}   # جاي من المجموعة من غير كلمة سر


def test_row_section_reset_and_filter(qapp):
    editor, asked = make_editor()
    first_target = next(r for r, (k, _d) in enumerate(editor._row_kind) if k == "target")
    editor.set_states(editor.pids_of_row(first_target), DENY)          # الشاشة كلها
    assert all(editor.states[p] == DENY for p in (1, 2, 3))
    header = next(r for r, (k, _d) in enumerate(editor._row_kind) if k == "header")
    editor.set_states(editor.pids_of_row(header), ALLOW)               # القسم كله ← كلمة سر لكل إجراء
    assert sorted(asked) == ["إظهار الإجماليات", "حذف الكل"] and all(s == ALLOW for s in editor.states.values())
    editor.reset_all()
    assert set(editor.states.values()) == {INHERIT} and editor.pending_passwords == {}

    editor.set_states([4], ALLOW)
    editor.filter_combo.setCurrentIndex(1)                              # التخصيصات بس
    hidden = [editor._row_kind[r][1] for r in range(editor.table.rowCount())
              if editor._row_kind[r][0] == "target" and editor.table.isRowHidden(r)]
    assert hidden == ["zz.journal"]
    editor.filter_combo.setCurrentIndex(0)
    editor.search.setText("دليل")
    assert editor.table.isRowHidden(editor._cells[1][0]) and not editor.table.isRowHidden(editor._cells[4][0])


def test_full_access_is_locked(qapp):
    editor, _asked = make_editor(full=True)
    editor.set_editable(True)
    assert not editor.editable and all(cell(editor, p).text() == "✔" for p in range(1, 6))
    assert not editor.set_states([1], DENY) and editor.missing_passwords() == {}
    assert "صلاحية كاملة" in editor.summary.text()


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


def add_permission(db, target, action, order) -> int:
    from app.repositories.security_repository import SecurityRepository
    from app.services import permission_registry as reg

    repo = SecurityRepository(db)
    code = f"acc.{target}.{action}"
    repo.upsert_permission({
        "permission_code": code, "permission_type": "screen", "module_code": "acc",
        "module_name_ar": "الحسابات", "module_name_en": "Accounts", "category_ar": "الشاشات",
        "category_en": "Screens", "target_code": target, "target_name_ar": "القيود اليومية",
        "target_name_en": "Journal", "action_code": action, "action_name_ar": reg.ACTION_NAMES_AR[action],
        "action_name_en": action, "sort_order": order})
    return next(int(p["id"]) for p in repo.list_permissions() if p["permission_code"] == code)


def pid_of(perms, code):
    return next(int(p["id"]) for p in perms.repository.list_permissions() if p["permission_code"] == code)


def audit_rows(db, user_id):
    return db.fetch_all("SELECT * FROM sec.audit_log WHERE entity='sec.app_users' AND entity_id=%s "
                        "AND action='permissions' ORDER BY id", [str(user_id)])


@pytest.fixture()
def data(env):
    db = env["db"]
    perms = PermissionService(db)
    delete_all = add_permission(db, "journal", "delete_all", 900)
    view = pid_of(perms, "sys.users.view")
    role = perms.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"}, {view: True})
    uid = UserService(db, hasher=FAST).create_user({"username": "ahmed", "full_name": "أحمد", "role_id": role}, "pw")
    return {**env, "perms": perms, "delete_all": delete_all, "view": view, "role": role, "uid": uid}


def test_save_user_permissions_one_transaction(data):
    db, perms, uid, delete_all, view = data["db"], data["perms"], data["uid"], data["delete_all"], data["view"]
    roles_view = pid_of(perms, "sys.roles.view")
    with pytest.raises(BusinessError, match="حذف الكل"):                    # من غير كلمة سر ← ولا حاجة
        perms.save_user_permissions(uid, {roles_view: ALLOW, view: DENY, delete_all: ALLOW})
    assert perms.get_user_permission_map(uid) == {} and audit_rows(db, uid) == []

    perms.save_user_permissions(uid, {roles_view: ALLOW, view: DENY, delete_all: ALLOW}, {delete_all: "secret"})
    assert perms.get_user_permission_map(uid) == {roles_view: True, view: False, delete_all: True}
    assert perms.get_user_password_ids(uid) == {delete_all}
    matrix = perms.user_matrix(uid)
    assert matrix[view]["role"] and matrix[view]["override"] == DENY and not matrix[view]["effective"]
    (row,) = audit_rows(db, uid)
    assert row["details"]["extra"]["action_password_for"] == ["acc.journal.delete_all"]
    assert "secret" not in str(row)
    user = perms.get_user(uid)
    assert perms.verify_action_password(user, "acc.journal.delete_all", "secret") is True

    counts = {r["username"]: r["overrides_count"] for r in perms.list_users_for_permissions()}
    assert counts == {DEFAULT_ADMIN_USERNAME: 0, "ahmed": 3}

    perms.save_user_permissions(uid, {roles_view: INHERIT, view: INHERIT, delete_all: INHERIT})
    assert perms.get_user_permission_map(uid) == {} and perms.get_user_password_ids(uid) == set()

    admin = perms.repository.get_user_by_username(DEFAULT_ADMIN_USERNAME)
    with pytest.raises(BusinessError, match="صلاحية كاملة"):
        perms.save_user_permissions(int(admin["id"]), {view: DENY})


def test_inherited_role_password_is_enough(data):
    perms, uid, delete_all, role = data["perms"], data["uid"], data["delete_all"], data["role"]
    perms.save_role(role, {"role_code": "ACC", "role_name_ar": "المحاسبين"},
                    {data["view"]: True, delete_all: True}, {delete_all: "rolepw"})
    perms.save_user_permissions(uid, {delete_all: ALLOW})                   # كلمة سر المجموعة بتكفي
    assert perms.user_matrix(uid)[delete_all]["role_has_password"]
    assert perms.verify_action_password(perms.get_user(uid), "acc.journal.delete_all", "rolepw") is True


def make_screen(db):
    from app.ui.screens.user_permissions_screen import UserPermissionsScreen

    return UserPermissionsScreen(PermissionService(db))


def test_screen_registered_view_and_admin_locked(data):
    from app.ui.screen_registry import SCREEN_FACTORIES

    assert "user_permissions" in SCREEN_FACTORIES
    screen = make_screen(data["db"])
    assert screen.table.rowCount() == 2 and not screen.new_button.isVisible()
    assert screen.new_button.isHidden() and screen.delete_button.isHidden()
    screen.table.select_id(data["uid"])
    assert screen.username.text() == "ahmed" and screen.role_name.text() == "المحاسبين"
    assert screen.mode == "view" and screen.edit_button.isEnabled() and not screen.reset_button.isEnabled()
    assert cell(screen.editor, data["view"]).text() == "✔"

    admin = screen.permissions.repository.get_user_by_username(DEFAULT_ADMIN_USERNAME)
    screen.load(admin["id"])
    assert screen.editor.is_full and not screen.edit_button.isEnabled()
    screen.edit_record()
    assert screen.mode == "view"
    assert screen.format_cell("overrides_count", 0, {"is_admin": True}) == "صلاحية كاملة"


def test_screen_edit_click_save_and_reset(data):
    db, uid, view = data["db"], data["uid"], data["view"]
    screen = make_screen(db)
    screen.load(uid)
    screen.edit_record()
    assert screen.mode == "edit" and screen.editor.editable and screen.reset_button.isEnabled()
    r, c = screen.editor._cells[view]
    screen.editor._on_cell_clicked(r, c)                      # وراثة ← سماح
    screen.editor._on_cell_clicked(r, c)                      # سماح ← منع
    data["answers"]["save"] = False
    screen.save()
    assert screen.mode == "edit" and data["perms"].get_user_permission_map(uid) == {}
    data["answers"]["save"] = True
    screen.save()
    assert data["shown"][-1] == "saved" and screen.mode == "view"
    assert data["perms"].get_user_permission_map(uid) == {view: False}
    assert screen.overrides.text() == "1 (0 سماح · 1 منع)"
    assert screen.table.current_row_data()["overrides_count"] == 1

    screen.edit_record()
    screen.reset_overrides()
    screen.save()
    assert data["perms"].get_user_permission_map(uid) == {}


def test_delete_all_password_for_all_screens(data):
    db, uid, delete_all = data["db"], data["uid"], data["delete_all"]
    second = add_permission(db, "receipts", "delete_all", 901)
    screen = make_screen(db)
    screen.reload_matrix()
    screen.load(uid)
    screen.edit_record()
    screen.set_delete_all_password()                          # مالوش «حذف الكل» في حتة
    assert "مالوش «حذف الكل»" in data["shown"][-1]

    data["answers"]["passwords"] = ["one", "all"]
    screen.editor.cycle(delete_all)                           # سماح ← كلمة سر «one»
    screen.editor.states[second] = ALLOW                      # كأنه اتسمح من غير كلمة سر
    screen.editor.refresh()
    screen.set_delete_all_password()                          # «all» للاتنين
    assert screen.editor.pending_passwords == {delete_all: "all", second: "all"}
    screen.save()
    assert data["shown"][-1] == "saved"
    user = data["perms"].get_user(uid)
    assert data["perms"].get_user_password_ids(uid) == {delete_all, second}
    assert data["perms"].verify_action_password(user, "acc.receipts.delete_all", "all") is True


def test_missing_password_asked_on_save(data):
    db, uid, delete_all = data["db"], data["uid"], data["delete_all"]
    screen = make_screen(db)
    screen.reload_matrix()
    screen.load(uid)
    screen.edit_record()
    screen.editor.states[delete_all] = ALLOW                  # من غير كلمة سر
    from app.ui.dialogs import action_password_dialog

    original = action_password_dialog.ask_new_action_password
    action_password_dialog.ask_new_action_password = lambda *_a, **_k: None
    try:
        screen.save()
    finally:
        action_password_dialog.ask_new_action_password = original
    assert "لازم يكون له كلمة سر" in data["shown"][-1] and screen.mode == "edit"
    assert data["perms"].get_user_permission_map(uid) == {}
    screen.save()                                              # المرة دي بكلمة سر
    assert data["shown"][-1] == "saved" and data["perms"].get_user_password_ids(uid) == {delete_all}


def test_editing_own_permissions_reloads_session(data):
    db, perms = data["db"], data["perms"]
    codes = ("sys.user_permissions.view", "sys.user_permissions.edit", "sys.user_permissions.save")
    role = perms.save_role(None, {"role_code": "SEC", "role_name_ar": "الأمان"}, {pid_of(perms, c): True for c in codes})
    uid = UserService(db, hasher=FAST).create_user({"username": "sec", "role_id": role}, "pw")
    SESSION.clear()
    assert AuthService(db, hasher=FAST).login("sec", "pw").ok
    assert not SESSION.can("sys.users.view")
    screen = make_screen(db)
    screen.load(uid)
    screen.edit_record()
    screen.editor.set_states([data["view"]], ALLOW)
    screen.save()
    assert data["shown"][-1] == "saved" and SESSION.can("sys.users.view")
