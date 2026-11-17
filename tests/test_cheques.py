"""اختبارات «شيكات العملاء» و«شيكات الموردين» (0038): حساب «شيكات تحت التحصيل» · الدورة والقيود لكل خطوة ·
الترقيم CHR / CHP من أول ترحيل · التكرار على نفس البنك · «تم التحصيل» / «تم الصرف» · التراجع · التنبيهات ·
الحماية في القاعدة · الصلاحيات · جهازين · الشاشة."""

from __future__ import annotations

import datetime as dt
import gc
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.cheque_service import TRANSITIONS, ChequeService, due_bucket
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages

D = dt.date(2026, 3, 10)
DUE = dt.date(2026, 4, 10)


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
    yield {"db": temp_db, "shown": shown, "asked": asked,
           "cin": ChequeService("in", temp_db), "cout": ChequeService("out", temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def customer(db, code="0"):
    return one(db, "SELECT id FROM party.customers WHERE code = %s", code)


def supplier(db):
    return one(db, "SELECT id FROM party.suppliers ORDER BY seq LIMIT 1")      # المورد النقدي


def bank_id(db, code="BNK-001"):
    return one(db, "SELECT id FROM cash.banks WHERE code = %s", code)


def account(db, code="BNK-001-01"):
    return one(db, "SELECT id FROM cash.bank_accounts WHERE code = %s", code)


def cin(db, no="556301", amount="10000", d=D, due=DUE, **extra):
    out = {"cheque_date": d, "due_date": due, "party_id": customer(db), "cheque_no": no,
           "drawer_bank_id": bank_id(db), "amount": amount, "description": "دفعة فاتورة 1045"}
    out.update(extra)
    return out


def cout(db, no="0012001", amount="4000", d=D, due=DUE, **extra):
    out = {"cheque_date": d, "due_date": due, "party_id": supplier(db), "cheque_no": no,
           "bank_account_id": account(db), "amount": amount, "description": "توريد كابلات"}
    out.update(extra)
    return out


def row(db, cid):
    return db.fetch_one("SELECT * FROM cash.cheques WHERE id = %s", [cid])


def lines(db, doc, event_id):
    return [(r["code"], r["debit"], r["credit"]) for r in db.fetch_all(
        "SELECT a.code, l.debit, l.credit FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
        "WHERE e.source_type = %s AND e.source_id = %s ORDER BY l.line_no", [doc, event_id])]


def last_event(db, cid):
    return db.fetch_one("SELECT * FROM cash.cheque_events WHERE cheque_id = %s ORDER BY seq DESC LIMIT 1", [cid])


def balance(db, code):
    return one(db, "SELECT coalesce(sum(l.debit - l.credit), 0) FROM acc.journal_lines l "
                   "JOIN acc.accounts a ON a.id = l.account_id WHERE a.code = %s", code)


# ---- القاعدة ---------------------------------------------------------------
def test_account_numbering_and_rules(env):
    db = env["db"]
    acc = db.fetch_one("SELECT a.code, a.name_ar, a.party_kind, a.is_system, p.code AS parent FROM acc.accounts a "
                       "JOIN acc.accounts p ON p.id = a.parent_id WHERE a.role_code = 'notes_coll'")
    # 0039: حسابات الشيكات من غير طرف (العميل / المورد على العملاء / الموردين بس)
    assert acc == {"code": "1108", "name_ar": "شيكات تحت التحصيل", "party_kind": None, "is_system": True,
                   "parent": "11"}
    assert db.fetch_all("SELECT code, party_kind FROM acc.accounts WHERE code IN ('1104', '2102') ORDER BY code") == [
        {"code": "1104", "party_kind": None}, {"code": "2102", "party_kind": None}]
    assert one(db, "SELECT a.code FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
                   "WHERE d.key = 'cheques_coll'") == "1108"
    keys = [r["key"] for r in db.fetch_all("SELECT key FROM acc.default_accounts WHERE group_code = 'cash' "
                                           "ORDER BY sort_order")]
    assert keys == ["cash_box", "bank", "notes_rec", "cheques_coll", "notes_pay", "bank_charges", "custody"]
    assert one(db, "SELECT prefix FROM core.document_types WHERE code = 'cheque_in'") == "CHR"
    assert one(db, "SELECT prefix FROM core.document_types WHERE code = 'cheque_out'") == "CHP"
    # الدورة في القاعدة = الدورة في البرنامج
    db_tr = {(r["direction"], r["event_type"], r["from_status"]): (r["to_status"], r["has_entry"], r["name_ar"])
             for r in db.fetch_all("SELECT * FROM cash.cheque_transitions")}
    assert db_tr == TRANSITIONS
    for idx in ("cheques_customer_idx", "cheques_supplier_idx", "cheques_drawer_bank_idx", "cheques_bank_account_idx",
                "cheques_project_idx", "cheques_due_idx", "cheque_events_bank_idx", "cheque_events_supplier_idx"):
        assert one(db, "SELECT to_regclass(%s)", f"cash.{idx}") is not None, idx


def test_incoming_full_cycle_entries(env):
    db, s = env["db"], env["cin"]
    cid = s.save_draft(None, cin(db))
    c = row(db, cid)
    assert c["status"] == "draft" and c["cheque_ref"] is None
    # المعاينة قبل الترحيل: مدين أوراق القبض / دائن العملاء
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in s.preview(cin(db), cid)] == [
        ("1104", Decimal("10000.00"), 0), ("1103", 0, Decimal("10000.00"))]
    assert s.post(cid, expected_version=c["row_version"]) == "CHR-2026-00001"
    c = row(db, cid)
    assert c["status"] == "portfolio" and c["status_date"] == D and c["posted_by"] is not None
    assert lines(db, "cheque_in", last_event(db, cid)["id"]) == [("1104", 10000, 0), ("1103", 0, 10000)]
    # الإيداع: مدين تحت التحصيل / دائن أوراق القبض
    s.do_step(cid, "deposit", dt.date(2026, 4, 11), bank_account_id=account(db))
    assert row(db, cid)["bank_account_id"] == account(db)
    assert lines(db, "cheque_in", last_event(db, cid)["id"]) == [("1108", 10000, 0), ("1104", 0, 10000)]
    # الرجوع من البنك + مصاريف: مدين العملاء / دائن تحت التحصيل + مدين مصاريف / دائن البنك
    s.do_step(cid, "bounce", dt.date(2026, 4, 14), fees="50")
    assert lines(db, "cheque_in", last_event(db, cid)["id"]) == [
        ("5402", 50, 0), ("1103", 10000, 0), ("1108", 0, 10000), ("110201", 0, 50)]
    # إيداع تاني (من الراجع): مدين تحت التحصيل / دائن العملاء ← وتحصيل بمصاريف
    s.do_step(cid, "deposit", dt.date(2026, 4, 15), bank_account_id=account(db, "BNK-002-01"))
    assert lines(db, "cheque_in", last_event(db, cid)["id"]) == [("1108", 10000, 0), ("1103", 0, 10000)]
    s.do_step(cid, "collect", dt.date(2026, 4, 18), fees="15")
    assert lines(db, "cheque_in", last_event(db, cid)["id"]) == [
        ("110202", Decimal("9985.00"), 0), ("5402", 15, 0), ("1108", 0, 10000)]
    c = row(db, cid)
    assert c["status"] == "collected" and c["status_date"] == dt.date(2026, 4, 18)
    # العميل اتقفل عليه بالمبلغ (دفع بالشيك) · الحافظة وتحت التحصيل صفر · البنك = المبلغ − المصاريف
    assert balance(db, "1104") == 0 and balance(db, "1108") == 0 and balance(db, "1103") == -10000
    assert balance(db, "110202") == Decimal("9985") and balance(db, "110201") == -50 and balance(db, "5402") == 65
    assert one(db, "SELECT sum(debit) = sum(credit) FROM acc.journal_lines") is True
    # مفيش خطوات بعد التحصيل
    assert s.next_steps(s.get_cheque(cid)) == []
    with pytest.raises(BusinessError, match="مش مسموحة"):
        s.do_step(cid, "bounce", dt.date(2026, 4, 20))


def test_endorse_return_and_outgoing(env):
    db, s, o = env["db"], env["cin"], env["cout"]
    a = s.save_draft(None, cin(db, "1"), post=True)
    s.do_step(a, "endorse", dt.date(2026, 3, 20), supplier_id=supplier(db))
    assert row(db, a)["status"] == "endorsed" and row(db, a)["supplier_id"] == supplier(db)
    assert lines(db, "cheque_in", last_event(db, a)["id"]) == [("2101", 10000, 0), ("1104", 0, 10000)]
    b = s.save_draft(None, cin(db, "2"), post=True)
    s.do_step(b, "return", dt.date(2026, 3, 20))
    assert lines(db, "cheque_in", last_event(db, b)["id"]) == [("1103", 10000, 0), ("1104", 0, 10000)]
    # الراجع من البنك وبعدين اترد: من غير قيد
    c = s.save_draft(None, cin(db, "3"), post=True)
    s.do_step(c, "deposit", dt.date(2026, 3, 20), bank_account_id=account(db))
    s.do_step(c, "bounce", dt.date(2026, 3, 22))
    s.do_step(c, "return", dt.date(2026, 3, 23))
    assert last_event(db, c)["has_entry"] is False and lines(db, "cheque_in", last_event(db, c)["id"]) == []
    # الصادر: إصدار ← صرف · وتاني: إصدار ← إلغاء
    p = o.save_draft(None, cout(db), post=True)
    assert row(db, p)["cheque_ref"] == "CHP-2026-00001"
    assert lines(db, "cheque_out", last_event(db, p)["id"]) == [("2101", 4000, 0), ("2102", 0, 4000)]
    o.complete(p, dt.date(2026, 4, 10))                         # «تم الصرف»
    assert row(db, p)["status"] == "cleared"
    assert lines(db, "cheque_out", last_event(db, p)["id"]) == [("2102", 4000, 0), ("110201", 0, 4000)]
    q = o.save_draft(None, cout(db, "0012002"), post=True)
    o.do_step(q, "cancel", dt.date(2026, 3, 25))
    assert lines(db, "cheque_out", last_event(db, q)["id"]) == [("2102", 4000, 0), ("2101", 0, 4000)]
    assert balance(db, "2102") == 0


def test_complete_checkbox_and_undo(env):
    db, s = env["db"], env["cin"]
    cid = s.save_draft(None, cin(db), post=True)
    v = row(db, cid)["row_version"]
    assert s.complete(cid, dt.date(2026, 4, 10), expected_version=v, bank_account_id=account(db)) == "collected"
    assert [e["event_type"] for e in s.events(cid)] == ["receive", "deposit", "collect"]
    # التراجع خطوة خطوة لحد المسودة (برقمها)
    assert s.undo_last(cid, expected_version=row(db, cid)["row_version"]) == "under_collection"
    assert row(db, cid)["bank_account_id"] == account(db)
    assert s.undo_last(cid) == "portfolio"
    assert row(db, cid)["bank_account_id"] is None
    assert s.undo_last(cid) == "draft"
    c = row(db, cid)
    assert c["cheque_ref"] == "CHR-2026-00001" and c["posted_at"] is None and c["status_date"] is None
    assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE source_type = 'cheque_in'") == 0
    with pytest.raises(BusinessError, match="مفيش خطوات"):
        s.undo_last(cid)
    # المسودة اللي خدت رقم بتتعدّل وتترحّل تاني بنفس الرقم — ومابتتمسحش
    s.save_draft(cid, cin(db, amount="12000"))
    with pytest.raises(BusinessError, match="رقم رسمي"):
        s.delete_cheque(cid)
    assert s.post(cid) == "CHR-2026-00001"
    # مسودة من غير رقم بتتمسح
    other = s.save_draft(None, cin(db, "777"))
    s.delete_cheque(other)
    assert row(db, other) is None


def test_validation_duplicates_and_warnings(env):
    db, s, o = env["db"], env["cin"], env["cout"]
    s.save_draft(None, cin(db))
    with pytest.raises(BusinessError, match="متسجل قبل كده"):
        s.save_draft(None, cin(db, " 556301 "))
    s.save_draft(None, cin(db, drawer_bank_id=bank_id(db, "BNK-002")))       # نفس الرقم على بنك تاني عادي
    for bad, msg in (({"cheque_no": ""}, "رقم الشيك"), ({"amount": "0"}, "مبلغ"), ({"party_id": None}, "العميل"),
                     ({"drawer_bank_id": None}, "بنك الساحب"), ({"description": " "}, "البيان")):
        with pytest.raises(BusinessError, match=msg):
            s.save_draft(None, cin(db, "9", **bad))
    assert any("الاستحقاق قبل" in w for w in s.warnings(cin(db, "9", due=dt.date(2026, 3, 1))))
    # الصادر: الرصيد مش مكفي ← تحذير بس (والإصدار بيتعمل عادي)
    warn = o.warnings(cout(db))
    assert warn and "مش هيكفي" in warn[0]
    pid = o.save_draft(None, cout(db), post=True)
    assert row(db, pid)["status"] == "issued"
    # الخطوة قبل تاريخ اللي قبلها ← القاعدة بترفض
    cid = s.save_draft(None, cin(db, "10"), post=True)
    with pytest.raises(BusinessError, match="قبل الخطوة"):
        s.do_step(cid, "deposit", dt.date(2026, 3, 1), bank_account_id=account(db))
    with pytest.raises(BusinessError, match="الحساب البنكي"):
        s.do_step(cid, "deposit", dt.date(2026, 3, 11))


def test_alerts_buckets(env):
    db, s = env["db"], env["cin"]
    today = dt.date(2026, 3, 20)
    for no, due in (("a", dt.date(2026, 3, 18)), ("b", today), ("c", dt.date(2026, 3, 21)),
                    ("d", dt.date(2026, 3, 23)), ("e", dt.date(2026, 3, 30))):
        s.save_draft(None, cin(db, no, due=due), post=True)
    s.save_draft(None, cin(db, "draft", due=today))                  # المسودة مش في التنبيهات
    done = s.save_draft(None, cin(db, "done", due=today), post=True)
    s.do_step(done, "return", dt.date(2026, 3, 15))                   # اللي خلص مش في التنبيهات
    a = s.alerts(today)
    assert [a[k]["count"] for k in ("overdue", "today", "tomorrow", "soon")] == [1, 1, 1, 1]
    assert a["total"] == 4 and a["overdue"]["amount"] == Decimal("10000")
    assert [r["cheque_no"] for r in a["rows"]] == ["a", "b", "c", "d"]
    assert due_bucket(dt.date(2026, 3, 24), today) == "later"
    assert env["cout"].alerts(today)["total"] == 0


def test_database_guards(env):
    db, s = env["db"], env["cin"]
    cid = s.save_draft(None, cin(db), post=True)
    with pytest.raises(psycopg.errors.RaiseException, match="ماتتعدّلش"):
        db.execute("UPDATE cash.cheques SET amount = 1 WHERE id = %s", [cid])
    with pytest.raises(psycopg.errors.RaiseException):                 # قفز من الحافظة للتحصيل على طول
        db.execute("UPDATE cash.cheques SET status = 'collected', bank_account_id = %s, status_date = %s "
                   "WHERE id = %s", [account(db), D, cid])
    with pytest.raises(psycopg.errors.RaiseException, match="مابتتعدّلش"):
        db.execute("UPDATE cash.cheque_events SET note = 'x' WHERE cheque_id = %s", [cid])
    with pytest.raises(psycopg.errors.RaiseException, match="اترحّل"):
        db.execute("DELETE FROM cash.cheques WHERE id = %s", [cid])
    # الحالة لوحدها من غير خطوة ← بترفض آخر الـ transaction
    with pytest.raises(psycopg.errors.RaiseException, match="لازم يتغيروا مع بعض"):
        with db.atomic() as tx:
            tx.execute("UPDATE cash.cheques SET status = 'returned', status_date = %s WHERE id = %s", [D, cid])
    # الخطوة من غير قيد ← بترفض
    with pytest.raises(psycopg.errors.RaiseException, match="من غير قيد"):
        with db.atomic() as tx:
            tx.execute("UPDATE cash.cheques SET status = 'returned', status_date = %s WHERE id = %s", [D, cid])
            tx.execute("INSERT INTO cash.cheque_events (cheque_id, seq, event_type, event_date, from_status, "
                       "to_status, has_entry) VALUES (%s, 2, 'return', %s, 'portfolio', 'returned', true)", [cid, D])
    # السنة ماتتقفلش وفيها شيك مسودة
    s.save_draft(None, cin(db, "x"))
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2026'")


def test_permissions(env):
    db, s = env["db"], env["cin"]
    cid = s.save_draft(None, cin(db), post=True)
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    perm = "cash.customer_cheques"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.create", f"{perm}.save"})
    s.save_draft(None, cin(db, "draft-ok"))                       # حفظ مسودة بس من غير ترحيل
    for call in (lambda: s.save_draft(None, cin(db, "z"), post=True), lambda: s.do_step(cid, "return", D),
                 lambda: s.undo_last(cid), lambda: s.complete(cid, D, bank_account_id=account(db)),
                 lambda: s.post(cid), lambda: env["cout"].save_draft(None, cout(db))):
        with pytest.raises(PermissionDenied):
            call()


def test_two_devices(env, base_settings):
    """جهازين على نفس الشيك: التاني بياخد ConcurrentEditError · وتسجيل نفس رقم الشيك في نفس اللحظة."""
    db, s = env["db"], env["cin"]
    cid = s.save_draft(None, cin(db), post=True)
    v = row(db, cid)["row_version"]
    s.do_step(cid, "deposit", dt.date(2026, 4, 11), expected_version=v, bank_account_id=account(db))
    with pytest.raises(ConcurrentEditError):
        s.do_step(cid, "endorse", dt.date(2026, 4, 11), expected_version=v, supplier_id=supplier(db))
    with pytest.raises(ConcurrentEditError):
        s.undo_last(cid, expected_version=v)
    # جهازين بيسجلوا نفس رقم الشيك على نفس البنك في نفس اللحظة ← واحد بس ينجح
    other = Database(db.settings) if hasattr(db, "settings") else None
    if other is None:
        return
    s2 = ChequeService("in", other)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def run(service):
        barrier.wait()
        try:
            service.save_draft(None, cin(db, "SAME"))
            results.append("ok")
        except BusinessError as exc:
            results.append(str(exc))

    threads = [threading.Thread(target=run, args=(x,)) for x in (s, s2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    other.close()
    assert results.count("ok") == 1 and any("نفس البنك" in r for r in results)


# ---- الشاشة -----------------------------------------------------------------
def plain(text):
    return text.replace("⁦", "").replace("⁩", "")


def make_screen(db, direction="in"):
    from app.ui.screens.cheques_screen import CustomerChequesScreen, SupplierChequesScreen

    cls = CustomerChequesScreen if direction == "in" else SupplierChequesScreen
    screen = cls(ChequeService(direction, db))
    screen.from_edit.setDate(screen.from_edit.date().addYears(-5))
    screen.to_edit.setDate(screen.to_edit.date().addYears(5))
    return screen


def qd(d):
    from PySide6.QtCore import QDate

    return QDate(d.year, d.month, d.day)


def test_screen_customer_cheques(env, monkeypatch):
    db, shown, asked = env["db"], env["shown"], env["asked"]
    today = dt.date.today()
    screen = make_screen(db)
    try:
        assert screen.windowTitle().endswith("شيكات العملاء") and screen.post_button.text() == "استلام ✔"
        assert screen.save_button.text() == "حفظ مسودة" and screen.undo_button.text() == "تراجع عن آخر خطوة"
        assert plain(screen.alert_chips["overdue"].text()) == "متأخر 0" and not screen.alert_button.isEnabled()
        screen.new_record()
        screen._refresh_side()
        assert screen.mode == "new" and not screen.steps_box.isEnabled()
        screen.party_edit.set_value(customer(db))
        screen.cheque_no.setText("556301")
        screen.bank_edit.set_value(bank_id(db))
        screen.amount_edit.setText("12,500")
        screen.due_edit.setDate(qd(today + dt.timedelta(days=1)))       # بكرة ← في التنبيهات
        screen.description.setText("دفعة فاتورة 1045")
        screen._refresh_side()
        assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
        assert screen.entry_table.item(0, 0).text().startswith("1104")
        screen.post_cheque()
        rec = screen.current_record
        assert rec["status"] == "portfolio" and rec["cheque_ref"] == "CHR-2026-00001" and "CHR-2026-00001" in shown[-1]
        assert not screen.edit_button.isEnabled() and screen.undo_button.isEnabled()
        screen._refresh_side()
        assert screen.steps_box.isEnabled() and screen.deposit_check.isEnabled() and not screen.deposit_check.isChecked()
        assert screen.step_buttons["endorse"].isEnabled() and not screen.step_buttons["bounce"].isEnabled()
        assert screen.history.rowCount() == 1 and screen.entry_box.title().startswith("آخر قيد: استلام")
        assert plain(screen.alert_chips["tomorrow"].text()).startswith("بكرة 1") and screen.alert_button.isEnabled()
        assert "بكرة" in screen.table.item(0, 4).text()
        # «تم الإيداع» (التشيك بوكس) ← نافذة الخطوة ← تحت التحصيل
        asks: list[tuple] = []

        def fake_ask(event, title, complete=False):
            asks.append((event, title, complete))
            return {"event_date": today, "bank_account_id": account(db), "supplier_id": None, "fees": "0", "note": ""}

        monkeypatch.setattr(screen, "_ask_step", fake_ask)
        screen.deposit_check.click()
        assert asks[-1][0] == "deposit" and screen.current_record["status"] == "under_collection"
        assert screen.deposit_check.isChecked() and screen.step_buttons["bounce"].isEnabled()
        assert screen.entry_box.title().startswith("آخر قيد: إيداع للتحصيل")
        # «تم التحصيل»
        screen.done_check.click()
        assert asks[-1] == ("collect", "تم التحصيل", True) and screen.current_record["status"] == "collected"
        assert screen.done_check.isChecked() and screen.history.rowCount() == 3
        assert plain(screen.alert_chips["tomorrow"].text()) == "بكرة 0"             # اتحصّل ← خرج من التنبيهات
        # «تم الإيداع» وهو متحصّل مقفولة (التراجع عن «تم التحصيل» الأول) — ولو اتنادت بتقول كده
        assert not screen.deposit_check.isEnabled()
        screen._on_check("deposit", False)
        assert "آخر خطوة بس" in shown[-1] and screen.deposit_check.isChecked()
        screen.done_check.click()                                               # شيل «تم التحصيل» = تراجع
        assert screen.current_record["status"] == "under_collection" and "تم التحصيل" in asked[-1]
        # رجع من البنك ← إيداع تاني ← رد للعميل (بعد التراجع)
        screen.step_buttons["bounce"].click()
        assert asks[-1][:2] == ("bounce", "رجع من البنك") and screen.current_record["status"] == "bounced"
        assert screen.deposit_check.text() == "إيداع تاني" and screen.step_buttons["return"].isEnabled()
        screen.step_buttons["return"].click()
        assert screen.current_record["status"] == "returned"
        screen._refresh_side()
        assert "خلص دورته" in screen.steps_hint.text() and "(من غير قيد)" in screen.history.item(3, 1).text()
        # التراجع لحد المسودة ← يتعدّل
        for _ in range(4):                                                      # رد · رجوع · إيداع · استلام
            screen.undo_step()
        assert screen.current_record["status"] == "draft" and screen.edit_button.isEnabled()
        assert not screen.delete_button.isEnabled()                              # خد رقم
        # المسودة التانية بنفس الرقم والبنك ← رسالة
        screen.new_record()
        screen.party_edit.set_value(customer(db))
        screen.cheque_no.setText("556301")
        screen.bank_edit.set_value(bank_id(db))
        screen.amount_edit.setText("100")
        screen.description.setText("x")
        screen.save()
        assert "متسجل قبل كده" in shown[-1]
        screen.cancel()
        # كارت التنبيهات ← الشاشة ← «افتح الشيك»
        cid = env["cin"].save_draft(None, cin(db, "999", d=today, due=today - dt.timedelta(days=2)), post=True)
        screen.refresh_screen()
        assert plain(screen.alert_chips["overdue"].text()).startswith("متأخر 1")

        class FakeDialog:
            chosen, done_requested = cid, False

        monkeypatch.setattr(screen, "_due_dialog", lambda: FakeDialog())
        screen.show_due()
        assert screen.current_id == cid and screen.status == "all"              # كان ظاهر في القائمة
        screen.from_edit.setDate(qd(today + dt.timedelta(days=400)))             # برّا الفترة ← «المفتوحة»
        screen.show_due()
        assert screen.current_id == cid and screen.status == "open"
    finally:
        screen.close()


def test_screen_supplier_cheques(env, monkeypatch):
    db, shown = env["db"], env["shown"]
    today = dt.date.today()
    screen = make_screen(db, "out")
    try:
        assert screen.windowTitle().endswith("شيكات الموردين") and screen.post_button.text() == "إصدار ✔"
        assert screen.deposit_check.isHidden() and screen.done_check.text() == "تم الصرف"
        screen.new_record()
        screen.party_edit.set_value(supplier(db))
        screen.cheque_no.setText("0012001")
        screen.bank_edit.set_value(account(db))
        screen.amount_edit.setText("4000")
        screen.due_edit.setDate(qd(today))
        screen.description.setText("توريد كابلات")
        screen._refresh_side()
        assert not screen.warn_label.isHidden() and "مش هيكفي" in screen.warn_label.text()   # تحذير بس
        screen.post_cheque()
        assert screen.current_record["status"] == "issued" and screen.current_record["cheque_ref"] == "CHP-2026-00001"
        assert plain(screen.alert_chips["today"].text()).startswith("النهارده 1")
        monkeypatch.setattr(screen, "_ask_step", lambda e, t, complete=False: {
            "event_date": today, "bank_account_id": None, "supplier_id": None, "fees": "0", "note": ""})
        screen.done_check.click()                                              # «تم الصرف»
        assert screen.current_record["status"] == "cleared" and screen.done_check.isChecked()
        assert screen.entry_table.item(1, 0).text().startswith("110201")
        screen.undo_step()
        screen.step_buttons["cancel"].click()
        assert screen.current_record["status"] == "cancelled"
    finally:
        screen.close()


def party_lines(db, column, party_id):
    """سطور الطرف في القيود (= كشف حسابه): (الحساب، مدين، دائن)."""
    return [(r["code"], r["debit"], r["credit"]) for r in db.fetch_all(
        f"SELECT a.code, l.debit, l.credit FROM acc.journal_lines l JOIN acc.accounts a ON a.id = l.account_id "
        f"JOIN acc.journal_entries e ON e.id = l.entry_id WHERE l.{column} = %s ORDER BY e.entry_date, e.id, l.line_no",
        [party_id])]


def test_party_statement_shows_cheque_once(env):
    """ملاحظة المستخدم: الاستلام كان بيبان في كشف العميل مرتين (مدين ودائن) — دلوقتي دائن بس."""
    db, s, o = env["db"], env["cin"], env["cout"]
    cid = s.save_draft(None, cin(db), post=True)
    assert party_lines(db, "customer_id", customer(db)) == [("1103", 0, 10000)]
    s.do_step(cid, "deposit", dt.date(2026, 4, 11), bank_account_id=account(db))
    s.do_step(cid, "bounce", dt.date(2026, 4, 12))                    # رجع ← على العميل تاني (مدين)
    assert party_lines(db, "customer_id", customer(db)) == [("1103", 0, 10000), ("1103", 10000, 0)]
    pid = o.save_draft(None, cout(db), post=True)
    assert party_lines(db, "supplier_id", supplier(db)) == [("2101", 4000, 0)]
    o.complete(pid, dt.date(2026, 4, 10))
    assert party_lines(db, "supplier_id", supplier(db)) == [("2101", 4000, 0)]


def test_0039_fixes_old_lines(temp_db, tmp_path):
    """القاعدة اللي اتنفذ عليها 0038 بقيود على أوراق القبض بالعميل ← 0039 بيشيل الطرف والقيد يفضل مرحّل."""
    import shutil

    from app.services.journal_service import JournalService

    folder = tmp_path / "m"
    folder.mkdir()
    for f in sorted(migrator.migrations_dir().glob("*.sql")):
        if f.name < "0039":
            shutil.copy(f, folder / f.name)
    migrator.migrate(temp_db, folder)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2026', '2026-01-01', '2026-12-31')")
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    try:
        acc = {r["code"]: r["id"] for r in temp_db.fetch_all("SELECT id, code FROM acc.accounts")}
        cust = customer(temp_db)
        js = JournalService(temp_db)
        eid = js.save_draft(None, {"entry_date": D, "description": "شيك قديم", "lines": [
            {"account_id": acc["1104"], "debit": "500", "party_id": cust},
            {"account_id": acc["1103"], "credit": "500", "party_id": cust}]})
        js.post(eid)
        assert one(temp_db, "SELECT count(*) FROM acc.journal_lines WHERE customer_id = %s", cust) == 2
        migrator.migrate(temp_db)                                         # 0039
        assert one(temp_db, "SELECT count(*) FROM acc.journal_lines WHERE customer_id = %s", cust) == 1
        assert one(temp_db, "SELECT status FROM acc.journal_entries WHERE id = %s", eid) == "posted"
        assert one(temp_db, "SELECT party_kind FROM acc.accounts WHERE code = '1104'") is None
    finally:
        SESSION.clear()


def test_step_dialog_opens_for_real(env):
    """النافذة الحقيقية (من غير monkeypatch) — كانت بتقفل البرنامج (self.event كان بيغطّي QWidget.event)."""
    from app.ui.screens.cheques_screen import StepDialog

    db = env["db"]
    screen = make_screen(db)
    try:
        cid = env["cin"].save_draft(None, cin(db), post=True)
        screen.refresh_list()
        screen.open_cheque(cid)
        for event, complete in (("deposit", False), ("endorse", False), ("collect", True)):
            dialog = StepDialog(screen, event, "خطوة", complete=complete)
            dialog.setWindowTitle("تاني")                               # بيبعت event — كان بيقع هنا
            dialog.show()
            QApplication.processEvents()
            values = dialog.values()
            assert values["event_date"] >= D and dialog.entry.rowCount() >= (0 if event == "endorse" else 2)
            dialog.reject()
            dialog.deleteLater()
    finally:
        screen.close()
