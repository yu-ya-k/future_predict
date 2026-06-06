from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from api.research.service import ResearchOrchestrator

logger = logging.getLogger(__name__)


class ResearchPoller:
    def __init__(self, *, orchestrator: ResearchOrchestrator, interval_seconds: float) -> None:
        self.orchestrator = orchestrator
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def run(self) -> None:
        while not self._stop_event.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
            except TimeoutError:
                continue

    async def tick(self) -> None:
        try:
            await self._tick_once()
        except Exception:
            logger.exception("Research poller tick failed")

    async def _tick_once(self) -> None:
        settings = self.orchestrator.settings
        timed_out = self.orchestrator.repository.list_timed_out_runs(
            timeout_seconds=settings.research_deep_research_timeout_seconds,
        )
        for run in timed_out:
            try:
                await asyncio.to_thread(self.orchestrator.mark_timeout, run.id)
            except Exception:
                logger.exception("Failed to mark research run %s as timed out", run.id)

        stale_reviews = self.orchestrator.repository.list_stale_reviewing_runs(
            timeout_seconds=settings.research_review_timeout_seconds,
        )
        for run in stale_reviews:
            try:
                await asyncio.to_thread(self.orchestrator.mark_review_timeout, run.id)
            except Exception:
                logger.exception("Failed to mark review run %s as timed out", run.id)

        waiting = self.orchestrator.repository.list_waiting_runs(
            timeout_seconds=settings.research_deep_research_timeout_seconds,
        )
        for run in waiting:
            claimed = self.orchestrator.repository.claim_deep_research_run(run.id)
            if claimed is None:
                continue
            try:
                await asyncio.to_thread(self.orchestrator.collect_deep_research, claimed.id)
            except Exception:
                logger.exception("Failed to collect research run %s", run.id)
