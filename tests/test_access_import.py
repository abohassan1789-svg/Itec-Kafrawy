"""اختبار نقل الماركات والأصناف من الأكسس (``AccessImportService``) — **ببيانات وهمية** مش من ملف الأكسس:
الماركة المكررة · القسم ← النوع · الوحدات بكتاباتها · الاسم الفاضي والمكرر في نفس النوع · التشغيل مرتين."""

from __future__ import annotations

import pytest

from app.database import migrator
from app.services.access_import_service import AccessImportService, map_unit


def fake_access() -> dict:
    return {
        "brands": [{"id": 1, "name": "Alpha"}, {"id": 2, "name": " ALPHA\xa0"}, {"id": 3, "name": "Beta  Co"},
                   {"id": 4, "name": "  "}],
        "shops": [{"id": 1, "name": "BMS"}, {"id": 5, "name": "Panel/ABB"}, {"id": 13, "name": "zxxxxxxx"}],
        "items": [
            {"id": 10, "code": None, "name": "Relay 24V", "description": "Relay 24V", "unit": "Unit",
             "shop_id": 1, "brand_id": 1},
            {"id": 11, "code": "relay-old", "name": "relay  24v", "description": "relay خاص", "unit": " unit",
             "shop_id": 1, "brand_id": 2},
            {"id": 12, "code": None, "name": "Relay 24V", "description": None, "unit": "Meter", "shop_id": 5,
             "brand_id": 3},
            {"id": 13, "code": "X1", "name": None, "description": None, "unit": None, "shop_id": None,
             "brand_id": None},
            {"id": 14, "code": None, "name": None, "description": "Cable 4x16", "unit": "كيلو", "shop_id": 13,
             "brand_id": None},
            {"id": 15, "code": None, "name": "Cable 4x16", "description": None, "unit": " KGM", "shop_id": None,
             "brand_id": None},
        ],
    }


def test_map_unit():
    assert [map_unit(u) for u in ("Unit", " unit", "ك", "Meter", "KGM", " KGM", "كيلو", None, "xx")] == \
        ["PCS", "PCS", "PCS", "M", "KG", "KG", "KG", "PCS", "PCS"]


def test_import(temp_db):
    db = temp_db
    migrator.migrate(db)
    db.execute("INSERT INTO inv.item_brands (seq, name) VALUES (1, 'Beta Co')")          # ماركة موجودة قبل النقل
    report = AccessImportService(db).import_items(fake_access())
    assert (report.brands_created, report.brands_existing) == (1, 1)                   # Alpha جديدة · Beta موجودة
    brands = {r["name"]: r["code"] for r in db.fetch_all("SELECT name, code FROM inv.item_brands")}
    assert brands == {"Beta Co": "BRD-001", "Alpha": "BRD-002"}
    rows = db.fetch_all(
        "SELECT i.code, i.name, i.description, i.notes, i.is_active, i.access_id, c.name AS cat, b.name AS brand, "
        "u.code AS unit, t.code AS tax FROM inv.items i LEFT JOIN inv.item_categories c ON c.id = i.category_id "
        "LEFT JOIN inv.item_brands b ON b.id = i.brand_id JOIN inv.units u ON u.id = i.unit_id "
        "JOIN acc.tax_codes t ON t.id = i.tax_code_id ORDER BY i.seq")
    got = [(r["code"], r["name"], r["cat"], r["brand"], r["unit"], r["is_active"], r["access_id"]) for r in rows]
    assert got == [
        ("ITM-0001", "Relay 24V", "BMS", "Alpha", "PCS", True, 10),
        ("ITM-0002", "relay 24v (2)", "BMS", "Alpha", "PCS", True, 11),        # متكرر في نفس النوع + الماركة المدموجة
        ("ITM-0003", "Relay 24V", "ABB", "Beta Co", "M", True, 12),            # نوع تاني ← نفس الاسم عادي
        ("ITM-0004", "صنف بدون اسم — أكسس 13", None, None, "PCS", False, 13),  # من غير اسم ولا وصف ← موقوف
        ("ITM-0005", "Cable 4x16", None, None, "KG", True, 14),                 # الاسم من الوصف · zxxxxxxx ← من غير نوع
        ("ITM-0006", "Cable 4x16 (2)", None, None, "KG", True, 15),
    ]
    assert rows[0]["description"] is None and rows[1]["description"] == "relay خاص"
    assert rows[1]["notes"] == "الكود القديم في الأكسس: relay-old" and rows[3]["notes"] == "الكود القديم في الأكسس: X1"
    assert {r["tax"] for r in rows} == {"VAT14"}
    assert (report.items_created, report.no_category, len(report.renamed), report.no_name) == (6, 3, 2, [13])
    assert report.first_code == "ITM-0001" and report.last_code == "ITM-0006"
    assert db.fetch_value("SELECT count(*) FROM sec.audit_log WHERE entity_label = 'نقل من الأكسس'") == 1
    # التشغيل تاني: مفيش حاجة بتتكرر
    again = AccessImportService(db).import_items(fake_access())
    assert (again.items_created, again.items_skipped, again.brands_created) == (0, 6, 0)
    assert db.fetch_value("SELECT count(*) FROM inv.items") == 6


def test_import_is_all_or_nothing(temp_db):
    db = temp_db
    migrator.migrate(db)
    data = fake_access()
    data["items"].append({"id": 99, "code": None, "name": "Bad", "description": None, "unit": "Unit",
                          "shop_id": 1, "brand_id": 777})       # ماركة مش موجودة ← brand_id فاضي (مش خطأ)
    data["items"].append({"id": -5, "code": None, "name": "Bad id", "description": None, "unit": "Unit",
                          "shop_id": 1, "brand_id": None})      # رقم أكسس غلط ← القاعدة بترفض ← مفيش حاجة اتنقلت
    with pytest.raises(Exception):
        AccessImportService(db).import_items(data)
    assert db.fetch_value("SELECT count(*) FROM inv.items") == 0
    assert db.fetch_value("SELECT count(*) FROM inv.item_brands") == 0
