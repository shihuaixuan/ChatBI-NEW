from dataclasses import dataclass

import pytest

from apps.semantic.services.rules.model_relation import (
    RelationDomainMismatchError,
    RelationModelUnavailableError,
    validate_model_relation,
)


@dataclass
class _ModelState:
    oid: int
    domain_id: int
    status: int = 1


def test_validate_model_relation_accepts_active_models_in_same_domain():
    validate_model_relation(
        oid=1,
        domain_id=10,
        left_model=_ModelState(oid=1, domain_id=10),
        right_model=_ModelState(oid=1, domain_id=10),
    )


@pytest.mark.parametrize(
    ("left_model", "right_model"),
    [
        (None, _ModelState(oid=1, domain_id=10)),
        (_ModelState(oid=2, domain_id=10), _ModelState(oid=1, domain_id=10)),
        (_ModelState(oid=1, domain_id=10, status=0), _ModelState(oid=1, domain_id=10)),
    ],
)
def test_validate_model_relation_rejects_unavailable_models(
    left_model: _ModelState | None,
    right_model: _ModelState | None,
):
    with pytest.raises(RelationModelUnavailableError) as exc_info:
        validate_model_relation(
            oid=1,
            domain_id=10,
            left_model=left_model,
            right_model=right_model,
        )

    assert exc_info.value.detail == "SEMANTIC_MODEL_NOT_FOUND"


def test_validate_model_relation_rejects_domain_mismatch():
    with pytest.raises(RelationDomainMismatchError) as exc_info:
        validate_model_relation(
            oid=1,
            domain_id=10,
            left_model=_ModelState(oid=1, domain_id=10),
            right_model=_ModelState(oid=1, domain_id=11),
        )

    assert exc_info.value.detail == "SEMANTIC_MODEL_RELATION_DOMAIN_MISMATCH"
