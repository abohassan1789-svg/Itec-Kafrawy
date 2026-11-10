from types import SimpleNamespace

from app.config.db_errors import describe_connection_error, describe_db_error


class FakeError(Exception):
    def __init__(self, text, sqlstate=None, **diag):
        super().__init__(text)
        self.sqlstate = sqlstate
        self.diag = SimpleNamespace(message_primary=diag.get("primary"), constraint_name=diag.get("constraint"))


def test_wrong_password_and_password_scrubbed():
    msg = describe_connection_error(FakeError("password authentication failed for user", "28P01"), password="pw1")
    assert "كلمة المرور" in msg


def test_missing_database():
    msg = describe_connection_error(FakeError('database "X" does not exist', "3D000"), db_name="X")
    assert "«X» غير موجودة" in msg


def test_unreachable():
    msg = describe_connection_error(FakeError("connection timeout expired"), host="10.0.0.9", port=5432)
    assert "10.0.0.9" in msg and "Firewall" in msg


def test_other_error_shows_text_without_password():
    msg = describe_connection_error(FakeError("boom secretpw boom", "XX000"), password="secretpw")
    assert "secretpw" not in msg and "******" in msg


def test_unique_violation_arabic_with_constraint():
    msg = describe_db_error(FakeError("duplicate key", "23505", constraint="uq_accounts_code"))
    assert "متسجلة قبل كده" in msg and "uq_accounts_code" in msg


def test_trigger_message_passes_through():
    msg = describe_db_error(FakeError("ERROR: القيد غير متوازن", "P0001", primary="القيد غير متوازن"))
    assert msg == "القيد غير متوازن"


def test_network_messages():
    assert describe_db_error(FakeError("canceling statement due to lock timeout", "55P03")) == \
        "السجل ده مستخدم من جهاز تاني دلوقتي — جرّب كمان شوية."
    assert "جهاز تاني" in describe_db_error(FakeError("deadlock detected", "40P01"))
    assert "اتقطع" in describe_db_error(FakeError("terminating connection due to administrator command", "57P01"))
    dropped = describe_db_error(FakeError("consuming input failed: server closed the connection unexpectedly"))
    assert dropped.startswith("الاتصال بالسيرفر اتقطع في نص العملية") and "Firewall" not in dropped
    assert "Firewall" in describe_db_error(FakeError("connection refused"))     # مش متصل أصلاً ← نفس رسالة الاتصال
