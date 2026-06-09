from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from api.config import Settings
from api.forecast import router as forecast_router
from api.forecast import service as forecast_service
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.probability.phase_a_v1 import compute_phase_a_estimates
from api.forecast.repository import ForecastRepository
from api.forecast.schemas import (
    FRAMING_ROUGH_QUESTION_MAX_LENGTH,
    ForecastCreateRequest,
    ForecastFramingDraft,
    ForecastFramingDraftAnswer,
    ForecastFramingDraftRequest,
)
from api.forecast.service import ForecastOrchestrator
from api.main import create_app
from api.research.artifacts import ArtifactStore
from api.research.dependencies import get_research_orchestrator
from api.research.repository import ResearchRepository
from api.research.schemas import CreateResearchRunRequest, ResearchRunOptions, RunStatus
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure


def _make_orchestrators(
    tmp_path: Path,
    fake: IntegrationFakeAzure,
) -> tuple[ForecastOrchestrator, ResearchOrchestrator]:
    settings = Settings(
        research_db_path=tmp_path / "phase-a.sqlite3",
        research_artifact_dir=tmp_path / "research-artifacts",
        forecast_artifact_dir=tmp_path / "forecast-artifacts",
        research_poller_enabled=False,
    )
    research = ResearchOrchestrator(
        settings=settings,
        repository=ResearchRepository(settings.research_db_path),
        artifacts=ArtifactStore(settings.research_artifact_dir),
        azure=cast(Any, fake),
    )
    forecast = ForecastOrchestrator(
        settings=settings,
        repository=ForecastRepository(settings.research_db_path),
        artifacts=ForecastArtifactStore(settings.forecast_artifact_dir),
        research_orchestrator=research,
    )
    return forecast, research


def _typed_code(response_json: dict[str, object]) -> str:
    detail = response_json["detail"]
    assert isinstance(detail, dict)
    code = cast(dict[str, object], detail)["code"]
    assert isinstance(code, str)
    return code


def _request_hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


async def _create_approved_forecast(client: AsyncClient, *, question: str) -> str:
    create = await client.post(
        "/forecasts",
        json={
            "question": question,
            "resolution_criteria": "Resolve from public vendor and benchmark reports.",
            "outcomes": ["Reached", "Not reached"],
        },
    )
    assert create.status_code == 202
    forecast_id = create.json()["forecast_id"]

    approve = await client.post(
        f"/forecasts/{forecast_id}/review",
        json={"action": "approve_framing"},
    )
    assert approve.status_code == 200
    return str(forecast_id)


def _forecast_pack_and_run_counts(
    forecast: ForecastOrchestrator,
    forecast_id: str,
) -> tuple[int, int]:
    with forecast.repository.connect() as connection:
        pack_count = connection.execute(
            "SELECT COUNT(*) FROM forecast_research_packs WHERE forecast_id = ?",
            (forecast_id,),
        ).fetchone()[0]
        run_count = connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    return int(pack_count), int(run_count)


class _MissingResponseIdFakeAzure(IntegrationFakeAzure):
    def submit_deep_research(
        self,
        *,
        prompt: str,
        max_tool_calls: int,
        tool_profile: str = "public",
        background: bool = True,
        policy_decision_id: str | None = None,
        **_: object,
    ) -> dict[str, object]:
        self.submit_calls.append(
            {
                "prompt": prompt,
                "max_tool_calls": max_tool_calls,
                "tool_profile": tool_profile,
                "background": background,
                "policy_decision_id": policy_decision_id,
            }
        )
        return {"status": "queued", "output": []}


def _framing_draft_payload(
    *,
    clarifying_questions: list[dict[str, Any]] | None = None,
    confidence: float = 0.82,
    outcomes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "forecast_prompt": "Forecast whether AI agents will handle support tickets.",
        "question": "Will AI agents handle at least 30% of support tickets by 2029?",
        "resolution_criteria": (
            "Resolve as milestone reached if public benchmark or vendor reports "
            "show AI agents handling at least 30% of support tickets by 2029; "
            "otherwise resolve as milestone not reached."
        ),
        "resolution_sources": ["Public vendor reports", "Independent benchmark reports"],
        "target_population": "Customer support teams using AI agents",
        "unit_of_analysis": "Share of support tickets handled end-to-end",
        "decision_context": "Plan support automation roadmap.",
        "outcomes": outcomes
        if outcomes is not None
        else ["Milestone reached", "Milestone not reached"],
        "clarifying_questions": clarifying_questions or [],
        "confidence": confidence,
    }


def test_framing_draft_prompt_extracts_metadata_without_rewriting() -> None:
    rough_question = "By 2030, will public EV charging uptime exceed 99% in Japan?"
    request = ForecastFramingDraftRequest(
        rough_question=rough_question,
        answers=[
            ForecastFramingDraftAnswer(
                question_id="source",
                answer="Use public regulator reports and operator disclosures.",
            )
        ],
        locale="en",
    )

    prompt = forecast_service._build_framing_draft_prompt(request)  # pyright: ignore[reportPrivateUsage]

    assert "extract metadata" in prompt
    assert "primary execution prompt" in prompt
    assert "question is short Forecast metadata" in prompt
    assert "forecast_prompt is only a short UI helper" in prompt
    assert "Do not invent missing metadata." in prompt
    assert "string fields empty, nullable fields null, and list fields empty" in prompt
    assert "clarifying_questions instead of filling fields with assumptions" in prompt
    assert "Core metadata required to create a Forecast is question" in prompt
    assert "Do not repeat answered clarifying questions" in prompt
    assert "keep only unanswered clarifying questions" in prompt
    assert "ask only for missing metadata" in prompt
    assert "outcomes are resolution outcome labels / 解決時の結果状態" in prompt
    assert "not the model's final Yes/No judgment" in prompt
    assert "Do not convert the original prompt into a binary Yes/No forecast" in prompt
    assert "do not ask the user for a final Yes/No answer" in prompt
    assert "leave outcomes empty and ask a required clarifying question" in prompt
    assert "question, resolution_criteria, and outcomes are all non-empty" in prompt
    assert "return clarifying_questions as an empty list" in prompt
    assert "replace, rewrite, refine, summarize, normalize, translate" in prompt
    assert "Keep default binary outcomes Yes/No" not in prompt
    assert "You help refine" not in prompt
    assert "clear, resolvable forecast question" not in prompt

    _, request_json = prompt.split("Request JSON:\n", maxsplit=1)
    payload = json.loads(request_json)
    assert payload["rough_question"] == rough_question
    assert payload["answers"] == [
        {
            "question_id": "source",
            "answer": "Use public regulator reports and operator disclosures.",
        }
    ]


def test_original_prompt_fields_reject_blank_without_trimming_text() -> None:
    original_prompt = "  Forecast the execution task exactly.\nKeep spacing.  "
    rough_question = "  Draft this exact forecast prompt.\n"

    create_request = ForecastCreateRequest(
        question="Will exact prompt preservation work?",
        original_execution_prompt=original_prompt,
        resolution_criteria="Resolve from public evidence.",
    )
    draft_request = ForecastFramingDraftRequest(rough_question=rough_question)

    assert create_request.original_execution_prompt == original_prompt
    assert draft_request.rough_question == rough_question
    with pytest.raises(ValidationError):
        ForecastCreateRequest(
            question="Will blank prompts be rejected?",
            original_execution_prompt=" \n\t ",
            resolution_criteria="Resolve from public evidence.",
        )
    with pytest.raises(ValidationError):
        ForecastFramingDraftRequest(rough_question=" \n\t ")


def test_framing_draft_schema_marks_question_metadata_and_prompt_ui_only() -> None:
    properties = ForecastFramingDraft.model_json_schema()["properties"]

    question_description = properties["question"]["description"]
    forecast_prompt_description = properties["forecast_prompt"]["description"]

    assert "Short resolvable forecast question metadata" in question_description
    assert "primary task" in question_description
    assert "UI helper text only" in forecast_prompt_description
    assert "must not replace, rewrite, summarize" in forecast_prompt_description

    resolution_criteria_schema = properties["resolution_criteria"]
    assert resolution_criteria_schema["default"] == ""
    assert "minLength" not in resolution_criteria_schema
    assert "leave empty when not provided" in resolution_criteria_schema["description"]

    outcomes_schema = properties["outcomes"]
    assert outcomes_schema["description"].startswith(
        "Resolution outcome labels / 解決時の結果状態"
    )
    assert "not the model's final Yes/No judgment" in outcomes_schema["description"]
    assert "default" not in outcomes_schema
    assert ForecastFramingDraft(
        forecast_prompt="UI helper",
        question="Will public EV charging uptime exceed 99% in Japan?",
        resolution_criteria="Resolve from public regulator reports.",
        outcomes=[],
        confidence=0.5,
    ).outcomes == []
    assert ForecastFramingDraft(
        forecast_prompt="UI helper",
        question="Will public EV charging uptime exceed 99% in Japan?",
        resolution_criteria="Resolve from public regulator reports.",
        outcomes=[" Milestone reached ", " ", "\nMilestone missed"],
        confidence=0.5,
    ).outcomes == ["Milestone reached", "Milestone missed"]
    assert ForecastFramingDraft(
        forecast_prompt="UI helper",
        question="Will public EV charging uptime exceed 99% in Japan?",
        resolution_criteria="Resolve from public regulator reports.",
        outcomes=[" ", "\n"],
        confidence=0.5,
    ).outcomes == []


def test_forecast_create_idempotency_payload_omits_absent_original_prompt() -> None:
    request = ForecastCreateRequest(
        question="Will the market adopt AI agents?",
        resolution_criteria="Resolve from public sources.",
        outcomes=["Yes", "No"],
    )

    payload = forecast_router._forecast_create_idempotency_payload(request)  # pyright: ignore[reportPrivateUsage]

    assert "original_execution_prompt" not in payload
    assert payload == {
        "question": "Will the market adopt AI agents?",
        "resolution_date": None,
        "target_population": None,
        "unit_of_analysis": None,
        "resolution_criteria": "Resolve from public sources.",
        "resolution_sources": [],
        "decision_context": None,
        "confidentiality_class": "public",
        "outcomes": ["Yes", "No"],
    }


@pytest.mark.anyio
async def test_framing_draft_route_order_and_happy_draft(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure(structured_parse_results=[_framing_draft_payload()])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": (
                    "AI agents might handle 30% of support tickets by 2029."
                ),
                "locale": "en",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["question"].startswith("Will AI agents")
    assert body["draft"]["outcomes"] == ["Milestone reached", "Milestone not reached"]
    assert body["ready_to_create"] is True
    assert body["create_payload"]["question"] == body["draft"]["question"]
    assert body["create_payload"]["outcomes"] == [
        "Milestone reached",
        "Milestone not reached",
    ]
    assert body["create_payload"]["outcomes"] != ["Yes", "No"]
    assert body["model"] == fake.reviewer_deployment
    assert body["response_id"] == "resp_structured_1"
    assert fake.structured_parse_calls[0]["tool_profile"] == "synthesis"
    assert fake.structured_parse_calls[0]["policy_decision_id"] is None
    assert fake.structured_parse_calls[0]["vector_store_ids"] is None


@pytest.mark.anyio
async def test_framing_draft_empty_resolution_criteria_needs_clarification(
    tmp_path: Path,
) -> None:
    payload = _framing_draft_payload(
        clarifying_questions=[
            {
                "question_id": "resolution_criteria",
                "label": "Resolution criteria",
                "prompt": "How should this forecast be resolved from public evidence?",
                "why_needed": "The forecast cannot be created without resolution criteria.",
                "answer_type": "text",
                "required": True,
                "options": [],
            }
        ],
        confidence=0.51,
    )
    payload["resolution_criteria"] = ""
    fake = IntegrationFakeAzure(structured_parse_results=[payload])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will AI agents handle many support tickets?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["resolution_criteria"] == ""
    assert body["draft"]["clarifying_questions"][0]["question_id"] == (
        "resolution_criteria"
    )
    assert body["draft"]["clarifying_questions"][0]["required"] is True
    assert body["ready_to_create"] is False
    assert body["create_payload"] is None
    assert body["warnings"] == ["required_clarifying_answers_missing"]


@pytest.mark.anyio
async def test_framing_draft_empty_outcomes_needs_resolution_axis(
    tmp_path: Path,
) -> None:
    payload = _framing_draft_payload(
        clarifying_questions=[
            {
                "question_id": "outcomes",
                "label": "Resolution outcome labels",
                "prompt": (
                    "What mutually exclusive outcome labels should be selected at "
                    "resolution time?"
                ),
                "why_needed": (
                    "The forecast cannot be created without explicit resolution "
                    "outcome labels."
                ),
                "answer_type": "text",
                "required": True,
                "options": [],
            }
        ],
        confidence=0.51,
    )
    payload["outcomes"] = []
    fake = IntegrationFakeAzure(structured_parse_results=[payload])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": (
                    "Assess support automation adoption by 2029 without imposing a "
                    "binary final answer."
                ),
                "locale": "en",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["outcomes"] == []
    assert body["draft"]["clarifying_questions"][0]["question_id"] == "outcomes"
    assert body["ready_to_create"] is False
    assert body["create_payload"] is None
    assert body["warnings"] == ["required_clarifying_answers_missing"]


@pytest.mark.anyio
async def test_framing_draft_accepts_long_rough_question(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure(structured_parse_results=[_framing_draft_payload()])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    long_question = "Forecast planning premise. " * 240
    assert 5000 < len(long_question) < FRAMING_ROUGH_QUESTION_MAX_LENGTH

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": long_question},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ready_to_create"] is True
    assert fake.structured_parse_calls[0]["model"] == fake.reviewer_deployment


@pytest.mark.anyio
async def test_framing_draft_answers_can_make_required_questions_ready(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(
        structured_parse_results=[
            _framing_draft_payload(
                clarifying_questions=[
                    {
                        "question_id": "deadline",
                        "label": "Resolution deadline",
                        "prompt": "What date should the forecast resolve against?",
                        "why_needed": "The forecast needs a concrete horizon.",
                        "answer_type": "date",
                        "required": True,
                        "options": [],
                    }
                ],
                confidence=0.64,
            )
        ]
    )
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Will AI agents handle many support tickets?",
                "answers": [
                    {
                        "question_id": "deadline",
                        "answer": "Resolve against public data available by 2029-12-31.",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ready_to_create"] is True
    assert body["draft"]["clarifying_questions"] == []
    assert body["warnings"] == []
    assert body["create_payload"]["outcomes"] == [
        "Milestone reached",
        "Milestone not reached",
    ]


@pytest.mark.anyio
async def test_framing_draft_core_metadata_ready_even_with_new_required_question(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(
        structured_parse_results=[
            _framing_draft_payload(
                clarifying_questions=[
                    {
                        "question_id": "deadline",
                        "label": "Resolution deadline",
                        "prompt": "What date should the forecast resolve against?",
                        "why_needed": "The forecast needs a concrete horizon.",
                        "answer_type": "date",
                        "required": True,
                        "options": [],
                    },
                    {
                        "question_id": "priority",
                        "label": "Metric priority",
                        "prompt": "Which metric priority should be used?",
                        "why_needed": "This can improve metadata quality.",
                        "answer_type": "text",
                        "required": True,
                        "options": [],
                    },
                ],
                confidence=0.64,
            )
        ]
    )
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Will AI agents handle many support tickets?",
                "answers": [
                    {
                        "question_id": "deadline",
                        "answer": "Resolve against public data available by 2029-12-31.",
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ready_to_create"] is True
    assert body["warnings"] == []
    assert body["create_payload"]["question"]
    assert body["create_payload"]["resolution_criteria"]
    assert body["create_payload"]["outcomes"] == [
        "Milestone reached",
        "Milestone not reached",
    ]
    assert [
        question["question_id"] for question in body["draft"]["clarifying_questions"]
    ] == ["priority"]


@pytest.mark.anyio
async def test_framing_draft_keeps_unanswered_core_metadata_required(
    tmp_path: Path,
) -> None:
    payload = _framing_draft_payload(
        clarifying_questions=[
            {
                "question_id": "outcomes",
                "label": "Resolution outcome labels",
                "prompt": "What outcome labels should be selected at resolution time?",
                "why_needed": "The forecast cannot be created without outcomes.",
                "answer_type": "text",
                "required": True,
                "options": [],
            }
        ],
        outcomes=[],
    )
    fake = IntegrationFakeAzure(structured_parse_results=[payload])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Forecast support automation adoption by 2029.",
                "answers": [
                    {
                        "question_id": "outcomes",
                        "answer": "Use milestone reached and milestone missed.",
                    }
                ],
                "locale": "en",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["draft"]["outcomes"] == []
    assert body["draft"]["clarifying_questions"] == []
    assert body["ready_to_create"] is False
    assert body["create_payload"] is None
    assert body["warnings"] == ["required_clarifying_answers_missing"]


@pytest.mark.anyio
async def test_framing_draft_keeps_changed_prompt_for_answered_question_id(
    tmp_path: Path,
) -> None:
    previous_draft = _framing_draft_payload(
        clarifying_questions=[
            {
                "question_id": "outcomes",
                "label": "Resolution outcome labels",
                "prompt": "What outcome labels should be selected at resolution time?",
                "why_needed": "The forecast cannot be created without outcomes.",
                "answer_type": "text",
                "required": True,
                "options": [],
            }
        ],
        outcomes=[],
    )
    payload = _framing_draft_payload(
        clarifying_questions=[
            {
                "question_id": "outcomes",
                "label": "Resolution outcome labels",
                "prompt": "Which mutually exclusive resolution states should be used?",
                "why_needed": "The prior answer did not define selectable outcome labels.",
                "answer_type": "text",
                "required": True,
                "options": [],
            }
        ],
        outcomes=[],
    )
    fake = IntegrationFakeAzure(structured_parse_results=[payload])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Forecast support automation adoption by 2029.",
                "answers": [
                    {
                        "question_id": "outcomes",
                        "answer": "Use the most important resolution axis.",
                    }
                ],
                "previous_draft": previous_draft,
                "locale": "en",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ready_to_create"] is False
    assert body["create_payload"] is None
    assert body["draft"]["outcomes"] == []
    assert body["draft"]["clarifying_questions"] != []
    assert [
        question["prompt"] for question in body["draft"]["clarifying_questions"]
    ] == ["Which mutually exclusive resolution states should be used?"]
    assert body["warnings"] == ["required_clarifying_answers_missing"]


@pytest.mark.anyio
async def test_framing_draft_blocks_sensitive_inputs_before_llm(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Will internal project codename Phoenix launch?",
                "answers": [{"question_id": "scope", "answer": "Public customers"}],
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert _typed_code(body) == "policy_requires_revision"
    assert "Phoenix" not in json.dumps(body)
    assert fake.structured_parse_calls == []


@pytest.mark.anyio
async def test_framing_draft_blocks_sensitive_previous_draft_before_llm(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    previous_draft = _framing_draft_payload()
    previous_draft["decision_context"] = "Internal project codename Phoenix."

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={
                "rough_question": "Will the public launch happen?",
                "previous_draft": previous_draft,
            },
        )

    assert response.status_code == 409
    body = response.json()
    assert _typed_code(body) == "policy_requires_revision"
    assert "Phoenix" not in json.dumps(body)
    assert fake.structured_parse_calls == []


@pytest.mark.anyio
async def test_framing_draft_idempotency_replays_conflicts_and_in_progress(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(structured_parse_results=[_framing_draft_payload()])
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        payload = {
            "rough_question": "Will idempotent framing draft requests replay?",
            "locale": "en",
        }
        first = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-replay"},
            json=payload,
        )
        replay = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-replay"},
            json=payload,
        )
        conflict = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-replay"},
            json={**payload, "rough_question": "Different framing question"},
        )

        in_progress_request = ForecastFramingDraftRequest(
            rough_question="Will in-progress framing idempotency block duplicates?"
        )
        existing = forecast.repository.reserve_idempotency_record(
            command_scope="forecast:framing_draft",
            resource_id="",
            idempotency_key="framing-in-progress",
            request_hash=_request_hash(in_progress_request.model_dump(mode="json")),
        )
        assert existing is None
        duplicate = await client.post(
            "/forecasts/framing-drafts",
            headers={"Idempotency-Key": "framing-in-progress"},
            json={"rough_question": in_progress_request.rough_question},
        )

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json()["response_id"] == first.json()["response_id"]
    assert len(fake.structured_parse_calls) == 1
    assert conflict.status_code == 409
    assert _typed_code(conflict.json()) == "idempotency_conflict"
    assert duplicate.status_code == 409
    assert _typed_code(duplicate.json()) == "idempotency_in_progress"


@pytest.mark.anyio
async def test_framing_draft_parse_text_fallback_and_invalid_response(
    tmp_path: Path,
) -> None:
    fallback_fake = IntegrationFakeAzure(
        structured_parse_results=[
            "Here is the draft JSON:\n"
            + json.dumps(_framing_draft_payload(), ensure_ascii=False)
        ]
    )
    fallback_forecast, fallback_research = _make_orchestrators(tmp_path / "ok", fallback_fake)
    fallback_app = create_app()
    fallback_app.dependency_overrides[get_forecast_orchestrator] = (
        lambda: fallback_forecast
    )
    fallback_app.dependency_overrides[get_research_orchestrator] = (
        lambda: fallback_research
    )

    async with AsyncClient(
        transport=ASGITransport(app=fallback_app),
        base_url="http://testserver",
    ) as client:
        fallback = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will fallback JSON parse correctly?"},
        )

    invalid_fake = IntegrationFakeAzure(
        structured_parse_results=['{"forecast_prompt": "missing required fields"}']
    )
    invalid_forecast, invalid_research = _make_orchestrators(
        tmp_path / "invalid",
        invalid_fake,
    )
    invalid_app = create_app()
    invalid_app.dependency_overrides[get_forecast_orchestrator] = (
        lambda: invalid_forecast
    )
    invalid_app.dependency_overrides[get_research_orchestrator] = (
        lambda: invalid_research
    )

    async with AsyncClient(
        transport=ASGITransport(app=invalid_app),
        base_url="http://testserver",
    ) as client:
        invalid = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will invalid JSON schema return a typed error?"},
        )

    assert fallback.status_code == 200
    assert fallback.json()["draft"]["confidence"] == 0.82
    assert invalid.status_code == 502
    assert _typed_code(invalid.json()) == "framing_draft_invalid_response"


@pytest.mark.anyio
async def test_framing_draft_runtime_failure_returns_unavailable(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(
        structured_parse_raises=RuntimeError("reviewer unavailable")
    )
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts/framing-drafts",
            json={"rough_question": "Will unavailable reviewer return 503?"},
        )

    assert response.status_code == 503
    assert _typed_code(response.json()) == "framing_draft_unavailable"


@pytest.mark.anyio
async def test_forecast_preserves_original_execution_prompt_for_research_pack(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    original_prompt = (
        "  Forecast the exact execution task without rewriting.\n"
        "Include the user's scenario framing verbatim.  "
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "original_execution_prompt": original_prompt,
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        assert detail.json()["original_execution_prompt"] == original_prompt
        assert detail.json()["current_research_pack"] is None

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200

    submitted_prompt = str(fake.submit_calls[-1]["prompt"])
    assert f"Primary execution prompt:\n{original_prompt}" in submitted_prompt
    assert "\nResolution outcome metadata:\n" in submitted_prompt
    assert "\nOutcomes:\n" not in submitted_prompt
    assert "- Forecast question: Will AI agents handle 30% of support tickets by 2029?" in (
        submitted_prompt
    )


@pytest.mark.anyio
async def test_forecast_create_rejects_blank_original_execution_prompt(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts",
            json={
                "question": "Will blank execution prompts be rejected?",
                "original_execution_prompt": " \n\t ",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_forecast_create_without_outcomes_uses_legacy_binary_fallback(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will legacy direct create still receive binary outcomes?",
                "resolution_criteria": "Resolve from public evidence.",
            },
        )
        assert create.status_code == 202
        detail = await client.get(f"/forecasts/{create.json()['forecast_id']}")

    assert detail.status_code == 200
    assert [outcome["label"] for outcome in detail.json()["outcomes"]] == ["Yes", "No"]


@pytest.mark.anyio
async def test_forecast_create_blank_outcomes_uses_legacy_binary_fallback(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will blank outcome labels be normalized?",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["  ", "\n"],
            },
        )
        assert create.status_code == 202
        detail = await client.get(f"/forecasts/{create.json()['forecast_id']}")

    assert detail.status_code == 200
    assert [outcome["label"] for outcome in detail.json()["outcomes"]] == ["Yes", "No"]


@pytest.mark.anyio
async def test_research_pack_blocks_legacy_forecast_without_outcomes(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will a legacy empty-outcome forecast be blocked?",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200
        with forecast.repository.connect() as connection:
            connection.execute(
                "DELETE FROM forecast_outcomes WHERE forecast_id = ?",
                (forecast_id,),
            )
        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )

    assert pack.status_code == 409
    assert _typed_code(pack.json()) == "forecast_outcomes_required"
    assert fake.submit_calls == []


@pytest.mark.anyio
async def test_research_pack_accepts_max_length_original_prompt_with_metadata(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    prompt_unit = (
        "Use public sources to assess whether the product launch milestone happens. "
    )
    original_prompt = (prompt_unit * FRAMING_ROUGH_QUESTION_MAX_LENGTH)[
        :FRAMING_ROUGH_QUESTION_MAX_LENGTH
    ]
    assert len(original_prompt) == FRAMING_ROUGH_QUESTION_MAX_LENGTH

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will long Forecast prompts dispatch research packs?",
                "original_execution_prompt": original_prompt,
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200

    research_run = research.repository.get_run(UUID(pack.json()["research_run_id"]))
    assert original_prompt in research_run.user_prompt
    assert len(fake.submit_calls) == 1


@pytest.mark.anyio
async def test_research_pack_prompt_falls_back_to_metadata_for_old_forecasts(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will robotics firms ship one million humanoids by 2030?",
                "resolution_criteria": "Resolve from public company and industry reports.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        assert detail.json()["original_execution_prompt"] is None

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200

    submitted_prompt = str(fake.submit_calls[-1]["prompt"])
    assert (
        "Primary execution prompt:\n"
        "Will robotics firms ship one million humanoids by 2030?"
        in submitted_prompt
    )
    assert "Original execution prompt was not stored for this forecast" in submitted_prompt
    assert "\nResolution outcome metadata:\n" in submitted_prompt
    assert "\nOutcomes:\n" not in submitted_prompt
    assert (
        "- Forecast question: Will robotics firms ship one million humanoids by 2030?"
        in submitted_prompt
    )


@pytest.mark.anyio
async def test_repository_migrates_old_forecast_rows_without_original_prompt(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "phase-a.sqlite3"
    forecast_id = "11111111-1111-1111-1111-111111111111"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE forecast_forecasts (
                id TEXT PRIMARY KEY,
                question TEXT NOT NULL,
                resolution_date TEXT,
                target_population TEXT,
                unit_of_analysis TEXT,
                resolution_criteria TEXT NOT NULL DEFAULT '',
                resolution_sources_json TEXT NOT NULL DEFAULT '[]',
                decision_context TEXT,
                confidentiality_class TEXT NOT NULL DEFAULT 'public',
                status TEXT NOT NULL,
                current_framing_version INTEGER NOT NULL DEFAULT 1,
                approved_framing_version INTEGER,
                committed_version_id TEXT,
                resolved_outcome_id TEXT,
                resolved_at TEXT,
                resolution_notes TEXT,
                idempotency_key TEXT UNIQUE,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO forecast_forecasts (
                id, question, resolution_criteria, status,
                current_framing_version, approved_framing_version,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, 1, ?, ?)
            """,
            (
                forecast_id,
                "Will old forecasts keep working after migration?",
                "Resolve from public sources.",
                "framing_approved",
                "2026-06-08T00:00:00+00:00",
                "2026-06-08T00:00:00+00:00",
            ),
        )

    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    with sqlite3.connect(db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(forecast_forecasts)")
        }
    assert "original_execution_prompt" in columns

    row = forecast.repository.get_forecast(UUID(forecast_id))
    base = ForecastRepository.forecast_row_to_dict(row)
    assert base["original_execution_prompt"] is None

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        assert detail.json()["original_execution_prompt"] is None

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 409

    assert _typed_code(pack.json()) == "forecast_outcomes_required"
    assert fake.submit_calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("research_status", "done_reason", "needs_human_review"),
    [
        ("failed", "deep_research_failed", False),
        ("needs_human_review", "review_schema_or_request_failed", True),
    ],
)
async def test_current_research_pack_effective_status_uses_terminal_research_run(
    tmp_path: Path,
    research_status: str,
    done_reason: str,
    needs_human_review: bool,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        run_id = pack.json()["research_run_id"]

        updated_run = research.repository.update_run(
            UUID(run_id),
            status=research_status,
            done_reason=done_reason,
            needs_human_review=needs_human_review,
        )
        assert updated_run.status.value == research_status
        assert updated_run.done_reason == done_reason
        assert updated_run.needs_human_review is needs_human_review

        detail = await client.get(f"/forecasts/{forecast_id}")

    assert detail.status_code == 200
    detail_json = detail.json()
    assert detail_json["current_research_pack_status"] == research_status
    pack_detail = detail_json["current_research_pack"]
    assert pack_detail["research_run_id"] == run_id
    assert pack_detail["pack_status"] == research_status
    assert pack_detail["effective_status"] == research_status
    assert pack_detail["research_run_status"] == research_status
    assert pack_detail["done_reason"] == done_reason
    assert pack_detail["needs_human_review"] is needs_human_review
    assert pack_detail["deep_research_started_at"]


@pytest.mark.anyio
async def test_research_pack_missing_deep_research_response_id_needs_review(
    tmp_path: Path,
) -> None:
    fake = _MissingResponseIdFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will queued research without response ids be visible?",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_json = pack.json()
        assert pack_json["status"] == "needs_human_review"
        run_id = UUID(pack_json["research_run_id"])

        run = research.repository.get_run(run_id)
        assert run.status.value == "needs_human_review"
        assert run.needs_human_review is True
        assert run.done_reason == "missing_deep_research_response_id"
        assert run.pending_deep_research_response_id is None

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        detail_json = detail.json()
        assert detail_json["current_research_pack_status"] == "needs_human_review"
        pack_detail = detail_json["current_research_pack"]
        assert pack_detail["pack_status"] == "needs_human_review"
        assert pack_detail["effective_status"] == "needs_human_review"
        assert pack_detail["research_run_status"] == "needs_human_review"
        assert pack_detail["done_reason"] == "missing_deep_research_response_id"
        assert pack_detail["needs_human_review"] is True


@pytest.mark.anyio
async def test_forecast_detail_reconciles_legacy_missing_response_id_pack(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will legacy missing response id packs recover on detail?",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = UUID(create.json()["forecast_id"])
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        run = research.create_run_record(
            CreateResearchRunRequest(
                user_prompt="legacy pack prompt",
                options=ResearchRunOptions(max_total_tool_calls=3),
            ),
            forecast_mode=True,
        )
        waiting_run = research.repository.update_run_if_status(
            run.id,
            {RunStatus.QUEUED},
            status=RunStatus.WAITING_DEEP_RESEARCH,
            needs_human_review=False,
            pending_deep_research_response_id=None,
            done_reason=None,
        )
        assert waiting_run is not None
        policy_decision_id = forecast.repository.add_policy_decision(
            forecast_id=forecast_id,
            profile="public",
            status="allowed",
            reason=None,
            prompt_hash="legacy-pack-prompt-hash",
        )
        forecast.repository.add_research_pack(
            forecast_id=forecast_id,
            research_run_id=run.id,
            pack_role="current_state",
            tool_profile="public",
            status="submitting",
            model_deployment="test-deployment",
            prompt_version="legacy-test",
            max_tool_calls=3,
            policy_decision_id=policy_decision_id,
        )

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        detail_json = detail.json()
        assert detail_json["current_research_pack_status"] == "needs_human_review"
        pack_detail = detail_json["current_research_pack"]
        assert pack_detail["pack_status"] == "needs_human_review"
        assert pack_detail["effective_status"] == "needs_human_review"
        assert pack_detail["research_run_status"] == "needs_human_review"
        assert pack_detail["done_reason"] == "missing_deep_research_response_id"


@pytest.mark.anyio
async def test_research_pack_submit_failure_remains_visible_on_forecast(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure(submit_raises=RuntimeError("remote submit failed"))
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_json = pack.json()
        assert pack_json["status"] == "needs_human_review"
        run_id = pack_json["research_run_id"]

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        detail_json = detail.json()
        assert detail_json["current_research_pack_status"] == "needs_human_review"
        pack_detail = detail_json["current_research_pack"]
        assert pack_detail["research_run_id"] == run_id
        assert pack_detail["pack_status"] == "needs_human_review"
        assert pack_detail["effective_status"] == "needs_human_review"
        assert pack_detail["research_run_status"] == "needs_human_review"
        assert pack_detail["done_reason"] == "deep_research_submit_failed"
        assert pack_detail["needs_human_review"] is True

        delete_response = await client.delete(f"/research-runs/{run_id}")
        assert delete_response.status_code == 409
        assert "forecast_linked_research_run" in str(delete_response.json()["detail"])


@pytest.mark.anyio
async def test_manual_research_pack_import_links_completed_pack_and_evidence(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    report = (
        "Public source A reports adoption grew in 2028.\n"
        "Public source B says support-ticket automation exceeded the threshold.\n"
        "Counter evidence remains limited but public benchmarks support the result."
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Reached", "Not reached"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        prompt = await client.get(
            f"/forecasts/{forecast_id}/research-packs/manual-prompt"
        )
        assert prompt.status_code == 200
        prompt_json = prompt.json()
        assert prompt_json["forecast_id"] == forecast_id
        assert prompt_json["pack_role"] == "current_state"
        assert prompt_json["tool_profile"] == "public"
        assert prompt_json["prompt_sha256"] == hashlib.sha256(
            prompt_json["prompt"].encode("utf-8")
        ).hexdigest()

        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-1"},
            data={
                "prompt_sha256": prompt_json["prompt_sha256"],
                "report_text": report,
            },
        )
        assert imported.status_code == 200
        imported_json = imported.json()
        assert imported_json["status"] == "completed"
        run_id = imported_json["research_run_id"]

        replay = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-1"},
            data={
                "prompt_sha256": prompt_json["prompt_sha256"],
                "report_text": report,
            },
        )
        assert replay.status_code == 200
        assert replay.json() == imported_json

        conflict = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-1"},
            data={
                "prompt_sha256": prompt_json["prompt_sha256"],
                "report_text": f"{report}\nDifferent line.",
            },
        )
        assert conflict.status_code == 409
        assert _typed_code(conflict.json()) == "idempotency_conflict"

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200
        detail_json = detail.json()
        assert detail_json["status"] == "pack_running"
        assert detail_json["current_research_pack_status"] == "completed"
        pack_detail = detail_json["current_research_pack"]
        assert pack_detail["research_run_id"] == run_id
        assert pack_detail["pack_status"] == "completed"
        assert pack_detail["effective_status"] == "completed"
        assert pack_detail["research_run_status"] == "completed"
        assert pack_detail["done_reason"] is None
        assert pack_detail["needs_human_review"] is False

        run_status = await client.get(f"/research-runs/{run_id}")
        assert run_status.status_code == 200
        run_status_json = run_status.json()
        assert run_status_json["status"] == "completed"
        assert run_status_json["terminal_status"] == "completed_manual_import"
        assert run_status_json["forecast_context"]["forecast_id"] == forecast_id
        assert run_status_json["forecast_context"]["pack_id"] == imported_json["pack_id"]
        assert research.repository.get_run(UUID(run_id)).run_origin == "forecast"

        delete_response = await client.delete(f"/research-runs/{run_id}")
        assert delete_response.status_code == 409
        assert "forecast_linked_research_run" in str(delete_response.json()["detail"])

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 200
        assert evidence.json()["sources"]
        assert evidence.json()["claims"]

        duplicate = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-2"},
            data={
                "prompt_sha256": prompt_json["prompt_sha256"],
                "report_text": report,
            },
        )
        assert duplicate.status_code == 409
        assert _typed_code(duplicate.json()) == "research_pack_already_exists"


@pytest.mark.anyio
async def test_manual_research_pack_import_rejects_stale_prompt_without_run(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Reached", "Not reached"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-stale"},
            data={
                "prompt_sha256": "0" * 64,
                "report_text": "Public report text from a manual Deep Research run.",
            },
        )
        assert imported.status_code == 409
        assert _typed_code(imported.json()) == "prompt_stale"

    with forecast.repository.connect() as connection:
        pack_count = connection.execute(
            "SELECT COUNT(*) FROM forecast_research_packs WHERE forecast_id = ?",
            (forecast_id,),
        ).fetchone()[0]
        run_count = connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert pack_count == 0
    assert run_count == 0


@pytest.mark.anyio
async def test_manual_research_pack_import_rejects_invalid_prompt_hash_before_report(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    forecast_id = "00000000-0000-4000-8000-000000000001"

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            data={
                "prompt_sha256": "A" * 64,
                "report_text": "",
            },
        )

    assert imported.status_code == 422
    detail = cast(list[dict[str, Any]], imported.json()["detail"])
    assert isinstance(detail, list)
    assert any(error["loc"][-1] == "prompt_sha256" for error in detail)
    assert _forecast_pack_and_run_counts(forecast, forecast_id) == (0, 0)


@pytest.mark.anyio
async def test_manual_research_pack_import_rejects_sensitive_report_without_run(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(
            client,
            question="Will AI agents handle 30% of support tickets by 2029?",
        )
        prompt = await client.get(
            f"/forecasts/{forecast_id}/research-packs/manual-prompt"
        )
        assert prompt.status_code == 200

        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-sensitive"},
            data={
                "prompt_sha256": prompt.json()["prompt_sha256"],
                "report_text": "Public summary accidentally included API_KEY=skipped.",
            },
        )

    assert imported.status_code == 409
    assert _typed_code(imported.json()) == "policy_requires_revision"
    assert _forecast_pack_and_run_counts(forecast, forecast_id) == (0, 0)


@pytest.mark.anyio
async def test_manual_research_pack_file_upload_tracks_filename_idempotency(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    report = (
        "Public source A reports adoption grew in 2028.\n"
        "Public source B says support-ticket automation exceeded the threshold.\n"
        "Public benchmarks support the result."
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(
            client,
            question="Will AI agents handle 30% of support tickets by 2029?",
        )
        prompt = await client.get(
            f"/forecasts/{forecast_id}/research-packs/manual-prompt"
        )
        assert prompt.status_code == 200
        prompt_sha256 = prompt.json()["prompt_sha256"]

        invalid_file = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            data={"prompt_sha256": prompt_sha256},
            files={
                "report_file": (
                    "report.pdf",
                    report.encode("utf-8"),
                    "application/pdf",
                ),
            },
        )
        assert invalid_file.status_code == 422
        assert _forecast_pack_and_run_counts(forecast, forecast_id) == (0, 0)

        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-file"},
            data={"prompt_sha256": prompt_sha256},
            files={
                "report_file": (
                    "first.md",
                    report.encode("utf-8"),
                    "text/markdown",
                ),
            },
        )
        assert imported.status_code == 200
        imported_json = imported.json()
        assert imported_json["status"] == "completed"

        replay = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-file"},
            data={"prompt_sha256": prompt_sha256},
            files={
                "report_file": (
                    "first.md",
                    report.encode("utf-8"),
                    "text/markdown",
                ),
            },
        )
        assert replay.status_code == 200
        assert replay.json() == imported_json

        conflict = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            headers={"Idempotency-Key": "manual-pack-file"},
            data={"prompt_sha256": prompt_sha256},
            files={
                "report_file": (
                    "second.md",
                    report.encode("utf-8"),
                    "text/markdown",
                ),
            },
        )

    assert conflict.status_code == 409
    assert _typed_code(conflict.json()) == "idempotency_conflict"
    assert _forecast_pack_and_run_counts(forecast, forecast_id) == (1, 1)


@pytest.mark.anyio
async def test_manual_research_pack_import_cleans_up_run_when_pack_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research
    report = (
        "Public source A reports adoption grew in 2028.\n"
        "Public source B says support-ticket automation exceeded the threshold."
    )

    def fail_pack_update(**_kwargs: object) -> sqlite3.Row:
        raise RuntimeError("pack update failed")

    monkeypatch.setattr(
        forecast.repository,
        "update_research_pack_status",
        fail_pack_update,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        forecast_id = await _create_approved_forecast(
            client,
            question="Will AI agents handle 30% of support tickets by 2029?",
        )
        prompt = await client.get(
            f"/forecasts/{forecast_id}/research-packs/manual-prompt"
        )
        assert prompt.status_code == 200

        imported = await client.post(
            f"/forecasts/{forecast_id}/research-packs/manual-import",
            data={
                "prompt_sha256": prompt.json()["prompt_sha256"],
                "report_text": report,
            },
        )
        assert imported.status_code == 500

        detail = await client.get(f"/forecasts/{forecast_id}")
        assert detail.status_code == 200

    assert _forecast_pack_and_run_counts(forecast, forecast_id) == (0, 0)
    assert not research.artifacts.root.exists() or not any(
        research.artifacts.root.iterdir()
    )
    detail_json = detail.json()
    assert detail_json["status"] == "framing_approved"
    assert detail_json["current_research_pack"] is None
    assert detail_json["current_research_pack_status"] is None


@pytest.mark.anyio
async def test_phase_a_forecast_lifecycle_and_forecast_research_mode(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "forecast-create-1"},
            json={
                "question": "Will AI agents handle 30% of support tickets by 2029?",
                "resolution_criteria": "Resolve from public vendor and benchmark reports.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]

        blocked = await client.post(f"/forecasts/{forecast_id}/research-packs", json={})
        assert blocked.status_code == 409
        assert _typed_code(blocked.json()) == "framing_not_approved"

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing", "comment": "framing ok"},
        )
        assert approve.status_code == 200
        assert approve.json()["approved_framing_version"] == 1

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-1"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        pack_json = pack.json()
        run_id = pack_json["research_run_id"]
        assert pack_json["policy_decision_id"]
        assert fake.submit_calls[-1]["tool_profile"] == "public"
        assert fake.submit_calls[-1]["background"] is False
        assert fake.submit_calls[-1]["policy_decision_id"] == pack_json["policy_decision_id"]
        detail_after_pack = await client.get(f"/forecasts/{forecast_id}")
        assert detail_after_pack.status_code == 200
        pack_detail = detail_after_pack.json()["current_research_pack"]
        assert detail_after_pack.json()["current_research_pack_status"] == "running"
        assert pack_detail["research_run_id"] == run_id
        assert pack_detail["pack_status"] == "running"
        assert pack_detail["effective_status"] == "running"
        assert pack_detail["research_run_status"] == "waiting_deep_research"
        assert pack_detail["deep_research_started_at"]
        assert pack_detail["total_tool_calls"] == 0
        assert pack_detail["estimated_cost_usd"] == 0.0
        assert pack_detail["done_reason"] is None
        assert pack_detail["needs_human_review"] is False

        run_status = await client.get(f"/research-runs/{run_id}")
        assert run_status.status_code == 200
        forecast_context = run_status.json()["forecast_context"]
        assert forecast_context["forecast_id"] == forecast_id
        assert forecast_context["pack_id"] == pack_json["pack_id"]
        assert forecast_context["pack_role"] == "current_state"
        assert forecast_context["tool_profile"] == "public"

        completed_run = research.collect_deep_research(run_id)
        assert completed_run.status == "completed"
        assert completed_run.done_reason == "forecast_raw_report_collected"
        assert fake.review_calls == []
        detail_after_collect = await client.get(f"/forecasts/{forecast_id}")
        assert detail_after_collect.status_code == 200
        assert (
            detail_after_collect.json()["current_research_pack_status"] == "completed"
        )
        collected_pack_detail = detail_after_collect.json()["current_research_pack"]
        assert collected_pack_detail["research_run_id"] == run_id
        assert collected_pack_detail["pack_status"] == "completed"
        assert collected_pack_detail["effective_status"] == "completed"
        assert collected_pack_detail["research_run_status"] == "completed"
        assert collected_pack_detail["done_reason"] == "forecast_raw_report_collected"
        assert collected_pack_detail["needs_human_review"] is False
        completed_run_status = await client.get(f"/research-runs/{run_id}")
        assert completed_run_status.status_code == 200
        completed_forecast_context = completed_run_status.json()["forecast_context"]
        assert completed_forecast_context["forecast_id"] == forecast_id
        assert completed_forecast_context["pack_id"] == pack_json["pack_id"]

        delete_response = await client.delete(f"/research-runs/{run_id}")
        assert delete_response.status_code == 409
        assert "forecast_linked_research_run" in str(delete_response.json()["detail"])

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 200
        assert evidence.json()["sources"]
        assert evidence.json()["claims"]

        scenarios = await client.post(f"/forecasts/{forecast_id}/scenarios/generate")
        assert scenarios.status_code == 200
        assert all(item["outcome_id"] for item in scenarios.json()["scenarios"])

        compute_without_link_approval = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute"
        )
        assert compute_without_link_approval.status_code == 409
        assert _typed_code(compute_without_link_approval.json()) == (
            "claim_targets_not_approved"
        )

        link_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_claim_target_links"},
        )
        assert link_approval.status_code == 200
        detail_after_link_approval = await client.get(f"/forecasts/{forecast_id}")
        assert detail_after_link_approval.status_code == 200
        assert (
            detail_after_link_approval.json()["approved_claim_target_link_count"] > 0
        )

        estimate = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")
        assert estimate.status_code == 200
        estimate_json = cast(dict[str, Any], estimate.json())
        assert estimate_json["engine_version"] == "phase_a_v1"
        assert estimate_json["random_seed"] == 0
        assert len(estimate_json["input_snapshot_hash"]) == 64
        estimates = cast(list[dict[str, Any]], estimate_json["estimates"])
        total_probability = sum(float(item["final_probability"]) for item in estimates)
        assert abs(total_probability - 1.0) < 1e-12
        assert any(
            item["components"]["cross_impact_engine"] == "none" for item in estimates
        )

        replay = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")
        assert replay.status_code == 200
        assert replay.json()["estimate_set_id"] == estimate_json["estimate_set_id"]

        commit_without_approval = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit_without_approval.status_code == 409
        assert _typed_code(commit_without_approval.json()) == "approval_required"

        version_approval = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={
                "action": "approve_phase_a_version",
                "estimate_set_id": estimate_json["estimate_set_id"],
            },
        )
        assert version_approval.status_code == 200
        current_estimate_after_approval = await client.get(
            f"/forecasts/{forecast_id}/estimate-set"
        )
        assert current_estimate_after_approval.status_code == 200
        assert current_estimate_after_approval.json()["approved"] is True

        commit = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit.status_code == 200
        assert commit.json()["snapshot_artifact_path"]

        current_estimate = await client.get(f"/forecasts/{forecast_id}/estimate-set")
        assert current_estimate.status_code == 200
        assert current_estimate.json()["estimate_set_id"] == estimate_json["estimate_set_id"]

        compute_after_commit = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute"
        )
        assert compute_after_commit.status_code == 409
        assert _typed_code(compute_after_commit.json()) == "estimate_set_already_committed"

        outcome_id = estimate_json["estimates"][0]["target_id"]
        resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={"outcome_id": outcome_id, "resolution_notes": "resolved"},
        )
        assert resolve.status_code == 200
        assert resolve.json()["scorer_version"] == "phase_a_scorer_v1"
        assert resolve.json()["multiclass_brier"] >= 0
        assert resolve.json()["log_score"] >= 0

        second_resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={"outcome_id": outcome_id},
        )
        assert second_resolve.status_code == 409
        assert _typed_code(second_resolve.json()) == "forecast_already_resolved"

        audit = await client.get(f"/forecasts/{forecast_id}/audit")
        assert audit.status_code == 200
        event_types = [event["event_type"] for event in audit.json()["events"]]
        assert "version_committed" in event_types
        assert "forecast_resolved" in event_types


@pytest.mark.anyio
async def test_forecast_idempotency_replays_and_conflicts(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        payload = {
            "question": "Will idempotency replay this forecast?",
            "resolution_criteria": "Resolve from public sources.",
            "outcomes": ["Yes", "No"],
        }
        first = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "create-replay"},
            json=payload,
        )
        replay = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "create-replay"},
            json=payload,
        )
        conflict = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "create-replay"},
            json={**payload, "question": "Different body"},
        )

        assert first.status_code == 202
        assert replay.status_code == 202
        assert replay.json()["forecast_id"] == first.json()["forecast_id"]
        assert conflict.status_code == 409
        assert _typed_code(conflict.json()) == "idempotency_conflict"

        listed = await client.get("/forecasts")
        assert listed.status_code == 200
        assert [
            item["forecast_id"] for item in listed.json()
        ] == [first.json()["forecast_id"]]

        forecast_id = first.json()["forecast_id"]
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            headers={"Idempotency-Key": "approve-framing-replay"},
            json={"action": "approve_framing"},
        )
        approve_replay = await client.post(
            f"/forecasts/{forecast_id}/review",
            headers={"Idempotency-Key": "approve-framing-replay"},
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200
        assert approve_replay.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-replay"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        pack_replay = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-replay"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        pack_retry = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200
        assert pack_replay.status_code == 200
        assert pack_retry.status_code == 200
        assert pack_replay.json()["pack_id"] == pack.json()["pack_id"]
        assert pack_retry.json()["pack_id"] == pack.json()["pack_id"]
        assert len(fake.submit_calls) == 1


@pytest.mark.anyio
async def test_research_pack_unique_race_returns_existing_and_deletes_losing_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will duplicate forecast pack posts race safely?",
                "resolution_criteria": "Resolve from public evidence.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = UUID(create.json()["forecast_id"])
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        first = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert first.status_code == 200
        first_json = first.json()

        with forecast.repository.connect() as connection:
            connection.execute(
                """
                UPDATE forecast_forecasts
                SET status = 'framing_approved'
                WHERE id = ?
                """,
                (str(forecast_id),),
            )

        original_list_packs = forecast.repository.list_packs
        list_pack_calls = 0

        def stale_empty_list_packs(forecast_id_arg: UUID) -> list[sqlite3.Row]:
            nonlocal list_pack_calls
            list_pack_calls += 1
            if list_pack_calls <= 2:
                return []
            return original_list_packs(forecast_id_arg)

        monkeypatch.setattr(forecast.repository, "list_packs", stale_empty_list_packs)

        duplicate = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            json={"pack_role": "current_state", "tool_profile": "public"},
        )

    assert duplicate.status_code == 200
    duplicate_json = duplicate.json()
    assert duplicate_json["pack_id"] == first_json["pack_id"]
    assert duplicate_json["research_run_id"] == first_json["research_run_id"]
    assert len(fake.submit_calls) == 1
    assert list_pack_calls >= 2
    with research.repository.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM research_runs
            WHERE run_origin = 'forecast'
            """,
        ).fetchone()
    assert row is not None
    assert row["count"] == 1


@pytest.mark.anyio
async def test_forecast_idempotency_in_progress_blocks_duplicate_side_effects(
    tmp_path: Path,
) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        create = await client.post(
            "/forecasts",
            json={
                "question": "Will in-progress idempotency block duplicates?",
                "resolution_criteria": "Resolve from public sources.",
                "outcomes": ["Yes", "No"],
            },
        )
        assert create.status_code == 202
        forecast_id = create.json()["forecast_id"]
        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        payload = {"pack_role": "current_state", "tool_profile": "public"}
        canonical_payload = {**payload, "max_tool_calls": 40}
        existing = forecast.repository.reserve_idempotency_record(
            command_scope="forecast:research_pack",
            resource_id=forecast_id,
            idempotency_key="pack-in-progress",
            request_hash=_request_hash(canonical_payload),
        )
        assert existing is None

        duplicate = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "pack-in-progress"},
            json=payload,
        )

        assert duplicate.status_code == 409
        assert _typed_code(duplicate.json()) == "idempotency_in_progress"
        assert fake.submit_calls == []


@pytest.mark.anyio
async def test_forecast_disabled_returns_typed_conflict(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, research = _make_orchestrators(tmp_path, fake)
    forecast.settings = forecast.settings.model_copy(update={"forecast_enabled": False})
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/forecasts",
            json={
                "question": "Will disabled forecasts reject mutations?",
                "resolution_criteria": "Resolve from public sources.",
                "outcomes": ["Yes", "No"],
            },
        )

    assert response.status_code == 409
    assert _typed_code(response.json()) == "forecast_disabled"


def test_forecast_audit_events_are_append_only(tmp_path: Path) -> None:
    fake = IntegrationFakeAzure()
    forecast, _research = _make_orchestrators(tmp_path, fake)
    created = forecast.create_forecast(
        request=forecast_create_request(),
        idempotency_key=None,
    )
    forecast_id = created.forecast_id

    with forecast.repository.connect() as connection:
        row = connection.execute(
            "SELECT event_id FROM forecast_audit_events WHERE forecast_id = ? LIMIT 1",
            (str(forecast_id),),
        ).fetchone()
        assert row is not None
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "UPDATE forecast_audit_events SET event_type = 'mutated' WHERE event_id = ?",
                (row["event_id"],),
            )
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(
                "DELETE FROM forecast_audit_events WHERE event_id = ?",
                (row["event_id"],),
            )


def test_phase_a_softmax_exact_reference_case() -> None:
    snapshot = {
        "outcomes": [
            {"outcome_id": "yes"},
            {"outcome_id": "no"},
        ],
        "claims": [
            {
                "claim_id": "c1",
                "evidence_strength": 1.0,
                "reliability_score": 1.0,
                "cluster_id": "cluster-a",
                "independence_group": "group-a",
            },
            {
                "claim_id": "c2",
                "evidence_strength": 1.0,
                "reliability_score": 1.0,
                "cluster_id": "cluster-b",
                "independence_group": "group-b",
            },
        ],
        "approved_target_links": [
            {
                "claim_id": "c1",
                "target_kind": "outcome",
                "target_id": "yes",
                "direction": 1,
                "relevance_weight": 1.0,
            },
            {
                "claim_id": "c2",
                "target_kind": "outcome",
                "target_id": "no",
                "direction": -1,
                "relevance_weight": 1.0,
            },
        ],
    }

    estimates = compute_phase_a_estimates(snapshot=snapshot, epsilon_floor=0.0)

    actual_probability = float(estimates[0]["final_probability"])
    assert abs(actual_probability - 0.8807970779778823) < 1e-15


def forecast_create_request():
    from api.forecast.schemas import ForecastCreateRequest

    return ForecastCreateRequest(
        question="Will the market adopt AI agents?",
        resolution_criteria="Resolve from public sources.",
        outcomes=["Yes", "No"],
    )
