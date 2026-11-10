"""SQL ترقيم المستندات (``core.document_types`` و``core.document_sequences``) — المرحلة 1ب.

الترقيم أوتوماتيك (من غير شاشة): ``core.next_document_no`` في القاعدة هي اللي بتعمل العدّاد
وبتزوّده وبتقفل الصف. صرف الرقم لازم يبقى جوه ``Tx`` الحفظ بتاع المستند نفسه.
"""

from __future__ import annotations

from datetime import date

from app.database.db import Database, Executor, Tx, get_db

_PEEK_SQL = """
    SELECT core.format_document_no(t.prefix, core.fiscal_year_label(y.start_date), t.digits,
                                   coalesce(s.last_no, 0) + 1) AS next_no
    FROM core.document_types t
    JOIN core.fiscal_years y ON %(d)s BETWEEN y.start_date AND y.end_date
    LEFT JOIN core.document_sequences s ON s.doc_type = t.code AND s.fiscal_year_id = y.id
    WHERE t.code = %(t)s
"""


class DocumentSequenceRepository:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db or get_db()

    def _x(self, ex: Executor | None) -> Executor:
        return ex if ex is not None else self.db

    def next_number(self, doc_type: str, doc_date: date, tx: Tx) -> str:
        """بيصرف الرقم (بيزوّد العدّاد ويقفله لحد آخر الـ transaction)."""
        return tx.fetch_value("SELECT core.next_document_no(%s, %s)", [doc_type, doc_date])

    def peek_next(self, doc_type: str, doc_date: date, ex: Executor | None = None) -> str | None:
        """الرقم الجاي المتوقع للعرض بس (مابيصرفش ولا بيقفل) — None لو مفيش سنة للتاريخ أو النوع مش موجود."""
        return self._x(ex).fetch_value(_PEEK_SQL, {"t": doc_type, "d": doc_date})

    def get_type(self, doc_type: str, ex: Executor | None = None) -> dict | None:
        return self._x(ex).fetch_one(
            "SELECT code, name_ar, prefix, digits, two_step_approval FROM core.document_types WHERE code = %s",
            [doc_type])

    def list_types(self, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT code, name_ar, prefix, digits, two_step_approval FROM core.document_types ORDER BY prefix")

    def list_sequences(self, fiscal_year_id: int, ex: Executor | None = None) -> list[dict]:
        return self._x(ex).fetch_all(
            "SELECT s.doc_type, t.name_ar, t.prefix, s.last_no, s.updated_at "
            "FROM core.document_sequences s JOIN core.document_types t ON t.code = s.doc_type "
            "WHERE s.fiscal_year_id = %s ORDER BY t.prefix", [fiscal_year_id])
