"""اختبارات «أكواد الضريبة» (القاعدة + الـ Service + الحساب + الشاشة) على قاعدة مؤقتة."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.tax_code_service import TaxCodeService, fmt_rate
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
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    service = TaxCodeService(temp_db)
    ids = {r["code"]: int(r["id"]) for r in service.list_codes()}
    yield {"db": temp_db, "shown": shown, "service": service, "ids": ids}
    SESSION.clear()


def make_screen(db):
    from app.ui.screens.tax_codes_screen import TaxCodesScreen

    return TaxCodesScreen(TaxCodeService(db))


# ---- القاعدة ---------------------------------------------------------------
def test_seed_and_database_rules(env):
    db, ids = env["db"], env["ids"]
    assert list(ids) == ["VAT14", "VAT0", "EXEMPT", "WHT1", "WHT3", "WHT5"]           # ترتيب القائمة
    vat = db.fetch_one("SELECT kind, rate, calc_base FROM acc.tax_codes WHERE code = 'VAT14'")
    assert vat == {"kind": "add", "rate": Decimal("14.0000"), "calc_base": "cum"}
    assert db.fetch_value("SELECT kind FROM acc.tax_codes WHERE code = 'WHT1'") == "deduct"
    bad = [
        ("INSERT INTO acc.tax_codes (code, name_ar) VALUES ('vat5', 'x')", psycopg.errors.CheckViolation),
        ("INSERT INTO acc.tax_codes (code, name_ar) VALUES ('VAT14', 'x')", psycopg.errors.UniqueViolation),
        ("INSERT INTO acc.tax_codes (code, name_ar, rate) VALUES ('X1', 'x', 101)", psycopg.errors.CheckViolation),
        ("INSERT INTO acc.tax_codes (code, name_ar, kind) VALUES ('X1', 'x', 'minus')", psycopg.errors.CheckViolation),
        ("INSERT INTO acc.tax_codes (code, name_ar, is_exempt, rate) VALUES ('X1', 'x', true, 5)",
         psycopg.errors.CheckViolation),
        ("INSERT INTO acc.tax_codes (code, name_ar, calc_order) VALUES ('X1', 'x', 10)", psycopg.errors.CheckViolation),
    ]
    for sql, err in bad:
        with pytest.raises(err):
            db.execute(sql)
    with pytest.raises(psycopg.errors.RaiseException, match="مينفعش يتغيّر"):
        db.execute("UPDATE acc.tax_codes SET code = 'VAT15' WHERE code = 'VAT14'")


# ---- الحساب (جدول ضرايب الفاتورة) ------------------------------------------
def test_calculate_add_and_deduct_taxes(env):
    service = env["service"]
    tbl = {"code": "TBL8", "kind": "add", "rate": Decimal("8"), "calc_base": "net", "calc_order": 1, "is_exempt": False}
    vat = {"code": "VAT14", "kind": "add", "rate": Decimal("14"), "calc_base": "cum", "calc_order": 2, "is_exempt": False}
    wht = {"code": "WHT1", "kind": "deduct", "rate": Decimal("1"), "calc_base": "net", "calc_order": 3,
           "is_exempt": False}
    r = service.calculate(10000, [wht, vat, tbl])                          # الترتيب من الكود مش من الإدخال
    assert [(x["tax"]["code"], x["base"], x["amount"]) for x in r["rows"]] == [
        ("TBL8", Decimal("10000.00"), Decimal("800.00")),
        ("VAT14", Decimal("10800.00"), Decimal("1512.00")),
        ("WHT1", Decimal("10000.00"), Decimal("100.00"))]
    assert (r["added"], r["deducted"], r["total"]) == (Decimal("2312.00"), Decimal("100.00"), Decimal("12212.00"))
    only_vat = service.calculate("999.99", [vat])
    assert only_vat["rows"][0]["amount"] == Decimal("140.00") and only_vat["total"] == Decimal("1139.99")
    exempt = {"code": "EXEMPT", "kind": "add", "rate": Decimal("0"), "calc_base": "net", "calc_order": 2,
              "is_exempt": True}
    assert service.calculate(500, [exempt])["total"] == Decimal("500.00")


# ---- الـ Service -----------------------------------------------------------
def test_validation_save_and_audit(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    for data, text in (({"code": "1VAT", "name_ar": "x"}, "يبدأ بحرف"), ({"code": "TBL8", "name_ar": " "}, "مطلوب"),
                       ({"code": "VAT14", "name_ar": "تاني"}, "متسجل قبل كده"),
                       ({"code": "TBL8", "name_ar": "جدول", "rate": 120}, "من 0 لـ 100"),
                       ({"code": "TBL8", "name_ar": "جدول", "calc_order": 0}, "من 1 لـ 9"),
                       ({"code": "X1", "name_ar": "x", "kind": "deduct", "is_exempt": True}, "مش خصم")):
        with pytest.raises(BusinessError, match=text):
            service.save_code(None, data)
    tbl = service.save_code(None, {"code": "tbl8", "name_ar": "ضريبة جدول 8%", "kind": "add", "rate": "8",
                                   "calc_base": "net", "calc_order": 1, "applies_to": "sales"})
    row = service.get_code(tbl)
    assert row["code"] == "TBL8" and row["rate"] == Decimal("8.0000") and row["applies_to"] == "sales"
    service.save_code(tbl, {"code": "ZZZ", "name_ar": "ضريبة جدول 8%", "kind": "add", "rate": "8",
                            "calc_order": 1, "applies_to": "both", "is_active": False})
    row = service.get_code(tbl)
    assert row["code"] == "TBL8" and row["applies_to"] == "both" and not row["is_active"]      # الكود ثابت
    exempt = service.save_code(None, {"code": "EX2", "name_ar": "معفي تاني", "rate": 5, "calc_base": "cum",
                                      "is_exempt": True})
    row = service.get_code(exempt)
    assert row["rate"] == 0 and row["calc_base"] == "net" and row["kind"] == "add"            # المعفي بيتظبط لوحده
    service.delete_code(exempt)
    logs = db.fetch_all("SELECT action, details FROM sec.audit_log WHERE entity = 'acc.tax_codes' ORDER BY id")
    assert [log["action"] for log in logs] == ["create", "update", "create", "delete"]
    # «المبيعات» بس ← حساب المشتريات فاضي · «الاتنين» ← حساب المشتريات بيتملى بالدور (ض.ق.م مدخلات)
    assert set(logs[1]["details"]["changed"]) == {"applies_to", "is_active", "purchase_account_id",
                                                 "purchase_account_code", "purchase_account_name"}
    assert service.summary(service.list_codes()) == {"total": 7, "add": 4, "deduct": 3, "inactive": 1}
    assert fmt_rate(Decimal("14.0000")) == "14%" and fmt_rate(Decimal("2.5000")) == "2.5%"


def test_permissions(env):
    service, ids = env["service"], env["ids"]
    admin_id = env["db"].fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"sys.tax_codes.view", "sys.tax_codes.edit", "sys.tax_codes.save"})
    service.save_code(ids["WHT1"], {"code": "WHT1", "name_ar": "خصم من المنبع 1%", "kind": "deduct", "rate": 1})
    with pytest.raises(PermissionDenied):
        service.save_code(None, {"code": "TBL8", "name_ar": "جدول"})
    with pytest.raises(PermissionDenied):
        service.delete_code(ids["WHT5"])


# ---- الشاشة ----------------------------------------------------------------
def test_screen_first_open_and_list(env):
    screen = make_screen(env["db"])
    assert screen.current_id == env["ids"]["VAT14"] and screen.code_edit.text() == "VAT14"
    assert screen.kind_add.isChecked() and screen.rate.value() == 14
    assert screen.calc_base.currentData() == "cum" and screen.applies_to.currentData() == "both"
    assert screen.count_label.text() == "عدد الضرايب: 6 · إضافة 3 · خصم 3 · موقوفة 0"
    assert [screen.table.item(r, 2).text() for r in range(6)] == ["‏+ إضافة", "‏+ إضافة", "معفي",
                                                                  "‏− خصم", "‏− خصم", "‏− خصم"]
    assert "14%" in screen.table.item(0, 3).text() and screen.table.item(2, 3).text() == "—"
    assert not screen.rate.isEnabled() and screen.edit_button.isEnabled()                  # العرض مقفول
    assert screen.sales_account.currentText().startswith("2103") and not screen.sales_account.isEnabled()
    assert screen.purchase_account.currentText().startswith("110601")
    # ترتيب شريط الأدوات زي النموذج: … تحديث · تصدير إكسل · خروج
    bar = screen.export_button.parentWidget().layout()
    order = [bar.itemAt(i).widget() for i in range(bar.count()) if bar.itemAt(i).widget() is not None]
    assert [w for w in order if w in (screen.refresh_button, screen.export_button, screen.exit_button)] == [
        screen.refresh_button, screen.export_button, screen.exit_button]


def test_screen_new_exempt_edit_and_delete(env):
    screen = make_screen(env["db"])
    screen.new_record()
    assert screen.code_edit.isEnabled() and screen.rate.isEnabled()
    screen.code_edit.setText("wht2")
    screen.name_ar.setText("خصم من المنبع 2%")
    screen.kind_deduct.setChecked(True)
    screen.rate.setValue(2)
    screen.calc_order.setValue(3)
    screen.save()
    assert env["shown"] == ["saved"]
    new_id = screen.current_id
    row = screen.taxes.get_code(new_id)
    assert (row["code"], row["kind"], row["rate"]) == ("WHT2", "deduct", Decimal("2.0000"))
    assert screen.table.rowCount() == 7
    # المعفي بيقفل النسبة والنوع والأساس
    screen.edit_record()
    assert not screen.code_edit.isEnabled()
    screen.is_exempt.setChecked(True)
    assert screen.kind_add.isChecked() and screen.rate.value() == 0 and not screen.rate.isEnabled()
    screen.is_exempt.setChecked(False)
    assert screen.rate.isEnabled()
    screen.cancel()
    screen.load(new_id)
    screen.delete()
    assert screen.current_id is None and screen.table.rowCount() == 6


# ---- حسابات الضريبة (سكريبت 0010) --------------------------------------------
def acc_id(db, code):
    return db.fetch_value("SELECT id FROM acc.accounts WHERE code = %s", [code])


def test_tax_accounts_seed_and_database_rules(env):
    db = env["db"]
    links = {r["code"]: (r["s"], r["p"]) for r in db.fetch_all(
        "SELECT t.code, sa.code AS s, pa.code AS p FROM acc.tax_codes t "
        "LEFT JOIN acc.accounts sa ON sa.id = t.sales_account_id "
        "LEFT JOIN acc.accounts pa ON pa.id = t.purchase_account_id")}
    assert links == {"VAT14": ("2103", "110601"), "VAT0": ("2103", None), "EXEMPT": (None, None),
                     "WHT1": ("110602", "2104"), "WHT3": ("110602", "2104"), "WHT5": ("110602", "2104")}
    assert db.fetch_value("SELECT max(row_version) FROM acc.tax_codes") == 1           # التجهيز مش تعديل
    raise_ = psycopg.errors.RaiseException
    for code, text in (("1102", "لازم يبقى فرعي ونشط"), ("5202", "من حسابات الميزانية"),
                       ("5302", "لازم يبقى فرعي ونشط")):
        with pytest.raises(raise_, match=text):
            db.execute("UPDATE acc.tax_codes SET sales_account_id = %s WHERE code = 'VAT14'", [acc_id(db, code)])
    # الحساب المربوط: مايتمسحش ولا يتوقف ولا يتحوّل رئيسي
    db.execute("UPDATE acc.tax_codes SET purchase_account_id = %s WHERE code = 'VAT14'", [acc_id(db, "1201")])
    for sql, text in (("DELETE FROM acc.accounts WHERE code = '1201'", "مربوط بأكواد الضريبة \\(VAT14\\)"),
                      ("UPDATE acc.accounts SET is_active = false WHERE code = '1201'", "مينفعش يتوقف"),
                      ("UPDATE acc.accounts SET is_leaf = false WHERE code = '1201'", "يتحوّل رئيسي")):
        with pytest.raises(raise_, match=text):
            db.execute(sql)
    db.execute("UPDATE acc.accounts SET name_ar = 'أجهزة' WHERE code = '1201'")          # الاسم عادي


def test_tax_accounts_service(env):
    db, service, ids = env["db"], env["service"], env["ids"]
    assert service.suggested_accounts("add") == {"sales_account_id": acc_id(db, "2103"),
                                                 "purchase_account_id": acc_id(db, "110601")}
    assert service.suggested_accounts("deduct") == {"sales_account_id": acc_id(db, "110602"),
                                                    "purchase_account_id": acc_id(db, "2104")}
    choices = {r["code"] for r in service.account_choices()}
    assert {"2103", "110601", "110602", "2104", "1103"} <= choices and not choices & {"1102", "5202", "5302"}
    # من غير حساب ← حساب الدور · الناحية اللي مش بتتطبق ← فاضي
    wht2 = service.save_code(None, {"code": "WHT2", "name_ar": "خصم 2%", "kind": "deduct", "rate": 2,
                                    "applies_to": "purch", "sales_account_id": acc_id(db, "2103")})
    row = service.get_code(wht2)
    assert row["sales_account_id"] is None and row["purchase_account_code"] == "2104"
    # حساب متحدد بإيدك بيتحفظ زي ما هو
    tbl = service.save_code(None, {"code": "TBL8", "name_ar": "جدول 8%", "kind": "add", "rate": 8,
                                   "sales_account_id": acc_id(db, "2104"), "purchase_account_id": acc_id(db, "1103")})
    row = service.get_code(tbl)
    assert (row["sales_account_code"], row["purchase_account_code"]) == ("2104", "1103")
    for bad in ("1102", "5202"):
        with pytest.raises(BusinessError, match="حساب فرعي نشط من حسابات الميزانية"):
            service.save_code(tbl, {**row, "sales_account_id": acc_id(db, bad)})
    # المعفي ونسبة صفر: الحساب اختياري
    ex = service.save_code(None, {"code": "EX2", "name_ar": "معفي 2", "is_exempt": True})
    assert service.get_code(ex)["sales_account_id"] is None
    # مسح الحساب المربوط من شاشة الدليل ← رسالة عربي
    from app.services.account_service import AccountService

    service.save_code(tbl, {**service.get_code(tbl), "purchase_account_id": acc_id(db, "1201")})
    accounts = AccountService(db)
    with pytest.raises(BusinessError, match=r"مربوط بأكواد الضريبة \(TBL8\)"):
        accounts.delete_account(acc_id(db, "1201"))
    computers = accounts.get_account(acc_id(db, "1201"))
    for change in ({"is_active": False}, {"is_leaf": False}):
        with pytest.raises(BusinessError, match="مينفعش يتوقف ولا يتحوّل رئيسي"):
            accounts.save_account(computers["id"], {**computers, **change})
    assert ids["VAT14"]


def test_tax_accounts_screen(env):
    db = env["db"]
    screen = make_screen(db)
    screen.new_record()
    assert screen.sales_account.currentData() == acc_id(db, "2103")                    # إضافة ← ض.ق.م
    assert screen.purchase_account.currentData() == acc_id(db, "110601")
    assert screen.sales_account.isEnabled() and screen.purchase_account.isEnabled()
    screen.kind_deduct.setChecked(True)                                                 # خصم ← خصم المنبع
    assert screen.sales_account.currentData() == acc_id(db, "110602")
    assert screen.purchase_account.currentData() == acc_id(db, "2104")
    screen.applies_to.setCurrentIndex(screen.applies_to.findData("sales"))
    assert screen.purchase_account.currentData() is None and not screen.purchase_account.isEnabled()
    screen.code_edit.setText("WHT2")
    screen.name_ar.setText("خصم من المنبع 2%")
    screen.rate.setValue(2)
    screen.save()
    assert env["shown"] == ["saved"]
    row = screen.taxes.get_code(screen.current_id)
    assert (row["sales_account_code"], row["purchase_account_id"]) == ("110602", None)
    # التعديل بيعرض الحساب المحفوظ ويقدر يغيّره
    screen.edit_record()
    screen.applies_to.setCurrentIndex(screen.applies_to.findData("both"))
    assert screen.purchase_account.isEnabled() and screen.purchase_account.currentData() == acc_id(db, "2104")
    screen.sales_account.setCurrentIndex(screen.sales_account.findData(acc_id(db, "2104")))
    screen.save()
    row = screen.taxes.get_code(screen.current_id)
    assert (row["sales_account_code"], row["purchase_account_code"]) == ("2104", "2104")
