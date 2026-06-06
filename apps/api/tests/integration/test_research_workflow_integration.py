from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.research.poller import ResearchPoller
from api.research.schemas import (
    CreateResearchRunRequest,
    FailureMode,
    HumanReviewAction,
    ItemAssessment,
    ItemStatus,
    RecommendedAction,
    ReviewResult,
    RunStatus,
    Severity,
    Verdict,
)
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


class BlockingReviewFakeAzure(IntegrationFakeAzure):
    def __init__(self) -> None:
        super().__init__(verdicts=[Verdict.PASS])
        self.review_started = threading.Event()
        self.review_can_finish = threading.Event()

    def review_report(self, **kwargs: Any) -> tuple[ReviewResult, str, dict[str, object]]:
        self.review_started.set()
        if not self.review_can_finish.wait(timeout=5):
            raise TimeoutError("Timed out waiting to finish blocked review.")
        return super().review_report(**kwargs)


@pytest.mark.integration
@pytest.mark.anyio
async def test_api_poller_workflow_persists_v2_contract_items_and_audit(
    integration_app: FastAPI,
    integration_orchestrator: ResearchOrchestrator,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=integration_app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報だけを使って短い市場調査をしてください。",
                "options": {"max_total_tool_calls": 5},
            },
        )
        assert create_response.status_code == 202
        run_id = create_response.json()["run_id"]

        poller = ResearchPoller(orchestrator=integration_orchestrator, interval_seconds=0.01)
        await poller.tick()

        status_response = await client.get(f"/research-runs/{run_id}")
        contract_response = await client.get(f"/research-runs/{run_id}/contract")
        items_response = await client.get(f"/research-runs/{run_id}/items")
        report_response = await client.get(f"/research-runs/{run_id}/report")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert status_response.status_code == 200
    assert status_response.json()["status"] == RunStatus.COMPLETED.value
    assert status_response.json()["progress"]["latest_verdict"] == Verdict.PASS.value
    assert status_response.json()["progress"]["items_total"] == 5
    assert contract_response.json()["contract"]["contract_frozen"] is True
    assert len(items_response.json()["items"]) == 5
    assert report_response.json()["final_report"].startswith("調査レポート本文")

    audit = audit_response.json()
    assert len(audit["attempts"]) == 1
    assert len(audit["reviews"]) == 1
    assert audit["objective_contract"]
    assert audit["research_items"]
    assert len(audit["citations"]) == 1
    assert len(audit["tool_calls"]) == 1


@pytest.mark.integration
def test_concurrent_review_run_is_db_claimed_across_orchestrator_instances(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    first_fake = BlockingReviewFakeAzure()
    second_fake = IntegrationFakeAzure()
    first_orchestrator = integration_orchestrator_factory(first_fake)
    second_orchestrator = integration_orchestrator_factory(second_fake)

    run = first_orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報に基づく市場調査をしてください。",
        )
    )

    result: list[RunStatus] = []
    errors: list[BaseException] = []

    def collect() -> None:
        try:
            result.append(first_orchestrator.collect_deep_research(run.id).status)
        except BaseException as error:  # pragma: no cover - forwarded to the test thread
            errors.append(error)

    worker = threading.Thread(target=collect)
    worker.start()
    try:
        assert first_fake.review_started.wait(timeout=5)

        assert (
            second_orchestrator.repository.claim_review_operation(
                run.id,
                operation="llm_finalize",
                lease_seconds=second_orchestrator.settings.research_review_timeout_seconds,
            )
            is None
        )
        duplicate = second_orchestrator.review_run(run.id)

        assert duplicate.status == RunStatus.REVIEWING
        assert len(first_fake.review_calls) == 0
        assert second_fake.review_calls == []
    finally:
        first_fake.review_can_finish.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    assert result == [RunStatus.COMPLETED]
    assert len(first_fake.review_calls) == 1
    assert len(first_orchestrator.repository.get_reviews(run.id)) == 1
    assert first_orchestrator.repository.get_run(run.id).review_claim_token is None


@pytest.mark.integration
def test_llm_patch_path_revises_report_and_re_reviews(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_LLM_PATCH, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報に基づく技術調査をしてください。",
        )
    )
    completed = orchestrator.collect_deep_research(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert completed.final_report == "軽微修正済みレポート本文"
    assert completed.llm_patch_runs == 1
    assert len(orchestrator.repository.get_reviews(run.id)) == 2
    assert fake.llm_finalize_prompts


@pytest.mark.integration
@pytest.mark.anyio
async def test_targeted_rerun_uses_delta_prompt_and_merges_without_replacing_report(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_TARGETED_RERUN, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報に基づく競合調査をしてください。",
        )
    )
    rerun = orchestrator.collect_deep_research(run.id)
    assert rerun.status == RunStatus.WAITING_DEEP_RESEARCH

    poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)
    await poller.tick()

    completed = orchestrator.repository.get_run(run.id)
    plans = orchestrator.repository.get_rerun_plans(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert completed.deep_research_runs == 2
    assert completed.targeted_rerun_runs == 1
    assert "Targeted Research Updates" in (completed.final_report or "")
    assert plans[0].target_item_ids
    assert "Do not return a complete revised report" in str(fake.submit_calls[-1]["prompt"])


@pytest.mark.integration
@pytest.mark.anyio
async def test_full_rerun_uses_full_counter_and_replaces_report(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_FULL_RERUN, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報に基づく調査をフルでやり直してください。",
        )
    )
    waiting = orchestrator.collect_deep_research(run.id)
    assert waiting.status == RunStatus.WAITING_DEEP_RESEARCH

    poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)
    await poller.tick()

    completed = orchestrator.repository.get_run(run.id)
    plans = orchestrator.repository.get_rerun_plans(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert completed.deep_research_runs == 2
    assert completed.full_rerun_runs == 1
    assert completed.targeted_rerun_runs == 0
    assert plans[-1].scope == "full_rerun"
    assert completed.final_report == "調査レポート本文 resp_deep_2"
    assert "Targeted Research Updates" not in (completed.final_report or "")


@pytest.mark.integration
def test_unknown_review_item_id_routes_to_human_review(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(
        verdicts=[Verdict.PASS],
        item_assessments=[
            ItemAssessment(
                item_id="RI-999",
                status=ItemStatus.ANSWERED,
                severity=Severity.MAJOR,
                failure_mode=FailureMode.NONE,
                failure_mode_confidence=90,
                recommended_action=RecommendedAction.NONE,
                evidence_summary="covered",
                missing_evidence=[],
                rationale="unknown item id",
            )
        ],
    )
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報に基づく市場調査をしてください。",
        )
    )
    stopped = orchestrator.collect_deep_research(run.id)

    assert stopped.status == RunStatus.NEEDS_HUMAN_REVIEW
    assert stopped.done_reason == "review_referenced_unknown_research_items"
    assert all(
        item.status == ItemStatus.NOT_STARTED
        for item in orchestrator.repository.get_research_items(run.id)
    )


@pytest.mark.integration
def test_verification_route_runs_policy_and_records_queries(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.NEEDS_VERIFICATION, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    run = orchestrator.create_run(
        CreateResearchRunRequest(
            user_prompt="公開情報に基づく事実確認をしてください。",
        )
    )
    completed = orchestrator.collect_deep_research(run.id)

    assert completed.status == RunStatus.COMPLETED
    assert completed.verification_runs == 1
    assert fake.verify_prompts
    assert "Targeted Verification Notes" in (completed.final_report or "")
    queries = orchestrator.repository.get_verification_queries(run.id)
    assert queries
    assert queries[0].policy_status == "allowed"


@pytest.mark.integration
@pytest.mark.anyio
async def test_human_review_request_targeted_rerun_resume_completes_after_poller(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.HUMAN_REVIEW, Verdict.PASS])
    orchestrator = integration_orchestrator_factory(fake)

    from api.main import create_app
    from api.research.dependencies import get_research_orchestrator

    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報だけで追加調査してください。",
            },
        )
        run_id = create_response.json()["run_id"]
        needs_human = orchestrator.collect_deep_research(run_id)
        assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_TARGETED_RERUN.value,
                "comment": "不足している公式情報を再調査してください。",
            },
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["status"] == RunStatus.WAITING_DEEP_RESEARCH.value

        poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)
        await poller.tick()

        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    assert status_response.json()["status"] == RunStatus.COMPLETED.value
    assert status_response.json()["progress"]["deep_research_runs"] == 2
    assert status_response.json()["progress"]["targeted_rerun_runs"] == 1
    assert audit_response.json()["human_decisions"][0]["action"] == (
        HumanReviewAction.REQUEST_TARGETED_RERUN.value
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_human_review_full_rerun_recovers_empty_error_attempt(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(
        retrieve_statuses=["incomplete", "completed"],
        verdicts=[Verdict.PASS],
    )
    orchestrator = integration_orchestrator_factory(fake)

    from api.main import create_app
    from api.research.dependencies import get_research_orchestrator

    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報だけで調査してください。",
            },
        )
        run_id = create_response.json()["run_id"]
        needs_human = orchestrator.collect_deep_research(run_id)
        assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW
        assert needs_human.report is None

        payload_response = await client.get(f"/research-runs/{run_id}/human-review")
        allowed_actions = payload_response.json()["allowed_actions"]
        assert HumanReviewAction.REQUEST_FULL_RERUN.value in allowed_actions
        assert HumanReviewAction.APPROVE.value not in allowed_actions
        assert HumanReviewAction.REQUEST_TARGETED_RERUN.value not in allowed_actions

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={
                "action": HumanReviewAction.REQUEST_FULL_RERUN.value,
                "comment": "エラーで空レポートになったので全体を再実行してください。",
            },
        )
        assert resume_response.status_code == 200
        assert resume_response.json()["status"] == RunStatus.WAITING_DEEP_RESEARCH.value

        poller = ResearchPoller(orchestrator=orchestrator, interval_seconds=0.01)
        await poller.tick()

        status_response = await client.get(f"/research-runs/{run_id}")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    status = status_response.json()
    assert status["status"] == RunStatus.COMPLETED.value
    assert status["progress"]["deep_research_runs"] == 2
    assert status["progress"]["full_rerun_runs"] == 1
    assert status["progress"]["targeted_rerun_runs"] == 0
    assert audit_response.json()["human_decisions"][0]["action"] == (
        HumanReviewAction.REQUEST_FULL_RERUN.value
    )


@pytest.mark.integration
@pytest.mark.anyio
async def test_human_review_resume_hard_stop_keeps_queue_and_records_no_decision(
    integration_orchestrator_factory: Callable[[IntegrationFakeAzure], ResearchOrchestrator],
) -> None:
    fake = IntegrationFakeAzure(verdicts=[Verdict.HUMAN_REVIEW])
    orchestrator = integration_orchestrator_factory(fake)

    from api.main import create_app
    from api.research.dependencies import get_research_orchestrator

    app = create_app()
    app.dependency_overrides[get_research_orchestrator] = lambda: orchestrator

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/research-runs",
            json={
                "user_prompt": "公開情報だけで調査してください。",
            },
        )
        run_id = create_response.json()["run_id"]
        needs_human = orchestrator.collect_deep_research(run_id)
        assert needs_human.status == RunStatus.NEEDS_HUMAN_REVIEW
        orchestrator.repository.update_run(
            needs_human.id,
            total_reviews=5,
            max_total_iterations=5,
        )

        resume_response = await client.post(
            f"/research-runs/{run_id}/resume",
            json={"action": HumanReviewAction.REQUEST_LLM_PATCH.value},
        )
        queue_response = await client.get("/research-runs/human-reviews")
        audit_response = await client.get(f"/research-runs/{run_id}/audit")

    audit = audit_response.json()
    assert resume_response.status_code == 409
    assert [item["run_id"] for item in queue_response.json()] == [run_id]
    assert audit["human_decisions"] == []
    assert audit["history"][-1]["step"] == "human_review_resume_blocked"
    assert audit["history"][-1]["reason"] == "max_total_iterations_reached"
