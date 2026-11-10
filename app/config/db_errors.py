"""تحويل أخطاء PostgreSQL / psycopg لرسائل عربي واضحة.

* أخطاء الاتصال (زي Hr GAD): السيرفر مش متاح / بيانات الدخول غلط / القاعدة مش موجودة.
* أخطاء التشغيل: تكرار قيمة، سجل مرتبط بيه، قيمة مطلوبة، شرط مش متحقق…
* الأخطاء اللي بترفعها Triggers بتاعتنا (``RAISE EXCEPTION`` = SQLSTATE P0001) رسالتها
  مكتوبة بالعربي أصلاً فبتظهر زي ما هي.

كلمة السر بتتشال من أي رسالة قبل ما تتعرض.
"""

from __future__ import annotations

_UNREACHABLE_MARKERS = (
    "timeout expired",
    "connection timeout",
    "timed out",
    "could not connect",
    "connection refused",
    "could not translate host name",
    "name or service not known",
    "no route to host",
    "network is unreachable",
    "host is unreachable",
    "could not receive data from server",
    "server closed the connection unexpectedly",
    "connection is lost",
    "the connection is closed",
)


def _scrub(text: str, password: str) -> str:
    return text.replace(password, "******") if password else text


def describe_connection_error(
    exc: BaseException,
    *,
    host: str = "",
    port: int | str = "",
    db_name: str = "",
    password: str = "",
    fallback_prefix: str = "تعذّر الاتصال بقاعدة البيانات",
) -> str:
    sqlstate = getattr(exc, "sqlstate", None)
    text = _scrub(str(exc), password)
    low = text.lower()

    if sqlstate in {"28P01", "28000"} or "password authentication failed" in low \
            or "authentication failed" in low or "no password supplied" in low:
        return "بيانات دخول قاعدة البيانات غير صحيحة: اسم المستخدم أو كلمة المرور خاطئة."

    if sqlstate == "3D000" or ("database" in low and "does not exist" in low):
        return (
            f"قاعدة البيانات «{db_name or '؟'}» غير موجودة على الخادم.\n"
            "تأكد من اسم قاعدة البيانات في إعدادات الاتصال."
        )

    if sqlstate is None and any(marker in low for marker in _UNREACHABLE_MARKERS):
        where = f" ({host}:{port})" if host else ""
        return (
            f"تعذّر الوصول إلى خادم قاعدة بيانات PostgreSQL{where}.\n"
            "تحقّق من الآتي:\n"
            f"• عنوان الخادم في إعدادات الاتصال (الحالي: {host or 'غير محدد'}).\n"
            "• أن خدمة PostgreSQL شغالة على الخادم.\n"
            "• أن جدار الحماية (Firewall) بيسمح بالاتصال.\n"
            f"• أن المنفذ {port or 5432} مفتوح والجهاز متصل بالشبكة."
        )

    return f"{fallback_prefix}:\n{text}"


# أخطاء التشغيل حسب SQLSTATE
_OPERATION_MESSAGES = {
    "23505": "القيمة دي متسجلة قبل كده ومينفعش تتكرر.",
    "23503": "مينفعش تكمّل: السجل ده مرتبط ببيانات تانية (أو بيشاور على سجل مش موجود).",
    "23502": "فيه بيان مطلوب لسه فاضي.",
    "23514": "فيه قيمة مخالفة لشروط البيانات.",
    "23P01": "البيانات دي متعارضة مع سجل تاني موجود.",
    "22001": "النص أطول من المسموح.",
    "22003": "الرقم أكبر من المسموح.",
    "22007": "صيغة التاريخ أو الوقت غير صحيحة.",
    "22008": "التاريخ أو الوقت خارج النطاق المسموح.",
    "22P02": "فيه قيمة مكتوبة بصيغة غير صحيحة.",
    "40001": "حصل تعارض مع جهاز تاني بيعدّل نفس البيانات في نفس اللحظة — جرّب تاني.",
    "40P01": "حصل تعارض مع جهاز تاني بيعدّل نفس البيانات في نفس اللحظة — جرّب تاني.",
    "55P03": "السجل ده مستخدم من جهاز تاني دلوقتي — جرّب كمان شوية.",
    "57014": "العملية اتلغت لأنها أخدت وقت أطول من المسموح.",
    "25P03": "الاتصال بالقاعدة اتقفل لأن عملية فضلت مفتوحة وقت طويل من غير شغل — جرّب تاني.",
    "57P01": "الاتصال بالسيرفر اتقطع — جرّب تاني (البرنامج بيعيد الاتصال لوحده).",
    "42501": "مستخدم قاعدة البيانات مالوش صلاحية على العملية دي.",
}


# الاتصال كان شغال ووقع وقت العملية (الشبكة قطعت / السيرفر قفل الجلسة) — البرنامج بيفتح اتصال جديد لوحده.
_DROPPED_MARKERS = ("server closed the connection unexpectedly", "consuming input failed",
                    "connection is lost", "the connection is closed")
_DROPPED_MESSAGE = ("الاتصال بالسيرفر اتقطع في نص العملية — جرّب تاني (البرنامج بيعيد الاتصال لوحده).\n"
                    "لو الرسالة اتكررت: اتأكد إن الجهاز متصل بالشبكة وإن السيرفر شغال.")


def describe_db_error(exc: BaseException, *, password: str = "") -> str:
    """رسالة عربي لأي خطأ قاعدة بيانات أثناء الشغل العادي."""
    sqlstate = getattr(exc, "sqlstate", None)
    diag = getattr(exc, "diag", None)

    # رسائل الـ Triggers بتاعتنا (RAISE EXCEPTION) مكتوبة بالعربي ← تظهر زي ما هي.
    if sqlstate == "P0001":
        primary = getattr(diag, "message_primary", None) or str(exc)
        return _scrub(primary, password)

    if sqlstate in _OPERATION_MESSAGES:
        message = _OPERATION_MESSAGES[sqlstate]
        constraint = getattr(diag, "constraint_name", None)
        if constraint:
            message += f"\n(القيد: {constraint})"
        return message

    if sqlstate is None and any(marker in str(exc).lower() for marker in _DROPPED_MARKERS):
        return _DROPPED_MESSAGE                     # كان متصل والاتصال وقع في نص الشغل (مش «السيرفر مش موجود»)

    if sqlstate is None or str(sqlstate).startswith("08"):
        return describe_connection_error(exc, password=password)

    return "حصل خطأ في قاعدة البيانات:\n" + _scrub(str(exc), password)
