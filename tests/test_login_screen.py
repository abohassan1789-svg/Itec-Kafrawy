"""اختبارات شاشة الدخول (النموذج 2) على قاعدة مؤقتة — من غير ما تظهر على الشاشة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QFrame

from app.database import migrator
from app.security.password_hasher import PasswordHasher
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.permissions_sync_service import PermissionsSyncService


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def login(qapp, temp_db):
    from app.ui.screens.login_screen import LoginWindow

    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    window = LoginWindow(AuthService(temp_db, hasher=PasswordHasher(1_000)))
    yield window
    window.deleteLater()
    SESSION.clear()


def test_empty_fields_show_message(login):
    login.attempt_login()
    assert login.error_label.text() == "اكتب اسم المستخدم"
    login.username.setText("user")
    login.attempt_login()
    assert login.error_label.text() == "اكتب كلمة المرور"
    assert login.result() == 0 and login.user is None


def test_wrong_password_clears_field_and_stays_open(login):
    login.username.setText(DEFAULT_ADMIN_USERNAME)
    login.password.setText("wrong")
    login.attempt_login()
    assert login.error_label.text() == "كلمة المرور غير صحيحة"
    assert login.password.text() == ""
    assert login.user is None and not SESSION.is_authenticated
    login.password.textEdited.emit("x")                 # الكتابة بتمسح رسالة الخطأ
    assert login.error_label.text() == ""


def test_successful_login_accepts_and_starts_session(login):
    login.username.setText(DEFAULT_ADMIN_USERNAME)
    login.password.setText(DEFAULT_ADMIN_PASSWORD)
    login.password.returnPressed.emit()                 # Enter من كلمة السر = دخول
    assert login.result() == QDialog.Accepted
    assert login.user["username"] == DEFAULT_ADMIN_USERNAME
    assert SESSION.is_authenticated and SESSION.is_admin


def test_layout_is_split_with_brand_on_right(login):
    from app.ui.screens.login_screen import BRAND_PANEL_WIDTH, LOGIN_SIZE

    assert (login.width(), login.height()) == LOGIN_SIZE
    login.show()
    panel = login.findChild(QFrame, "brandPanel")
    assert panel is not None and panel.width() == BRAND_PANEL_WIDTH
    assert panel.geometry().x() > login.username.mapTo(login, login.username.rect().topLeft()).x()
    login.hide()
