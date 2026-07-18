from typing import Any, cast


def all_results(result: Any) -> list[Any]:
    if hasattr(result, "scalars"):
        return cast(list[Any], result.scalars().all())
    if hasattr(result, "all"):
        return cast(list[Any], result.all())
    return list(result or [])


def first_result(result: Any) -> Any | None:
    # SQLAlchemy select(SQLModel) 可能返回 Row，统一通过 scalars() 解包为实体对象。
    if hasattr(result, "scalars"):
        return result.scalars().first()
    if hasattr(result, "first"):
        return result.first()
    return None
