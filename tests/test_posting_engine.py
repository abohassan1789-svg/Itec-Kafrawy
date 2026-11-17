"""اختبارات «محرك الترحيل المركزي» (0032: قواعد الترحيل · الحساب من الافتراضي / نوع الصنف وأبوه / مجموعة الطرف /
كود الضريبة / الخزنة / البنك / المصروف / المستند · الجنب والسالب · التجميع · فرق التقريب على سطر واحد · الرفض بخطأ
عربي · قيد واحد لكل مستند وإعادة الترحيل · إلغاء الترحيل بيمسح القيد · سجل التعديلات · جهازين)."""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal

import psycopg
import pytest

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError
from app.services.journal_service import JournalService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.posting_service import PostingLine as L
from app.services.posting_service import PostingService

D = dt.date(2026, 10, 31)


@pytest.fixture()
def env(temp_db):
    migrator.migrate(temp_db)
    temp_db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
                    "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    AuthService(temp_db).seed_admin()
    PermissionsSyncService(temp_db).sync()
    SESSION.clear()
    assert AuthService(temp_db).login(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD).ok
    yield {"db": temp_db, "engine": PostingService(temp_db)}
    SESSION.clear()


def one(db, sql, *params):
    return db.fetch_value(sql, list(params))


def acc(db, code):
    return one(db, "SELECT id FROM acc.accounts WHERE code = %s", code)


def cc(db, code="11"):
    return one(db, "SELECT id FROM acc.cost_centers WHERE code = %s", code)


def default(db, key):
    return one(db, "SELECT account_id FROM acc.default_accounts WHERE key = %s", key)


def customer(db):
    return ("customer", one(db, "SELECT id FROM party.customers WHERE code = '0'"))


def supplier(db):
    return ("supplier", one(db, "SELECT id FROM party.suppliers ORDER BY seq LIMIT 1"))


def vat14(db):
    return one(db, "SELECT id FROM acc.tax_codes WHERE code = 'VAT14'")


def lines_of(db, eid):
    return db.fetch_all("SELECT l.line_no, a.code, l.debit, l.credit, l.customer_id, l.supplier_id "
                        "FROM acc.journal_lines l JOIN acc.accounts a ON a.id = l.account_id "
                        "WHERE l.entry_id = %s ORDER BY l.line_no", [eid])


def invoice(db, total="1140", net="1000", vat="140", cost="600", category=None):
    return [L("receivable", total, party=customer(db)), L("revenue", net, category_id=category),
            L("vat", vat, tax_code_id=vat14(db)), L("cogs", cost, category_id=category),
            L("inventory", cost, category_id=category)]


def post(env, doc_type="sales_invoice", source_id=1, no="INV-2026-00001", lines=None, d=D, **kw):
    db, engine = env["db"], env["engine"]
    kw.setdefault("cost_center_id", cc(db))
    with db.atomic() as tx:
        return engine.post_document(tx, doc_type=doc_type, source_id=source_id, source_no=no, entry_date=d,
                                    description=kw.pop("description", "فاتورة بيع للعميل النقدي"),
                                    lines=invoice(db) if lines is None else lines, **kw)


def category(db, name, parent=None, sales=None):
    return one(db, "INSERT INTO inv.item_categories (seq, name, parent_id, sales_account_id) "
                   "VALUES ((SELECT coalesce(max(seq), 0) + 1 FROM inv.item_categories), %s, %s, %s) RETURNING id",
               name, parent, sales)


# ---- القواعد ----------------------------------------------------------------
def test_rules_integrity(env):
    db = env["db"]
    docs = {r["code"] for r in db.fetch_all("SELECT code FROM acc.posting_doc_types")}
    with_rules = {r["doc_type"] for r in db.fetch_all("SELECT DISTINCT doc_type FROM acc.posting_rules")}
    assert docs - with_rules == {"manual"}
    # كل مستند فيه جنب مدين وجنب دائن
    for doc in with_rules:
        sides = {r["side"] for r in db.fetch_all("SELECT side FROM acc.posting_rules WHERE doc_type = %s", [doc])}
        assert sides == {"dr", "cr"}, doc
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, sort_order) "
                   "VALUES ('receipt', 'x', 'x', 'dr', 'default', 9)")              # الافتراضي من غير مفتاح
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO acc.posting_rules (doc_type, role, name_ar, side, account_source, sort_order) "
                   "VALUES ('receipt', 'y', 'y', 'dr', 'party', 9)")                # الطرف من غير نوعه
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("INSERT INTO acc.journal_entries (entry_date, fiscal_year_id, description, source_type, "
                   "source_id, source_no) VALUES (%s, (SELECT id FROM core.fiscal_years WHERE year_code = '2026'), "
                   "'x', 'xyz', 1, 'X')", [D])


# ---- فاتورة البيع -------------------------------------------------------------
def test_sales_invoice(env):
    db, engine = env["db"], env["engine"]
    eid = post(env)
    e = db.fetch_one("SELECT * FROM acc.journal_entries WHERE id = %s", [eid])
    assert e["status"] == "posted" and e["entry_no"] is None and e["source_no"] == "INV-2026-00001"
    assert e["source_type"] == "sales_invoice" and e["total_debit"] == e["total_credit"] == Decimal("1740.00")
    rows = lines_of(db, eid)
    codes = {r["code"]: (r["debit"], r["credit"]) for r in rows}
    assert codes["1103"] == (Decimal("1140.00"), 0) and rows[0]["customer_id"] == customer(db)[1]
    vat_account = one(db, "SELECT coalesce(sales_account_id, %s) FROM acc.tax_codes WHERE code = 'VAT14'",
                      default(db, "vat_out"))
    sales_code = one(db, "SELECT code FROM acc.accounts WHERE id = %s", default(db, "sales"))
    vat_code = one(db, "SELECT code FROM acc.accounts WHERE id = %s", vat_account)
    assert codes[sales_code] == (0, Decimal("1000.00")) and codes[vat_code] == (0, Decimal("140.00"))
    assert len(rows) == 5
    # القيد بيتعرض برقم المستند في «القيود اليومية» وقراية بس
    journal = JournalService(db)
    assert journal.get_entry(eid)["label"] == "INV-2026-00001"
    with pytest.raises(BusinessError, match="أوتوماتيك"):
        journal.unpost(eid)
    assert engine.entry_for("sales_invoice", 1)["id"] == eid and engine.entry_for("sales_invoice", 2) is None


def test_category_accounts(env):
    """النوع ← أقرب أب عنده الحساب ← الافتراضي · السطور المتشابهة بتتجمّع."""
    db, engine = env["db"], env["engine"]
    other = one(db, "SELECT id FROM acc.accounts WHERE account_type = 'revenue' AND is_leaf AND id <> %s "
                    "AND party_kind IS NULL AND role_code IS DISTINCT FROM 'sales_ret' ORDER BY code LIMIT 1",
                default(db, "sales"))
    parent = category(db, "T-Panel", sales=other)
    child = category(db, "T-Panel/BMS", parent=parent)
    plain = category(db, "T-Cables")
    built = engine.build("sales_invoice", [
        L("receivable", "300", party=customer(db)),
        L("revenue", "100", category_id=child), L("revenue", "50", category_id=child),
        L("revenue", "150", category_id=plain)], cost_center_id=cc(db))
    by_account = {(r["account_id"], r["credit"]) for r in built if r["credit"]}
    assert by_account == {(other, Decimal("150.00")), (default(db, "sales"), Decimal("150.00"))}
    assert len(built) == 3 and [r["line_no"] for r in built] == [1, 2, 3]


def test_document_cost_center(env):
    """مركز تكلفة المستند بيتحط على سطور الإيرادات والمصروفات بس · أمر التوريد على كل السطور · من غير مركز ← خطأ."""
    db, engine = env["db"], env["engine"]
    # 0033 (رد (ج)): المبيعات من غير «لازم مركز تكلفة» — بنرجّعها هنا عشان نختبر الرفض
    db.execute("UPDATE acc.accounts SET requires_cost_center = true WHERE code = '4101'")
    with pytest.raises(BusinessError, match="«المبيعات»: الحساب «4101 .*» لازم يتكتب عليه مركز تكلفة"):
        engine.build("sales_invoice", invoice(db))
    project = one(db, "INSERT INTO acc.projects (code, name, cost_center_id, currency_code) "
                      "VALUES ('Itec-900', 'تجربة', %s, 'EGP') RETURNING id", cc(db))
    built = engine.build("sales_invoice", invoice(db), cost_center_id=cc(db), project_id=project)
    centers = {r["role"]: r["cost_center_id"] for r in built}
    assert centers["receivable"] is None and centers["vat"] is None and centers["inventory"] is None
    assert centers["revenue"] == centers["cogs"] == cc(db)
    assert {r["project_id"] for r in built} == {project}
    # مركز على السطر نفسه بيكسب
    built = engine.build("sales_invoice", [L("receivable", "10", party=customer(db)),
                                           L("revenue", "10", cost_center_id=cc(db, "12"))], cost_center_id=cc(db))
    assert built[1]["cost_center_id"] == cc(db, "12")


def test_rounding_and_sides(env):
    db, engine = env["db"], env["engine"]
    cats = [category(db, f"C{i}") for i in range(3)]
    other = one(db, "SELECT id FROM acc.accounts WHERE account_type = 'revenue' AND is_leaf AND id <> %s "
                    "AND party_kind IS NULL ORDER BY code LIMIT 1", default(db, "sales"))
    db.execute("UPDATE inv.item_categories SET sales_account_id = %s WHERE id = %s", [other, cats[1]])
    # 33.333 × 3 = 99.999 ← بعد التقريب 33.33 + 33.33 = 66.66 + … ← الفرق قرش بيروح على أكبر سطر دائن
    built = engine.build("sales_invoice", [
        L("receivable", "100", party=customer(db)),
        L("revenue", "33.333", category_id=cats[0], description="أ"),
        L("revenue", "33.333", category_id=cats[1], description="ب"),
        L("revenue", "33.334", category_id=cats[2], description="ج")], cost_center_id=cc(db))
    debit = sum(r["debit"] for r in built)
    credit = sum(r["credit"] for r in built)
    assert debit == credit == Decimal("100.00")
    assert sum(r["rounding"] for r in built) == Decimal("0.01")
    # فرق كبير ← المستند فيه غلط ← مايترحّلش
    with pytest.raises(BusinessError, match="مش متوازن .*الفرق 1.00"):
        engine.build("sales_invoice", [L("receivable", "101", party=customer(db)), L("revenue", "100")],
                     cost_center_id=cc(db))
    # السالب بيقلب الجنب (عجز الجرد) · الصفر بيتشال
    built = engine.build("stock_adjustment", [L("inventory", "-50"), L("adjustment", "-50"), L("inventory", 0)])
    assert [(r["role"], r["debit"], r["credit"]) for r in built] == [
        ("inventory", 0, Decimal("50.00")), ("adjustment", Decimal("50.00"), 0)]
    with pytest.raises(BusinessError, match="كل المبالغ صفر"):
        engine.build("stock_adjustment", [L("inventory", "0")])


def test_errors_in_arabic(env):
    db, engine = env["db"], env["engine"]
    with pytest.raises(BusinessError, match="لازم يتحدد العميل"):
        engine.build("sales_invoice", [L("receivable", "10"), L("revenue", "10")])
    with pytest.raises(BusinessError, match="الطرف لازم يبقى العميل — مش المورد"):
        engine.build("sales_invoice", [L("receivable", "10", party=supplier(db)), L("revenue", "10")])
    with pytest.raises(BusinessError, match="دور السطر «xyz» مش معروف في قواعد ترحيل «فاتورة بيع»"):
        engine.build("sales_invoice", [L("xyz", "10")])
    with pytest.raises(BusinessError, match="مالوش قواعد ترحيل"):
        engine.build("manual", [L("line", "10")])
    with pytest.raises(BusinessError, match="لازم تتحدد الخزنة"):
        engine.build("receipt", [L("cash", "10"), L("receivable", "10", party=customer(db))])
    # الحساب الافتراضي مش متحدد ← اسمه واسم الشاشة
    db.execute("UPDATE acc.default_accounts SET account_id = NULL WHERE key = 'disc_allowed'")
    with pytest.raises(BusinessError, match="«خصم مسموح به»: الحساب الافتراضي .* مش متحدد — حدده من شاشة «الحسابات الافتراضية»"):
        engine.build("sales_invoice", [L("receivable", "90", party=customer(db)), L("discount", "10"),
                                       L("revenue", "100")])
    # حساب من المستند: رئيسي · موقوف · مالوش طرف · لازم مركز تكلفة
    with pytest.raises(BusinessError, match="رئيسي"):
        engine.build("opening_balance", [L("line", "10", account_id=acc(db, "52")), L("opening", "10")])
    db.execute("UPDATE acc.accounts SET is_active = false WHERE code = '1201'")
    with pytest.raises(BusinessError, match="«رصيد الحساب»: الحساب «1201 .*» موقوف"):
        engine.build("opening_balance", [L("line", "10", account_id=acc(db, "1201")), L("opening", "10")])
    with pytest.raises(BusinessError, match="مالوش طرف — العميل مايتكتبش عليه"):
        engine.build("opening_balance", [L("line", "10", account_id=acc(db, "5202"), party=customer(db)),
                                         L("opening", "10")])
    with pytest.raises(BusinessError, match="حساب رقابة — لازم يتحدد المورد"):
        engine.build("opening_balance", [L("line", "-10", account_id=acc(db, "2101")), L("opening", "-10")])
    db.execute("UPDATE acc.accounts SET requires_cost_center = true WHERE code = '5203'")
    with pytest.raises(BusinessError, match="لازم يتكتب عليه مركز تكلفة"):
        engine.build("opening_balance", [L("line", "10", account_id=acc(db, "5203")), L("opening", "10")])
    # السنة المقفولة ← رسالة القاعدة بالعربي · والمستند كله بيرجع
    db.execute("UPDATE core.fiscal_years SET status = 'closed', closed_at = now() WHERE year_code = '2025'")
    with pytest.raises(BusinessError, match="مقفولة"):
        post(env, d=dt.date(2025, 5, 1))
    with pytest.raises(BusinessError, match="مفيش سنة مالية"):
        post(env, d=dt.date(2030, 5, 1))
    assert one(db, "SELECT count(*) FROM acc.journal_entries") == 0
    with pytest.raises(BusinessError, match="رقم المستند مطلوب"):
        post(env, no="  ")


def test_cash_documents(env):
    """سند قبض (خزنة + بنك + خصم منبع) · سند صرف (مورد + مصروف) · أرصدة افتتاحية (السالب دائن)."""
    db, engine = env["db"], env["engine"]
    treasury = one(db, "SELECT id FROM cash.treasuries ORDER BY seq LIMIT 1")
    bank = one(db, "SELECT id FROM cash.bank_accounts ORDER BY id LIMIT 1")
    expense = one(db, "SELECT id FROM exp.expense_types ORDER BY seq LIMIT 1")
    built = engine.build("receipt", [L("cash", "600", treasury_id=treasury), L("bank", "390", bank_account_id=bank),
                                     L("wht", "10"), L("receivable", "1000", party=customer(db))])
    assert [r["account_id"] for r in built[:2]] == [
        one(db, "SELECT account_id FROM cash.treasuries WHERE id = %s", treasury),
        one(db, "SELECT account_id FROM cash.bank_accounts WHERE id = %s", bank)]
    assert sum(r["debit"] for r in built) == sum(r["credit"] for r in built) == Decimal("1000.00")
    built = engine.build("payment", [L("payable", "500", party=supplier(db)),
                                     L("expense", "200", expense_type_id=expense),
                                     L("cash", "700", treasury_id=treasury)])
    assert built[1]["account_id"] == one(db, "SELECT account_id FROM exp.expense_types WHERE id = %s", expense)
    assert built[0]["supplier_id"] == supplier(db)[1]
    eid = post(env, doc_type="opening_balance", source_id=1, no="OB-2026", description="أرصدة أول المدة", lines=[
        L("line", "5000", account_id=acc(db, "1103"), party=customer(db)),
        L("line", "-2000", account_id=acc(db, "2101"), party=supplier(db)),
        L("opening", "3000")])
    rows = lines_of(db, eid)
    assert [(r["code"], r["debit"], r["credit"]) for r in rows][:2] == [
        ("1103", Decimal("5000.00"), 0), ("2101", 0, Decimal("2000.00"))]
    assert rows[2]["credit"] == Decimal("3000.00")


def test_repost_and_unpost(env):
    """قيد واحد لكل مستند: إعادة الترحيل بتعدّل نفس القيد · إلغاء الترحيل بيمسحه · كله في سجل التعديلات."""
    db, engine = env["db"], env["engine"]
    eid = post(env)
    again = post(env, no="INV-2026-00001", lines=invoice(db, total="2280", net="2000", vat="280"),
                 d=dt.date(2026, 11, 2), description="بعد التعديل")
    assert again == eid
    e = db.fetch_one("SELECT * FROM acc.journal_entries WHERE id = %s", [eid])
    assert e["total_debit"] == Decimal("2880.00") and e["entry_date"] == dt.date(2026, 11, 2)
    assert e["description"] == "بعد التعديل" and e["status"] == "posted"
    assert one(db, "SELECT count(*) FROM acc.journal_entries") == 1
    # مستند تاني بنفس الرقم في نوع تاني = قيد لوحده
    other = post(env, doc_type="sales_return", source_id=1, no="RET-2026-00001", lines=[
        L("sales_return", "100"), L("receivable", "100", party=customer(db))])
    assert other != eid
    with db.atomic() as tx:
        assert engine.unpost_document(tx, doc_type="sales_invoice", source_id=1) is True
    with db.atomic() as tx:
        assert engine.unpost_document(tx, doc_type="sales_invoice", source_id=1) is False
    assert db.fetch_one("SELECT id FROM acc.journal_entries WHERE id = %s", [eid]) is None
    log = db.fetch_all("SELECT action, details FROM sec.audit_log WHERE entity = 'acc.journal_entries' "
                       "AND entity_id = %s ORDER BY id", [str(eid)])
    assert [r["action"] for r in log] == ["post", "post", "unpost"]
    assert len(log[2]["details"]["lines"]["acc.journal_lines"]["before"]) == 5
    assert log[2]["details"]["extra"] == {"source_type": "sales_invoice", "source_id": 1}
    # الترحيل التاني بعد الإلغاء ← قيد جديد
    assert post(env) != eid
    # لو المستند وقع في النص كله بيرجع (rollback) — قيده القديم زي ما هو
    before = one(db, "SELECT row_version FROM acc.journal_entries WHERE source_type = 'sales_invoice'")
    with pytest.raises(BusinessError):
        post(env, lines=[L("receivable", "10", party=customer(db)), L("revenue", "20")])
    assert one(db, "SELECT row_version FROM acc.journal_entries WHERE source_type = 'sales_invoice'") == before


def test_two_devices(env):
    """جهازين بيرحّلوا نفس المستند الجديد في نفس اللحظة ← التاني بيستنى وبيترفض برسالة واضحة (مش قيدين)."""
    db = env["db"]
    other_db = Database(db.settings)
    other = PostingService(other_db)
    results: dict[str, object] = {}

    def worker():
        try:
            with other_db.atomic() as tx:
                results["id"] = other.post_document(tx, doc_type="sales_invoice", source_id=7, source_no="INV-7",
                                                    entry_date=D, description="x", lines=invoice(db),
                                                    cost_center_id=cc(db))
        except Exception as exc:  # noqa: BLE001
            results["error"] = exc

    try:
        thread = threading.Thread(target=worker)
        with db.atomic() as tx:
            env["engine"].post_document(tx, doc_type="sales_invoice", source_id=7, source_no="INV-7",
                                        entry_date=D, description="x", lines=invoice(db),
                                                    cost_center_id=cc(db))
            thread.start()
            thread.join(0.7)
            assert thread.is_alive()                          # مستني الـ unique index
        thread.join(10)
        assert isinstance(results.get("error"), BusinessError) and "جهاز تاني" in str(results["error"])
        assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE source_id = 7") == 1
        # إعادة الترحيل من الجهاز التاني بعد ما الأول خلص ← نفس القيد
        with other_db.atomic() as tx:
            second = other.post_document(tx, doc_type="sales_invoice", source_id=7, source_no="INV-7",
                                         entry_date=D, description="y", lines=invoice(db, cost="500"),
                                         cost_center_id=cc(db))
        assert one(db, "SELECT count(*) FROM acc.journal_entries WHERE source_id = 7") == 1
        assert one(db, "SELECT description FROM acc.journal_entries WHERE id = %s", second) == "y"
    finally:
        other_db.close()
