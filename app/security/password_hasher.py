"""تشفير كلمات السر بـ PBKDF2-HMAC-SHA256 (من المكتبة الأساسية) — منقول من Hr GAD.

كلمة السر عمرها ما بتتخزن نص صريح. الشكل المتخزن::

    pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>
"""

from __future__ import annotations

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 240_000


class PasswordHasher:
    def __init__(self, iterations: int = DEFAULT_ITERATIONS) -> None:
        self.iterations = iterations

    def hash(self, password: str) -> str:
        salt = os.urandom(16)
        digest = self._derive(password or "", salt, self.iterations)
        return f"{ALGORITHM}${self.iterations}${salt.hex()}${digest.hex()}"

    def verify(self, password: str, stored: str | None) -> bool:
        if not stored:
            return False
        try:
            algorithm, iterations_text, salt_hex, hash_hex = stored.split("$")
            iterations = int(iterations_text)
            salt = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
        except ValueError:
            return False
        if algorithm != ALGORITHM:
            return False
        candidate = self._derive(password or "", salt, iterations)
        return hmac.compare_digest(candidate, expected)

    @staticmethod
    def _derive(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
