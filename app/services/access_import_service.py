"""نقل الماركات والأصناف من الأكسس (طلب المستخدم 2026-11-16: «جيب الأصناف اللي في الأكسس»).

القراية من ملف الأكسس وقت التشغيل (``read_access`` — pyodbc، قراية بس) والكتابة في القاعدة في **transaction واحد**
(يا كله يا مفيش). بيانات الأكسس نفسها عمرها ما بتتكتب في ملف في المشروع.

التنضيف (النموذج 9 في mockups/phase3/13_items.html + ردود المستخدم):
* **الماركات** (``tb-Company``): الاسم بيتنضف (``clean_name``) والمكرر (HIMEL مرتين) بيبقى ماركة واحدة · الموجودة
  بنفس الاسم بتتستخدم زي ما هي.
* **النوع** (``TypeShop`` ← ``tb-Shopes``): بالاسم في ``inv.item_categories.access_name`` (Panel/ABB ← Panel / ABB) ·
  «zxxxxxxx» واللي من غير قسم ← من غير نوع.
* **الوحدة** (نص بـ 9 كتابات): Unit/unit/ك ← عدد (PCS) · Meter ← متر (M) · KGM/كيلو ← كيلو جرام (KG) · الفاضي ← عدد.
* **الاسم** = ``matrialname`` (ولو فاضي ← الوصف ← «صنف بدون اسم») · **المتكرر في نفس النوع** بياخد « (2)» « (3)»…
  (الاسم مايتكررش في نفس النوع) · الصنف من غير اسم ولا وصف بيتنقل **موقوف**.
* **الوصف** = ``descriptien`` لو مختلف عن الاسم · **الملاحظات** = ``code`` القديم لو مختلف عن الاسم.
* ``number1`` (رقم الكتالوج) **مابيتنقلش** (رد المستخدم: «احذفه») · ض.ق.م 14% · بضاعة (مش خدمي) · نشط.
* ``access_id`` = ``pruduct.id`` ← النقل لو اتشغّل تاني بيعدّي اللي اتنقل، والفواتير القديمة هتتربط بيه.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.database.db import Database, get_db
from app.repositories.access_import_repository import AccessImportRepository
from app.repositories.item_repository import format_code
from app.services.audit_service import AuditService
from app.services.brand_service import clean_name

UNIT_MAP = {"unit": "PCS", "ك": "PCS", "meter": "M", "kgm": "KG", "كيلو": "KG"}
DROP_CATEGORIES = {"zxxxxxxx"}
NO_NAME = "صنف بدون اسم"
DEFAULT_TAX = "VAT14"


@dataclass
class ImportReport:
    brands_created: int = 0
    brands_existing: int = 0
    items_created: int = 0
    items_skipped: int = 0            # اتنقلوا قبل كده (access_id موجود)
    renamed: list[tuple[int, str]] = field(default_factory=list)      # (access_id، الاسم الجديد)
    no_name: list[int] = field(default_factory=list)
    no_category: int = 0
    units: dict[str, int] = field(default_factory=dict)
    first_code: str = ""
    last_code: str = ""

    def lines(self) -> list[str]:
        out = [f"الماركات: {self.brands_created} جديدة · {self.brands_existing} كانت موجودة",
               f"الأصناف: {self.items_created} اتنقلت ({self.first_code} ← {self.last_code})"
               if self.items_created else "الأصناف: مفيش جديد",
               f"اتعدّوا (اتنقلوا قبل كده): {self.items_skipped}",
               f"من غير نوع: {self.no_category} · الوحدات: "
               + " · ".join(f"{k} {v}" for k, v in sorted(self.units.items())),
               f"أسماء متكررة في نفس النوع اتزوّد عليها رقم: {len(self.renamed)}",
               f"من غير اسم ولا وصف (اتنقلوا موقوفين): {len(self.no_name)}"]
        return out


def map_unit(text: Any) -> str:
    key = " ".join(str(text or "").split()).lower()
    return UNIT_MAP.get(key, "PCS")


def _clip(text: str | None, size: int) -> str | None:
    if not text:
        return None
    return text if len(text) <= size else text[: size - 1] + "…"


def read_access(path: str) -> dict[str, list[dict[str, Any]]]:
    """يقرا الماركات والأقسام والأصناف من ملف الأكسس (قراية بس)."""
    import pyodbc  # noqa: PLC0415 - مطلوب للنقل بس

    conn = pyodbc.connect(f"DRIVER={{Microsoft Access Driver (*.mdb, *.accdb)}};DBQ={path};ReadOnly=1")
    try:
        cur = conn.cursor()

        def rows(sql: str) -> list[dict[str, Any]]:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

        return {
            "brands": rows("SELECT id, CompanyN AS name FROM [tb-Company] ORDER BY id"),
            "shops": rows("SELECT id, Shope1 AS name FROM [tb-Shopes] ORDER BY id"),
            "items": rows("SELECT id, code, matrialname AS name, descriptien AS description, unit, TypeShop AS shop_id, "
                          "COmpanYNAm AS brand_id FROM pruduct ORDER BY id"),
        }
    finally:
        conn.close()


class AccessImportService:
    def __init__(self, db: Database | None = None, repository: AccessImportRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or AccessImportRepository()
        self.audit = audit or AuditService(self.db)

    def import_items(self, data: dict[str, list[dict[str, Any]]]) -> ImportReport:
        report = ImportReport()
        repo = self.repository
        with self.db.atomic() as tx:
            repo.lock(tx)
            # ---- الماركات
            brand_by_name = {r["name"].lower(): int(r["id"]) for r in repo.brands(tx)}
            brand_map: dict[int, int] = {}
            before_names, counted = set(brand_by_name), set()
            seq = repo.next_brand_seq(tx)
            for b in data.get("brands", []):
                name = clean_name(b.get("name"))[:120]
                if not name:
                    continue
                key = name.lower()
                if key in brand_by_name:
                    if key in before_names and key not in counted:
                        counted.add(key)
                        report.brands_existing += 1
                else:
                    brand_by_name[key] = repo.insert_brand(tx, seq, name, "من الأكسس")
                    seq += 1
                    report.brands_created += 1
                brand_map[int(b["id"])] = brand_by_name[key]
            # ---- الأنواع والوحدات والضريبة
            cats = repo.categories(tx)
            cat_by_access = {(c["access_name"] or "").lower(): int(c["id"]) for c in cats if c["access_name"]}
            shop_map: dict[int, int | None] = {}
            for s in data.get("shops", []):
                name = " ".join(str(s.get("name") or "").split())
                shop_map[int(s["id"])] = None if name.lower() in DROP_CATEGORIES else cat_by_access.get(name.lower())
            units = repo.units(tx)
            tax_id = repo.tax_id(tx, DEFAULT_TAX)
            if tax_id is None:
                raise RuntimeError("كود الضريبة VAT14 مش موجود — مينفعش أنقل الأصناف.")
            # ---- الأصناف
            existing = repo.items(tx)
            done = {int(r["access_id"]) for r in existing if r["access_id"] is not None}
            taken = {(r["category_id"], r["name"].lower()) for r in existing}
            seq = repo.next_item_seq(tx)
            for it in sorted(data.get("items", []), key=lambda r: int(r["id"])):
                access_id = int(it["id"])
                if access_id in done:
                    report.items_skipped += 1
                    continue
                name = clean_name(it.get("name"))
                description = clean_name(it.get("description")) or None
                old_code = clean_name(it.get("code")) or None
                active = True
                if not name:
                    name = description or ""
                if not name:
                    name, active = f"{NO_NAME} — أكسس {access_id}", False
                    report.no_name.append(access_id)
                name = _clip(name, 200)
                category_id = shop_map.get(int(it["shop_id"])) if it.get("shop_id") is not None else None
                if category_id is None:
                    report.no_category += 1
                base, n = name, 1
                while (category_id, name.lower()) in taken:
                    n += 1
                    suffix = f" ({n})"
                    name = base[: 200 - len(suffix)] + suffix
                if n > 1:
                    report.renamed.append((access_id, name))
                taken.add((category_id, name.lower()))
                if description and description.lower() == base.lower():
                    description = None
                notes = None
                if old_code and old_code.lower() not in (base.lower(), (description or "").lower()):
                    notes = f"الكود القديم في الأكسس: {old_code}"
                unit_code = map_unit(it.get("unit"))
                report.units[unit_code] = report.units.get(unit_code, 0) + 1
                brand = it.get("brand_id")
                repo.insert_item(tx, {
                    "seq": seq, "name": name, "description": _clip(description, 500),
                    "category_id": category_id, "brand_id": brand_map.get(int(brand)) if brand is not None else None,
                    "unit_id": units[unit_code], "tax_code_id": tax_id, "notes": _clip(notes, 500),
                    "is_active": active, "access_id": access_id})
                if not report.first_code:
                    report.first_code = format_code(seq)
                report.last_code = format_code(seq)
                seq += 1
                report.items_created += 1
            if report.items_created or report.brands_created:
                self.audit.record(tx, action="create", entity="inv.items", entity_id=None,
                                  entity_label="نقل من الأكسس",
                                  record_label=f"نقل {report.items_created} صنف و{report.brands_created} ماركة من الأكسس",
                                  extra={"report": report.lines()})
        return report
