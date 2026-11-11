"""الموظفين (شاشة «الموظفين» — المرحلة 3ب، النموذج 1 اختيار المستخدم 2026-11-11).

ردود المستخدم (2026-11-11):
* **الكود** ``EMP-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``EmployeeRepository.next_seq``) وثابت (زي الموردين).
* **الرقم القومي** و**تاريخ التعيين** اختياريين (الرقم القومي 14 رقم ومايتكررش لو اتكتب).
* **القسم نص حر**: اللي يتكتب بيتحفظ، وأي قسم جديد بيظهر لوحده في قايمة الاختيار بعد كده. لو اتكتب قسم موجود
  بشكل تاني (مسافات / كابيتال) بيتحفظ بنفس كتابة الموجود عشان القايمة ماتتكررش.
* **مفيش حد للعهدة** · **مفيش ربط بمستخدم البرنامج** (المندوب بيتختار بإيده في فاتورة البيع).
* الأدوار (مندوب / أمين خزينة / أمين مخزن / بياخد عهدة) — كل شاشة جاية هتعرض الموظفين اللي ليهم الدور بتاعها.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version`` (جهازين على نفس الموظف).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.database.db import Database, get_db
from app.repositories.employee_repository import ROLE_COLUMNS, STATUSES, EmployeeRepository, format_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "party.employees", "موظف"
PERM = "hr.employees"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
NOT_FOUND = "الموظف ده مش موجود (ممكن يكون اتمسح)."
ROLE_LABELS = {"sales_rep": "مندوب", "treasurer": "أمين خزينة", "storekeeper": "أمين مخزن", "custody": "عهدة"}
MIN_HIRE, MAX_HIRE = date(1950, 1, 1), date(2100, 12, 31)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 200), ("job_title", "الوظيفة", 120), ("mobile", "الموبايل", 30),
          ("phone", "التليفون", 30), ("email", "الإيميل", 120), ("address", "العنوان", 300),
          ("notes", "الملاحظات", 500))


class EmployeeService:
    def __init__(self, db: Database | None = None, repository: EmployeeRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or EmployeeRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def _role(role: str | None) -> str | None:
        return role if role in ROLE_COLUMNS else None

    def list_employees(self, keyword: str = "", status: str = "all", role: str | None = None,
                       page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_employees(keyword.strip(), status, self._role(role), page_size, offset)

    def count_employees(self, keyword: str = "", status: str = "all", role: str | None = None) -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_employees(keyword.strip(), status, self._role(role))
        return {k: int(row[k]) for k in ("total", "active", "inactive", "sales_reps")}

    def lookup_employees(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل الموظفين (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_employees("", "all", None, LOOKUP_LIMIT, 0)

    def get_employee(self, employee_id: int) -> dict[str, Any] | None:
        return self.repository.get_employee(int(employee_id))

    def department_choices(self) -> list[str]:
        return self.repository.department_choices()

    def new_defaults(self) -> dict[str, Any]:
        return {"code": self.repository.suggested_code(), "is_active": True}

    @staticmethod
    def roles_text(row: dict[str, Any]) -> str:
        return " · ".join(label for key, label in ROLE_LABELS.items() if row.get(ROLE_COLUMNS[key]))

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    @staticmethod
    def clean_national_id(value: Any) -> str | None:
        """الرقم القومي: اختياري، 14 رقم بالظبط (المسافات والشُرَط بتتشال)."""
        text = re.sub(r"[\s-]", "", str(value or ""))
        if not text:
            return None
        if not re.fullmatch(r"[0-9]{14}", text):
            raise BusinessError(f"«الرقم القومي» لازم يبقى 14 رقم (مكتوب {len(text)}).")
        return text

    def _clean_department(self, value: Any, ex: Any) -> str | None:
        """القسم نص حر — لو هو نفس قسم موجود بشكل تاني بياخد كتابة الموجود."""
        text = " ".join(str(value or "").split())
        if not text:
            return None
        if len(text) > 80:
            raise BusinessError("«القسم» أطول من المسموح (80 حرف).")
        for existing in self.repository.department_choices(ex=ex):
            if existing.casefold() == text.casefold():
                return existing
        return text

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم الموظف مطلوب.")
        if len(name) > 200:
            raise BusinessError("«الاسم» أطول من المسموح (200 حرف).")
        clean: dict[str, Any] = {"name_ar": name}
        for key, label, size in _TEXTS:
            value = (data.get(key) or "").strip() or None
            if value and len(value) > size:
                raise BusinessError(f"«{label}» أطول من المسموح ({size} حرف).")
            clean[key] = value
        if clean["email"] and not _EMAIL.match(clean["email"]):
            raise BusinessError("«الإيميل» مش مكتوب صح (مثال: name@company.com).")
        hire = data.get("hire_date") or None
        if hire is not None:
            if not isinstance(hire, date):
                try:
                    hire = date.fromisoformat(str(hire))
                except ValueError as exc:
                    raise BusinessError("«تاريخ التعيين» مش مكتوب صح.") from exc
            if not MIN_HIRE <= hire <= MAX_HIRE:
                raise BusinessError("«تاريخ التعيين» برّه المسموح.")
        clean.update({"department": data.get("department"), "national_id": self.clean_national_id(data.get("national_id")),
                      "hire_date": hire, "is_active": bool(data.get("is_active", True))})
        for column in ROLE_COLUMNS.values():
            clean[column] = bool(data.get(column, False))
        return clean

    def save_employee(self, employee_id: int | None, data: dict[str, Any],
                      expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if employee_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if employee_id is not None:
                before = self.repository.get_employee(int(employee_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=employee_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
                clean["seq"] = before["seq"]                         # الكود ثابت بعد الإضافة
            else:
                clean["seq"] = self.repository.next_seq(tx)          # جوه قفل الترقيم
            clean["department"] = self._clean_department(clean["department"], tx)
            code = format_code(clean["seq"])
            exclude = None if employee_id is None else int(employee_id)
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه موظف بنفس الاسم «{taken['name_ar']}» ({taken['code']}) — "
                                    "لو شخص تاني ميّزه (مثلاً اسم الأب).")
            if clean["national_id"]:
                taken = self.repository.national_id_taken(clean["national_id"], exclude, ex=tx)
                if taken:
                    raise BusinessError(f"الرقم القومي ده متسجل قبل كده للموظف «{taken['name_ar']}» "
                                        f"({taken['code']}).")
            if employee_id is None:
                employee_id = self.repository.insert_employee(clean, ex=tx)
            else:
                updated = self.repository.update_employee(int(employee_id), clean, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=employee_id)
            after = self.repository.get_employee(int(employee_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=employee_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name_ar']}", before=before, after=after)
        return int(employee_id)

    def set_active(self, employee_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات (الموقوف مابيتختارش في مستند جديد)."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_employee(int(employee_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=employee_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            updated = self.repository.set_active(int(employee_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=employee_id)
            after = self.repository.get_employee(int(employee_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=employee_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_employee(self, employee_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_employee(int(employee_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=employee_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            # TODO (العهد والخزائن والمخازن والفواتير والقيود): الموظف اللي عليه حركة مايتمسحش — FK RESTRICT
            # من الجداول دي + رسالة عربي «عليه N عهدة — وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_employee(int(employee_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=employee_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=employee_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
