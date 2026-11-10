"""Database helper (psycopg 3) — نفس فكرة Hr GAD مع دعم الـ transactions للمحاسبة.

* اتصال واحد autocommit بيتعاد فتحه لو وقع (مهلة الاتصال 5 ثواني).
* **الشغل على الشبكة (المرحلة 1ج — 10–20 جهاز):** كل اتصال بيتفتح بـ
  - ``lock_timeout`` 5 ثواني: لو جهاز تاني ماسك السجل، العملية بتترفض برسالة عربي بدل ما الشاشة تهنّج.
  - ``statement_timeout`` 30 ثانية: أي أمر بيطوّل بيتلغي (التقارير التقيلة تعلّيه بـ ``SET LOCAL`` جوه الـ Tx).
  - ``idle_in_transaction_session_timeout`` دقيقة: transaction اتسابت مفتوحة مابتفضلش ماسكة أقفال.
  - TCP keepalive: الاتصال اللي وقع (الشبكة قطعت) بيتعرف في أقل من دقيقة.
  - لو الاتصال وقع، **القراية** (``SELECT``/``SHOW`` برّه transaction) بتتعاد لوحدها مرة على اتصال جديد.
    الكتابة عمرها ما بتتعاد (ممكن تكون اتنفذت قبل ما الاتصال يقع) — ولازم أي كتابة أو دالة بتكتب
    (زي ``core.next_document_no``) تبقى جوه ``db.atomic()``.
* ``with db.transaction() as cur:`` لأي عملية محتاجة كذا أمر يا كلهم يتنفذوا يا ولا واحد
  (زي حفظ مستند + قيده). أي خطأ جوه البلوك بيرجّع كل حاجة.
* الصفوف بترجع dict.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg
from psycopg.rows import dict_row

from app import APP_DIRNAME
from app.config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
LOCK_TIMEOUT_MS = 5_000                  # جهاز تاني ماسك السجل ← رسالة بعد 5 ثواني
STATEMENT_TIMEOUT_MS = 30_000            # أقصى وقت لأمر واحد (التقارير تعلّيه بـ SET LOCAL)
IDLE_IN_TX_TIMEOUT_MS = 60_000           # transaction مفتوحة من غير شغل ← السيرفر يقفلها ويفك أقفالها
KEEPALIVE = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}
_READ_PREFIXES = ("select", "show")
APPLICATION_NAME = f"{APP_DIRNAME} Accounting"

Params = Sequence[Any] | dict[str, Any] | None


class Database:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._conn: psycopg.Connection | None = None

    # ---- الاتصال ----------------------------------------------------------

    def connect_kwargs(self) -> dict[str, Any]:
        s = self.settings
        return {
            "host": s.db_host,
            "port": int(s.db_port),
            "dbname": s.db_name,
            "user": s.db_user,
            "password": s.db_password,
            "sslmode": s.db_sslmode,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "application_name": APPLICATION_NAME,
            "options": session_options(),
            **KEEPALIVE,
        }

    def connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed or self._conn.broken:
            self._conn = psycopg.connect(row_factory=dict_row, autocommit=True, **self.connect_kwargs())
            logger.info("اتصال جديد بالقاعدة %s على %s:%s",
                        self.settings.db_name, self.settings.db_host, self.settings.db_port)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def _lost(self, conn: psycopg.Connection) -> bool:
        """الاتصال وقع فعلاً؟ (مش مجرد مهلة قفل / أمر اتلغى — دول الاتصال بيفضل سليم بعدهم)."""
        if conn.closed or conn.broken:
            self.close()
            return True
        return False

    def _run(self, fn, query: Any = None):
        conn = self.connection()
        try:
            return fn(conn)
        except psycopg.OperationalError:
            if self._lost(conn) and is_read_only(query):
                logger.warning("الاتصال بالقاعدة وقع — بنعيد القراية على اتصال جديد")
                return fn(self.connection())
            raise

    # ---- أوامر سريعة (كل أمر لوحده) ----------------------------------------

    def execute(self, query: Any, params: Params = None) -> int:
        return self._run(lambda c: c.execute(query, params).rowcount)

    def fetch_all(self, query: Any, params: Params = None) -> list[dict[str, Any]]:
        return self._run(lambda c: c.execute(query, params).fetchall(), query)

    def fetch_one(self, query: Any, params: Params = None) -> dict[str, Any] | None:
        return self._run(lambda c: c.execute(query, params).fetchone(), query)

    def fetch_value(self, query: Any, params: Params = None) -> Any:
        row = self.fetch_one(query, params)
        return next(iter(row.values())) if row else None

    def execute_script(self, sql: str) -> None:
        """سكريبت فيه كذا أمر (من غير بارامترات)."""
        self._run(lambda c: c.execute(sql))

    # ---- Transactions ---------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[psycopg.Cursor]:
        """بلوك transaction: كله يتنفذ أو كله يرجع."""
        conn = self.connection()
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    yield cur
        except psycopg.OperationalError:
            self._lost(conn)          # لو الاتصال وقع يتقفل (والمرة الجاية يتفتح جديد) — مفيش إعادة للكتابة
            raise

    @contextmanager
    def atomic(self) -> Iterator["Tx"]:
        """زي ``transaction()`` بس بيدّي ``Tx`` بنفس أوامر ``Database``
        (execute / fetch_all / fetch_one / fetch_value) — عشان الـ Repository يشتغل
        بنفس الكود سواء جوه transaction أو برّاها."""
        with self.transaction() as cur:
            yield Tx(cur)

    # ---- معلومات ----------------------------------------------------------

    def server_version(self) -> str:
        return str(self.fetch_value("SHOW server_version") or "")


class Tx:
    """أوامر جوه transaction مفتوحة (نفس واجهة ``Database``)."""

    def __init__(self, cursor: psycopg.Cursor) -> None:
        self.cursor = cursor

    def execute(self, query: Any, params: Params = None) -> int:
        return self.cursor.execute(query, params).rowcount

    def fetch_all(self, query: Any, params: Params = None) -> list[dict[str, Any]]:
        return self.cursor.execute(query, params).fetchall()

    def fetch_one(self, query: Any, params: Params = None) -> dict[str, Any] | None:
        return self.cursor.execute(query, params).fetchone()

    def fetch_value(self, query: Any, params: Params = None) -> Any:
        row = self.fetch_one(query, params)
        return next(iter(row.values())) if row else None


def session_options() -> str:
    """إعدادات كل اتصال (المهلات) — بتتبعت مع الاتصال نفسه (``-c name=value``)."""
    return (f"-c lock_timeout={LOCK_TIMEOUT_MS} -c statement_timeout={STATEMENT_TIMEOUT_MS} "
            f"-c idle_in_transaction_session_timeout={IDLE_IN_TX_TIMEOUT_MS}")


def is_read_only(query: Any) -> bool:
    """أمر قراية بس (يتعاد بأمان لو الاتصال وقع)؟ — SELECT / SHOW نص عادي."""
    return isinstance(query, str) and query.lstrip().lower().startswith(_READ_PREFIXES)


# أي حاجة بتنفّذ SQL: القاعدة نفسها (كل أمر لوحده) أو Tx (جوه transaction).
Executor = Database | Tx

_default: Database | None = None


def get_db() -> Database:
    """القاعدة المشتركة للبرنامج كله."""
    global _default
    if _default is None:
        _default = Database()
    return _default


def reset_db() -> None:
    """بعد تغيير ملف الاتصال: نقفل ونبدأ من جديد بالإعدادات الجديدة."""
    global _default
    if _default is not None:
        _default.close()
    _default = None
