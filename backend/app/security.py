import base64
import hashlib
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

# bcrypt принимает не больше 72 байт. До 5.0 лишнее молча отбрасывалось, с 5.0
# библиотека бросает ошибку: длинная парольная фраза роняла смену пароля в 500,
# а длинный стартовый пароль - весь старт панели. Длинное сворачиваем в sha256
# (base64, чтобы не было нулевых байтов). Короткие пароли хешируются как
# раньше, поэтому уже сохранённые хеши продолжают работать.
_BCRYPT_MAX = 72


def _prepare(password: str) -> bytes:
    raw = password.encode()
    if len(raw) <= _BCRYPT_MAX:
        return raw
    return base64.b64encode(hashlib.sha256(raw).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), password_hash.encode())
    except ValueError:
        return False


def create_access_token(
    username: str, secret: str, ttl_minutes: int, token_version: int = 0
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "ver": token_version,
        "iat": now,
        "exp": now + timedelta(minutes=ttl_minutes),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict | None:
    """Возвращает payload (sub/ver/…) при валидной подписи и сроке, иначе None."""
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if not isinstance(payload.get("sub"), str):
        return None
    return payload
