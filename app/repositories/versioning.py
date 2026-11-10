"""Optimistic Locking في الـ SQL (المرحلة 1ج — سكريبت ``0008_row_version``).

كل UPDATE / DELETE من شاشة بياخد ``expected_version`` (الإصدار اللي الشاشة فتحت بيه السجل):
* ``None`` = من غير تحقق (أوامر داخلية / اختبارات قديمة).
* رقم = الأمر بيتنفذ بس لو ``row_version`` لسه هو هو ← لو جهاز تاني حفظ قبلك مفيش صف بيتأثر
  (``rowcount = 0``) والـ Service بيطلّع ``ConcurrentEditError``.
الإصدار نفسه بيزيد في القاعدة (trigger ``core.bump_row_version``) — البرنامج عمره ما بيكتبه بإيده.
"""

from __future__ import annotations

from typing import Any


def version_clause(expected_version: int | None, column: str = "row_version") -> tuple[str, list[Any]]:
    """``(" AND row_version = %s", [N])`` أو ``("", [])`` لو مفيش تحقق."""
    if expected_version is None:
        return "", []
    return f" AND {column} = %s", [int(expected_version)]
