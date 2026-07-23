"""ChatBI 对外入口层（R3-d 建立，R4-a 统一路由）。

承载旧 Chat HTTP/SSE 协议的落位模块（legacy_*，随旧前端迁移后删除，见 COMPAT_LEDGER C2）
与 ChatBI 路由。会话和查询接口分别位于 conversations / queries；Agent、Graph 路由由
最外层注入 router 聚合入口。协议转换与展示格式化属本层；业务规则一律调用 chatbi.services。
"""
