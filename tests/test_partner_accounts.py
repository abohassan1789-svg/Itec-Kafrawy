"""اختبارات «حساب لكل شريك تحت 32» (0042): الحساب بيتعمل لوحده · الاسم بيتغيّر معاه · بيتمسح معاه · الحماية من الشجرة
· الشريك على السطر لازم يبقى صاحب الحساب · الترحيل على حساب الشريك · ترقية قاعدة عليها حركة (زي DBITEC) · الشاشة."""

from __future__ import annotations

import datetime as dt
import gc
import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError
from app.services.partner_service import PartnerService
from app.services.partner_transaction_service import PartnerTransactionService
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages
from tests.test_partners import BLANK

D1 = dt.date(2026, 3, 1)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def login(db):
    AuthService(db).seed_admin()
    PermissionsSyncService(db).sync()
    SESSION.clear()
    assert AuthService(db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2026', '2026-01-01', '2026-12-31')")
    login(temp_db)
    shown: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    yield {"db": temp_db, "shown": shown, "service": PartnerService(temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def account(db, code):
    return db.fetch_one("SELECT a.*, p.code AS parent_code FROM acc.accounts a "
                        "LEFT JOIN acc.accounts p ON p.id = a.parent_id WHERE a.code = %s", [code])


def payload(**extra):
    return {**BLANK, **extra}


def partner_id(db, code):
    return one(db, "SELECT id FROM party.partners WHERE code = %s", code)


def partner_line(db, account_code, partner, amount=1000, post=True):
    """قيد يدوي: مدين الخزنة / دائن حساب الشريك (بالشريك اللي بنبعته)."""
    eid = one(db, "INSERT INTO acc.journal_entries (entry_date, fiscal_year_id, description) VALUES "
                  "(%s, (SELECT id FROM core.fiscal_years LIMIT 1), 'إيداع') RETURNING id", D1)
    db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, debit) VALUES "
               "(%s, 1, (SELECT id FROM acc.accounts WHERE code = '110101'), %s)", [eid, amount])
    db.execute("INSERT INTO acc.journal_lines (entry_id, line_no, account_id, credit, partner_id) VALUES "
               "(%s, 2, (SELECT id FROM acc.accounts WHERE code = %s), %s, %s)", [eid, account_code, amount, partner])
    if post:
        db.execute("UPDATE acc.journal_entries SET status = 'posted' WHERE id = %s", [eid])
    return eid


# ---- القاعدة -----------------------------------------------------------------------
def test_account_created_renamed_deleted(env):
    db, svc = env["db"], env["service"]
    g = account(db, "32")
    assert not g["is_leaf"] and g["role_code"] == "partner"                       # 32 بقى رئيسي
    general = account(db, "3299")
    assert (general["name_ar"], general["parent_code"], general["role_code"], general["party_kind"]) == \
           ("جاري الشركاء — عام", "32", "partner", "partner")
    a = svc.save_partner(None, payload(name_ar="مصطفى إبراهيم", name_en="Mostafa", profit_percent=60, loss_percent=60))
    b = svc.save_partner(None, payload(name_ar="طارق سليمان", profit_percent=40, loss_percent=40))
    pa, pb = svc.get_partner(a), svc.get_partner(b)
    assert (pa["account_code"], pa["account_name"], pb["account_code"]) == ("3201", "مصطفى إبراهيم", "3202")
    acc_a = account(db, "3201")
    assert (acc_a["parent_code"], acc_a["role_code"], acc_a["party_kind"], acc_a["is_leaf"], acc_a["is_system"],
            acc_a["name_en"], acc_a["account_type"]) == ("32", "partner", "partner", True, False, "Mostafa", "equity")
    assert one(db, "SELECT count(*) FROM sec.audit_log WHERE entity = 'acc.accounts' AND action = 'create' "
                   "AND entity_id = %s", str(acc_a["id"])) == 1
    # الاسم بيتغيّر مع الشريك
    svc.save_partner(a, payload(name_ar="م/ مصطفى إبراهيم", name_en="Mostafa I", profit_percent=60, loss_percent=60),
                     svc.get_partner(a)["row_version"])
    assert (account(db, "3201")["name_ar"], account(db, "3201")["name_en"]) == ("م/ مصطفى إبراهيم", "Mostafa I")
    # لو اسم الحساب اتغيّر من الشجرة ← مايتلمسش
    db.execute("UPDATE acc.accounts SET name_ar = 'جاري م. طارق' WHERE code = '3202'")
    svc.save_partner(b, payload(name_ar="طارق سليمان علي", profit_percent=40, loss_percent=40),
                     svc.get_partner(b)["row_version"])
    assert account(db, "3202")["name_ar"] == "جاري م. طارق"
    # الحساب ثابت ومايتختارش
    with pytest.raises(psycopg.Error, match="ثابت"):
        db.execute("UPDATE party.partners SET account_id = (SELECT id FROM acc.accounts WHERE code = '3299') "
                   "WHERE id = %s", [a])
    with pytest.raises(psycopg.Error, match="مايتختارش"):
        db.execute("INSERT INTO party.partners (seq, name_ar, account_id) VALUES (9, 'س', "
                   "(SELECT id FROM acc.accounts WHERE code = '3299'))")
    # المسح بيمسح الحساب معاه · والكود الفاضي بيترجع (زي الشجرة)
    svc.delete_partner(b, svc.get_partner(b)["row_version"])
    assert account(db, "3202") is None
    assert one(db, "SELECT count(*) FROM sec.audit_log WHERE entity = 'acc.accounts' AND action = 'delete'") == 1
    c = svc.save_partner(None, payload(name_ar="هالة منصور"))
    assert svc.get_partner(c)["account_code"] == "3202"
    assert "32 جاري الشركاء" in svc.account_hint()


def test_tree_guards_and_line_owner(env):
    db, svc = env["db"], env["service"]
    a = svc.save_partner(None, payload(name_ar="مصطفى", profit_percent=50, loss_percent=50))
    b = svc.save_partner(None, payload(name_ar="طارق", profit_percent=50, loss_percent=50))
    with pytest.raises(psycopg.Error, match="امسح الشريك نفسه"):
        db.execute("DELETE FROM acc.accounts WHERE code = '3201'")
    with pytest.raises(psycopg.Error, match="مينفعش يتحوّل رئيسي"):
        db.execute("SELECT acc.split_account((SELECT id FROM acc.accounts WHERE code = '3201'), NULL)")
    with pytest.raises(psycopg.Error, match="وقّف الشريك الأول"):
        db.execute("UPDATE acc.accounts SET is_active = false WHERE code = '3201'")
    with pytest.raises(psycopg.Error, match="ثابتين"):
        db.execute("UPDATE acc.accounts SET role_code = NULL WHERE code = '3201'")
    # السطر على حساب الشريك ← الشريك لازم يبقى صاحبه (القيود والأرصدة الافتتاحية)
    with pytest.raises(psycopg.Error, match="لازم يبقى هو"):
        partner_line(db, "3201", b, post=False)
    partner_line(db, "3201", a)
    partner_line(db, "3299", b)                                                   # «العام» بأي شريك
    with pytest.raises(psycopg.Error, match="لازم يبقى هو"):
        db.execute("INSERT INTO acc.opening_balance_lines (account_id, partner_id, credit) VALUES "
                   "((SELECT id FROM acc.accounts WHERE code = '3202'), %s, 500)", [a])
    db.execute("INSERT INTO acc.opening_balance_lines (account_id, partner_id, credit) VALUES "
               "((SELECT id FROM acc.accounts WHERE code = '3202'), %s, 500)", [b])
    # الشريك اللي خرج: حسابه يتوقف عادي
    svc.save_partner(b, payload(name_ar="طارق", is_active=False), svc.get_partner(b)["row_version"])
    db.execute("UPDATE acc.accounts SET is_active = false WHERE code = '3202'")
    assert not account(db, "3202")["is_active"]


def test_posting_uses_partner_account(env):
    db = env["db"]
    svc, ps = env["service"], PartnerTransactionService(db)
    a = svc.save_partner(None, payload(name_ar="مصطفى", profit_percent=50, loss_percent=50, subscribed_capital=100))
    svc.save_partner(None, payload(name_ar="طارق", profit_percent=50, loss_percent=50))
    t = one(db, "SELECT id FROM cash.treasuries WHERE code = 'TRS-001'")
    x = ps.save_txn(None, {"txn_kind": "deposit", "txn_date": D1, "partner_id": a, "source_kind": "treasury",
                           "treasury_id": t, "amount": "5000", "description": "إيداع"}, post=True)
    codes = [r["code"] for r in db.fetch_all(
        "SELECT a.code FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = 'partner_dep' AND e.source_id = %s "
        "ORDER BY l.line_no", [x])]
    assert codes == ["110101", "3201"]
    # رأس المال فاضل على 31 ببُعد الشريك
    c = ps.save_txn(None, {"txn_kind": "capital", "txn_date": D1, "partner_id": a, "source_kind": "current",
                           "amount": "100", "description": "زيادة"}, post=True)
    assert [r["code"] for r in db.fetch_all(
        "SELECT a.code FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = 'partner_cap' AND e.source_id = %s "
        "ORDER BY l.line_no", [c])] == ["3201", "31"]
    assert ps.partner_summary(a)["current"] == Decimal("4900")


# ---- ترقية قاعدة عليها حركة على 32 (زي DBITEC: 0041 + شريك + إيداع مرحّل) -------------
def test_upgrade_moves_existing_lines(qapp, temp_db, tmp_path):
    db = temp_db
    old = tmp_path / "migrations"
    old.mkdir()
    for f in migrator.migrations_dir().glob("*.sql"):
        if int(f.name[:4]) <= 41:
            shutil.copy(f, old / f.name)
    migrator.migrate(db, old)
    db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
               "('2026', '2026-01-01', '2026-12-31')")
    for seq, name in ((1, "شريك أول"), (2, "شريك تاني")):
        db.execute("INSERT INTO party.partners (seq, name_ar) VALUES (%s, %s)", [seq, name])
    p1, p2 = partner_id(db, "PRT-001"), partner_id(db, "PRT-002")
    e = partner_line(db, "32", p1, 150000)
    partner_line(db, "32", p2, 2000, post=False)
    db.execute("INSERT INTO acc.opening_balance_lines (account_id, partner_id, credit) VALUES "
               "((SELECT id FROM acc.accounts WHERE code = '32'), %s, 700)", [p2])
    v = one(db, "SELECT row_version FROM party.partners WHERE id = %s", p1)
    migrator.migrate(db)                                                          # 0042
    assert not account(db, "32")["is_leaf"]
    assert [(r["code"], r["name_ar"]) for r in db.fetch_all(
        "SELECT code, name_ar FROM acc.accounts WHERE left(code, 2) = '32' AND length(code) = 4 ORDER BY code")] == \
           [("3201", "شريك أول"), ("3202", "شريك تاني"), ("3299", "جاري الشركاء — عام")]
    assert one(db, "SELECT row_version FROM party.partners WHERE id = %s", p1) == v
    rows = db.fetch_all("SELECT a.code, l.partner_id, l.credit, e.status FROM acc.journal_lines l "
                        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
                        "WHERE l.partner_id IS NOT NULL ORDER BY a.code")
    assert [(r["code"], r["partner_id"], r["credit"], r["status"]) for r in rows] == [
        ("3201", p1, Decimal("150000.00"), "posted"), ("3202", p2, Decimal("2000.00"), "draft")]
    assert one(db, "SELECT a.code FROM acc.opening_balance_lines l JOIN acc.accounts a ON a.id = l.account_id") \
        == "3202"
    assert one(db, "SELECT a.code FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
                   "WHERE d.key = 'partner_cur'") == "3299"
    assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE id = %s AND status = 'posted'", e) == 1
    # شريك جديد بعد الترقية ← 3203
    db.execute("INSERT INTO party.partners (seq, name_ar) VALUES (3, 'تالت')")
    assert one(db, "SELECT a.code FROM party.partners p JOIN acc.accounts a ON a.id = p.account_id "
                   "WHERE p.seq = 3") == "3203"


# ---- الشاشة ------------------------------------------------------------------------
def test_screen_shows_account(env):
    from app.ui.screens.partners_screen import PartnersScreen

    db, shown = env["db"], env["shown"]
    screen = PartnersScreen(PartnerService(db))
    try:
        screen.new_record()
        assert "32 جاري الشركاء" in screen.account_edit.text() and screen.account_edit.isReadOnly()
        screen.name_ar.setText("م/ حسام عبد الله")
        screen.save()
        assert shown[-1] == "saved"
        assert screen.account_edit.text().replace("⁦", "").replace("⁩", "") == "3201 م/ حسام عبد الله"
    finally:
        screen.close()


def test_journal_fills_partner_from_account(env):
    """شاشة القيود: اختيار حساب شريك (3201) ← الشريك بيتملى لوحده · والـ Service بتملاه لو مبعوتش."""
    from app.services.journal_service import JournalService
    from app.ui.screens.journal_entries_screen import C_PARTY, JournalEntriesScreen
    from tests.test_journal_entries import set_line

    db, shown, svc = env["db"], env["shown"], env["service"]
    a = svc.save_partner(None, payload(name_ar="مصطفى"))
    svc.save_partner(None, payload(name_ar="طارق"))
    acc_id = lambda code: one(db, "SELECT id FROM acc.accounts WHERE code = %s", code)   # noqa: E731
    js = JournalService(db)
    x = js.save_draft(None, {"entry_date": D1, "description": "إيداع", "lines": [
        {"account_id": acc_id("110101"), "debit": "100"}, {"account_id": acc_id("3201"), "credit": "100"}]})
    assert one(db, "SELECT partner_id FROM acc.journal_lines WHERE entry_id = %s AND line_no = 2", x) == a
    screen = JournalEntriesScreen(JournalService(db))
    try:
        screen.new_record()
        screen.description.setText("إيداع شريك")
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-01", "yyyy-MM-dd"))
        set_line(screen, 0, acc_id("3201"), credit="100")
        set_line(screen, 1, acc_id("110101"), debit="100")
        party = screen.lines_table.cellWidget(0, C_PARTY)
        assert party.currentData() == a and len(party._rows) == 1
        screen.save()
        assert shown[-1] == "saved"
        assert one(db, "SELECT count(*) FROM acc.journal_lines WHERE partner_id = %s", a) == 2
    finally:
        screen.close()
