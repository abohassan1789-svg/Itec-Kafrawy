"""اختبارات «حماية التعديل في نفس الوقت» (Optimistic Locking — المرحلة 1ج، سكريبت 0008).

كل اختبار «جهازين»: اتصالين منفصلين بنفس القاعدة المؤقتة (``dev1`` و``dev2``) — الاتنين فتحوا نفس السجل،
الأول حفظ، التاني بيحفظ ← يترفض برسالة «اتعدّل من جهاز تاني (المستخدم، الوقت)»، ومافيش حاجة اتمسحت.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date
from decimal import Decimal

import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.company_service import CompanyService
from app.services.currency_service import CurrencyService
from app.services.errors import ConcurrentEditError
from app.services.fiscal_year_service import FiscalYearService
from app.services.permission_service import ALLOW, DENY, PermissionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.tax_code_service import TaxCodeService
from app.services.user_service import UserService
from app.ui.common import messages

EDITED = "اتعدّل من جهاز تاني"
DELETED = "اتمسح من جهاز تاني"


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def login(db, username=DEFAULT_ADMIN_USERNAME, password=DEFAULT_ADMIN_PASSWORD):
    SESSION.clear()
    assert AuthService(db).login(username, password).ok


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    login(temp_db)
    dev2 = Database(temp_db.settings)                  # «الجهاز التاني» = اتصال تاني بنفس القاعدة
    shown: list[str] = []
    conflicts: list[str] = []
    answers = {"conflict": False}
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "ask_conflict",
                        lambda _p, exc: conflicts.append(messages.error_text(exc)) or answers["conflict"])
    yield {"dev1": temp_db, "dev2": dev2, "shown": shown, "conflicts": conflicts, "answers": answers}
    dev2.close()
    SESSION.clear()


def make_ahmed(db) -> int:
    """مستخدم تاني (مدير) عشان الرسالة تقول مين اللي عدّل."""
    return UserService(db).create_user({"username": "ahmed", "full_name": "أحمد", "is_admin": True}, "Ahmed#123")


# ---- القاعدة -----------------------------------------------------------------
def test_row_version_columns_and_trigger(env):
    db = env["dev1"]
    tables = ["sec.roles", "sec.app_users", "core.company_settings", "core.fiscal_years", "core.currencies",
              "core.exchange_rates", "acc.tax_codes"]
    for table in tables:
        schema, name = table.split(".")
        assert db.fetch_value(
            "SELECT column_default FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = 'row_version'", [schema, name]) == "1"
    vat = "WHERE code = 'VAT14'"
    assert db.fetch_value(f"SELECT row_version FROM acc.tax_codes {vat}") == 1
    db.execute(f"UPDATE acc.tax_codes SET name_ar = 'ق.م' {vat}")
    assert db.fetch_value(f"SELECT row_version FROM acc.tax_codes {vat}") == 2           # تعديل = +1
    db.execute(f"UPDATE acc.tax_codes SET name_ar = 'ق.م', updated_at = now() {vat}")
    assert db.fetch_value(f"SELECT row_version FROM acc.tax_codes {vat}") == 2           # من غير تغيير = زي ما هو
    db.execute(f"UPDATE acc.tax_codes SET row_version = 99 {vat}")
    assert db.fetch_value(f"SELECT row_version FROM acc.tax_codes {vat}") == 3           # محدش يحط رقم بإيده
    # آخر دخول وإصدار الصلاحيات مابيزوّدوش إصدار المستخدم (عشان مايعملوش تعارض على شاشة المستخدمين).
    uid = SESSION.user_id
    before = db.fetch_value("SELECT row_version FROM sec.app_users WHERE id = %s", [uid])
    db.execute("UPDATE sec.app_users SET last_login_at = now(), permissions_version = permissions_version + 1 "
               "WHERE id = %s", [uid])
    assert db.fetch_value("SELECT row_version FROM sec.app_users WHERE id = %s", [uid]) == before
    assert db.fetch_value("SELECT version FROM core.schema_migrations ORDER BY version DESC LIMIT 1") >= 8


def test_conflict_message_has_user_device_and_time(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    make_ahmed(dev1)
    vat = next(r for r in TaxCodeService(dev1).list_codes() if r["code"] == "VAT14")
    login(dev2, "ahmed", "Ahmed#123")                                    # الجهاز التاني بمستخدم تاني
    TaxCodeService(dev2).save_code(vat["id"], {**vat, "name_ar": "من أحمد"}, vat["row_version"])
    login(dev1)
    with pytest.raises(ConcurrentEditError) as err:
        TaxCodeService(dev1).save_code(vat["id"], {**vat, "name_ar": "من المدير"}, vat["row_version"])
    text = str(err.value)
    assert text.startswith("السجل ده " + EDITED) and "ahmed" in text and "دوس «تحديث» وعدّل تاني" in text
    assert err.value.changed_by == "ahmed" and err.value.changed_at is not None and err.value.device
    assert f"{date.today():%d/%m/%Y}" in text
    assert TaxCodeService(dev1).get_code(vat["id"])["name_ar"] == "من أحمد"           # تعديل أحمد مااتمسحش


# ---- الـ Services: «جهازين» ------------------------------------------------------
def test_users_two_devices(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    uid = make_ahmed(dev1)
    seen1, seen2 = UserService(dev1).get_user(uid), UserService(dev2).get_user(uid)
    assert seen1["row_version"] == seen2["row_version"] == 1
    UserService(dev1).update_user(uid, {**seen1, "phone": "0100"}, expected_version=seen1["row_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        UserService(dev2).update_user(uid, {**seen2, "phone": "0122"}, expected_version=seen2["row_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        UserService(dev2).set_user_active(uid, False, expected_version=seen2["row_version"])
    fresh = UserService(dev1).get_user(uid)
    assert fresh["phone"] == "0100" and fresh["is_active"] and fresh["row_version"] == 2
    # الدخول من جهاز تاني مابيعملش تعارض
    AuthService(dev2).login("ahmed", "Ahmed#123")
    login(dev1)
    UserService(dev1).set_user_active(uid, False, expected_version=fresh["row_version"])
    assert UserService(dev1).get_user(uid)["is_active"] is False


def test_roles_two_devices_permissions_bump_version(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    svc1, svc2 = PermissionService(dev1), PermissionService(dev2)
    role_id = svc1.save_role(None, {"role_code": "ACC", "role_name_ar": "المحاسبين"}, {})
    role = svc1.get_role(role_id)
    pid = int(next(p["id"] for p in svc1.repository.list_permissions() if p["permission_code"] == "sys.users.view"))
    svc1.save_role(role_id, role, {pid: True}, expected_version=role["row_version"])   # الصلاحيات بس
    assert svc1.get_role(role_id)["row_version"] == role["row_version"] + 1
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.save_role(role_id, {**role, "role_name_ar": "تاني"}, {}, expected_version=role["row_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.delete_role(role_id, expected_version=role["row_version"])
    assert svc1.get_role(role_id)["role_name_ar"] == "المحاسبين"
    assert svc1.get_role_permission_map(role_id) == {pid: True}                        # الصلاحية مااتشالتش
    svc2.delete_role(role_id, expected_version=svc2.get_role(role_id)["row_version"])
    assert svc1.get_role(role_id) is None


def test_user_permissions_two_devices(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    users = UserService(dev1)
    uid = users.create_user({"username": "mona"}, "Mona#123")
    svc1, svc2 = PermissionService(dev1), PermissionService(dev2)
    pids = [int(p["id"]) for p in svc1.repository.list_permissions()
            if p["permission_code"] in ("sys.users.view", "sys.roles.view")]
    seen1, seen2 = svc1.get_user(uid), svc2.get_user(uid)
    svc1.save_user_permissions(uid, {pids[0]: ALLOW}, expected_version=seen1["permissions_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.save_user_permissions(uid, {pids[0]: DENY, pids[1]: ALLOW},
                                   expected_version=seen2["permissions_version"])
    assert svc1.get_user_permission_map(uid) == {pids[0]: True}
    # بيانات المستخدم وصلاحياته إصدارين منفصلين: تعديل البيانات مابيعطّلش حفظ الصلاحيات والعكس.
    fresh = svc1.get_user(uid)
    users.update_user(uid, {**fresh, "phone": "0111"}, expected_version=fresh["row_version"])
    svc2.save_user_permissions(uid, {pids[1]: DENY}, expected_version=fresh["permissions_version"])
    assert svc1.get_user_permission_map(uid) == {pids[0]: True, pids[1]: False}


def test_company_two_devices_first_time_and_update(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    data = {"name_ar": "شركة الأول"}
    CompanyService(dev1).save_company(data, expected_version=0)                      # الاتنين شافوها فاضية
    with pytest.raises(ConcurrentEditError, match=EDITED):
        CompanyService(dev2).save_company({"name_ar": "شركة التاني"}, expected_version=0)
    company = CompanyService(dev1).get_company()
    assert company["name_ar"] == "شركة الأول" and company["row_version"] == 1
    CompanyService(dev1).save_company({**data, "phone": "02"}, expected_version=1)
    with pytest.raises(ConcurrentEditError, match=EDITED):
        CompanyService(dev2).save_company({**data, "city": "طنطا"}, expected_version=1)
    company = CompanyService(dev2).get_company()
    assert company["phone"] == "02" and company["city"] is None and company["row_version"] == 2


def _year(code: int) -> dict:
    return {"year_code": str(code), "name_ar": f"سنة {code}", "start_date": date(code, 1, 1),
            "end_date": date(code, 12, 31)}


def test_fiscal_years_two_devices_save_lock_delete(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    svc1, svc2 = FiscalYearService(dev1), FiscalYearService(dev2)
    yid = svc1.save_year(None, _year(2026))
    seen = svc2.get_year(yid)
    svc1.save_year(yid, {**_year(2026), "notes": "من الأول"}, expected_version=seen["row_version"])
    for attempt in (lambda: svc2.save_year(yid, {**_year(2026), "notes": "من التاني"}, seen["row_version"]),
                    lambda: svc2.set_closed(yid, True, expected_version=seen["row_version"]),
                    lambda: svc2.delete_year(yid, expected_version=seen["row_version"])):
        with pytest.raises(ConcurrentEditError, match=EDITED):
            attempt()
    year = svc1.get_year(yid)
    assert year["notes"] == "من الأول" and year["status"] == "open"
    svc1.set_closed(yid, True, expected_version=year["row_version"])                  # القفل بيزوّد الإصدار
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.set_closed(yid, True, expected_version=year["row_version"])
    assert svc1.get_year(yid)["row_version"] == year["row_version"] + 1


def test_currencies_and_rates_two_devices(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    svc1, svc2 = CurrencyService(dev1), CurrencyService(dev2)
    usd = next(r for r in svc1.list_currencies() if r["code"] == "USD")
    svc1.save_currency(usd["id"], {**usd, "symbol": "US$"}, usd["row_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.save_currency(usd["id"], {**usd, "name_ar": "دولار"}, usd["row_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.delete_currency(usd["id"], expected_version=usd["row_version"])
    assert svc1.get_currency(usd["id"])["symbol"] == "US$"
    # سعر اليوم: الاتنين شافوا إن مفيش سعر (0) ← التاني مايستبدلش سعر الأول من غير ما يعرف.
    today = date.today()
    rate_id = svc1.save_rate(usd["id"], today, "48.5", expected_version=0)
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.save_rate(usd["id"], today, "49", expected_version=0)
    rate = svc1.rate_context(usd["id"], today)["same_day"]
    assert rate["rate"] == Decimal("48.5") and rate["row_version"] == 1
    svc1.save_rate(usd["id"], today, "48.7", expected_version=1)                       # استبدال = +1
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.save_rate(usd["id"], today, "49", expected_version=1)
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc2.delete_rate(rate_id, expected_version=1)
    assert svc1.rate_context(usd["id"], today)["same_day"]["rate"] == Decimal("48.7")


def test_tax_codes_two_devices_and_deleted_record(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    svc1, svc2 = TaxCodeService(dev1), TaxCodeService(dev2)
    wht = next(r for r in svc1.list_codes() if r["code"] == "WHT5")
    svc1.delete_code(wht["id"], expected_version=wht["row_version"])
    with pytest.raises(ConcurrentEditError, match=DELETED) as err:                   # اتمسح من الجهاز التاني
        svc2.save_code(wht["id"], {**wht, "name_ar": "x"}, wht["row_version"])
    assert err.value.deleted and DEFAULT_ADMIN_USERNAME in str(err.value)
    vat = next(r for r in svc1.list_codes() if r["code"] == "VAT14")
    svc2.save_code(vat["id"], {**vat, "rate": 15}, vat["row_version"])
    with pytest.raises(ConcurrentEditError, match=EDITED):
        svc1.delete_code(vat["id"], expected_version=vat["row_version"])
    assert svc1.get_code(vat["id"]) is not None                                       # مااتمسحش


# ---- الـ Base Screen ------------------------------------------------------------
def tax_screen(db):
    from app.ui.screens.tax_codes_screen import TaxCodesScreen

    return TaxCodesScreen(TaxCodeService(db))


def test_base_screen_keeps_form_after_conflict_then_refresh(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    s1, s2 = tax_screen(dev1), tax_screen(dev2)
    vat_id = next(r["id"] for r in TaxCodeService(dev1).list_codes() if r["code"] == "VAT14")
    for screen in (s1, s2):                                    # الاتنين فتحوا نفس السجل ودوسوا «تعديل»
        screen.load(vat_id)
        screen.edit_record()
        assert screen.mode == "edit" and screen.current_version == 1
    s1.name_ar.setText("قيمة مضافة — الجهاز الأول")
    s1.save()
    assert s1.mode == "view" and s1.current_version == 2 and env["shown"][-1] == "saved"

    s2.name_ar.setText("قيمة مضافة — الجهاز التاني")
    s2.save()                                                  # «رجوع للتعديل» (الافتراضي)
    assert EDITED in env["conflicts"][-1]
    assert s2.mode == "edit" and s2.name_ar.text() == "قيمة مضافة — الجهاز التاني"   # الفورم مااتمسحش
    assert s2.current_version == 1 and s2.save_button.isEnabled()
    assert TaxCodeService(dev1).get_code(vat_id)["name_ar"] == "قيمة مضافة — الجهاز الأول"
    s2.save()                                                  # لسه على الإصدار القديم ← يترفض تاني
    assert len(env["conflicts"]) == 2

    env["answers"]["conflict"] = True                          # «تحديث»
    s2.save()
    assert s2.mode == "view" and s2.name_ar.text() == "قيمة مضافة — الجهاز الأول" and s2.current_version == 2
    s2.edit_record()
    s2.name_ar.setText("بعد التحديث")
    s2.save()
    assert s2.mode == "view" and TaxCodeService(dev1).get_code(vat_id)["name_ar"] == "بعد التحديث"


def test_base_screen_edit_reloads_latest_and_cancel(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    s1 = tax_screen(dev1)
    vat = next(r for r in TaxCodeService(dev1).list_codes() if r["code"] == "VAT14")
    s1.load(vat["id"])
    TaxCodeService(dev2).save_code(vat["id"], {**vat, "name_ar": "من بره"}, vat["row_version"])
    assert s1.name_ar.text() != "من بره"
    s1.edit_record()                                           # «تعديل» بيجيب آخر نسخة ← مفيش تعارض
    assert s1.name_ar.text() == "من بره" and s1.current_version == 2
    s1.cancel()
    assert s1.mode == "view"


def test_base_screen_delete_conflict_refreshes(env):
    dev1, dev2 = env["dev1"], env["dev2"]
    s1 = tax_screen(dev1)
    wht = next(r for r in TaxCodeService(dev1).list_codes() if r["code"] == "WHT1")
    s1.load(wht["id"])
    TaxCodeService(dev2).save_code(wht["id"], {**wht, "name_ar": "خصم 1% (جديد)"}, wht["row_version"])
    s1.delete()
    assert EDITED in env["shown"][-1]
    assert TaxCodeService(dev1).get_code(wht["id"]) is not None                       # مااتمسحش
    assert s1.mode == "view" and s1.name_ar.text() == "خصم 1% (جديد)"                # واتحدّث لوحده


# ---- الشاشات اللي اتعملت ---------------------------------------------------------
def test_users_screen_two_devices(env):
    from app.ui.screens.users_screen import UsersScreen

    dev1, dev2 = env["dev1"], env["dev2"]
    uid = make_ahmed(dev1)
    s1, s2 = UsersScreen(UserService(dev1), PermissionService(dev1)), UsersScreen(UserService(dev2),
                                                                                PermissionService(dev2))
    for screen in (s1, s2):
        screen.load(uid)
        screen.edit_record()
    s1.full_name.setText("أحمد علي")
    s1.save()
    s2.full_name.setText("أحمد حسن")
    s2.save()
    assert EDITED in env["conflicts"][-1] and s2.mode == "edit" and s2.full_name.text() == "أحمد حسن"
    assert UserService(dev1).get_user(uid)["full_name"] == "أحمد علي"
    s2.cancel()                                                # «إلغاء» ← آخر نسخة
    assert s2.full_name.text() == "أحمد علي" and s2.mode == "view"


def test_roles_and_user_permissions_screens_two_devices(env):
    from app.ui.screens.roles_screen import RolesScreen
    from app.ui.screens.user_permissions_screen import UserPermissionsScreen

    dev1, dev2 = env["dev1"], env["dev2"]
    role_id = PermissionService(dev1).save_role(None, {"role_code": "SALES", "role_name_ar": "المبيعات"}, {})
    r1, r2 = RolesScreen(PermissionService(dev1)), RolesScreen(PermissionService(dev2))
    for screen in (r1, r2):
        screen.load(role_id)
        screen.edit_record()
    r1.role_name_ar.setText("المبيعات 1")
    r1.save()
    r2.role_name_ar.setText("المبيعات 2")
    r2.save()
    assert EDITED in env["conflicts"][-1] and r2.mode == "edit" and r2.role_name_ar.text() == "المبيعات 2"
    assert PermissionService(dev1).get_role(role_id)["role_name_ar"] == "المبيعات 1"

    uid = UserService(dev1).create_user({"username": "mona"}, "Mona#123")
    p1, p2 = UserPermissionsScreen(PermissionService(dev1)), UserPermissionsScreen(PermissionService(dev2))
    for screen in (p1, p2):
        screen.load(uid)
        screen.edit_record()
        assert screen.current_version == 1                     # permissions_version
    pid = int(next(p["id"] for p in PermissionService(dev1).repository.list_permissions()
                   if p["permission_code"] == "sys.users.view"))
    p1.editor.states[pid] = ALLOW
    p1.save()
    assert p1.mode == "view" and p1.current_version == 2
    p2.editor.states[pid] = DENY
    p2.save()
    assert EDITED in env["conflicts"][-1] and p2.mode == "edit" and p2.editor.states[pid] == DENY
    assert PermissionService(dev1).get_user_permission_map(uid) == {pid: True}


def test_company_screen_two_devices(env):
    from app.ui.screens.company_screen import CompanyScreen

    dev1, dev2 = env["dev1"], env["dev2"]
    s1, s2 = CompanyScreen(CompanyService(dev1)), CompanyScreen(CompanyService(dev2))
    for screen in (s1, s2):                                    # الاتنين فتحوا والبيانات لسه ماتسجلتش
        screen.edit_record()
    s1.name_ar.setText("الشركة — الأول")
    s1.save()
    s2.name_ar.setText("الشركة — التاني")
    s2.save()
    assert EDITED in env["conflicts"][-1] and s2.mode == "edit" and s2.name_ar.text() == "الشركة — التاني"
    assert CompanyService(dev1).get_company()["name_ar"] == "الشركة — الأول"


def test_fiscal_years_screen_lock_conflict(env):
    from app.ui.screens.fiscal_years_screen import FiscalYearsScreen

    dev1, dev2 = env["dev1"], env["dev2"]
    yid = FiscalYearService(dev1).save_year(None, _year(2026))
    s1 = FiscalYearsScreen(FiscalYearService(dev1))
    s1.load(yid)
    seen = FiscalYearService(dev2).get_year(yid)
    FiscalYearService(dev2).save_year(yid, {**_year(2026), "notes": "اتعدّلت"}, seen["row_version"])
    s1.toggle_lock()
    assert any(EDITED in text for text in env["shown"])
    year = FiscalYearService(dev1).get_year(yid)
    assert year["status"] == "open" and s1.current_version == year["row_version"]    # مااتقفلتش واتحدّثت
    s1.toggle_lock()                                           # بعد التحديث ← القفل بيشتغل
    assert FiscalYearService(dev1).get_year(yid)["status"] == "closed"


def test_currencies_screen_two_devices(env):
    from app.ui.screens.currencies_screen import CurrenciesScreen

    dev1, dev2 = env["dev1"], env["dev2"]
    s1, s2 = CurrenciesScreen(CurrencyService(dev1)), CurrenciesScreen(CurrencyService(dev2))
    eur = next(r for r in CurrencyService(dev1).list_currencies() if r["code"] == "EUR")
    for screen in (s1, s2):
        screen.load(eur["id"])
        screen.edit_record()
    s1.symbol.setText("€1")
    s1.save()
    s2.symbol.setText("€2")
    s2.save()
    assert EDITED in env["conflicts"][-1] and s2.mode == "edit" and s2.symbol.text() == "€2"
    s2.cancel()
    s2.delete()                                                # بعد الإلغاء على آخر إصدار ← المسح بيشتغل
    assert CurrencyService(dev1).get_currency(eur["id"]) is None
    s1.edit_record()                                           # الجهاز الأول: السجل اتمسح ← الشاشة فضيت
    assert s1.current_id is None and s1.mode == "view"


def test_rate_dialog_conflict_reloads_same_day(env):
    from app.ui.dialogs.rate_dialog import RateDialog

    dev1, dev2 = env["dev1"], env["dev2"]
    usd = next(r for r in CurrencyService(dev1).list_currencies() if r["code"] == "USD")
    dialog = RateDialog(CurrencyService(dev1), usd)
    assert dialog._context["same_day"] is None
    CurrencyService(dev2).save_rate(usd["id"], date.today(), "50", expected_version=0)   # جهاز تاني سبقك
    dialog.rate.setValue(49)
    dialog.save()
    assert EDITED in env["shown"][-1] and dialog.saved_rate_id is None
    assert dialog._context["same_day"]["rate"] == 50 and "الحفظ هيستبدله" in dialog.hint.text()
    dialog.save()                                              # دلوقتي عارف ← «تستبدله؟» نعم
    assert CurrencyService(dev1).rate_context(usd["id"], date.today())["same_day"]["rate"] == 49
