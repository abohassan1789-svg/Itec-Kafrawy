"""الشركاء (شاشة «الشركاء» — المرحلة 3ب، النموذج 1 اختيار المستخدم 2026-11-14).

المستخدم اختار النموذج 1 من غير رد على الأسئلة ← اتعمل بالاقتراحات:
* **الكود** ``PRT-001`` بيتولّد لوحده وقت الحفظ جوه قفل (``PartnerRepository.next_seq``) وثابت.
* **نسبة الربح** و**نسبة الخسارة** من 0 لـ 100 (رقمين عشريين) — الشاشة فيها «الخسارة بنفس نسبة الربح».
* **مجموع نسب الشركاء النشطين ≠ 100%** ← تنبيه بس (``share_warning``) — المنع وقت توزيع الأرباح (المرحلة 5).
* **الشريك اللي خرج (موقوف) نسبته لازم تبقى 0** (والقاعدة بتحمي ده كمان).
* **رأس المال المكتتب** رقم مرجعي — المدفوع الفعلي من القيود على 31 رأس المال ببُعد الشريك (المرحلة 5).
* نوع الشريك: متضامن / موصي · الرقم القومي وتاريخ الدخول اختياريين.
* كل حفظ / إيقاف / مسح في ``sec.audit_log`` في نفس الـ transaction (بالنسب القديمة والجديدة) + ``row_version``.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.database.db import Database, get_db
from app.repositories.partner_repository import PARTNER_TYPES, STATUSES, PartnerRepository, format_code
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "party.partners", "شريك"
PERM = "acc.partners"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
NOT_FOUND = "الشريك ده مش موجود (ممكن يكون اتمسح)."
TYPE_LABELS = {"general": "متضامن", "limited": "موصي"}
HUNDRED = Decimal("100")
MAX_CAPITAL = Decimal("9999999999999999.99")
MIN_JOIN, MAX_JOIN = date(1950, 1, 1), date(2100, 12, 31)
INACTIVE_SHARE = ("الشريك اللي خرج (موقوف) نسبته لازم تبقى 0 — صفّر نسبة الربح والخسارة الأول "
                  "(وزّعها على الباقيين).")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# (المفتاح، الاسم، أقصى طول) للخانات النصية الاختيارية
_TEXTS = (("name_en", "الاسم EN", 200), ("mobile", "الموبايل", 30), ("phone", "التليفون", 30),
          ("email", "الإيميل", 120), ("address", "العنوان", 300), ("notes", "الملاحظات", 500))


def fmt_percent(value: Any) -> str:
    """50.00 ← «50» · 33.50 ← «33.5»."""
    text = f"{Decimal(value or 0):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def share_warning(profit: Any, loss: Any) -> str | None:
    """تنبيه لو مجموع نسب الشركاء النشطين مش 100% (None = مظبوط)."""
    profit, loss = Decimal(profit or 0), Decimal(loss or 0)
    if profit == HUNDRED and loss == HUNDRED:
        return None
    return (f"مجموع نسب الشركاء النشطين: الربح {fmt_percent(profit)}% · الخسارة {fmt_percent(loss)}% — "
            "لازم 100% قبل توزيع الأرباح")


class PartnerService:
    def __init__(self, db: Database | None = None, repository: PartnerRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or PartnerRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    def list_partners(self, keyword: str = "", status: str = "all", page: int = 1,
                      page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        status = status if status in STATUSES else "all"
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_partners(keyword.strip(), status, page_size, offset)

    def count_partners(self, keyword: str = "", status: str = "all") -> dict:
        status = status if status in STATUSES else "all"
        row = self.repository.count_partners(keyword.strip(), status)
        return {k: int(row[k]) for k in ("total", "active", "inactive")}

    def share_totals(self, exclude_id: int | None = None) -> dict[str, Any]:
        """مجموع نسب الشركاء النشطين (``exclude_id`` = الشريك اللي بيتعدّل — عشان الشاشة تجمع اللي مكتوب)."""
        row = self.repository.share_totals(None if exclude_id is None else int(exclude_id))
        return {"profit": Decimal(row["profit"]), "loss": Decimal(row["loss"]),
                "capital": Decimal(row["capital"]), "partners": int(row["partners"])}

    def lookup_partners(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل الشركاء (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_partners("", "all", LOOKUP_LIMIT, 0)

    def get_partner(self, partner_id: int) -> dict[str, Any] | None:
        return self.repository.get_partner(int(partner_id))

    def new_defaults(self) -> dict[str, Any]:
        return {"code": self.repository.suggested_code(), "is_active": True, "partner_type": "general",
                "profit_percent": Decimal("0"), "loss_percent": Decimal("0"), "subscribed_capital": Decimal("0")}

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

    @staticmethod
    def _decimal(value: Any, label: str) -> Decimal:
        try:
            number = Decimal(str(value if value not in (None, "") else 0).replace(",", ""))
        except InvalidOperation as exc:
            raise BusinessError(f"«{label}» لازم يبقى رقم.") from exc
        if not number.is_finite():
            raise BusinessError(f"«{label}» لازم يبقى رقم.")
        return number.quantize(Decimal("0.01"))

    @classmethod
    def clean_percent(cls, value: Any, label: str) -> Decimal:
        number = cls._decimal(value, label)
        if not Decimal("0") <= number <= HUNDRED:
            raise BusinessError(f"«{label}» لازم تبقى من 0 لـ 100.")
        return number

    def _clean(self, data: dict[str, Any]) -> dict[str, Any]:
        name = " ".join((data.get("name_ar") or "").split())
        if not name:
            raise BusinessError("اسم الشريك مطلوب.")
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
        partner_type = data.get("partner_type") or "general"
        if partner_type not in PARTNER_TYPES:
            raise BusinessError("«نوع الشريك» لازم يبقى متضامن أو موصي.")
        joined = data.get("join_date") or None
        if joined is not None:
            if not isinstance(joined, date):
                try:
                    joined = date.fromisoformat(str(joined))
                except ValueError as exc:
                    raise BusinessError("«تاريخ الدخول» مش مكتوب صح.") from exc
            if not MIN_JOIN <= joined <= MAX_JOIN:
                raise BusinessError("«تاريخ الدخول» برّه المسموح.")
        profit = self.clean_percent(data.get("profit_percent"), "نسبة الربح")
        loss = self.clean_percent(data.get("loss_percent"), "نسبة الخسارة")
        capital = self._decimal(data.get("subscribed_capital"), "رأس المال المكتتب")
        if capital < 0:
            raise BusinessError("«رأس المال المكتتب» مايبقاش سالب.")
        if capital > MAX_CAPITAL:
            raise BusinessError("«رأس المال المكتتب» أكبر من المسموح.")
        active = bool(data.get("is_active", True))
        if not active and (profit or loss):
            raise BusinessError(INACTIVE_SHARE)
        clean.update({"partner_type": partner_type, "national_id": self.clean_national_id(data.get("national_id")),
                      "join_date": joined, "profit_percent": profit, "loss_percent": loss,
                      "subscribed_capital": capital, "is_active": active})
        return clean

    def save_partner(self, partner_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require(f"{PERM}.create" if partner_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            before = None
            if partner_id is not None:
                before = self.repository.get_partner(int(partner_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=partner_id)
                if before is None:
                    raise BusinessError(NOT_FOUND)
                clean["seq"] = before["seq"]                         # الكود ثابت بعد الإضافة
            else:
                clean["seq"] = self.repository.next_seq(tx)          # جوه قفل الترقيم
            code = format_code(clean["seq"])
            exclude = None if partner_id is None else int(partner_id)
            taken = self.repository.name_taken(clean["name_ar"], exclude, ex=tx)
            if taken:
                raise BusinessError(f"فيه شريك بنفس الاسم «{taken['name_ar']}» ({taken['code']}).")
            if clean["national_id"]:
                taken = self.repository.national_id_taken(clean["national_id"], exclude, ex=tx)
                if taken:
                    raise BusinessError(f"الرقم القومي ده متسجل قبل كده للشريك «{taken['name_ar']}» "
                                        f"({taken['code']}).")
            if partner_id is None:
                partner_id = self.repository.insert_partner(clean, ex=tx)
            else:
                updated = self.repository.update_partner(int(partner_id), clean, ex=tx,
                                                         expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=partner_id)
            after = self.repository.get_partner(int(partner_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=partner_id, entity_label=ENTITY_LABEL,
                              record_label=f"{code} {clean['name_ar']}", before=before, after=after)
        return int(partner_id)

    def set_active(self, partner_id: int, active: bool, expected_version: int | None = None) -> None:
        """«إيقاف / تفعيل» من شريط الأدوات — الإيقاف بس لو النسب اتصفّرت."""
        PermissionService.require(f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        with self.db.atomic() as tx:
            before = self.repository.get_partner(int(partner_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=partner_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            if bool(before["is_active"]) == bool(active):
                return
            if not active and (before["profit_percent"] or before["loss_percent"]):
                raise BusinessError(INACTIVE_SHARE)
            updated = self.repository.set_active(int(partner_id), bool(active), ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=partner_id)
            after = self.repository.get_partner(int(partner_id), ex=tx)
            self.audit.record(tx, action="update", entity=ENTITY, entity_id=partner_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before, after=after)

    def delete_partner(self, partner_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_partner(int(partner_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=partner_id)
            if before is None:
                raise BusinessError(NOT_FOUND)
            # TODO (القيود — المرحلة 4): الشريك اللي عليه قيد مايتمسحش — FK RESTRICT من سطور القيود
            # + رسالة عربي «عليه حركة — وقّفه بدل ما تمسحه».
            deleted = self.repository.delete_partner(int(partner_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=partner_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=partner_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name_ar']}", before=before)
