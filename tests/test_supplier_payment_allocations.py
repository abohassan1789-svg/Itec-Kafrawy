"""اختبارات «سداد الموردين» (0050 + voucher_service + شاشة سند الصرف): السند بعملة وخزنة / بنك بنفس العملة ·
التوزيع على فواتير الشراء بالتوتال (الإجمالي − المرتجع الآجل − المسدد المرحّل) · كل فاتورة بعملتها · «وزّع الأقدم
الأول» · الدفعة المقدمة · فرق العملة مع السند · الفاتورة متعددة العملات · جهازين بيدفعوا نفس الفاتورة · حماية
القاعدة · الشاشة ونافذة «📋 الفواتير»."""

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
from app.services.errors import BusinessError
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.purchase_invoice_service import PurchaseInvoiceService
from app.services.purchase_return_service import PurchaseReturnService
from app.services.trial_balance_service import TrialBalanceService
from app.services.voucher_service import VoucherService
from app.ui.common import messages

D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 5), dt.date(2026, 3, 20)
D_ = Decimal


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    db = temp_db
    db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
               "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    AuthService(db).seed_admin()
    PermissionsSyncService(db).sync()
    SESSION.clear()
    assert AuthService(db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    shown: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: asked.append(text) or True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    group = db.fetch_value("SELECT group_id FROM party.suppliers WHERE seq = 0")
    db.execute("UPDATE core.currencies SET is_active = true WHERE code IN ('USD', 'EUR')")
    db.execute("INSERT INTO core.exchange_rates (currency_id, rate_date, rate) "
               "SELECT id, '2026-01-01', CASE code WHEN 'USD' THEN 48.5 ELSE 52 END FROM core.currencies "
               "WHERE code IN ('USD', 'EUR')")
    db.execute("UPDATE cash.treasuries SET currency_code = 'USD' WHERE code = 'TRS-002'")   # خزنة بالدولار
    env = {"db": db, "shown": shown, "asked": asked, "inv": PurchaseInvoiceService(db),
           "ret": PurchaseReturnService(db), "pv": VoucherService("payment", db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "لمبة LED"),
           "vat14": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT14'"),
           "exempt": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'EXEMPT'"),
           "egp_box": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-001'"),
           "usd_box": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-002'"),
           "bank": db.fetch_value("SELECT id FROM cash.bank_accounts WHERE code = 'BNK-001-01'"),
           "sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code, payment_terms_days) "
                                 "VALUES (1, 'شركة النور', %s, 'EGP', 30) RETURNING id", [group]),
           "sup2": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code) "
                                  "VALUES (2, 'المصرية', %s, 'EGP') RETURNING id", [group]),
           "usd_sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code, "
                                     "vat_tax_code_id) VALUES (3, 'Global Controls', %s, 'USD', "
                                     "(SELECT id FROM acc.tax_codes WHERE code = 'EXEMPT')) RETURNING id", [group])}
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14')) "
        "RETURNING id", [name])


def buy(env, lines, sup=None, d=D1, due=None, post=True, **kw):
    """فاتورة شراء (مرحّلة) ← (id، سطورها). ``lines`` = [(الصنف، الكمية، السعر)] أو dict جاهز."""
    rows = [ln if isinstance(ln, dict) else
            {"item_id": ln[0], "qty": ln[1], "unit_price": ln[2], "tax_code_id": kw.get("tax", env["vat14"])}
            for ln in lines]
    kw.pop("tax", None)
    payload = {"invoice_date": d, "supplier_id": sup or env["sup"], "warehouse_id": env["main"],
               "currency_code": kw.pop("currency_code", "EGP"), "due_date": due or d + dt.timedelta(days=30),
               "lines": rows, **kw}
    if payload.get("pay_kind") == "cash":
        payload.pop("due_date")
    iid = env["inv"].save_draft(None, payload, post=post)
    return iid, env["inv"].get_invoice(iid)["lines"]


def pay(env, amount, allocations=(), sup=None, d=D3, **kw):
    """بيانات سند صرف."""
    out = {"voucher_date": d, "party_id": sup or env["sup"], "source_kind": "treasury",
           "treasury_id": env["egp_box"], "bank_account_id": None, "amount": amount,
           "description": "سداد فواتير", "currency_code": "EGP",
           "allocations": [x if isinstance(x, dict) else {"purchase_invoice_id": x[0], "amount": x[1]}
                           for x in allocations]}
    out.update(kw)
    return out


def entry(db, vid):
    return db.fetch_all(
        "SELECT a.code, l.debit, l.credit, l.supplier_id, l.currency_code, l.fx_rate, l.amount_fc "
        "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = 'supplier_payment' AND e.source_id = %s "
        "AND e.status = 'posted' ORDER BY l.line_no", [vid])


def due(env, iid, currency="EGP"):
    return env["db"].fetch_one("SELECT * FROM purch.purchase_invoice_dues WHERE invoice_id = %s AND currency_code = %s",
                               [iid, currency])


def ledger_balance(db, sup):
    """رصيد المورد من دفتر الأستاذ (دائن − مدين بالجنيه — كل العملات) = اللي بيطلع في كشف الحساب."""
    return db.fetch_value(
        "SELECT coalesce(sum(l.credit - l.debit), 0) FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.supplier_id = %s", [sup])


def payable_code(db, sup):
    return db.fetch_value("SELECT a.code FROM party.supplier_groups g JOIN acc.accounts a ON a.id = g.control_account_id "
                          "JOIN party.suppliers s ON s.group_id = g.id WHERE s.id = %s", [sup])


# ---- السكريبت ------------------------------------------------------------------
def test_migration(env):
    db = env["db"]
    cols = {r["column_name"] for r in db.fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'cash' "
        "AND table_name = 'supplier_payments'")}
    assert {"currency_code", "fx_rate", "amount_base"} <= cols
    # السند من غير عملة = العملة الأساسية وسعرها 1 والمبلغ بالجنيه بيتحسب لوحده
    vid = db.fetch_value("INSERT INTO cash.supplier_payments (voucher_date, supplier_id, source_kind, treasury_id, "
                         "amount, description) VALUES (%s, %s, 'treasury', %s, 250, 'x') RETURNING id",
                         [D3, env["sup"], env["egp_box"]])
    row = db.fetch_one("SELECT currency_code, fx_rate, amount_base FROM cash.supplier_payments WHERE id = %s", [vid])
    assert row == {"currency_code": "EGP", "fx_rate": D_("1"), "amount_base": D_("250.00")}
    with pytest.raises(psycopg.errors.RaiseException, match="سعرها 1"):
        db.execute("UPDATE cash.supplier_payments SET fx_rate = 2 WHERE id = %s", [vid])
    # عملة الخزنة لازم = عملة السند (الخزنة الدولار مع سند جنيه · والعكس)
    with pytest.raises(psycopg.errors.RaiseException, match="بنفس عملة السند"):
        db.execute("UPDATE cash.supplier_payments SET treasury_id = %s WHERE id = %s", [env["usd_box"], vid])
    with pytest.raises(psycopg.errors.RaiseException, match="بنفس عملة السند"):
        db.execute("UPDATE cash.supplier_payments SET currency_code = 'USD', fx_rate = 48 WHERE id = %s", [vid])
    db.execute("UPDATE cash.supplier_payments SET currency_code = 'USD', fx_rate = 48, treasury_id = %s WHERE id = %s",
               [env["usd_box"], vid])
    assert db.fetch_value("SELECT amount_base FROM cash.supplier_payments WHERE id = %s", [vid]) == D_("12000.00")
    # index على كل FK في جدول التوزيع (أول عمود في الـ unique = السند)
    missing = db.fetch_all("""
        SELECT a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.conrelid = 'cash.voucher_allocations'::regclass
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])""")
    assert missing == []
    assert db.fetch_value("SELECT count(*) FROM pg_indexes WHERE schemaname = 'cash' "
                          "AND indexname = 'supplier_payments_currency_idx'") == 1


# ---- التوزيع بالجنيه --------------------------------------------------------------
def test_allocate_post_unpost_and_dues(env):
    db, pv = env["db"], env["pv"]
    i1, l1 = buy(env, [(env["item"], "10", "100")], d=D1)                    # 1,140 — استحقاق 31/3
    i2, _l = buy(env, [(env["item2"], "5", "200")], d=D2)                    # 1,140 — استحقاق 4/4
    icash, _c = buy(env, [(env["item"], "1", "100")], pay_kind="cash", source_kind="treasury",
                    treasury_id=env["egp_box"])                             # نقدي ← مابيظهرش
    idraft, _d = buy(env, [(env["item"], "1", "100")], post=False)
    iother, _o = buy(env, [(env["item"], "1", "100")], sup=env["sup2"])
    # مرتجع آجل مربوط بالفاتورة الأولى (2 × 100 + 14%) ← بينقص من التوتال
    env["ret"].save_draft(None, {"return_date": D2, "supplier_id": env["sup"], "warehouse_id": env["main"],
                                 "currency_code": "EGP", "lines": [{"source_line_id": l1[0]["id"], "qty": "2"}]},
                          post=True)
    d1 = due(env, i1)
    assert (d1["total_amount"], d1["returned_amount"], d1["paid_amount"], d1["remaining_amount"]) == (
        D_("1140.00"), D_("228.00"), D_("0"), D_("912.00"))
    opened = pv.open_invoices(env["sup"], "EGP")
    assert [r["invoice_id"] for r in opened] == [i1, i2]                       # الآجل المرحّل بتاع المورد بس
    assert pv.count_open_invoices(env["sup"], "EGP") == {"total": 2, "remaining": D_("2052.00")}
    assert pv.open_invoices(env["sup"], "USD") == []                           # مفيش فواتير بالدولار
    assert [r["invoice_id"] for r in pv.open_invoices(env["sup"], "EGP", keyword=opened[1]["invoice_no"])] == [i2]

    # «وزّع الأقدم الأول»: 1,500 ← الأولى كلها (912) والباقي على التانية
    auto = pv.auto_allocate(env["sup"], "EGP", "1500")
    assert auto == [{"purchase_invoice_id": i1, "amount": D_("912.00")},
                    {"purchase_invoice_id": i2, "amount": D_("588.00")}]
    kept = pv.auto_allocate(env["sup"], "EGP", "1500", keep=[{"purchase_invoice_id": i2, "amount": "1000"}])
    assert kept == [{"purchase_invoice_id": i2, "amount": D_("1000")},
                    {"purchase_invoice_id": i1, "amount": D_("500.00")}]

    # الأخطاء: أكتر من المتبقي · أكتر من السند · مورد تاني · نقدي · مسودة · متكررة
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        pv.save_draft(None, pay(env, "2000", [(i1, "913")]))
    with pytest.raises(BusinessError, match="أكتر من مبلغ السند"):
        pv.save_draft(None, pay(env, "1000", [(i1, "912"), (i2, "100")]))
    with pytest.raises(BusinessError, match="لمورد تاني"):
        pv.save_draft(None, pay(env, "1000", [(iother, "100")]))
    with pytest.raises(BusinessError, match="نقدية"):
        pv.save_draft(None, pay(env, "1000", [(icash, "100")]))
    with pytest.raises(BusinessError, match="مش مرحّلة"):
        pv.save_draft(None, pay(env, "1000", [(idraft, "100")]))
    with pytest.raises(BusinessError, match="متكررة"):
        pv.save_draft(None, pay(env, "1000", [(i1, "100"), (i1, "50")]))
    assert pv.preview(pay(env, "1500", auto))[0]["debit"] == D_("1500.00")
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        pv.preview(pay(env, "9000", [(i1, "5000")]))

    # المسودة مابتخصمش من المتبقي
    vid = pv.save_draft(None, pay(env, "1500", auto))
    v = pv.get_voucher(vid)
    assert v["allocated"] == D_("1500.00") and v["unallocated"] == D_("0.00") and len(v["allocations"]) == 2
    assert v["allocations"][0]["remaining_amount"] == D_("912.00")
    assert due(env, i1)["remaining_amount"] == D_("912.00")
    # تعديل المسودة: مبلغ أكبر ← الباقي دفعة مقدمة
    pv.save_draft(vid, pay(env, "2000", [(i1, "912"), (i2, "588")]), v["row_version"])
    assert pv.post(vid) == "PV-2026-00001"
    v = pv.get_voucher(vid)
    assert v["unallocated"] == D_("500.00") and v["currency_code"] == "EGP" and v["amount_base"] == D_("2000.00")
    assert v["allocations"][0]["remaining_amount"] == D_("912.00")            # المتبقي «قبل» السند ده
    assert due(env, i1)["remaining_amount"] == D_("0.00") and due(env, i2)["remaining_amount"] == D_("552.00")
    assert [r["invoice_id"] for r in pv.open_invoices(env["sup"], "EGP")] == [i2]
    assert pv.repository.unallocated_credit(env["sup"], "EGP") == D_("500.00")
    # القيد واحد: مدين المورد بالمبلغ كله / دائن الخزنة (التوزيع مابيغيّرهوش بالجنيه)
    lines = entry(db, vid)
    assert [(ln["code"], ln["debit"], ln["credit"]) for ln in lines] == [
        (payable_code(db, env["sup"]), D_("2000.00"), D_("0.00")), ("110101", D_("0.00"), D_("2000.00"))]
    assert lines[0]["supplier_id"] == env["sup"] and lines[0]["currency_code"] is None
    # رصيد المورد بالجنيه: 1,140 + 1,140 − 228 − 2,000 = 52 (ليه علينا)
    assert pv.balances(env["sup"], "treasury", env["egp_box"], "0", posted=True)["party"] == D_("52.00")

    # المرحّل: توزيعه مايتغيّرش في القاعدة · والفاتورة مايتلغيش ترحيلها وعليها سداد مرحّل
    with pytest.raises(psycopg.errors.RaiseException, match="توزيعه مايتغيّرش"):
        db.execute("DELETE FROM cash.voucher_allocations WHERE supplier_payment_id = %s", [vid])
    with pytest.raises(BusinessError, match="عليها سداد مرحّل"):
        env["inv"].unpost(i2)
    # سند تاني بيحاول يدفع أكتر من اللي فاضل على التانية
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        pv.save_draft(None, pay(env, "600", [(i2, "553")]), post=True)
    # إلغاء الترحيل: القيد بيتمسح والتوزيع بيبطّل يخصم
    pv.unpost(vid)
    assert entry(db, vid) == [] and due(env, i1)["remaining_amount"] == D_("912.00")
    assert pv.get_voucher(vid)["allocations"][0]["amount"] == D_("912.00")       # التوزيع فضل في المسودة
    env["inv"].unpost(i2)                                                    # الفاتورة اتلغى ترحيلها والسند مسودة
    with pytest.raises(BusinessError, match="مش مرحّلة"):
        pv.post(vid)
    env["inv"].post(i2)
    pv.post(vid)
    assert TrialBalanceService(db).trial_balance(dt.date(2026, 1, 1), dt.date(2026, 12, 31))["balanced"]


def test_database_guards(env):
    """القاعدة نفسها بترفض حتى لو الـ Service اتخطّى."""
    db, pv = env["db"], env["pv"]
    i1, _l = buy(env, [(env["item"], "10", "100")])                           # 1,140
    iother, _o = buy(env, [(env["item"], "1", "100")], sup=env["sup2"])
    vid = pv.save_draft(None, pay(env, "500"))

    def alloc(invoice, amount, base=None):
        db.execute("INSERT INTO cash.voucher_allocations (supplier_payment_id, purchase_invoice_id, currency_code, "
                   "amount, amount_base) VALUES (%s, %s, 'USD', %s, %s)", [vid, invoice, amount, base or amount])

    with pytest.raises(psycopg.errors.RaiseException, match="لمورد تاني"):
        alloc(iother, 100)
    with pytest.raises(psycopg.errors.RaiseException, match="أكتر من مبلغ السند"):
        alloc(i1, 600)
    alloc(i1, 300)
    assert db.fetch_value("SELECT currency_code FROM cash.voucher_allocations WHERE supplier_payment_id = %s",
                          [vid]) == "EGP"                                    # العملة = عملة السند دايماً
    with pytest.raises(psycopg.errors.RaiseException, match="شيل التوزيع الأول عشان تغيّر المورد"):
        db.execute("UPDATE cash.supplier_payments SET supplier_id = %s WHERE id = %s", [env["sup2"], vid])
    with pytest.raises(psycopg.errors.RaiseException, match="شيل التوزيع الأول عشان تغيّر العملة"):
        db.execute("UPDATE cash.supplier_payments SET currency_code = 'USD', fx_rate = 48, treasury_id = %s "
                   "WHERE id = %s", [env["usd_box"], vid])
    with pytest.raises(psycopg.errors.RaiseException, match="من غير قيد"):
        db.execute("UPDATE cash.supplier_payments SET status = 'posted' WHERE id = %s", [vid])
    # المسودة بتتمسح بتوزيعها (CASCADE)
    pv.delete_voucher(vid)
    assert db.fetch_value("SELECT count(*) FROM cash.voucher_allocations") == 0


# ---- العملات وفرق العملة -----------------------------------------------------------
def test_foreign_currency_and_fx_difference(env):
    db, pv = env["db"], env["pv"]
    sup = env["usd_sup"]
    iid, _l = buy(env, [(env["item"], "10", "100")], sup=sup, currency_code="USD", fx_rate="48",
                  tax=env["exempt"])                                          # 1,000 دولار × 48 = 48,000
    assert due(env, iid, "USD")["total_amount"] == D_("1000.00") and due(env, iid, "USD")["total_base"] == D_("48000.00")
    assert due(env, iid) is None                                             # مفيش جزء بالجنيه
    assert pv.rate_for("USD", D3) == D_("48.5") and pv.rate_for("EGP", D3) == D_("1")
    # سند بالجنيه مايسددش فاتورة دولار · وخزنة الجنيه ماتنفعش لسند دولار
    with pytest.raises(BusinessError, match="مافيهاش مبلغ بعملة EGP"):
        pv.save_draft(None, pay(env, "1000", [(iid, "100")], sup=sup))
    with pytest.raises(BusinessError, match="بنفس عملة السند"):
        pv.save_draft(None, pay(env, "400", [(iid, "400")], sup=sup, currency_code="USD", fx_rate="49"))
    usd = dict(sup=sup, currency_code="USD", treasury_id=env["usd_box"])
    # 500 دولار بسعر 49: 400 على الفاتورة (بسعرها 48) + 100 دفعة مقدمة (بسعر السند) ← خسارة 400
    v1 = pv.save_draft(None, pay(env, "500", [(iid, "400")], fx_rate="49", **usd), post=True)
    rec = pv.get_voucher(v1)
    assert rec["fx_rate"] == D_("49") and rec["amount_base"] == D_("24500.00") and rec["unallocated"] == D_("100.00")
    assert rec["allocations"][0]["invoice_rate"] == D_("48") and rec["allocations"][0]["amount_base"] == D_("19200.00")
    payable = payable_code(db, sup)
    assert [(ln["code"], ln["debit"], ln["credit"], ln["currency_code"], ln["fx_rate"], ln["amount_fc"])
            for ln in entry(db, v1)] == [
        (payable, D_("19200.00"), D_("0.00"), "USD", D_("48"), D_("400.00")),
        (payable, D_("4900.00"), D_("0.00"), "USD", D_("49"), D_("100.00")),
        ("110102", D_("0.00"), D_("24500.00"), "USD", D_("49"), D_("500.00")),
        ("5401", D_("400.00"), D_("0.00"), None, None, None)]
    assert due(env, iid, "USD")["remaining_amount"] == D_("600.00")
    # الأرصدة **بالجنيه** زي كشف الحساب: المورد 48,000 − 19,200 − 4,900 = 23,900 · الخزنة طلع منها 24,500 —
    # ومعاهم للعلم بالدولار: المورد 1,000 − 500 = 500 · الخزنة −500
    b = pv.balances(sup, "treasury", env["usd_box"], "0", posted=True, currency_code="USD", fx_rate="49")
    assert b["party"] == D_("23900.00") and b["source"] == D_("-24500.00") and b["currency_code"] == "USD"
    assert b["party_fc"] == D_("500.00") and b["source_fc"] == D_("-500.00")
    assert b["party"] == ledger_balance(db, sup)                              # مطابق لدفتر الأستاذ
    # قبل الترحيل: «بعد السند» = اللي هيتقفل من المورد بالجنيه (من القيد) · والخزنة بالمبلغ × سعر السند
    b = pv.balances(sup, "treasury", env["usd_box"], "100", currency_code="USD", fx_rate="50",
                    party_effect=D_("4800"))
    assert b["amount_base"] == D_("5000.00") and b["party_after"] == D_("19100.00")
    assert b["source_after"] == D_("-29500.00") and b["party_fc_after"] == D_("400.00")
    assert "الخزنة" in pv.funds_warning(pay(env, "10", fx_rate="49", **usd))
    # آخر دفعة (600 بسعر 47 — من غير ما أكتب السعر ← 48.5 من الجدول؛ هنا مكتوب 47): بتقفل الفاتورة بالظبط ← مكسب 600
    v2 = pv.save_draft(None, pay(env, "600", [(iid, "600")], fx_rate="47", **usd), post=True)
    assert [(ln["code"], ln["debit"], ln["credit"]) for ln in entry(db, v2)] == [
        (payable, D_("28800.00"), D_("0.00")), ("110102", D_("0.00"), D_("28200.00")),
        ("4301", D_("0.00"), D_("600.00"))]
    assert due(env, iid, "USD")["remaining_amount"] == D_("0.00") and due(env, iid, "USD")["remaining_base"] == D_("0.00")
    # السعر من جدول الأسعار لو مااتكتبش
    v3 = pv.save_draft(None, pay(env, "10", fx_rate="", **usd))
    assert pv.get_voucher(v3)["fx_rate"] == D_("48.5")
    db.execute("DELETE FROM core.exchange_rates")
    with pytest.raises(BusinessError, match="مفيش سعر متسجل"):
        pv.save_draft(None, pay(env, "10", fx_rate="", **usd))
    assert TrialBalanceService(db).trial_balance(dt.date(2026, 1, 1), dt.date(2026, 12, 31))["balanced"]


def test_multi_currency_invoice_part_per_currency(env):
    pv = env["pv"]
    sup = env["usd_sup"]
    lines = [{"item_id": env["item"], "qty": "2", "unit_price": "100", "tax_code_id": env["exempt"],
              "currency_code": "USD", "fx_rate": "50"},
             {"item_id": env["item2"], "qty": "1", "unit_price": "300", "tax_code_id": env["exempt"],
              "currency_code": "EGP", "fx_rate": "1"}]
    iid, _l = buy(env, lines, sup=sup, currency_mode="multi", currency_code="USD")
    assert due(env, iid, "USD")["total_amount"] == D_("200.00") and due(env, iid, "EGP")["total_amount"] == D_("300.00")
    # الفاتورة بتظهر في سند كل عملة بجزئها بس
    assert [(r["invoice_id"], r["remaining_amount"]) for r in pv.open_invoices(sup, "USD")] == [(iid, D_("200.00"))]
    assert [(r["invoice_id"], r["remaining_amount"]) for r in pv.open_invoices(sup, "EGP")] == [(iid, D_("300.00"))]
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        pv.save_draft(None, pay(env, "300", [(iid, "250")], sup=sup, currency_code="USD", treasury_id=env["usd_box"],
                                fx_rate="50"))
    pv.save_draft(None, pay(env, "300", [(iid, "300")], sup=sup), post=True)                       # الجزء الجنيه
    pv.save_draft(None, pay(env, "200", [(iid, "200")], sup=sup, currency_code="USD", treasury_id=env["usd_box"],
                            fx_rate="50"), post=True)                                              # الجزء الدولار
    assert due(env, iid, "USD")["remaining_amount"] == 0 and due(env, iid, "EGP")["remaining_amount"] == 0
    assert pv.open_invoices(sup, "USD") == [] and pv.open_invoices(sup, "EGP") == []


def test_bank_like_treasury(env):
    """الدفع من بنك زي الخزنة بالظبط — وعملة الحساب البنكي لازم = عملة السند."""
    db, pv = env["db"], env["pv"]
    i1, _l = buy(env, [(env["item"], "10", "100")])
    vid = pv.save_draft(None, pay(env, "1140", [(i1, "1140")], source_kind="bank", treasury_id=None,
                                  bank_account_id=env["bank"]), post=True)
    assert [ln["code"] for ln in entry(db, vid)] == [payable_code(db, env["sup"]), "110201"]
    assert due(env, i1)["remaining_amount"] == 0
    with pytest.raises(BusinessError, match="الحساب البنكي .* بعملة EGP"):
        pv.save_draft(None, pay(env, "10", sup=env["usd_sup"], currency_code="USD", fx_rate="48",
                                source_kind="bank", treasury_id=None, bank_account_id=env["bank"]))


# ---- جهازين ---------------------------------------------------------------------
def test_two_devices_pay_same_invoice(env):
    db = env["db"]
    s1, s2 = env["pv"], VoucherService("payment", Database(db.settings))
    try:
        i1, _l = buy(env, [(env["item"], "10", "100")])                       # 1,140
        a = s1.save_draft(None, pay(env, "800", [(i1, "800")]))
        b = s2.save_draft(None, pay(env, "800", [(i1, "800")]))               # المسودات مابتخصمش ← الاتنين اتحفظوا
        s1.post(a)
        with pytest.raises(BusinessError, match="أكتر من المتبقي"):
            s2.post(b)
        assert s2.get_voucher(b)["status"] == "draft" and due(env, i1)["remaining_amount"] == D_("340.00")
        # في نفس اللحظة: التاني بيستنى قفل الفاتورة وبعدين بيشوف سداد الأول ← بيترفض
        i2, _l2 = buy(env, [(env["item"], "10", "100")])
        c = s1.save_draft(None, pay(env, "1000", [(i2, "1000")]))
        d = s2.save_draft(None, pay(env, "1000", [(i2, "1000")]))
        results: dict[str, object] = {}

        def worker():
            try:
                results["no"] = s2.post(d)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            tx.execute("SELECT id FROM purch.purchase_invoices WHERE id = %s FOR UPDATE", [i2])   # الجهاز الأول ماسكها
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                                          # التاني مستني
            s1_tx = VoucherService("payment", db)
            before = s1_tx._lock(tx, c, None, draft_only=True)
            s1_tx._post(tx, c, before)
        thread.join(10)
        assert isinstance(results.get("error"), BusinessError) and "أكتر من المتبقي" in str(results["error"])
        assert due(env, i2)["paid_amount"] == D_("1000.00")
        assert db.fetch_value("SELECT count(*) FROM cash.supplier_payments WHERE status = 'posted'") == 2
    finally:
        s2.db.close()


def test_database_blocks_overpay_even_without_service_check(env):
    """آخر خط دفاع: Constraint Trigger بيحسب المسدد على الفاتورة آخر الـ transaction."""
    db, pv = env["db"], env["pv"]
    i1, _l = buy(env, [(env["item"], "10", "100")])                           # 1,140
    pv.save_draft(None, pay(env, "1000", [(i1, "1000")]), post=True)
    vid = pv.save_draft(None, pay(env, "500"))
    db.execute("INSERT INTO cash.voucher_allocations (supplier_payment_id, purchase_invoice_id, currency_code, amount, "
               "amount_base) VALUES (%s, %s, 'EGP', 500, 500)", [vid, i1])    # مسودة ← مسموح
    real = pv._allocation_rows
    pv._allocation_rows = lambda v, ex=None: [  # type: ignore[method-assign]
        {"purchase_invoice_id": i1, "currency_code": "EGP", "amount": D_("500"), "invoice_rate": D_("1"),
         "amount_base": D_("500"), "invoice_no": "x"}]
    try:
        with pytest.raises((BusinessError, psycopg.errors.RaiseException), match="أكتر من المستحق"):
            pv.post(vid)
    finally:
        pv._allocation_rows = real  # type: ignore[method-assign]
    assert pv.get_voucher(vid)["status"] == "draft"


# ---- الشاشة ---------------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.vouchers_screen import PaymentsScreen

    screen = PaymentsScreen(VoucherService("payment", db))
    screen.from_edit.setDate(screen.from_edit.date().addYears(-5))
    screen.to_edit.setDate(screen.to_edit.date().addYears(5))
    return screen


def plain(text):
    return text.replace("⁦", "").replace("⁩", "")


def test_screen_currency_lookups_and_allocation(env):
    from app.ui.screens.vouchers_screen import A_LEFT, A_NO, A_PAY, InvoiceDuesDialog

    db, shown = env["db"], env["shown"]
    i1, _l = buy(env, [(env["item"], "10", "100")], d=D1)                    # 1,140
    i2, _l2 = buy(env, [(env["item2"], "5", "200")], d=D2)                   # 1,140
    iusd, _l3 = buy(env, [(env["item"], "1", "100")], sup=env["usd_sup"], currency_code="USD", fx_rate="48",
                    tax=env["exempt"])
    screen = make_screen(db)
    try:
        # أيقونة البحث جنب العملة والمورد والخزنة · تبويبين: التوزيع + القيد
        assert screen.currency_find.text() == screen.party_find.text() == screen.source_find.text() == "🔍"
        assert [screen.tabs.tabText(i) for i in range(2)] == ["🧾 التوزيع على الفواتير", "📒 القيد"]
        screen.new_record()
        assert screen.mode == "new" and screen.currency_edit.currentData() == "EGP"
        assert not screen.rate_edit.isVisibleTo(screen) or screen.rate_edit.text() == "1"
        assert screen.source_edit.currentData() == env["egp_box"]            # خزن الجنيه بس
        assert [r[0] for r in screen._source_rows("treasury")] == [env["egp_box"]]
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-20", "yyyy-MM-dd"))
        # نافذة البحث: المورد
        screen._pick_from = lambda _t, _c, rows: next(r["id"] for r in rows if r["id"] == env["sup"])
        screen._find_party()
        assert screen.party_edit.currentData() == env["sup"]
        screen.amount_edit.setText("1500")
        screen.description.setText("سداد فواتير النور")
        screen.auto_allocate()
        assert screen.alloc_table.rowCount() == 2 and "(2)" in screen.tabs.tabText(0)
        assert screen.alloc_table.item(0, A_LEFT).text() == "1,140.00" and screen.alloc_table.item(1, A_PAY).text() == "360.00"
        assert "اتوزّع كله" in plain(screen.alloc_summary.text())
        # تعديل «يدفع» في السطر ← الباقي دفعة مقدمة · أكتر من المتبقي ← أحمر والقيد مايطلعش
        screen.alloc_table.item(1, A_PAY).setText("100")
        assert screen._allocs[1]["amount"] == D_("100") and "دفعة مقدمة" in plain(screen.alloc_summary.text())
        screen.alloc_table.item(1, A_PAY).setText("5000")
        assert "أكتر" in plain(screen.alloc_summary.text())
        screen._refresh_side()
        assert screen.entry_label.text().startswith("✖")
        screen.alloc_table.item(1, A_PAY).setText("0")                        # صفر = شيل الفاتورة
        assert screen.alloc_table.rowCount() == 1
        # نافذة «📋 الفواتير»: بالتوتال · ✔ = المتبقي (أو اللي فاضل من السند) · أكتر من المتبقي ← رسالة
        dialog = InvoiceDuesDialog(screen.service, screen, supplier_id=env["sup"], supplier_name="النور",
                                   currency_code="EGP", amount=D_("1500"), picks={i1: D_("1140")})
        try:
            assert dialog.table.rowCount() == 2 and dialog.table.item(0, dialog.C_LEFT).text() == "1,140.00"
            assert dialog.table.item(0, dialog.C_CHECK).checkState().name == "Checked"
            dialog.table.item(1, dialog.C_CHECK).setCheckState(dialog.table.item(1, dialog.C_CHECK).checkState().Checked)
            assert dialog.picks == {i1: D_("1140"), i2: D_("360.00")}          # اللي فاضل من مبلغ السند
            dialog.table.item(1, dialog.C_PAY).setText("9999")
            assert "المتبقي عليها" in dialog.summary.text() and dialog.picks[i2] == D_("360.00")
            dialog.clear()
            dialog.auto_allocate()
            assert dialog.picks == {i1: D_("1140.00"), i2: D_("360.00")}
            dialog.apply()
            assert dialog.result == [{"purchase_invoice_id": i1, "amount": D_("1140.00")},
                                     {"purchase_invoice_id": i2, "amount": D_("360.00")}]
        finally:
            dialog.close()
        screen._set_allocations(dialog.result)
        assert screen.alloc_table.item(0, A_NO).text().startswith("PUR-2026-")
        screen._refresh_side()
        assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
        screen.post_voucher()
        rec = screen.current_record
        assert rec["voucher_no"] == "PV-2026-00001" and rec["allocated"] == D_("1500.00")
        assert screen.alloc_table.rowCount() == 2 and not screen.alloc_pick_button.isEnabled()
        assert due(env, i1)["remaining_amount"] == 0 and due(env, i2)["remaining_amount"] == D_("780.00")

        # سند بالدولار: العملة من المورد · السعر من الجدول · «صرف من» خزنة الدولار بس · البنك مالوش حساب دولار
        screen.new_record()
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-20", "yyyy-MM-dd"))
        screen.party_edit.set_value(env["usd_sup"])
        assert screen.currency_edit.currentData() == "USD" and screen.rate_edit.text() == "48.5"
        assert screen.source_edit.currentData() == env["usd_box"] and screen._source_rows("bank") == []
        screen.rate_edit.setText("49")
        screen._on_rate_edited()
        screen.amount_edit.setText("100")
        screen.description.setText("سداد دولار")
        picked = {}
        screen._pick_from = lambda title, _c, rows: picked.update(title=title, ids=[r["id"] for r in rows]) or rows[0]["id"]
        screen._find_source()
        assert "USD" in picked["title"] and picked["ids"] == [f"treasury:{env['usd_box']}"]
        screen._find_currency()
        assert set(picked["ids"]) >= {"EGP", "USD"}
        assert screen.currency_edit.currentData() == picked["ids"][0]
        screen.currency_edit.set_value("USD")
        screen.rate_edit.setText("49")
        screen._on_rate_edited()
        screen.auto_allocate()
        assert [a["purchase_invoice_id"] for a in screen._allocs] == [iusd]
        screen._refresh_side()
        # الكروت بالجنيه (زي كشف الحساب) — والعملة سطر صغير للعلم
        assert plain(screen.kpi_values["amount"].text()) == "4,900.00" and "بالجنيه" in screen.kpi_captions["party"].text()
        assert "100.00 USD × 49" in plain(screen.kpi_notes["amount"].text())
        assert plain(screen.kpi_values["party"].text()) == "4,800.00 دائن  ←  0.00"     # 100 دولار بسعر الفاتورة 48
        assert "USD" in screen.kpi_notes["party"].text() and not screen.kpi_notes["party"].isHidden()
        assert screen.entry_table.rowCount() == 3                             # المورد · الخزنة · فروق العملة
        # تغيير العملة بيصفّر التوزيع
        screen.currency_edit.set_value("EGP")
        assert screen._allocs == [] and "اتصفّر" in screen.alloc_summary.text()
        assert screen.source_edit.currentData() == env["egp_box"]
        screen.currency_edit.set_value("USD")
        screen.rate_edit.setText("49")
        screen._on_rate_edited()
        screen.auto_allocate()
        screen.save()
        assert shown[-1] == "saved"
        rec = screen.current_record
        assert rec["currency_code"] == "USD" and rec["fx_rate"] == D_("49") and rec["allocated"] == D_("100.00")
        assert "USD" in screen.format_cell("amount", rec["amount"], rec)
        screen.post_voucher()
        assert screen.current_record["voucher_no"] == "PV-2026-00002"
        assert [ln["code"] for ln in entry(db, screen.current_id)][-1] == "5401"       # 100 × (49 − 48)
    finally:
        screen.close()


def test_two_devices_edit_same_draft_allocations(env):
    """جهازين فاتحين نفس المسودة: الأول غيّر التوزيع بس (الرأس زي ما هو) ← التاني بنسخته القديمة بيترفض."""
    from app.services.errors import ConcurrentEditError

    pv = env["pv"]
    i1, _l = buy(env, [(env["item"], "10", "100")])
    vid = pv.save_draft(None, pay(env, "1000", [(i1, "400")]))
    seen = pv.get_voucher(vid)["row_version"]
    pv.save_draft(vid, pay(env, "1000", [(i1, "900")]), seen)
    with pytest.raises(ConcurrentEditError):
        pv.save_draft(vid, pay(env, "1000", [(i1, "100")]), seen)
    assert pv.get_voucher(vid)["allocations"][0]["amount"] == D_("900.00")


def test_account_statement_in_base_currency_with_fc_note(env):
    """كشف حساب المورد **بالجنيه بس** (مطابق لدفتر الأستاذ) — والسطر اللي أصله دولار عليه ملاحظة «… USD × السعر»."""
    from app.services.statement_service import StatementService

    db, pv = env["db"], env["pv"]
    sup = env["usd_sup"]
    buy(env, [(env["item2"], "5", "200")], sup=sup)                                        # 1,140 جنيه
    iid, _l = buy(env, [(env["item"], "10", "100")], sup=sup, currency_code="USD", fx_rate="55",
                  tax=env["exempt"], d=D2)                                                 # 1,000 دولار × 55
    pv.save_draft(None, pay(env, "400", [(iid, "400")], sup=sup, currency_code="USD", treasury_id=env["usd_box"],
                            fx_rate="55"), post=True)
    res = StatementService(db).statement("supplier", sup, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    rows = [r for r in res["rows"] if r["kind"] == "line"]
    assert [(r["debit"], r["credit"]) for r in rows] == [(0, D_("1140.00")), (0, D_("55000.00")), (D_("22000.00"), 0)]
    assert "USD" not in rows[0]["description"]                                             # سطر الجنيه من غير ملاحظة
    assert rows[1]["description"].endswith("(1,000.00 USD × 55)")
    assert rows[2]["description"].endswith("(400.00 USD × 55)")
    assert -res["end"] == ledger_balance(db, sup) == D_("34140.00")                        # 1,140 + 55,000 − 22,000
    # نفس الرقم في كارت «رصيد المورد» في سند الصرف
    assert pv.balances(sup, "treasury", env["usd_box"], "0", posted=True, currency_code="USD",
                       fx_rate="55")["party"] == D_("34140.00")
