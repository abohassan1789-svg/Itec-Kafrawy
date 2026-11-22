"""الأصناف (شاشة «الأصناف» — المرحلة 3د، النموذج 3 «كروت أرقام فوق + قائمة وفورم» اختيار المستخدم 2026-11-16).

* **الكود** ``ITM-0001`` بيتولّد لوحده وقت الحفظ جوه قفل وثابت · مفيش «رقم كتالوج» (رد المستخدم).
* **الاسم** مطلوب ومتنضف (عربي أو إنجليزي) و**مايتكررش في نفس النوع** — في نوع تاني مسموح **بتحذير بس**
  (``same_name_elsewhere`` للشاشة) · «الاسم التاني» اختياري.
* **النوع** اختياري · **الحسابات من النوع بس** (``effective_accounts``) · الصنف الجديد بياخد «خدمي» من النوع.
* **الوحدة الأساسية** + **وحدات تانية بمعامل** (كرتونة = 12 عدد): غير الأساسية · مش متكررة · المعامل > 0.
* **خدمي** مالوش مخزن افتراضي ولا حد طلب · **المخزن الافتراضي** اختياري (الفاضي = الافتراضي العام) ومش وهمي.
* **كود الضريبة** الافتراضي ض.ق.م 14% ويتغيّر (إضافة بس) · **سعر البيع** و**حد الطلب** اختياريين ≥ 0.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction (ومعاه الوحدات التانية) + ``row_version``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.item_repository import KINDS, STATUSES, ItemRepository, format_code
from app.services.audit_service import AuditService
from app.services.brand_service import clean_name
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.services.stock_ledger_service import KINDS as STOCK_KINDS
from app.services.stock_ledger_service import StockLedgerService, fmt_qty

ENTITY, ENTITY_LABEL = "inv.items", "صنف"
PERM = "inv.items"
PAGE_SIZE = 100
LOOKUP_LIMIT = 10000
MAX_UNITS = 10
DEFAULT_TAX = "VAT14"
NOT_FOUND = "الصنف ده مش موجود (ممكن يكون اتمسح)."
BALANCE_NOTE = ("الرصيد ومتوسط التكلفة من «دفتر المخزون» (بيتحرّك من الفواتير والتحويلات والجرد) — الصنف اللي عليه "
                "حركة مايتمسحش ووحدته الأساسية و«خدمي» مايتغيّروش.")
ACCOUNT_LABELS = {"inventory": "المخزون", "sales": "المبيعات", "cogs": "تكلفة المبيعات", "sales_ret": "المردودات"}
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("alt_name", "الاسم التاني", 200), ("description", "الوصف", 500), ("notes", "الملاحظات", 500))
_MAX_AMOUNT = Decimal("99999999999999")


def parse_amount(value: Any, label: str) -> Decimal | None:
    """«1,250.5» ← Decimal · الفاضي ← None · سالب أو مش رقم ← BusinessError."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        text = str(value)
    else:
        text = str(value).strip().replace(",", "").replace("٫", ".").replace("٬", "")
        text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if text == "":
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise BusinessError(f"«{label}» لازم يبقى رقم.") from None
    if not amount.is_finite():
        raise BusinessError(f"«{label}» لازم يبقى رقم.")
    if amount < 0:
        raise BusinessError(f"«{label}» مينفعش يبقى بالسالب.")
    if amount > _MAX_AMOUNT:
        raise BusinessError(f"«{label}» أكبر من المسموح.")
    return amount


def category_path(row: dict[str, Any]) -> str:
    """«Panel / ABB» من صف الصنف (النوع وأبوه)."""
    if not row.get("category_name"):
        return ""
    parent = row.get("category_parent_name")
    return f"{parent} / {row['category_name']}" if parent else row["category_name"]


class ItemService:
    def __init__(self, db: Database | None = None, repository: ItemRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or ItemRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)
        self.stock = StockLedgerService(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def _filters(status: str, kind: str) -> tuple[str, str]:
        return (status if status in STATUSES else "all", kind if kind in KINDS else "all")

    def list_items(self, keyword: str = "", status: str = "all", kind: str = "all", page: int = 1,
                   page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status, kind = self._filters(status, kind)
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_items(keyword.strip(), status, kind, limit=page_size, offset=offset)

    def count_items(self, keyword: str = "", status: str = "all", kind: str = "all") -> dict:
        status, kind = self._filters(status, kind)
        row = self.repository.count_items(keyword.strip(), status, kind)
        return {k: int(row[k]) for k in ("total", "active", "inactive")}

    def stats(self) -> dict[str, int]:
        row = self.repository.stats()
        out = {k: int(row[k]) for k in ("active", "service", "no_category", "with_reorder")}
        out["below_reorder"] = self.stock.below_reorder_count()
        return out

    def stock_text(self, item_id: int | None) -> tuple[str, str]:
        """(الرصيد، متوسط التكلفة) للفورم — من دفتر المخزون."""
        if item_id is None:
            return "", ""
        data = self.stock.item_summary(int(item_id))
        if not data["has_movements"]:
            return "0 — مفيش حركة لسه", "—"
        parts = [f"{fmt_qty(data['qty'])} (القيمة {data['value']:,.2f})"]
        virtual = sum((r["qty"] for r in data["warehouses"] if r["is_virtual"]), Decimal("0"))
        if virtual:
            parts.append(f"+ {fmt_qty(virtual)} في الوهمي")
        avg = " · ".join(f"{STOCK_KINDS[r['stock_kind']]}: {r['avg_cost']:,.2f}" for r in data["kinds"] if r["qty"] > 0)
        return " ".join(parts), avg or "—"

    def lookup_items(self) -> list[dict[str, Any]]:
        """نافذة البحث F1 + «تصدير إكسل»."""
        return self.repository.list_items("", "all", "all", limit=LOOKUP_LIMIT)

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        row = self.repository.get_item(int(item_id))
        if row is not None:
            row["units"] = self.repository.item_units(int(item_id))
        return row

    def choices(self) -> dict[str, list[dict[str, Any]]]:
        """اختيارات الفورم (الموقوف بيرجع كمان عشان صنف قديم عليه يتعرض — الشاشة بتعرض النشط بس للجديد)."""
        cats = self.repository.category_choices()
        by_id = {c["id"]: c for c in cats}
        for c in cats:
            names, node, guard = [c["name"]], c, 0
            while node.get("parent_id") is not None and guard < 50:
                node = by_id.get(node["parent_id"]) or {}
                if not node:
                    break
                names.append(node["name"])
                guard += 1
            c["path"] = " / ".join(reversed(names))
        cats.sort(key=lambda c: c["path"].lower())
        return {"categories": cats, "brands": self.repository.brand_choices(),
                "units": self.repository.unit_choices(), "warehouses": self.repository.warehouse_choices(),
                "taxes": self.repository.tax_choices()}

    def effective_accounts(self, category_id: Any) -> list[dict[str, Any]]:
        """حسابات الصنف (من النوع، والفاضي من الافتراضي): ``[{kind, label, code, name_ar}]``."""
        cid = int(category_id) if category_id not in (None, "", 0) else None
        rows = self.repository.effective_accounts(cid)
        return [{**r, "label": ACCOUNT_LABELS[r["kind"]]} for r in rows]

    def same_name_elsewhere(self, name: str, category_id: Any, exclude_id: int | None = None) -> list[dict]:
        """الأصناف بنفس الاسم في **نوع تاني** (للتحذير قبل الحفظ)."""
        name = clean_name(name)
        if not name:
            return []
        cid = int(category_id) if category_id not in (None, "", 0) else None
        return [r for r in self.repository.same_name(name, exclude_id) if r["category_id"] != cid]

    def new_defaults(self, category_id: Any = None) -> dict[str, Any]:
        """الوحدة الافتراضية («عدد») · ض.ق.م 14% · «خدمي» من النوع."""
        ch = self.choices()
        unit = next((u for u in ch["units"] if u["is_default"] and u["is_active"]), None)
        tax = next((t for t in ch["taxes"] if t["code"] == DEFAULT_TAX and t["is_active"]),
                   next((t for t in ch["taxes"] if t["is_active"]), None))
        cat = next((c for c in ch["categories"] if c["id"] == category_id), None) if category_id else None
        return {"code": self.repository.suggested_code(), "is_active": True,
                "unit_id": unit["id"] if unit else None, "tax_code_id": tax["id"] if tax else None,
                "category_id": cat["id"] if cat else None,
                "is_service": bool(cat["new_items_service"]) if cat else False}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _id(value: Any) -> int | None:
        return int(value) if value not in (None, "", 0) else None

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = clean_name(data.get("name"))
        if not name:
            raise BusinessError("اسم الصنف مطلوب.")
        if len(name) > 200:
            raise BusinessError("«الاسم» أطول من المسموح (200 حرف).")
        clean: dict[str, Any] = {"name": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if key == "alt_name" and value:
                value = clean_name(value)
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        for key in ("category_id", "brand_id", "unit_id", "default_warehouse_id", "tax_code_id"):
            clean[key] = self._id(data.get(key))
        if clean["unit_id"] is None:
            raise BusinessError("اختار الوحدة الأساسية للصنف.")
        if clean["tax_code_id"] is None:
            raise BusinessError("اختار كود الضريبة (الافتراضي ض.ق.م 14%).")
        clean["is_service"] = bool(data.get("is_service", False))
        clean["sale_price"] = parse_amount(data.get("sale_price"), "سعر البيع")
        clean["reorder_level"] = parse_amount(data.get("reorder_level"), "حد الطلب")
        if clean["is_service"]:                       # الخدمي مالوش مخزن ولا حد طلب
            clean["default_warehouse_id"] = None
            clean["reorder_level"] = None
        clean["is_active"] = bool(data.get("is_active", True))
        clean["units"] = self._clean_units(data.get("units") or [], clean["unit_id"])
        return clean

    def _clean_units(self, units: list[dict[str, Any]], base_unit_id: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for row in units:
            unit_id = self._id(row.get("unit_id"))
            if unit_id is None:
                continue
            if unit_id == base_unit_id:
                raise BusinessError("الوحدة التانية لازم تبقى غير الوحدة الأساسية.")
            if unit_id in seen:
                raise BusinessError("نفس الوحدة متكررة في «الوحدات التانية».")
            factor = parse_amount(row.get("factor"), "المعامل")
            if factor is None or factor <= 0:
                raise BusinessError("المعامل لازم أكبر من صفر (زي: كرتونة = 12 عدد).")
            seen.add(unit_id)
            out.append({"unit_id": unit_id, "factor": factor})
        if len(out) > MAX_UNITS:
            raise BusinessError(f"أقصى عدد للوحدات التانية {MAX_UNITS}.")
        return out

    def _check_refs(self, tx, clean: dict[str, Any], before: dict[str, Any] | None) -> None:
        """اللي اتختار جديد لازم نشط (رسايل أوضح من القاعدة)."""
        if not clean["is_active"]:
            return
        ch = self.choices()
        checks = (("category_id", "categories", "نوع الأصناف", "name"), ("brand_id", "brands", "الماركة", "name"),
                  ("unit_id", "units", "الوحدة", "name_ar"), ("default_warehouse_id", "warehouses", "المخزن", "name_ar"),
                  ("tax_code_id", "taxes", "كود الضريبة", "name_ar"))
        activating = before is not None and not before["is_active"]
        for key, group, label, name_key in checks:
            value = clean[key]
            if value is None or (before is not None and before[key] == value and not activating):
                continue
            row = next((r for r in ch[group] if r["id"] == value), None)
            if row is None:
                raise BusinessError(f"{label} اللي اخترته مش موجود (ممكن يكون اتمسح أو المخزن وهمي) — دوس «تحديث».")
            if not row["is_active"]:
                raise BusinessError(f"{label} «{row[name_key]}» موقوف — اختار واحد نشط.")
        units = {u["id"]: u for u in ch["units"]}
        old = {u["unit_id"] for u in (before or {}).get("units", [])}
        for u in clean["units"]:
            unit = units.get(u["unit_id"])
            if unit is None:
                raise BusinessError("وحدة من «الوحدات التانية» مش موجودة — دوس «تحديث».")
            if u["unit_id"] not in old and not unit["is_active"]:
                raise BusinessError(f"الوحدة «{unit['name_ar']}» موقوفة — اختار وحدة نشطة.")

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_item(self, item_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if item_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if item_id is not None:
                before = self.repository.get_item(int(item_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=item_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
                before["units"] = self.repository.item_units(int(item_id), ex=tx)
            # جوه قفل الترقيم: جهازين بيضيفوا نفس الاسم في نفس النوع في نفس اللحظة ← التاني بيترفض.
            if before is None:
                clean["seq"] = self.repository.next_seq(tx)
            else:
                self.repository.lock(tx)
                clean["seq"] = before["seq"]
            exclude = None if item_id is None else int(item_id)
            for row in self.repository.same_name(clean["name"], exclude, ex=tx):
                if row["category_id"] == clean["category_id"]:
                    where = f"في نفس النوع «{row['category_name']}»" if row["category_name"] else "(من غير نوع)"
                    raise BusinessError(f"فيه صنف بنفس الاسم «{row['name']}» ({row['code']}) {where} — الاسم "
                                        "مايتكررش في نفس النوع.")
            self._check_refs(tx, clean, before)
            code = format_code(clean["seq"])
            if before is None:
                item_id = self.repository.insert_item(clean, ex=tx)
            else:
                updated = self.repository.update_item(int(item_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=item_id)
            self.repository.replace_units(int(item_id), clean["units"], ex=tx)
            after = self.repository.get_item(int(item_id), ex=tx)
            after["units"] = self.repository.item_units(int(item_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=item_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name']}", before=before, after=after)
        return int(item_id)

    def set_active(self, item_id: int, active: bool, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_item(int(item_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=item_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if active:
                for key, label, name in (("category_active", "نوع الأصناف", "category_name"),
                                         ("brand_active", "الماركة", "brand_name")):
                    if before[name] is not None and not before[key]:
                        raise BusinessError(f"{label} «{before[name]}» موقوف — نشّطه الأول أو غيّره من «تعديل».")
            updated = self.repository.set_active(int(item_id), bool(active), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=item_id)
            after = self.repository.get_item(int(item_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=item_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before, after=after)

    def delete_item(self, item_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_item(int(item_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=item_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            before["units"] = self.repository.item_units(int(item_id), ex=tx)
            # الصنف اللي عليه حركة مخزون: القاعدة بترفض برسالة «وقّفه بدل ما تمسحه» (0043).
            deleted = self.repository.delete_item(int(item_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=item_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=item_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before)
