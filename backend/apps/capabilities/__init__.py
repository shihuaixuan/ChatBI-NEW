"""ChatBI 共享领域能力层。

Graph 链路与 Agent 链路共用的领域能力：
SQL 校验/权限/执行/修复、语义资产检索、语义编译请求组装。

依赖规则（强约束）：
- 本包禁止 import apps.workflow_engine / apps.chatbi.orchestration / apps.agent / apps.agentic_chat；
- 入参出参均为普通领域对象（dict / pydantic / dataclass），不感知任何一侧的运行时上下文。
"""
