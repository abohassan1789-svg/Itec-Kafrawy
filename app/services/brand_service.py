"""الماركات / الشركات المصنّعة (تبويب «الماركات» في شاشة «الوحدات والماركات» — المرحلة 3د، النموذج 1).

* **الكود** ``BRD-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``BrandRepository.next_seq``) وثابت.
* **الاسم** مطلوب ومايتكررش **حتى لو بحروف كبيرة/صغيرة أو مسافات** — بيتنضف قبل الحفظ (المسافة المخفية
  NBSP والمسافات الزيادة بتتشال)، والقاعدة بترفض الاسم المش متنضف وبتعمل unique على ``lower(name)``.
* الاسم العربي وبلد المنشأ اختياريين.
* الماركات الـ 118 من الأكسس بتتنقل مع «نقل بيانات الأكسس» (المرحلة 11) بنفس التنضيف (``clean_name``).
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version``.
"""

from __future__ import annotations

from typing import Any

from app.database.db import Database, get_db
from app.repositories.brand_repository import STATUSES, BrandRepository, format_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "inv.item_brands", "ماركة"
PERM = "inv.units_brands"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
NOT_FOUND = "الماركة دي مش موجودة (ممكن تكون اتمسحت)."
USAGE_NOTE = "عدد الأصناف اللي عليها بيظهر مع شاشة الأصناف."
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_ar", "الاسم العربي", 120), ("country", "بلد المنشأ", 60), ("notes", "الملاحظات", 500))


def clean_name(name: str | None) -> str:
    """«\\xa0Himel  » ← «Himel» (نفس شرط القاعدة ``item_brands_name_clean``)."""
    return " ".join((name or "").replace(" ", " ").split())


class BrandService:
    def __init__(self, db: Database | None = None, repository: BrandRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or BrandRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_brands(self, keyword: str = "", status: str = "all", page: int = 1,
                    page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_brands(keyword.strip(), status, page_size, offset)

    def count_brands(self, keyword: str = "", status: str = "all") -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_brands(keyword.strip(), status)
        return {k: int(row[k]) for k in ("total", "active", "inactive")}

    def lookup_brands(self) -> list[dict[str, Any]]:
        return self.repository.list_brands("", "all", LOOKUP_LIMIT, 0)

    def get_brand(self, brand_id: int) -> dict[str, Any] | None:
        return self.repository.get_brand(int(brand_id))

    def find_by_name(self, name: str) -> dict[str, Any] | None:
        """الماركة اللي بنفس الاسم (من غير حروف كبيرة/صغيرة ولا مسافات) — للتنبيه وقت الكتابة."""
        name = clean_name(name)
        return self.repository.name_taken(name) if name else None

    def new_defaults(self) -> dict[str, Any]:
        return {"code": self.repository.suggested_code(), "is_active": True}

    # ---- التحقق --------------------------------------------------------------
    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        name = clean_name(data.get("name"))
        if not name:
            raise BusinessError("اسم الماركة مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم» أطول من المسموح (120 حرف).")
        clean: dict[str, Any] = {"name": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if key != "notes" and value:
                value = clean_name(value)
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        clean["is_active"] = bool(data.get("is_active", True))
        return clean

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_brand(self, brand_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if brand_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if brand_id is not None:
                before = self.repository.get_brand(int(brand_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=brand_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean(data)
            exclude = None if brand_id is None else int(brand_id)
            clean["seq"] = self.repository.next_seq(tx) if before is None else before["seq"]   # جوه قفل الترقيم
            taken = self.repository.name_taken(clean["name"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"الماركة دي موجودة: «{taken['name']}» ({taken['code']}) — الاسم مايتكررش "
                                    "حتى لو بحروف كبيرة/صغيرة أو مسافات.")
            code = format_code(clean["seq"])
            if before is None:
                brand_id = self.repository.insert_brand(clean, ex=tx)
            else:
                updated = self.repository.update_brand(int(brand_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=brand_id)
            after = self.repository.get_brand(int(brand_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=brand_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name']}", before=before, after=after)
        return int(brand_id)

    def set_active(self, brand_id: int, active: bool, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_brand(int(brand_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=brand_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            updated = self.repository.set_active(int(brand_id), bool(active), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=brand_id)
            after = self.repository.get_brand(int(brand_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=brand_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before, after=after)

    def delete_brand(self, brand_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_brand(int(brand_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=brand_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            # TODO (شاشة الأصناف): الماركة اللي عليها أصناف ماتتمسحش — FK RESTRICT + رسالة «وقّفها بدل ما تمسحها».
            deleted = self.repository.delete_brand(int(brand_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=brand_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=brand_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before)
