"""ChatBI 对外入口层（R3-d 起建立）。

承载旧 Chat HTTP/SSE 协议的落位模块（legacy_*，随旧前端迁移后删除，见 COMPAT_LEDGER C2）
与 ChatBI 路由。协议转换与展示格式化属本层；业务规则一律调用 chatbi.services。
"""
