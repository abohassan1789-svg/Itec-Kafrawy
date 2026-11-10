"""اختبارات «العملات وأسعار الصرف» (القاعدة + الـ Service + الشاشة + نافذة تسجيل السعر) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.currency_service import CurrencyService, change_percent, fmt_rate
from app.services.errors import BusinessError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages


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
    asked: list[str] = []
    answers = {"save": True, "yes": True}
    monkeypatch.setattr(messages, "ask_save", lambda _p: answers["save"])
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: asked.append(text) or answers["yes"])
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    service = CurrencyService(temp_db)
    ids = {r["code"]: int(r["id"]) for r in service.list_currencies()}
    yield {"db": temp_db, "shown": shown, "asked": asked, "answers": answers, "service": service, "ids": ids}
    SESSION.clear()


def make_screen(db):
    from app.ui.screens.currencies_screen import CurrenciesScreen

    return CurrenciesScreen(CurrencyService(db))


D = date(2026, 11, 8)


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db, ids = env["db"], env["ids"]
    rows = db.fetch_all("SELECT code, is_base FROM core.currencies ORDER BY id")
    assert rows == [{"code": "EGP", "is_base": True}, {"code": "USD", "is_base": False},
                    {"code": "EUR", "is_base": False}]
    with pytest.raises(psycopg.errors.UniqueViolation):                 # أساسية واحدة بس
        db.execute("INSERT INTO core.currencies (code, name_ar, is_base) VALUES ('SAR', 'ريال', true)")
    with pytest.raises(psycopg.errors.CheckViolation):                  # الكود 3 حروف كابيتال
        db.execute("INSERT INTO core.currencies (code, name_ar) VALUES ('us1', 'غلط')")
    with pytest.raises(psycopg.errors.CheckViolation):                  # الكسور 0–6
        db.execute("INSERT INTO core.currencies (code, name_ar, decimals) VALUES ('SAR', 'ريال', 7)")
    with pytest.raises(psycopg.errors.CheckViolation):                  # الأساسية دايماً نشطة
        db.execute("UPDATE core.currencies SET is_active = false WHERE code = 'EGP'")
    with pytest.raises(psycopg.errors.RaiseException, match="مينفعش تتمسح"):
        db.execute("DELETE FROM core.currencies WHERE code = 'EGP'")
    with pytest.raises(psycopg.errors.RaiseException, match="مينفعش يتغيّر"):
        db.execute("UPDATE core.currencies SET code = 'USX' WHERE code = 'USD'")
    with pytest.raises(psycopg.errors.RaiseException, match="ثابتة"):
        db.execute("UPDATE core.currencies SET is_base = true WHERE code = 'USD'")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):             # عملة الشركة لازم تبقى موجودة
        db.execute("INSERT INTO core.company_settings (name_ar, base_currency_code) VALUES ('شركة', 'XYZ')")

    ins = "INSERT INTO core.exchange_rates (currency_id, rate_date, rate) VALUES (%s, %s, %s)"
    with pytest.raises(psycopg.errors.RaiseException, match="دايماً 1"):  # الأساسية مالهاش أسعار
        db.execute(ins, [ids["EGP"], D, 1])
    with pytest.raises(psycopg.errors.CheckViolation):                  # السعر > 0
        db.execute(ins, [ids["USD"], D, 0])
    db.execute(ins, [ids["USD"], D - timedelta(days=7), Decimal("48.4")])
    db.execute(ins, [ids["USD"], D, Decimal("48.65")])
    with pytest.raises(psycopg.errors.UniqueViolation):                 # سعر واحد في اليوم
        db.execute(ins, [ids["USD"], D, Decimal("49")])
    rate_on = "SELECT core.rate_on(%s, %s)"
    assert db.fetch_value(rate_on, ["USD", D - timedelta(days=1)]) == Decimal("48.4")   # آخر سعر قبله
    assert db.fetch_value(rate_on, ["USD", D + timedelta(days=30)]) == Decimal("48.65")
    assert db.fetch_value(rate_on, ["USD", D - timedelta(days=30)]) is None
    assert db.fetch_value(rate_on, ["EGP", D]) == 1
    db.execute("DELETE FROM core.currencies WHERE code = 'USD'")          # أسعارها بتتمسح معاها
    assert db.fetch_value("SELECT count(*) FROM core.exchange_rates") == 0


# ---- الـ Service ------------------------------------------------------------
def test_currency_validation_and_save_with_audit(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    for data, text in (({"code": "US", "name_ar": "x"}, "3 حروف"), ({"code": "SAR", "name_ar": " "}, "مطلوب"),
                       ({"code": "USD", "name_ar": "تاني"}, "متسجلة قبل كده"),
                       ({"code": "SAR", "name_ar": "ريال", "decimals": 9}, "من 0 لـ 6")):
        with pytest.raises(BusinessError, match=text):
            service.save_currency(None, data)
    sar = service.save_currency(None, {"code": "sar", "name_ar": "ريال سعودي", "symbol": "ر.س",
                                       "fraction_name": "هللة"})
    assert service.get_currency(sar)["code"] == "SAR"                      # بيتحوّل كابيتال
    service.save_currency(sar, {"code": "XXX", "name_ar": "ريال سعودي", "symbol": "ر.س", "fraction_name": "هللة",
                                "decimals": 3, "is_active": False})
    row = service.get_currency(sar)
    assert row["code"] == "SAR" and row["decimals"] == 3 and not row["is_active"]   # الكود ثابت
    with pytest.raises(BusinessError, match="مينفعش تتوقف"):
        service.save_currency(ids["EGP"], {"code": "EGP", "name_ar": "جنيه", "is_active": False})
    with pytest.raises(BusinessError, match="مينفعش تتمسح"):
        service.delete_currency(ids["EGP"])
    logs = db.fetch_all("SELECT action, details FROM sec.audit_log WHERE entity='core.currencies' ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "update"]
    assert set(logs[1]["details"]["changed"]) == {"decimals", "is_active"}
    assert "last_rate" not in logs[1]["details"]["after"]


def test_rates_save_replace_convert_and_delete(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    usd = ids["USD"]
    for bad, text in ((0, "أكبر من صفر"), ("abc", "رقم")):
        with pytest.raises(BusinessError, match=text):
            service.save_rate(usd, D, bad)
    with pytest.raises(BusinessError, match="دايماً 1"):
        service.save_rate(ids["EGP"], D, 1)
    first = service.save_rate(usd, D - timedelta(days=7), "48.40")
    second = service.save_rate(usd, D, "48.65")
    assert service.rate_context(usd, D)["previous"]["rate"] == Decimal("48.4")
    assert service.rate_context(usd, D)["same_day"]["id"] == second
    assert service.save_rate(usd, D, "48.70") == second                    # نفس اليوم = استبدال
    rates = service.list_rates(usd)
    assert [r["rate"] for r in rates] == [Decimal("48.7"), Decimal("48.4")]
    assert rates[0]["prev_rate"] == Decimal("48.4") and rates[0]["created_by_name"] == DEFAULT_ADMIN_USERNAME
    conv = service.convert(usd, Decimal("1250"), D - timedelta(days=2))
    assert conv["rate"] == Decimal("48.4") and conv["rate_date"] == D - timedelta(days=7)
    assert conv["amount_base"] == Decimal("60500.00")
    assert service.convert(usd, 10, D - timedelta(days=30))["rate"] is None
    assert service.convert(ids["EGP"], 5, D)["amount_base"] == Decimal("5")
    row = service.get_currency(usd)
    assert row["last_rate"] == Decimal("48.7") and row["prev_rate"] == Decimal("48.4") and row["rates_count"] == 2

    service.delete_rate(first)
    service.save_currency(usd, {"code": "USD", "name_ar": "دولار أمريكي", "is_active": False})
    with pytest.raises(BusinessError, match="موقوفة"):
        service.save_rate(usd, D + timedelta(days=1), "49")
    logs = db.fetch_all("SELECT action FROM sec.audit_log WHERE entity='core.exchange_rates' ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "create", "update", "delete"]
    service.delete_currency(usd)
    log = db.fetch_one("SELECT details FROM sec.audit_log WHERE entity='core.currencies' AND action='delete'")
    assert log["details"]["extra"] == {"rates_deleted": 1}


def test_rate_permissions(env):
    service, ids = env["service"], env["ids"]
    service.save_rate(ids["USD"], D, "48.65")
    admin_id = env["db"].fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"sys.currencies.view", "sys.currencies.create"})
    service.save_rate(ids["USD"], D + timedelta(days=1), "48.70")       # «إضافة» بتكفي لسعر جديد
    with pytest.raises(PermissionDenied):                                # الاستبدال محتاج «تعديل»
        service.save_rate(ids["USD"], D, "48.60")
    with pytest.raises(PermissionDenied):
        service.save_currency(None, {"code": "SAR", "name_ar": "ريال"})
    with pytest.raises(PermissionDenied):
        service.delete_rate(service.list_rates(ids["USD"])[0]["id"])


def test_formatting_helpers():
    assert fmt_rate(Decimal("48.65")) == "48.6500"
    assert fmt_rate(Decimal("1234.123456")) == "1,234.123456"
    assert change_percent(Decimal("52.5"), Decimal("50")) == Decimal("5.00")
    assert change_percent(Decimal("1"), None) is None


# ---- الشاشة -----------------------------------------------------------------
def test_screen_first_open_layout_and_converter(env):
    service, ids = env["service"], env["ids"]
    service.save_rate(ids["USD"], D - timedelta(days=7), "48.40")
    service.save_rate(ids["USD"], D, "48.65")
    screen = make_screen(env["db"])
    assert screen.current_id == ids["USD"]                                # أول عملة غير الأساسية
    assert screen.count_label.text() == "عدد العملات: 3 · نشطة 3 · الأساسية: EGP"
    assert [screen.table.item(r, 4).text() for r in range(3)] == ["⭐ أساسية", "نشطة", "نشطة"]
    assert screen.table.item(0, 2).text() == "1.0000"
    assert screen.rates_title.text().startswith("آخر أسعار USD") and screen.rates_table.rowCount() == 2
    assert "+0.2500" in screen.rates_table.item(0, 2).text() and screen.rates_table.item(1, 2).text() == "—"
    assert screen.rate_button.isEnabled() and screen.delete_button.isEnabled()
    # ترتيب شريط الأدوات زي النموذج: … تحديث · تسجيل سعر · تصدير إكسل · خروج
    bar = screen.rate_button.parentWidget().layout()
    order = [bar.itemAt(i).widget() for i in range(bar.count()) if bar.itemAt(i).widget() is not None]
    assert [w for w in order if w in (screen.refresh_button, screen.rate_button, screen.export_button,
                                      screen.exit_button)] == [screen.refresh_button, screen.rate_button,
                                                               screen.export_button, screen.exit_button]
    # المحوّل: آخر سعر في التاريخ أو قبله
    screen.convert_amount.setValue(1250)
    screen.convert_date.setDate(QDate(2026, 11, 5))
    assert "60,500.00 EGP" in screen.convert_result.text() and "48.4000" in screen.convert_note.text()
    screen.convert_date.setDate(QDate(2026, 1, 1))
    assert screen.convert_result.text() == "—" and "سجّل سعر" in screen.convert_note.text()
    # الأساسية: مفيش تسجيل سعر ولا حذف
    screen.load(ids["EGP"])
    assert not screen.rate_button.isEnabled() and not screen.delete_button.isEnabled()
    assert "دايماً 1" in screen.rates_note.text()
    screen.edit_record()
    assert not screen.is_active.isEnabled() and not screen.code_edit.isEnabled() and screen.name_ar.isEnabled()
    screen.cancel()


def test_screen_new_currency_and_delete(env):
    db = env["db"]
    screen = make_screen(db)
    screen.new_record()
    assert screen.code_edit.isEnabled()
    screen.code_edit.setText("gbp")
    screen.name_ar.setText("جنيه إسترليني")
    screen.fraction_name.setText("بنس")
    screen.save()
    assert env["shown"] == ["saved"]
    gbp = screen.current_id
    assert screen.currencies.get_currency(gbp)["code"] == "GBP" and screen.table.rowCount() == 4
    screen.currencies.save_rate(gbp, D, "61.2")
    screen.load(gbp)
    screen.delete()
    assert "هيتمسح معاها 1 سعر صرف" in env["asked"][-1]
    assert screen.current_id is None and screen.table.rowCount() == 3


def test_rate_dialog_hint_confirm_and_replace(env, monkeypatch):
    from app.ui.dialogs.rate_dialog import RateDialog

    service, ids = env["service"], env["ids"]
    service.save_rate(ids["USD"], D - timedelta(days=7), "48.00")
    screen = make_screen(env["db"])

    def run(rate: float, when: date):
        def fake_exec(dialog):
            dialog.rate_date.setDate(QDate(when.year, when.month, when.day))
            dialog.rate.setValue(rate)
            dialog.save()
            return dialog.result()
        monkeypatch.setattr(RateDialog, "exec", fake_exec)
        screen.register_rate()

    dialog = screen.make_rate_dialog()
    assert dialog.rate.value() == 48 and "48.0000" in dialog.hint.text()
    dialog.rate.setValue(60)
    assert dialog.warning.isVisibleTo(dialog) and "25.00%" in dialog.warning.text()

    env["answers"]["yes"] = False
    run(60, D)                                                           # تغيير 25% و«لا» ← مااتسجلش
    assert "25.00%" in env["asked"][-1] and len(service.list_rates(ids["USD"])) == 1
    env["answers"]["yes"] = True
    run(48.5, D)                                                         # تغيير صغير ← من غير سؤال
    assert env["shown"][-1] == "اتسجل السعر." and screen.rates_table.rowCount() == 2
    asked_before = len(env["asked"])
    run(48.6, D)                                                         # نفس اليوم ← «تستبدله؟»
    assert "تستبدله" in env["asked"][asked_before] and service.list_rates(ids["USD"])[0]["rate"] == Decimal("48.6")
    assert len(service.list_rates(ids["USD"])) == 2

    screen.rates_table.select_index(0)
    assert screen.delete_rate_button.isEnabled()
    screen.delete_selected_rate()
    assert [r["rate"] for r in service.list_rates(ids["USD"])] == [Decimal("48")]
