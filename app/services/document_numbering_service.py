"""ترقيم المستندات — **أوتوماتيك من غير شاشة** (قرار المستخدم 2026-11-08).

* الشكل: البادئة-السنة-المسلسل (``INV-2026-00001``)، والمسلسل **بيبدأ من 1 كل سنة مالية**.
* العدّاد بتاع كل (نوع، سنة) بيتعمل لوحده مع أول مستند في السنة — مفيش حاجة تتجهز.
* الرقم بيتصرف **وقت الحفظ** جوه نفس transaction المستند (``tx``): لو الحفظ فشل الرقم بيرجع.
* جهازين مايخدوش نفس الرقم (القاعدة بتقفل صف العدّاد لحد آخر الـ transaction).
* مفيش رقم في سنة مقفولة أو في تاريخ مالوش سنة مالية — والرسالة بالعربي.

الاستخدام من أي Service مستند::

    with self.db.atomic() as tx:
        doc_no = self.numbering.next_number("sales_invoice", doc_date, tx)
        ... INSERT المستند بـ doc_no ...
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

from app.database.db import Database, Tx, get_db
from app.repositories.document_sequence_repository import DocumentSequenceRepository
from app.services.errors import BusinessError


class DocumentNumberingService:
    def __init__(self, db: Database | None = None, repository: DocumentSequenceRepository | None = None) -> None:
        self.db = db or get_db()
        self.repository = repository or DocumentSequenceRepository(self.db)

    def next_number(self, doc_type: str, doc_date: date, tx: Tx) -> str:
        """بيصرف رقم المستند الجاي — لازم يتنادى جوه transaction الحفظ."""
        if tx is None:
            raise BusinessError("صرف رقم المستند لازم يبقى جوه نفس عملية حفظ المستند.")
        try:
            return self.repository.next_number(doc_type, doc_date, tx)
        except psycopg.errors.RaiseException as exc:
            raise BusinessError(exc.diag.message_primary or str(exc)) from exc

    def peek_next(self, doc_type: str, doc_date: date) -> str | None:
        """الرقم الجاي المتوقع (للعرض في المستند الجديد قبل الحفظ) — مش محجوز."""
        return self.repository.peek_next(doc_type, doc_date)

    def get_type(self, doc_type: str) -> dict[str, Any] | None:
        return self.repository.get_type(doc_type)

    def list_types(self) -> list[dict[str, Any]]:
        return self.repository.list_types()

    def list_sequences(self, fiscal_year_id: int) -> list[dict[str, Any]]:
        return self.repository.list_sequences(int(fiscal_year_id))
