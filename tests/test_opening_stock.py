"""اختبارات «بضاعة أول المدة» (0045 + opening_stock_service): الحفظ سطر سطر بالإصدار · جهازين · الوهمي · الخدمي ·
مقفولة بعد ترحيل الأرصدة الافتتاحية · الترحيل بقيد المخزون (من نوع الصنف) + 34 وحركات دفتر المخزون والمتوسط ·
إلغاء الترحيل وصلاحية «من غير رصيد» · الاستيراد من إكسل (الأخطاء · المتكرر · الاستبدال) · منع السطر اليدوي على 1105 ·
شاشة الأصناف · الشاشة."""

from __future__ import annotations

import datetime as dt
import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from decimal import Decimal

import psycopg
import pytest
from openpyxl import Workbook, load_workbook
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.database import migrator
from app.database.db import Database
from app.security.session import SESSION
from app.services.auth_service import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USERNAME, AuthService
from app.services.errors import BusinessError, ConcurrentEditError, PermissionDenied
from app.services.opening_balance_service import OpeningBalanceService
from app.services.opening_stock_service import OpeningStockService
from app.services.permissions_sync_service import PermissionsSyncService
from app.services.stock_ledger_service import NegativeStockError, StockLedgerService, StockLine
from app.ui.common import messages

D = dt.date(2026, 1, 1)


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def env(qapp, temp_db, monkeypatch):
    migrator.migrate(temp_db)
    db = temp_db
    db.execute("INSERT INTO core.fiscal_years (year_code, start_date, end_date) VALUES "
               "('2025', '2025-01-01', '2025-12-31'), ('2026', '2026-01-01', '2026-12-31')")
    db.execute("UPDATE acc.opening_balances SET balance_date = %s", [D])
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
    env = {"db": db, "shown": shown, "service": OpeningStockService(db), "ob": OpeningBalanceService(db),
           "main": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_default"),
           "virtual": db.fetch_value("SELECT id FROM inv.warehouses WHERE is_virtual ORDER BY seq LIMIT 1"),
           "item": item(db, "كابل نحاس"), "item2": item(db, "مفتاح")}
    env["branch"] = db.fetch_value("INSERT INTO inv.warehouses (seq, name_ar) VALUES "
                                   "((SELECT max(seq) + 1 FROM inv.warehouses), 'مخزن الفرع') RETURNING id")
    yield env
    SESSION.clear()
    gc.collect()


def item(db, name, service=False, category=None):
    return db.fetch_value(
        "INSERT INTO inv.items (seq, name, unit_id, tax_code_id, is_service, category_id) VALUES "
        "((SELECT coalesce(max(seq), 0) + 1 FROM inv.items), %s, "
        "(SELECT id FROM inv.units WHERE is_default LIMIT 1), (SELECT id FROM acc.tax_codes WHERE code = 'VAT14'), %s, %s) "
        "RETURNING id", [name, service, category])


def code_of(db, item_id):
    return db.fetch_value("SELECT code FROM inv.items WHERE id = %s", [item_id])


def wh_code(db, wh):
    return db.fetch_value("SELECT code FROM inv.warehouses WHERE id = %s", [wh])


def acc(db, code):
    return db.fetch_value("SELECT id FROM acc.accounts WHERE code = %s", [code])


def row_for(rows, item_id):
    return next(r for r in rows if r["item_id"] == item_id)


def change(row, qty, cost=""):
    """تغيير من صف الجدول (زي ما الشاشة بتبعته)."""
    return {"item_id": row["item_id"], "warehouse_id": row["warehouse_id"], "stock_kind": row["stock_kind"],
            "line_id": row["line_id"], "row_version": row["row_version"] or 0, "qty": qty, "unit_cost": cost,
            "label": row["name"]}


def put(env, item_id, qty, cost, wh=None, kind="invoice"):
    rows = env["service"].grid(wh or env["main"], kind)
    return env["service"].save([change(row_for(rows, item_id), qty, cost)])


def line(env, item_id, wh=None, kind="invoice"):
    return env["db"].fetch_one("SELECT * FROM inv.opening_stock_lines WHERE item_id = %s AND warehouse_id = %s "
                               "AND stock_kind = %s", [item_id, wh or env["main"], kind])


def post(env):
    env["ob"].post()


def other_device(env):
    db2 = Database(env["db"].settings)
    return db2, OpeningStockService(db2)


# ---- الحفظ والإصدار -----------------------------------------------------------------
def test_grid_save_update_delete(env):
    rows = env["service"].grid(env["main"], "invoice")
    assert {r["item_id"] for r in rows} >= {env["item"], env["item2"]}
    assert all(r["line_id"] is None for r in rows)
    assert put(env, env["item"], "10", "12.5") == 1
    l = line(env, env["item"])
    assert (l["qty"], l["unit_cost"], l["row_version"]) == (Decimal("10"), Decimal("12.5"), 1)
    r = row_for(env["service"].grid(env["main"], "invoice"), env["item"])
    assert r["value"] == Decimal("125.00")
    assert env["service"].save([change(r, "8", "12.5")]) == 1
    assert line(env, env["item"])["row_version"] == 2
    r = row_for(env["service"].grid(env["main"], "invoice"), env["item"])
    assert env["service"].save([change(r, "8", "12.5")]) == 0            # من غير تغيير
    assert env["service"].save([change(r, "", "")]) == 1                 # الفاضي = امسح
    assert line(env, env["item"]) is None
    s = env["service"].summary()
    assert s["lines"] == 0 and s["locked"] is False
    audit = env["db"].fetch_all("SELECT action FROM sec.audit_log WHERE entity = 'inv.opening_stock_lines' ORDER BY id")
    assert [a["action"] for a in audit] == ["create", "update", "delete"]


def test_validation(env):
    with pytest.raises(BusinessError, match="بالسالب"):
        put(env, env["item"], "-1", "5")
    with pytest.raises(BusinessError, match="6 أرقام"):
        put(env, env["item"], "1.1234567", "5")
    with pytest.raises(BusinessError, match="مش رقم"):
        put(env, env["item"], "abc", "5")
    with pytest.raises(BusinessError, match="بالسالب"):
        put(env, env["item"], "1", "-5")
    assert put(env, env["item"], "٣", "") == 1                          # السعر صفر مسموح
    assert line(env, env["item"])["unit_cost"] == 0
    assert env["service"].summary()["zero_cost"] == 1
    service = item(env["db"], "تركيب", service=True)
    with pytest.raises(BusinessError, match="خدمي"):
        env["service"].save([{"item_id": service, "warehouse_id": env["main"], "stock_kind": "invoice",
                              "qty": "1", "unit_cost": "1", "row_version": 0}])
    with pytest.raises(psycopg.errors.RaiseException, match="عليه رصيد أول المدة"):
        env["db"].execute("UPDATE inv.items SET is_service = true WHERE id = %s", [env["item"]])


def test_virtual_qty_only_and_kinds(env):
    put(env, env["item"], "5", "99", wh=env["virtual"])
    assert line(env, env["item"], env["virtual"])["unit_cost"] == 0
    r = row_for(env["service"].grid(env["virtual"], "invoice"), env["item"])
    assert r["is_virtual"] and r["value"] == 0
    put(env, env["item"], "4", "10", kind="statement")
    put(env, env["item"], "6", "11")
    s = env["service"].summary()
    assert (s["lines"], s["items"]) == (3, 1)
    assert (s["invoice_value"], s["statement_value"], s["value"]) == (Decimal("66.00"), Decimal("40.00"),
                                                                      Decimal("106.00"))


def test_permissions(env):
    env["db"].execute("INSERT INTO sec.app_users (username, full_name, password_hash) VALUES ('u1', 'u1', 'x')")
    SESSION.clear()
    with pytest.raises(PermissionDenied):
        env["service"].save([{"item_id": env["item"], "warehouse_id": env["main"], "qty": "1"}])


# ---- جهازين ----------------------------------------------------------------------
def test_two_devices_different_items_both_saved(env):
    db2, s2 = other_device(env)
    try:
        rows1 = env["service"].grid(env["main"], "invoice")
        rows2 = s2.grid(env["main"], "invoice")
        env["service"].save([change(row_for(rows1, env["item"]), "1", "10")])
        s2.save([change(row_for(rows2, env["item2"]), "2", "20")])
        assert line(env, env["item"])["qty"] == 1 and line(env, env["item2"])["qty"] == 2
        # نفس السطر: التاني يترفض (الاتنين شافوه فاضي)
        third = item(env["db"], "لمبة")
        rows1 = env["service"].grid(env["main"], "invoice")
        rows2 = s2.grid(env["main"], "invoice")
        env["service"].save([change(row_for(rows1, third), "3", "5")])
        with pytest.raises(ConcurrentEditError):
            s2.save([change(row_for(rows2, third), "4", "5")])
        # نفس السطر بعد ما اتسجل: الإصدار القديم يترفض (تعديل ومسح)
        rows1 = env["service"].grid(env["main"], "invoice")
        rows2 = s2.grid(env["main"], "invoice")
        env["service"].save([change(row_for(rows1, third), "7", "5")])
        with pytest.raises(ConcurrentEditError):
            s2.save([change(row_for(rows2, third), "9", "5")])
        with pytest.raises(ConcurrentEditError):
            s2.save([change(row_for(rows2, third), "", "")])
        assert line(env, third)["qty"] == 7
        # جهاز مسح والتاني بيعدّل ← تعارض (اتمسح)
        rows2 = s2.grid(env["main"], "invoice")
        env["service"].save([change(row_for(env["service"].grid(env["main"], "invoice"), third), "", "")])
        with pytest.raises(ConcurrentEditError):
            s2.save([change(row_for(rows2, third), "1", "5")])
    finally:
        db2.close()


def test_failed_save_rolls_back_everything(env):
    rows = env["service"].grid(env["main"], "invoice")
    with pytest.raises(BusinessError):
        env["service"].save([change(row_for(rows, env["item"]), "5", "1"),
                             change(row_for(rows, env["item2"]), "-5", "1")])
    assert line(env, env["item"]) is None


# ---- الترحيل مع الأرصدة الافتتاحية ------------------------------------------------------
def test_post_with_opening_balances(env):
    db = env["db"]
    cat = db.fetch_value("INSERT INTO inv.item_categories (seq, name) VALUES ((SELECT coalesce(max(seq), 0) + 1 FROM inv.item_categories), 'كابلات') RETURNING id")
    cabled = item(db, "كابل 2 مم", category=cat)
    put(env, env["item"], "10", "100")                   # 1000
    put(env, env["item2"], "4", "25", kind="statement")  # 100
    put(env, cabled, "2", "0")                           # سعر صفر ← مالوش قيمة
    put(env, env["item"], "3", "", wh=env["virtual"])    # وهمي كمية بس
    doc = env["ob"].get_document()
    assert doc["sections"]["inv"] == {"lines": 4, "debit": Decimal("1100.00"), "credit": Decimal("0")}
    assert doc["total_debit"] == Decimal("1100.00")
    assert len(env["ob"].section_rows("inv")) == 4
    post(env)
    entry = db.fetch_all("SELECT a.code, l.debit, l.credit FROM acc.journal_lines l JOIN acc.accounts a "
                         "ON a.id = l.account_id JOIN acc.journal_entries e ON e.id = l.entry_id "
                         "WHERE e.source_type = 'opening_balance' ORDER BY a.code")
    assert {(e["code"], e["debit"], e["credit"]) for e in entry} == {("1105", Decimal("1100.00"), 0),
                                                                     ("34", 0, Decimal("1100.00"))}
    moves = db.fetch_all("SELECT item_id, warehouse_id, stock_kind, qty, value, movement_date FROM inv.stock_movements "
                         "WHERE source_type = 'opening_stock' ORDER BY line_no")
    assert len(moves) == 4 and all(m["movement_date"] == D for m in moves)
    ledger = StockLedgerService(db)
    assert ledger.average_cost(env["item"]) == Decimal("100")
    assert ledger.available(env["item"], env["virtual"]) == 3
    # مقفولة بعد الترحيل
    with pytest.raises(BusinessError, match="مترحّلة|مرحّل"):
        put(env, env["item"], "11", "100")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("DELETE FROM inv.opening_stock_lines")
    # إلغاء الترحيل بيطلّع الحركات
    env["ob"].unpost()
    assert db.fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'opening_stock'") == 0
    assert db.fetch_value("SELECT count(*) FROM acc.journal_entries WHERE source_type = 'opening_balance'") == 0
    put(env, env["item"], "11", "100")


def test_category_inventory_account(env):
    """نوع الصنف بحساب مخزون فرعي (110502) ← قيمته عليه · الباقي على الافتراضي (110501 بعد التفريع)."""
    db = env["db"]
    db.fetch_value("SELECT acc.split_account(%s)", [acc(db, "1105")])
    parent = acc(db, "1105")
    panels = db.fetch_value(
        "INSERT INTO acc.accounts (code, name_ar, parent_id, is_leaf, nature, classification, cash_flow, role_code, "
        "is_active) VALUES ('110502', 'مخزون لوحات', %s, true, 'dr', 'current', 'op', 'inventory', true) RETURNING id",
        [parent])
    cat = db.fetch_value("INSERT INTO inv.item_categories (seq, name, inventory_account_id) VALUES ((SELECT coalesce(max(seq), 0) + 1 FROM inv.item_categories), 'لوحات', %s) "
                         "RETURNING id", [panels])
    it = item(db, "لوحة توزيع", category=cat)
    put(env, it, "2", "50")
    put(env, env["item"], "1", "30")
    post(env)
    rows = db.fetch_all("SELECT a.code, l.debit FROM acc.journal_lines l JOIN acc.accounts a ON a.id = l.account_id "
                        "JOIN acc.journal_entries e ON e.id = l.entry_id WHERE e.source_type = 'opening_balance' "
                        "AND l.debit > 0 ORDER BY a.code")
    assert [(r["code"], r["debit"]) for r in rows] == [("110501", Decimal("30.00")), ("110502", Decimal("100.00"))]


def test_only_virtual_or_zero_no_entry(env):
    put(env, env["item"], "3", "", wh=env["virtual"])
    post(env)
    assert env["db"].fetch_value("SELECT count(*) FROM acc.journal_entries WHERE source_type = 'opening_balance'") == 0
    assert env["db"].fetch_value("SELECT status FROM acc.opening_balances") == "posted"
    assert env["db"].fetch_value("SELECT count(*) FROM inv.stock_movements WHERE source_type = 'opening_stock'") == 1
    env["ob"].unpost()


def test_unpost_negative_needs_permission(env, monkeypatch):
    db = env["db"]
    put(env, env["item"], "10", "100")
    post(env)
    with db.atomic() as tx:
        StockLedgerService(db).post(tx, doc_type="sales_invoice", source_id=1, source_no="S-1",
                                    movement_date=dt.date(2026, 2, 1),
                                    lines=[StockLine(env["item"], env["main"], Decimal("-6"), "invoice")])
    assert db.fetch_value("SELECT count(*) FROM sec.permissions WHERE permission_code = 'acc.opening_balances.negative'") == 1
    real_can = SESSION.can
    monkeypatch.setattr(SESSION, "can", lambda code: code != "acc.opening_balances.negative" and real_can(code))
    with pytest.raises(NegativeStockError):
        env["ob"].unpost()
    assert db.fetch_value("SELECT status FROM acc.opening_balances") == "posted"
    monkeypatch.setattr(SESSION, "can", real_can)
    env["ob"].unpost()                                   # بالصلاحية ← بيتلغي والبيع يفضل بالسالب
    assert StockLedgerService(db).available(env["item"], env["main"]) == -6


def test_no_manual_inventory_line(env):
    with pytest.raises(BusinessError, match="حساب مخزون"):
        env["ob"].save(None, [{"account_id": acc(env["db"], "1105"), "debit": "500", "credit": "",
                               "old_debit": 0, "old_credit": 0, "label": "المخزون", "new_row": True}])
    accounts = env["ob"].choices()["accounts"]
    assert all(a["code"] != "1105" for a in accounts)


# ---- الاستيراد من إكسل --------------------------------------------------------------
def _xlsx(tmp_path, rows, head=("كود الصنف", "اسم الصنف", "المخزن", "فاتورة / بيان", "الرصيد", "السعر"),
          name="stock.xlsx"):
    wb = Workbook()
    ws = wb.active
    ws.append(list(head))
    for r in rows:
        ws.append(list(r))
    path = tmp_path / name
    wb.save(path)
    return path


def test_import_review_errors(env, tmp_path):
    db = env["db"]
    svc = env["service"]
    c1, c2 = code_of(db, env["item"]), code_of(db, env["item2"])
    srv = code_of(db, item(db, "تركيب", service=True))
    main = wh_code(db, env["main"])
    path = _xlsx(tmp_path, [
        (c1, "", main, "فاتورة", 10, 5),                      # 2 ✔
        ("ITM-9999", "", main, "", 1, 1),                     # 3 كود مش موجود
        (c2, "", "مخزن مش موجود", "", 1, 1),                 # 4 مخزن مش موجود
        (srv, "", main, "", 1, 1),                            # 5 خدمي
        (c2, "", main, "بيان", 0, 1),                         # 6 رصيد صفر
        (c2, "", main, "فاتورة", 3, None),                    # 7 سعر ناقص
        (c1, "", main, "", 2, 5),                             # 8 متكرر مع 2
        (c2, "", "مخزن الفرع", "بيان", 2, 0),                 # 9 ✔ بالاسم وسعر صفر (تحذير)
        (c2, "", wh_code(db, env["virtual"]), "", 2, None),   # 10 ✔ وهمي من غير سعر
    ])
    rows = svc.read_excel(path)
    assert [r["row"] for r in rows] == list(range(2, 11))
    reviewed = svc.review(rows)
    errors = {r["row"]: r["errors"] for r in reviewed}
    assert errors[2] == [] and errors[9] == [] and errors[10] == []
    assert "مش موجود" in errors[3][0] and "مش موجود" in errors[4][0] and "خدمي" in errors[5][0]
    assert "أكبر من صفر" in errors[6][0] and "السعر ناقص" in errors[7][0] and "متكرر" in errors[8][0]
    assert next(r for r in reviewed if r["row"] == 9)["warnings"] == ["السعر صفر"]
    with pytest.raises(BusinessError, match="6 صف غلط"):
        svc.import_rows(reviewed)
    assert db.fetch_value("SELECT count(*) FROM inv.opening_stock_lines") == 0


def test_import_merge_and_replace(env, tmp_path):
    db = env["db"]
    svc = env["service"]
    c1, c2 = code_of(db, env["item"]), code_of(db, env["item2"])
    put(env, env["item"], "1", "1")
    put(env, env["item2"], "9", "9", wh=env["branch"])
    path = _xlsx(tmp_path, [(c1.lower(), "", wh_code(db, env["main"]), "فاتورة", 10, "5.5"),
                            (c2, "", wh_code(db, env["main"]), "بيان", 3, 2)])
    counts = svc.import_rows(svc.review(svc.read_excel(path)))
    assert counts == {"created": 1, "updated": 1, "deleted": 0}
    assert line(env, env["item"])["qty"] == 10 and line(env, env["item2"], env["branch"])["qty"] == 9
    counts = svc.import_rows(svc.review(svc.read_excel(path)), replace=True)
    assert counts == {"created": 2, "updated": 0, "deleted": 3}
    assert line(env, env["item2"], env["branch"]) is None
    assert db.fetch_value("SELECT count(*) FROM inv.opening_stock_lines") == 2
    # ناقصه عمود
    bad = _xlsx(tmp_path, [(c1, 1)], head=("الكود", "الكمية"), name="bad.xlsx")
    with pytest.raises(BusinessError, match="ناقصه عمود"):
        svc.read_excel(bad)
    # التصدير والنموذج الفاضي بنفس الأعمدة (التصدير يتستورد تاني)
    out = tmp_path / "out.xlsx"
    assert svc.export(out) == 2
    again = svc.review(svc.read_excel(out))
    assert all(not r["errors"] for r in again) and len(again) == 2
    tpl = tmp_path / "tpl.xlsx"
    svc.write_template(tpl)
    ws = load_workbook(tpl).worksheets[0]
    assert [c.value for c in ws[1]] == ["كود الصنف", "اسم الصنف", "المخزن", "فاتورة / بيان", "الرصيد", "السعر"]
    # بعد الترحيل الاستيراد مقفول
    post(env)
    with pytest.raises(BusinessError, match="مترحّلة"):
        svc.import_rows(svc.review(svc.read_excel(path)))


# ---- شاشة الأصناف -------------------------------------------------------------------
def item_payload(db, name, **extra):
    return {"name": name, "alt_name": None, "description": None, "category_id": None, "brand_id": None,
            "unit_id": db.fetch_value("SELECT id FROM inv.units WHERE is_default LIMIT 1"), "is_service": False,
            "default_warehouse_id": None,
            "tax_code_id": db.fetch_value("SELECT id FROM acc.tax_codes WHERE code = 'VAT14'"),
            "sale_price": "", "reorder_level": "", "notes": None, "is_active": True, "units": [], **extra}


def item_version(db, item_id):
    return db.fetch_value("SELECT row_version FROM inv.items WHERE id = %s", [item_id])


def test_item_service_opening(env):
    from app.services.item_service import ItemService

    db = env["db"]
    items = ItemService(db)
    new_id = items.save_item(None, item_payload(db, "قاطع 16 أمبير", opening={
        "warehouse_id": env["branch"], "stock_kind": "invoice", "qty": "5", "unit_cost": "40", "row_version": 0}))
    l = line(env, new_id, env["branch"])
    assert (l["qty"], l["unit_cost"]) == (5, 40)
    info = env["service"].item_line(new_id)
    assert info["line"]["id"] == l["id"] and info["warehouse_id"] == env["branch"]   # سطره هو اللي بيظهر
    version = item_version(db, new_id)
    # جهاز تاني عدّل الرصيد ← حفظ الصنف بالإصدار القديم للسطر بيترفض ومفيش حاجة بتتحفظ
    put(env, new_id, "6", "40", wh=env["branch"])
    with pytest.raises(ConcurrentEditError):
        items.save_item(new_id, item_payload(db, "قاطع 16 أمبير", notes="x", opening={
            "warehouse_id": env["branch"], "stock_kind": "invoice", "qty": "7", "unit_cost": "40",
            "line_id": l["id"], "row_version": l["row_version"]}), expected_version=version)
    assert db.fetch_value("SELECT notes FROM inv.items WHERE id = %s", [new_id]) is None
    l = line(env, new_id, env["branch"])
    items.save_item(new_id, item_payload(db, "قاطع 16 أمبير", opening={
        "warehouse_id": env["branch"], "stock_kind": "invoice", "qty": "7", "unit_cost": "41",
        "line_id": l["id"], "row_version": l["row_version"]}), expected_version=version)
    assert line(env, new_id, env["branch"])["qty"] == 7
    # خدمي عليه رصيد ← ممنوع · ومسح الرصيد + خدمي في نفس الحفظ ← يتحفظ
    version = item_version(db, new_id)
    l = line(env, new_id, env["branch"])
    with pytest.raises(BusinessError, match="خدمي"):
        items.save_item(new_id, item_payload(db, "قاطع 16 أمبير", is_service=True, opening={
            "warehouse_id": env["branch"], "stock_kind": "invoice", "qty": "7", "unit_cost": "41",
            "line_id": l["id"], "row_version": l["row_version"]}), expected_version=version)
    items.save_item(new_id, item_payload(db, "قاطع 16 أمبير", is_service=True, opening={
        "warehouse_id": env["branch"], "stock_kind": "invoice", "qty": "", "unit_cost": "",
        "line_id": l["id"], "row_version": l["row_version"]}), expected_version=version)
    assert line(env, new_id, env["branch"]) is None
    # بعد الترحيل: نفس الرصيد يعدّي · رصيد جديد يترفض
    other = items.save_item(None, item_payload(db, "مفتاح 3 خط", opening={
        "warehouse_id": env["main"], "stock_kind": "statement", "qty": "2", "unit_cost": "9", "row_version": 0}))
    post(env)
    l = line(env, other, kind="statement")
    items.save_item(other, item_payload(db, "مفتاح 3 خط", notes="بعد الترحيل", opening={
        "warehouse_id": env["main"], "stock_kind": "statement", "qty": "2", "unit_cost": "9",
        "line_id": l["id"], "row_version": l["row_version"]}), expected_version=item_version(db, other))
    with pytest.raises(BusinessError, match="مترحّلة"):
        items.save_item(other, item_payload(db, "مفتاح 3 خط", opening={
            "warehouse_id": env["main"], "stock_kind": "statement", "qty": "3", "unit_cost": "9",
            "line_id": l["id"], "row_version": l["row_version"]}), expected_version=item_version(db, other))


def test_items_screen_opening(env):
    from app.services.item_service import ItemService
    from app.ui.screens.items_screen import ItemsScreen

    db, shown = env["db"], env["shown"]
    screen = ItemsScreen(ItemService(db))
    screen.new_record()
    assert screen.mode == "new" and screen.opening_wh.currentData() == env["main"]
    assert screen.opening_kind.currentData() == "invoice" and screen.opening_qty.isEnabled()
    screen.name.setText("كشاف ليد")
    screen.set_opening("12", "30")
    assert "360.00" in screen.opening_value.text()
    screen.save()
    assert "saved" in shown, shown
    new_id = screen.current_id
    assert line(env, new_id)["qty"] == 12
    assert screen.opening_qty.text() == "12" and not screen.opening_qty.isEnabled()
    # سطر تاني من شاشة «بضاعة أول المدة» ← تنبيه
    put(env, new_id, "3", "30", wh=env["branch"])
    screen.load(new_id)
    assert "سطر تاني" in screen.opening_note.text()
    screen.edit_record()
    screen.set_opening("4", "31", warehouse_id=env["branch"])
    screen.save()
    assert line(env, new_id, env["branch"])["qty"] == 4 and line(env, new_id)["qty"] == 12
    # الوهمي: السعر مقفول
    screen.edit_record()
    screen.opening_wh.setCurrentIndex(screen.opening_wh.findData(env["virtual"]))
    assert not screen.opening_cost.isEnabled()
    screen.cancel()
    post(env)
    screen.load(new_id)
    screen.edit_record()
    assert not screen.opening_qty.isEnabled() and "مترحّلة" in screen.opening_note.text()
    screen.cancel()
    screen.close()


# ---- الشاشة ------------------------------------------------------------------------
def test_screen(env, tmp_path, monkeypatch):
    from app.ui.screens.opening_stock_screen import C_QTY, C_VALUE, OpeningStockScreen

    db, shown = env["db"], env["shown"]
    screen = OpeningStockScreen(OpeningStockService(db))
    assert screen.current_id == 1 and screen.mode == "view"
    assert not screen.new_button.isVisible() and not screen.delete_button.isVisible()
    assert screen.warehouse_id == env["main"] and screen.stock_kind == "invoice"
    assert screen.grid.rowCount() >= 2
    r = next(i for i, row in enumerate(screen.rows) if row["item_id"] == env["item"])
    assert not screen.grid.item(r, C_QTY).flags() & Qt.ItemIsEditable
    screen.edit_record()
    assert screen.mode == "edit" and screen.grid.item(r, C_QTY).flags() & Qt.ItemIsEditable
    screen.set_cell(env["item"], "10", "12.5")
    assert screen.grid.item(r, C_VALUE).text() == "125.00"
    # تغيير المخزن والنوع وانت بتعدّل ← اللي اتكتب بيفضل
    screen.set_filter(env["branch"])
    screen.set_cell(env["item2"], "3", "0")
    screen.set_filter(stock_kind="statement")
    screen.set_cell(env["item"], "2", "7")
    screen.set_filter(env["main"], "invoice")
    assert screen.grid.item(r, C_QTY).text() == "10"
    assert "3 تعديل" in screen.hint.text()
    asked: list[str] = []
    monkeypatch.setattr(messages, "ask_yes_no", lambda _p, _t, text: asked.append(text) or True)
    screen.save()
    assert any("سعر صفر" in a for a in asked) and "saved" in shown
    assert line(env, env["item"])["qty"] == 10 and line(env, env["item2"], env["branch"])["unit_cost"] == 0
    assert line(env, env["item"], env["branch"], "statement")["qty"] == 2
    assert "1 سطر بسعر صفر" in screen.status_label.text()
    # جهاز تاني عدّل نفس السطر ← تعارض
    screen.edit_record()
    put(env, env["item"], "11", "12.5")
    screen.set_cell(env["item"], "15", "12.5")
    conflicts: list = []
    monkeypatch.setattr(messages, "ask_conflict", lambda _p, exc: conflicts.append(exc) or True)
    screen.save()
    assert conflicts and line(env, env["item"])["qty"] == 11 and screen.mode == "view"
    # اللي عليها رصيد بس + البحث
    screen.only_balance.setChecked(True)
    visible = [screen.rows[i]["item_id"] for i in range(screen.grid.rowCount()) if not screen.grid.isRowHidden(i)]
    assert visible == [env["item"]]
    screen.only_balance.setChecked(False)
    screen.filter_edit.setText("مفتاح")
    visible = [screen.rows[i]["item_id"] for i in range(screen.grid.rowCount()) if not screen.grid.isRowHidden(i)]
    assert visible == [env["item2"]]
    screen.filter_edit.clear()
    # الاستيراد + النموذج + التصدير
    tpl = tmp_path / "tpl.xlsx"
    screen.download_template(str(tpl))
    assert tpl.exists()
    path = _xlsx(tmp_path, [(code_of(db, env["item2"]), "", wh_code(db, env["main"]), "فاتورة", 4, 8)])
    monkeypatch.setattr(screen, "review_dialog", lambda reviewed: (True, False))
    screen.import_excel(str(path))
    assert line(env, env["item2"])["qty"] == 4 and any("اتضاف 1" in s for s in shown)
    out = tmp_path / "out.xlsx"
    screen.export_excel(str(out))
    assert len(load_workbook(out).worksheets[0]["A"]) == 5     # العناوين + 4 سطور
    # بعد الترحيل كله مقفول
    post(env)
    screen.refresh_screen()
    assert not screen.edit_button.isEnabled() and not screen.import_button.isEnabled()
    assert "مترحّلة" in screen.status_label.text()
    screen.close()


def test_import_dialog(env):
    from app.ui.screens.opening_stock_screen import ImportReviewDialog

    reviewed = [{"row": 2, "code": "X", "item_name": "", "warehouse_name": "", "stock_kind": None, "qty": None,
                 "unit_cost": None, "is_virtual": False, "value": 0, "errors": ["الكود «X» مش موجود"],
                 "warnings": []}]
    dialog = ImportReviewDialog(reviewed)
    assert not dialog.ok_button.isEnabled() and "1 غلط" in dialog.summary.text()
    dialog.close()


def test_opening_balances_screen_inventory_section(env):
    from app.ui.screens.opening_balances_screen import M_DR, OpeningBalancesScreen

    put(env, env["item"], "10", "100")
    screen = OpeningBalancesScreen(OpeningBalanceService(env["db"]))
    assert screen.current_record["sections"]["inv"]["lines"] == 1
    screen.show_section("inv")
    assert screen.section_table.rowCount() == 1
    assert "1,000.00" in screen.balance_label.text()
    screen.edit_record()
    assert not screen.section_table.item(0, M_DR).flags() & Qt.ItemIsEditable
    assert "بضاعة أول المدة" in screen.section_hint.text()
    screen.cancel()
    screen.close()
