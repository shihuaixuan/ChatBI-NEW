from types import SimpleNamespace

from apps.workflow.capabilities.adapters.permission import PermissionAdapter


class FakePermissionTool:
    def __init__(self, result) -> None:
        self.result = result
        self.payloads: list[dict] = []

    def run(self, payload: dict):
        self.payloads.append(payload)
        return self.result


class FakePolicyProvider:
    def __init__(self, policy: dict) -> None:
        self.policy = policy
        self.payloads: list[dict] = []

    def get_policy(self, payload: dict) -> dict:
        self.payloads.append(payload)
        return self.policy


def test_permission_adapter_allows_and_returns_rewritten_sql():
    tool = FakePermissionTool(
        SimpleNamespace(
            success=True,
            payload={"sql": "select * from orders where tenant_id = 1"},
            error_code=None,
            message=None,
        )
    )
    adapter = PermissionAdapter(permission_tool=tool)

    result = adapter.apply({"sql": "select * from orders", "datasource_id": 7, "tenant_id": 1, "user_id": 9})

    assert tool.payloads == [{"sql": "select * from orders", "datasource_id": 7, "tenant_id": 1, "user_id": 9}]
    assert result == {
        "allowed": True,
        "reason": "permission_applied",
        "sql": "select * from orders where tenant_id = 1",
        "error_code": None,
    }


def test_permission_adapter_applies_row_filters_from_policy_provider():
    tool = FakePermissionTool(
        SimpleNamespace(
            success=True,
            payload={"sql": "SELECT SUM(o.amount) AS amount FROM orders AS o WHERE o.tenant_id = 1"},
            error_code=None,
            message=None,
        )
    )
    provider = FakePolicyProvider(
        {
            "allowed": True,
            "row_filters": [{"table": "orders", "condition": "tenant_id = 1"}],
            "denied_columns": [],
        }
    )
    adapter = PermissionAdapter(permission_tool=tool, policy_provider=provider)

    result = adapter.apply(
        {
            "sql": "select sum(o.amount) as amount from orders o",
            "datasource_id": 7,
            "tenant_id": 1,
            "user_id": 9,
        }
    )

    assert provider.payloads == [{"datasource_id": 7, "tenant_id": 1, "user_id": 9}]
    assert tool.payloads == [
        {
            "sql": "SELECT SUM(o.amount) AS amount FROM orders AS o WHERE o.tenant_id = 1",
            "datasource_id": 7,
            "tenant_id": 1,
            "user_id": 9,
        }
    ]
    assert result == {
        "allowed": True,
        "reason": "permission_applied",
        "sql": "SELECT SUM(o.amount) AS amount FROM orders AS o WHERE o.tenant_id = 1",
        "error_code": None,
    }


def test_permission_adapter_denies_when_policy_blocks_restricted_column():
    tool = FakePermissionTool(
        SimpleNamespace(success=True, payload={"sql": "select o.secret_cost from orders o"}, error_code=None, message=None)
    )
    provider = FakePolicyProvider(
        {
            "allowed": True,
            "row_filters": [],
            "denied_columns": [{"table": "orders", "column": "secret_cost"}],
        }
    )
    adapter = PermissionAdapter(permission_tool=tool, policy_provider=provider)

    result = adapter.apply({"sql": "select o.secret_cost from orders o", "datasource_id": 7})

    assert result == {
        "allowed": False,
        "reason": "字段 secret_cost 无访问权限",
        "sql": None,
        "error_code": "column_permission_denied",
    }


def test_permission_adapter_denies_when_permission_tool_fails():
    tool = FakePermissionTool(
        SimpleNamespace(success=False, payload=None, error_code="permission_denied", message="没有数据源权限")
    )
    adapter = PermissionAdapter(permission_tool=tool)

    result = adapter.apply({"sql": "select * from orders", "datasource_id": 7})

    assert result == {
        "allowed": False,
        "reason": "没有数据源权限",
        "sql": None,
        "error_code": "permission_denied",
    }


def test_permission_adapter_denies_malformed_provider_policy():
    tool = FakePermissionTool(
        SimpleNamespace(success=True, payload={}, error_code=None, message=None)
    )
    provider = FakePolicyProvider(
        {
            "row_filters": {"table": "orders", "condition": "tenant_id = 1"},
            "denied_columns": [],
        }
    )
    adapter = PermissionAdapter(permission_tool=tool, policy_provider=provider)

    result = adapter.apply({"sql": "select * from orders", "datasource_id": 7})

    assert result == {
        "allowed": False,
        "reason": "权限策略格式错误",
        "sql": None,
        "error_code": "permission_policy_invalid",
    }
    assert tool.payloads == []
