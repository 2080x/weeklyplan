from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass


@dataclass(frozen=True)
class PasswordHash:
    algorithm: str
    iterations: int
    salt_b64: str
    hash_b64: str

    def to_string(self) -> str:
        return f"{self.algorithm}${self.iterations}${self.salt_b64}${self.hash_b64}"

    @staticmethod
    def from_string(value: str) -> "PasswordHash":
        algorithm, iterations, salt_b64, hash_b64 = value.split("$", 3)
        return PasswordHash(algorithm=algorithm, iterations=int(iterations), salt_b64=salt_b64, hash_b64=hash_b64)


def hash_password(password: str, *, iterations: int = 210_000) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return PasswordHash(
        algorithm="pbkdf2_sha256",
        iterations=iterations,
        salt_b64=base64.b64encode(salt).decode("ascii"),
        hash_b64=base64.b64encode(dk).decode("ascii"),
    ).to_string()


def verify_password(password: str, stored: str) -> bool:
    ph = PasswordHash.from_string(stored)
    if ph.algorithm != "pbkdf2_sha256":
        return False
    salt = base64.b64decode(ph.salt_b64.encode("ascii"))
    expected = base64.b64decode(ph.hash_b64.encode("ascii"))
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ph.iterations)
    return hmac.compare_digest(actual, expected)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)

