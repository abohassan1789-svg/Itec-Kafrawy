"""اختبارات «فاتورة الشراء» (0048 + purchase_invoice_service + الشاشة — النموذج 9):
الحسبة (خصم السطر · خصم الفاتورة بيتوزّع ويقلّل التكلفة · شامل / غير شامل · البيان) · مسودة ← ترحيل برقم PUR وحركة
مخزون لكل صنف مخزني والمتوسط · القيد (مخزون · خدمي · ض.ق.م / المورد) · نقدي = قيد صرف لوحده · العملة الواحدة بسعرها
والعملة على سطر القيد · متعدد العملات (سطر على المورد لكل عملة) · الوهمي والمكرر · إلغاء الترحيل و«من غير رصيد» ·
حماية القاعدة · جهازين · المراجعة وتنبيه السعر · قفل السنة · الشاشة."""

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
from app.services.errors import BusinessError, ConcurrentEditError
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.purchase_invoice_service import PurchaseInvoiceService, calculate
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService, StockLine
from app.ui.common import messages

D1, D2, D3 = dt.date(2026, 3, 1), dt.date(2026, 3, 2), dt.date(2026, 3, 3)
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
    monkeypatch.setattr(messages, "ask_save", lambda _p: True)
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, _text: True)
    monkeypatch.setattr(messages, "show_saved", lambda _p: shown.append("saved"))
    monkeypatch.setattr(messages, "show_error", lambda _p, _t, exc: shown.append(messages.error_text(exc)))
    monkeypatch.setattr(messages, "warn", lambda _p, _t, text: shown.append(text))
    monkeypatch.setattr(messages, "info", lambda _p, _t, text: shown.append(text))
    group = db.fetch_value("SELECT group_id FROM party.suppliers WHERE seq = 0")
    env = {"db": db, "shown": shown, "service": PurchaseInvoiceService(db), "ledger": StockLedgerService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "لمبة LED"), "svc": item(db, "تركيب ومعايرة", True),
           "vat14": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT14'"),
           "exempt": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'EXEMPT'"),
           "treasury": db.fetch_value("SELECT id FROM cash.treasuries WHERE code = 'TRS-001'"),
           "sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code, payment_terms_days) "
                                 "VALUES (1, 'شركة النور', %s, 'EGP', 45) RETURNING id", [group]),
           "usd_sup": db.fetch_value("INSERT INTO party.suppliers (seq, name_ar, group_id, currency_code, "
                                     "vat_tax_code_id) VALUES (2, 'Global Controls', %s, 'USD', "
                                     "(SELECT id FROM acc.tax_codes WHERE code = 'EXEMPT')) RETURNING id", [group])}
    env["branch"] = db.fetch_value("INSERT INTO inv.warehouses (seq, name_ar) VALUES "
                                   "((SELECT max(seq) + 1 FROM inv.warehouses), 'مخزن الفرع') RETURNING id")
    for code, rate in (("USD", "48.5"), ("EUR", "52.8")):
        db.execute("INSERT INTO core.exchange_rates (currency_id, rate_date, rate) "
                   "SELECT id, '2026-01-01', %s FROM core.currencies WHERE code = %s", [rate, code])
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name, service=False):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, is_service) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), %s) "
        "RETURNING id", [name, service])


def line(env, it=None, qty="10", price="100", **kw):
    return {"item_id": it or env["item"], "qty": qty, "unit_price": price, "tax_code_id": env["vat14"], **kw}


def data(env, lines, sup=None, d=D1, **kw):
    return {"invoice_date": d, "supplier_id": sup or env["sup"], "warehouse_id": env["main"], "currency_code": "EGP",
            "due_date": d + dt.timedelta(days=45), "lines": lines, **kw}


def no_negative(monkeypatch):
    real = SESSION.can
    monkeypatch.setattr(SESSION, "can", lambda code: code != "purch.purchase_invoices.negative" and real(code))


def entry(db, doc, sid):
    return db.fetch_all(
        "SELECT a.code, a.role_code, l.debit, l.credit, l.supplier_id, l.currency_code, l.fx_rate, l.amount_fc, "
        "l.project_id FROM acc.journal_lines l JOIN acc.journal_entries e ON e.id = l.entry_id "
        "JOIN acc.accounts a ON a.id = l.account_id WHERE e.source_type = %s AND e.source_id = %s "
        "AND e.status = 'posted' ORDER BY l.line_no", [doc, sid])


def stock(env, it=None, wh=None, kind="invoice"):
    return env["ledger"].available(it or env["item"], wh or env["main"], kind)


def avg(env, it=None, kind="invoice"):
    return env["ledger"].average_cost(it or env["item"], kind)


# ---- السكريبت ------------------------------------------------------------------
def test_migration_rules_columns_permissions(env):
    db = env["db"]
    rules = db.fetch_all("SELECT role, side, account_source, category_kind FROM acc.posting_rules "
                         "WHERE doc_type = 'purchase_invoice' AND role = 'service'")
    assert rules == [{"role": "service", "side": "dr", "account_source": "category", "category_kind": "cogs"}]
    assert [r["role"] for r in db.fetch_all("SELECT role FROM acc.posting_rules WHERE doc_type = 'purchase_payment' "
                                            "ORDER BY sort_order")] == ["payable", "cash", "bank"]
    cols = {r["column_name"] for r in db.fetch_all(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = 'acc' AND table_name = 'journal_lines'")}
    assert {"currency_code", "fx_rate", "amount_fc"} <= cols
    perms = {r["action_code"] for r in db.fetch_all(
        "SELECT action_code FROM sec.permissions WHERE module_code = 'purch' AND target_code = 'purchase_invoices'")}
    assert perms == {"view", "create", "edit", "save", "delete", "export", "post", "unpost", "negative"}
    # كل FK عليه index
    missing = db.fetch_all("""
        SELECT c.conrelid::regclass::text AS tbl, a.attname FROM pg_constraint c
        JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
        WHERE c.contype = 'f' AND c.conrelid::regclass::text LIKE 'purch.%%'
          AND NOT EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.conrelid AND i.indkey[0] = c.conkey[1])""")
    assert missing == [{"tbl": "purch.purchase_invoice_lines", "attname": "invoice_id"}] or not missing or all(
        m["attname"] == "invoice_id" for m in missing)          # invoice_id أول عمود في الـ unique (invoice_id, line_no)


# ---- الحسبة -----------------------------------------------------------------
def test_calculate_discounts_inclusive_statement():
    lines = [{"qty": D_("5"), "unit_price": D_("1850"), "discount_pct": D_("0"), "tax_rate": D_("14"),
              "currency_code": "EGP", "fx_rate": D_("1"), "factor": D_("1")},
             {"qty": D_("10"), "unit_price": D_("880"), "discount_pct": D_("5"), "tax_rate": D_("14"),
              "currency_code": "EGP", "fx_rate": D_("1"), "factor": D_("20")}]
    c = calculate(lines, discount_amount=D_("500"))
    a, b = c["lines"]
    assert (a["gross_amount"], a["line_discount"]) == (D_("9250.00"), D_("0.00"))
    assert (b["gross_amount"], b["line_discount"]) == (D_("8800.00"), D_("440.00"))
    assert a["header_discount"] + b["header_discount"] == D_("500")                  # بيتوزّع بالقيمة
    assert a["header_discount"] == D_("262.63")                                      # 500 × 9250 / 17610
    assert c["net_amount"] == D_("17110.00") and c["vat_amount"] == D_("2395.40")
    assert c["total_amount"] == D_("19505.40") and c["items_total"] == D_("17610.00")
    assert b["base_qty"] == D_("200") and b["unit_cost"] == (b["net_base"] / 200).quantize(D_("0.000001"))
    inc = calculate([{**lines[0], "unit_price": D_("114")}], prices_include_vat=True)
    assert (inc["net_amount"], inc["vat_amount"], inc["total_amount"]) == (D_("500.00"), D_("70.00"), D_("570.00"))
    st = calculate(lines, statement=True)
    assert st["vat_amount"] == 0 and st["total_amount"] == st["net_amount"]
    with pytest.raises(BusinessError, match="أكبر من إجمالي"):
        calculate(lines, discount_amount=D_("99999"))


# ---- الترحيل ---------------------------------------------------------------
def test_credit_invoice_post_stock_avg_and_entry(env):
    s, db = env["service"], env["db"]
    iid = s.save_draft(None, data(env, [line(env, qty="10", price="100"),
                                        line(env, env["item2"], "4", "50", warehouse_id=env["branch"])],
                                  supplier_invoice_no="5520", discount_amount="120"))
    rec = s.get_invoice(iid)
    assert rec["status"] == "draft" and rec["invoice_no"] is None and len(rec["lines"]) == 2
    assert rec["total_base"] == D_("1231.20")                                     # (1200 − 120) × 1.14
    assert stock(env) == 0 and entry(db, "purchase_invoice", iid) == []
    no = s.post(iid, expected_version=rec["row_version"])
    assert no == "PUR-2026-00001"
    assert stock(env) == 10 and stock(env, env["item2"], env["branch"]) == 4
    assert avg(env) == D_("90")                                                    # 1000 − 100 (خصم الفاتورة)
    assert avg(env, env["item2"]) == D_("45")
    lines = entry(db, "purchase_invoice", iid)
    dr = {r["role_code"]: r["debit"] for r in lines if r["debit"]}
    assert sum(r["debit"] for r in lines) == sum(r["credit"] for r in lines) == D_("1231.20")
    assert dr["inventory"] == D_("1080.00") and dr["vat_in"] == D_("151.20")
    cr = [r for r in lines if r["credit"]]
    assert len(cr) == 1 and cr[0]["supplier_id"] == env["sup"] and cr[0]["currency_code"] is None
    assert s.supplier_balance(env["sup"])["base"] == D_("1231.20")
    with pytest.raises(BusinessError, match="مرحّلة"):
        s.save_draft(iid, data(env, [line(env)]))


def test_service_item_statement_and_units(env):
    s, db = env["service"], env["db"]
    box = db.fetch_value("SELECT id FROM inv.units WHERE NOT is_default ORDER BY id LIMIT 1")
    db.execute("INSERT INTO inv.item_units (item_id, unit_id, factor) VALUES (%s, %s, 20)", [env["item2"], box])
    iid = s.save_draft(None, data(env, [line(env, env["item2"], "2", "800", unit_id=box),
                                        line(env, env["svc"], "1", "2500", warehouse_id=env["virtual"])]), post=True)
    assert stock(env, env["item2"]) == 40 and avg(env, env["item2"]) == D_("40")  # 1600 ÷ 40
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'purchase_invoice' "
                          "AND source_id = %s", [iid]) == 1                        # الخدمي مالوش حركة
    roles = {r["role_code"] for r in entry(db, "purchase_invoice", iid) if r["debit"]}
    assert "cogs" in roles and "inventory" in roles                                 # الخدمي على «تكلفة» النوع
    assert s.get_invoice(iid)["lines"][1]["warehouse_id"] is None                  # الخدمي من غير مخزن
    st = s.save_draft(None, data(env, [line(env, qty="5", price="10")], stock_kind="statement",
                                 prices_include_vat=True), post=True)
    rec = s.get_invoice(st)
    assert rec["vat_amount"] == 0 and rec["lines"][0]["tax_code_id"] is None and not rec["prices_include_vat"]
    assert stock(env, kind="statement") == 5 and stock(env) == 0
    assert all(r["role_code"] != "vat_in" for r in entry(db, "purchase_invoice", st))


def test_cash_invoice_payment_entry(env):
    s, db = env["service"], env["db"]
    iid = s.save_draft(None, data(env, [line(env, qty="2", price="500")], pay_kind="cash",
                                  source_kind="treasury", treasury_id=env["treasury"]), post=True)
    rec = s.get_invoice(iid)
    assert rec["due_date"] is None and rec["pay_kind"] == "cash"
    pay = entry(db, "purchase_payment", iid)
    assert [(r["debit"], r["credit"]) for r in pay] == [(D_("1140.00"), 0), (0, D_("1140.00"))]
    assert pay[0]["supplier_id"] == env["sup"]
    assert s.supplier_balance(env["sup"])["base"] == 0                             # اتقفل بالصرف
    s.unpost(iid)
    assert entry(db, "purchase_payment", iid) == [] and entry(db, "purchase_invoice", iid) == []
    assert stock(env) == 0 and s.get_invoice(iid)["invoice_no"] == "PUR-2026-00001"
    with pytest.raises(BusinessError, match="الخزنة"):
        s.save_draft(None, data(env, [line(env)], pay_kind="cash"))


def test_single_foreign_currency(env):
    s, db = env["service"], env["db"]
    d = s.supplier_defaults(env["usd_sup"], D1)
    assert d["currency_code"] == "USD" and d["fx_rate"] == D_("48.5") and d["tax_code_id"] == env["exempt"]
    assert d["due_date"] == D1 + dt.timedelta(days=30)
    iid = s.save_draft(None, data(env, [line(env, qty="2", price="106", tax_code_id=env["exempt"])],
                                  sup=env["usd_sup"], currency_code="USD", fx_rate="48.3"), post=True)
    rec = s.get_invoice(iid)
    assert rec["total_amount"] == D_("212.00") and rec["total_base"] == D_("10239.60")
    assert avg(env) == D_("5119.8")
    lines = entry(db, "purchase_invoice", iid)
    payable = [r for r in lines if r["credit"]][0]
    assert payable["currency_code"] == "USD" and payable["fx_rate"] == D_("48.3")
    assert payable["amount_fc"] == D_("212.00") and payable["credit"] == D_("10239.60")
    bal = s.supplier_balance(env["usd_sup"])["by_currency"]
    assert bal == [{"currency_code": "USD", "amount": D_("212.00"), "amount_base": D_("10239.60")}]
    with pytest.raises(BusinessError, match="سعر صرف"):
        s.save_draft(None, data(env, [line(env)], currency_code="USD", fx_rate=""))


def test_multi_currency_supplier_line_per_currency(env):
    s, db = env["service"], env["db"]
    lines = [line(env, qty="2", price="100", currency_code="USD", fx_rate="50"),
             line(env, env["item2"], "1", "10", currency_code="EUR", fx_rate="52"),
             line(env, env["item2"], "1", "100", currency_code="EGP")]
    iid = s.save_draft(None, data(env, lines, currency_mode="multi", currency_code="USD"), post=True)
    rec = s.get_invoice(iid)
    assert rec["currency_mode"] == "multi" and rec["currency_code"] == "EGP" and rec["fx_rate"] == 1
    # 200$×50 + 10€×52 + 100 = 10,620 → + 14%
    assert rec["net_base"] == D_("10620.00") and rec["total_base"] == D_("12106.80")
    payable = [r for r in entry(db, "purchase_invoice", iid) if r["credit"]]
    assert {(r["currency_code"], r["amount_fc"], r["credit"]) for r in payable} == {
        ("USD", D_("228.00"), D_("11400.00")), ("EUR", D_("11.40"), D_("592.80")), (None, None, D_("114.00"))}
    by = {r["currency_code"]: r["amount"] for r in s.supplier_balance(env["sup"])["by_currency"]}
    assert by == {"USD": D_("228.00"), "EUR": D_("11.40"), "EGP": D_("114.00")}
    assert avg(env) == D_("5000") and avg(env, env["item2"]) == D_("310")
    with pytest.raises(BusinessError, match="مالهاش خصم فاتورة"):
        s.save_draft(None, data(env, lines, currency_mode="multi", discount_amount="10"))
    review = s.review(data(env, lines, currency_mode="multi"))
    assert any("متعدد العملات" in c[1] for c in review["checks"])
    assert set(review["calc"]["by_currency"]) == {"USD", "EUR", "EGP"}


def test_virtual_duplicate_due_and_project(env):
    s, db = env["service"], env["db"]
    with pytest.raises(BusinessError, match="وهمي"):
        s.save_draft(None, data(env, [line(env, warehouse_id=env["virtual"])]))
    s.save_draft(None, data(env, [line(env)], supplier_invoice_no="7781"), post=True)
    with pytest.raises(BusinessError, match="اتسجل قبل كده"):
        s.save_draft(None, data(env, [line(env)], supplier_invoice_no=" 7781 "))
    # نفس الرقم لمورد تاني عادي
    s.save_draft(None, data(env, [line(env)], sup=env["usd_sup"], supplier_invoice_no="7781",
                            currency_code="USD", fx_rate="48.5"))
    with pytest.raises(BusinessError, match="الاستحقاق"):
        s.save_draft(None, data(env, [line(env)], due_date=D1 - dt.timedelta(days=1)))
    center = db.fetch_value("SELECT id FROM acc.cost_centers WHERE is_leaf AND is_active ORDER BY code LIMIT 1")
    pid = db.fetch_value("INSERT INTO acc.projects (code, name, cost_center_id, currency_code) "
                         "VALUES ('Itec-900', 'برج النخيل', %s, 'EGP') RETURNING id", [center])
    iid = s.save_draft(None, data(env, [line(env)], project_id=pid), post=True)
    assert all(r["project_id"] == pid for r in entry(db, "purchase_invoice", iid))


def test_unpost_negative_permission_and_delete(env, monkeypatch):
    s, ledger, db = env["service"], env["ledger"], env["db"]
    iid = s.save_draft(None, data(env, [line(env, qty="10", price="20")]), post=True)
    with db.atomic() as tx:                                   # اتصرف 6 بعدها (إذن صرف)
        ledger.post(tx, doc_type="sales_invoice", source_id=1, source_no="INV-1", movement_date=D2,
                    lines=[StockLine(env["item"], env["main"], -6, "invoice")])
    no_negative(monkeypatch)
    with pytest.raises(NegativeStockError):
        s.unpost(iid)
    assert s.get_invoice(iid)["status"] == "posted"
    monkeypatch.undo()
    s.unpost(iid)                                              # الأدمن عنده «من غير رصيد»
    rec = s.get_invoice(iid)
    assert rec["status"] == "draft" and rec["invoice_no"] == "PUR-2026-00001"
    with pytest.raises(BusinessError, match="رقم رسمي"):
        s.delete_invoice(iid)
    s.save_draft(iid, data(env, [line(env, qty="12", price="20")]), expected_version=rec["row_version"], post=True)
    assert s.get_invoice(iid)["invoice_no"] == "PUR-2026-00001" and stock(env) == 6
    draft = s.save_draft(None, data(env, [line(env)]))
    s.delete_invoice(draft)
    assert s.get_invoice(draft) is None


def test_database_guards(env):
    s, db = env["service"], env["db"]
    iid = s.save_draft(None, data(env, [line(env)]))
    with pytest.raises(psycopg.errors.RaiseException, match="وهمي"):
        db.execute("UPDATE purch.purchase_invoice_lines SET warehouse_id = %s WHERE invoice_id = %s",
                   [env["virtual"], iid])
    with pytest.raises(psycopg.errors.RaiseException, match="عملة السطر"):
        db.execute("UPDATE purch.purchase_invoice_lines SET currency_code = 'USD', fx_rate = 50, "
                   "net_base = round(net_amount * 50, 2), vat_base = round(vat_amount * 50, 2), "
                   "total_base = round(net_amount * 50, 2) + round(vat_amount * 50, 2) WHERE invoice_id = %s", [iid])
    with pytest.raises(psycopg.errors.CheckViolation):              # المبالغ بالجنيه لازم = بالعملة × السعر
        db.execute("UPDATE purch.purchase_invoice_lines SET net_base = net_base + 1 WHERE invoice_id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="مسودة الأول"):
        db.execute("INSERT INTO purch.purchase_invoices (invoice_date, fiscal_year_id, supplier_id, currency_code, "
                   "due_date, status, posted_at) SELECT '2026-03-01', id, %s, 'EGP', '2026-03-01', 'posted', now() "
                   "FROM core.fiscal_years WHERE year_code = '2026'", [env["sup"]])
    with pytest.raises(psycopg.errors.RaiseException, match="من غير حركات المخزون|من غير قيدها"):
        db.execute("UPDATE purch.purchase_invoices SET status = 'posted' WHERE id = %s", [iid])
    s.post(iid)
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة"):
        db.execute("UPDATE purch.purchase_invoice_lines SET qty = 1 WHERE invoice_id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة"):
        db.execute("UPDATE purch.purchase_invoices SET description = 'x' WHERE id = %s", [iid])
    with pytest.raises(psycopg.errors.RaiseException, match="مرحّلة"):
        db.execute("DELETE FROM purch.purchase_invoices WHERE id = %s", [iid])


def test_concurrent_edit_and_year_close(env):
    s, db = env["service"], env["db"]
    iid = s.save_draft(None, data(env, [line(env)]))
    v = s.get_invoice(iid)["row_version"]
    s.save_draft(iid, data(env, [line(env, qty="3")]), expected_version=v)          # جهاز 1
    with pytest.raises(ConcurrentEditError):
        s.save_draft(iid, data(env, [line(env, qty="4")]), expected_version=v)       # جهاز 2 بالإصدار القديم
    with pytest.raises(ConcurrentEditError):
        s.post(iid, expected_version=v)
    assert s.get_invoice(iid)["lines"][0]["qty"] == 3
    with pytest.raises(psycopg.errors.RaiseException, match="سند مسودة"):
        db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2026'")


def test_two_devices_post_same_item(env, base_settings):
    """جهازين بيرحّلوا فاتورتين على نفس الصنف في نفس اللحظة ← الاتنين يعدّوا ورا بعض والمتوسط صح ·
    وجهازين بيرحّلوا نفس الفاتورة ← واحد بس يعدّي."""
    from dataclasses import replace
    import threading

    s, db = env["service"], env["db"]
    a = s.save_draft(None, data(env, [line(env, qty="10", price="100")]))
    b = s.save_draft(None, data(env, [line(env, qty="30", price="200")]))
    c = s.save_draft(None, data(env, [line(env, env["item2"], "1", "5")]))
    other = Database(replace(base_settings, db_name=db.settings.db_name))
    results: list[str] = []

    def run(service, iid, version=None):
        try:
            service.post(iid, expected_version=version)
            results.append("ok")
        except ConcurrentEditError:
            results.append("conflict")
        except BusinessError as exc:
            results.append(f"err:{exc}")

    try:
        threads = [threading.Thread(target=run, args=(s, a)),
                   threading.Thread(target=run, args=(PurchaseInvoiceService(other), b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(results) == ["ok", "ok"]
        assert stock(env) == 40 and avg(env) == D_("175")                       # (1000 + 6000) ÷ 40
        numbers = {s.get_invoice(a)["invoice_no"], s.get_invoice(b)["invoice_no"]}
        assert numbers == {"PUR-2026-00001", "PUR-2026-00002"}
        results.clear()
        v = s.get_invoice(c)["row_version"]
        threads = [threading.Thread(target=run, args=(s, c, v)),
                   threading.Thread(target=run, args=(PurchaseInvoiceService(other), c, v))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    finally:
        other.close()
    assert sorted(results) == ["conflict", "ok"] or sorted(results)[0].startswith("err:") and "ok" in results
    assert s.get_invoice(c)["invoice_no"] == "PUR-2026-00003"


def test_review_checks_price_warning_and_list(env):
    s = env["service"]
    s.save_draft(None, data(env, [line(env, qty="10", price="100")], supplier_invoice_no="A1"), post=True)
    r = s.review(data(env, [line(env, qty="1", price="130"), line(env, env["item2"], "1", "0")],
                      supplier_invoice_no="A1"))
    texts = " | ".join(c[1] for c in r["checks"])
    assert "أكتر من 20%" in texts and "السعر صفر" in texts and "اتسجل قبل كده" in texts
    assert not r["ready"] and r["entry"] and r["supplier"]["after"] - r["supplier"]["before"] == D_("148.20")
    st = {x["item_id"]: x for x in r["stock"]}
    assert st[env["item"]]["qty_before"] == 10 and st[env["item"]]["qty_after"] == 11
    assert st[env["item"]]["avg_after"] == D_("102.727273")
    ok = s.review(data(env, [line(env, qty="1", price="110")], supplier_invoice_no="A2", e_invoice_no="EG-1"))
    assert ok["ready"]
    assert s.count_invoices(D1, D3)["total"] == 1
    assert [r["invoice_no"] for r in s.list_invoices(D1, D3, keyword="PUR-2026-00001")] == ["PUR-2026-00001"]
    assert len(s.list_invoices(D1, D3, keyword="كابل")) == 1


# ---- الشاشة (النموذج 9) ---------------------------------------------------------
def _screen(env):
    from app.ui.screens.purchase_invoices_screen import PurchaseInvoicesScreen

    return PurchaseInvoicesScreen(service=env["service"])


def _add(screen, item_id, qty, price, **kw):
    screen.item_edit.set_value(item_id)
    screen.add_qty.setText(qty)
    screen.add_price.setText(price)
    for key, value in kw.items():
        getattr(screen, key).setText(value)
    screen.add_line()


def test_screen_new_add_update_save_post_unpost(env):
    from app.ui.screens.purchase_invoices_screen import C_NAME, C_TOTAL

    s = env["service"]
    screen = _screen(env)
    assert screen.current_id is None and screen.mode == "view"
    screen.new_record()
    assert screen.mode == "new" and screen.wh_combo.currentData() == env["main"]
    screen.supplier_edit.set_value(env["sup"])
    assert screen.due_edit.date() == screen.date_edit.date().addDays(45)
    screen.sinv_edit.setText("9001")
    _add(screen, env["item"], "10", "100")
    _add(screen, env["item"], "5", "100")                                       # نفس الصنف والسعر بيتجمّع
    _add(screen, env["item2"], "2", "50")
    assert len(screen._lines) == 2 and screen._lines[0]["qty"] == 15
    screen._refresh_review()
    assert screen.lines_table.rowCount() == 2
    assert screen.lines_table.item(0, C_TOTAL).text() == "1,710.00"
    assert screen.net_value.text() == "1,824.00" and screen.vat_value.text() == "224.00"
    screen._on_row(1)                                                           # السطر بينزل تحت للتعديل
    assert screen.add_button.text().endswith("تحديث السطر") and screen.add_qty.text() == "2"
    screen.add_qty.setText("4")
    screen.add_line()
    assert screen._lines[1]["qty"] == 4 and screen._sel == -1
    screen.disc_amount_edit.setText("100")
    screen._on_disc_amount()
    assert screen.net_value.text() == "1,824.00"                                # 1700 − 100 + 14%
    screen.save()
    rec = screen.current_record
    assert screen.mode == "view" and rec["status"] == "draft" and rec["discount_amount"] == 100
    assert rec["total_base"] == D_("1824.00") and len(rec["lines"]) == 2
    screen.post_invoice()
    rec = screen.current_record
    assert rec["status"] == "posted" and rec["invoice_no"] == "PUR-2026-00001"
    assert not screen.edit_button.isEnabled() and screen.unpost_button.isEnabled()
    assert "مرحّلة" in screen.status_badge.text() and stock(env) == 15
    screen.unpost_invoice()
    assert screen.current_record["status"] == "draft" and stock(env) == 0
    assert screen.edit_button.isEnabled() and not screen.delete_button.isEnabled()   # خدت رقم
    # خطأ في السطر بيبان تحت اسمه
    screen.edit_record()
    screen._lines[0]["warehouse_id"] = env["virtual"]
    screen._refresh_review()
    assert "وهمي" in screen.lines_table.item(0, C_NAME).text() and "✖" in screen.review_label.text()
    screen.cancel()
    screen.close()
    screen.deleteLater()


def test_screen_currency_multi_and_duplicate(env):
    screen = _screen(env)
    screen.new_record()
    screen.supplier_edit.set_value(env["usd_sup"])
    assert screen.currency_combo.currentData() == "USD" and screen.rate_edit.text() == "48.5"
    _add(screen, env["item"], "2", "100")
    screen._refresh_review()
    assert screen.egp_box.isVisibleTo(screen) and "9,700.00" in screen.egp_label.text()
    assert "(USD)" in screen.net_caption.text()
    from app.ui.screens.purchase_invoices_screen import MULTI
    screen._set_combo(screen.currency_combo, MULTI)
    assert screen.add_cur_box.isVisibleTo(screen) and not screen.disc_amount_edit.isEnabled()
    screen._set_combo(screen.add_cur, "EUR")
    assert screen.add_rate.text() == "52.8"
    _add(screen, env["item2"], "1", "10")
    screen._lines[0]["currency_code"], screen._lines[0]["fx_rate"] = "USD", D_("48.5")
    screen._refresh_review()
    assert "USD" in screen.egp_label.text() and "EUR" in screen.egp_label.text()
    screen.save()
    rec = screen.current_record
    assert rec["currency_mode"] == "multi" and {ln["currency_code"] for ln in rec["lines"]} == {"USD", "EUR"}
    screen.duplicate_invoice()
    assert screen.mode == "new" and len(screen._lines) == 2 and screen.sinv_edit.text() == ""
    screen.save()
    assert screen.current_record["id"] != rec["id"] and len(screen.current_record["lines"]) == 2
    screen.close()
    screen.deleteLater()


def test_screen_cash_source_roundtrip(env):
    s = env["service"]
    iid = s.save_draft(None, data(env, [line(env, qty="1", price="10")], pay_kind="cash", source_kind="treasury",
                                  treasury_id=env["treasury"]))
    screen = _screen(env)
    screen.load(iid)
    assert not screen.source_box.isHidden() and screen.due_box.isHidden()          # في تبويب «الدفع والعملة»
    assert "موقوف" not in screen.source_combo.currentText()
    got = screen.collect()
    assert (got["pay_kind"], got["source_kind"], got["treasury_id"]) == ("cash", "treasury", env["treasury"])
    screen.edit_record()
    screen.post_invoice()
    assert screen.current_record["status"] == "posted" and entry(env["db"], "purchase_payment", iid)
    screen.close()
    screen.deleteLater()
