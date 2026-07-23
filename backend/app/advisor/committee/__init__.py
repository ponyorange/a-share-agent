"""Opt-in Redis/RQ infrastructure for advisor committee jobs.

Importing this package is side-effect free: no Redis connection is created
until a caller explicitly asks for a client, queue, lock, or health check.
"""

from .redis_client import (
    CommitteeConfigurationError,
    CommitteeDisabledError,
    CommitteeRedisSettings,
    health_check,
)
from .execution import (
    CommitteeInvoker,
    ainvoke_committee,
    committee_thread_id,
    create_committee_invoker,
    invoke_committee,
)
from .backtest import create_backtest_provider
from .risk import create_risk_provider

__all__ = [
    "CommitteeConfigurationError",
    "CommitteeDisabledError",
    "CommitteeRedisSettings",
    "CommitteeInvoker",
    "ainvoke_committee",
    "committee_thread_id",
    "create_backtest_provider",
    "create_committee_invoker",
    "create_risk_provider",
    "health_check",
    "invoke_committee",
]
