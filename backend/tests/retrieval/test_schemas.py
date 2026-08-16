"""统一检索契约和安全不变量测试。"""

import pytest
from pydantic import ValidationError

from apps.retrieval.models.dto import (
    AssetReference,
    ExecutableAssetReference,
    RetrievalBindings,
    RetrievalBundle,
    RetrievalChannel,
    RetrievalChannelDiagnostic,
    RetrievalChannelStatus,
    RetrievalDecision,
    RetrievalDecisionStatus,
    RetrievalDiagnostics,
    RetrievalHit,
    RetrievalProfileName,
    RetrievalPurpose,
    RetrievalResourceType,
    RetrievalScores,
    RetrievalSlotDecision,
    RetrievalSourceType,
)
from apps.retrieval.query.profiles import PROFILE_REGISTRY, get_retrieval_profile


def _metric_hit(asset_id: int = 7) -> RetrievalHit:
    return RetrievalHit(
        resource_id=f"headless:metric:{asset_id}",
        resource_type=RetrievalResourceType.METRIC,
        source_type=RetrievalSourceType.SEMANTIC,
        source_id="headless:dataset:3",
        source_resource_id=str(asset_id),
        unit_id=f"headless:metric:{asset_id}:identity",
        content_kind="identity",
        title="销售额",
        scores=RetrievalScores(exact=1.0, final=1.0),
        ranks_by_channel={RetrievalChannel.EXACT: 1},
        source_version="1",
        asset_ref=AssetReference(
            asset_type=RetrievalResourceType.METRIC,
            asset_id=asset_id,
            model_id=10,
        ),
    )


def _bundle(**updates) -> RetrievalBundle:
    values = {
        "request_id": "request-1",
        "bindings": RetrievalBindings(metrics=[_metric_hit()]),
        "decision": RetrievalDecision(
            status=RetrievalDecisionStatus.RESOLVED,
            slot_decisions=[
                RetrievalSlotDecision(
                    subquery_id="metric:0",
                    purpose=RetrievalPurpose.METRIC,
                    status=RetrievalDecisionStatus.RESOLVED,
                    candidate_assets=[
                        AssetReference(
                            asset_type=RetrievalResourceType.METRIC,
                            asset_id=7,
                            model_id=10,
                        )
                    ],
                    selected_assets=[
                        AssetReference(
                            asset_type=RetrievalResourceType.METRIC,
                            asset_id=7,
                            model_id=10,
                        )
                    ],
                )
            ],
            allowed_asset_ids=[
                ExecutableAssetReference(
                    asset_type=RetrievalResourceType.METRIC,
                    asset_id=7,
                    model_id=10,
                )
            ],
        ),
        "diagnostics": RetrievalDiagnostics(
            strategy_version="semantic-binding-v1",
            index_generation="headless-1",
            channels=[
                RetrievalChannelDiagnostic(
                    channel=RetrievalChannel.EXACT,
                    status=RetrievalChannelStatus.SUCCEEDED,
                    candidate_count=1,
                )
            ],
        ),
    }
    values.update(updates)
    return RetrievalBundle.model_validate(values)


def test_allowed_assets_must_come_from_binding_candidates():
    with pytest.raises(ValidationError, match="allowed_asset_ids 必须是语义绑定候选的子集"):
        _bundle(
            decision=RetrievalDecision(
                status=RetrievalDecisionStatus.RESOLVED,
                slot_decisions=[
                    RetrievalSlotDecision(
                        subquery_id="metric:0",
                        purpose=RetrievalPurpose.METRIC,
                        status=RetrievalDecisionStatus.RESOLVED,
                        candidate_assets=[
                            AssetReference(
                                asset_type=RetrievalResourceType.METRIC,
                                asset_id=99,
                                model_id=10,
                            )
                        ],
                        selected_assets=[
                            AssetReference(
                                asset_type=RetrievalResourceType.METRIC,
                                asset_id=99,
                                model_id=10,
                            )
                        ],
                    )
                ],
                allowed_asset_ids=[
                    ExecutableAssetReference(
                        asset_type=RetrievalResourceType.METRIC,
                        asset_id=99,
                        model_id=10,
                    )
                ],
            )
        )


def test_allowed_assets_must_come_from_final_slot_selection():
    with pytest.raises(ValidationError, match="allowed_asset_ids 必须来自槽位最终选中资产"):
        RetrievalDecision(
            status=RetrievalDecisionStatus.RESOLVED,
            allowed_asset_ids=[
                ExecutableAssetReference(
                    asset_type=RetrievalResourceType.METRIC,
                    asset_id=7,
                    model_id=10,
                )
            ],
        )


def test_knowledge_evidence_cannot_be_mixed_into_exemplars():
    evidence = RetrievalHit(
        resource_id="kb:chunk:1",
        resource_type=RetrievalResourceType.KNOWLEDGE_CHUNK,
        source_type=RetrievalSourceType.KNOWLEDGE_BASE,
        source_id="kb:1",
        source_resource_id="file:1",
        unit_id="chunk:1",
        content_kind="paragraph",
        title="口径说明",
        source_version="1",
    )

    with pytest.raises(ValidationError, match="exemplars 只能包含 SQL_EXEMPLAR 来源"):
        _bundle(exemplars=[evidence])


def test_degraded_result_requires_explicit_reason():
    with pytest.raises(ValidationError, match="degraded 结果必须提供 degraded_reason"):
        _bundle(
            decision=RetrievalDecision(status=RetrievalDecisionStatus.DEGRADED),
            diagnostics=RetrievalDiagnostics(
                strategy_version="semantic-binding-v1",
                index_generation="headless-1",
            ),
        )


def test_failed_channel_requires_stable_error_code():
    with pytest.raises(ValidationError, match="必须提供 error_code"):
        RetrievalChannelDiagnostic(
            channel=RetrievalChannel.DENSE,
            status=RetrievalChannelStatus.UNAVAILABLE,
        )


def test_contracts_reject_unknown_fields():
    payload = _bundle().model_dump(mode="json")
    payload["unexpected"] = True

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RetrievalBundle.model_validate(payload)


def test_profile_registry_only_contains_implemented_profiles():
    """P0-7：未实现的检索 profile 不注册，避免伪装能力。"""

    semantic = get_retrieval_profile(RetrievalProfileName.SEMANTIC_BINDING)
    exemplar = get_retrieval_profile(RetrievalProfileName.SQL_EXEMPLAR)

    assert semantic.allows_executable_assets is True
    assert semantic.requires_slot_decision is True
    assert exemplar.allows_executable_assets is False
    assert set(PROFILE_REGISTRY) == {
        RetrievalProfileName.SEMANTIC_BINDING,
        RetrievalProfileName.SQL_EXEMPLAR,
    }
