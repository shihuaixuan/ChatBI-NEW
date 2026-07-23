"""兼容现有 rsa 表的项目内 RSA 加解密实现。"""

import base64
import secrets
import string

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from sqlalchemy import BigInteger, Column, Text
from sqlmodel import Field, Session, SQLModel, col, select

from common.core.db import engine
from common.utils.snowflake import snowflake
from common.utils.time import get_timestamp


class RSAKeyModel(SQLModel, table=True):
    __tablename__ = "rsa"

    id: int = Field(
        default_factory=snowflake.generate_id,
        sa_column=Column(BigInteger, primary_key=True, nullable=False),
    )
    private_key: str = Field(sa_column=Column(Text, nullable=False))
    public_key: str = Field(sa_column=Column(Text, nullable=False))
    salt: str = Field(sa_column=Column(Text, nullable=False))
    create_time: int = Field(sa_column=Column(BigInteger, nullable=False))
    update_time: int = Field(sa_column=Column(BigInteger, nullable=False))


def _get_or_create_key_pair(session: Session) -> RSAKeyModel:
    model = session.exec(
        select(RSAKeyModel).order_by(col(RSAKeyModel.id))
    ).first()
    if model is not None:
        return model

    key = RSA.generate(2048)
    now = get_timestamp()
    alphabet = string.ascii_letters + string.digits
    model = RSAKeyModel(
        private_key=key.export_key(format="PEM", passphrase=None, pkcs=1).decode(),
        public_key=key.public_key().export_key(format="PEM").decode(),
        salt="".join(secrets.choice(alphabet) for _ in range(16)),
        create_time=now,
        update_time=now,
    )
    session.add(model)
    session.commit()
    session.refresh(model)
    return model


async def sqlbot_decrypt(text: str) -> str:
    with Session(engine) as session:
        key_model = _get_or_create_key_pair(session)
    try:
        encrypted = base64.b64decode(text, validate=True)
        decrypted = PKCS1_v1_5.new(RSA.import_key(key_model.private_key)).decrypt(
            encrypted,
            b"",
        )
        if not decrypted:
            raise ValueError("RSA 解密失败")
        return decrypted.decode()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("RSA 密文格式不合法或密钥不匹配") from exc


async def sqlbot_encrypt(text: str) -> str:
    with Session(engine) as session:
        key_model = _get_or_create_key_pair(session)
    try:
        encrypted = PKCS1_v1_5.new(RSA.import_key(key_model.public_key)).encrypt(
            text.encode()
        )
    except ValueError as exc:
        raise ValueError("RSA 明文过长，无法加密") from exc
    return base64.b64encode(encrypted).decode()


__all__ = ["RSAKeyModel", "sqlbot_decrypt", "sqlbot_encrypt"]
