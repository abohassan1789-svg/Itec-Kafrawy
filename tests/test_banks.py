"""اختبارات «البنوك» (0024: البنوك + الحسابات البنكية + حساب الدليل تحت 1102 + البنك الافتراضي + الـ Service +
الصلاحيات + جهازين + الشاشة «البنوك في دليل الحسابات» + شباك البنوك)."""

from __future__ import annotations

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.repositories.bank_repository import format_account_code, format_bank_code
from app.security.session import SESSION
from app.services.account_service import AccountService
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.bank_service import BankService, clean_iban, clean_swift, iban_checksum_ok
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

raise_ = psycopg.errors.RaiseException


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
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "service": BankService(temp_db)}
    SESSION.clear()
    gc.collect()


def make_iban(bban: str, country: str = "EG") -> str:
    """IBAN صحيح (رقم التحقق محسوب) — للاختبارات بس."""
    moved = bban + country + "00"
    check = 98 - int("".join(str(int(ch, 36)) for ch in moved)) % 97
    return f"{country}{check:02d}{bban}"


IBAN1 = make_iban("0003" + "0102" + "30456789012345678")      # EG + 27 رقم
IBAN2 = make_iban("0002" + "0220" + "13300009988220001")


def ba(db, code):
    return db.fetch_one("SELECT * FROM cash.bank_accounts WHERE code = %s", [code])


def bank(db, code):
    return db.fetch_one("SELECT * FROM cash.banks WHERE code = %s", [code])


def acc(db, code):
    return db.fetch_one("SELECT * FROM acc.accounts WHERE code = %s", [code])


def payload(**extra):
    return {"bank_id": 2, "account_type": "current", "currency_code": "EGP", "account_number": "2201-3300-0099",
            "iban": "", "overdraft_limit": "", "maturity_date": None, "notes": None, "is_active": True,
            "account_mode": "auto", **extra}


def bank_payload(**extra):
    return {"name_ar": "البنك التجاري الدولي", "name_en": "Commercial International Bank", "short_name": "CIB",
            "branch": "فرع التجمع", "address": None, "swift_code": "cibeegcx", "contact_name": "أ/ رانيا فتحي",
            "contact_phone": "0111 777 8899", "notes": None, "is_active": True, **extra}


def new_account(db, code, name, currency="EGP"):
    parent = acc(db, "1102")
    return db.fetch_value("INSERT INTO acc.accounts (code, name_ar, parent_id, is_leaf, account_type, nature, "
                          "classification, cash_flow, role_code, currency_code) VALUES (%s, %s, %s, true, 'asset', "
                          "'dr', 'current', 'cash', 'bank', %s) RETURNING id", [code, name, parent["id"], currency])


# ---- الأدوات ---------------------------------------------------------------
def test_codes_iban_swift():
    assert [format_bank_code(n) for n in (1, 12, 999, 1000)] == ["BNK-001", "BNK-012", "BNK-999", "BNK-1000"]
    assert [format_account_code("BNK-001", n) for n in (1, 12, 100)] == ["BNK-001-01", "BNK-001-12", "BNK-001-100"]
    assert iban_checksum_ok(IBAN1) and len(IBAN1) == 29
    assert iban_checksum_ok("GB82WEST12345698765432")                             # المثال الرسمي
    spaced = " ".join(IBAN1[i:i + 4] for i in range(0, 29, 4)).lower()
    assert clean_iban(spaced) == IBAN1 and clean_iban("  ") is None
    bad_check = IBAN1[:-1] + str((int(IBAN1[-1]) + 1) % 10)
    for bad, msg in ((bad_check, "رقم التحقق"), (IBAN1[:-1], "29 حرف"), ("1234", "شكله غلط")):
        with pytest.raises(BusinessError, match=msg):
            clean_iban(bad)
    assert clean_swift(" nbegegcx ") == "NBEGEGCX" and clean_swift("NBEGEGCXXXX") == "NBEGEGCXXXX"
    with pytest.raises(BusinessError, match="8 أو 11"):
        clean_swift("NBEG")


# ---- القاعدة ---------------------------------------------------------------
def test_seed_and_database_rules(env):
    db = env["db"]
    rows = db.fetch_all("SELECT b.code AS bank, ba.code, ba.account_type, ba.currency_code, a.code AS acc, "
                        "ba.owns_account, ba.account_number FROM cash.bank_accounts ba JOIN cash.banks b "
                        "ON b.id = ba.bank_id JOIN acc.accounts a ON a.id = ba.account_id ORDER BY ba.code")
    assert rows == [{"bank": "BNK-001", "code": "BNK-001-01", "account_type": "current", "currency_code": "EGP",
                     "acc": "110201", "owns_account": False, "account_number": None},
                    {"bank": "BNK-002", "code": "BNK-002-01", "account_type": "current", "currency_code": "EGP",
                     "acc": "110202", "owns_account": False, "account_number": None}]
    assert [(b["code"], b["short_name"], b["swift_code"]) for b in db.fetch_all(
        "SELECT * FROM cash.banks ORDER BY seq")] == [("BNK-001", "البنك الأهلي", "NBEGEGCX"),
                                                      ("BNK-002", "بنك مصر", "BMISEGCX")]
    # اسم الحساب المتولّد = نفس أسماء الدليل الجاهز
    assert db.fetch_value("SELECT cash.bank_account_title('البنك الأهلي', 'current', 'EGP')") == "البنك الأهلي — جاري"
    assert db.fetch_value("SELECT cash.bank_account_title('CIB', 'overdraft', 'USD')") == "CIB — جاري مدين USD"
    ins = ("INSERT INTO cash.bank_accounts (bank_id, seq, code, account_type, currency_code, account_id, "
           "overdraft_limit, maturity_date, iban) VALUES (1, 9, '', %s, %s, %s, %s, %s, %s)")
    a1102, a1101 = acc(db, "1102"), acc(db, "110101")
    free = new_account(db, "110203", "حساب حر")
    usd = new_account(db, "110204", "حساب دولار", "USD")
    for args, msg in ((("current", "EGP", a1102["id"], None, None, None), "رئيسي"),
                      (("current", "EGP", a1101["id"], None, None, None), "مش بدور «بنك»"),
                      (("current", "EGP", usd, None, None, None), "غير عملة الحساب البنكي")):
        with pytest.raises(raise_, match=msg):
            db.execute(ins, list(args))
    for args in (("overdraft", "EGP", free, None, None, None),                   # الجاري المدين من غير حد
                 ("current", "EGP", free, 5000, None, None),                     # حد تسهيلات على جاري
                 ("current", "EGP", free, None, date(2027, 1, 1), None),         # استحقاق على جاري
                 ("current", "EGP", free, None, None, "EG12345"),                # IBAN مصري ناقص
                 ("current", "EGP", free, None, None, IBAN1.lower())):           # IBAN صغير
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(ins, list(args))
    with pytest.raises(psycopg.errors.UniqueViolation):                         # حساب الدليل مربوط بالفعل
        db.execute(ins, ["current", "EGP", acc(db, "110201")["id"], None, None, None])
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE cash.banks SET swift_code = 'NBE' WHERE seq = 1")
    db.execute(ins, ["deposit", "EGP", free, None, date(2027, 1, 1), IBAN1])
    dep = ba(db, "BNK-001-09")
    assert dep is not None and dep["maturity_date"] == date(2027, 1, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):                         # IBAN مكرر
        db.execute(ins, ["current", "USD", usd, None, None, IBAN1])
    # رقم الحساب مايتكررش في نفس البنك (من غير الشرط والمسافات)
    db.execute("UPDATE cash.bank_accounts SET account_number = '1023-0456' WHERE code = 'BNK-001-01'")
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute("UPDATE cash.bank_accounts SET account_number = '1023 0456' WHERE code = 'BNK-001-09'")
    db.execute("UPDATE cash.bank_accounts SET account_number = '1023 0456' WHERE code = 'BNK-002-01'")  # بنك تاني
    # الثابت + البنك الافتراضي
    for sql, msg in ("UPDATE cash.bank_accounts SET seq = 7 WHERE code = 'BNK-001-09'", "ثابتين"), \
                    ("UPDATE cash.bank_accounts SET bank_id = 2 WHERE code = 'BNK-001-09'", "ثابتين"), \
                    (f"UPDATE cash.bank_accounts SET account_id = {usd} WHERE code = 'BNK-001-09'", "ثابت"), \
                    ("UPDATE cash.banks SET seq = 9 WHERE seq = 1", "ثابت"), \
                    ("UPDATE cash.bank_accounts SET is_active = false WHERE code = 'BNK-001-01'", "البنك الافتراضي"), \
                    ("DELETE FROM cash.bank_accounts WHERE code = 'BNK-001-01'", "البنك الافتراضي"), \
                    ("UPDATE cash.banks SET is_active = false WHERE seq = 2", "وقّفها الأول"):
        with pytest.raises(raise_, match=msg):
            db.execute(sql)
    with pytest.raises(psycopg.errors.RestrictViolation):
        db.execute("DELETE FROM cash.banks WHERE seq = 2")
    # البنك الموقوف: حساباته ماتتنشّطش
    db.execute("UPDATE cash.bank_accounts SET is_active = false WHERE code = 'BNK-002-01'")
    db.execute("UPDATE cash.banks SET is_active = false WHERE seq = 2")
    with pytest.raises(raise_, match="البنك «بنك مصر» موقوف"):
        db.execute("UPDATE cash.bank_accounts SET is_active = true WHERE code = 'BNK-002-01'")
    db.execute("UPDATE cash.banks SET is_active = true WHERE seq = 2")
    db.execute("UPDATE cash.bank_accounts SET is_active = true WHERE code = 'BNK-002-01'")
    # حماية حساب الدليل من شجرة الحسابات
    a = acc(db, "110202")
    for sql, msg in ((f"DELETE FROM acc.accounts WHERE id = {a['id']}", "امسح الحساب البنكي"),
                     (f"UPDATE acc.accounts SET is_active = false WHERE id = {a['id']}", "وقّف الحساب البنكي"),
                     (f"UPDATE acc.accounts SET currency_code = 'USD' WHERE id = {a['id']}", "من شاشة البنوك"),
                     (f"UPDATE acc.accounts SET role_code = NULL WHERE id = {a['id']}", "دوره"),
                     (f"SELECT acc.split_account({a['id']})", "يتفرّع")):
        with pytest.raises(raise_, match=msg):
            db.execute(sql)
    db.execute("UPDATE acc.accounts SET name_en = 'Banque Misr' WHERE id = %s", [a["id"]])   # تعديل عادي ماشي
    # «البنك الافتراضي» لازم يبقى حساب بنكي نشط
    set_bank = "UPDATE acc.default_accounts SET account_id = %s WHERE key = 'bank'"
    with pytest.raises(raise_, match="مش مربوط بحساب بنكي"):
        db.execute(set_bank, [usd])
    db.execute("UPDATE cash.bank_accounts SET is_active = false WHERE code = 'BNK-001-09'")
    with pytest.raises(raise_, match="موقوف"):
        db.execute(set_bank, [free])
    db.execute(set_bank, [a["id"]])                                                # بنك مصر بقى الافتراضي
    with pytest.raises(raise_, match="البنك الافتراضي"):
        db.execute("UPDATE cash.bank_accounts SET is_active = false WHERE code = 'BNK-002-01'")
    db.execute(set_bank, [acc(db, "110201")["id"]])
    # النوع والعملة بيغيّروا حساب الدليل (والاسم لو كان المتولّد) · اسم البنك بيغيّر أسماء حساباته
    db.execute("UPDATE cash.bank_accounts SET account_type = 'savings', currency_code = 'USD' WHERE code = 'BNK-002-01'")
    assert (acc(db, "110202")["currency_code"], acc(db, "110202")["name_ar"]) == ("USD", "بنك مصر — توفير USD")
    db.execute("UPDATE cash.banks SET short_name = 'مصر' WHERE seq = 2")
    assert acc(db, "110202")["name_ar"] == "مصر — توفير USD"
    db.execute("UPDATE acc.accounts SET name_ar = 'اسم بإيدي' WHERE id = %s", [a["id"]])
    db.execute("UPDATE cash.bank_accounts SET account_type = 'current' WHERE code = 'BNK-002-01'")
    assert acc(db, "110202")["name_ar"] == "اسم بإيدي"                            # الاسم اللي اتكتب بإيد بيفضل
    assert bank(db, "BNK-002")["row_version"] == 4


# ---- الـ Service -------------------------------------------------------------
def test_service_banks(env):
    db, service = env["db"], env["service"]
    assert service.new_bank_defaults()["code"] == "BNK-003"
    bid = service.save_bank(None, bank_payload())
    b = service.get_bank(bid)
    assert (b["code"], b["swift_code"], b["short_name"], b["accounts_count"]) == ("BNK-003", "CIBEEGCX", "CIB", 0)
    for bad, msg in ((dict(name_ar=" "), "اسم البنك"), (dict(name_ar="بنك  مصر"), "بنفس الاسم"),
                     (dict(swift_code="AB"), "8 أو 11"), (dict(contact_phone="1" * 31), "أطول")):
        with pytest.raises(BusinessError, match=msg):
            service.save_bank(None, bank_payload(**bad))
    assert [r["code"] for r in service.list_banks("cib")] == ["BNK-003"]
    assert [r["code"] for r in service.list_banks("003")] == ["BNK-003"]
    assert [r["code"] for r in service.active_banks()] == ["BNK-001", "BNK-002", "BNK-003"]
    # بنك عليه حسابات: مايتوقفش وحساب نشط، ومايتمسحش
    misr = service.get_bank(2)
    with pytest.raises(BusinessError, match="حساب نشط"):
        service.set_bank_active(2, False, misr["row_version"])
    with pytest.raises(BusinessError, match="حساب نشط"):
        service.save_bank(2, {**misr, "is_active": False}, misr["row_version"])
    with pytest.raises(BusinessError, match="امسحهم الأول"):
        service.delete_bank(2, misr["row_version"])
    # تغيير الاسم المختصر ← اسم حسابه في الدليل اتغيّر + سطر audit للحساب
    service.save_bank(2, {**misr, "short_name": "بنك مصر الرئيسي"}, misr["row_version"])
    assert acc(db, "110202")["name_ar"] == "بنك مصر الرئيسي — جاري"
    audits = db.fetch_all("SELECT entity, action, record_label FROM sec.audit_log WHERE entity IN "
                          "('cash.banks', 'acc.accounts') ORDER BY id")
    assert audits[-2:] == [{"entity": "acc.accounts", "action": "update", "record_label": "110202 بنك مصر الرئيسي — جاري"},
                           {"entity": "cash.banks", "action": "update", "record_label": "BNK-002 بنك مصر"}]
    service.set_bank_active(bid, False, 1)
    assert service.get_bank(bid)["is_active"] is False and [r["code"] for r in service.active_banks()][-1] == "BNK-002"
    service.delete_bank(bid, 2)
    assert bank(db, "BNK-003") is None


def test_service_accounts_auto_pick_and_rules(env):
    db, service = env["db"], env["service"]
    d = service.new_defaults()
    assert (d["bank_id"], d["code"], d["currency_code"], d["account_type"], d["account_mode"]) == \
           (1, "BNK-001-02", "EGP", "current", "auto")
    assert d["suggested_account"] == {"code": "110203", "parent_code": "1102", "parent_name": "البنوك"}
    assert service.suggested_code(2) == "BNK-002-02"
    nid = service.save_account(None, payload(bank_id=1, currency_code="USD", account_number="1023-0456-7890",
                                             iban=" ".join(IBAN1[i:i + 4] for i in range(0, 29, 4))))
    n = service.get_account(nid)
    assert (n["code"], n["account_code"], n["account_name"], n["parent_code"], n["iban"], n["owns_account"],
            n["is_default"]) == ("BNK-001-02", "110203", "البنك الأهلي — جاري USD", "1102", IBAN1, True, False)
    a = acc(db, "110203")
    assert (a["role_code"], a["currency_code"], a["is_leaf"], a["name_en"]) == \
           ("bank", "USD", True, "National Bank of Egypt — Current")
    audits = db.fetch_all("SELECT entity, action, record_label FROM sec.audit_log WHERE entity IN "
                          "('cash.bank_accounts', 'acc.accounts') ORDER BY id")
    assert audits == [{"entity": "acc.accounts", "action": "create", "record_label": "110203 البنك الأهلي — جاري USD"},
                      {"entity": "cash.bank_accounts", "action": "create",
                       "record_label": "BNK-001-02 البنك الأهلي — جاري"}]
    assert [r["is_default"] for r in service.lookup_accounts()] == [True, False, False]
    # التحقق
    for bad, msg in ((dict(account_number=""), "رقم الحساب مطلوب"), (dict(account_number="--"), "فيه أرقام"),
                     (dict(account_number="1023 0456 7890", bank_id=1), "متسجل في نفس البنك"),
                     (dict(iban=IBAN1), "متسجل على الحساب البنكي BNK-001-02"),
                     (dict(iban=IBAN1[:-1] + "0" if IBAN1[-1] != "0" else IBAN1[:-1] + "1"), "رقم التحقق"),
                     (dict(account_type="x"), "نوع الحساب"), (dict(currency_code="XXX"), "مش موجودة"),
                     (dict(account_type="overdraft"), "حد التسهيلات"),
                     (dict(account_type="overdraft", overdraft_limit="-5"), "أكبر من صفر"),
                     (dict(account_type="overdraft", overdraft_limit="abc"), "رقم"),
                     (dict(account_type="deposit", maturity_date="2027-13-01"), "تاريخ غلط"),
                     (dict(bank_id=None), "اختار البنك"), (dict(bank_id=99), "مش موجود"),
                     (dict(account_mode="x"), "يتعمل لوحده"),
                     (dict(account_mode="pick", account_id=None), "اختار الحساب"),
                     (dict(account_mode="pick", account_id=acc(db, "110201")["id"]), "مربوط بالفعل"),
                     (dict(account_mode="pick", account_id=acc(db, "1102")["id"]), "رئيسي"),
                     (dict(account_mode="pick", account_id=acc(db, "110101")["id"]), "مش بدور")):
        with pytest.raises(BusinessError, match=msg):
            service.save_account(None, payload(**bad))
    assert acc(db, "110204") is None                                        # الحساب مااتعملش مع الغلط
    # الجاري المدين والوديعة
    od = service.save_account(None, payload(account_type="overdraft", overdraft_limit="200,000",
                                            account_number="2201-9", maturity_date=date(2027, 1, 1)))
    o = service.get_account(od)
    assert (o["code"], o["overdraft_limit"], o["maturity_date"], o["account_name"]) == \
           ("BNK-002-02", Decimal("200000.00"), None, "بنك مصر — جاري مدين")
    dep = service.save_account(None, payload(account_type="deposit", account_number="2201-10",
                                             maturity_date="2027-02-01", overdraft_limit="5"))
    assert (service.get_account(dep)["maturity_date"], service.get_account(dep)["overdraft_limit"]) == \
           (date(2027, 2, 1), None)
    # «حساب موجود»: حساب يورو حر تحت 1102
    accounts = AccountService(db)
    eur = accounts.save_account(None, {**accounts.new_defaults(acc(db, "1102")["id"]), "name_ar": "حساب يورو",
                                       "currency_code": "EUR"})
    assert [x["code"] for x in service.free_accounts()] == ["110206"]
    with pytest.raises(BusinessError, match="غير عملة الحساب البنكي"):
        service.save_account(None, payload(account_number="E-1", account_mode="pick", account_id=eur))
    eur_id = service.save_account(None, payload(account_number="E-1", currency_code="EUR", account_mode="pick",
                                                account_id=eur))
    assert (service.get_account(eur_id)["owns_account"], service.free_accounts()) == (False, [])
    assert acc(db, "110206")["name_ar"] == "حساب يورو"                    # الاسم بتاعه زي ما هو
    # تعديل: النوع والعملة بيغيّروا الدليل + سطر audit للحساب · البنك والحساب ثابتين
    n = service.get_account(nid)
    service.save_account(nid, {**n, "account_type": "savings", "currency_code": "EGP", "bank_id": 2,
                               "account_id": 999}, n["row_version"])
    n2 = service.get_account(nid)
    assert (n2["code"], n2["bank_id"], n2["account_code"], n2["account_name"], n2["currency_code"]) == \
           ("BNK-001-02", 1, "110203", "البنك الأهلي — توفير", "EGP")
    assert db.fetch_value("SELECT count(*) FROM sec.audit_log WHERE entity = 'acc.accounts' AND action = 'update'") == 1
    # الحسابات اللي جت من السكريبت من غير رقم ← التعديل بيطلب الرقم
    seed = service.get_account(1)
    with pytest.raises(BusinessError, match="رقم الحساب مطلوب"):
        service.save_account(1, {**seed, "notes": "x"}, seed["row_version"])
    # الإيقاف: الافتراضي لأ · البنك الموقوف بيمنع التفعيل
    with pytest.raises(BusinessError, match="البنك الافتراضي"):
        service.set_active(1, False, seed["row_version"])
    with pytest.raises(BusinessError, match="البنك الافتراضي"):
        service.save_account(1, {**seed, "account_number": "1", "is_active": False}, seed["row_version"])
    with pytest.raises(BusinessError, match="البنك الافتراضي"):
        service.delete_account(1, seed["row_version"])
    for x in service.lookup_accounts():
        if x["bank_id"] == 2:
            service.set_active(x["id"], False, x["row_version"])
    misr = service.get_bank(2)
    service.set_bank_active(2, False, misr["row_version"])
    o = service.get_account(od)
    with pytest.raises(BusinessError, match="موقوف — نشّطه"):
        service.set_active(od, True, o["row_version"])
    with pytest.raises(BusinessError, match="موقوف — نشّطه"):
        service.save_account(None, payload(account_number="X-9"))
    assert [b["code"] for b in service.active_banks()] == ["BNK-001"] and service.new_defaults()["bank_id"] == 1
    # البحث والفلاتر (الكود / البنك / الرقم من غير الشرط / IBAN / حساب الدليل)
    codes = lambda rows: [r["code"] for r in rows]                                 # noqa: E731
    assert len(service.lookup_accounts()) == 6
    for key in ("BNK-001-02", "الأهلي", "102304567890", "1023-0456", IBAN1[4:12], "110203"):
        assert "BNK-001-02" in codes(service.list_accounts(key)), key
    assert codes(service.list_accounts(status="active")) == ["BNK-001-01", "BNK-001-02"]
    assert codes(service.list_accounts(page=2, page_size=4)) == ["BNK-002-03", "BNK-002-04"]
    chart = {r["code"]: r["bank_account_code"] for r in service.chart_rows()}
    assert chart == {"1102": None, "110201": "BNK-001-01", "110202": "BNK-002-01", "110203": "BNK-001-02",
                     "110204": "BNK-002-02", "110205": "BNK-002-03", "110206": "BNK-002-04"}
    # المسح: الحساب البنكي + حساب الدليل اللي اتعمل معاه · اللي اتختار بيفضل
    service.delete_account(nid, n2["row_version"])
    assert ba(db, "BNK-001-02") is None and acc(db, "110203") is None
    e = service.get_account(eur_id)
    service.delete_account(eur_id, e["row_version"])
    assert acc(db, "110206") is not None
    assert service.new_defaults()["suggested_account"]["code"] == "110207"      # بعد الأكبر


def test_permissions(env):
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {"cash.banks.view", "cash.banks.create", "cash.banks.save"})
    nid = service.save_account(None, payload())
    bid = service.save_bank(None, bank_payload())
    n, b = service.get_account(nid), service.get_bank(bid)
    for call in (lambda: service.save_account(nid, {**n, "notes": "x"}, n["row_version"]),
                 lambda: service.set_active(nid, False, n["row_version"]),
                 lambda: service.delete_account(nid, n["row_version"]),
                 lambda: service.save_bank(bid, {**b, "branch": "x"}, b["row_version"]),
                 lambda: service.set_bank_active(bid, False, b["row_version"]),
                 lambda: service.delete_bank(bid, b["row_version"])):
        with pytest.raises(PermissionDenied):
            call()
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'cash.banks.%%'")}
    assert codes == {f"cash.banks.{a}" for a in ("view", "create", "edit", "save", "delete", "export")}


def test_two_devices(env):
    """جهازين: التعديل في نفس الوقت بيترفض للتاني · حسابين جداد في نفس البنك في نفس اللحظة بياخدوا كود ومسلسل
    مختلفين وحساب دليل مختلف · نفس حساب الدليل من جهازين ← واحد بس اللي بيتربط · وقف البنك وتفعيل حسابه مع بعض."""
    db = env["db"]
    dev1, dev2 = BankService(db), BankService(Database(db.settings))
    try:
        seen1, seen2 = dev1.get_account(2), dev2.get_account(2)
        dev2.save_account(2, {**seen2, "account_number": "2201-1"}, seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            dev1.save_account(2, {**seen1, "account_number": "2201-2"}, seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.set_active(2, False, seen1["row_version"])
        b1, b2 = dev1.get_bank(2), dev2.get_bank(2)
        dev2.save_bank(2, {**b2, "branch": "روكسي"}, b2["row_version"])
        with pytest.raises(ConcurrentEditError):
            dev1.save_bank(2, {**b1, "branch": "وسط البلد"}, b1["row_version"])
        assert dev1.get_account(2)["account_number"] == "2201-1" and dev1.get_bank(2)["branch"] == "روكسي"
        # حسابين جداد في بنك مصر في نفس اللحظة: التاني بيستنى قفل الترقيم ← BNK-002-02 و BNK-002-03
        results: dict[str, object] = {}

        def second_device():
            try:
                results["id"] = dev2.save_account(None, payload(account_number="B-2"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=second_device)
        with db.atomic() as tx:
            seq = dev1.repository.next_account_seq(2, tx)          # الجهاز الأول ماسك قفل الترقيم
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()                               # التاني مستني
            clean = dev1._clean_account(payload(account_number="B-1"), None)
            clean.update(bank_id=2, seq=seq, owns_account=True,
                         account_id=dev1._create_account(tx, dev1.repository.get_bank(2, ex=tx), clean))
            dev1.repository.insert_account(clean, ex=tx)
        worker.join(10)
        assert "error" not in results, results
        got = {r["code"]: (r["acc"], r["account_number"]) for r in db.fetch_all(
            "SELECT b.code, a.code AS acc, b.account_number FROM cash.bank_accounts b "
            "JOIN acc.accounts a ON a.id = b.account_id WHERE b.bank_id = 2 AND b.seq > 1")}
        assert got == {"BNK-002-02": ("110203", "B-1"), "BNK-002-03": ("110204", "B-2")}
        # نفس حساب الدليل من جهازين: التاني بيستنى قفل الحساب وبعدين بيلاقيه مربوط
        free = new_account(db, "110205", "حساب حر")
        results.clear()

        def pick_second():
            try:
                results["id"] = dev2.save_account(None, payload(account_number="P-2", account_mode="pick",
                                                                account_id=free))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=pick_second)
        with db.atomic() as tx:
            dev1.accounts.get_account(free, ex=tx, for_update=True)  # الجهاز الأول ماسك الحساب
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
            clean = dev1._clean_account(payload(account_number="P-1"), None)
            clean.update(bank_id=2, seq=50, account_id=free, owns_account=False)
            dev1.repository.insert_account(clean, ex=tx)
        worker.join(10)
        assert isinstance(results.get("error"), BusinessError) and "مربوط بالفعل" in str(results["error"])
        # جهاز بيوقف «البنك الأهلي» (لسه ماحفظش) والتاني بيضيف عليه حساب نشط ← بيستنى وبعدين يترفض
        db.execute("UPDATE acc.default_accounts SET account_id = %s WHERE key = 'bank'", [acc(db, "110202")["id"]])
        db.execute("UPDATE cash.bank_accounts SET is_active = false WHERE bank_id = 1")
        results.clear()

        def add_on_bank1():
            try:
                results["id"] = dev2.save_account(None, payload(bank_id=1, account_number="N-1"))
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        worker = threading.Thread(target=add_on_bank1)
        with db.atomic() as tx:
            tx.execute("UPDATE cash.banks SET is_active = false WHERE id = 1")
            worker.start()
            worker.join(0.7)
            assert worker.is_alive()
        worker.join(10)
        assert "error" in results and "موقوف" in str(results["error"]), results
        assert db.fetch_value("SELECT count(*) FROM cash.bank_accounts WHERE bank_id = 1") == 1
    finally:
        dev2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.banks_screen import BanksScreen

    return BanksScreen(BankService(db))


def test_screen_tree_link_new_edit_toggle_delete(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    free = new_account(db, "110203", "حساب بنكي قديم")
    screen = make_screen(db)
    try:
        assert screen.current_record["code"] == "BNK-001-01" and "⭐" in screen.default_label.text()
        texts = {screen.tree.topLevelItem(0).child(i).text(0): screen.tree.topLevelItem(0).child(i).text(1)
                 for i in range(screen.tree.topLevelItem(0).childCount())}
        assert texts == {"⁦110201⁩  البنك الأهلي — جاري": "🏦 BNK-001-01 ⭐",
                         "⁦110202⁩  بنك مصر — جاري": "🏦 BNK-002-01", "⁦110203⁩  حساب بنكي قديم": "⚠ مش مربوط"}
        assert screen.tree.topLevelItem(0).text(0) == "⁦1102⁩  البنوك"
        assert "مربوط ⁦2⁩ · مش مربوط ⁦1⁩" in screen.count_label.text()
        assert screen.table.rowCount() == 2 and screen.bank_swift.text() == "NBEGEGCX"
        assert not screen._main_form.isRowVisible(screen.overdraft_edit)
        # دوس على الحساب اللي مش مربوط ← اللوحة الصفرا ← «اعمل حساب بنكي عليه»
        screen.tree.setCurrentItem(screen.items[free])
        assert screen.current_id is None and screen.free_account["id"] == free
        assert not screen.unlinked_frame.isHidden() and "110203" in screen.unlinked_label.text()
        screen.link_free_account()
        assert screen.mode == "new" and screen.mode_pick.isChecked() and screen.account_pick.currentData() == free
        assert screen.unlinked_frame.isHidden() and not screen.tree.isEnabled()
        screen.bank_combo.setCurrentIndex(screen.bank_combo.findData(2))
        assert screen.code_edit.text() == "BNK-002-02 (مقترح)"
        screen.number_edit.setText("2201-3300")
        screen.iban_edit.setText(IBAN2)
        screen.save()
        assert shown[-1] == "saved" and screen.mode == "view" and screen.code_edit.text() == "BNK-002-02"
        assert ba(db, "BNK-002-02")["account_id"] == free and screen.items[free].text(1) == "🏦 BNK-002-02"
        assert screen.iban_edit.text().replace(" ", "") == IBAN2
        # جديد «يتعمل لوحده» + جاري مدين بحد تسهيلات
        screen.new_record(False)                                   # زرار «جديد» بيبعت checked=False
        assert screen.mode == "new" and screen.mode_auto.isChecked() and "110204" in screen.account_view.text()
        screen.type_combo.setCurrentIndex(screen.type_combo.findData("overdraft"))
        assert screen._main_form.isRowVisible(screen.overdraft_edit)
        screen.number_edit.setText("777")
        screen.save()
        assert "حد التسهيلات" in shown[-1] and screen.mode == "new"
        screen.overdraft_edit.setText("150000")
        screen.save()
        o = ba(db, "BNK-001-02")
        assert (o["overdraft_limit"], acc(db, "110204")["name_ar"]) == (150000, "البنك الأهلي — جاري مدين")
        # رقم مكرر ← رسالة والفورم بيفضل
        screen.new_record()
        screen.number_edit.setText("777")
        screen.save()
        assert "متسجل في نفس البنك" in shown[-1] and screen.mode == "new"
        screen.cancel()
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.load(int(o["id"]))
        screen.edit_record()
        assert not screen.bank_combo.isEnabled() and screen.number_edit.isEnabled()
        db.execute("UPDATE cash.bank_accounts SET notes = 'من جهاز تاني' WHERE code = 'BNK-001-02'")
        screen.notes.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit" and ba(db, "BNK-001-02")["notes"] == "من جهاز تاني"
        screen.cancel()
        # الافتراضي مايتوقفش · إيقاف حساب ← فلتر «الموقوفة»
        screen.load(1)
        screen.toggle_active()
        assert "البنك الافتراضي" in shown[-1] and ba(db, "BNK-001-01")["is_active"] is True
        screen.load(int(o["id"]))
        screen.toggle_active()
        assert ba(db, "BNK-001-02")["is_active"] is False
        chip = next(b for b in screen.status_buttons.buttons() if b.property("status") == "inactive")
        chip.click()
        shown_codes = [screen.chart_by_id(i)["code"] for i in screen.items]
        assert shown_codes == ["1102", "110204"]
        next(b for b in screen.status_buttons.buttons() if b.property("status") == "all").click()
        # البحث برقم الحساب
        screen.search.setText("2201 3300")
        assert [screen.chart_by_id(i)["code"] for i in screen.items] == ["1102", "110203"]
        screen.search.clear()
        # المسح ← حساب الدليل اللي اتعمل معاه بيتمسح
        screen.load(int(o["id"]))
        screen.delete()
        assert ba(db, "BNK-001-02") is None and acc(db, "110204") is None and screen.current_id is None
        assert acc(db, "110204") is None and "110204" not in [screen.chart_by_id(i)["code"] for i in screen.items]
    finally:
        screen.close()


def test_bank_list_window(env):
    from app.ui.screens.banks_screen import BankListScreen

    db, shown = env["db"], env["shown"]
    window = BankListScreen(BankService(db))
    try:
        assert window.table.rowCount() == 2 and window.current_record["code"] == "BNK-001"
        assert "عليه 1 حساب بنكي" in window.accounts_label.text()
        window.new_record()
        assert window.code_edit.text() == "BNK-003 (مقترح)"
        window.name_ar.setText("البنك التجاري الدولي")
        window.short_name.setText("CIB")
        window.swift.setText("cibeegcx")
        window.save()
        assert shown[-1] == "saved" and bank(db, "BNK-003")["swift_code"] == "CIBEEGCX"
        window.toggle_active()
        assert bank(db, "BNK-003")["is_active"] is False
        window.delete()
        assert bank(db, "BNK-003") is None
        window.load(2)
        window.delete()
        assert "امسحهم الأول" in shown[-1] and bank(db, "BNK-002") is not None
    finally:
        window.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    group = reg.section("cash").groups[0]
    assert group.title == "الخزائن والبنوك" and [t.code for t in group.targets] == ["treasuries", "banks", "cash_transfers"]
    assert reg.find_target("cash", "banks").name_ar == "البنوك"
    assert "banks" in SCREEN_FACTORIES
