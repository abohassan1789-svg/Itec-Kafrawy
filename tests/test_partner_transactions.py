"""اختبارات «حركات الشركاء» (0041): إيداع · سحب · زيادة رأس المال (نقدي / من الجاري) · توزيع أرباح وخسارة بالنسب ·
الربح المتاح · الحماية في القاعدة · الصلاحيات · جهازين · الشاشة."""

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
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.partner_transaction_service import PartnerTransactionService, split_shares
from app.services.permissions_sync_service import PermissionsSyncService
from app.ui.common import messages
from tests.test_account_statement import entry

D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 10), dt.date(2026, 3, 20)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2026', '2026-01-01', '2026-12-31')")
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
    for seq, name, pct, cap in ((1, "مصطفى إبراهيم", 50, 500000), (2, "طارق سليمان", 30, 350000),
                                (3, "هالة منصور", 20, 200000)):
        temp_db.execute("INSERT INTO party.partners (seq, name_ar, profit_percent, loss_percent, subscribed_capital) "
                        "VALUES (%s, %s, %s, %s, %s)", [seq, name, pct, pct, cap])
    yield {"db": temp_db, "shown": shown, "asked": asked, "ps": PartnerTransactionService(temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def prt(db, code="PRT-001"):
    return one(db, "SELECT id FROM party.partners WHERE code = %s", code)


def treasury(db, code="TRS-001"):
    return one(db, "SELECT id FROM cash.treasuries WHERE code = %s", code)


def bank(db, code="BNK-001-01"):
    return one(db, "SELECT id FROM cash.bank_accounts WHERE code = %s", code)


def txn(db, kind, amount, d=D1, partner="PRT-001", **extra):
    out = {"txn_kind": kind, "txn_date": d, "partner_id": prt(db, partner), "source_kind": "treasury",
           "treasury_id": treasury(db), "amount": amount, "description": "حركة"}
    out.update(extra)
    return out


def dist(amount, kind="profit", d=D3):
    return {"dist_kind": kind, "dist_date": d, "amount": amount, "description": "توزيع"}


def income(db, amount, d=D1):
    """إيراد مرحّل (مدين الخزنة / دائن المبيعات) ← ربح الفترة."""
    entry(db, d, "مبيعات نقدي", [("110101", amount, 0), ("4101", 0, amount)])


def entry_lines(db, source_type, source_id):
    return [(r["code"], r["debit"], r["credit"], r["partner_id"]) for r in db.fetch_all(
        "SELECT a.code, l.debit, l.credit, l.partner_id FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
        "WHERE e.source_type = %s AND e.source_id = %s ORDER BY l.line_no", [source_type, source_id])]


# ---- القاعدة -----------------------------------------------------------------
def test_database_rules(env):
    db, ps = env["db"], env["ps"]
    assert one(db, "SELECT a.code FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
                   "WHERE d.key = 'partner_cur'") == "3299"
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة الأول"):
        db.execute("INSERT INTO cash.partner_transactions (txn_kind, txn_date, fiscal_year_id, partner_id, source_kind, "
                   "treasury_id, amount, description, status, posted_at) VALUES ('deposit', %s, 1, %s, 'treasury', "
                   "%s, 10, 'x', 'posted', now())", [D1, prt(db), treasury(db)])
    with pytest.raises(psycopg.errors.CheckViolation):            # «من الجاري» لزيادة رأس المال بس
        db.execute("INSERT INTO cash.partner_transactions (txn_kind, txn_date, fiscal_year_id, partner_id, source_kind, "
                   "amount, description) VALUES ('withdrawal', %s, 1, %s, 'current', 10, 'x')", [D1, prt(db)])
    a = ps.save_txn(None, txn(db, "deposit", "1000"), post=True)
    assert ps.get_txn(a)["txn_no"] == "PD-2026-00001"
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة — ماتتعدّلش"):
        db.execute("UPDATE cash.partner_transactions SET amount = 1 WHERE id = %s", [a])
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة وليها قيد"):
        db.execute("UPDATE cash.partner_transactions SET status = 'draft' WHERE id = %s", [a])
    ps.unpost_txn(a)
    with pytest.raises(psycopg.errors.RaiseException, match="النوع مايتغيّرش"):
        db.execute("UPDATE cash.partner_transactions SET txn_kind = 'withdrawal' WHERE id = %s", [a])
    with pytest.raises(BusinessError, match="رقم رسمي"):
        ps.delete_txn(a)
    # الشريك الموقوف: السحب مسموح (ياخد رصيده) — الإيداع لأ
    db.execute("UPDATE party.partners SET is_active = false, profit_percent = 0, loss_percent = 0 WHERE code = 'PRT-003'")
    with pytest.raises(BusinessError, match="موقوف"):
        ps.save_txn(None, txn(db, "deposit", "10", partner="PRT-003"))
    ps.save_txn(None, txn(db, "withdrawal", "10", partner="PRT-003"))
    # التوزيع: المسودة من غير أنصبة · المرحّل أنصبته ماتتغيّرش
    income(db, 10000)
    db.execute("UPDATE party.partners SET profit_percent = 70, loss_percent = 70 WHERE code = 'PRT-001'")
    x = ps.save_distribution(None, dist("1000"))
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة وعليه أنصبة"):
        db.execute("INSERT INTO acc.profit_distribution_lines (distribution_id, partner_id, share_percent, amount) "
                   "VALUES (%s, %s, 100, 1000)", [x, prt(db)])
    ps.post_distribution(x)
    with pytest.raises(psycopg.errors.RaiseException, match="أنصبة الشركاء ماتتغيّرش"):
        db.execute("UPDATE acc.profit_distribution_lines SET amount = 1 WHERE distribution_id = %s", [x])
    # السنة ماتتقفلش وفيها مسودة
    with pytest.raises(psycopg.errors.RaiseException, match="سند مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed' WHERE year_code = '2026'")


# ---- الإيداع والسحب ورأس المال ----------------------------------------------------
def test_deposit_withdrawal_capital(env):
    db, ps = env["db"], env["ps"]
    p = prt(db)
    preview = ps.preview_txn(txn(db, "deposit", "100000", source_kind="bank", treasury_id=None,
                                 bank_account_id=bank(db)))
    assert [(ln["account_code"], ln["debit"], ln["credit"]) for ln in preview] == [
        ("110201", Decimal("100000.00"), 0), ("3201", 0, Decimal("100000.00"))]
    a = ps.save_txn(None, txn(db, "deposit", "100,000"), post=True)
    assert entry_lines(db, "partner_dep", a) == [("110101", Decimal("100000.00"), Decimal("0"), None),
                                                ("3201", Decimal("0"), Decimal("100000.00"), p)]
    w = ps.save_txn(None, txn(db, "withdrawal", "15000", d=D2))
    assert ps.post_txn(w) == "PW-2026-00001"
    assert entry_lines(db, "partner_wd", w) == [("3201", Decimal("15000.00"), Decimal("0"), p),
                                               ("110101", Decimal("0"), Decimal("15000.00"), None)]
    s = ps.partner_summary(p, D3)
    assert s["current"] == Decimal("85000") and s["capital"] == 0
    assert s["year"]["deposit"] == Decimal("100000") and s["year"]["withdrawal"] == Decimal("15000")
    # سحب أكبر من الجاري ← تحذير بس (ويبقى عليه) · والخزنة مش مكفية ← تحذير
    warns = ps.txn_warnings(txn(db, "withdrawal", "90000"))
    assert any("هيبقى عليه 5,000.00" in w for w in warns) and any("الخزنة" in w for w in warns)
    ps.save_txn(None, txn(db, "withdrawal", "90000"), post=True)
    assert ps.partner_summary(p)["current"] == Decimal("-5000")
    # زيادة رأس المال نقدي ← 31 بالشريك · ومن الجاري ← مدين 32 / دائن 31
    c = ps.save_txn(None, txn(db, "capital", "400000"), post=True)
    assert ps.get_txn(c)["txn_no"] == "PC-2026-00001"
    assert entry_lines(db, "partner_cap", c)[1] == ("31", Decimal("0"), Decimal("400000.00"), p)
    ps.save_txn(None, txn(db, "deposit", "200000"), post=True)
    warns = ps.txn_warnings(txn(db, "capital", "150000", source_kind="current", treasury_id=None))
    assert any("أكبر من المكتتب" in w for w in warns)                       # 400 + 150 > 500
    c2 = ps.save_txn(None, txn(db, "capital", "100000", source_kind="current", treasury_id=None), post=True)
    assert entry_lines(db, "partner_cap", c2) == [("3201", Decimal("100000.00"), Decimal("0"), p),
                                                 ("31", Decimal("0"), Decimal("100000.00"), p)]
    s = ps.partner_summary(p)
    assert s["capital"] == Decimal("500000") and s["current"] == Decimal("95000")
    with pytest.raises(BusinessError, match="اختار خزنة أو بنك"):
        ps.save_txn(None, txn(db, "deposit", "1", source_kind="current", treasury_id=None))
    with pytest.raises(BusinessError, match="اكتب المبلغ"):
        ps.save_txn(None, txn(db, "deposit", "0"))
    # القوائم بالنوع والبحث
    assert ps.count_docs("withdrawal", D1, D3)["total"] == 2
    assert [r["doc_no"] for r in ps.list_docs("capital", D1, D3)] == ["PC-2026-00002", "PC-2026-00001"]
    assert len(ps.list_docs("deposit", D1, D3, keyword="مصطفى")) == 2


# ---- توزيع الأرباح والخسارة ---------------------------------------------------------
def test_split_shares_rounding():
    partners = [{"id": 1, "code": "A", "name_ar": "أ", "profit_percent": Decimal("33.33"), "loss_percent": 0},
                {"id": 2, "code": "B", "name_ar": "ب", "profit_percent": Decimal("33.33"), "loss_percent": 0},
                {"id": 3, "code": "C", "name_ar": "ج", "profit_percent": Decimal("33.34"), "loss_percent": 0}]
    out = split_shares(Decimal("100"), partners, "profit")
    assert [s["amount"] for s in out] == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]
    assert sum(s["amount"] for s in out) == Decimal("100")
    with pytest.raises(BusinessError, match="مفيش شركاء"):
        split_shares(Decimal("100"), partners, "loss")


def test_distribution(env):
    db, ps = env["db"], env["ps"]
    income(db, 240000)
    entry(db, D1, "أرباح سنين فاتت", [("34", 180000, 0), ("33", 0, 180000)])      # محتجزة 180,000
    avail = ps.available_profit(D3)
    assert (avail["retained"], avail["income"], avail["available"]) == (Decimal("180000"), Decimal("240000"),
                                                                        Decimal("420000"))
    pv = ps.distribution_preview("profit", "150000", D3)
    assert [s["amount"] for s in pv["shares"]] == [Decimal("75000.00"), Decimal("45000.00"), Decimal("30000.00")]
    x = ps.save_distribution(None, dist("150000"))
    assert ps.get_distribution(x)["lines"] == []
    assert ps.post_distribution(x) == "PF-2026-00001"
    rec = ps.get_distribution(x)
    assert [(ln["partner_name"], ln["amount"]) for ln in rec["lines"]] == [
        ("مصطفى إبراهيم", Decimal("75000.00")), ("طارق سليمان", Decimal("45000.00")), ("هالة منصور", Decimal("30000.00"))]
    je = entry_lines(db, "profit_dist", x)
    assert je[0] == ("33", Decimal("150000.00"), Decimal("0"), None)
    assert je[1] == ("3201", Decimal("0"), Decimal("75000.00"), prt(db))
    assert je[2] == ("3202", Decimal("0"), Decimal("45000.00"), prt(db, "PRT-002"))
    assert ps.partner_summary(prt(db, "PRT-002"))["current"] == Decimal("45000")
    # قبل الإقفال عادي: 300,000 > المحتجزة (30,000) بس أقل من المتاح (270,000 + ...)؟ لأ — أكبر من المتاح ← ممنوع
    assert ps.available_profit(D3)["available"] == Decimal("270000")
    with pytest.raises(BusinessError, match="أكبر من الربح المتاح"):
        ps.save_distribution(None, dist("300000"), post=True)
    y = ps.save_distribution(None, dist("100000"), post=True)                 # المحتجزة هتبقى بالسالب — عادي
    assert ps.available_profit(D3)["retained"] == Decimal("-70000")
    # النسب مش 100% ← ممنوع
    db.execute("UPDATE party.partners SET profit_percent = 15 WHERE code = 'PRT-003'")
    with pytest.raises(BusinessError, match="95% مش 100%"):
        ps.save_distribution(None, dist("1000"), post=True)
    assert "95%" in ps.distribution_preview("profit", "1000", D3)["error"]
    db.execute("UPDATE party.partners SET profit_percent = 20 WHERE code = 'PRT-003'")
    # تحميل خسارة: مدين 32 لكل شريك / دائن 33 — وتحذير لأن الفترة كسبانة
    assert ps.dist_warnings(dist("10000", "loss"))
    z = ps.save_distribution(None, dist("10000", "loss"), post=True)
    je = entry_lines(db, "profit_dist", z)
    assert ("33", Decimal("0"), Decimal("10000.00"), None) in je and len(je) == 4
    assert ("3201", Decimal("5000.00"), Decimal("0"), prt(db)) in je
    # إلغاء الترحيل بيمسح القيد والأنصبة
    ps.unpost_distribution(y)
    assert ps.get_distribution(y)["lines"] == [] and entry_lines(db, "profit_dist", y) == []
    with pytest.raises(BusinessError, match="رقم رسمي"):
        ps.delete_distribution(y)
    assert ps.count_docs("distribution", D1, D3)["posted"] == 2
    assert len(ps.list_docs("distribution", D1, D3, partner_id=prt(db))) == 2


def test_permissions(env):
    db, ps = env["db"], env["ps"]
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    perm = "acc.partner_transactions"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.create", f"{perm}.edit", f"{perm}.save"})
    a = ps.save_txn(None, txn(db, "deposit", "100"))
    for call in (lambda: ps.post_txn(a), lambda: ps.save_txn(a, txn(db, "deposit", "5"), post=True),
                 lambda: ps.unpost_txn(a), lambda: ps.delete_txn(a),
                 lambda: ps.save_distribution(None, dist("5"), post=True)):
        with pytest.raises(PermissionDenied):
            call()
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.post", f"{perm}.unpost"})
    ps.post_txn(a)
    ps.unpost_txn(a)
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE %s AND is_active", [f"{perm}.%"])}
    assert codes == {f"{perm}.{x}" for x in ("view", "create", "edit", "save", "delete", "export", "post", "unpost")}


def test_two_devices(env):
    """جهازين: تعديل نفس المسودة ← التاني بيترفض · توزيعين في نفس اللحظة ← التاني بيستنى ويشوف المتاح بعد الأول."""
    db = env["db"]
    income(db, 100000)
    s1, s2 = PartnerTransactionService(db), PartnerTransactionService(Database(db.settings))
    try:
        x = s1.save_txn(None, txn(db, "deposit", "1000"))
        seen1, seen2 = s1.get_txn(x), s2.get_txn(x)
        s2.save_txn(x, txn(db, "deposit", "900"), seen2["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.save_txn(x, txn(db, "deposit", "800"), seen1["row_version"])
        a = s1.save_distribution(None, dist("70000"))
        b = s2.save_distribution(None, dist("70000"))
        results: dict[str, object] = {}

        def worker():
            try:
                results["no"] = s2.post_distribution(b)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            s1.repository.lock_distributions(tx)                         # جهاز بيرحّل توزيع a
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                                     # التاني مستني
            s1._post_dist(tx, a, s1._snapshot(tx, a))
        thread.join(10)
        assert isinstance(results.get("error"), BusinessError) and "30,000.00" in str(results["error"]), results
        assert s1.get_distribution(a)["status"] == "posted" and s1.get_distribution(b)["status"] == "draft"
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------------
def plain(text):
    return text.replace("⁦", "").replace("⁩", "")


def make_screen(db):
    from app.ui.screens.partner_transactions_screen import PartnerTransactionsScreen

    screen = PartnerTransactionsScreen(PartnerTransactionService(db))
    screen.period_from.setDate(screen.period_from.date().addYears(-5))
    screen.period_to.setDate(screen.period_to.date().addYears(5))
    return screen


def set_day(edit, day):
    edit.setDate(edit.date().fromString(day, "yyyy-MM-dd"))


def test_screen(env):
    db, shown, asked = env["db"], env["shown"], env["asked"]
    income(db, 240000)
    screen = make_screen(db)
    try:
        assert screen.windowTitle().endswith("حركات الشركاء") and screen.kind_tabs["deposit"].isChecked()
        assert not screen.via_combo.isVisible() and screen.stack.currentIndex() == 0
        # 1) إيداع ← ترحيل
        screen.new_record()
        assert screen.mode == "new" and screen.source_edit.currentData() == treasury(db)
        screen.partner_edit.set_value(prt(db))
        set_day(screen.date_edit, "2026-03-01")
        screen.amount_edit.setText("50000")
        screen._refresh_side()
        assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
        assert "مصطفى إبراهيم" in screen.card_label.text()
        screen.post_doc()
        assert screen.current_record["txn_no"] == "PD-2026-00001" and screen.current_record["status"] == "posted"
        screen._refresh_side()
        assert screen.entry_box.title() == "القيد المرحّل" and "50,000.00 له" in plain(screen.card_label.text())
        # 2) سحب أكبر من الجاري ← تحذير في الشاشة وسؤال قبل الترحيل ويبقى «عليه»
        screen.kind_tabs["withdrawal"].click()
        assert screen.kind == "withdrawal" and screen.table.rows == [] and screen.source_label.text() == "الصرف من *"
        screen.new_record()
        screen.partner_edit.set_value(prt(db))
        set_day(screen.date_edit, "2026-03-05")
        screen.amount_edit.setText("60000")
        screen._refresh_side()
        assert screen.warn_label.isVisibleTo(screen) and "هيبقى عليه 10,000.00" in screen.warn_label.text()
        asked.clear()
        screen.post_doc()
        assert any("هيبقى عليه" in a for a in asked) and screen.current_record["txn_no"] == "PW-2026-00001"
        screen._refresh_side()
        assert "10,000.00 عليه" in plain(screen.card_label.text())
        # 3) زيادة رأس مال من الجاري: من غير خزنة
        screen.kind_tabs["capital"].click()
        screen.new_record()
        assert screen.via_combo.isVisibleTo(screen)
        screen.partner_edit.set_value(prt(db, "PRT-002"))
        screen.via_combo.setCurrentIndex(1)
        assert not screen.source_row.isVisibleTo(screen)
        screen.amount_edit.setText("1000")
        screen.save()
        assert shown[-1] == "saved" and screen.current_record["source_kind"] == "current"
        assert screen.current_record["status"] == "draft"
        screen.post_doc()                                                     # ترحيل المسودة من وضع العرض
        assert screen.current_record["txn_no"] == "PC-2026-00001"
        # 4) توزيع أرباح: «كل المتاح» ← الأنصبة ← ترحيل
        screen.kind_tabs["distribution"].click()
        assert screen.stack.currentIndex() == 1 and screen.shares_table.isVisibleTo(screen)
        screen.new_record()
        set_day(screen.date_edit, "2026-03-20")
        screen.fill_available()
        assert screen.dist_amount.text() == "240,000.00"
        screen._refresh_side()
        assert screen.shares_table.rowCount() == 3 and screen.shares_table.item(0, 2).text() == "120,000.00"
        assert "240,000.00" in plain(screen.avail_label.text())
        assert screen.entry_table.rowCount() == 4 and "متوازن" in screen.entry_label.text()
        screen.dist_amount.setText("300000")
        screen._refresh_side()
        assert "أكبر من المتاح" in screen.card_label.text()
        screen.post_doc()
        assert screen.mode == "new" and "أكبر من الربح المتاح" in shown[-1]
        screen.dist_amount.setText("100000")
        screen.post_doc()
        rec = screen.current_record
        assert rec["dist_no"] == "PF-2026-00001" and len(rec["lines"]) == 3
        assert plain(screen.table.item(0, 2).text()) == "كل الشركاء — ربح"
        # 5) إلغاء الترحيل ← مسودة من غير أنصبة ← حذف ممنوع (خد رقم)
        screen.unpost_doc()
        assert screen.current_record["status"] == "draft" and screen.current_record["lines"] == []
        assert not screen.delete_button.isEnabled()
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    t = reg.find_target("acc", "partner_transactions")
    assert t.name_ar == "حركات الشركاء" and "partner_transactions" in SCREEN_FACTORIES
    assert set(t.action_codes()) == {"view", "create", "edit", "save", "delete", "export", "post", "unpost"}
