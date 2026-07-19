from apps.datasource.models.dto import PhysicalRelationCell


class DatasourcePhysicalRelationError(ValueError):
    """物理表关系引用了无效或不归属当前数据源的资源。"""


def validate_physical_relation_cells(
    datasource_id: int,
    cells: list[PhysicalRelationCell],
    table_ids: set[int],
    field_table_ids: dict[int, int],
) -> None:
    """校验关系图中的表和字段均属于当前数据源。"""

    for cell in cells:
        if cell.shape == "edge":
            _validate_edge(cell, table_ids, field_table_ids)
            continue

        table_id = _node_table_id(cell)
        if table_id is None:
            raise DatasourcePhysicalRelationError(
                f"物理表节点 ID 不合法: {cell.id}"
            )
        if table_id not in table_ids:
            raise DatasourcePhysicalRelationError(
                f"物理表 {table_id} 不属于数据源 {datasource_id}"
            )


def retain_valid_physical_relation_cells(
    cells: list[PhysicalRelationCell],
    table_ids: set[int],
    field_table_ids: dict[int, int],
) -> list[PhysicalRelationCell]:
    """物理元数据变化后仅保留端点仍然有效的关系图单元。"""

    retained: list[PhysicalRelationCell] = []
    for cell in cells:
        if cell.shape == "edge":
            if _edge_is_valid(cell, table_ids, field_table_ids):
                retained.append(cell)
            continue

        table_id = _node_table_id(cell)
        if table_id is not None and table_id in table_ids:
            retained.append(cell)
    return retained


def _validate_edge(
    cell: PhysicalRelationCell,
    table_ids: set[int],
    field_table_ids: dict[int, int],
) -> None:
    if cell.source is None or cell.target is None:
        raise DatasourcePhysicalRelationError("物理表关系缺少来源或目标端点")
    if cell.source.cell == cell.target.cell:
        raise DatasourcePhysicalRelationError("物理表不能与自身建立关系")
    if cell.source.cell not in table_ids or cell.target.cell not in table_ids:
        raise DatasourcePhysicalRelationError("关系端点引用了当前数据源之外的表")
    if field_table_ids.get(cell.source.port) != cell.source.cell:
        raise DatasourcePhysicalRelationError("来源字段不属于来源表")
    if field_table_ids.get(cell.target.port) != cell.target.cell:
        raise DatasourcePhysicalRelationError("目标字段不属于目标表")


def _edge_is_valid(
    cell: PhysicalRelationCell,
    table_ids: set[int],
    field_table_ids: dict[int, int],
) -> bool:
    if cell.source is None or cell.target is None:
        return False
    if cell.source.cell == cell.target.cell:
        return False
    return (
        cell.source.cell in table_ids
        and cell.target.cell in table_ids
        and field_table_ids.get(cell.source.port) == cell.source.cell
        and field_table_ids.get(cell.target.port) == cell.target.cell
    )


def _node_table_id(cell: PhysicalRelationCell) -> int | None:
    try:
        return int(cell.id)
    except (TypeError, ValueError):
        return None
