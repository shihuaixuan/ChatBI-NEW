# 领域实现已下沉到共享能力层 apps.chatbi_capabilities.semantic.compile；
# 本类保留 v1 工具协议（name/run/can_compile），内部委托能力层。
from apps.agentic_chat.schemas import ToolResult
from apps.chatbi_capabilities.semantic.compile import (
    compile_semantic_sql,
    resolve_dataset_by_datasource,
)
from apps.headless.sql_compiler import SemanticSQLCompiler


class SemanticSQLCompilerTool:
    name = "sql.generate_semantic_compiler"

    def __init__(self, session, compiler: SemanticSQLCompiler | None = None):
        self.session = session
        self.compiler = compiler or SemanticSQLCompiler()

    def can_compile(self, oid: int, datasource_id: int | None) -> bool:
        if not datasource_id:
            return False
        return resolve_dataset_by_datasource(self.session, oid=oid, datasource_id=datasource_id) is not None

    def run(self, payload: dict) -> ToolResult:
        datasource_id = payload.get("datasource_id")
        oid = payload.get("oid") or 1
        if not datasource_id:
            return ToolResult(success=False, error_code="datasource_required", message="缺少数据源")
        return compile_semantic_sql(
            self.session,
            oid=oid,
            question=payload.get("question") or payload.get("origin_question") or "",
            slots=payload.get("slots") or {},
            limit=payload.get("limit"),
            datasource_id=datasource_id,
            compiler=self.compiler,
        )
