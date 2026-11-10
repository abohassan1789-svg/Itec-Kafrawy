"""مراكز التكلفة (شاشة «مراكز التكلفة» — المرحلة 2، النموذج 1 «تبويب لكل مركز رئيسي + شجرة + فورم»).

القرارات (docs/03 القسم 3.10 + mockups/phase2/03_cost_centers.html + اختيار المستخدم 2026-11-09):
* شجرة زي الدليل بالظبط: الكود أرقام بس، **بيبدأ بكود الأب وأطول منه** لحد **5 مستويات**، والكود الجاي
  بيتقترح لوحده (المراكز الرئيسية رقم واحد ``1``، ``2``… ← تحتها رقم ``21`` ← وبعد كده رقمين ``2101``).
* «رئيسي» = تجميعي · «فرعي» = اللي بيتكتب في القيد (المرحلة 4).
* **النوع** (إداري / تشغيلي / بيعي / خدمي) بيتقترح من الأب ويتغيّر.
* **فلتر الأب بيجيب الأبناء**: ``subtree_ids`` (دالة ``acc.cost_center_subtree`` في القاعدة).
* **«+ فرعي تحت المحدد»** زي الدليل: الفرعي يتحوّل رئيسي و«<الاسم> — عام» تحته (بياخد أوامر التوريد والقيود
  لما يتعملوا)، والجديد تحته — كله في **transaction واحدة** (``branch_center``).
* القاعدة نفسها بتحمي نفس القواعد (trigger ``acc.cost_centers_guard``) — هنا الرسايل بتطلع أوضح وقبل الحفظ.
* كل حفظ/مسح بيتسجل في ``sec.audit_log`` في نفس الـ transaction، وبحماية التعديل في نفس الوقت (row_version).
"""

from __future__ import annotations

import re
from typing import Any

from app.database.db import Database, get_db
from app.repositories.cost_center_repository import CostCenterRepository
from app.services.account_service import code_width, general_name, matching_ids, next_child_code, visible_ids
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

__all__ = ["CENTER_TYPES", "MAX_LEVEL", "BRANCH_BUTTON", "CostCenterService", "matching_ids", "visible_ids",
           "root_id_of"]

MAX_LEVEL = 5
ENTITY, ENTITY_LABEL = "acc.cost_centers", "مركز تكلفة"
PERM = "acc.cost_centers"
_CODE = re.compile(r"^[0-9]{1,20}$")
CENTER_TYPES = {"admin": "إداري", "ops": "تشغيلي (فني)", "sales": "بيعي", "serv": "خدمي"}
BRANCH_BUTTON = "+ فرعي تحت المحدد"


def label(center: dict[str, Any]) -> str:
    return f"«⁦{center['code']}⁩ {center['name_ar']}»"


def root_id_of(by_id: dict[Any, dict[str, Any]], row: dict[str, Any]) -> Any:
    """رقم المركز الرئيسي (أول مستوى) اللي المركز ده تحته."""
    node = row
    while node.get("parent_id") is not None and node["parent_id"] in by_id:
        node = by_id[node["parent_id"]]
    return node["id"]


class CostCenterService:
    def __init__(self, db: Database | None = None, repository: CostCenterRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or CostCenterRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_centers(self) -> list[dict[str, Any]]:
        return self.repository.list_centers()

    def get_center(self, center_id: int) -> dict[str, Any] | None:
        return self.repository.get_center(int(center_id))

    def subtree_ids(self, center_id: int) -> list[int]:
        """فلتر الأب بيجيب الأبناء: المركز + كل اللي تحته (للتقارير والكشوف)."""
        return self.repository.subtree_ids(int(center_id))

    def accounts_requiring(self) -> list[dict[str, Any]]:
        return self.repository.accounts_requiring()

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[Any, dict[str, int]]:
        """لكل مركز رئيسي (أول مستوى): عدد المراكز تحته (معاه) والفرعي والرئيسي والموقوف."""
        by_id = {r["id"]: r for r in rows}
        out: dict[Any, dict[str, int]] = {}
        for r in rows:
            s = out.setdefault(root_id_of(by_id, r), {"total": 0, "leaf": 0, "main": 0, "inactive": 0})
            s["total"] += 1
            s["leaf" if r["is_leaf"] else "main"] += 1
            s["inactive"] += 0 if r["is_active"] else 1
        return out

    # ---- المركز الجديد (الكود المقترح + الوراثة) ------------------------------
    def new_defaults(self, parent_id: int | None) -> dict[str, Any]:
        """مركز جديد تحت الأب (None = مركز رئيسي أول مستوى): الكود المقترح + المستوى + النوع من الأب."""
        if parent_id is None:
            code = next_child_code("", 0, self.repository.child_codes(None))
            return {"parent_id": None, "parent_code": None, "parent_name": None, "code": code, "level": 1,
                    "center_type": "admin", "is_leaf": False, "is_active": True, "has_children": False,
                    "name_ar": "", "name_en": "", "manager_name": "", "notes": ""}
        parent = self.repository.get_center(int(parent_id))
        if parent is None:
            raise BusinessError("مركز التكلفة الأب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        return self._child_defaults(
            parent, next_child_code(parent["code"], int(parent["level"]), self.repository.child_codes(parent["id"])))

    @staticmethod
    def _child_defaults(parent: dict[str, Any], code: str) -> dict[str, Any]:
        return {"parent_id": parent["id"], "parent_code": parent["code"], "parent_name": parent["name_ar"],
                "code": code, "level": int(parent["level"]) + 1, "center_type": parent["center_type"],
                "is_leaf": True, "is_active": True, "has_children": False,
                "name_ar": "", "name_en": "", "manager_name": "", "notes": ""}

    # ---- التفريع («+ فرعي تحت المحدد» على مركز فرعي) -------------------------
    def _free_code(self, center: dict[str, Any], taken: list[str], ex=None) -> str:
        """أول كود فاضي تحت المركز (بيشوف كل المراكز — ممكن مركز جنبه واخد نفس الكود)."""
        width = code_width(int(center["level"]))
        used = self.repository.codes_with_prefix(center["code"], len(center["code"]) + width, ex=ex)
        return next_child_code(center["code"], int(center["level"]), [*used, *taken])

    @staticmethod
    def _branch_checks(center: dict[str, Any] | None) -> None:
        if center is None:
            raise BusinessError("مركز التكلفة ده مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
        if not center["is_leaf"]:
            raise BusinessError(f"المركز {label(center)} رئيسي أصلاً — ضيف تحته على طول («مركز جديد»).")
        if int(center["level"]) >= MAX_LEVEL:
            raise BusinessError(f"المركز {label(center)} في المستوى الخامس (آخر مستوى) — مينفعش يتفرّع منه.")
        if not center["is_active"]:
            raise BusinessError(f"المركز {label(center)} موقوف — نشّطه الأول وبعدين فرّع منه.")

    def branch_plan(self, center_id: int) -> dict[str, Any]:
        """اللي هيحصل لو فرّعنا المركز الفرعي ده (من غير ما يتحفظ حاجة): «العام» + المركز الجديد."""
        center = self.repository.get_center(int(center_id))
        self._branch_checks(center)
        general_code = self._free_code(center, [])
        code = self._free_code(center, [general_code]) if general_code else ""
        if not general_code or not code:
            raise BusinessError("مفيش كود فاضي تحت المركز ده.")
        return {"center": center, "general": {"code": general_code, "name_ar": general_name(center["name_ar"])},
                "defaults": self._child_defaults(center, code), "expected_version": int(center["row_version"])}

    def branch_center(self, center_id: int, data: dict[str, Any], expected_version: int | None = None) -> int:
        """التفريع كله في transaction واحدة: قفل المركز ← التأكد من الإصدار ← يتحوّل رئيسي + «— عام»
        ← المركز الجديد تحته ← سجل التعديلات. أي غلط = ولا حاجة اتحفظت. بيرجّع رقم المركز الجديد."""
        PermissionService.require(f"{PERM}.create")
        PermissionService.require(f"{PERM}.edit")                 # المركز نفسه بيتعدّل (فرعي ← رئيسي)
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_center(int(center_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=center_id)
            self._branch_checks(before)
            general_code = self._free_code(before, [], ex=tx)
            general_id = self.repository.split_center(before["id"], general_code, ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=before["id"], entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before,
                              after=self.repository.get_center(before["id"], ex=tx),
                              extra={"split": {"general_code": general_code}})
            if general_id is not None:
                general = self.repository.get_center(general_id, ex=tx)
                self.audit.record(tx, action="create", entity=ENTITY, entity_id=general_id, entity_label=ENTITY_LABEL,
                                  record_label=f"{general['code']} {general['name_ar']}", after=general,
                                  extra={"split_from": before["code"]})
            clean = self._clean({**data, "parent_id": before["id"]}, None, tx)
            new_id = self.repository.insert_center(clean, ex=tx)
            self.audit.record(tx, action="create", entity=ENTITY, entity_id=new_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}",
                              after=self.repository.get_center(new_id, ex=tx))
        return int(new_id)

    # ---- التحقق ------------------------------------------------------------
    def _clean(self, data: dict[str, Any], before: dict[str, Any] | None, ex) -> dict[str, Any]:
        code = str(data.get("code") or "").strip()
        if not _CODE.match(code):
            raise BusinessError("كود المركز أرقام بس (من 1 لـ 20 رقم).")
        name = (data.get("name_ar") or "").strip()
        if not name:
            raise BusinessError("اسم المركز (عربي) مطلوب.")
        if len(name) > 120:
            raise BusinessError("«الاسم عربي» أطول من المسموح (120 حرف).")
        name_en = (data.get("name_en") or "").strip() or None
        if name_en and len(name_en) > 120:
            raise BusinessError("«الاسم إنجليزي» أطول من المسموح (120 حرف).")
        manager = (data.get("manager_name") or "").strip() or None
        if manager and len(manager) > 120:
            raise BusinessError("«المسؤول» أطول من المسموح (120 حرف).")
        notes = (data.get("notes") or "").strip() or None
        if notes and len(notes) > 500:
            raise BusinessError("«ملاحظات» أطول من المسموح (500 حرف).")
        center_type = data.get("center_type") or ""
        if center_type not in CENTER_TYPES:
            raise BusinessError("نوع المركز لازم يبقى إداري أو تشغيلي أو بيعي أو خدمي.")
        is_leaf = bool(data.get("is_leaf", True))
        is_active = bool(data.get("is_active", True))
        parent_id = data.get("parent_id")
        parent_id = int(parent_id) if parent_id not in (None, "") else None
        level = 1
        if parent_id is not None:
            if before is not None and parent_id == before["id"]:
                raise BusinessError("المركز مينفعش يبقى تحت نفسه.")
            parent = self.repository.get_center(parent_id, ex=ex)
            if parent is None:
                raise BusinessError("مركز التكلفة الأب مش موجود (ممكن يكون اتمسح) — دوس «تحديث».")
            if parent["is_leaf"]:
                raise BusinessError(f"المركز {label(parent)} فرعي — مينفعش يبقى تحته مراكز "
                                    f"(فرّع منه بـ «{BRANCH_BUTTON}»).")
            moved = before is None or before["parent_id"] != parent_id
            reactivated = before is not None and not before["is_active"] and is_active
            if not parent["is_active"] and (moved or reactivated):
                raise BusinessError(f"المركز {label(parent)} موقوف — مينفعش يبقى تحته مراكز نشطة أو جديدة.")
            if not code.startswith(parent["code"]) or len(code) <= len(parent["code"]):
                raise BusinessError(f"كود المركز لازم يبدأ بكود الأب «⁦{parent['code']}⁩» ويبقى أطول منه.")
            level = int(parent["level"]) + 1
            if level > MAX_LEVEL:
                raise BusinessError(f"أقصى عدد مستويات لمراكز التكلفة {MAX_LEVEL} — مينفعش تضيف تحت {label(parent)}.")
        if level == MAX_LEVEL and not is_leaf:
            raise BusinessError("المستوى الخامس آخر مستوى — المركز لازم يبقى «فرعي».")
        if self.repository.code_exists(code, before["id"] if before else None, ex=ex):
            raise BusinessError(f"كود المركز «⁦{code}⁩» متسجل قبل كده.")
        if before is not None and before["has_children"]:
            if code != before["code"] or parent_id != before["parent_id"]:
                raise BusinessError(f"المركز {label(before)} تحته مراكز — مينفعش تغيّر كوده ولا مكانه.")
            if is_leaf:
                raise BusinessError(f"المركز {label(before)} تحته مراكز — مينفعش يتحوّل «فرعي».")
            if before["is_active"] and not is_active and self.repository.has_active_children(before["id"], ex=ex):
                raise BusinessError(f"المركز {label(before)} تحته مراكز نشطة — وقّفهم الأول.")
        return {"code": code, "name_ar": name, "name_en": name_en, "parent_id": parent_id, "is_leaf": is_leaf,
                "center_type": center_type, "manager_name": manager, "notes": notes, "is_active": is_active}

    # ---- حفظ / مسح ---------------------------------------------------------
    def save_center(self, center_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if center_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = None
            if center_id is not None:
                before = self.repository.get_center(int(center_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=center_id)
                if before is None:
                    raise BusinessError("مركز التكلفة ده مش موجود (ممكن يكون اتمسح).")
            clean = self._clean(data, before, tx)
            if before is None:
                center_id = self.repository.insert_center(clean, ex=tx)
            else:
                updated = self.repository.update_center(int(center_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=center_id)
            after = self.repository.get_center(int(center_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=center_id, entity_label=ENTITY_LABEL,
                              record_label=f"{clean['code']} {clean['name_ar']}", before=before, after=after)
        return int(center_id)

    def delete_center(self, center_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_center(int(center_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=center_id)
            if before is None:
                raise BusinessError("مركز التكلفة ده مش موجود (ممكن يكون اتمسح).")
            if before["has_children"]:
                raise BusinessError(f"المركز {label(before)} تحته مراكز — امسحهم الأول.")
            # أوامر التوريد (البند الجاي) والقيود (المرحلة 4) هيمنعوا المسح بـ FK RESTRICT — والمركز يتوقف بس.
            deleted = self.repository.delete_center(int(center_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=center_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=center_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
