"""اختبارات «سند قبض العملاء» و«سند صرف العملاء» (0035: جدولين منفصلين · الحماية في القاعدة · الترقيم RV / PV من أول
ترحيل · القيد بمحرك الترحيل · إلغاء الترحيل · تحذير الخزنة مش مكفية · الصلاحيات · جهازين · الشاشتين)."""

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
from app.services.customer_voucher_service import CustomerVoucherService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.trial_balance_service import TrialBalanceService
from app.ui.common import messages

D = dt.date(2026, 3, 10)


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
           "rv": CustomerVoucherService("receipt", temp_db), "pv": CustomerVoucherService("payment", temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def acc(db, code):
    return one(db, "SELECT id FROM acc.accounts WHERE code = %s", code)


def customer(db, code="0"):
    return one(db, "SELECT id FROM party.customers WHERE code = %s", code)


def treasury(db, code="TRS-001"):
    return one(db, "SELECT id FROM cash.treasuries WHERE code = %s", code)


def bank(db, code="BNK-001-01"):
    return one(db, "SELECT id FROM cash.bank_accounts WHERE code = %s", code)


def data(db, amount="5000", source="treasury", d=D, **extra):
    out = {"voucher_date": d, "customer_id": customer(db), "source_kind": source,
           "treasury_id": treasury(db) if source == "treasury" else None,
           "bank_account_id": bank(db) if source == "bank" else None,
           "amount": amount, "description": "تحصيل دفعة من الحساب"}
    out.update(extra)
    return out


def row(db, table, vid):
    return db.fetch_one(f"SELECT * FROM cash.{table} WHERE id = %s", [vid])


def entry_lines(db, doc, vid):
    return db.fetch_all(
        "SELECT a.code, l.debit, l.credit, l.customer_id, l.project_id FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
        "WHERE e.source_type = %s AND e.source_id = %s ORDER BY l.line_no", [doc, vid])


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db = env["db"]
    # الترقيم وقواعد الترحيل
    assert db.fetch_all("SELECT code, prefix FROM core.document_types WHERE code LIKE 'customer_%%' ORDER BY code") == [
        {"code": "customer_payment", "prefix": "PV"}, {"code": "customer_receipt", "prefix": "RV"}]
    assert one(db, "SELECT count(*) FROM acc.posting_rules WHERE doc_type IN ('customer_receipt', 'customer_payment')") == 6

    def ins(table="customer_receipts", **kw):
        vals = {"voucher_date": D, "customer_id": customer(db), "source_kind": "treasury",
                "treasury_id": treasury(db), "amount": 100, "description": "x", **kw}
        cols = ", ".join(vals)
        return one(db, f"INSERT INTO cash.{table} ({cols}) VALUES ({', '.join(['%s'] * len(vals))}) RETURNING id",
                   *vals.values())

    with pytest.raises(psycopg.errors.CheckViolation):
        ins(amount=0)
    with pytest.raises(psycopg.errors.CheckViolation):                   # خزنة وبنك مع بعض
        ins(bank_account_id=bank(db))
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة الأول"):
        ins(status="posted", posted_at=dt.datetime.now(dt.timezone.utc), voucher_no="RV-2026-00009")
    with pytest.raises(psycopg.errors.RaiseException, match="مفيش سنة مالية"):
        ins(voucher_date=dt.date(2030, 1, 1))
    other = one(db, "INSERT INTO party.customers (code, name_ar, group_id, currency_code, is_active) "
                    "SELECT '7', 'عميل موقوف', group_id, 'EGP', false FROM party.customers WHERE code = '0' RETURNING id")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوف"):
        ins(customer_id=other)
    db.execute("UPDATE core.currencies SET is_active = true WHERE code = 'USD'")
    db.execute("UPDATE cash.treasuries SET currency_code = 'USD' WHERE code = 'TRS-002'")
    with pytest.raises(psycopg.errors.RaiseException, match="العملة الأساسية"):
        ins(treasury_id=treasury(db, "TRS-002"))
    vid = ins()
    assert row(db, "customer_receipts", vid)["fiscal_year_id"] == one(db, "SELECT id FROM core.fiscal_years "
                                                                        "WHERE year_code = '2026'")
    # مرحّل من غير قيد ← القاعدة بترفض آخر الـ transaction
    with pytest.raises(psycopg.errors.RaiseException, match="من غير قيد"):
        db.execute("UPDATE cash.customer_receipts SET status = 'posted' WHERE id = %s", [vid])
    assert row(db, "customer_receipts", vid)["voucher_no"] is None            # الرقم رجع مع الـ rollback
    # السنة ماتتقفلش وفيها مسودة · تواريخها ماتسيبش سند برّاها
    with pytest.raises(psycopg.errors.RaiseException, match="سند مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")
    with pytest.raises(psycopg.errors.RaiseException, match="برّا المدة"):
        db.execute("UPDATE core.fiscal_years SET start_date = '2026-04-01' WHERE year_code = '2026'")
    # المسودة من غير رقم بتتمسح · الجدول التاني لوحده
    pid = ins("customer_payments")
    db.execute("DELETE FROM cash.customer_receipts WHERE id = %s", [vid])
    assert row(db, "customer_payments", pid) is not None
    db.execute("UPDATE party.customers SET is_active = true WHERE id = %s", [other])
    ins("customer_payments", customer_id=other)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):                # العميل اللي عليه سندات مايتمسحش
        db.execute("DELETE FROM party.customers WHERE id = %s", [other])


# ---- الـ Service -------------------------------------------------------------
def test_receipt_flow(env):
    db, rv = env["db"], env["rv"]
    with pytest.raises(BusinessError, match="اختار العميل"):
        rv.save_draft(None, data(db, customer_id=None))
    with pytest.raises(BusinessError, match="اكتب مبلغ"):
        rv.save_draft(None, data(db, amount="0"))
    with pytest.raises(BusinessError, match="بقرشين"):
        rv.save_draft(None, data(db, amount="10.555"))
    with pytest.raises(BusinessError, match="البيان مطلوب"):
        rv.save_draft(None, data(db, description="  "))
    # معاينة القيد قبل الحفظ
    preview = rv.preview(data(db))
    assert [(p["account_code"], p["debit"], p["credit"]) for p in preview] == [
        ("110101", Decimal("5000.00"), Decimal("0")), ("1103", Decimal("0"), Decimal("5000.00"))]

    vid = rv.save_draft(None, data(db, paper_no="  دفتر 12  "))
    v = rv.get_voucher(vid)
    assert v["status"] == "draft" and v["voucher_no"] is None and v["label"] == f"مسودة {v['draft_no']}"
    assert v["paper_no"] == "دفتر 12" and v["customer_name"] == "عميل نقدي"
    assert entry_lines(db, "customer_receipt", vid) == []
    no = rv.post(vid, v["row_version"])
    assert no == "RV-2026-00001"
    lines = entry_lines(db, "customer_receipt", vid)
    assert [(ln["code"], ln["debit"], ln["credit"]) for ln in lines] == [
        ("110101", Decimal("5000.00"), Decimal("0.00")), ("1103", Decimal("0.00"), Decimal("5000.00"))]
    assert lines[1]["customer_id"] == customer(db)
    entry = rv.entry_for(vid)
    assert entry["source_no"] == "RV-2026-00001" and entry["reference"] == "دفتر 12" and entry["entry_no"] is None
    # الأرصدة: العميل دائن 5,000 · الخزنة مدين 5,000
    b = rv.balances(customer(db), "treasury", treasury(db), "1000")
    assert (b["customer"], b["customer_after"], b["source"], b["source_after"]) == (
        Decimal("-5000.00"), Decimal("-6000.00"), Decimal("5000.00"), Decimal("6000.00"))
    # المرحّل مايتعدّلش ولا يتمسح
    with pytest.raises(BusinessError, match="مرحّل"):
        rv.save_draft(vid, data(db, amount="10"))
    with pytest.raises(BusinessError, match="مرحّل"):
        rv.delete_voucher(vid)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّل"):
        db.execute("UPDATE cash.customer_receipts SET amount = 1 WHERE id = %s", [vid])
    # إلغاء الترحيل ← القيد بيتمسح والسند يرجع مسودة برقمه · يتعدّل ويترحّل تاني بنفس الرقم
    rv.unpost(vid)
    v = rv.get_voucher(vid)
    assert v["status"] == "draft" and v["voucher_no"] == "RV-2026-00001"
    assert entry_lines(db, "customer_receipt", vid) == []
    with pytest.raises(BusinessError, match="خد رقم رسمي"):
        rv.delete_voucher(vid)
    with pytest.raises(BusinessError, match="نفس السنة"):
        rv.save_draft(vid, data(db, d=dt.date(2025, 12, 30)))
    rv.save_draft(vid, data(db, amount="4500", source="bank"), post=True)
    v = rv.get_voucher(vid)
    assert v["voucher_no"] == "RV-2026-00001" and v["status"] == "posted" and v["bank_account_id"] == bank(db)
    assert [ln["code"] for ln in entry_lines(db, "customer_receipt", vid)] == ["110201", "1103"]
    # الترقيم كل سنة لوحده · المسودة من غير رقم بتتمسح
    old = rv.save_draft(None, data(db, d=dt.date(2025, 6, 1)), post=True)
    assert rv.get_voucher(old)["voucher_no"] == "RV-2025-00001"
    draft = rv.save_draft(None, data(db))
    rv.delete_voucher(draft)
    assert rv.get_voucher(draft) is None
    # القائمة والعدّ والبحث
    assert [r["id"] for r in rv.list_vouchers(dt.date(2026, 1, 1), dt.date(2026, 12, 31))] == [vid]
    counts = rv.count_vouchers(None, None)
    assert counts == {"total": 2, "drafts": 0, "posted": 2, "posted_amount": Decimal("9500.00")}
    assert [r["id"] for r in rv.list_vouchers(None, None, keyword="rv-2025-00001")] == [old]
    assert [r["id"] for r in rv.list_vouchers(None, None, keyword="نقدي")] == [vid, old]
    # السنة المقفولة ← مايتلغيش الترحيل
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2025'")
    with pytest.raises(BusinessError, match="مقفولة"):
        rv.unpost(old)
    # ميزان المراجعة متوازن والعميل عليه الحركة
    tb = TrialBalanceService(db).trial_balance(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert tb["balanced"]


def test_payment_and_funds_warning(env):
    db, rv, pv = env["db"], env["rv"], env["pv"]
    rv.save_draft(None, data(db, amount="3000"), post=True)
    assert pv.funds_warning(data(db, amount="3000")) is None
    warning = pv.funds_warning(data(db, amount="3500", description="رد مبلغ للعميل"))
    assert "3,000.00" in warning and "-500.00" in warning and "الخزنة" in warning
    assert rv.funds_warning(data(db, amount="999999")) is None              # القبض مالوش تحذير
    # تحذير بس — الترحيل بيعدّي والخزنة بالسالب
    pid = pv.save_draft(None, data(db, amount="3500", description="رد مبلغ للعميل"), post=True)
    p = pv.get_voucher(pid)
    assert p["voucher_no"] == "PV-2026-00001"
    assert [(ln["code"], ln["debit"], ln["credit"]) for ln in entry_lines(db, "customer_payment", pid)] == [
        ("1103", Decimal("3500.00"), Decimal("0.00")), ("110101", Decimal("0.00"), Decimal("3500.00"))]
    b = pv.balances(customer(db), "treasury", treasury(db), "0", posted=True)
    assert b["source"] == Decimal("-500.00") and b["customer"] == Decimal("500.00")
    # الجاري المدين: حد التسهيلات بيدخل في الحساب
    db.execute("UPDATE cash.bank_accounts SET account_type = 'overdraft', overdraft_limit = 10000 WHERE code = 'BNK-001-01'")
    assert pv.funds_warning(data(db, amount="9000", source="bank")) is None
    assert "حد الجاري المدين" in pv.funds_warning(data(db, amount="12000", source="bank"))
    # أمر التوريد على كل سطور القيد · المقفول بيترفض
    center = one(db, "SELECT id FROM acc.cost_centers WHERE is_leaf AND is_active ORDER BY code LIMIT 1")
    project = one(db, "INSERT INTO acc.projects (code, name, cost_center_id, currency_code) "
                      "VALUES ('Itec-900', 'تجربة', %s, 'EGP') RETURNING id", center)
    pid2 = pv.save_draft(None, data(db, amount="100", project_id=project), post=True)
    assert {ln["project_id"] for ln in entry_lines(db, "customer_payment", pid2)} == {project}
    db.execute("UPDATE acc.projects SET status = 'closed' WHERE id = %s", [project])
    with pytest.raises(BusinessError, match="مش جاري"):
        pv.save_draft(None, data(db, amount="100", project_id=project))


def test_permissions(env):
    """كل شاشة بصلاحياتها: «ترحيل» و«إلغاء الترحيل» لوحدهم · القبض غير الصرف."""
    db, rv, pv = env["db"], env["rv"], env["pv"]
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    perm = "cash.customer_receipts"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.create", f"{perm}.edit", f"{perm}.save"})
    vid = rv.save_draft(None, data(db))
    for call in (lambda: rv.post(vid), lambda: rv.save_draft(vid, data(db), post=True), lambda: rv.unpost(vid),
                 lambda: rv.delete_voucher(vid), lambda: pv.save_draft(None, data(db))):
        with pytest.raises(PermissionDenied):
            call()
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.post", f"{perm}.unpost"})
    rv.post(vid)
    rv.unpost(vid)
    for target in ("customer_receipts", "customer_payments"):
        codes = {r["permission_code"] for r in db.fetch_all(
            "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE %s", [f"cash.{target}.%"])}
        assert codes == {f"cash.{target}.{a}" for a in
                         ("view", "create", "edit", "save", "delete", "export", "post", "unpost")}


def test_two_devices(env):
    """جهازين: تعديل نفس المسودة ← التاني بيترفض · ترحيل من جهازين ← مرة واحدة بس · ترحيلين في نفس اللحظة ← رقمين."""
    db = env["db"]
    s1, s2 = CustomerVoucherService("receipt", db), CustomerVoucherService("receipt", Database(db.settings))
    try:
        vid = s1.save_draft(None, data(db))
        seen1, seen2 = s1.get_voucher(vid), s2.get_voucher(vid)
        s2.save_draft(vid, data(db, amount="5100"), seen2["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.save_draft(vid, data(db, amount="5200"), seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.post(vid, seen1["row_version"])
        fresh = s1.get_voucher(vid)
        s2.post(vid, fresh["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.post(vid, fresh["row_version"])                    # الجهاز الأول شايف المسودة القديمة
        assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE source_type = 'customer_receipt'") == 1
        # ترحيلين في نفس اللحظة: التاني بيستنى قفل العدّاد وبياخد الرقم اللي بعده
        a, b = s1.save_draft(None, data(db)), s2.save_draft(None, data(db))
        results: dict[str, object] = {}

        def worker():
            try:
                results["no"] = s2.post(b)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            tx.execute("SELECT core.next_document_no('customer_receipt', %s)", [D])   # جهاز تالت ماسك العدّاد
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()
        thread.join(10)
        assert "error" not in results, results
        assert results["no"] == "RV-2026-00003"
        assert s1.post(a) == "RV-2026-00004"
    finally:
        s2.db.close()


# ---- الشاشتين ----------------------------------------------------------------
def make_screen(db, kind="receipt"):
    from app.ui.screens.customer_vouchers_screen import CustomerPaymentsScreen, CustomerReceiptsScreen

    cls = CustomerReceiptsScreen if kind == "receipt" else CustomerPaymentsScreen
    screen = cls(CustomerVoucherService(kind, db))
    screen.from_edit.setDate(screen.from_edit.date().addYears(-5))
    screen.to_edit.setDate(screen.to_edit.date().addYears(5))
    return screen


def test_screens(env):
    db, shown, asked = env["db"], env["shown"], env["asked"]
    screen = make_screen(db)
    try:
        assert screen.windowTitle().endswith("سند قبض العملاء") and screen.save_button.text() == "حفظ مسودة"
        screen.new_record()
        assert screen.mode == "new" and screen.source_edit.currentData() == treasury(db)
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-10", "yyyy-MM-dd"))
        screen.customer_edit.setText("نقدي")
        screen.customer_edit.editingFinished.emit()
        assert screen.customer_edit.currentData() == customer(db)
        screen.amount_edit.setText("2,500")
        screen.description.setText("تحصيل نقدي")
        screen._refresh_side()
        assert "2,500.00" in screen.kpi_values["amount"].text()
        assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
        assert screen.entry_table.item(0, 0).text().startswith("110101")
        screen.save()
        assert shown[-1] == "saved" and screen.current_record["status"] == "draft"
        assert screen.post_button.isEnabled() and screen.delete_button.isEnabled()
        screen.post_voucher()
        assert screen.current_record["voucher_no"] == "RV-2026-00001" and "RV-2026-00001" in shown[-1]
        assert not screen.edit_button.isEnabled() and screen.unpost_button.isEnabled()
        screen._refresh_side()
        assert screen.entry_box.title() == "القيد المرحّل" and screen.entry_table.rowCount() == 2
        assert screen.table.rows[0]["voucher_no"] == "RV-2026-00001"
        screen.unpost_voucher()
        assert screen.current_record["status"] == "draft" and not screen.delete_button.isEnabled()
        screen.edit_record()
        screen.source_kind.setCurrentIndex(1)                       # بنك ← أول حساب بنكي
        assert screen.source_edit.currentData() == bank(db)
        screen.post_voucher()                                       # وانت بتعدّل = حفظ + ترحيل
        assert screen.current_record["bank_account_id"] == bank(db) and screen.mode == "view"
    finally:
        screen.close()

    pay = make_screen(db, "payment")
    try:
        assert pay.windowTitle().endswith("سند صرف العملاء")
        pay.new_record()
        pay.date_edit.setDate(pay.date_edit.date().fromString("2026-03-11", "yyyy-MM-dd"))
        pay.customer_edit.set_value(customer(db))
        pay.amount_edit.setText("700")
        pay.description.setText("رد مبلغ")
        pay._refresh_side()
        assert pay.funds_label.isVisible() or not pay.funds_label.isHidden()  # الخزنة فاضية
        assert pay.entry_table.item(0, 0).text().startswith("1103")
        asked.clear()
        pay.post_voucher()
        assert any("مش مكفي" in a or "تكمّل الترحيل" in a for a in asked)   # تحذير بس — وبعدين اترحّل
        assert pay.current_record["voucher_no"] == "PV-2026-00001"
    finally:
        pay.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    for target, title in (("customer_receipts", "سند قبض العملاء"), ("customer_payments", "سند صرف العملاء")):
        t = reg.find_target("cash", target)
        assert t.name_ar == title and target in SCREEN_FACTORIES
        assert set(t.action_codes()) == {"view", "create", "edit", "save", "delete", "export", "post", "unpost"}
