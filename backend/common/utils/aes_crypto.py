from common.core.config import settings


def _secure_encryption():
    # XPack 初始化时会加载 Assistant 兼容入口，运行时再加载加密实现可避免包初始化环。
    from sqlbot_xpack.aes_utils import SecureEncryption

    return SecureEncryption


simple_aes_iv_text = "sqlbot_em_aes_iv"


def sqlbot_aes_encrypt(text: str, key: str | None = None) -> str:
    return _secure_encryption().encrypt_to_single_string(
        text,
        key or settings.SECRET_KEY,
    )


def sqlbot_aes_decrypt(text: str, key: str | None = None) -> str:
    return _secure_encryption().decrypt_from_single_string(
        text,
        key or settings.SECRET_KEY,
    )


def simple_aes_encrypt(
    text: str,
    key: str | None = None,
    ivtext: str | None = None,
) -> str:
    return _secure_encryption().simple_aes_encrypt(
        text,
        key or settings.SECRET_KEY[:32],
        ivtext or simple_aes_iv_text,
    )


def simple_aes_decrypt(
    text: str,
    key: str | None = None,
    ivtext: str | None = None,
) -> str:
    return _secure_encryption().simple_aes_decrypt(
        text,
        key or settings.SECRET_KEY[:32],
        ivtext or simple_aes_iv_text,
    )
