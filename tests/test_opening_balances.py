"""اختبارات «الأرصدة الافتتاحية» (0033: مستند واحد · السطور وحمايتها · مايتكررش · الأقسام · الحفظ بالتعارض لكل سطر ·
الترحيل بقيد OB واحد والفرق على 34 · إلغاء الترحيل · خانة «رصيد أول المدة» في العملاء والموردين · التفريع ·
رد (ج) مركز التكلفة · الصلاحيات · جهازين · الشاشة)."""

from __future__ import annotations

import datetime as dt
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.customer_service import CustomerService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.opening_balance_service import OpeningBalanceService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.supplier_service import SupplierService
from app.ui.common import messages

D = dt.date(2026, 1, 1)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    temp_db.execute("UPDATE acc.opening_balances SET balance_date = %s", [D])
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "service": OpeningBalanceService(temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def acc(db, code):
    return one(db, "SELECT id FROM acc.accounts WHERE code = %s", code)


def cash_customer(db):
    return one(db, "SELECT id FROM party.customers WHERE code = '0'")


def cash_supplier(db):
    return one(db, "SELECT id FROM party.suppliers ORDER BY seq LIMIT 1")


def header(db):
    return db.fetch_one("SELECT * FROM acc.opening_balances WHERE id = 1")


def row_of(rows, **kw):
    return next(r for r in rows if all(r.get(k) == v for k, v in kw.items()))


def change(row, debit="", credit="", **extra):
    """تغيير من صف قسم (زي ما الشاشة بتبعته)."""
    return {"line_id": row.get("line_id"), "account_id": row["account_id"], "party_kind": row.get("party_kind"),
            "party_id": row.get("party_id"), "debit": debit, "credit": credit, "old_debit": row["debit"],
            "old_credit": row["credit"], "label": row.get("name"), **extra}


def fill(env):
    """خزنة 24,300 مدين + العميل النقدي 5,000 مدين + المورد 1,800 دائن."""
    service = env["service"]
    trs = service.section_rows("trs")[0]
    cus = row_of(service.section_rows("cus"), code="0")
    sup = service.section_rows("sup")[0]
    return service.save(None, [change(trs, "24300"), change(cus, "5000"), change(sup, credit="1800")],
                        header(env["db"])["row_version"])


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db = env["db"]
    h = header(db)
    assert h["status"] == "draft" and h["balance_date"] == D
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO acc.opening_balances (id) VALUES (2)")
    with pytest.raises(psycopg.errors.RaiseException, match="مايتمسحش"):
        db.execute("DELETE FROM acc.opening_balances")
    # رد (ج): المبيعات والخدمات والمردودات والتكلفة من غير «لازم مركز تكلفة»
    assert one(db, "SELECT count(*) FROM acc.accounts WHERE code IN ('4101','4102','42','51') "
                   "AND requires_cost_center") == 0

    def ins(code, debit=0, credit=0, **party):
        cols = ", ".join(["account_id", "debit", "credit", *party])
        marks = ", ".join(["%s"] * (3 + len(party)))
        return one(db, f"INSERT INTO acc.opening_balance_lines ({cols}) VALUES ({marks}) RETURNING id",
                   acc(db, code), debit, credit, *party.values())

    with pytest.raises(psycopg.errors.CheckViolation):
        ins("110101", 10, 10)
    with pytest.raises(psycopg.errors.RaiseException, match="رئيسي"):
        ins("11", 10)
    with pytest.raises(psycopg.errors.RaiseException, match="حساب الأرصدة الافتتاحية"):
        ins("34", credit=10)
    with pytest.raises(psycopg.errors.RaiseException, match="لازم تختار العميل"):
        ins("1103", 10)
    with pytest.raises(psycopg.errors.RaiseException, match="مالوش طرف"):
        ins("110101", 10, customer_id=cash_customer(db))
    ins("1103", 100, customer_id=cash_customer(db))
    with pytest.raises(psycopg.errors.UniqueViolation):                    # مايتكررش
        ins("1103", 50, customer_id=cash_customer(db))
    ins("110101", 100)
    with pytest.raises(psycopg.errors.UniqueViolation):                    # الخزنة مرتين
        ins("110101", credit=5)
    db.execute("UPDATE acc.accounts SET requires_cost_center = true WHERE code = '5203'")
    with pytest.raises(psycopg.errors.RaiseException, match="لازم يتكتب عليه مركز تكلفة"):
        ins("5203", 10)
    ins("1201", 30)
    # المرحّل: السطور مقفولة والرأس كمان
    db.execute("UPDATE acc.opening_balances SET status = 'posted', posted_at = now()")
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        ins("110102", 10)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("UPDATE acc.opening_balances SET balance_date = '2026-02-01'")
    # التفريع بينقل السطر للـ «— عام» حتى لو مرحّل
    general = one(db, "SELECT acc.split_account(%s)", acc(db, "1201"))
    assert one(db, "SELECT count(*) FROM acc.opening_balance_lines WHERE account_id = %s", general) == 1
    db.execute("UPDATE acc.opening_balances SET status = 'draft'")
    assert header(db)["posted_at"] is None
    # مسح العميل بيمسح سطره (CASCADE) — والعميل اللي عليه قيود القاعدة بتمنعه أصلاً
    other = one(db, "INSERT INTO party.customers (code, name_ar, group_id, currency_code) "
                    "VALUES ('99', 'عميل تجربة', (SELECT group_id FROM party.customers WHERE code = '0'), 'EGP') "
                    "RETURNING id")
    ins("1103", 70, customer_id=other)
    db.execute("DELETE FROM party.customers WHERE id = %s", [other])
    assert one(db, "SELECT count(*) FROM acc.opening_balance_lines WHERE customer_id = %s", other) == 0


# ---- الـ Service --------------------------------------------------------------
def test_sections_and_save(env):
    db, service = env["db"], env["service"]
    trs = service.section_rows("trs")
    assert [r["code"] for r in trs][:2] == ["TRS-001", "TRS-002"] and trs[0]["line_id"] is None
    cus = service.section_rows("cus")
    assert row_of(cus, code="0")["account_code"] == "1103"
    version = fill(env)
    doc = service.get_document()
    assert doc["row_version"] == version and doc["total_debit"] == Decimal("29300.00")
    assert doc["total_credit"] == Decimal("1800.00")
    assert doc["sections"]["trs"]["lines"] == 1 and doc["sections"]["cus"]["lines"] == 1
    assert doc["sections"]["sup"]["credit"] == Decimal("1800.00") and doc["opening_account"]["code"] == "34"
    # تعديل + مسح (الفاضي = مالوش رصيد)
    trs0 = service.section_rows("trs")[0]
    service.save(None, [change(trs0, "25000")], doc["row_version"])
    assert service.section_rows("trs")[0]["debit"] == Decimal("25000.00")
    service.save(None, [change(service.section_rows("trs")[0], "")], None)
    assert service.section_rows("trs")[0]["line_id"] is None
    # الرأس: التاريخ بيتعدّل · لازم في سنة موجودة ومفتوحة
    v = header(db)["row_version"]
    service.save({"balance_date": dt.date(2026, 3, 1), "notes": "أرصدة البداية"}, [], v)
    assert header(db)["balance_date"] == dt.date(2026, 3, 1) and header(db)["notes"] == "أرصدة البداية"
    with pytest.raises(BusinessError, match="مفيش سنة مالية"):
        service.save({"balance_date": dt.date(2030, 1, 1), "notes": ""}, [], header(db)["row_version"])
    # «حسابات تانية»: سطر جديد + مايتكررش مع قسم العملاء
    oth = {"line_id": None, "account_id": acc(db, "1201"), "debit": "65000", "credit": "", "old_debit": 0,
           "old_credit": 0, "label": "أجهزة", "new_row": True}
    service.save(None, [oth], None)
    assert [r["account_code"] for r in service.section_rows("oth")] == ["1201"]
    dup = {"line_id": None, "account_id": acc(db, "1103"), "party_kind": "customer", "party_id": cash_customer(db),
           "debit": "10", "credit": "", "old_debit": 0, "old_credit": 0, "label": "تكرار", "new_row": True}
    with pytest.raises(BusinessError, match="مايتكررش"):
        service.save(None, [dup], None)
    with pytest.raises(BusinessError, match="مش الاتنين"):
        service.save(None, [change(service.section_rows("trs")[0], "5", "5")], None)
    # سجل التعديلات بالسطور
    log = db.fetch_all("SELECT action, details FROM sec.audit_log WHERE entity = 'acc.opening_balances' ORDER BY id")
    assert log and all(r["action"] == "update" for r in log)
    assert len(log[0]["details"]["lines"]["acc.opening_balance_lines"]["after"]) == 3


def test_post_and_unpost(env):
    db, service = env["db"], env["service"]
    with pytest.raises(BusinessError, match="مفيش ولا رصيد"):
        service.post(header(db)["row_version"])
    fill(env)
    service.post(header(db)["row_version"])
    h = header(db)
    assert h["status"] == "posted" and h["posted_at"] is not None
    entry = db.fetch_one("SELECT * FROM acc.journal_entries WHERE source_type = 'opening_balance'")
    assert entry["status"] == "posted" and entry["source_no"] == "OB" and entry["entry_no"] is None
    assert entry["entry_date"] == D and entry["total_debit"] == Decimal("29300.00")
    plug = db.fetch_one("SELECT l.debit, l.credit FROM acc.journal_lines l JOIN acc.accounts a ON a.id = l.account_id "
                        "WHERE l.entry_id = %s AND a.code = '34'", [entry["id"]])
    assert plug["credit"] == Decimal("27500.00")                           # 29,300 − 1,800
    assert service.get_document()["entry_id"] == entry["id"]
    # المرحّل مقفول
    with pytest.raises(BusinessError, match="الغي ترحيله الأول"):
        service.save(None, [change(service.section_rows("trs")[0], "1")], None)
    # إلغاء الترحيل ← القيد اتمسح والمستند مسودة
    service.unpost(header(db)["row_version"])
    assert header(db)["status"] == "draft"
    assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE source_type = 'opening_balance'") == 0
    # حفظ + ترحيل مرة واحدة
    service.save(None, [change(service.section_rows("trs")[0], "30000")], header(db)["row_version"], post=True)
    assert header(db)["status"] == "posted"
    actions = [r["action"] for r in db.fetch_all(
        "SELECT action FROM sec.audit_log WHERE entity = 'acc.opening_balances' ORDER BY id")]
    assert actions == ["update", "post", "unpost", "update", "post"]
    # السنة المقفولة ← مايتلغيش ترحيله
    service.unpost(header(db)["row_version"])
    service.save({"balance_date": dt.date(2025, 12, 31), "notes": ""}, [], header(db)["row_version"], post=True)
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2025'")
    with pytest.raises(BusinessError, match="مقفولة"):
        service.unpost(header(db)["row_version"])


def test_party_screens(env):
    """خانة «رصيد أول المدة» في العملاء والموردين = نفس السطر في المستند (ومايتكررش)."""
    db, service = env["db"], env["service"]
    customers, suppliers = CustomerService(db), SupplierService(db)
    cid = cash_customer(db)
    cust = customers.get_customer(cid)
    assert cust["opening_debit"] == 0 and cust["opening_status"] == "draft"
    data = {k: cust[k] for k in ("name_ar", "group_id", "payment_terms_days", "credit_limit", "currency_code",
                                 "vat_tax_code_id", "wht_tax_code_id", "is_active")}
    customers.save_customer(cid, {**data, "opening": {"debit": "7500", "credit": "", "old_debit": 0,
                                                      "old_credit": 0}}, cust["row_version"])
    row = row_of(service.section_rows("cus"), code="0")
    assert row["debit"] == Decimal("7500.00")
    # من المستند ← بيبان في شاشة العميل
    service.save(None, [change(row, "8000")], None)
    cust = customers.get_customer(cid)
    assert cust["opening_debit"] == Decimal("8000.00")
    # الشاشة شايفة الرصيد القديم ← تعارض (الجهاز التاني غيّره)
    with pytest.raises(ConcurrentEditError, match="رصيد أول المدة"):
        customers.save_customer(cid, {**data, "opening": {"debit": "1", "credit": "", "old_debit": "7500",
                                                          "old_credit": 0}}, cust["row_version"])
    # المورد: دائن
    sid = cash_supplier(db)
    sup = suppliers.get_supplier(sid)
    sdata = {k: sup[k] for k in ("name_ar", "group_id", "payment_terms_days", "currency_code", "vat_tax_code_id",
                                 "wht_tax_code_id", "is_active") if k in sup}
    suppliers.save_supplier(sid, {**sdata, "opening": {"debit": "", "credit": "1800", "old_debit": 0,
                                                       "old_credit": 0}}, sup["row_version"])
    assert service.section_rows("sup")[0]["credit"] == Decimal("1800.00")
    # عميل جديد برصيده في نفس الحفظ
    new_id = customers.save_customer(None, {**data, "name_ar": "شركة تجربة الرصيد",
                                            "opening": {"debit": "", "credit": "300", "old_debit": 0,
                                                        "old_credit": 0}})
    assert row_of(service.section_rows("cus"), ref_id=new_id)["credit"] == Decimal("300.00")
    # بعد الترحيل الخانة مقفولة (التغيير بيترفض · نفس القيمة عادي)
    service.post(header(db)["row_version"])
    cust = customers.get_customer(cid)
    assert cust["opening_status"] == "posted"
    with pytest.raises(BusinessError, match="مرحّل — رصيد أول المدة مايتعدّلش"):
        customers.save_customer(cid, {**data, "opening": {"debit": "9000", "credit": "", "old_debit": "8000",
                                                          "old_credit": 0}}, cust["row_version"])
    customers.save_customer(cid, {**data, "notes": "x", "opening": {"debit": "8000", "credit": "",
                                                                    "old_debit": "8000", "old_credit": 0}},
                            cust["row_version"])


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    perm = "acc.opening_balances"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.edit", f"{perm}.save"})
    fill(env)
    for call in (lambda: service.post(), lambda: service.unpost(),
                 lambda: service.save(None, [], None, post=True)):
        with pytest.raises(PermissionDenied):
            call()
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.opening_balances.%%'")}
    assert codes == {f"{perm}.{a}" for a in ("view", "edit", "save", "export", "post", "unpost")}


def test_two_devices(env):
    """جهازين: كل واحد بيعدّل سطر مختلف ← الاتنين بيتحفظوا · نفس السطر ← التاني بيترفض · الرأس بالإصدار."""
    db = env["db"]
    s1, s2 = env["service"], OpeningBalanceService(Database(db.settings))
    try:
        seen_trs = s1.section_rows("trs")
        seen_cus = s2.section_rows("cus")
        v = header(db)["row_version"]
        s1.save(None, [change(seen_trs[0], "1000")], v)
        s2.save(None, [change(row_of(seen_cus, code="0"), "2000")], v)          # سطر تاني ← عادي
        assert s1.get_document()["total_debit"] == Decimal("3000.00")
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            s2.save(None, [change(seen_trs[0], "5000")], None)                  # نفس السطر بقيمة قديمة
        with pytest.raises(ConcurrentEditError):
            s2.save({"balance_date": dt.date(2026, 2, 1), "notes": ""}, [], v)  # الرأس بإصدار قديم
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def test_screen(env):
    from app.ui.screens.opening_balances_screen import OpeningBalancesScreen

    db, shown = env["db"], env["shown"]
    screen = OpeningBalancesScreen(OpeningBalanceService(db))
    assert screen.current_id == 1 and screen.mode == "view"
    assert not screen.new_button.isVisible() and not screen.delete_button.isVisible()
    screen.show_section("trs")
    assert screen.section_table.rowCount() >= 2
    screen.edit_record()
    assert screen.mode == "edit"
    screen.set_amount(0, "dr", "24300")
    screen.show_section("cus")
    r = next(i for i in range(screen.section_table.rowCount()) if screen.section_rows[i]["code"] == "0")
    screen.set_amount(r, "dr", "5000")
    screen.show_section("sup")
    screen.set_amount(0, "cr", "1800")
    assert "27,500.00" in screen.balance_label.text()                      # الفرق على 34
    screen.save()
    assert screen.mode == "view" and shown[-1] == "saved"
    assert header(db)["row_version"] == screen.current_version
    doc = OpeningBalanceService(db).get_document()
    assert doc["total_debit"] == Decimal("29300.00") and doc["sections"]["cus"]["lines"] == 1
    # «حسابات تانية» + التشيك بوكس
    screen.edit_record()
    screen.show_section("oth")
    assert not screen.all_accounts.isChecked()
    screen.add_other_line({"account_id": acc(db, "1201"), "debit": "65000"})
    screen.save()
    assert [r["account_code"] for r in OpeningBalanceService(db).section_rows("oth")] == ["1201"]
    # ترحيل من العرض ← القيد · وإلغاء الترحيل
    screen.post_document()
    assert header(db)["status"] == "posted" and not screen.edit_button.isEnabled()
    assert screen.unpost_button.isEnabled()
    screen.unpost_document()
    assert header(db)["status"] == "draft"
    screen.close()
