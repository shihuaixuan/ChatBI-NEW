from collections import defaultdict, deque

from sqlbot_platform.workflow_engine.domain.definition import (
    NodeType,
    WorkflowDefinition,
)
from sqlbot_platform.workflow_engine.registry.condition_registry import (
    ConditionRegistry,
)
from sqlbot_platform.workflow_engine.registry.handler_registry import HandlerRegistry


class DefinitionValidationError(ValueError):
    """流程发布校验失败，code 是跨接口稳定的错误类别。"""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class DefinitionValidator:
    """在流程发布前执行全图静态校验。

    校验被集中在发布边界，而不是分散在 Runtime 中。通过校验的定义在执行期间
    可以被视为可信结构，Runtime 只需处理运行数据和外部能力失败。
    """

    def __init__(self, handlers: HandlerRegistry, conditions: ConditionRegistry) -> None:
        self._handlers = handlers
        self._conditions = conditions

    def validate(self, definition: WorkflowDefinition) -> None:
        self._validate_start_node(definition)
        self._validate_registered_references(definition)
        outgoing = self._build_and_validate_edges(definition)
        self._validate_outgoing_edges(definition, outgoing)
        self._validate_reachability(definition, outgoing)
        self._validate_cycle_budget(definition, outgoing)

    def _validate_start_node(self, definition: WorkflowDefinition) -> None:
        if definition.start_node not in definition.nodes:
            raise DefinitionValidationError(
                "START_NODE_NOT_FOUND",
                f"起始节点 {definition.start_node!r} 不存在",
            )

    def _validate_registered_references(self, definition: WorkflowDefinition) -> None:
        for node in definition.nodes.values():
            if not self._handlers.contains(node.handler):
                raise DefinitionValidationError(
                    "HANDLER_NOT_FOUND",
                    f"节点 {node.name!r} 引用了未注册处理器 {node.handler!r}",
                )

        for edge in definition.edges:
            if edge.condition is not None and not self._conditions.contains(edge.condition):
                raise DefinitionValidationError(
                    "CONDITION_NOT_FOUND",
                    f"边 {edge.source!r}->{edge.target!r} 引用了未注册条件 {edge.condition!r}",
                )

    def _build_and_validate_edges(self, definition: WorkflowDefinition) -> dict[str, list[str]]:
        outgoing: dict[str, list[str]] = defaultdict(list)
        default_edge_count: dict[str, int] = defaultdict(int)

        for edge in definition.edges:
            if edge.source not in definition.nodes or edge.target not in definition.nodes:
                raise DefinitionValidationError(
                    "EDGE_NODE_NOT_FOUND",
                    f"边 {edge.source!r}->{edge.target!r} 引用了不存在的节点",
                )
            outgoing[edge.source].append(edge.target)
            if edge.condition is None:
                default_edge_count[edge.source] += 1
                if default_edge_count[edge.source] > 1:
                    raise DefinitionValidationError(
                        "DUPLICATE_DEFAULT_EDGE",
                        f"节点 {edge.source!r} 存在多条默认边",
                    )
        return outgoing

    def _validate_outgoing_edges(
        self,
        definition: WorkflowDefinition,
        outgoing: dict[str, list[str]],
    ) -> None:
        for node in definition.nodes.values():
            if node.type is not NodeType.TERMINAL and not outgoing[node.name]:
                raise DefinitionValidationError(
                    "NODE_WITHOUT_OUTGOING_EDGE",
                    f"非终止节点 {node.name!r} 没有出口",
                )

    def _validate_reachability(
        self,
        definition: WorkflowDefinition,
        outgoing: dict[str, list[str]],
    ) -> None:
        visited: set[str] = set()
        queue: deque[str] = deque([definition.start_node])
        while queue:
            node_name = queue.popleft()
            if node_name in visited:
                continue
            visited.add(node_name)
            queue.extend(outgoing[node_name])

        unreachable = sorted(set(definition.nodes) - visited)
        if unreachable:
            raise DefinitionValidationError(
                "UNREACHABLE_NODE",
                f"存在不可达节点: {', '.join(unreachable)}",
            )

    def _validate_cycle_budget(
        self,
        definition: WorkflowDefinition,
        outgoing: dict[str, list[str]],
    ) -> None:
        """有环流程必须保留显式循环预算。

        Pydantic 已保证预算为正数；这里仍执行环检测，使未来若策略改成可选字段时，
        发布边界不会静默接受无限循环。
        """

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node_name: str) -> bool:
            if node_name in visiting:
                return True
            if node_name in visited:
                return False
            visiting.add(node_name)
            has_cycle = any(visit(target) for target in outgoing[node_name])
            visiting.remove(node_name)
            visited.add(node_name)
            return has_cycle

        has_cycle = visit(definition.start_node)
        if has_cycle and definition.policies.max_loop_iterations <= 0:
            raise DefinitionValidationError(
                "LOOP_BUDGET_REQUIRED",
                "有环流程必须配置正数 max_loop_iterations",
            )
