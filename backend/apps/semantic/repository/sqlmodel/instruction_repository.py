"""数据集 instructions 的 SQLModel 仓储。"""

from sqlmodel import Session, col, select

from apps.semantic.models.orm import SemanticDatasetInstruction
from apps.semantic.repository.sqlmodel.results import all_results


class SqlModelInstructionRepository:
    """提供租户隔离、版本唯一和启用状态过滤。"""

    def __init__(self, session: Session):
        self._session = session

    def list_for_dataset(
        self,
        oid: int,
        dataset_id: int,
        *,
        module: str | None = None,
        enabled_only: bool = False,
    ) -> list[SemanticDatasetInstruction]:
        statement = select(SemanticDatasetInstruction).where(
            SemanticDatasetInstruction.oid == oid,
            SemanticDatasetInstruction.dataset_id == dataset_id,
        )
        if module is not None:
            statement = statement.where(SemanticDatasetInstruction.module == module)
        if enabled_only:
            statement = statement.where(SemanticDatasetInstruction.enabled.is_(True))
        return all_results(
            self._session.exec(
                statement.order_by(
                    col(SemanticDatasetInstruction.module),
                    col(SemanticDatasetInstruction.version),
                )
            )
        )

    def get(self, oid: int, instruction_id: int) -> SemanticDatasetInstruction | None:
        instruction = self._session.get(SemanticDatasetInstruction, instruction_id)
        if instruction is None or instruction.oid != oid:
            return None
        return instruction

    def create(self, instruction: SemanticDatasetInstruction) -> SemanticDatasetInstruction:
        self._session.add(instruction)
        self._session.flush()
        self._session.refresh(instruction)
        self._session.commit()
        return instruction

    def update(self, instruction: SemanticDatasetInstruction) -> SemanticDatasetInstruction:
        self._session.add(instruction)
        self._session.commit()
        self._session.refresh(instruction)
        return instruction

    def delete(self, instruction: SemanticDatasetInstruction) -> None:
        self._session.delete(instruction)
        self._session.commit()


__all__ = ["SqlModelInstructionRepository"]
