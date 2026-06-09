# Forecast PhaseA Manual Verification

Use this runbook to smoke-test Forecast PhaseA locally without touching the
workspace `.data/` files. Live Azure/OpenAI calls are not required unless you
explicitly choose to test real Research pack dispatch.

## Full Lifecycle Smoke With Test Double

This exercises the Forecast API through FastAPI/httpx and injects the local
Research fake used by integration tests. It verifies the full PhaseA state
machine, idempotency replay/conflict behavior, Forecast-mode Research
collection, linked ResearchRun deletion protection, probability computation,
commit, resolve, and audit retrieval.

```sh
PYTHONPATH=apps/api/src:apps/api/tests \
UV_CACHE_DIR=.uv-cache \
uv --project apps/api run python - <<'PY'
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

from httpx import ASGITransport, AsyncClient

from api.config import Settings
from api.forecast.artifacts import ForecastArtifactStore
from api.forecast.dependencies import get_forecast_orchestrator
from api.forecast.repository import ForecastRepository
from api.forecast.service import ForecastOrchestrator
from api.main import create_app
from api.research.artifacts import ArtifactStore
from api.research.dependencies import get_research_orchestrator
from api.research.repository import ResearchRepository
from api.research.service import ResearchOrchestrator
from research_fakes import IntegrationFakeAzure

ROOT = Path("/private/tmp/future_predict_manual_forecast_smoke")
DB = ROOT / "manual.sqlite3"


def typed_code(payload: dict[str, Any]) -> str:
    detail = payload.get("detail")
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return detail["code"]
    return str(detail)


async def main() -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    settings = Settings(
        research_db_path=DB,
        research_artifact_dir=ROOT / "research-artifacts",
        forecast_artifact_dir=ROOT / "forecast-artifacts",
        research_poller_enabled=False,
    )
    fake = IntegrationFakeAzure()
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
    app = create_app()
    app.dependency_overrides[get_forecast_orchestrator] = lambda: forecast
    app.dependency_overrides[get_research_orchestrator] = lambda: research

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://manual-smoke",
    ) as client:
        assert (await client.get("/forecasts/human-reviews")).status_code == 200
        payload = {
            "question": "Will AI agents handle 30% of support workflows by 2029?",
            "resolution_criteria": "Resolve from public vendor and benchmark reports.",
            "outcomes": ["Yes", "No"],
        }
        create = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "manual-create-1"},
            json=payload,
        )
        assert create.status_code == 202, create.text
        forecast_id = create.json()["forecast_id"]

        replay = await client.post(
            "/forecasts",
            headers={"Idempotency-Key": "manual-create-1"},
            json=payload,
        )
        assert replay.status_code == 202
        assert replay.json()["forecast_id"] == forecast_id

        pre_pack = await client.post(f"/forecasts/{forecast_id}/research-packs", json={})
        assert pre_pack.status_code == 409
        assert typed_code(pre_pack.json()) == "framing_not_approved"

        approve = await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_framing"},
        )
        assert approve.status_code == 200

        pack = await client.post(
            f"/forecasts/{forecast_id}/research-packs",
            headers={"Idempotency-Key": "manual-pack-1"},
            json={"pack_role": "current_state", "tool_profile": "public"},
        )
        assert pack.status_code == 200, pack.text
        run_id = pack.json()["research_run_id"]
        assert fake.submit_calls[-1]["tool_profile"] == "public"
        assert fake.submit_calls[-1]["background"] is False

        completed_run = research.collect_deep_research(run_id)
        assert completed_run.status == "completed"
        assert completed_run.done_reason == "forecast_raw_report_collected"
        assert fake.review_calls == []

        delete_linked = await client.delete(f"/research-runs/{run_id}")
        assert delete_linked.status_code == 409
        assert "forecast_linked_research_run" in delete_linked.text

        evidence = await client.post(f"/forecasts/{forecast_id}/evidence/extract")
        assert evidence.status_code == 200
        assert evidence.json()["sources"] and evidence.json()["claims"]

        scenarios = await client.post(f"/forecasts/{forecast_id}/scenarios/generate")
        assert scenarios.status_code == 200

        compute_blocked = await client.post(
            f"/forecasts/{forecast_id}/probabilities/compute"
        )
        assert compute_blocked.status_code == 409
        assert typed_code(compute_blocked.json()) == "claim_targets_not_approved"

        await client.post(
            f"/forecasts/{forecast_id}/review",
            json={"action": "approve_claim_target_links"},
        )
        estimate = await client.post(f"/forecasts/{forecast_id}/probabilities/compute")
        assert estimate.status_code == 200, estimate.text
        estimate_json = estimate.json()
        assert estimate_json["engine_version"] == "phase_a_v1"
        assert len(estimate_json["input_snapshot_hash"]) == 64

        commit_blocked = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit_blocked.status_code == 409
        assert typed_code(commit_blocked.json()) == "approval_required"

        await client.post(
            f"/forecasts/{forecast_id}/review",
            json={
                "action": "approve_phase_a_version",
                "estimate_set_id": estimate_json["estimate_set_id"],
            },
        )
        commit = await client.post(
            f"/forecasts/{forecast_id}/versions/commit",
            json={
                "estimate_set_id": estimate_json["estimate_set_id"],
                "expected_input_snapshot_hash": estimate_json["input_snapshot_hash"],
            },
        )
        assert commit.status_code == 200

        outcome_id = estimate_json["estimates"][0]["target_id"]
        resolve = await client.post(
            f"/forecasts/{forecast_id}/resolve",
            json={"outcome_id": outcome_id, "resolution_notes": "manual smoke"},
        )
        assert resolve.status_code == 200
        assert resolve.json()["scorer_version"] == "phase_a_scorer_v1"

        audit = await client.get(f"/forecasts/{forecast_id}/audit")
        assert audit.status_code == 200
        event_types = [event["event_type"] for event in audit.json()["events"]]
        assert "version_committed" in event_types
        assert "forecast_resolved" in event_types

    print("MANUAL_SMOKE_PASS")
    print(f"forecast_id={forecast_id}")
    print(f"research_run_id={run_id}")
    print(f"estimate_set_id={estimate_json['estimate_set_id']}")
    print(f"input_snapshot_hash={estimate_json['input_snapshot_hash']}")


asyncio.run(main())
PY
```

## Real HTTP Smoke Without Live Azure

Start the API against temporary local storage:

```sh
RESEARCH_DB_PATH=/private/tmp/future_predict_manual_server/research.sqlite3 \
RESEARCH_ARTIFACT_DIR=/private/tmp/future_predict_manual_server/research-runs \
FORECAST_ARTIFACT_DIR=/private/tmp/future_predict_manual_server/forecast-runs \
RESEARCH_POLLER_ENABLED=false \
UV_CACHE_DIR=.uv-cache \
uv --project apps/api run uvicorn api.main:app --host 127.0.0.1 --port 8017
```

In another shell, verify static route ordering, create/idempotency behavior,
typed precondition errors, and framing approval:

```sh
BASE=http://127.0.0.1:8017
curl -sS "$BASE/health"
curl -sS "$BASE/forecasts/human-reviews"

body='{"question":"Will manual HTTP smoke create a Forecast?","resolution_criteria":"Resolve from public sources.","outcomes":["Yes","No"]}'
create=$(curl -sS -X POST "$BASE/forecasts" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: http-create-1' \
  -d "$body")
forecast_id=$(printf '%s' "$create" | jq -r '.forecast_id')

curl -sS -X POST "$BASE/forecasts" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: http-create-1' \
  -d "$body" | jq -r '.forecast_id'

curl -sS -X POST "$BASE/forecasts/$forecast_id/research-packs" \
  -H 'Content-Type: application/json' \
  -d '{}' | jq -r '.detail.code'

curl -sS -X POST "$BASE/forecasts/$forecast_id/review" \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: http-approve-framing-1' \
  -d '{"action":"approve_framing","comment":"manual HTTP smoke"}' | jq -r '.status'
```

Expected signals:

- `GET /health` returns `{"status":"ok","env":"development"}`.
- `GET /forecasts/human-reviews` returns `[]`.
- Replaying `Idempotency-Key: http-create-1` returns the same `forecast_id`.
- Research pack before framing approval returns `framing_not_approved`.
- Framing approval returns `framing_approved`.

## Web Smoke

Start Vite against the temporary API:

```sh
COREPACK_HOME=$PWD/.corepack \
VITE_API_BASE_URL=http://127.0.0.1:8017 \
corepack pnpm --dir apps/web dev --host 127.0.0.1 --port 5177
```

Open `http://127.0.0.1:5177/#/forecasts`. The dashboard should render the
Forecast created by the HTTP smoke. Open `#/forecasts/new`, create a framing,
confirm the preview renders, and approve framing. If no browser surface is
available in the agent environment, verify the dev server and API connection
with:

```sh
curl -sS -I http://127.0.0.1:5177/#/forecasts
curl -sS http://127.0.0.1:8017/forecasts | jq -r '.[0] | [.status, .question] | @tsv'
```

## Live Research Pack Dispatch

Only run this when live API testing is explicitly approved and both Forecast
and Research live-test environment flags are set. Use a public forecast,
dispatch `{"pack_role":"current_state","tool_profile":"public"}`, wait for the
Research poller to collect the run, and then continue with evidence extraction.
Do not use private data approval or reforecast workflows in PhaseA.
