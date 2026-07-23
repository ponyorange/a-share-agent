"""Package-level committee construction and stable invoke adapters."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from ..config_loader import load_config
from .agents import ChatModelRoleRunner, RoleAgentExecutor
from .dependencies import create_production_dependencies
from .graph import CommitteeDependencies, build_committee_graph
from .state import BudgetLimits


INITIAL_INPUT_FIELDS = frozenset(
    {
        "user_id",
        "run_id",
        "snapshot",
        "snapshot_request",
        "limits",
        "max_debate_rounds",
    }
)


def committee_thread_id(user_id: str, run_id: str) -> str:
    if not user_id or not run_id:
        raise ValueError("user_id and run_id are required")
    return f"committee:{user_id}:{run_id}"


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _initial_payload(state: Mapping[str, Any]) -> dict[str, Any]:
    unexpected = set(state).difference(INITIAL_INPUT_FIELDS)
    if unexpected:
        raise ValueError(
            "initial committee input contains forbidden fields: "
            + ", ".join(sorted(unexpected))
        )
    if not state.get("user_id") or not state.get("run_id"):
        raise ValueError("initial committee input requires user_id and run_id")
    has_snapshot = state.get("snapshot") is not None
    has_request = state.get("snapshot_request") is not None
    if has_snapshot and has_request:
        raise ValueError(
            "initial committee input accepts only one of "
            "snapshot or snapshot_request"
        )
    return _json_safe(dict(state))


@dataclass(slots=True)
class CommitteeInvoker:
    graph: Any
    has_checkpointer: bool
    default_limits: BudgetLimits
    default_debate_rounds: int

    async def ainvoke(self, state: Mapping[str, Any]) -> dict[str, Any]:
        payload = _initial_payload(state)
        user_id = str(payload["user_id"])
        run_id = str(payload["run_id"])
        config = {
            "configurable": {
                "thread_id": committee_thread_id(user_id, run_id),
            }
        }
        checkpoint_values: dict[str, Any] = {}
        if self.has_checkpointer:
            snapshot = await self.graph.aget_state(config)
            checkpoint_values = dict(snapshot.values or {})
            if checkpoint_values and set(payload) != {"user_id", "run_id"}:
                raise ValueError(
                    "checkpoint recovery accepts only user_id and run_id"
                )
            if checkpoint_values and (
                checkpoint_values.get("user_id") != user_id
                or checkpoint_values.get("run_id") != run_id
            ):
                raise ValueError("checkpoint recovery identity mismatch")
            if (
                checkpoint_values.get("user_id") == user_id
                and checkpoint_values.get("run_id") == run_id
                and checkpoint_values.get("status") in {"completed", "aborted"}
            ):
                return _json_safe(checkpoint_values)
        if not checkpoint_values:
            if (
                payload.get("snapshot") is None
                and payload.get("snapshot_request") is None
            ):
                raise ValueError(
                    "initial committee input requires snapshot or "
                    "snapshot_request when no checkpoint exists"
                )
            payload.setdefault(
                "limits",
                self.default_limits.model_dump(mode="json"),
            )
            payload.setdefault(
                "max_debate_rounds",
                self.default_debate_rounds,
            )
        result = await self.graph.ainvoke(payload, config=config)
        return _json_safe(result)

    def invoke(self, state: Mapping[str, Any]) -> dict[str, Any]:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.ainvoke(state))
        raise RuntimeError(
            "invoke_committee cannot run inside an event loop; "
            "use ainvoke_committee"
        )


def _committee_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is not None:
        return dict(value)
    configured = load_config().get("committee") or {}
    return dict(configured) if isinstance(configured, Mapping) else {}


def create_committee_invoker(
    *,
    dependencies: CommitteeDependencies | None = None,
    checkpointer: Any | None = None,
    committee_config: Mapping[str, Any] | None = None,
) -> CommitteeInvoker:
    config = _committee_config(committee_config)
    limits = BudgetLimits.model_validate(config.get("budget") or {})
    resolved = dependencies or create_production_dependencies(
        RoleAgentExecutor(ChatModelRoleRunner(config)),
        config,
    )
    return CommitteeInvoker(
        graph=build_committee_graph(resolved, checkpointer=checkpointer),
        has_checkpointer=checkpointer is not None,
        default_limits=limits,
        default_debate_rounds=max(
            1,
            min(int(config.get("max_debate_rounds", 2)), 2),
        ),
    )


async def ainvoke_committee(
    state: Mapping[str, Any],
    *,
    invoker: CommitteeInvoker | None = None,
    dependencies: CommitteeDependencies | None = None,
    checkpointer: Any | None = None,
    committee_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = invoker or create_committee_invoker(
        dependencies=dependencies,
        checkpointer=checkpointer,
        committee_config=committee_config,
    )
    return await resolved.ainvoke(state)


def invoke_committee(
    state: Mapping[str, Any],
    *,
    invoker: CommitteeInvoker | None = None,
    dependencies: CommitteeDependencies | None = None,
    checkpointer: Any | None = None,
    committee_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolved = invoker or create_committee_invoker(
        dependencies=dependencies,
        checkpointer=checkpointer,
        committee_config=committee_config,
    )
    return resolved.invoke(state)
