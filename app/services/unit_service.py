"""الوحدات (تبويب «الوحدات» في شاشة «الوحدات والماركات» — المرحلة 3د، النموذج 1 2026-11-15).

* **الرمز** (PCS · M · KG) بيكتبه المستخدم ومايتكررش، وبيتخزن حروف كبيرة من غير مسافات · الاسم مايتكررش.
* **النوع** (عدد / طول / وزن / مساحة / حجم / وقت) · **«الكمية تقبل كسور»**.
* **الافتراضية** واحدة بس — تعليمها على وحدة بيشيلها من التانية في **نفس الـ transaction** جوه قفل،
  والافتراضية مابتتوقفش ولا تتمسح.
* الوحدات المتعددة للصنف (كرتونة = 12) على الصنف نفسه في شاشة الأصناف.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version``.
"""

from __future__ import annotations

import re
from typing import Any

from app.database.db import Database, get_db
from app.repositories.unit_repository import CATEGORIES, CATEGORY_NAMES, STATUSES, UnitRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "inv.units", "وحدة"
PERM = "inv.units_brands"
NOT_FOUND = "الوحدة دي مش موجودة (ممكن تكون اتمسحت)."
DEFAULT_LOCKED = ("الوحدة «{name}» هي الوحدة الافتراضية (بتتملى لوحدها في الصنف الجديد) — {what} "
                  "(علّم وحدة تانية «افتراضية» الأول).")
USAGE_NOTE = "عدد الأصناف اللي عليها بيظهر مع شاشة الأصناف."
_CODE_RE = re.compile(r"^\S{1,10}$")


def clean_code(code: str | None) -> str:
    """الرمز: من غير مسافات وحروف كبيرة («kg » ← KG)."""
    return "".join((code or "").split()).upper()


class UnitService:
    def __init__(self, db: Database | None = None, repository: UnitRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or UnitRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def categories() -> tuple[tuple[str, str], ...]:
        return CATEGORIES

    @staticmethod
    def category_name(key: str | None) -> str:
        return CATEGORY_NAMES.get(key or "", key or "")

    def list_units(self, keyword: str = "", status: str = "all", category: str = "") -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        category = category if category in CATEGORY_NAMES else ""
        return self.repository.list_units(keyword.strip(), status, category)

    def count_units(self, keyword: str = "", status: str = "all") -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_units(keyword.strip(), status)
        return {k: int(row[k]) for k in ("total", "active", "inactive")}

    def lookup_units(self) -> list[dict[str, Any]]:
        return self.repository.list_units()

    def active_units(self) -> list[dict[str, Any]]:
        """للاختيار في الصنف (الموقوفة ماتتختارش)."""
        return self.repository.list_units(status="active")

    def get_unit(self, unit_id: int) -> dict[str, Any] | None:
        return self.repository.get_unit(int(unit_id))

    @staticmethod
    def new_defaults() -> dict[str, Any]:
        return {"category": "count", "allows_fraction": False, "is_default": False, "is_active": True}

    # ---- التحقق --------------------------------------------------------------
    def _clean(self, data: dict[str, Any], before: dict[str, Any] | None) -> dict[str, Any]:
        code = clean_code(data.get("code"))
        if not code:
            raise BusinessError("رمز الوحدة مطلوب (زي PCS أو M أو KG).")
        if not _CODE_RE.match(code):
            raise BusinessError("«الرمز» أطول من المسموح (10 حروف).")
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم الوحدة مطلوب.")
        if len(name) > 60:
            raise BusinessError("«الاسم» أطول من المسموح (60 حرف).")
        name_en = " ".join((data.get("name_en") or "").split()) or None
        if name_en and len(name_en) > 60:
            raise BusinessError("«الاسم EN» أطول من المسموح (60 حرف).")
        notes = (data.get("notes") or "").strip() or None
        if notes and len(notes) > 500:
            raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
        category = data.get("category") or "count"
        if category not in CATEGORY_NAMES:
            raise BusinessError("نوع الوحدة مش معروف.")
        clean = {"code": code, "name_ar": name, "name_en": name_en, "category": category,
                 "allows_fraction": bool(data.get("allows_fraction", False)),
                 "is_default": bool(data.get("is_default", False)), "notes": notes,
                 "is_active": bool(data.get("is_active", True))}
        if clean["is_default"] and not clean["is_active"]:
            if before is not None and before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="مابتتوقفش"))
            raise BusinessError("الوحدة الافتراضية لازم تبقى نشطة.")
        # TODO (شاشة الأصناف): «تقبل كسور» ماتتقفلش على وحدة عليها كميات بكسور.
        return clean

    def _take_default(self, tx, unit_id: int | None) -> None:
        """قبل ما الوحدة تاخد «الافتراضية»: نشيلها من الوحدة اللي عليها (جوه القفل + سطر في السجل)."""
        old = self.repository.lock_default(tx)
        if old is None or (unit_id is not None and int(old["id"]) == int(unit_id)):
            return
        self.repository.clear_default(int(old["id"]), tx)
        after = self.repository.get_unit(int(old["id"]), ex=tx)
        self.audit.record(tx, action="update", entity=ENTITY, entity_id=old["id"], entity_label=ENTITY_LABEL,
                          record_label=f"{old['code']} {old['name_ar']}", before=old, after=after)

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    def save_unit(self, unit_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if unit_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if unit_id is not None:
                before = self.repository.get_unit(int(unit_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=unit_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
            clean = self._clean(data, before)
            exclude = None if unit_id is None else int(unit_id)
            taken = self.repository.code_taken(clean["code"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"الرمز «{clean['code']}» موجود على وحدة «{taken['name_ar']}» — الرمز مايتكررش.")
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه وحدة بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            if clean["is_default"] and (before is None or not before["is_default"]):
                self._take_default(tx, exclude)
            if before is None:
                unit_id = self.repository.insert_unit(clean, ex=tx)
            else:
                updated = self.repository.update_unit(int(unit_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=unit_id)
            after = self.repository.get_unit(int(unit_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=unit_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}", before=before, after=after)
        return int(unit_id)

    def set_active(self, unit_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» — الافتراضية مابتتوقفش."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_unit(int(unit_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=unit_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active and before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="مابتتوقفش"))
            updated = self.repository.set_active(int(unit_id), bool(active), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=unit_id)
            after = self.repository.get_unit(int(unit_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=unit_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_unit(self, unit_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_unit(int(unit_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=unit_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["is_default"]:
                raise BusinessError(DEFAULT_LOCKED.format(name=before["name_ar"], what="مابتتمسحش"))
            # TODO (شاشة الأصناف): الوحدة اللي عليها أصناف ماتتمسحش — FK RESTRICT
            # + رسالة عربي «عليها أصناف — وقّفها بدل ما تمسحها».
            deleted = self.repository.delete_unit(int(unit_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=unit_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=unit_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
