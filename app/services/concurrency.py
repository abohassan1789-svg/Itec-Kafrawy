"""حماية التعديل في نفس الوقت (Optimistic Locking) — المرحلة 1ج.

الفكرة (قرار المستخدم 2026-11-08 — شبكة 10–20 جهاز، و2–3 على نفس الشاشة):
* الشاشة بتحفظ ``row_version`` اللي فتحت بيه السجل وبتبعته مع «حفظ» و«حذف» (``expected_version``).
* الـ Service جوه الـ transaction: بيقفل الصف (``FOR UPDATE``) ← ``guard.check`` بيقارن الإصدار
  ← الـ UPDATE / DELETE نفسه بشرط ``AND row_version = …`` ← ``guard.ensure`` لو مفيش صف اتأثر.
* أي فرق = ``ConcurrentEditError`` برسالة عربي فيها مين عدّل وإمتى ومن أنهي جهاز (من ``sec.audit_log``)،
  ومفيش حاجة بتتحفظ ولا بتتمسح.

``expected_version``:
* ``None`` = من غير تحقق (أوامر داخلية / اختبارات قديمة).
* ``0`` = «الشاشة شافت إن السجل مش موجود» (زي بيانات الشركة أول مرة، أو سعر يوم لسه ماتسجلش) —
  لو جهاز تاني سجّله في الوقت ده ← تعارض برضه.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.database.db import Database, Executor, get_db
from app.repositories.audit_repository import AuditRepository
from app.services.errors import ConcurrentEditError

LRI, PDI = "⁦", "⁩"           # عزل الكلام الإنجليزي والأرقام عشان مايتقلبش جوه الجملة العربي
REFRESH_HINT = "دوس «تحديث» وعدّل تاني."


def _ltr(text: Any) -> str:
    return f"{LRI}{text}{PDI}"


def _when(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone().strftime("%d/%m/%Y %H:%M")
    return str(value or "")


def conflict_message(change: dict | None, deleted: bool = False) -> str:
    """«السجل ده اتعدّل من جهاز تاني (المستخدم، الوقت) — دوس «تحديث» وعدّل تاني.»"""
    head = "السجل ده اتمسح من جهاز تاني" if deleted else "السجل ده اتعدّل من جهاز تاني"
    change = change or {}
    parts = [_ltr(v) for v in (change.get("username"), _when(change.get("occurred_at"))) if v]
    who = f" ({'، '.join(parts)})" if parts else ""
    tail = "دوس «تحديث»." if deleted else REFRESH_HINT
    text = f"{head}{who} — {tail}"
    if change.get("device_name"):                       # سطر لوحده عشان اسم الجهاز الإنجليزي مايلخبطش الجملة
        text += f"\nالجهاز: {_ltr(change['device_name'])}"
    return text


class ConcurrencyGuard:
    def __init__(self, db: Database | None = None, audit: AuditRepository | None = None) -> None:
        self.audit = audit or AuditRepository(db or get_db())

    def conflict(self, ex: Executor, entity: str, entity_id: Any, actions: tuple[str, ...] | None = None,
                 deleted: bool | None = None) -> ConcurrentEditError:
        """الخطأ برسالته (مين وإمتى من آخر سطر في سجل التعديلات للسجل ده)."""
        change = self.audit.last_change(entity, entity_id, actions, ex=ex)
        if deleted is None:
            deleted = bool(change and change["action"] == "delete")
        elif deleted:
            change = self.audit.last_change(entity, entity_id, ("delete",), ex=ex) or change
        return ConcurrentEditError(conflict_message(change, deleted), deleted=deleted,
                                   changed_by=(change or {}).get("username"),
                                   changed_at=(change or {}).get("occurred_at"),
                                   device=(change or {}).get("device_name"))

    def check(self, ex: Executor, row: dict | None, expected_version: int | None, *, entity: str,
              entity_id: Any, field: str = "row_version", actions: tuple[str, ...] | None = None) -> None:
        """الصف (المقفول ``FOR UPDATE``) لسه على نفس الإصدار اللي الشاشة فتحت بيه؟"""
        if expected_version is None:
            return
        expected = int(expected_version)
        if row is None:
            if expected == 0:
                return
            raise self.conflict(ex, entity, entity_id, deleted=True)
        if expected == 0 or int(row[field]) != expected:
            raise self.conflict(ex, entity, entity_id, actions, deleted=False)

    def ensure(self, ex: Executor, affected: int, expected_version: int | None, *, entity: str,
               entity_id: Any, actions: tuple[str, ...] | None = None) -> None:
        """بعد ``UPDATE/DELETE … AND row_version = …``: لو مفيش صف اتأثر = جهاز تاني سبقك."""
        if expected_version is not None and not affected:
            raise self.conflict(ex, entity, entity_id, actions)
