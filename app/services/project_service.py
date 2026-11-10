"""أوامر التوريد / المشاريع (شاشة «أوامر التوريد» — المرحلة 3أ، النموذج 1 اختيار المستخدم 2026-11-10).

ردود المستخدم (2026-11-10):
* **العميل اختياري** (لو اتختار لازم يبقى نشط — القاعدة بتتأكد).
* **الحالة:** جاري / مقفول / ملغي.
* **رقم الأمر بيطلع لوحده** (``0001-Itec``) ويتعدّل عادي، ومايتكررش. الرقم المقترح بيتصرف **وقت الحفظ**
  جوه قفل العدّاد (``acc.next_project_no``) — لو جهاز تاني أخده في نفس الوقت الأمر بياخد اللي بعده.
  لو المستخدم كتب رقم بإيده بيتحفظ زي ما هو (بعد التأكد إنه مش متكرر).
* **شامل / غير شامل · فاتورة / بيان · العملة** على الأمر = **افتراضي** بيتنقل للفاتورة ويتغيّر فيها.
* **تاريخ بداية ونهاية** اختياريين (النهاية مش قبل البداية).

* مركز التكلفة **فرعي نشط** ومطلوب · القيمة ≥ 0 (الصفر مسموح لحد ما تتحدد).
* كل حفظ / مسح في ``sec.audit_log`` في نفس الـ transaction + ``row_version`` (جهازين على نفس الأمر).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import psycopg

from app.database.db import Database, get_db
from app.repositories.project_repository import FILTERS, STATUSES, ProjectRepository
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

ENTITY, ENTITY_LABEL = "acc.projects", "أمر توريد"
PERM = "sales.projects"
PAGE_SIZE = 100
LOOKUP_LIMIT = 5000
MAX_AMOUNT = Decimal("9999999999999999.99")
STATUS_NAMES = {"open": "جاري", "closed": "مقفول", "cancelled": "ملغي"}
DOC_KINDS = {"invoice": "فاتورة", "statement": "بيان"}


def _to_date(value: Any, label: str, required: bool = False) -> date | None:
    if value in (None, ""):
        if required:
            raise BusinessError(f"«{label}» مطلوب.")
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise BusinessError(f"«{label}» مش مكتوب صح.") from exc


class ProjectService:
    def __init__(self, db: Database | None = None, repository: ProjectRepository | None = None,
                 audit: AuditService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or ProjectRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def _filter(status: str) -> str:
        return status if status in FILTERS else "all"

    def list_projects(self, keyword: str = "", status: str = "all", center_id: int | None = None,
                      year: int | None = None, page: int = 1,
                      page_size: int | None = PAGE_SIZE) -> list[dict[str, Any]]:
        offset = (max(1, int(page)) - 1) * page_size if page_size else 0
        return self.repository.list_projects(keyword.strip(), self._filter(status), center_id, year,
                                             page_size, offset)

    def count_projects(self, keyword: str = "", status: str = "all", center_id: int | None = None,
                       year: int | None = None) -> dict[str, int]:
        row = self.repository.count_projects(keyword.strip(), self._filter(status), center_id, year)
        return {k: int(row[k]) for k in ("total", "open", "closed", "cancelled")}

    def lookup_projects(self) -> list[dict[str, Any]]:
        """نافذة البحث F1: كل الأوامر (لحد ``LOOKUP_LIMIT``)."""
        return self.repository.list_projects("", "all", None, None, LOOKUP_LIMIT, 0)

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        return self.repository.get_project(int(project_id))

    def years(self) -> list[int]:
        return self.repository.years()

    def customer_choices(self) -> list[dict[str, Any]]:
        return self.repository.customer_choices()

    def center_choices(self) -> list[dict[str, Any]]:
        return self.repository.center_choices()

    def currency_choices(self) -> list[dict[str, Any]]:
        return self.repository.currency_choices()

    def suggested_code(self, order_date: date | None = None) -> str:
        return self.repository.suggested_code(order_date)

    def new_defaults(self) -> dict[str, Any]:
        """وضع «جديد»: الرقم المقترح + النهارده + جاري + العملة الأساسية + شامل + فاتورة."""
        today = date.today()
        currencies = self.repository.currency_choices()
        base = next((c["code"] for c in currencies if c["is_base"]), currencies[0]["code"] if currencies else "EGP")
        return {"code": self.repository.suggested_code(today), "order_date": today, "status": "open",
                "currency_code": base, "tax_inclusive": True, "doc_kind": "invoice", "amount": Decimal("0")}

    # ---- حفظ / مسح ---------------------------------------------------------
    @staticmethod
    def _int_or_none(value: Any) -> int | None:
        return int(value) if value not in (None, "") else None

    def _clean(self, data: dict[str, Any], new: bool) -> dict[str, Any]:
        name = " ".join((data.get("name") or "").split())
        if not name:
            raise BusinessError("اسم المشروع مطلوب.")
        if len(name) > 200:
            raise BusinessError("«اسم المشروع» أطول من المسموح (200 حرف).")
        code = " ".join(str(data.get("code") or "").split())
        auto = bool(data.get("auto_code")) or (new and not code)
        if not auto:
            if not code:
                raise BusinessError("رقم الأمر مطلوب.")
            if len(code) > 30:
                raise BusinessError("«رقم الأمر» أطول من المسموح (30 حرف).")
        center = self._int_or_none(data.get("cost_center_id"))
        if center is None:
            raise BusinessError("لازم تختار «مركز التكلفة» (مركز فرعي).")
        try:
            amount = Decimal(str(data.get("amount") if data.get("amount") not in (None, "") else 0))
            amount = amount.quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError) as exc:
            raise BusinessError("«القيمة» لازم تبقى رقم.") from exc
        if amount < 0 or amount > MAX_AMOUNT:
            raise BusinessError("«القيمة» لازم تبقى صفر أو أكبر (صفر = لسه ماتحددتش).")
        currency = (data.get("currency_code") or "").strip().upper()
        if len(currency) != 3:
            raise BusinessError("لازم تختار العملة.")
        doc_kind = data.get("doc_kind") or "invoice"
        if doc_kind not in DOC_KINDS:
            raise BusinessError("«فاتورة / بيان» لازم تبقى فاتورة أو بيان.")
        status = data.get("status") or "open"
        if status not in STATUSES:
            raise BusinessError("الحالة لازم تبقى جاري أو مقفول أو ملغي.")
        order_date = _to_date(data.get("order_date"), "تاريخ الأمر", required=True)
        start = _to_date(data.get("start_date"), "تاريخ البداية")
        end = _to_date(data.get("end_date"), "تاريخ النهاية")
        if start and end and end < start:
            raise BusinessError("«تاريخ النهاية» لازم يبقى بعد «تاريخ البداية» أو في نفس اليوم.")
        notes = (data.get("notes") or "").strip() or None
        if notes and len(notes) > 500:
            raise BusinessError("«الملاحظات» أطول من المسموح (500 حرف).")
        return {"code": None if auto else code, "name": name,
                "customer_id": self._int_or_none(data.get("customer_id")), "cost_center_id": center,
                "amount": amount, "currency_code": currency, "tax_inclusive": bool(data.get("tax_inclusive", True)),
                "doc_kind": doc_kind, "order_date": order_date, "start_date": start, "end_date": end,
                "status": status, "notes": notes}

    def save_project(self, project_id: int | None, data: dict[str, Any],
                     expected_version: int | None = None) -> int:
        """``data["auto_code"]`` = True (أو رقم فاضي في «جديد») ← الرقم بيتصرف من العدّاد وقت الحفظ."""
        PermissionService.require(f"{PERM}.create" if project_id is None else f"{PERM}.edit")
        PermissionService.require(f"{PERM}.save")
        clean = self._clean(data, new=project_id is None)
        try:
            with self.db.atomic() as tx:
                before = None
                if project_id is not None:
                    before = self.repository.get_project(int(project_id), ex=tx, for_update=True)
                    self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=project_id)
                    if before is None:
                        raise BusinessError("أمر التوريد ده مش موجود (ممكن يكون اتمسح).")
                exclude = None if project_id is None else int(project_id)
                if clean["code"] is None:
                    clean["code"] = self.repository.next_code(clean["order_date"], tx)   # جوه قفل العدّاد
                else:
                    taken = self.repository.code_taken(clean["code"], exclude, ex=tx)
                    if taken:
                        raise BusinessError(f"رقم الأمر «{clean['code']}» موجود قبل كده («{taken['name']}») — "
                                            "الرقم مايتكررش.")
                if project_id is None:
                    project_id = self.repository.insert_project(clean, ex=tx)
                else:
                    updated = self.repository.update_project(int(project_id), clean, ex=tx,
                                                             expected_version=expected_version)
                    self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=project_id)
                after = self.repository.get_project(int(project_id), ex=tx)
                self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                                  entity_id=project_id, entity_label=ENTITY_LABEL,
                                  record_label=f"{clean['code']} {clean['name']}", before=before, after=after)
        except psycopg.errors.UniqueViolation as exc:            # جهاز تاني سجّل نفس الرقم في نفس اللحظة
            raise BusinessError(f"رقم الأمر «{clean['code']}» اتسجل من جهاز تاني دلوقتي — غيّره أو سيبه "
                                "يطلع لوحده.") from exc
        except psycopg.errors.RaiseException as exc:             # رسايل الـ trigger (عربي أصلاً)
            raise BusinessError(exc.diag.message_primary or str(exc)) from exc
        return int(project_id)

    def delete_project(self, project_id: int, expected_version: int | None = None) -> None:
        PermissionService.require(f"{PERM}.delete")
        with self.db.atomic() as tx:
            before = self.repository.get_project(int(project_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=project_id)
            if before is None:
                raise BusinessError("أمر التوريد ده مش موجود (ممكن يكون اتمسح).")
            # TODO (الفواتير المرحلة 8 · المصروفات المرحلة 9 · القيود المرحلة 4): الأمر اللي عليه حركة
            # مايتمسحش — FK RESTRICT من الجداول دي + رسالة «عليه N فاتورة — اقفله بدل ما تمسحه».
            deleted = self.repository.delete_project(int(project_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=project_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=project_id, entity_label=ENTITY_LABEL,
                              record_label=f"{before['code']} {before['name']}", before=before)
