import base64
import hashlib
import os

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

from common.core.config import settings

simple_aes_iv_text = "sqlbot_em_aes_iv"


def sqlbot_aes_encrypt(text: str, key: str | None = None) -> str:
    salt = os.urandom(16)
    iv = os.urandom(16)
    iterations = 100_000
    aes_key = hashlib.pbkdf2_hmac(
        "sha1",
        (key or settings.SECRET_KEY).encode(),
        salt,
        iterations,
        dklen=32,
    )
    encrypted = AES.new(aes_key, AES.MODE_CBC, iv).encrypt(
        pad(text.encode(), AES.block_size)
    )
    return ":".join(
        (
            base64.b64encode(salt).decode(),
            base64.b64encode(iv).decode(),
            base64.b64encode(encrypted).decode(),
            str(iterations),
        )
    )


def sqlbot_aes_decrypt(text: str, key: str | None = None) -> str:
    try:
        salt_text, iv_text, encrypted_text, iterations_text = text.split(":")
        salt = base64.b64decode(salt_text, validate=True)
        iv = base64.b64decode(iv_text, validate=True)
        encrypted = base64.b64decode(encrypted_text, validate=True)
        iterations = int(iterations_text)
        aes_key = hashlib.pbkdf2_hmac(
            "sha1",
            (key or settings.SECRET_KEY).encode(),
            salt,
            iterations,
            dklen=32,
        )
        return unpad(
            AES.new(aes_key, AES.MODE_CBC, iv).decrypt(encrypted),
            AES.block_size,
        ).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("AES 密文格式不合法或密钥不匹配") from exc


def simple_aes_encrypt(
    text: str,
    key: str | None = None,
    ivtext: str | None = None,
) -> str:
    aes_key, iv = _simple_key_and_iv(
        key or settings.SECRET_KEY[:32],
        ivtext or simple_aes_iv_text,
    )
    encrypted = AES.new(aes_key, AES.MODE_CBC, iv).encrypt(
        pad(text.encode(), AES.block_size)
    )
    return base64.b64encode(encrypted).decode()


def simple_aes_decrypt(
    text: str,
    key: str | None = None,
    ivtext: str | None = None,
) -> str:
    aes_key, iv = _simple_key_and_iv(
        key or settings.SECRET_KEY[:32],
        ivtext or simple_aes_iv_text,
    )
    try:
        encrypted = base64.b64decode(text, validate=True)
        return unpad(
            AES.new(aes_key, AES.MODE_CBC, iv).decrypt(encrypted),
            AES.block_size,
        ).decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("AES 密文格式不合法或密钥不匹配") from exc


def _simple_key_and_iv(key: str, ivtext: str) -> tuple[bytes, bytes]:
    key_bytes = key.encode()
    iv_bytes = ivtext.encode()
    if len(key_bytes) not in {16, 24, 32}:
        raise ValueError("AES 密钥长度必须为 16、24 或 32 字节")
    if len(iv_bytes) != AES.block_size:
        raise ValueError("AES IV 长度必须为 16 字节")
    return key_bytes, iv_bytes
