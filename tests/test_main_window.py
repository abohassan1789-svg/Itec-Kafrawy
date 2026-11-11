"""اختبارات الواجهة الرئيسية (النموذج 4) وشجرة القائمة حسب الصلاحيات."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from app.security.session import SESSION
from app.services.navigation_service import menu_tree

ADMIN = {"id": 1, "username": "user", "full_name": "مدير النظام", "job_title": "مدير النظام",
         "is_admin": True, "full_access": False}
CLERK = {"id": 2, "username": "clerk", "full_name": "", "job_title": "", "is_admin": False, "full_access": False}


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def _session():
    SESSION.clear()
    yield
    SESSION.clear()


class FakeAuth:
    def __init__(self):
        self.logged_out = 0

    def logout(self):
        self.logged_out += 1
        SESSION.clear()


class Screen(QWidget):
    def __init__(self, title="المستخدمين", mode="view"):
        super().__init__()
        self.setWindowTitle(title)
        self.mode = mode


def make_window(qapp, monkeypatch, factories=None):
    from app.ui import main_window as mw

    infos: list[str] = []
    monkeypatch.setattr(mw, "_info", lambda _p, _t, text, *_a: infos.append(text))
    window = mw.MainWindow(FakeAuth(), factories=factories or {}, photo_provider=lambda: None)
    window.infos = infos
    return window


# ---- شجرة القائمة (من غير Qt) ----------------------------------------------

def test_menu_tree_admin_sees_everything():
    SESSION.start(ADMIN, set())
    tree = menu_tree(SESSION.can)
    ids = [s["id"] for s in tree]
    assert ids == ["acc", "sales", "purch", "hr", "inv", "cash", "rep", "sys"]
    sys_sec = tree[-1]
    assert sys_sec["bottom"] and not sys_sec["coming_soon"] and sys_sec["count"] == 8
    acc = tree[0]
    assert not acc["coming_soon"] and acc["count"] == 3                 # «الحسابات» ← شجرة الحسابات + الحسابات الافتراضية + مراكز التكلفة
    sales = tree[1]
    assert not sales["coming_soon"] and sales["count"] == 3             # «المبيعات» ← مجموعات العملاء + العملاء + أوامر التوريد
    purch = tree[2]
    assert not purch["coming_soon"] and purch["count"] == 2             # «المشتريات» ← مجموعات الموردين + الموردين
    hr = tree[3]
    assert not hr["coming_soon"] and hr["count"] == 1                   # «الموظفين» ← قسم لوحده
    assert all(s["coming_soon"] for s in tree[4:-1])


def test_menu_tree_hides_what_user_cannot_view():
    SESSION.start(CLERK, {"sys.users.view", "sys.roles.edit"})      # edit من غير view = مايظهرش
    sys_sec = next(s for s in menu_tree(SESSION.can) if s["id"] == "sys")
    assert [i["key"] for g in sys_sec["groups"] for i in g["items"]] == ["users"]
    SESSION.start(CLERK, set())
    assert "sys" not in [s["id"] for s in menu_tree(SESSION.can)]   # قسم كله ممنوع = مستخبي


# ---- الواجهة -----------------------------------------------------------------

def test_layout_rail_on_right_with_logo_and_header(qapp, monkeypatch):
    from app.ui.main_window import RAIL_WIDTH

    SESSION.start(ADMIN, set())
    w = make_window(qapp, monkeypatch)
    w.resize(1300, 800)
    w.show()
    qapp.processEvents()
    assert w._rail.width() == RAIL_WIDTH
    assert w._rail.geometry().x() > w.width() // 2                     # الشريط يمين
    assert w.home_page.logo_view is not None                           # لوجو ITEC موجود
    assert w.user_header.name.text() == "مدير النظام"
    assert w.active_rail() == "home"
    assert list(w._rail_buttons) == ["home", "acc", "sales", "purch", "hr", "inv", "cash", "rep", "sys"]
    w._quitting = True
    w.close()


def test_popup_lists_screens_and_opens_them(qapp, monkeypatch):
    SESSION.start(ADMIN, set())
    w = make_window(qapp, monkeypatch, factories={"users": lambda: Screen()})
    w.resize(1300, 800)
    w.show()
    w.show_popup("sys")
    assert w.popup_open()
    assert list(w._popup_buttons) == ["company", "fiscal_years", "currencies", "tax_codes", "users", "roles", "user_permissions", "login_logs"]
    assert w._popup.geometry().right() < w._rail.geometry().x() + 2     # القائمة شمال الشريط

    w._popup_buttons["users"].click()
    assert not w.popup_open()
    screen = w.open_windows["users"]
    assert screen.isVisible() and w.active_rail() == "sys"
    assert w.open_screen("users") is screen                             # مفتوحة ← يرجع لها
    w.open_screen("roles")                                              # لسه مالهاش شاشة
    assert "لسه بتتبني" in w.infos[-1]
    screen.close()
    w._quitting = True
    w.close()


def test_open_screen_without_permission_is_refused(qapp, monkeypatch):
    SESSION.start(CLERK, {"sys.users.view"})
    w = make_window(qapp, monkeypatch, factories={"roles": lambda: Screen("المجموعات")})
    assert w.open_screen("roles") is None
    assert w.infos[-1] == "ليس لديك صلاحية لفتح هذه الشاشة"
    assert "roles" not in w.open_windows
    w._quitting = True
    w.close()


def test_close_button_is_blocked_and_logout_checks_editing(qapp, monkeypatch):
    SESSION.start(ADMIN, set())
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", lambda *a: quit_calls.append(1))
    w = make_window(qapp, monkeypatch, factories={"users": lambda: Screen(mode="edit")})
    w.show()
    w.close()                                      # ✕ / Alt+F4 ← ممنوع
    qapp.processEvents()
    assert w.isVisible()

    w.open_screen("users")
    assert w.request_logout() is False             # فيه شاشة في «تعديل»
    assert "المستخدمين" in w.infos[-1] and w.auth_service.logged_out == 0

    w.open_windows["users"].mode = "view"
    monkeypatch.setattr(w, "confirm_logout", lambda: False)
    assert w.request_logout() is False             # قال «لا»
    monkeypatch.setattr(w, "confirm_logout", lambda: True)
    assert w.request_logout() is True
    assert w.auth_service.logged_out == 1 and w.infos[-1] == "تم تسجيل الخروج بنجاح"
    assert not w.isVisible() and quit_calls == [1]
