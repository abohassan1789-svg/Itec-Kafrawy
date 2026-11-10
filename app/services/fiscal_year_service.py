"""السنين المالية والفترات (شاشة «السنة المالية والفترات» — المرحلة 1ب).

قرارات المستخدم (docs/03 القسم 8):
* القفل **سنوي بس** — السنة يا مفتوحة يا مقفولة، ومفيش قفل شهري.
* الشهور بتتعمل لوحدها من «من/إلى» (في القاعدة) وهي للتقارير بس.
* الترقيم بيبدأ من 1 كل سنة.

القواعد (الـ Service بيدي رسالة واضحة، والقاعدة بتحمي برضه):
* مفيش تداخل ولا فجوة بين السنين · المدة لحد 24 شهر و«من» قبل «إلى» · الكود مش متكرر.
* السنة المقفولة مابتتعدّلش ولا بتتمسح — لازم تتفتح الأول.
* المسح للسنة الأولى أو الأخيرة بس (اللي في النص = فجوة).
* «قفل / فتح السنة» إجراء ليه صلاحية لوحده (``sys.fiscal_years.lock``) وبكلمة سر إجراء،
  وبيتسجل في ``sec.audit_log`` (``lock`` / ``unlock``).
* منع الترحيل في السنة المقفولة (ومنع مسح سنة عليها قيود) هيتعمل مع القيود في المرحلة 4.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any

from app.database.db import Database, get_db
from app.repositories.fiscal_year_repository import FiscalYearRepository
from app.repositories.settings_repository import SettingsRepository
from app.security.session import SESSION
from app.services.audit_service import AuditService
from app.services.concurrency import ConcurrencyGuard
from app.services.company_service import MONTHS_AR
from app.services.errors import BusinessError
from app.services.permission_service import PermissionService

MAX_MONTHS = 24
STATUS_OPEN, STATUS_CLOSED = "open", "closed"
LOCK_CODE = "sys.fiscal_years.lock"
LOCK_ACTION_NAME = "قفل / فتح السنة"
ENTITY = "core.fiscal_years"
ENTITY_LABEL = "سنة مالية"


def fmt(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def build_periods(start: date, end: date) -> list[dict[str, Any]]:
    """نفس اللي بيعمله الـ trigger في القاعدة: شهر شهر، وأول وآخر شهر بيتقصّوا على حدود السنة."""
    periods: list[dict[str, Any]] = []
    if not start or not end or start > end:
        return periods
    month = date(start.year, start.month, 1)
    while month <= end:
        last = date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
        periods.append({"period_no": len(periods) + 1, "name_ar": f"{MONTHS_AR[month.month - 1]} {month.year}",
                        "start_date": max(month, start), "end_date": min(last, end)})
        month = last + timedelta(days=1)
    return periods


def default_code(start: date, end: date) -> str:
    return str(start.year) if start.year == end.year else f"{start.year}/{end.year}"


class FiscalYearService:
    def __init__(self, db: Database | None = None, repository: FiscalYearRepository | None = None,
                 audit: AuditService | None = None, permissions: PermissionService | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or FiscalYearRepository(self.db)
        self.settings = SettingsRepository(self.db)
        self.audit = audit or AuditService(self.db)
        self.guard = ConcurrencyGuard(self.db)
        self.permissions = permissions or PermissionService(self.db)

    # ---- قراية -------------------------------------------------------------
    @staticmethod
    def _decorate(row: dict[str, Any], today: date) -> dict[str, Any]:
        row["is_current"] = row["start_date"] <= today <= row["end_date"]
        row["is_closed"] = row["status"] == STATUS_CLOSED
        return row

    def list_years(self, keyword: str = "") -> list[dict[str, Any]]:
        today = date.today()
        return [self._decorate(r, today) for r in self.repository.list_years(keyword.strip())]

    def get_year(self, year_id: int) -> dict[str, Any] | None:
        row = self.repository.get_year(int(year_id))
        if row is None:
            return None
        row = self._decorate(row, date.today())
        row["periods"] = self.repository.list_periods(int(year_id))
        return row

    @staticmethod
    def summary(rows: list[dict[str, Any]]) -> dict[str, int]:
        closed = sum(1 for r in rows if r["status"] == STATUS_CLOSED)
        return {"total": len(rows), "open": len(rows) - closed, "closed": closed}

    def suggest_new_year(self) -> dict[str, Any]:
        """الافتراضي لـ «سنة جديدة»: بعد آخر سنة على طول، أو من «أول شهر في السنة» في بيانات الشركة."""
        spans = self.repository.year_spans()
        if spans:
            start = spans[-1]["end_date"] + timedelta(days=1)
        else:
            company = self.settings.get_company()
            start_month = int(company["fiscal_year_start_month"]) if company else 1
            today = date.today()
            start = date(today.year if today.month >= start_month else today.year - 1, start_month, 1)
        end = add_months(start, 12) - timedelta(days=1)
        code = default_code(start, end)
        return {"year_code": code, "name_ar": f"السنة المالية {code}", "start_date": start, "end_date": end,
                "status": STATUS_OPEN, "notes": None, "periods": build_periods(start, end)}

    # ---- التحقق ------------------------------------------------------------
    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        code = (data.get("year_code") or "").strip()
        if not code:
            raise BusinessError("«السنة» مطلوبة (مثال: 2026).")
        if len(code) > 20:
            raise BusinessError("«السنة» أطول من المسموح (20 حرف).")
        name = (data.get("name_ar") or "").strip() or None
        if name and len(name) > 100:
            raise BusinessError("«الاسم» أطول من المسموح (100 حرف).")
        notes = (data.get("notes") or "").strip() or None
        if notes and len(notes) > 500:
            raise BusinessError("«ملاحظات» أطول من المسموح (500 حرف).")
        start, end = data.get("start_date"), data.get("end_date")
        if not isinstance(start, date) or not isinstance(end, date):
            raise BusinessError("«من» و«إلى» مطلوبين.")
        if start > end:
            raise BusinessError("«من» لازم يبقى قبل «إلى».")
        if end >= add_months(start, MAX_MONTHS):
            raise BusinessError(f"مدة السنة المالية أقصاها {MAX_MONTHS} شهر.")
        return {"year_code": code, "name_ar": name, "start_date": start, "end_date": end, "notes": notes}

    @staticmethod
    def _check_sequence(spans: list[dict[str, Any]]) -> None:
        """مفيش تداخل ولا فجوة بعد التغيير (``spans`` = كل السنين بعد التغيير)."""
        ordered = sorted(spans, key=lambda s: s["start_date"])
        for prev, cur in zip(ordered, ordered[1:]):
            if cur["start_date"] <= prev["end_date"]:
                raise BusinessError(f"السنة {cur['year_code']} متداخلة مع السنة {prev['year_code']} "
                                    f"({fmt(prev['start_date'])} ← {fmt(prev['end_date'])}).")
            if cur["start_date"] != prev["end_date"] + timedelta(days=1):
                raise BusinessError(f"مينفعش يبقى فيه فجوة بين السنين: السنة {cur['year_code']} لازم تبدأ "
                                    f"{fmt(prev['end_date'] + timedelta(days=1))} (تاني يوم بعد نهاية "
                                    f"السنة {prev['year_code']}).")

    # ---- حفظ ---------------------------------------------------------------
    def save_year(self, year_id: int | None, data: dict[str, Any], expected_version: int | None = None) -> int:
        PermissionService.require("sys.fiscal_years.create" if year_id is None else "sys.fiscal_years.edit")
        PermissionService.require("sys.fiscal_years.save")
        clean = self._clean(data)
        with self.db.atomic() as tx:
            self.repository.lock_table(tx)
            before = None
            if year_id is not None:
                before = self.repository.get_year(int(year_id), ex=tx, for_update=True)
                self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=year_id)
                if before is None:
                    raise BusinessError("السنة المالية دي مش موجودة (ممكن تكون اتمسحت).")
                if before["status"] == STATUS_CLOSED:
                    raise BusinessError(f"السنة المالية {before['year_code']} مقفولة ومينفعش تتعدّل — افتحها الأول.")
            if self.repository.code_exists(clean["year_code"], year_id, ex=tx):
                raise BusinessError(f"السنة «{clean['year_code']}» متسجلة قبل كده.")
            spans = [s for s in self.repository.year_spans(ex=tx) if year_id is None or s["id"] != int(year_id)]
            self._check_sequence([*spans, {"id": year_id, **clean}])
            if year_id is None:
                year_id = self.repository.insert_year(clean, ex=tx)
            else:
                updated = self.repository.update_year(int(year_id), clean, ex=tx, expected_version=expected_version)
                self.guard.ensure(tx, updated, expected_version, entity=ENTITY, entity_id=year_id)
            after = self.repository.get_year(int(year_id), ex=tx)
            self.audit.record(tx, action="create" if before is None else "update", entity=ENTITY,
                              entity_id=year_id, entity_label=ENTITY_LABEL, record_label=clean["year_code"],
                              before=before, after=after)
        return int(year_id)

    def delete_year(self, year_id: int, expected_version: int | None = None) -> None:
        PermissionService.require("sys.fiscal_years.delete")
        with self.db.atomic() as tx:
            self.repository.lock_table(tx)
            before = self.repository.get_year(int(year_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=year_id)
            if before is None:
                raise BusinessError("السنة المالية دي مش موجودة (ممكن تكون اتمسحت).")
            if before["status"] == STATUS_CLOSED:
                raise BusinessError(f"السنة المالية {before['year_code']} مقفولة ومينفعش تتمسح.")
            spans = self.repository.year_spans(ex=tx)
            ids = [s["id"] for s in spans]
            if int(year_id) not in (ids[0], ids[-1]):
                raise BusinessError("مينفعش تمسح سنة في النص (هيبقى فيه فجوة) — المسح لأول سنة أو آخر سنة بس.")
            if self.repository.has_document_numbers(int(year_id), ex=tx):
                raise BusinessError(f"السنة المالية {before['year_code']} اتصرف فيها أرقام مستندات ومينفعش تتمسح.")
            deleted = self.repository.delete_year(int(year_id), ex=tx, expected_version=expected_version)
            self.guard.ensure(tx, deleted, expected_version, entity=ENTITY, entity_id=year_id)
            self.audit.record(tx, action="delete", entity=ENTITY, entity_id=year_id, entity_label=ENTITY_LABEL,
                              record_label=before["year_code"], before=before)

    # ---- قفل / فتح ---------------------------------------------------------
    def needs_lock_password(self) -> bool:
        """المستخدم الحالي محتاج كلمة سر «قفل / فتح السنة»؟ (المدير لأ)."""
        if SESSION.user is None or SESSION.is_admin:
            return False
        return self.permissions.verify_action_password(SESSION.user, LOCK_CODE, "") is not None

    def set_closed(self, year_id: int, closed: bool, action_password: str | None = None,
                   expected_version: int | None = None) -> None:
        PermissionService.require(LOCK_CODE)
        if SESSION.user is not None and not SESSION.is_admin:
            check = self.permissions.verify_action_password(SESSION.user, LOCK_CODE, action_password or "")
            if check is False:
                raise BusinessError(f"كلمة سر «{LOCK_ACTION_NAME}» غلط.")
        with self.db.atomic() as tx:
            before = self.repository.get_year(int(year_id), ex=tx, for_update=True)
            self.guard.check(tx, before, expected_version, entity=ENTITY, entity_id=year_id)
            if before is None:
                raise BusinessError("السنة المالية دي مش موجودة (ممكن تكون اتمسحت).")
            if (before["status"] == STATUS_CLOSED) == closed:
                raise BusinessError(f"السنة المالية {before['year_code']} "
                                    f"{'مقفولة' if closed else 'مفتوحة'} أصلاً.")
            changed = self.repository.set_status(int(year_id), closed, SESSION.user_id, ex=tx,
                                                 expected_version=expected_version)
            self.guard.ensure(tx, changed, expected_version, entity=ENTITY, entity_id=year_id)
            self.audit.record(tx, action="lock" if closed else "unlock", entity=ENTITY, entity_id=year_id,
                              entity_label=ENTITY_LABEL, record_label=before["year_code"], before=before,
                              after=self.repository.get_year(int(year_id), ex=tx))
