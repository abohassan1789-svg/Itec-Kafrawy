"""المخازن (شاشة «المخازن» — المرحلة 3د، النموذج 2 اختيار المستخدم 2026-11-15 + ردوده).

* **الكود** ``WH-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``WarehouseRepository.next_seq``) وثابت.
* **مفيش حساب لكل مخزن**: كل المخازن على حساب المخزون الواحد (1105) والمخزن «بُعد» على سطر القيد.
* **الأمين** اختياري: موظف نشط عليه دور «أمين مخزن» (والأمين الواحد يمسك كذا مخزن).
* **الافتراضي** (تشيك بوكس): واحد بس — تعليمه على مخزن بيشيله من المخزن التاني في **نفس الـ transaction**
  جوه قفل (جهازين في نفس اللحظة ← واحد يستنى التاني). الافتراضي مايتوقفش ومايتمسحش، والوهمي مايبقاش افتراضي.
* **وهمي** (تشيك بوكس زي الأكسس): حركته كميات بس من غير قيد ولا تكلفة (المرحلة 8).
* **مفيش مركز تكلفة** على المخزن (رد المستخدم).
* الأرصدة والقيمة من «دفتر المخزون» (0043) — المخزن اللي عليه حركة مايتمسحش و«وهمي» مايتغيّرش.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction (والمخزن اللي اتشال منه «الافتراضي») +
  ``row_version``.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.warehouse_repository import STATUSES, WarehouseRepository, format_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService
from app.services.stock_ledger_service import StockLedgerService

ENTITY, ENTITY_LABEL = "inv.warehouses", "مخزن"
PERM = "inv.warehouses"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
NOT_FOUND = "المخزن ده مش موجود (ممكن يكون اتمسح)."
DEFAULT_LOCKED = ("المخزن «{name}» هو المخزن الافتراضي (بيتملى لوحده في الفواتير) — {what} "
                  "(علّم مخزن تاني «افتراضي» الأول).")
VIRTUAL_NOTE = "👻 مخزن وهمي — حركته كميات بس من غير قيد ولا تكلفة (الصرف الوهمي — المرحلة 8)."
BALANCE_NOTE = "الأرصدة والقيمة من «دفتر المخزون» (القيمة = الكمية × متوسط تكلفة الشركة)."
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 120), ("address", "العنوان", 200), ("phone", "التليفون", 30),
          ("notes", "الملاحظات", 500))


class WarehouseService:
    def __init__(self, db: Database | None = None, repository: WarehouseRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or WarehouseRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)
        self.stock = StockLedgerService(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_warehouses(self, keyword: str = "", status: str = "all", page: int = 1,
                        page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_warehouses(keyword.strip(), status, page_size, offset)

    def count_warehouses(self, keyword: str = "", status: str = "all") -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_warehouses(keyword.strip(), status)
        return {k: int(row[k]) for k in ("total", "active", "inactive")}

    def stats(self) -> dict[str, Any]:
        """كروت الأرقام: المخازن النشطة · منها وهمي · العادية من غير أمين · الافتراضي."""
        row = self.repository.stats()
        return {"active": int(row["active"]), "virtual": int(row["virtual"]), "no_keeper": int(row["no_keeper"]),
                "default_code": row["default_code"], "default_name": row["default_name"],
                "value": self.stock.total_value()}

    def stock_of(self, warehouse_id: int | None) -> dict[str, Any] | None:
        """عدد الأصناف اللي عليها رصيد في المخزن + قيمتها (``None`` لو مفيش)."""
        if warehouse_id is None:
            return None
        return self.stock.warehouse_summary().get(int(warehouse_id))

    def lookup_warehouses(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل المخازن (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_warehouses("", "all", LOOKUP_LIMIT, 0)

    def get_warehouse(self, warehouse_id: int) -> dict[str, Any] | None:
        return self.repository.get_warehouse(int(warehouse_id))

    def keepers(self) -> list[dict[str, Any]]:
        """الموظفين النشطين اللي عليهم دور «أمين مخزن»."""
        return self.repository.list_keepers()

    def inventory_account(self) -> dict[str, Any] | None:
        """حساب المخزون الواحد (للعرض): ``{code, name_ar}`` أو None لو مش متحدد."""
        return self.repository.inventory_account()

    def new_defaults(self) -> dict[str, Any]:
        return {"code": self.repository.suggested_code(), "is_active": True, "is_virtual": False,
                "is_default": False}

    # ---- التحقق --------------------------------------------------------------
    def _clean(self, data: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم المخزن مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم» أطول من المسموح (120 حرف).")
        clean: dict[str, Any] = {"name_ar": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        clean["is_virtual"] = bool(data.get("is_virtual", False))
        clean["is_default"] = bool(data.get("is_default", False))
        clean["is_active"] = bool(data.get("is_active", True))
        if clean["is_default"] and clean["is_virtual"]:
            raise BusinessError("المخزن الوهمي مينفعش يبقى المخزن الافتراضي — شيل علامة واحدة منهم.")
        if clean["is_default"] and not clean["is_active"]:
            if before is not None and before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="مايتوقفش"))
            raise BusinessError("المخزن الافتراضي لازم يبقى نشط.")
        keeper = data.get("keeper_id")
        clean["keeper_id"] = int(keeper) if keeper not in (None, "", 0) else None
        if clean["keeper_id"] is not None and clean["is_active"]:
            changed = (before is None or before["keeper_id"] != clean["keeper_id"] or not before["is_active"])
            if changed:
                self._check_keeper(clean["keeper_id"])
        return clean

    def _check_keeper(self, keeper_id: int, ex=None) -> None:
        emp = self.repository.get_employee(int(keeper_id), ex=ex)
        if emp is None:
            raise BusinessError("أمين المخزن مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        if not emp["is_storekeeper"]:
            raise BusinessError(f"الموظف «{emp['name_ar']}» ({emp['code']}) مش عليه دور «أمين مخزن» — "
                                "فعّله من شاشة الموظفين.")
        if not emp["is_active"]:
            raise BusinessError(f"الموظف «{emp['name_ar']}» ({emp['code']}) موقوف — مينفعش يبقى أمين مخزن.")

    def _take_default(self, tx, warehouse_id: int | None) -> None:
        """قبل ما المخزن ياخد «الافتراضي»: نشيلها من المخزن اللي عليه (جوه القفل + سطر في السجل)."""
        old = self.repository.lock_default(tx)
        if old is None or (warehouse_id is not None and int(old["id"]) == int(warehouse_id)):
            return
        self.repository.clear_default(int(old["id"]), tx)
        after = self.repository.get_warehouse(int(old["id"]), ex=tx)
        self.audit.record(tx, action="update", entity=ENTITY, entity_id=old["id"], entity_label=ENTITY_LABEL,
                          record_label=f"{old['code']} {old['name_ar']}", before=old, after=after)

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_warehouse(self, warehouse_id: int | None, data: dict[str, Any],
                       expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if warehouse_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if warehouse_id is not None:
                before = self.repository.get_warehouse(int(warehouse_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=warehouse_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean(data, before)
            exclude = None if warehouse_id is None else int(warehouse_id)
            clean["seq"] = self.repository.next_seq(tx) if before is None else before["seq"]   # جوه قفل الترقيم
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه مخزن بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            if clean["is_default"] and (before is None or not before["is_default"]):
                self._take_default(tx, exclude)
            code = format_code(clean["seq"])
            if before is None:
                warehouse_id = self.repository.insert_warehouse(clean, ex=tx)
            else:
                updated = self.repository.update_warehouse(int(warehouse_id), clean, ex=tx,
                                                           expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=warehouse_id)
            after = self.repository.get_warehouse(int(warehouse_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=warehouse_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name_ar']}", before=before, after=after)
        return int(warehouse_id)

    def set_active(self, warehouse_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات — الافتراضي مايتوقفش."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_warehouse(int(warehouse_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=warehouse_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active and before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="مايتوقفش"))
            if active and before["keeper_id"] is not None:
                emp = self.repository.get_employee(int(before["keeper_id"]), ex=tx)
                if emp is None or not emp["is_storekeeper"] or not emp["is_active"]:
                    raise BusinessError(f"أمين المخزن «{before['keeper_name']}» مابقاش أمين مخزن نشط — "
                                        "غيّر الأمين («تعديل») وبعدين فعّل المخزن.")
            updated = self.repository.set_active(int(warehouse_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=warehouse_id)
            after = self.repository.get_warehouse(int(warehouse_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=warehouse_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_warehouse(self, warehouse_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_warehouse(int(warehouse_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=warehouse_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="مايتمسحش"))
            # المخزن اللي عليه حركة مخزون: القاعدة بترفض برسالة «وقّفه بدل ما تمسحه» (0043).
            deleted = self.repository.delete_warehouse(int(warehouse_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=warehouse_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=warehouse_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
