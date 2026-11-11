"""الموردين (شاشة «الموردين» — المرحلة 3ب، النموذج 1 اختيار المستخدم 2026-11-11).

ردود المستخدم (2026-11-11):
* **الكود** ``SUB-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``SupplierRepository.next_seq``) وثابت.
* **رقم التسجيل الضريبي** اختياري، مايتكررش، أرقام وحروف (نفس العملاء — ``party.normalize_tax_no``).
* **البنك ورقم الحساب / IBAN** اختياريين.
* **«مورد نقدي»** (``SUB-000`` — سكريبت 0020) نظامي للمشتريات الكاش: مابيتمسحش ولا بيتوقف.
* ربط المورد بالعميل **اتأجّل** لحد ما المستخدم يطلبه.

* مفيش حد ائتمان (اتقرر مع مجموعات الموردين) · حساب الرقابة من المجموعة (القرار 2) · افتراضي المجموعة بيتنسخ
  للمورد الجديد (المدة، العملة، ض.ق.م، خصم المنبع).
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version`` (جهازين على نفس المورد).
"""

from __future__ import annotations

import re
from typing import Any

from app.database.db import Database, get_db
from app.repositories.supplier_repository import STATUSES, SupplierRepository, format_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.customer_service import CustomerService
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "party.suppliers", "مورد"
PERM = "purch.suppliers"
MAX_TERMS = 365
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
SYSTEM_STOP = "المورد «{}» نظامي (للمشتريات الكاش) — مابيتوقفش."
NOT_FOUND = "المورد ده مش موجود (ممكن يكون اتمسح)."
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 200), ("contact_person", "المسؤول", 120), ("mobile", "الموبايل", 30),
          ("phone", "التليفون", 30), ("email", "الإيميل", 120), ("address", "العنوان", 300),
          ("bank_name", "البنك", 120), ("bank_account", "رقم الحساب / IBAN", 60), ("notes", "الملاحظات", 500))


class SupplierService:
    def __init__(self, db: Database | None = None, repository: SupplierRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or SupplierRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_suppliers(self, keyword: str = "", status: str = "all", group_id: int | None = None,
                       page: int = 1, page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_suppliers(keyword.strip(), status, group_id, page_size, offset)

    def count_suppliers(self, keyword: str = "", status: str = "all", group_id: int | None = None) -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_suppliers(keyword.strip(), status, group_id)
        return {k: int(row[k]) for k in ("total", "active", "inactive", "no_tax")}

    def lookup_suppliers(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل الموردين (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_suppliers("", "all", None, LOOKUP_LIMIT, 0)

    def get_supplier(self, supplier_id: int) -> dict[str, Any] | None:
        return self.repository.get_supplier(int(supplier_id))

    def group_choices(self) -> list[dict[str, Any]]:
        return self.repository.group_choices()

    def currency_choices(self) -> list[dict[str, Any]]:
        return self.repository.currency_choices()

    def tax_choices(self) -> dict[str, list[dict[str, Any]]]:
        return {"vat": self.repository.tax_choices("add"), "wht": self.repository.tax_choices("deduct")}

    @staticmethod
    def group_defaults(group: dict[str, Any]) -> dict[str, Any]:
        """اللي بيتنسخ من المجموعة للمورد الجديد."""
        return {"group_id": int(group["id"]), "payment_terms_days": int(group["payment_terms_days"]),
                "currency_code": group["currency_code"], "vat_tax_code_id": group["vat_tax_code_id"],
                "wht_tax_code_id": group["wht_tax_code_id"]}

    def new_defaults(self) -> dict[str, Any]:
        """وضع «جديد»: الكود المقترح + المجموعة النظامية «موردين — عام» (أو أول مجموعة نشطة) وافتراضيها."""
        groups = [g for g in self.repository.group_choices() if g["is_active"]]
        group = next((g for g in groups if g["is_system"]), groups[0] if groups else None)
        data: dict[str, Any] = {"code": self.repository.suggested_code(), "is_active": True}
        if group is not None:
            data.update(self.group_defaults(group))
        return data

    @staticmethod
    def needs_tax_warning(data: dict[str, Any]) -> bool:
        """تنبيه بس (مش منع): مورد عليه ض.ق.م ومن غير رقم تسجيل ← اتأكد قبل خصم ض.ق.م المدخلات."""
        return bool(data.get("vat_tax_code_id")) and not (data.get("tax_reg_no") or "").strip() \
            and not data.get("is_system")

    # ---- حفظ / إيقاف / مسح -------------------------------------------------
    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        return int(value) if value not in (None, "") else None

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم المورد مطلوب.")
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
        group = self._int_or_none(data.get("group_id"))
        if group is None:
            raise BusinessError("لازم تختار «المجموعة».")
        try:
            terms = int(data.get("payment_terms_days") if data.get("payment_terms_days") not in (None, "") else 0)
        except (TypeError, ValueError) as exc:
            raise BusinessError("«مدة السداد» لازم تبقى رقم أيام.") from exc
        if not 0 <= terms <= MAX_TERMS:
            raise BusinessError(f"«مدة السداد» لازم تبقى من 0 لـ {MAX_TERMS} يوم (0 = نقدي).")
        currency = (data.get("currency_code") or "").strip().upper()
        if len(currency) != 3:
            raise BusinessError("لازم تختار العملة.")
        clean.update({"group_id": group, "tax_reg_no": CustomerService.clean_tax_no(data.get("tax_reg_no")),
                      "payment_terms_days": terms, "currency_code": currency,
                      "vat_tax_code_id": self._int_or_none(data.get("vat_tax_code_id")),
                      "wht_tax_code_id": self._int_or_none(data.get("wht_tax_code_id")),
                      "is_active": bool(data.get("is_active", True))})
        return clean

    def save_supplier(self, supplier_id: int | None, data: dict[str, Any],
                      expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if supplier_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if supplier_id is not None:
                before = self.repository.get_supplier(int(supplier_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=supplier_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
                clean["seq"] = before["seq"]                         # الكود ثابت بعد الإضافة
                if before["is_system"] and not clean["is_active"]:
                    raise BusinessError(SYSTEM_STOP.format(before["name_ar"]))
            else:
                clean["seq"] = self.repository.next_seq(tx)          # جوه قفل الترقيم
            code = format_code(clean["seq"])
            exclude = None if supplier_id is None else int(supplier_id)
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه مورد بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            if clean["tax_reg_no"]:
                taken = self.repository.tax_taken(clean["tax_reg_no"], exclude, ex=tx)
                if taken:
                    raise BusinessError(f"رقم التسجيل الضريبي «{clean['tax_reg_no']}» متسجل قبل كده للمورد "
                                        f"«{taken['name_ar']}» ({taken['code']}).")
            if supplier_id is None:
                supplier_id = self.repository.insert_supplier(clean, ex=tx)
            else:
                updated = self.repository.update_supplier(int(supplier_id), clean, ex=tx,
                                                          expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=supplier_id)
            after = self.repository.get_supplier(int(supplier_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=supplier_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name_ar']}", before=before, after=after)
        return int(supplier_id)

    def set_active(self, supplier_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات (الموقوف مابيتختارش في مستند جديد)."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_supplier(int(supplier_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=supplier_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if before["is_system"] and not active:
                raise BusinessError(SYSTEM_STOP.format(before["name_ar"]))
            updated = self.repository.set_active(int(supplier_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=supplier_id)
            after = self.repository.get_supplier(int(supplier_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=supplier_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_supplier(self, supplier_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_supplier(int(supplier_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=supplier_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if before["is_system"]:
                raise BusinessError(f"المورد «{before['name_ar']}» نظامي (للمشتريات الكاش) — مابيتمسحش.")
            # TODO (فواتير المشتريات والسندات والشيكات والقيود): المورد اللي عليه حركة مايتمسحش — FK RESTRICT
            # من الجداول دي + رسالة عربي «عليه N فاتورة — وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_supplier(int(supplier_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=supplier_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=supplier_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
