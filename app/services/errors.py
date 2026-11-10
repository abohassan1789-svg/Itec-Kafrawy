"""أخطاء قواعد العمل — رسالتها بالعربي وبتتعرض للمستخدم زي ما هي."""


class BusinessError(Exception):
    """خطأ متوقع (بيانات ناقصة، ممنوع، مكرر…) — مش bug."""


class PermissionDenied(BusinessError):
    """المستخدم مالوش صلاحية على العملية."""

    def __init__(self, message: str = "ليس لديك صلاحية لتنفيذ هذه العملية.") -> None:
        super().__init__(message)


class ConcurrentEditError(BusinessError):
    """السجل اتعدّل (أو اتمسح) من جهاز تاني بعد ما الشاشة فتحته (Optimistic Locking — المرحلة 1ج).

    الرسالة فيها مين وإمتى (من ``sec.audit_log``) — الشاشة بتفضل في وضع التعديل باللي المستخدم كتبه،
    وبتسأله: «تحديث» (يشوف التعديل الجديد) ولا يرجع للتعديل.
    """

    def __init__(self, message: str, *, deleted: bool = False, changed_by: str | None = None,
                 changed_at=None, device: str | None = None) -> None:  # noqa: ANN001 - datetime
        super().__init__(message)
        self.deleted = deleted
        self.changed_by = changed_by
        self.changed_at = changed_at
        self.device = device
