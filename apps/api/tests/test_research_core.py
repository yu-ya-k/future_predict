from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from api.config import Settings
from api.research.artifacts import ArtifactStore
from api.research.azure_responses import AzureResponsesClient
from api.research.graph import build_phase_3_graph, build_phase_4_graph
from api.research.nodes import build_objective_contract
from api.research.repository import ResearchRepository
from api.research.routing import route_after_review
from api.research.schemas import (
    REVIEW_RESULT_SCHEMA,
    CreateResearchRunRequest,
    HumanReviewAction,
    ResearchRunOptions,
    RunStatus,
    Verdict,
)
from api.research.service import ResearchOrchestrator


class _FailingDeleteArtifactStore(ArtifactStore):
    def delete_run(self, run_id: UUID) -> None:
        raise OSError("artifact cleanup failed")


def test_objective_contract_builder_creates_frozen_items() -> None:
    contract, items, prompt = build_objective_contract(
        user_prompt="Research battery recycling in Japan.",
    )

    assert contract.contract_frozen is True
    assert contract.security_policy["public_web_search_allowed"] is True
    assert len(items) == len(contract.acceptance_criteria)
    assert [item.item_id for item in items] == [
        "RI-001",
        "RI-002",
        "RI-003",
        "RI-004",
        "RI-005",
    ]
    assert "Research Items" in prompt


def test_review_result_schema_required_fields_match_properties() -> None:
    required = set(REVIEW_RESULT_SCHEMA["required"])
    properties = set(REVIEW_RESULT_SCHEMA["properties"])

    assert required == properties


@pytest.mark.parametrize("writer", ["save_json", "save_text"])
@pytest.mark.parametrize(
    "relative_path",
    [
        "../escape.txt",
        "reports/../../escape.txt",
        "/tmp/escape.txt",
        "C:\\tmp\\escape.txt",
        "reports\\..\\escape.txt",
    ],
)
def test_artifact_store_rejects_unsafe_relative_paths(
    tmp_path: Path,
    writer: str,
    relative_path: str,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()

    with pytest.raises(ValueError):
        if writer == "save_json":
            store.save_json(run_id, relative_path, {"ok": True})
        else:
            store.save_text(run_id, relative_path, "ok")

    assert not (tmp_path / "escape.txt").exists()


def test_artifact_store_rejects_symlink_escape(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    run_root = tmp_path / "artifacts" / str(run_id)
    outside = tmp_path / "outside"
    run_root.mkdir(parents=True)
    outside.mkdir()
    try:
        (run_root / "link").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlink creation is unavailable: {error}")

    with pytest.raises(ValueError):
        store.save_text(run_id, "link/escape.txt", "escaped")

    assert not (outside / "escape.txt").exists()


def test_artifact_store_save_text_uses_same_directory_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    run_id = uuid4()
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        replacements.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("api.research.artifacts.os.replace", record_replace)

    path, digest = store.save_text(run_id, "reports/final.md", "hello")

    artifact_path = Path(path)
    assert artifact_path.read_text() == "hello"
    assert digest == hashlib.sha256(b"hello").hexdigest()
    assert len(replacements) == 1
    temp_path, replaced_path = replacements[0]
    assert replaced_path == artifact_path
    assert temp_path.parent == artifact_path.parent
    assert not temp_path.exists()


def test_delete_run_keeps_database_record_when_artifact_cleanup_fails(
    tmp_path: Path,
) -> None:
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
    )
    repository = ResearchRepository(settings.research_db_path)
    artifacts = _FailingDeleteArtifactStore(settings.research_artifact_dir)
    orchestrator = ResearchOrchestrator(
        settings=settings,
        repository=repository,
        artifacts=artifacts,
        azure=cast(AzureResponsesClient, object()),
    )
    run = repository.create_run(
        user_prompt="Research public information.",
        options=ResearchRunOptions(),
        settings=settings,
    )
    repository.update_run(run.id, status=RunStatus.COMPLETED)

    with pytest.raises(RuntimeError, match="Artifact cleanup failed"):
        orchestrator.delete_run(run.id)

    assert repository.get_run(run.id).id == run.id


def test_forecast_mode_create_run_requires_policy_decision_id(tmp_path: Path) -> None:
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
    )
    orchestrator = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, object()),
    )

    with pytest.raises(ValueError, match="policy_decision_id is required"):
        orchestrator.create_run(
            CreateResearchRunRequest(user_prompt="Forecast public information."),
            forecast_mode=True,
        )


def test_forecast_origin_submit_requires_policy_decision_id(tmp_path: Path) -> None:
    settings = Settings(
        research_db_path=tmp_path / "research.sqlite3",
        research_artifact_dir=tmp_path / "artifacts",
        research_poller_enabled=False,
    )
    repository = ResearchRepository(settings.research_db_path)
    orchestrator = ResearchOrchestrator(
        settings=settings,
        repository=repository,
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(AzureResponsesClient, object()),
    )
    run = repository.create_run(
        user_prompt="Forecast public information.",
        options=ResearchRunOptions(),
        settings=settings,
        run_origin="forecast",
    )

    with pytest.raises(ValueError, match="policy_decision_id is required"):
        orchestrator.submit_deep_research(run.id)

    assert repository.get_run(run.id).status == RunStatus.QUEUED


def test_route_after_review_prefers_targeted_rerun_for_missing_sources() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.NEEDS_TARGETED_RERUN.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_different_sources",
                        "failure_mode_confidence": 90,
                    }
                ],
            },
            "targeted_rerun_runs": 0,
            "max_targeted_rerun_runs": 2,
            "total_reviews": 1,
            "max_total_iterations": 8,
        }
    )

    assert route == "build_targeted_rerun_plan"


def test_route_after_review_security_concern_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.PASS.value,
                "reviewer_confidence": 90,
                "security_concerns": ["query may contain confidential context"],
            }
        }
    )

    assert route == "human_review"


def test_route_after_review_pass_with_low_confidence_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.PASS.value,
                "reviewer_confidence": 60,
                "high_risk_flags": [],
                "security_concerns": [],
            }
        }
    )

    assert route == "human_review"


def test_route_after_review_pass_with_high_risk_flag_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.PASS.value,
                "reviewer_confidence": 90,
                "high_risk_flags": ["regulated advice"],
                "security_concerns": [],
            }
        }
    )

    assert route == "human_review"


def test_route_after_review_pass_with_hard_stop_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.PASS.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
            },
            "total_reviews": 5,
            "max_total_iterations": 5,
        }
    )

    assert route == "human_review"


def test_route_after_review_pass_with_item_action_requires_human_review() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.PASS.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_deeper_search",
                        "failure_mode_confidence": 90,
                        "recommended_action": "targeted_rerun",
                    }
                ],
            }
        }
    )

    assert route == "human_review"


def test_route_after_review_prefers_explicit_human_action_over_item_revision() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.NEEDS_ITEM_REVISION.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "requires_human_judgment",
                        "failure_mode_confidence": 90,
                        "recommended_action": "human_review",
                    },
                    {
                        "item_id": "RI-002",
                        "status": "partial",
                        "severity": "minor",
                        "failure_mode": "criterion_too_ambiguous",
                        "failure_mode_confidence": 90,
                        "recommended_action": "revise_items",
                    },
                ],
            }
        }
    )

    assert route == "human_review"


def test_route_after_review_prefers_rerun_before_patch_for_mixed_actions() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.NEEDS_TARGETED_RERUN.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "minor",
                        "failure_mode": "format_only",
                        "failure_mode_confidence": 90,
                        "recommended_action": "llm_patch",
                    },
                    {
                        "item_id": "RI-002",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_deeper_search",
                        "failure_mode_confidence": 90,
                        "recommended_action": "targeted_rerun",
                    },
                ],
            },
            "llm_patch_runs": 0,
            "max_llm_patch_runs": 3,
            "targeted_rerun_runs": 0,
            "max_targeted_rerun_runs": 2,
            "total_reviews": 1,
            "max_total_iterations": 8,
        }
    )

    assert route == "build_targeted_rerun_plan"


def test_route_after_review_prefers_full_rerun_over_targeted_and_verify() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.NEEDS_FULL_RERUN.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_targeted_verification",
                        "failure_mode_confidence": 90,
                        "recommended_action": "verify",
                    },
                    {
                        "item_id": "RI-002",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_deeper_search",
                        "failure_mode_confidence": 90,
                        "recommended_action": "targeted_rerun",
                    },
                    {
                        "item_id": "RI-003",
                        "status": "partial",
                        "severity": "blocker",
                        "failure_mode": "needs_query_reformulation",
                        "failure_mode_confidence": 90,
                        "recommended_action": "full_rerun",
                    },
                ],
            },
            "full_rerun_runs": 0,
            "max_full_rerun_runs": 1,
            "targeted_rerun_runs": 0,
            "max_targeted_rerun_runs": 2,
            "verification_runs": 0,
            "max_verification_runs": 2,
            "total_reviews": 1,
            "max_total_iterations": 8,
        }
    )

    assert route == "full_rerun_submit"


def test_route_after_review_prefers_targeted_rerun_over_verify() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.NEEDS_TARGETED_RERUN.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_targeted_verification",
                        "failure_mode_confidence": 90,
                        "recommended_action": "verify",
                    },
                    {
                        "item_id": "RI-002",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_deeper_search",
                        "failure_mode_confidence": 90,
                        "recommended_action": "targeted_rerun",
                    },
                ],
            },
            "targeted_rerun_runs": 1,
            "max_targeted_rerun_runs": 2,
            "verification_runs": 0,
            "max_verification_runs": 2,
            "no_progress_count": 2,
            "total_reviews": 2,
            "max_total_iterations": 8,
        }
    )

    assert route == "build_targeted_rerun_plan"


def test_route_after_review_stops_verification_when_no_progress_repeats() -> None:
    route = route_after_review(
        {
            "review": {
                "verdict": Verdict.NEEDS_VERIFICATION.value,
                "reviewer_confidence": 90,
                "high_risk_flags": [],
                "security_concerns": [],
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_targeted_verification",
                        "failure_mode_confidence": 90,
                        "recommended_action": "verify",
                    }
                ],
            },
            "verification_runs": 0,
            "max_verification_runs": 2,
            "no_progress_count": 2,
            "total_reviews": 2,
            "max_total_iterations": 8,
        }
    )

    assert route == "human_review"


def test_phase_3_graph_routes_v2_targeted_rerun() -> None:
    graph = build_phase_3_graph()
    result = graph.invoke(
        {
            "review": {
                "verdict": "needs_targeted_rerun",
                "reviewer_confidence": 90,
                "item_assessments": [
                    {
                        "item_id": "RI-001",
                        "status": "partial",
                        "severity": "major",
                        "failure_mode": "needs_deeper_search",
                        "failure_mode_confidence": 90,
                    }
                ],
            },
            "targeted_rerun_runs": 0,
            "max_targeted_rerun_runs": 2,
            "total_reviews": 1,
            "max_total_iterations": 8,
        }
    )

    assert result["visited_deep_research_submit"] is True
    assert result["graph_terminal"] == "finalize"


def test_phase_4_graph_uses_v2_human_review_actions() -> None:
    checkpointer = MemorySaver()
    graph = build_phase_4_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "thread-v2"}}

    initial = graph.invoke(
        {
            "review": {"rationale": "manual decision required"},
            "report": "draft",
        },
        config=config,
    )
    assert "__interrupt__" in initial

    resumed = graph.invoke(
        Command(resume={"action": HumanReviewAction.REQUEST_TARGETED_RERUN.value}),
        config=config,
    )

    assert resumed["visited_deep_research_submit"] is True


def test_phase_4_graph_routes_human_llm_patch_action_with_v2_route_name() -> None:
    checkpointer = MemorySaver()
    graph = build_phase_4_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "thread-v2-llm-patch"}}

    initial = graph.invoke(
        {
            "review": {"rationale": "manual decision required"},
            "report": "draft",
        },
        config=config,
    )
    assert "__interrupt__" in initial

    resumed = graph.invoke(
        Command(resume={"action": HumanReviewAction.REQUEST_LLM_PATCH.value}),
        config=config,
    )

    assert resumed["visited_llm_finalize"] is True


def test_phase_4_graph_routes_human_reject_to_rejected_terminal() -> None:
    checkpointer = MemorySaver()
    graph = build_phase_4_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "thread-v2-reject"}}

    initial = graph.invoke(
        {
            "review": {"rationale": "manual decision required"},
            "report": "draft",
        },
        config=config,
    )
    assert "__interrupt__" in initial

    resumed = graph.invoke(
        Command(resume={"action": HumanReviewAction.REJECT.value}),
        config=config,
    )

    assert resumed["graph_terminal"] == "human_review_rejected"
