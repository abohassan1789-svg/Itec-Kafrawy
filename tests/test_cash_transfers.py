"""اختبارات «حركات الخزائن والبنوك» (0037): الحماية في القاعدة · النوع لوحده من «من» و«إلى» · الترقيم CT من أول
ترحيل · القيد بمحرك الترحيل (والمصاريف البنكية) · إلغاء الترحيل · تحذير الرصيد مش مكفي · الصلاحيات · جهازين · الشاشة."""

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
from app.services.cash_transfer_service import CashTransferService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.trial_balance_service import TrialBalanceService
from app.services.voucher_service import VoucherService
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
    yield {"db": temp_db, "shown": shown, "asked": asked, "ct": CashTransferService(temp_db),
           "rv": VoucherService("receipt", temp_db)}
    SESSION.clear()
    gc.collect()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def treasury(db, code="TRS-001"):
    return one(db, "SELECT id FROM cash.treasuries WHERE code = %s", code)


def bank(db, code="BNK-001-01"):
    return one(db, "SELECT id FROM cash.bank_accounts WHERE code = %s", code)


def data(db, amount="5000", frm=("treasury", "TRS-001"), to=("bank", "BNK-001-01"), d=D, **extra):
    """بيانات حركة (الافتراضي: إيداع من الخزينة الرئيسية في البنك الأهلي)."""
    out = {"transfer_date": d, "amount": amount, "description": "إيداع نقدية الخزينة"}
    for side, (kind, code) in (("from", frm), ("to", to)):
        out.update({f"{side}_kind": kind,
                    f"{side}_treasury_id": treasury(db, code) if kind == "treasury" else None,
                    f"{side}_bank_account_id": bank(db, code) if kind == "bank" else None})
    out.update(extra)
    return out


def fund(env, amount="10000"):
    """سند قبض نقدي في الخزينة الرئيسية عشان يبقى فيها رصيد."""
    db = env["db"]
    customer = one(db, "SELECT id FROM party.customers WHERE code = '0'")
    env["rv"].save_draft(None, {"voucher_date": D, "party_id": customer, "source_kind": "treasury",
                                "treasury_id": treasury(db), "amount": amount, "description": "تحصيل"}, post=True)


def entry_lines(db, tid):
    return [(r["code"], r["debit"], r["credit"]) for r in db.fetch_all(
        "SELECT a.code, l.debit, l.credit FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id JOIN acc.accounts a ON a.id = l.account_id "
        "WHERE e.source_type = 'cash_transfer' AND e.source_id = %s ORDER BY l.line_no", [tid])]


def bank_charges(db):
    return one(db, "SELECT a.code FROM acc.default_accounts d JOIN acc.accounts a ON a.id = d.account_id "
                   "WHERE d.key = 'bank_charges'")


# ---- القاعدة ---------------------------------------------------------------
def test_database_rules(env):
    db = env["db"]
    assert one(db, "SELECT prefix FROM core.document_types WHERE code = 'cash_transfer'") == "CT"
    assert [r["role"] for r in db.fetch_all("SELECT role FROM acc.posting_rules WHERE doc_type = 'cash_transfer' "
                                            "ORDER BY sort_order")] == [
        "to_cash", "to_bank", "fees", "from_cash", "from_bank"]

    def ins(**kw):
        vals = {"transfer_date": D, "transfer_type": "x", "from_kind": "treasury", "from_treasury_id": treasury(db),
                "to_kind": "bank", "to_bank_account_id": bank(db), "amount": 100, "description": "x", **kw}
        cols = ", ".join(vals)
        return one(db, f"INSERT INTO cash.cash_transfers ({cols}) VALUES ({', '.join(['%s'] * len(vals))}) "
                       "RETURNING id", *vals.values())

    tid = ins()                                                    # النوع بيتحط لوحده (مش من برّا)
    row = db.fetch_one("SELECT transfer_type, fiscal_year_id FROM cash.cash_transfers WHERE id = %s", [tid])
    assert row["transfer_type"] == "deposit"
    db.execute("UPDATE cash.cash_transfers SET from_kind = 'bank', from_treasury_id = NULL, "
               "from_bank_account_id = %s, to_kind = 'treasury', to_bank_account_id = NULL, to_treasury_id = %s "
               "WHERE id = %s", [bank(db), treasury(db), tid])
    assert one(db, "SELECT transfer_type FROM cash.cash_transfers WHERE id = %s", tid) == "withdraw"
    with pytest.raises(psycopg.errors.CheckViolation):            # «من» = «إلى»
        ins(to_kind="treasury", to_bank_account_id=None, to_treasury_id=treasury(db))
    with pytest.raises(psycopg.errors.CheckViolation):
        ins(amount=0)
    with pytest.raises(psycopg.errors.CheckViolation):
        ins(fees=-1)
    with pytest.raises(psycopg.errors.CheckViolation):            # خزنة وبنك مع بعض في «من»
        ins(from_bank_account_id=bank(db))
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة الأول"):
        ins(status="posted", posted_at=dt.datetime.now(dt.timezone.utc), transfer_no="CT-2026-00009")
    with pytest.raises(psycopg.errors.RaiseException, match="مفيش سنة مالية"):
        ins(transfer_date=dt.date(2030, 1, 1))
    db.execute("UPDATE cash.treasuries SET is_active = false WHERE code = 'TRS-002'")
    with pytest.raises(psycopg.errors.RaiseException, match="موقوفة"):
        ins(to_kind="treasury", to_bank_account_id=None, to_treasury_id=treasury(db, "TRS-002"))
    db.execute("UPDATE cash.treasuries SET is_active = true WHERE code = 'TRS-002'")
    db.execute("UPDATE core.currencies SET is_active = true WHERE code = 'USD'")
    db.execute("UPDATE cash.treasuries SET currency_code = 'USD' WHERE code = 'TRS-002'")
    with pytest.raises(psycopg.errors.RaiseException, match="العملة الأساسية"):
        ins(to_kind="treasury", to_bank_account_id=None, to_treasury_id=treasury(db, "TRS-002"))
    # مرحّلة من غير قيد ← القاعدة بترفض آخر الـ transaction
    with pytest.raises(psycopg.errors.RaiseException, match="من غير قيد"):
        db.execute("UPDATE cash.cash_transfers SET status = 'posted' WHERE id = %s", [tid])
    # السنة ماتتقفلش وفيها مسودة · تواريخها ماتسيبش حركة برّاها
    with pytest.raises(psycopg.errors.RaiseException, match="سند مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")
    with pytest.raises(psycopg.errors.RaiseException, match="برّا المدة"):
        db.execute("UPDATE core.fiscal_years SET start_date = '2026-04-01' WHERE year_code = '2026'")
    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.RestrictViolation,
                        psycopg.errors.RaiseException)):
        db.execute("DELETE FROM cash.bank_accounts WHERE id = %s", [bank(db)])   # عليه حركة
    db.execute("DELETE FROM cash.cash_transfers WHERE id = %s", [tid])


# ---- الـ Service -------------------------------------------------------------
def test_transfer_flow(env):
    db, ct = env["db"], env["ct"]
    fund(env)
    with pytest.raises(BusinessError, match="نفس الحساب"):
        ct.save_draft(None, data(db, to=("treasury", "TRS-001")))
    with pytest.raises(BusinessError, match="اكتب مبلغ"):
        ct.save_draft(None, data(db, amount="0"))
    with pytest.raises(BusinessError, match="اختار الحساب البنكي"):
        ct.save_draft(None, data(db, to_bank_account_id=None))
    with pytest.raises(BusinessError, match="البيان مطلوب"):
        ct.save_draft(None, data(db, description=" "))
    # معاينة القيد: مدين البنك · دائن الخزنة
    assert [(p["account_code"], p["debit"], p["credit"]) for p in ct.preview(data(db))] == [
        ("110201", Decimal("5000.00"), Decimal("0")), ("110101", Decimal("0"), Decimal("5000.00"))]

    tid = ct.save_draft(None, data(db, reference_no="  إيصال 77 "))
    t = ct.get_transfer(tid)
    assert t["status"] == "draft" and t["transfer_no"] is None and t["label"] == f"مسودة {t['draft_no']}"
    assert t["transfer_type"] == "deposit" and t["reference_no"] == "إيصال 77"
    assert t["from_name"] == "الخزينة الرئيسية" and t["to_code"] == "BNK-001-01"
    assert entry_lines(db, tid) == []
    assert ct.post(tid, t["row_version"]) == "CT-2026-00001"
    assert entry_lines(db, tid) == [("110201", Decimal("5000.00"), Decimal("0.00")),
                                    ("110101", Decimal("0.00"), Decimal("5000.00"))]
    entry = ct.entry_for(tid)
    assert entry["source_no"] == "CT-2026-00001" and entry["reference"] == "إيصال 77"
    # الأرصدة: الخزنة 10,000 − 5,000 · البنك 5,000
    b = ct.balances("treasury", treasury(db), "bank", bank(db), "1000", "10")
    assert (b["from"], b["from_after"], b["to"], b["to_after"]) == (
        Decimal("5000.00"), Decimal("3990.00"), Decimal("5000.00"), Decimal("6000.00"))
    # المرحّلة ماتتعدّلش ولا تتمسح
    with pytest.raises(BusinessError, match="مرحّلة"):
        ct.save_draft(tid, data(db, amount="10"))
    with pytest.raises(BusinessError, match="مرحّلة"):
        ct.delete_transfer(tid)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة"):
        db.execute("UPDATE cash.cash_transfers SET amount = 1 WHERE id = %s", [tid])
    # إلغاء الترحيل ← القيد بيتمسح والحركة ترجع مسودة برقمها · تتعدّل (سحب بمصاريف) وتترحّل بنفس الرقم
    ct.unpost(tid)
    t = ct.get_transfer(tid)
    assert t["status"] == "draft" and t["transfer_no"] == "CT-2026-00001" and entry_lines(db, tid) == []
    with pytest.raises(BusinessError, match="خدت رقم رسمي"):
        ct.delete_transfer(tid)
    with pytest.raises(BusinessError, match="نفس السنة"):
        ct.save_draft(tid, data(db, d=dt.date(2025, 12, 30)))
    ct.save_draft(tid, data(db, amount="2000", fees="15", frm=("bank", "BNK-001-01"), to=("treasury", "TRS-002")),
                  post=True)
    t = ct.get_transfer(tid)
    assert (t["transfer_no"], t["status"], t["transfer_type"]) == ("CT-2026-00001", "posted", "withdraw")
    charges = bank_charges(db)
    assert entry_lines(db, tid) == [("110102", Decimal("2000.00"), Decimal("0.00")),
                                    (charges, Decimal("15.00"), Decimal("0.00")),
                                    ("110201", Decimal("0.00"), Decimal("2015.00"))]
    # بين خزنتين · بين بنكين · الترقيم كل سنة لوحده
    t2t = ct.save_draft(None, data(db, amount="300", to=("treasury", "TRS-002")), post=True)
    assert ct.get_transfer(t2t)["transfer_type"] == "t2t"
    b2b = ct.save_draft(None, data(db, amount="100", frm=("bank", "BNK-001-01"), to=("bank", "BNK-002-01")),
                        post=True)
    assert ct.get_transfer(b2b)["transfer_type"] == "b2b" and ct.get_transfer(b2b)["transfer_no"] == "CT-2026-00003"
    old = ct.save_draft(None, data(db, d=dt.date(2025, 6, 1)), post=True)
    assert ct.get_transfer(old)["transfer_no"] == "CT-2025-00001"
    draft = ct.save_draft(None, data(db, amount="1"))
    ct.delete_transfer(draft)
    assert ct.get_transfer(draft) is None
    # القائمة والعدّ بالنوع (التبويبات) والبحث
    counts = ct.count_transfers(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    assert counts["total"] == 3 and counts["types"] == {"deposit": 0, "withdraw": 1, "t2t": 1, "b2b": 1}
    assert counts["all"] == 3 and counts["posted_amount"] == Decimal("2400.00")
    assert [r["id"] for r in ct.list_transfers(None, None, transfer_type="t2t")] == [t2t]
    assert ct.count_transfers(None, None, transfer_type="b2b")["total"] == 1
    assert [r["id"] for r in ct.list_transfers(None, None, keyword="ct-2025-00001")] == [old]
    assert [r["id"] for r in ct.list_transfers(None, None, keyword="بنك مصر")] == [b2b]
    # السنة المقفولة ← مايتلغيش الترحيل · الميزان متوازن
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2025'")
    with pytest.raises(BusinessError, match="مقفولة"):
        ct.unpost(old)
    assert TrialBalanceService(db).trial_balance(dt.date(2026, 1, 1), dt.date(2026, 12, 31))["balanced"]


def test_funds_warning(env):
    db, ct = env["db"], env["ct"]
    fund(env, "3000")
    assert ct.funds_warning(data(db, amount="3000")) is None
    warning = ct.funds_warning(data(db, amount="3000", fees="20"))      # المصاريف بتخرج من «من» برضه
    assert "3,000.00" in warning and "-20.00" in warning and "الخزنة" in warning
    # تحذير بس — الترحيل بيعدّي والخزنة بالسالب
    tid = ct.save_draft(None, data(db, amount="3500"), post=True)
    assert ct.get_transfer(tid)["status"] == "posted"
    assert ct.balances("treasury", treasury(db), "bank", bank(db), "0", posted=True)["from"] == Decimal("-500.00")
    # الجاري المدين: حد التسهيلات بيدخل في الحساب
    db.execute("UPDATE cash.bank_accounts SET account_type = 'overdraft', overdraft_limit = 10000 "
               "WHERE code = 'BNK-001-01'")
    frm = ("bank", "BNK-001-01")
    assert ct.funds_warning(data(db, amount="9000", frm=frm, to=("treasury", "TRS-001"))) is None
    assert "حد الجاري المدين" in ct.funds_warning(data(db, amount="14000", frm=frm, to=("treasury", "TRS-001")))


def test_permissions(env):
    """«ترحيل» و«إلغاء الترحيل» لوحدهم."""
    db, ct = env["db"], env["ct"]
    admin_id = one(db, "SELECT id FROM sec.app_users WHERE username = %s", DEFAULT_ADMIN_USERNAME)
    perm = "cash.cash_transfers"
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.create", f"{perm}.edit", f"{perm}.save"})
    tid = ct.save_draft(None, data(db))
    for call in (lambda: ct.post(tid), lambda: ct.save_draft(tid, data(db), post=True), lambda: ct.unpost(tid),
                 lambda: ct.delete_transfer(tid)):
        with pytest.raises(PermissionDenied):
            call()
    SESSION.start({"id": admin_id, "username": "clerk", "is_admin": False, "full_access": False},
                  {f"{perm}.view", f"{perm}.post", f"{perm}.unpost"})
    ct.post(tid)
    ct.unpost(tid)
    codes = {r["permission_code"] for r in db.fetch_all(
        "SELECT permission_code FROM sec.permissions WHERE permission_code LIKE %s AND is_active", [f"{perm}.%"])}
    assert codes == {f"{perm}.{a}" for a in ("view", "create", "edit", "save", "delete", "export", "post", "unpost")}


def test_two_devices(env):
    """جهازين: تعديل نفس المسودة ← التاني بيترفض · ترحيل من جهازين ← مرة واحدة بس · ترحيلين في نفس اللحظة ← رقمين."""
    db = env["db"]
    s1, s2 = CashTransferService(db), CashTransferService(Database(db.settings))
    try:
        tid = s1.save_draft(None, data(db))
        seen1, seen2 = s1.get_transfer(tid), s2.get_transfer(tid)
        s2.save_draft(tid, data(db, amount="5100"), seen2["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.save_draft(tid, data(db, amount="5200"), seen1["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.post(tid, seen1["row_version"])
        fresh = s1.get_transfer(tid)
        s2.post(tid, fresh["row_version"])
        with pytest.raises(ConcurrentEditError):
            s1.post(tid, fresh["row_version"])
        assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE source_type = 'cash_transfer'") == 1
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
            tx.execute("SELECT core.next_document_no('cash_transfer', %s)", [D])   # جهاز تالت ماسك العدّاد
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()
        thread.join(10)
        assert "error" not in results, results
        assert results["no"] == "CT-2026-00003"
        assert s1.post(a) == "CT-2026-00004"
    finally:
        s2.db.close()


# ---- الشاشة ----------------------------------------------------------------
def plain(text):
    return text.replace("⁦", "").replace("⁩", "")


def make_screen(db):
    from app.ui.screens.cash_transfers_screen import CashTransfersScreen

    screen = CashTransfersScreen(CashTransferService(db))
    screen.period_from.setDate(screen.period_from.date().addYears(-5))
    screen.period_to.setDate(screen.period_to.date().addYears(5))
    return screen


def test_screen(env):
    db, shown, asked = env["db"], env["shown"], env["asked"]
    fund(env, "1000")
    screen = make_screen(db)
    try:
        assert screen.windowTitle().endswith("حركات الخزائن والبنوك") and screen.save_button.text() == "حفظ مسودة"
        assert screen.type_tabs["all"].isChecked() and "(0)" in plain(screen.type_tabs["deposit"].text())
        screen.new_record()                                         # «الكل» ← إيداع: خزنة ← بنك
        assert screen.mode == "new" and screen.from_edit.currentData() == treasury(db)
        assert screen.to_kind.currentData() == "bank" and screen.to_edit.currentData() == bank(db)
        assert screen.type_tabs["deposit"].isChecked() and not screen.type_tabs["all"].isEnabled()
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-10", "yyyy-MM-dd"))
        screen.amount_edit.setText("1,500")
        screen.description.setText("إيداع")
        screen._refresh_side()
        assert "1,500.00" in screen.kpi_values["amount"].text()
        assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
        assert screen.entry_table.item(0, 0).text().startswith("110201")
        assert not screen.funds_label.isHidden()                    # الخزنة فيها 1,000 بس
        screen.type_tabs["t2t"].click()                             # التبويب وانت بتكتب ← بين خزنتين
        assert (screen.from_kind.currentData(), screen.to_kind.currentData()) == ("treasury", "treasury")
        assert screen.to_edit.currentData() == treasury(db, "TRS-002")
        assert "بين خزنتين" in screen.type_label.text()
        screen.amount_edit.setText("400")
        screen.save()
        assert shown[-1] == "saved" and screen.current_record["status"] == "draft"
        assert screen.current_record["transfer_type"] == "t2t"
        assert screen.post_button.isEnabled() and screen.delete_button.isEnabled()
        screen.post_transfer()
        assert screen.current_record["transfer_no"] == "CT-2026-00001" and "CT-2026-00001" in shown[-1]
        assert not screen.edit_button.isEnabled() and screen.unpost_button.isEnabled()
        screen._refresh_side()
        assert screen.entry_box.title() == "القيد المرحّل" and screen.entry_table.rowCount() == 2
        assert screen.table.rows[0]["transfer_no"] == "CT-2026-00001"
        assert "(1)" in plain(screen.type_tabs["t2t"].text())
        screen.unpost_transfer()
        assert screen.current_record["status"] == "draft" and not screen.delete_button.isEnabled()
        screen.edit_record()
        screen.to_kind.setCurrentIndex(1)                           # «إلى» بنك ← إيداع
        assert screen.to_edit.currentData() == bank(db) and screen.type_tabs["deposit"].isChecked()
        screen.fees_edit.setText("10")
        asked.clear()
        screen.post_transfer()                                      # وانت بتعدّل = حفظ + ترحيل
        assert screen.mode == "view" and screen.current_record["transfer_type"] == "deposit"
        assert screen.current_record["fees"] == Decimal("10.00")
        # تبويب «سحب» بيفلتر القائمة (مفيش سحب)
        screen.type_tabs["withdraw"].click()
        assert screen.list_type == "withdraw" and screen.table.rows == []
        screen.type_tabs["all"].click()
        assert len(screen.table.rows) == 1
    finally:
        screen.close()


def test_menu_and_registry():
    from app.services import permission_registry as reg
    from app.ui.screen_registry import SCREEN_FACTORIES

    t = reg.find_target("cash", "cash_transfers")
    assert t.name_ar == "حركات الخزائن والبنوك" and "cash_transfers" in SCREEN_FACTORIES
    assert set(t.action_codes()) == {"view", "create", "edit", "save", "delete", "export", "post", "unpost"}
