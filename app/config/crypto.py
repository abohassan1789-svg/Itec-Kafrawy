"""تشفير كلمة سر قاعدة البيانات (نفس فكرة Hr GAD).

كلمة السر عمرها ما بتتكتب على الديسك نص صريح. الناتج نص بيوصف نفسه:

* ``dpapi:<base64>`` — Windows DPAPI على مستوى الجهاز (أي مستخدم على نفس الجهاز يقدر يفكّه).
* ``plain:<base64>`` — احتياطي أخير لو DPAPI مش متاح (مش تشفير، تمويه بس، وبيتسجل تحذير).

:func:`decrypt` بيفرّق بالبادئة، ولو النص من غير بادئة بيعتبره نص قديم صريح.
"""

from __future__ import annotations

import base64
import logging
import os

logger = logging.getLogger(__name__)

DPAPI_PREFIX = "dpapi:"
PLAIN_PREFIX = "plain:"
_CRYPTPROTECT_LOCAL_MACHINE = 0x4


def dpapi_available() -> bool:
    return os.name == "nt"


def _dpapi_crypt(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    func = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    ok = func(
        ctypes.byref(blob_in), None, None, None, None,
        _CRYPTPROTECT_LOCAL_MACHINE, ctypes.byref(blob_out),
    )
    if not ok:
        raise OSError("فشل تشفير/فك تشفير DPAPI")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def encrypt(secret: str | None) -> str:
    raw = (secret or "").encode("utf-8")
    if dpapi_available():
        try:
            return DPAPI_PREFIX + base64.b64encode(_dpapi_crypt(raw, encrypt=True)).decode("ascii")
        except OSError as exc:
            logger.warning("DPAPI غير متاح (%s) — كلمة السر هتتخزن مموّهة بس.", exc)
    logger.warning("كلمة السر متخزنة base64 بس (مفيش DPAPI على الجهاز ده).")
    return PLAIN_PREFIX + base64.b64encode(raw).decode("ascii")


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    if token.startswith(DPAPI_PREFIX):
        return _dpapi_crypt(base64.b64decode(token[len(DPAPI_PREFIX):]), encrypt=False).decode("utf-8")
    if token.startswith(PLAIN_PREFIX):
        return base64.b64decode(token[len(PLAIN_PREFIX):]).decode("utf-8")
    return token


def is_encrypted(token: str | None) -> bool:
    return bool(token) and token.startswith((DPAPI_PREFIX, PLAIN_PREFIX))
