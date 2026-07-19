from pydantic import BaseModel, Field


class McpDs(BaseModel):
    """MCP 数据源列表请求。"""

    token: str = Field(description="用户token")
    oid: str | None = Field(
        default=None,
        description="组织ID，如果不传则为最后一次登录 Numora 时所使用的组织ID",
    )


class ChatStart(BaseModel):
    """MCP 登录并创建会话请求。"""

    username: str = Field(description="用户名")
    password: str = Field(description="密码")


class McpQuestion(BaseModel):
    """MCP 问数请求。"""

    question: str = Field(description="用户提问")
    chat_id: int = Field(description="会话ID")
    token: str = Field(description="token")
    stream: bool | None = Field(
        default=True,
        description="是否流式输出，默认为true开启, 关闭false则返回JSON对象",
    )
    lang: str | None = Field(
        default="zh-CN",
        description="语言：zh-CN|zh-TW|en|ko-KR",
    )
    datasource_id: int | str | None = Field(
        default=None,
        description="数据源ID，仅当当前对话没有确定数据源时有效",
    )
    oid: str | None = Field(
        default=None,
        description="组织ID，仅当数据源ID为空时有效，如果不传则为最后一次登录 Numora 时所使用的组织ID",
    )
    return_img: bool | None = Field(
        default=True,
        description="是否返回图表，默认为true开启, 关闭false则仅返回数据",
    )


__all__ = ["ChatStart", "McpDs", "McpQuestion"]
