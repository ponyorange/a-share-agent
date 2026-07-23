"""Entrypoint for the opt-in advisor committee RQ worker."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from rq import SimpleWorker

from ..paper import recover_stale_pending_mutations
from .checkpoint import (
    create_checkpoint_saver,
    initialize_checkpoint_saver,
)
from .jobs import create_queue
from .redis_client import (
    CommitteeConfigurationError,
    CommitteeDisabledError,
    CommitteeRedisSettings,
    create_client,
)
from .reconcile import reconcile_stale_runs
from .repository import CommitteeRepository

logger = logging.getLogger(__name__)


class CommitteeWorker(SimpleWorker):
    """In-process RQ worker; avoids pymongo/Redis crashes after ``os.fork``.

    Parent heartbeat also runs the durable watchdog.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._last_committee_reconcile = 0.0

    def heartbeat(self, timeout=None, pipeline=None):
        result = super().heartbeat(timeout=timeout, pipeline=pipeline)
        now = time.monotonic()
        if now - self._last_committee_reconcile >= 30:
            self._last_committee_reconcile = now
            try:
                reconcile_stale_runs(
                    repository=CommitteeRepository.from_default_database(),
                    connection=self.connection,
                )
            except Exception as exc:
                logger.error(
                    "Committee watchdog failed: %s",
                    type(exc).__name__,
                )
        return result


def run_worker(
    *,
    settings: CommitteeRedisSettings | None = None,
    client_factory: Callable[..., Any] = create_client,
    queue_factory: Callable[..., Any] = create_queue,
    worker_class: type[SimpleWorker] | Callable[..., Any] = CommitteeWorker,
    with_scheduler: bool = True,
) -> int:
    """Run a worker, failing safely before startup when disabled/misconfigured."""
    try:
        resolved = (
            settings if settings is not None else CommitteeRedisSettings.from_env()
        )
    except CommitteeConfigurationError as exc:
        logger.error("Committee worker configuration invalid: %s", type(exc).__name__)
        return 2

    if not resolved.enabled:
        logger.info("Committee worker disabled")
        return 0

    try:
        client = client_factory(resolved)
        queue = queue_factory(resolved, connection=client)
    except (CommitteeConfigurationError, CommitteeDisabledError) as exc:
        logger.error("Committee worker not started: %s", type(exc).__name__)
        return 2

    if client_factory is create_client and queue_factory is create_queue:
        checkpoint = initialize_checkpoint_saver(
            resolved,
            saver_factory=lambda value: create_checkpoint_saver(
                value,
                client=client,
            ),
        )
        if not checkpoint.ok:
            logger.error(
                "Committee checkpoint unavailable: %s",
                checkpoint.status,
            )
            return 2
        try:
            recover_stale_pending_mutations(300)
        except Exception as exc:
            logger.error(
                "Paper mutation recovery failed: %s",
                type(exc).__name__,
            )
            return 2

    try:
        worker = worker_class([queue], connection=client)
        worker.work(with_scheduler=with_scheduler)
    except Exception as exc:
        logger.error("Committee worker failed: %s", type(exc).__name__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(run_worker())
