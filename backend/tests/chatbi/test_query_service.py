from apps.capabilities.schemas import ToolResult
from apps.chatbi.services import QueryService, SQLPermissionService
from apps.workflow.capabilities.adapters.permission import PermissionAdapter


class RecordingExecutor:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    def run(self, payload: dict) -> ToolResult:
        self.payloads.append(payload)
        return ToolResult(
            success=True,
            payload={
                "fields": ["amount"],
                "data": [{"amount": 10}, {"amount": 20}],
            },
        )


class StaticPolicyProvider:
    def __init__(self, policy: dict) -> None:
        self.policy = policy
        self.payloads: list[dict] = []

    def get_policy(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return self.policy


class UnsafePermissionService:
    def apply(self, payload: dict) -> dict:
        _ = payload
        return {
            "allowed": True,
            "reason": "permission_applied",
            "sql": "delete from orders",
            "error_code": None,
        }


def test_query_service_applies_permission_before_validation_and_execution():
    executor = RecordingExecutor()
    provider = StaticPolicyProvider(
        {
            "allowed": True,
            "row_filters": [
                {"table": "orders", "condition": "workspace_id = 3"}
            ],
            "denied_columns": [],
        }
    )
    service = QueryService(
        sample_rows=1,
        permission_service=SQLPermissionService(policy_provider=provider),
        execute_tool=executor,
    )

    result = service.execute_sql(
        sql="select amount from orders",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
        allowed_tables=["orders"],
    )

    assert result.success
    assert provider.payloads == [
        {"datasource_id": 8, "tenant_id": 3, "user_id": 9}
    ]
    assert executor.payloads == [
        {
            "sql": (
                "SELECT amount FROM orders WHERE orders.workspace_id = 3 "
                "limit 100"
            ),
            "datasource_id": 8,
        }
    ]
    assert result.payload["sample_rows"] == [{"amount": 10}]
    assert result.payload["full_data"] == [{"amount": 10}, {"amount": 20}]
    assert result.payload["stats_summary"]["amount"]["sum"] == 30


def test_query_service_does_not_execute_when_policy_denies():
    executor = RecordingExecutor()
    provider = StaticPolicyProvider(
        {
            "allowed": False,
            "reason": "没有数据源权限",
            "error_code": "data_policy_denied",
        }
    )
    service = QueryService(
        permission_service=SQLPermissionService(policy_provider=provider),
        execute_tool=executor,
    )

    result = service.execute_sql(
        sql="select amount from orders",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
    )

    assert not result.success
    assert result.error_code == "data_policy_denied"
    assert executor.payloads == []


def test_query_service_revalidates_permission_rewritten_sql():
    executor = RecordingExecutor()
    service = QueryService(
        permission_service=UnsafePermissionService(),
        execute_tool=executor,
    )

    result = service.execute_sql(
        sql="select amount from orders",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
    )

    assert not result.success
    assert result.error_code == "unsafe_statement"
    assert executor.payloads == []


def test_old_graph_permission_import_is_same_service_object():
    assert PermissionAdapter is SQLPermissionService


def test_query_service_preserves_execution_metadata():
    executor = RecordingExecutor()
    executor.run = lambda payload: ToolResult(
        success=True,
        payload={
            "fields": ["amount"],
            "data": [{"amount": 10}],
            "sql": "encoded-sql",
            "driver": "mysql",
        },
    )
    service = QueryService(
        permission_service=SQLPermissionService(),
        execute_tool=executor,
    )

    result = service.execute_sql(
        sql="select amount from orders",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
    )

    assert result.success
    assert result.payload["execution_metadata"] == {
        "sql": "encoded-sql",
        "driver": "mysql",
    }


def test_query_service_can_validate_without_adding_limit():
    executor = RecordingExecutor()
    service = QueryService(
        default_limit=None,
        permission_service=SQLPermissionService(),
        execute_tool=executor,
    )

    result = service.execute_sql(
        sql="select amount from orders",
        datasource_id=8,
        workspace_id=3,
        user_id=9,
    )

    assert result.success
    assert executor.payloads == [
        {"sql": "select amount from orders", "datasource_id": 8}
    ]
