"""اختبارات شاشة «سجل الدخول» (النموذج 1: فلاتر فوق + جدول) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import PermissionDenied
from app.services.permission_service import PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.user_service import UserService, login_duration
from app.ui.common import messages

FAST = PasswordHasher(1_000)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    auth = AuthService(temp_db, hasher=FAST)
    auth.seed_admin()
    PermissionsSyncService(temp_db).sync()
    users = UserService(temp_db, hasher=FAST)
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    users.create_user({"username": "mona", "full_name": "منى علي"}, "pw")
    sara = users.create_user({"username": "sara", "full_name": "سارة"}, "pw")
    users.set_user_active(sara, False)
    SESSION.clear()
    assert auth.login("mona", "pw").ok
    auth.logout()                                     # جلسة اتقفلت
    SESSION.clear()
    assert not auth.login("mona", "غلط").ok           # فشل: كلمة سر غلط
    assert not auth.login("sara", "pw").ok            # فشل: موقوف
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok   # مفتوحة
    shown: list[str] = []
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(str(exc)))
    yield {"db": temp_db, "users": users, "shown": shown}
    SESSION.clear()


def make_screen(env):
    from app.ui.screens.login_logs_screen import LoginLogsScreen

    return LoginLogsScreen(UserService(env["db"], hasher=FAST))


def test_login_duration():
    start = datetime(2026, 11, 7, 8, 55, tzinfo=timezone.utc)
    assert login_duration(start, start + timedelta(hours=3, minutes=45)) == "3:45"
    assert login_duration(start, None) == ""


def test_screen_registered_read_only(env):
    from app.ui.screen_registry import SCREEN_FACTORIES

    assert "login_logs" in SCREEN_FACTORIES
    screen = make_screen(env)
    for button in (screen.new_button, screen.edit_button, screen.save_button, screen.delete_button,
                   screen.cancel_button):
        assert button.isHidden()                       # للقراءة بس
    assert not screen.export_button.isHidden() and screen.export_button.isEnabled()
    assert screen._form_box.isHidden()                 # مفيش فورم
    assert screen.date_to.text() == QDate.currentDate().toString("dd/MM/yyyy")   # مش مقلوب


def test_rows_states_colors_and_counts(env):
    screen = make_screen(env)
    rows = screen.table.rows
    assert len(rows) == 5                              # admin(تجهيز) + mona + 2 فشل + admin(مفتوحة)
    states = [r["state"] for r in rows]
    assert states.count("failed") == 2 and states.count("open") >= 1
    failed = {r["username"]: r["notes"] for r in rows if r["state"] == "failed"}
    assert set(failed) == {"mona", "sara"} and all(failed.values())
    assert "فاشل 2" in screen.count_label.text() and screen.count_label.text().startswith("عدد السجلات المعروضة: 5")
    status_col = [k for k, _t in screen.COLUMNS].index("status_ar")
    r = states.index("failed")
    assert screen.table.item(r, status_col).text() == "✖ فشل"
    assert screen.table.item(r, status_col).foreground().color().name() == "#dc2626"
    closed = next(i for i, row in enumerate(rows) if row["state"] == "closed")
    assert rows[closed]["duration"] != "" and screen.table.item(closed, 3).text() not in ("", "—")


def test_filters_and_search(env):
    screen = make_screen(env)
    screen.status_filter.setCurrentIndex(2)            # فشل
    assert {r["state"] for r in screen.table.rows} == {"failed"} and screen.table.rowCount() == 2
    screen.status_filter.setCurrentIndex(3)            # مفتوحة دلوقتي
    assert screen.table.rows and {r["state"] for r in screen.table.rows} == {"open"}
    screen.status_filter.setCurrentIndex(0)
    mona = screen.user_filter.findText("mona — منى علي")
    screen.user_filter.setCurrentIndex(mona)
    assert {r["username"] for r in screen.table.rows} == {"mona"} and screen.table.rowCount() == 2
    screen.user_filter.setCurrentIndex(0)
    screen.search.setText("سارة")
    assert [r["username"] for r in screen.table.rows] == ["sara"]
    screen.search.clear()
    tomorrow = QDate.currentDate().addDays(1)
    screen.date_from.setDate(tomorrow)
    screen.date_to.setDate(tomorrow.addDays(5))
    assert screen.table.rowCount() == 0
    screen.date_from.setDate(QDate.currentDate().addDays(5))   # من > إلى ← بيتبدّلوا لوحدهم
    screen.date_to.setDate(QDate.currentDate().addDays(-1))
    assert screen.table.rowCount() == 5


def test_selection_keeps_view_and_export(env, tmp_path, monkeypatch):
    from openpyxl import load_workbook
    from PySide6.QtWidgets import QFileDialog

    screen = make_screen(env)
    screen.table.select_index(1)
    assert screen.current_id == screen.table.rows[1]["id"] and screen.mode == "view"
    path = tmp_path / "log.xlsx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(path), ""))
    monkeypatch.setattr(messages, "info", lambda *_a: None)
    screen.export_excel()
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]][:3] == ["المستخدم", "الاسم", "وقت الدخول"] and ws.max_row == 6


def test_requires_view_permission(env):
    db = env["db"]
    perms = PermissionService(db)
    role = perms.create_role({"role_code": "U", "role_name_ar": "مستخدمين بس"})
    view = next(p["id"] for p in perms.repository.list_permissions() if p["permission_code"] == "sys.users.view")
    perms.save_role_permissions(role, {view: True})
    env["users"].create_user({"username": "nolog", "role_id": role}, "pw")
    SESSION.clear()
    assert AuthService(db, hasher=FAST).login("nolog", "pw").ok
    with pytest.raises(PermissionDenied):
        UserService(db).login_log_rows()
    screen = make_screen(env)                           # الشاشة مابتقعش — بتقول الرسالة
    assert screen.table.rowCount() == 0 and env["shown"]
