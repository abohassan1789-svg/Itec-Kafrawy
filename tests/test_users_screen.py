"""اختبارات الـ Base Screen على شاشة المستخدمين (قاعدة مؤقتة، من غير ما تظهر)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from app.database import migrator
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.permission_service import PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.user_service import UserService
from app.ui.common import messages

FAST = PasswordHasher(1_000)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    answers = {"save": True}
    monkeypatch.setattr(messages, "ask_save", lambda _p: answers["save"])
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _x: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "answers": answers}
    SESSION.clear()


def make_screen(db):
    from app.ui.screens.users_screen import UsersScreen

    return UsersScreen(UserService(db, hasher=FAST), PermissionService(db))


def test_admin_sees_seeded_user_and_view_mode(env):
    screen = make_screen(env["db"])
    assert screen.table.rowCount() == 1 and screen.count_label.text() == "عدد السجلات المعروضة: 1"
    screen.table.select_index(0)
    assert screen.username.text() == DEFAULT_ADMIN_USERNAME
    assert screen.mode == "view" and screen.mode_badge.text() == "عرض"
    assert not screen.full_name.isEnabled()               # الحقول مقفولة في العرض
    assert screen.new_button.isEnabled() and screen.edit_button.isEnabled()
    assert not screen.save_button.isEnabled()
    assert not screen.delete_button.isVisible()           # المستخدمين مالهاش «حذف»


def test_form_is_right_aligned(env):
    """التسميات والكلام جوه الخانات يمين (حتى الإنجليزي) — من الـ Base Screen."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFormLayout

    from app.ui.common.base_screen import RIGHT

    screen = make_screen(env["db"])
    forms = screen.findChild(QWidget, "formBox").findChildren(QFormLayout)
    assert forms and all(form.labelAlignment() == RIGHT for form in forms)
    for field in (screen.username, screen.password, screen.full_name, screen.job_title, screen.email,
                  screen.phone):
        assert field.alignment() == RIGHT
    assert screen.notes.document().defaultTextOption().alignment() == Qt.AlignRight | Qt.AlignAbsolute


def test_create_user_flow(env):
    screen = make_screen(env["db"])
    screen.new_record()
    assert screen.mode == "new" and screen.username.isEnabled() and screen.save_button.isEnabled()
    screen.username.setText("mona")
    screen.full_name.setText("منى علي")
    screen.job_title.setText("محاسبة")

    env["answers"]["save"] = False                         # «هل تريد الحفظ؟» ← لا
    screen.save()
    assert screen.mode == "new" and UserService(env["db"]).list_users("mona") == []

    env["answers"]["save"] = True
    screen.save()                                           # من غير كلمة سر ← رفض بالعربي
    assert env["shown"][-1] == "كلمة المرور مطلوبة." and screen.mode == "new"

    screen.password.setText("pw")
    screen.save()
    assert env["shown"][-1] == "saved" and screen.mode == "view"
    assert screen.table.rowCount() == 2 and screen.username.text() == "mona"
    assert screen.table.current_row_data()["username"] == "mona"


def test_edit_cancel_and_toggle_hides_inactive(env):
    users = UserService(env["db"], hasher=FAST)
    uid = users.create_user({"username": "karim", "full_name": "كريم"}, "pw")
    screen = make_screen(env["db"])
    screen.load(uid)
    screen.edit_record()
    assert screen.mode == "edit" and not screen.username.isEnabled()
    screen.full_name.setText("اسم اتلغى")
    screen.cancel()
    assert screen.mode == "view" and screen.full_name.text() == "كريم"

    screen.toggle_active()                                  # إيقاف ← يختفي من الجدول
    assert screen.current_id is None and "اختفى" in env["shown"][-1]
    assert [r["username"] for r in screen.table.rows] == [DEFAULT_ADMIN_USERNAME]
    screen.show_inactive.setChecked(True)
    assert {r["username"] for r in screen.table.rows} == {DEFAULT_ADMIN_USERNAME, "karim"}


def test_buttons_follow_permissions(env):
    db = env["db"]
    perms = PermissionService(db)
    role = perms.create_role({"role_code": "V", "role_name_ar": "عرض بس"})
    view_id = next(p["id"] for p in perms.repository.list_permissions() if p["permission_code"] == "sys.users.view")
    perms.save_role_permissions(role, {view_id: True})
    UserService(db, hasher=FAST).create_user({"username": "viewer", "role_id": role}, "pw")
    SESSION.clear()
    assert AuthService(db, hasher=FAST).login("viewer", "pw").ok

    screen = make_screen(db)
    screen.table.select_index(0)
    assert screen.current_id is not None
    assert not screen.new_button.isEnabled() and not screen.edit_button.isEnabled()
    assert not screen.toggle_button.isEnabled() and not screen.export_button.isEnabled()
    screen.new_record()                                      # حتى لو اتنادت مباشرة
    assert screen.mode == "view"


def test_lookup_and_preview_dialogs(env, monkeypatch):
    from app.ui.dialogs import lookup_dialog
    from app.ui.screens import users_screen

    users = UserService(env["db"], hasher=FAST)
    uid = users.create_user({"username": "sara"}, "pw")
    users.set_user_active(uid, False)
    screen = make_screen(env["db"])

    class FakeLookup:
        def __init__(self, _title, _cols, rows, _fmt, _parent):
            self.rows = rows
            self.selected_id = next(r["id"] for r in rows if r["username"] == "sara")

        def exec(self):
            return True

    monkeypatch.setattr(lookup_dialog, "LookupDialog", FakeLookup)
    screen.open_lookup()                                    # F1 بيلاقي الموقوفين كمان
    assert screen.current_id == uid and screen.username.text() == "sara"

    opened = []
    monkeypatch.setattr(users_screen.TableDialog, "exec", lambda self: opened.append(self.table.rows) or 0)
    screen.preview_permissions()
    assert opened and all(r["result"] == "ممنوع" for r in opened[-1])   # مالوش مجموعة
    screen.show_login_log()
    assert opened[-1] == []


def test_lookup_dialog_filters_rows(qapp):
    from app.ui.dialogs.lookup_dialog import LookupDialog

    rows = [{"id": 1, "username": "admin"}, {"id": 2, "username": "mona"}, {"id": 3, "username": "monir"}]
    dialog = LookupDialog("المستخدمين", [("username", "المستخدم")], rows)
    dialog.search.setText("mon")
    assert dialog.table.rowCount() == 2 and dialog.count.text() == "عدد السجلات: 2"
    dialog._choose()
    assert dialog.selected_id == 2


def test_export_rows_to_excel(tmp_path):
    from openpyxl import load_workbook

    from app.ui.common.base_screen import export_rows_to_excel
    from app.ui.common.data_table import default_format

    path = tmp_path / "users.xlsx"
    export_rows_to_excel(path, "المستخدمين", [("username", "المستخدم"), ("is_active", "نشط")],
                         [{"username": "mona", "is_active": True}], default_format)
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == ["المستخدم", "نشط"]
    assert [c.value for c in ws[2]] == ["mona", "نعم"]
    assert ws.sheet_view.rightToLeft
