"""平台参数持久化模型。"""

from sqlmodel import BigInteger, Field, SQLModel

from common.utils.snowflake import snowflake


class SysArgModel(SQLModel, table=True):
    __tablename__ = "sys_arg"

    id: int = Field(
        default_factory=snowflake.generate_id,
        primary_key=True,
        sa_type=BigInteger,
        nullable=False,
    )
    pkey: str = Field(max_length=255, nullable=False)
    pval: str | None = Field(default=None, max_length=255, nullable=True)
    ptype: str = Field(default="str", max_length=255, nullable=False)
    sort_no: int = Field(default=1, nullable=False)


__all__ = ["SysArgModel"]
