"""اختبارات «تحصيل العملاء + الدفعات المقدمة» (0054 + voucher_service + شاشة سند القبض) — نفس فكرة سند الصرف (0050)
بالظبط للعميل: السند بعملة وخزنة / بنك بنفس العملة · التوزيع على فواتير البيع بالتوتال (الإجمالي − **المرتجع الآجل
المربوط** − المحصّل المرحّل) · كل فاتورة بعملتها · «وزّع الأقدم الأول» · الدفعة المقدمة · فرق العملة مع السند (مكسب
وخسارة بالعكس من الصرف) · الفاتورة متعددة العملات · **فاتورة البيع مايتلغيش ترحيلها وعليها تحصيل** · جهازين بيحصّلوا
نفس الفاتورة · حماية القاعدة · الشاشة ونافذة «📋 الفواتير» · كشف حساب العميل بالجنيه = كارت السند."""

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
from app.services.sales_invoice_service import SalesInvoiceService
from app.services.sales_return_service import SalesReturnService
from app.services.trial_balance_service import TrialBalanceService
from app.services.voucher_service import VoucherService
from app.ui.common import messages

D0, D1, D2, D3 = dt.date(2026, 2, 1), dt.date(2026, 3, 1), dt.date(2026, 3, 5), dt.date(2026, 3, 20)
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
    cgroup = db.fetch_value("SELECT id FROM party.customer_groups WHERE is_system ORDER BY id LIMIT 1")
    sgroup = db.fetch_value("SELECT group_id FROM party.suppliers WHERE seq = 0")
    db.execute("UPDATE core.currencies SET is_active = true WHERE code IN ('USD', 'EUR')")
    db.execute("INSERT INTO core.exchange_rates (currency_id, rate_date, rate) "
               "SELECT id, '2026-01-01', CASE code WHEN 'USD' THEN 48.5 ELSE 52 END FROM core.currencies "
               "WHERE code IN ('USD', 'EUR')")
    db.execute("UPDATE cash.treasuries SET currency_code = 'USD' WHERE code = 'TRS-002'")   # خزنة بالدولار
    env = {"db": db, "shown": shown, "asked": asked, "sales": SalesInvoiceService(db),
           "purchase": PurchaseInvoiceService(db), "ret": SalesReturnService(db), "rv": VoucherService("receipt", db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "item": item(db, "كابل نحاس", "150"), "item2": item(db, "لمبة LED", "40"),
           "vat14": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT14'"),
           "vat0": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT0'"),
           "egp_box": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-001'"),
           "usd_box": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-002'"),
           "bank": db.fetch_value("SELECT id FROM cash.bank_accounts WHERE code = 'BNK-001-01'"),
           "cust": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code, "
                                  "payment_terms_days) VALUES ('1', 'شركة الأمل', %s, 'EGP', 30) RETURNING id",
                                  [cgroup]),
           "cust2": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code) "
                                   "VALUES ('3', 'النيل للمقاولات', %s, 'EGP') RETURNING id", [cgroup]),
           "usd_cust": db.fetch_value("INSERT INTO party.customers (code, name_ar, group_id, currency_code, "
                                      "vat_tax_code_id) VALUES ('2', 'Gulf Trading', %s, 'USD', "
                                      "(SELECT id FROM acc.tax_codes WHERE code = 'VAT0')) RETURNING id", [cgroup]),
           "sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code) "
                                 "VALUES (1, 'شركة النور', %s, 'EGP') RETURNING id", [sgroup])}
    # رصيد مخزون كفاية للبيع (شراء أول المدة)
    for it in (env["item"], env["item2"]):
        env["purchase"].save_draft(None, {
            "invoice_date": D0, "supplier_id": env["sup"], "warehouse_id": env["main"], "currency_code": "EGP",
            "due_date": D0, "lines": [{"item_id": it, "qty": "500", "unit_price": "50"}]}, post=True)
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name, price):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, sale_price) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), "
        "%s) RETURNING id", [name, price])


def sell(env, lines, cust=None, d=D1, post=True, **kw):
    """فاتورة بيع (مرحّلة) ← (id، سطورها). ``lines`` = [(الصنف، الكمية، السعر)] أو dict جاهز."""
    tax = kw.pop("tax", env["vat14"])
    rows = [ln if isinstance(ln, dict) else {"item_id": ln[0], "qty": ln[1], "unit_price": ln[2], "tax_code_id": tax}
            for ln in lines]
    payload = {"invoice_date": d, "customer_id": cust or env["cust"], "warehouse_id": env["main"],
               "currency_code": kw.pop("currency_code", "EGP"), "due_date": kw.pop("due", d + dt.timedelta(days=30)),
               "lines": rows, **kw}
    if payload.get("pay_kind") == "cash":
        payload.pop("due_date")
    iid = env["sales"].save_draft(None, payload, post=post)
    return iid, env["sales"].get_invoice(iid)["lines"]


def receipt(env, amount, allocations=(), cust=None, d=D3, **kw):
    """بيانات سند قبض."""
    out = {"voucher_date": d, "party_id": cust or env["cust"], "source_kind": "treasury",
           "treasury_id": env["egp_box"], "bank_account_id": None, "amount": amount,
           "description": "تحصيل فواتير", "currency_code": "EGP",
           "allocations": [x if isinstance(x, dict) else {"sales_invoice_id": x[0], "amount": x[1]}
                           for x in allocations]}
    out.update(kw)
    return out


def entry(db, vid):
    return db.fetch_all(
        "SELECT a.code, l.debit, l.credit, l.customer_id, l.currency_code, l.fx_rate, l.amount_fc "
        "FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = 'customer_receipt' AND e.source_id = %s "
        "AND e.status = 'posted' ORDER BY l.line_no", [vid])


def due(env, iid, currency="EGP"):
    return env["db"].fetch_one("SELECT * FROM sales.sales_invoice_dues WHERE invoice_id = %s AND currency_code = %s",
                               [iid, currency])


def ledger_balance(db, cust):
    """رصيد العميل من دفتر الأستاذ (مدين − دائن بالجنيه — كل العملات) = اللي بيطلع في كشف الحساب."""
    return db.fetch_value(
        "SELECT coalesce(sum(l.debit - l.credit), 0) FROM acc.journal_lines l "
        "JOIN acc.journal_entries e ON e.id = l.entry_id AND e.status = 'posted' WHERE l.customer_id = %s", [cust])


def receivable_code(db, cust):
    return db.fetch_value("SELECT a.code FROM party.customer_groups g JOIN acc.accounts a ON a.id = g.control_account_id "
                          "JOIN party.customers c ON c.group_id = g.id WHERE c.id = %s", [cust])


def balanced(db):
    return TrialBalanceService(db).trial_balance(dt.date(2026, 1, 1), dt.date(2026, 12, 31))["balanced"]


# ---- السكريبت ------------------------------------------------------------------
def test_migration(env):
    db = env["db"]
    cols = {r["column_name"] for r in db.fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'cash' "
        "AND table_name = 'customer_receipts'")}
    assert {"currency_code", "fx_rate", "amount_base"} <= cols
    # السند من غير عملة = العملة الأساسية وسعرها 1 والمبلغ بالجنيه بيتحسب لوحده
    vid = db.fetch_value("INSERT INTO cash.customer_receipts (voucher_date, customer_id, source_kind, treasury_id, "
                         "amount, description) VALUES (%s, %s, 'treasury', %s, 250, 'x') RETURNING id",
                         [D3, env["cust"], env["egp_box"]])
    row = db.fetch_one("SELECT currency_code, fx_rate, amount_base FROM cash.customer_receipts WHERE id = %s", [vid])
    assert row == {"currency_code": "EGP", "fx_rate": D_("1"), "amount_base": D_("250.00")}
    with pytest.raises(psycopg.errors.RaiseException, match="سعرها 1"):
        db.execute("UPDATE cash.customer_receipts SET fx_rate = 2 WHERE id = %s", [vid])
    # عملة الخزنة لازم = عملة السند (الخزنة الدولار مع سند جنيه · والعكس)
    with pytest.raises(psycopg.errors.RaiseException, match="بنفس عملة السند"):
        db.execute("UPDATE cash.customer_receipts SET treasury_id = %s WHERE id = %s", [env["usd_box"], vid])
    with pytest.raises(psycopg.errors.RaiseException, match="بنفس عملة السند"):
        db.execute("UPDATE cash.customer_receipts SET currency_code = 'USD', fx_rate = 48 WHERE id = %s", [vid])
    db.execute("UPDATE cash.customer_receipts SET currency_code = 'USD', fx_rate = 48, treasury_id = %s WHERE id = %s",
               [env["usd_box"], vid])
    assert db.fetch_value("SELECT amount_base FROM cash.customer_receipts WHERE id = %s", [vid]) == D_("12000.00")
    # index على كل FK في جدول التوزيع (أول عمود في الـ unique = السند)
    missing = db.fetch_all("""
        SELECT a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.conrelid = 'cash.receipt_allocations'::regclass
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])""")
    assert missing == []
    assert db.fetch_value("SELECT count(*) FROM pg_indexes WHERE schemaname = 'cash' "
                          "AND indexname = 'customer_receipts_currency_idx'") == 1


# ---- التوزيع بالجنيه + المرتجع بيقلّل المتبقي ------------------------------------------------
def test_allocate_post_unpost_and_dues(env):
    db, rv = env["db"], env["rv"]
    i1, l1 = sell(env, [(env["item"], "10", "100")], d=D1)                  # 1,140 — استحقاق 31/3
    i2, _l = sell(env, [(env["item2"], "5", "200")], d=D2)                  # 1,140 — استحقاق 4/4
    icash, _c = sell(env, [(env["item"], "1", "100")], pay_kind="cash", source_kind="treasury",
                     treasury_id=env["egp_box"])                            # نقدي ← مابيظهرش
    idraft, _d = sell(env, [(env["item"], "1", "100")], post=False)
    iother, _o = sell(env, [(env["item"], "1", "100")], cust=env["cust2"])
    # مرتجع آجل مربوط بالفاتورة الأولى (2 × 100 + 14%) ← بينقص من التوتال
    env["ret"].save_draft(None, {"return_date": D2, "customer_id": env["cust"], "warehouse_id": env["main"],
                                 "currency_code": "EGP", "lines": [{"source_line_id": l1[0]["id"], "qty": "2"}]},
                          post=True)
    d1 = due(env, i1)
    assert (d1["total_amount"], d1["returned_amount"], d1["paid_amount"], d1["remaining_amount"]) == (
        D_("1140.00"), D_("228.00"), D_("0"), D_("912.00"))
    opened = rv.open_invoices(env["cust"], "EGP")
    assert [r["invoice_id"] for r in opened] == [i1, i2]                      # الآجل المرحّل بتاع العميل بس
    assert rv.count_open_invoices(env["cust"], "EGP") == {"total": 2, "remaining": D_("2052.00")}
    assert rv.open_invoices(env["cust"], "USD") == []                          # مفيش فواتير بالدولار
    assert [r["invoice_id"] for r in rv.open_invoices(env["cust"], "EGP", keyword=opened[1]["invoice_no"])] == [i2]

    # «وزّع الأقدم الأول»: 1,500 ← الأولى كلها (912) والباقي على التانية
    auto = rv.auto_allocate(env["cust"], "EGP", "1500")
    assert auto == [{"sales_invoice_id": i1, "amount": D_("912.00")},
                    {"sales_invoice_id": i2, "amount": D_("588.00")}]
    kept = rv.auto_allocate(env["cust"], "EGP", "1500", keep=[{"sales_invoice_id": i2, "amount": "1000"}])
    assert kept == [{"sales_invoice_id": i2, "amount": D_("1000")},
                    {"sales_invoice_id": i1, "amount": D_("500.00")}]

    # الأخطاء: أكتر من المتبقي · أكتر من السند · عميل تاني · نقدي · مسودة · متكررة
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        rv.save_draft(None, receipt(env, "2000", [(i1, "913")]))
    with pytest.raises(BusinessError, match="أكتر من مبلغ السند"):
        rv.save_draft(None, receipt(env, "1000", [(i1, "912"), (i2, "100")]))
    with pytest.raises(BusinessError, match="لعميل تاني"):
        rv.save_draft(None, receipt(env, "1000", [(iother, "100")]))
    with pytest.raises(BusinessError, match="نقدية — اتحصّلت"):
        rv.save_draft(None, receipt(env, "1000", [(icash, "100")]))
    with pytest.raises(BusinessError, match="مش مرحّلة"):
        rv.save_draft(None, receipt(env, "1000", [(idraft, "100")]))
    with pytest.raises(BusinessError, match="متكررة"):
        rv.save_draft(None, receipt(env, "1000", [(i1, "100"), (i1, "50")]))
    assert rv.preview(receipt(env, "1500", auto))[0]["debit"] == D_("1500.00")   # الخزنة مدين
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        rv.preview(receipt(env, "9000", [(i1, "5000")]))

    # المسودة مابتخصمش من المتبقي
    vid = rv.save_draft(None, receipt(env, "1500", auto))
    v = rv.get_voucher(vid)
    assert v["allocated"] == D_("1500.00") and v["unallocated"] == D_("0.00") and len(v["allocations"]) == 2
    assert v["allocations"][0]["remaining_amount"] == D_("912.00")
    assert due(env, i1)["remaining_amount"] == D_("912.00")
    # تعديل المسودة: مبلغ أكبر ← الباقي دفعة مقدمة
    rv.save_draft(vid, receipt(env, "2000", [(i1, "912"), (i2, "588")]), v["row_version"])
    assert rv.post(vid) == "RV-2026-00001"
    v = rv.get_voucher(vid)
    assert v["unallocated"] == D_("500.00") and v["currency_code"] == "EGP" and v["amount_base"] == D_("2000.00")
    assert v["allocations"][0]["remaining_amount"] == D_("912.00")            # المتبقي «قبل» السند ده
    assert due(env, i1)["remaining_amount"] == D_("0.00") and due(env, i2)["remaining_amount"] == D_("552.00")
    assert [r["invoice_id"] for r in rv.open_invoices(env["cust"], "EGP")] == [i2]
    assert rv.repository.unallocated_credit(env["cust"], "EGP", kind="receipt") == D_("500.00")
    # القيد واحد: مدين الخزنة / دائن العميل بالمبلغ كله (التوزيع مابيغيّرهوش بالجنيه)
    lines = entry(db, vid)
    rec_code = receivable_code(db, env["cust"])
    assert [(ln["code"], ln["debit"], ln["credit"]) for ln in lines] == [
        ("110101", D_("2000.00"), D_("0.00")), (rec_code, D_("0.00"), D_("2000.00"))]
    assert lines[1]["customer_id"] == env["cust"] and lines[1]["currency_code"] is None
    # رصيد العميل بالجنيه: 1,140 + 1,140 − 228 (المرتجع) − 2,000 = 52 (عليه) — النقدية اتحصّلت مع الفاتورة
    assert rv.balances(env["cust"], "treasury", env["egp_box"], "0", posted=True)["party"] == \
        ledger_balance(db, env["cust"]) == D_("52.00")

    # مرتجع آجل مربوط بالتانية بعد التحصيل ← المتبقي عليها بيقل كمان
    lines2 = env["sales"].get_invoice(i2)["lines"]
    env["ret"].save_draft(None, {"return_date": D3, "customer_id": env["cust"], "warehouse_id": env["main"],
                                 "currency_code": "EGP", "lines": [{"source_line_id": lines2[0]["id"], "qty": "1"}]},
                          post=True)
    assert due(env, i2)["remaining_amount"] == D_("324.00")                      # 552 − 228

    # المرحّل: توزيعه مايتغيّرش في القاعدة · والفاتورة مايتلغيش ترحيلها وعليها تحصيل مرحّل
    with pytest.raises(psycopg.errors.RaiseException, match="توزيعه مايتغيّرش"):
        db.execute("DELETE FROM cash.receipt_allocations WHERE customer_receipt_id = %s", [vid])
    with pytest.raises(BusinessError, match="عليها مرتجع|عليها تحصيل مرحّل"):
        env["sales"].unpost(i2)
    with pytest.raises(psycopg.errors.RaiseException, match="عليها تحصيل مرحّل"):
        db.execute("UPDATE sales.sales_invoices SET status = 'draft' WHERE id = %s", [i2])
    # سند تاني بيحاول يحصّل أكتر من اللي فاضل على التانية
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        rv.save_draft(None, receipt(env, "600", [(i2, "325")]), post=True)
    # إلغاء الترحيل: القيد بيتمسح والتوزيع بيبطّل يخصم
    rv.unpost(vid)
    assert entry(db, vid) == [] and due(env, i1)["remaining_amount"] == D_("912.00")
    assert rv.get_voucher(vid)["allocations"][0]["amount"] == D_("912.00")       # التوزيع فضل في المسودة
    rv.post(vid)
    assert balanced(db)


def test_invoice_without_return_can_unpost_after_receipt_unposted(env):
    """فاتورة البيع عليها تحصيل مرحّل ← إلغاء ترحيلها ممنوع · بعد إلغاء ترحيل السند ← مسموح والسند مايترحّلش."""
    rv = env["rv"]
    iid, _l = sell(env, [(env["item"], "10", "100")])                       # 1,140
    vid = rv.save_draft(None, receipt(env, "500", [(iid, "500")]), post=True)
    with pytest.raises(BusinessError, match="عليها تحصيل مرحّل .*RV-2026-00001"):
        env["sales"].unpost(iid)
    rv.unpost(vid)
    env["sales"].unpost(iid)                                                 # السند مسودة ← الفاتورة اتلغى ترحيلها
    with pytest.raises(BusinessError, match="مش مرحّلة"):
        rv.post(vid)
    env["sales"].post(iid)
    rv.post(vid)
    assert due(env, iid)["remaining_amount"] == D_("640.00")


def test_database_guards(env):
    """القاعدة نفسها بترفض حتى لو الـ Service اتخطّى."""
    db, rv = env["db"], env["rv"]
    i1, _l = sell(env, [(env["item"], "10", "100")])                        # 1,140
    iother, _o = sell(env, [(env["item"], "1", "100")], cust=env["cust2"])
    vid = rv.save_draft(None, receipt(env, "500"))

    def alloc(invoice, amount, base=None):
        db.execute("INSERT INTO cash.receipt_allocations (customer_receipt_id, sales_invoice_id, currency_code, "
                   "amount, amount_base) VALUES (%s, %s, 'USD', %s, %s)", [vid, invoice, amount, base or amount])

    with pytest.raises(psycopg.errors.RaiseException, match="لعميل تاني"):
        alloc(iother, 100)
    with pytest.raises(psycopg.errors.RaiseException, match="أكتر من مبلغ السند"):
        alloc(i1, 600)
    alloc(i1, 300)
    assert db.fetch_value("SELECT currency_code FROM cash.receipt_allocations WHERE customer_receipt_id = %s",
                          [vid]) == "EGP"                                    # العملة = عملة السند دايماً
    with pytest.raises(psycopg.errors.RaiseException, match="شيل التوزيع الأول عشان تغيّر العميل"):
        db.execute("UPDATE cash.customer_receipts SET customer_id = %s WHERE id = %s", [env["cust2"], vid])
    with pytest.raises(psycopg.errors.RaiseException, match="شيل التوزيع الأول عشان تغيّر العملة"):
        db.execute("UPDATE cash.customer_receipts SET currency_code = 'USD', fx_rate = 48, treasury_id = %s "
                   "WHERE id = %s", [env["usd_box"], vid])
    with pytest.raises(psycopg.errors.RaiseException, match="من غير قيد"):
        db.execute("UPDATE cash.customer_receipts SET status = 'posted' WHERE id = %s", [vid])
    # المسودة بتتمسح بتوزيعها (CASCADE)
    rv.delete_voucher(vid)
    assert db.fetch_value("SELECT count(*) FROM cash.receipt_allocations") == 0


# ---- العملات وفرق العملة -----------------------------------------------------------
def test_foreign_currency_and_fx_difference(env):
    db, rv = env["db"], env["rv"]
    cust = env["usd_cust"]
    iid, _l = sell(env, [(env["item"], "10", "100")], cust=cust, currency_code="USD", fx_rate="48",
                   tax=env["vat0"])                                         # 1,000 دولار × 48 = 48,000
    assert due(env, iid, "USD")["total_amount"] == D_("1000.00") and due(env, iid, "USD")["total_base"] == D_("48000.00")
    assert due(env, iid) is None                                             # مفيش جزء بالجنيه
    assert rv.rate_for("USD", D3) == D_("48.5") and rv.rate_for("EGP", D3) == D_("1")
    # سند بالجنيه مايحصّلش فاتورة دولار · وخزنة الجنيه ماتنفعش لسند دولار
    with pytest.raises(BusinessError, match="مافيهاش مبلغ بعملة EGP"):
        rv.save_draft(None, receipt(env, "1000", [(iid, "100")], cust=cust))
    with pytest.raises(BusinessError, match="بنفس عملة السند"):
        rv.save_draft(None, receipt(env, "400", [(iid, "400")], cust=cust, currency_code="USD", fx_rate="49"))
    usd = dict(cust=cust, currency_code="USD", treasury_id=env["usd_box"])
    # 500 دولار بسعر 49: 400 على الفاتورة (بسعرها 48) + 100 دفعة مقدمة (بسعر السند) ← استلمنا 400 جنيه زيادة = مكسب
    v1 = rv.save_draft(None, receipt(env, "500", [(iid, "400")], fx_rate="49", **usd), post=True)
    rec = rv.get_voucher(v1)
    assert rec["fx_rate"] == D_("49") and rec["amount_base"] == D_("24500.00") and rec["unallocated"] == D_("100.00")
    assert rec["allocations"][0]["invoice_rate"] == D_("48") and rec["allocations"][0]["amount_base"] == D_("19200.00")
    rec_code = receivable_code(db, cust)
    assert [(ln["code"], ln["debit"], ln["credit"], ln["currency_code"], ln["fx_rate"], ln["amount_fc"])
            for ln in entry(db, v1)] == [
        ("110102", D_("24500.00"), D_("0.00"), "USD", D_("49"), D_("500.00")),
        (rec_code, D_("0.00"), D_("19200.00"), "USD", D_("48"), D_("400.00")),
        (rec_code, D_("0.00"), D_("4900.00"), "USD", D_("49"), D_("100.00")),
        ("4301", D_("0.00"), D_("400.00"), None, None, None)]
    assert due(env, iid, "USD")["remaining_amount"] == D_("600.00")
    # الأرصدة **بالجنيه** زي كشف الحساب: العميل 48,000 − 19,200 − 4,900 = 23,900 · الخزنة دخلها 24,500 —
    # ومعاهم للعلم بالدولار: العميل 1,000 − 500 = 500 · الخزنة +500
    b = rv.balances(cust, "treasury", env["usd_box"], "0", posted=True, currency_code="USD", fx_rate="49")
    assert b["party"] == D_("23900.00") and b["source"] == D_("24500.00") and b["currency_code"] == "USD"
    assert b["party_fc"] == D_("500.00") and b["source_fc"] == D_("500.00")
    assert b["party"] == ledger_balance(db, cust)                             # مطابق لدفتر الأستاذ
    # قبل الترحيل: «بعد السند» = اللي هيتقفل من العميل بالجنيه (من القيد) · والخزنة بالمبلغ × سعر السند
    b = rv.balances(cust, "treasury", env["usd_box"], "100", currency_code="USD", fx_rate="50", party_effect=D_("4800"))
    assert b["amount_base"] == D_("5000.00") and b["party_after"] == D_("19100.00")
    assert b["source_after"] == D_("29500.00") and b["party_fc_after"] == D_("400.00")
    assert b["source_fc_after"] == D_("600.00")
    assert rv.funds_warning(receipt(env, "10", fx_rate="49", **usd)) is None      # القبض مالوش تحذير خزنة
    # آخر دفعة (600 بسعر 47): بتقفل الفاتورة بالظبط ← استلمنا 600 جنيه أقل = خسارة
    v2 = rv.save_draft(None, receipt(env, "600", [(iid, "600")], fx_rate="47", **usd), post=True)
    assert [(ln["code"], ln["debit"], ln["credit"]) for ln in entry(db, v2)] == [
        ("110102", D_("28200.00"), D_("0.00")), (rec_code, D_("0.00"), D_("28800.00")),
        ("5401", D_("600.00"), D_("0.00"))]
    assert due(env, iid, "USD")["remaining_amount"] == D_("0.00") and due(env, iid, "USD")["remaining_base"] == D_("0.00")
    # السعر من جدول الأسعار لو مااتكتبش
    v3 = rv.save_draft(None, receipt(env, "10", fx_rate="", **usd))
    assert rv.get_voucher(v3)["fx_rate"] == D_("48.5")
    db.execute("DELETE FROM core.exchange_rates")
    with pytest.raises(BusinessError, match="مفيش سعر متسجل"):
        rv.save_draft(None, receipt(env, "10", fx_rate="", **usd))
    assert balanced(db)


def test_multi_currency_invoice_part_per_currency(env):
    rv = env["rv"]
    cust = env["usd_cust"]
    lines = [{"item_id": env["item"], "qty": "2", "unit_price": "100", "tax_code_id": env["vat0"],
              "currency_code": "USD", "fx_rate": "50"},
             {"item_id": env["item2"], "qty": "1", "unit_price": "300", "tax_code_id": env["vat0"],
              "currency_code": "EGP", "fx_rate": "1"}]
    iid, _l = sell(env, lines, cust=cust, currency_mode="multi", currency_code="EGP")
    assert due(env, iid, "USD")["total_amount"] == D_("200.00") and due(env, iid, "EGP")["total_amount"] == D_("300.00")
    # الفاتورة بتظهر في سند كل عملة بجزئها بس
    assert [(r["invoice_id"], r["remaining_amount"]) for r in rv.open_invoices(cust, "USD")] == [(iid, D_("200.00"))]
    assert [(r["invoice_id"], r["remaining_amount"]) for r in rv.open_invoices(cust, "EGP")] == [(iid, D_("300.00"))]
    with pytest.raises(BusinessError, match="أكتر من المتبقي"):
        rv.save_draft(None, receipt(env, "300", [(iid, "250")], cust=cust, currency_code="USD",
                                    treasury_id=env["usd_box"], fx_rate="50"))
    rv.save_draft(None, receipt(env, "300", [(iid, "300")], cust=cust), post=True)                   # الجزء الجنيه
    rv.save_draft(None, receipt(env, "200", [(iid, "200")], cust=cust, currency_code="USD", treasury_id=env["usd_box"],
                                fx_rate="50"), post=True)                                          # الجزء الدولار
    assert due(env, iid, "USD")["remaining_amount"] == 0 and due(env, iid, "EGP")["remaining_amount"] == 0
    assert rv.open_invoices(cust, "USD") == [] and rv.open_invoices(cust, "EGP") == []


def test_bank_like_treasury(env):
    """التحصيل في بنك زي الخزنة بالظبط — وعملة الحساب البنكي لازم = عملة السند."""
    db, rv = env["db"], env["rv"]
    i1, _l = sell(env, [(env["item"], "10", "100")])
    vid = rv.save_draft(None, receipt(env, "1140", [(i1, "1140")], source_kind="bank", treasury_id=None,
                                      bank_account_id=env["bank"]), post=True)
    assert [ln["code"] for ln in entry(db, vid)] == ["110201", receivable_code(db, env["cust"])]
    assert due(env, i1)["remaining_amount"] == 0
    with pytest.raises(BusinessError, match="الحساب البنكي .* بعملة EGP"):
        rv.save_draft(None, receipt(env, "10", cust=env["usd_cust"], currency_code="USD", fx_rate="48",
                                    source_kind="bank", treasury_id=None, bank_account_id=env["bank"]))


# ---- جهازين ---------------------------------------------------------------------
def test_two_devices_collect_same_invoice(env):
    db = env["db"]
    s1, s2 = env["rv"], VoucherService("receipt", Database(db.settings))
    try:
        i1, _l = sell(env, [(env["item"], "10", "100")])                      # 1,140
        a = s1.save_draft(None, receipt(env, "800", [(i1, "800")]))
        b = s2.save_draft(None, receipt(env, "800", [(i1, "800")]))           # المسودات مابتخصمش ← الاتنين اتحفظوا
        s1.post(a)
        with pytest.raises(BusinessError, match="أكتر من المتبقي"):
            s2.post(b)
        assert s2.get_voucher(b)["status"] == "draft" and due(env, i1)["remaining_amount"] == D_("340.00")
        # في نفس اللحظة: التاني بيستنى قفل الفاتورة وبعدين بيشوف تحصيل الأول ← بيترفض
        i2, _l2 = sell(env, [(env["item"], "10", "100")])
        c = s1.save_draft(None, receipt(env, "1000", [(i2, "1000")]))
        d = s2.save_draft(None, receipt(env, "1000", [(i2, "1000")]))
        results: dict[str, object] = {}

        def worker():
            try:
                results["no"] = s2.post(d)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            tx.execute("SELECT id FROM sales.sales_invoices WHERE id = %s FOR UPDATE", [i2])   # الجهاز الأول ماسكها
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                                          # التاني مستني
            s1_tx = VoucherService("receipt", db)
            before = s1_tx._lock(tx, c, None, draft_only=True)
            s1_tx._post(tx, c, before)
        thread.join(10)
        assert isinstance(results.get("error"), BusinessError) and "أكتر من المتبقي" in str(results["error"])
        assert due(env, i2)["paid_amount"] == D_("1000.00")
        assert db.fetch_value("SELECT count(*) FROM cash.customer_receipts WHERE status = 'posted'") == 2
        # جهاز بيلغي ترحيل الفاتورة وجهاز بيحصّلها في نفس اللحظة: التحصيل بيستنى قفل الفاتورة ← الإلغاء خلص الأول
        # ← التحصيل بيترفض (الفاتورة بقت مسودة)
        i3, _l3 = sell(env, [(env["item"], "1", "100")])
        e = s2.save_draft(None, receipt(env, "114", [(i3, "114")]))
        results.clear()

        def collect():
            try:
                results["no"] = s2.post(e)
            except Exception as exc:  # noqa: BLE001
                results["error"] = exc

        thread = threading.Thread(target=collect)
        with db.atomic() as tx:
            tx.execute("SELECT id FROM sales.sales_invoices WHERE id = %s FOR UPDATE", [i3])
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                                          # التحصيل مستني الفاتورة
            # نفس خطوات «إلغاء الترحيل» في فاتورة البيع جوه الـ transaction اللي ماسكة الفاتورة
            from app.services.sales_invoice_service import DOC_TYPE

            sales = env["sales"]
            sales.posting.unpost_document(tx, doc_type=DOC_TYPE, source_id=i3)
            sales.ledger.remove(tx, doc_type=DOC_TYPE, source_id=i3, allow_negative=True)
            sales.repository.set_status(i3, "draft", None, ex=tx)
        thread.join(10)
        assert isinstance(results.get("error"), BusinessError) and "مش مرحّلة" in str(results["error"])
        assert s2.get_voucher(e)["status"] == "draft"
    finally:
        s2.db.close()


def test_database_blocks_overcollect_even_without_service_check(env):
    """آخر خط دفاع: Constraint Trigger بيحسب المحصّل على الفاتورة آخر الـ transaction."""
    db, rv = env["db"], env["rv"]
    i1, _l = sell(env, [(env["item"], "10", "100")])                         # 1,140
    rv.save_draft(None, receipt(env, "1000", [(i1, "1000")]), post=True)
    vid = rv.save_draft(None, receipt(env, "500"))
    db.execute("INSERT INTO cash.receipt_allocations (customer_receipt_id, sales_invoice_id, currency_code, amount, "
               "amount_base) VALUES (%s, %s, 'EGP', 500, 500)", [vid, i1])  # مسودة ← مسموح
    real = rv._allocation_rows
    rv._allocation_rows = lambda v, ex=None: [  # type: ignore[method-assign]
        {"sales_invoice_id": i1, "currency_code": "EGP", "amount": D_("500"), "invoice_rate": D_("1"),
         "amount_base": D_("500"), "invoice_no": "x"}]
    try:
        with pytest.raises((BusinessError, psycopg.errors.RaiseException), match="أكتر من المستحق"):
            rv.post(vid)
    finally:
        rv._allocation_rows = real  # type: ignore[method-assign]
    assert rv.get_voucher(vid)["status"] == "draft"


def test_two_devices_edit_same_draft_allocations(env):
    """جهازين فاتحين نفس المسودة: الأول غيّر التوزيع بس (الرأس زي ما هو) ← التاني بنسخته القديمة بيترفض."""
    from app.services.errors import ConcurrentEditError

    rv = env["rv"]
    i1, _l = sell(env, [(env["item"], "10", "100")])
    vid = rv.save_draft(None, receipt(env, "1000", [(i1, "400")]))
    seen = rv.get_voucher(vid)["row_version"]
    rv.save_draft(vid, receipt(env, "1000", [(i1, "900")]), seen)
    with pytest.raises(ConcurrentEditError):
        rv.save_draft(vid, receipt(env, "1000", [(i1, "100")]), seen)
    assert rv.get_voucher(vid)["allocations"][0]["amount"] == D_("900.00")


def test_account_statement_in_base_currency(env):
    """كشف حساب العميل **بالجنيه بس** (مطابق لدفتر الأستاذ) — وسند القبض الدولار عليه ملاحظة «… USD × السعر»."""
    from app.services.statement_service import StatementService

    db, rv = env["db"], env["rv"]
    cust = env["usd_cust"]
    sell(env, [(env["item2"], "5", "200")], cust=cust)                                     # 1,140 جنيه
    iid, _l = sell(env, [(env["item"], "10", "100")], cust=cust, currency_code="USD", fx_rate="55",
                   tax=env["vat0"], d=D2)                                                  # 1,000 دولار × 55
    rv.save_draft(None, receipt(env, "400", [(iid, "400")], cust=cust, currency_code="USD", treasury_id=env["usd_box"],
                                fx_rate="55"), post=True)
    res = StatementService(db).statement("customer", cust, dt.date(2026, 1, 1), dt.date(2026, 12, 31))
    rows = [r for r in res["rows"] if r["kind"] == "line"]
    assert [(r["debit"], r["credit"]) for r in rows] == [(D_("1140.00"), 0), (D_("55000.00"), 0), (0, D_("22000.00"))]
    assert rows[2]["description"].endswith("(400.00 USD × 55)")
    assert res["end"] == ledger_balance(db, cust) == D_("34140.00")                        # 1,140 + 55,000 − 22,000
    # نفس الرقم في كارت «رصيد العميل» في سند القبض
    assert rv.balances(cust, "treasury", env["usd_box"], "0", posted=True, currency_code="USD",
                       fx_rate="55")["party"] == D_("34140.00")


# ---- الشاشة ---------------------------------------------------------------------
def make_screen(db):
    from app.ui.screens.vouchers_screen import ReceiptsScreen

    screen = ReceiptsScreen(VoucherService("receipt", db))
    screen.from_edit.setDate(screen.from_edit.date().addYears(-5))
    screen.to_edit.setDate(screen.to_edit.date().addYears(5))
    return screen


def plain(text):
    return text.replace("⁦", "").replace("⁩", "")


def test_screen_currency_lookups_and_allocation(env):
    from app.ui.screens.vouchers_screen import A_LEFT, A_NO, A_PAY, InvoiceDuesDialog

    db, shown = env["db"], env["shown"]
    i1, _l = sell(env, [(env["item"], "10", "100")], d=D1, customer_ref="PO-77")   # 1,140
    i2, _l2 = sell(env, [(env["item2"], "5", "200")], d=D2)                       # 1,140
    iusd, _l3 = sell(env, [(env["item"], "1", "100")], cust=env["usd_cust"], currency_code="USD", fx_rate="48",
                     tax=env["vat0"])
    screen = make_screen(db)
    try:
        # أيقونة البحث جنب العملة والعميل والخزنة · تبويبين: التوزيع + القيد · عناوين القبض
        assert screen.currency_find.text() == screen.party_find.text() == screen.source_find.text() == "🔍"
        assert [screen.tabs.tabText(i) for i in range(2)] == ["🧾 التوزيع على الفواتير", "📒 القيد"]
        assert screen.alloc_table.horizontalHeaderItem(A_PAY).text() == "يحصّل"
        screen.new_record()
        assert screen.mode == "new" and screen.currency_edit.currentData() == "EGP"
        assert screen.source_edit.currentData() == env["egp_box"]            # خزن الجنيه بس
        assert [r[0] for r in screen._source_rows("treasury")] == [env["egp_box"]]
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-20", "yyyy-MM-dd"))
        screen._pick_from = lambda _t, _c, rows: next(r["id"] for r in rows if r["id"] == env["cust"])
        screen._find_party()
        assert screen.party_edit.currentData() == env["cust"]
        screen.amount_edit.setText("1500")
        screen.description.setText("تحصيل فواتير الأمل")
        screen.auto_allocate()
        assert screen.alloc_table.rowCount() == 2 and "(2)" in screen.tabs.tabText(0)
        assert screen.alloc_table.item(0, A_LEFT).text() == "1,140.00" and screen.alloc_table.item(1, A_PAY).text() == "360.00"
        assert "اتوزّع كله" in plain(screen.alloc_summary.text())
        screen.alloc_table.item(1, A_PAY).setText("100")
        assert screen._allocs[1]["amount"] == D_("100") and "دفعة مقدمة على حساب العميل" in plain(screen.alloc_summary.text())
        screen.alloc_table.item(1, A_PAY).setText("5000")
        assert "أكتر" in plain(screen.alloc_summary.text())
        screen._refresh_side()
        assert screen.entry_label.text().startswith("✖")
        screen.alloc_table.item(1, A_PAY).setText("0")                        # صفر = شيل الفاتورة
        assert screen.alloc_table.rowCount() == 1
        # نافذة «📋 الفواتير» (فواتير العميل): بالتوتال · أمر الشراء عند العميل · ✔ = المتبقي أو اللي فاضل من السند
        dialog = InvoiceDuesDialog(screen.service, screen, supplier_id=env["cust"], supplier_name="الأمل",
                                   currency_code="EGP", amount=D_("1500"), picks={i1: D_("1140")})
        try:
            assert "العميل" in dialog.windowTitle()
            assert dialog.table.horizontalHeaderItem(dialog.C_SUP_NO).text() == "أمر شراء العميل"
            assert dialog.table.rowCount() == 2 and dialog.table.item(0, dialog.C_LEFT).text() == "1,140.00"
            assert dialog.table.item(0, dialog.C_SUP_NO).text() == "PO-77"
            assert dialog.table.item(0, dialog.C_CHECK).checkState().name == "Checked"
            dialog.table.item(1, dialog.C_CHECK).setCheckState(dialog.table.item(1, dialog.C_CHECK).checkState().Checked)
            assert dialog.picks == {i1: D_("1140"), i2: D_("360.00")}
            assert "هيتحصّل" in dialog.summary.text()
            dialog.table.item(1, dialog.C_PAY).setText("9999")
            assert "المتبقي عليها" in dialog.summary.text() and dialog.picks[i2] == D_("360.00")
            dialog.clear()
            dialog.auto_allocate()
            assert dialog.picks == {i1: D_("1140.00"), i2: D_("360.00")}
            dialog.apply()
            assert dialog.result == [{"sales_invoice_id": i1, "amount": D_("1140.00")},
                                     {"sales_invoice_id": i2, "amount": D_("360.00")}]
        finally:
            dialog.close()
        screen._set_allocations(dialog.result)
        assert screen.alloc_table.item(0, A_NO).text().startswith("INV-2026-")
        screen._refresh_side()
        assert screen.entry_table.rowCount() == 2 and "متوازن" in screen.entry_label.text()
        assert screen.entry_table.item(0, 0).text().startswith("110101")    # الخزنة مدين الأول
        # رصيد العميل (عليه) بيقل بالمبلغ
        assert plain(screen.kpi_values["party"].text()).endswith("←  780.00 مدين")
        screen.post_voucher()
        rec = screen.current_record
        assert rec["voucher_no"] == "RV-2026-00001" and rec["allocated"] == D_("1500.00")
        assert screen.alloc_table.rowCount() == 2 and not screen.alloc_pick_button.isEnabled()
        assert due(env, i1)["remaining_amount"] == 0 and due(env, i2)["remaining_amount"] == D_("780.00")

        # سند بالدولار: العملة من العميل · السعر من الجدول · «استلمنا في» خزنة الدولار بس
        screen.new_record()
        screen.date_edit.setDate(screen.date_edit.date().fromString("2026-03-20", "yyyy-MM-dd"))
        screen.party_edit.set_value(env["usd_cust"])
        assert screen.currency_edit.currentData() == "USD" and screen.rate_edit.text() == "48.5"
        assert screen.source_edit.currentData() == env["usd_box"] and screen._source_rows("bank") == []
        screen.rate_edit.setText("49")
        screen._on_rate_edited()
        screen.amount_edit.setText("100")
        screen.description.setText("تحصيل دولار")
        screen.auto_allocate()
        assert [a["sales_invoice_id"] for a in screen._allocs] == [iusd]
        screen._refresh_side()
        # الكروت بالجنيه (زي كشف الحساب) — والعملة سطر صغير للعلم
        assert plain(screen.kpi_values["amount"].text()) == "4,900.00" and "بالجنيه" in screen.kpi_captions["party"].text()
        assert "100.00 USD × 49" in plain(screen.kpi_notes["amount"].text())
        assert plain(screen.kpi_values["party"].text()) == "4,800.00 مدين  ←  0.00"     # 100 دولار بسعر الفاتورة 48
        assert screen.entry_table.rowCount() == 3                             # الخزنة · العميل · فروق العملة
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
        assert screen.current_record["voucher_no"] == "RV-2026-00002"
        assert [ln["code"] for ln in entry(db, screen.current_id)][-1] == "4301"       # 100 × (49 − 48) مكسب
    finally:
        screen.close()
