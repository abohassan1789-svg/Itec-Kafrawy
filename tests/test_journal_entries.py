"""اختبارات «القيود اليومية» (0031: المسودة والترحيل وإلغاؤه · الرقم الرسمي من أول ترحيل وبعد مليون قيد · التوازن ·
السنة المقفولة · الحساب الفرعي النشط · الطرف على حسابات الرقابة · مركز التكلفة · التفريع بينقل السطور للـ «— عام» ·
حماية الحساب والمركز والسنة · الـ Service · الصلاحيات · جهازين · الشاشة)."""

from __future__ import annotations

import datetime as dt
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.journal_service import JournalService, parse_money
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

D = dt.date(2026, 10, 31)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: asked.append(text) or True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "asked": asked, "service": JournalService(temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def acc(db, code):
    return one(db, "SELECT id FROM acc.accounts WHERE code = %s", code)


def cc(db, code):
    return one(db, "SELECT id FROM acc.cost_centers WHERE code = %s", code)


def cash_customer(db):
    return one(db, "SELECT id FROM party.customers WHERE code = '0'")


def entry(db, eid):
    return db.fetch_one("SELECT * FROM acc.journal_entries WHERE id = %s", [eid])


def pay(db, amount="1500", d=D, **extra):
    """إيجار مدين ← الخزينة دائن (متوازن)."""
    return {"entry_date": d, "description": "استحقاق إيجار أكتوبر", "reference": "عقد 12",
            "lines": [{"account_id": acc(db, "5202"), "description": "إيجار", "debit": amount, "credit": "",
                       "cost_center_id": cc(db, "11")},
                      {"account_id": acc(db, "110101"), "debit": "", "credit": amount}], **extra}


def raw_entry(db, d=D, lines=(("5202", 100, 0), ("110101", 0, 100))):
    """قيد مسودة بالـ SQL على طول (من غير الـ Service)."""
    eid = one(db, "INSERT INTO acc.journal_entries (entry_date, fiscal_year_id, description) "
                  "VALUES (%s, (SELECT id FROM core.fiscal_years WHERE %s BETWEEN start_date AND end_date), 'قيد') "
                  "RETURNING id", d, d)
    for n, (code, dr, cr) in enumerate(lines, start=1):
        party = cash_customer(db) if code == "1103" else None
        db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit, credit, customer_id) "
                   "VALUES (%s, %s, %s, %s, %s, %s)", [eid, n, acc(db, code), dr, cr, party])
    return eid


def post_sql(db, eid):
    db.execute("UPDATE acc.journal_entries SET status = 'posted' WHERE id = %s", [eid])


# ---- القاعدة ---------------------------------------------------------------
def test_parse_money():
    assert parse_money(" 1,250.5 ", "x") == Decimal("1250.50") and parse_money("", "x") == 0
    assert parse_money("١٢٣", "x") == Decimal("123.00")
    for bad in ("-1", "abc", "1.005"):
        with pytest.raises(BusinessError):
            parse_money(bad, "x")


def test_database_rules(env):
    db = env["db"]
    eid = raw_entry(db, lines=(("5202", 100, 0), ("110101", 0, 90)))
    e = entry(db, eid)
    assert e["status"] == "draft" and e["entry_no"] is None and e["draft_no"] > 0
    with pytest.raises(psycopg.errors.RaiseException, match="مش متوازن"):
        post_sql(db, eid)
    db.execute("UPDATE acc.journal_lines SET credit = 100 WHERE entry_id = %s AND line_no = 2", [eid])
    post_sql(db, eid)
    e = entry(db, eid)
    assert e["entry_no"] == "JV-2026-00001" and e["total_debit"] == 100 and e["lines_count"] == 2
    # المرحّل مقفول: الرأس والسطور والمسح
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل — مايتعدّلش"):
        db.execute("UPDATE acc.journal_entries SET description = 'x' WHERE id = %s", [eid])
    with pytest.raises(psycopg.errors.RaiseException, match="سطوره ماتتعدّلش"):
        db.execute("UPDATE acc.journal_lines SET debit = 5 WHERE entry_id = %s AND line_no = 1", [eid])
    with pytest.raises(psycopg.errors.RaiseException, match="مايتمسحش"):
        db.execute("DELETE FROM acc.journal_entries WHERE id = %s", [eid])
    # إلغاء الترحيل ← مسودة برقمه · التعديل · الترحيل تاني بنفس الرقم
    db.execute("UPDATE acc.journal_entries SET status = 'draft' WHERE id = %s", [eid])
    e = entry(db, eid)
    assert e["status"] == "draft" and e["entry_no"] == "JV-2026-00001" and e["posted_at"] is None
    db.execute("UPDATE acc.journal_lines SET debit = 120 WHERE entry_id = %s AND line_no = 1", [eid])
    db.execute("UPDATE acc.journal_lines SET credit = 120 WHERE entry_id = %s AND line_no = 2", [eid])
    post_sql(db, eid)
    assert entry(db, eid)["entry_no"] == "JV-2026-00001" and entry(db, eid)["total_credit"] == 120
    db.execute("UPDATE acc.journal_entries SET status = 'draft' WHERE id = %s", [eid])
    with pytest.raises(psycopg.errors.RaiseException, match="خد رقم رسمي"):
        db.execute("DELETE FROM acc.journal_entries WHERE id = %s", [eid])
    with pytest.raises(psycopg.errors.RaiseException, match="نفس السنة"):
        db.execute("UPDATE acc.journal_entries SET entry_date = '2025-12-31' WHERE id = %s", [eid])
    post_sql(db, eid)
    # سطر واحد ← مايترحّلش · المسودة اللي ماخدتش رقم بتتمسح
    lone = raw_entry(db, lines=(("5202", 50, 0),))
    with pytest.raises(psycopg.errors.RaiseException, match="سطرين"):
        post_sql(db, lone)
    db.execute("DELETE FROM acc.journal_entries WHERE id = %s", [lone])
    assert entry(db, lone) is None
    # السطر: مدين ودائن مع بعض · الحساب الرئيسي · الطرف
    with pytest.raises(psycopg.errors.CheckViolation):
        raw_entry(db, lines=(("5202", 10, 10),))
    with pytest.raises(psycopg.errors.RaiseException, match="رئيسي"):
        raw_entry(db, lines=(("52", 10, 0),))
    with pytest.raises(psycopg.errors.RaiseException, match="لازم تختار العميل"):
        db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit) VALUES (%s, 9, %s, 5)",
                   [raw_entry(db, lines=()), acc(db, "1103")])
    with pytest.raises(psycopg.errors.RaiseException, match="مالوش طرف"):
        db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit, customer_id) "
                   "VALUES (%s, 1, %s, 5, %s)", [raw_entry(db, lines=()), acc(db, "5202"), cash_customer(db)])
    with pytest.raises(psycopg.errors.RaiseException, match="طرفه المورد"):
        db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit, customer_id) "
                   "VALUES (%s, 1, %s, 5, %s)", [raw_entry(db, lines=()), acc(db, "2101"), cash_customer(db)])
    party_entry = raw_entry(db, lines=(("1103", 70, 0), ("110101", 0, 70)))
    post_sql(db, party_entry)
    # مركز التكلفة: الرئيسي مرفوض · «لازم مركز تكلفة»
    with pytest.raises(psycopg.errors.RaiseException, match="مركز التكلفة «1 "):
        db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit, cost_center_id) "
                   "VALUES (%s, 1, %s, 5, %s)", [raw_entry(db, lines=()), acc(db, "5202"), cc(db, "1")])
    db.execute("UPDATE acc.accounts SET requires_cost_center = true WHERE code = '5203'")
    with pytest.raises(psycopg.errors.RaiseException, match="لازم يتكتب عليه مركز تكلفة"):
        raw_entry(db, lines=(("5203", 5, 0),))
    # الحساب الموقوف بعد المسودة ← مايترحّلش
    draft = raw_entry(db, lines=(("1201", 30, 0), ("110101", 0, 30)))
    db.execute("UPDATE acc.accounts SET is_active = false WHERE code = '1201'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوف — مايترحّلش"):
        post_sql(db, draft)
    db.execute("UPDATE acc.accounts SET is_active = true WHERE code = '1201'")
    # الحساب اللي عليه قيود: مايتمسحش ولا يتغيّر نوع طرفه
    with pytest.raises(psycopg.errors.RaiseException, match="عليه .* سطر في القيود"):
        db.execute("DELETE FROM acc.accounts WHERE code = '1201'")
    with pytest.raises(psycopg.errors.RaiseException, match="نوع الطرف"):
        db.execute("UPDATE acc.accounts SET party_kind = NULL WHERE code = '1103'")
    # السنة: ماتتقفلش وفيها مسودات · المقفولة مفيهاش أي تعديل ولا قيد جديد · اللي عليها قيود ماتتمسحش
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة — رحّلهم أو امسحهم"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")
    old = raw_entry(db, d=dt.date(2025, 6, 1))
    post_sql(db, old)
    assert entry(db, old)["entry_no"] == "JV-2025-00001"
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2025'")
    with pytest.raises(psycopg.errors.RaiseException, match="مقفولة"):
        db.execute("UPDATE acc.journal_entries SET status = 'draft' WHERE id = %s", [old])
    with pytest.raises(psycopg.errors.RaiseException, match="مقفولة"):
        raw_entry(db, d=dt.date(2025, 7, 1))
    db.execute("UPDATE core.fiscal_years SET status = 'open', closed_at = NULL WHERE year_code = '2025'")
    with pytest.raises(psycopg.errors.RaiseException, match="عليها .* قيد"):
        db.execute("DELETE FROM core.fiscal_years WHERE year_code = '2025'")
    with pytest.raises(psycopg.errors.RaiseException, match="برّا المدة"):
        db.execute("UPDATE core.fiscal_years SET start_date = '2025-07-01' WHERE year_code = '2025'")


def test_numbering_after_a_million(env):
    """الرقم بعد 99,999 وبعد 999,999 بيكمّل من غير ما يتقص ولا يتكرر (رد المستخدم: «مليون قيد من غير إيرور»)."""
    db = env["db"]
    first = raw_entry(db)
    post_sql(db, first)
    for last, expected in ((99_999, "JV-2026-100000"), (999_999, "JV-2026-1000000"), (1_000_000, "JV-2026-1000001")):
        db.execute("UPDATE core.document_sequences SET last_no = %s WHERE doc_type = 'journal'", [last])
        eid = raw_entry(db)
        post_sql(db, eid)
        assert entry(db, eid)["entry_no"] == expected
    assert one(db, "SELECT count(DISTINCT entry_no) FROM acc.journal_entries") == 4


def test_split_moves_lines(env):
    """تفريع حساب أو مركز عليه قيود (حتى المرحّلة) ← السطور بتنزل على «— عام» (قرار المستخدم (أ))."""
    db = env["db"]
    eid = raw_entry(db, lines=(("1201", 100, 0), ("110101", 0, 100)))
    db.execute("UPDATE acc.journal_lines SET cost_center_id = %s WHERE entry_id = %s AND line_no = 1",
               [cc(db, "11"), eid])
    post_sql(db, eid)
    with pytest.raises(psycopg.errors.RaiseException, match="استخدم «\\+ فرعي تحت المحدد»"):
        db.execute("UPDATE acc.accounts SET is_leaf = false WHERE code = '1201'")
    general = one(db, "SELECT acc.split_account(%s)", acc(db, "1201"))
    assert one(db, "SELECT code FROM acc.accounts WHERE id = %s", general) == "120101"
    assert one(db, "SELECT account_id FROM acc.journal_lines WHERE entry_id = %s AND line_no = 1", eid) == general
    general_cc = one(db, "SELECT acc.split_cost_center(%s)", cc(db, "11"))
    assert one(db, "SELECT cost_center_id FROM acc.journal_lines WHERE entry_id = %s AND line_no = 1", eid) == general_cc
    assert entry(db, eid)["status"] == "posted"
    with pytest.raises(psycopg.errors.RaiseException, match="وقّفه بدل ما تمسحه"):
        db.execute("DELETE FROM acc.cost_centers WHERE id = %s", [general_cc])


# ---- الـ Service ----------------------------------------------------------
def test_service(env):
    db, service = env["db"], env["service"]
    # مسودة مش متوازنة بتتحفظ — ومايترحّلش
    data = pay(db)
    data["lines"][1]["credit"] = "1400"
    eid = service.save_draft(None, data)
    e = service.get_entry(eid)
    assert e["status"] == "draft" and e["label"].startswith("مسودة") and e["total_debit"] == 1500
    assert e["lines"][0]["cost_center_code"] == "11" and e["lines"][1]["description"] is None
    with pytest.raises(BusinessError, match="مش متوازن"):
        service.post(eid, e["row_version"])
    # تعديل + ترحيل في نفس الخطوة
    service.save_draft(eid, pay(db), service.get_entry(eid)["row_version"], post=True)
    e = service.get_entry(eid)
    assert e["status"] == "posted" and e["entry_no"] == "JV-2026-00001" and e["posted_by_name"] == "user"
    with pytest.raises(BusinessError, match="الغي ترحيله الأول"):
        service.save_draft(eid, pay(db), e["row_version"])
    with pytest.raises(BusinessError, match="الغي ترحيله الأول"):
        service.delete_entry(eid, e["row_version"])
    # إلغاء الترحيل ← مسودة برقمه تتعدّل وتترحّل تاني
    service.unpost(eid, e["row_version"])
    e = service.get_entry(eid)
    assert e["status"] == "draft" and e["entry_no"] == "JV-2026-00001"
    service.save_draft(eid, pay(db, amount="2000"), e["row_version"])
    assert service.post(eid, service.get_entry(eid)["row_version"]) == "JV-2026-00001"
    assert service.get_entry(eid)["total_credit"] == 2000
    actions = [r["action"] for r in db.fetch_all(
        "SELECT action FROM sec.audit_log WHERE entity = 'acc.journal_entries' ORDER BY id")]
    assert actions == ["create", "update", "post", "unpost", "update", "post"]
    lines_log = one(db, "SELECT details -> 'lines' -> 'acc.journal_lines' -> 'after' FROM sec.audit_log "
                        "WHERE entity = 'acc.journal_entries' ORDER BY id DESC LIMIT 1")
    assert len(lines_log) == 2
    # التحقق في الـ Service
    with pytest.raises(BusinessError, match="لازم تختار العميل"):
        service.save_draft(None, {**pay(db), "lines": [{"account_id": acc(db, "1103"), "debit": "5"}]})
    with pytest.raises(BusinessError, match="مش الاتنين"):
        service.save_draft(None, {**pay(db), "lines": [{"account_id": acc(db, "5202"), "debit": "5", "credit": "5"}]})
    with pytest.raises(BusinessError, match="سطر واحد على الأقل"):
        service.save_draft(None, {**pay(db), "lines": [{"account_id": None, "debit": "", "credit": ""}]})
    with pytest.raises(BusinessError, match="بيان القيد مطلوب"):
        service.save_draft(None, {**pay(db), "description": "  "})
    with pytest.raises(BusinessError, match="مفيش سنة مالية"):
        service.save_draft(None, pay(db, d=dt.date(2030, 1, 1)))
    party = service.save_draft(None, {**pay(db), "lines": [
        {"account_id": acc(db, "1103"), "debit": "70", "party_id": cash_customer(db)},
        {"account_id": acc(db, "110101"), "credit": "70"}]}, post=True)
    assert service.get_entry(party)["lines"][0]["party_name"] == "عميل نقدي"
    assert service.get_entry(party)["entry_no"] == "JV-2026-00002"
    # المسودة اللي ماخدتش رقم بتتمسح
    draft = service.save_draft(None, pay(db))
    service.delete_entry(draft, service.get_entry(draft)["row_version"])
    assert service.get_entry(draft) is None
    # القائمة بالفترة والفلاتر
    rows = service.list_entries(dt.date(2026, 10, 1), dt.date(2026, 10, 31))
    assert [r["entry_no"] for r in rows] == ["JV-2026-00002", "JV-2026-00001"]
    assert service.count_entries(dt.date(2026, 10, 1), dt.date(2026, 10, 31), status="draft")["total"] == 0
    assert service.list_entries(None, None, keyword="JV-2026-00002")[0]["id"] == party
    assert service.list_entries(None, None, source="auto") == []
    # القيد الأوتوماتيك قراية بس
    db.execute("UPDATE acc.journal_entries SET status = 'draft' WHERE id = %s", [party])
    db.execute("ALTER TABLE acc.journal_entries DISABLE TRIGGER journal_entries_guard_trg")
    db.execute("UPDATE acc.journal_entries SET source_type = 'sales_invoice', source_id = 1, "
               "source_no = 'INV-2026-00001' WHERE id = %s", [party])
    db.execute("ALTER TABLE acc.journal_entries ENABLE TRIGGER journal_entries_guard_trg")
    with pytest.raises(BusinessError, match="أوتوماتيك"):
        service.post(party)
    assert [r["id"] for r in service.list_entries(None, None, source="auto")] == [party]


def test_permissions(env):
    """«ترحيل» و«إلغاء الترحيل» صلاحيات لوحدهم (إلغاء الترحيل بالصلاحية بس — من غير كلمة سر)."""
    db, service = env["db"], env["service"]
    admin_id = db.fetch_value("SELECT id FROM sec.app_users WHERE username = %s", [DEFAULT_ADMIN_USERNAME])
    perm = "acc.journal_entries"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.create", f"{perm}.edit", f"{perm}.save"})
    eid = service.save_draft(None, pay(db))
    for call in (lambda: service.post(eid), lambda: service.save_draft(eid, pay(db), post=True),
                 lambda: service.unpost(eid), lambda: service.delete_entry(eid)):
        with pytest.raises(PermissionDenied):
            call()
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.post", f"{perm}.unpost"})
    service.post(eid)
    service.unpost(eid)
    assert entry(db, eid)["status"] == "draft"
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE 'acc.journal_entries.%%'")}
    assert codes == {f"{perm}.{a}" for a in ("view", "create", "edit", "save", "delete", "export", "post", "unpost")}
    assert "unpost" not in __import__("app.services.permission_registry", fromlist=["x"]).PASSWORD_ACTIONS


def test_two_devices(env):
    """جهازين: تعديل نفس المسودة ← التاني بيترفض · ترحيل وإلغاء من جهازين · ترحيلين في نفس اللحظة ← رقمين مختلفين."""
    db = env["db"]
    s1, s2 = JournalService(db), JournalService(Database(db.settings))
    try:
        eid = s1.save_draft(None, pay(db))
        seen1, seen2 = s1.get_entry(eid), s2.get_entry(eid)
        s2.save_draft(eid, pay(db, amount="1700"), seen2["row_version"])
        with pytest.raises(ConcurrentEditError, match="اتعدّل من جهاز تاني"):
            s1.save_draft(eid, pay(db, amount="1800"), seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.post(eid, seen1["row_version"])
        fresh = s1.get_entry(eid)
        s2.post(eid, fresh["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.unpost(eid, fresh["row_version"])                 # الجهاز الأول شايف المسودة القديمة
        # ترحيلين في نفس اللحظة: التاني بيستنى قفل العدّاد وبياخد الرقم اللي بعده
        a, b = s1.save_draft(None, pay(db)), s2.save_draft(None, pay(db))
        results: dict[str, object] = {}

        def worker():
            try:
                results["no"] = s2.post(b)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            tx.execute("UPDATE acc.journal_entries SET status = 'posted' WHERE id = %s", [a])
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                              # مستني قفل العدّاد
        thread.join(10)
        assert "error" not in results, results
        assert entry(db, a)["entry_no"] == "JV-2026-00002" and results["no"] == "JV-2026-00003"
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.journal_entries_screen import JournalEntriesScreen

    screen = JournalEntriesScreen(JournalService(db))
    screen.from_edit.setDate(screen.from_edit.date().addYears(-5))
    return screen


def set_line(screen, r, account, debit="", credit="", party=None):
    from app.ui.screens.journal_entries_screen import C_ACC, C_CR, C_DR, C_PARTY

    w = screen.lines_table.cellWidget
    w(r, C_ACC).set_value(account)
    w(r, C_DR).setText(debit)
    w(r, C_CR).setText(credit)
    if party is not None:
        w(r, C_PARTY).set_value(party)


def test_screen(env, monkeypatch):
    from app.ui.screens.journal_entries_screen import C_PARTY

    db, shown = env["db"], env["shown"]
    screen = make_screen(db)
    try:
        assert screen.save_button.text() == "حفظ مسودة" and screen.table.rowCount() == 0
        screen.new_record()
        assert screen.mode == "new" and screen.lines_table.rowCount() == 2
        screen.description.setText("تحصيل من عميل نقدي")
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-10-15", "yyyy-MM-dd"))
        set_line(screen, 0, acc(db, "110101"), debit="500")
        set_line(screen, 1, acc(db, "1103"), credit="400")
        party = screen.lines_table.cellWidget(1, C_PARTY)
        assert party.isEnabled() and "العميل" in party.placeholderText()
        assert not screen.lines_table.cellWidget(0, C_PARTY).isEnabled()
        assert "مش متوازن" in screen.balance_label.text()
        screen.save()                                              # من غير الطرف ← رسالة والفورم زي ما هو
        assert "لازم تختار العميل" in shown[-1] and screen.mode == "new"
        party.setText("نقدي")                                      # بيكتب جوه الخانة على طول
        party.editingFinished.emit()
        assert party.currentData() == cash_customer(db) and party.text() == "0 — عميل نقدي"
        screen.save()                                              # مسودة مش متوازنة بتتحفظ
        assert shown[-1] == "saved" and screen.current_record["status"] == "draft"
        assert screen.post_button.isEnabled() and not screen.unpost_button.isEnabled()
        screen.post_entry()                                        # من العرض ← رسالة التوازن
        assert "مش متوازن" in shown[-1]
        screen.edit_record()
        set_line(screen, 1, acc(db, "1103"), credit="500")
        assert "✔ متوازن" in screen.balance_label.text()
        screen.post_entry()                                        # وانت بتعدّل = حفظ + ترحيل
        assert screen.mode == "view" and screen.current_record["entry_no"] == "JV-2026-00001"
        assert "JV-2026-00001" in shown[-1]
        assert not screen.edit_button.isEnabled() and not screen.delete_button.isEnabled()
        assert screen.unpost_button.isEnabled() and not screen.post_button.isEnabled()
        assert screen.table.rows[0]["entry_no"] == "JV-2026-00001"
        # إلغاء الترحيل ← مسودة تتعدّل
        screen.unpost_entry()
        assert screen.current_record["status"] == "draft" and screen.edit_button.isEnabled()
        assert not screen.delete_button.isEnabled()                # خد رقم رسمي ← مايتمسحش
        # فلتر «مسودة»
        chip = next(b for b in screen.status_buttons.buttons() if b.property("value") == "posted")
        chip.click()
        assert screen.table.rowCount() == 0 and screen.current_id is None
        chip = next(b for b in screen.status_buttons.buttons() if b.property("value") == "all")
        chip.click()
        screen.table.select_id(screen.table.rows[0]["id"])
        screen.load(screen.table.rows[0]["id"])
        # جهاز تاني عدّل ← الحفظ بيترفض والفورم بيفضل
        screen.edit_record()
        db.execute("UPDATE acc.journal_entries SET reference = 'من جهاز تاني' WHERE id = %s", [screen.current_id])
        screen.reference.setText("x")
        conflicts: list = []
        monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or False)
        screen.save()
        assert conflicts and screen.mode == "edit"
        screen.cancel()
        # مسودة جديدة من غير رقم بتتمسح
        screen.new_record()
        screen.description.setText("مسودة للمسح")
        set_line(screen, 0, acc(db, "5202"), debit="10")
        screen.save()
        draft_id = screen.current_id
        assert screen.delete_button.isEnabled()
        screen.delete()
        assert entry(db, draft_id) is None and screen.current_id is None
    finally:
        screen.close()


def test_lookup_edit(qapp):
    """خانة الاختيار بالكتابة: الكود الكامل · جزء من الاسم مطابق لواحد · أكتر من واحد أو مفيش ← أحمر وفاضي."""
    from app.ui.common.lookup_edit import LookupEdit

    edit = LookupEdit()
    changes: list = []
    edit.value_changed.connect(changes.append)
    edit.set_items([(1, "5202", "5202 — الإيجار"), (2, "5203", "5203 — كهرباء ومياه"), (3, "110101", "110101 — الخزينة")])
    for text, expected in (("5202", 1), ("كهرباء", 2), ("520", None), ("مش موجود", None), ("الخزينة", 3), ("", None)):
        edit.setText(text)
        edit.editingFinished.emit()
        assert edit.currentData() == expected, text
    assert changes == [1, 2, None, 3, None]
    edit.set_items([(1, "5202", "5202 — الإيجار")], value=9, missing_text="9 — قديم")
    assert edit.currentData() == 9 and "(موقوف)" in edit.text()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    target = reg.find_target("acc", "journal_entries")
    assert target.name_ar == "القيود اليومية"
    assert set(target.action_codes()) == {"view", "create", "edit", "save", "delete", "export", "post", "unpost"}
    assert "journal_entries" in SCREEN_FACTORIES
