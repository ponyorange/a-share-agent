from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.advisor.committee.approval import (
    ApprovalPlan,
    PlannedOrder,
    execute_approval_once,
)
from app.advisor.committee.models import RunStatus
from app.advisor.committee.repository import VersionConflict
from app.advisor.committee.repository import CommitteeRepository
from app.advisor.committee.reconcile import reconcile_stale_runs
from app.advisor.committee.tasks import (
    _persist_node_update,
    canonical_event_key,
    execute_committee_job,
    reconcile_checkpoint_to_mongo,
    rq_failure_callback,
)
from app.advisor import paper


NOW = datetime(2026, 7, 22, 2, tzinfo=timezone.utc)


def _plan() -> ApprovalPlan:
    return ApprovalPlan(
        proposal_hash="a" * 64,
        decision_hash="b" * 64,
        account_version=9,
        orders=(
            PlannedOrder(
                symbol="510300",
                side="buy",
                qty=100,
                price=10,
            ),
        ),
    )


class ApprovalCollection:
    def __init__(self):
        self.doc = None

    def insert_one(self, document):
        self.doc = dict(document)

    def find_one(self, query):
        if self.doc and all(self.doc.get(k) == v for k, v in query.items()):
            return dict(self.doc)
        return None

    def find_one_and_update(self, query, update, **_kwargs):
        if not self.doc or any(self.doc.get(k) != v for k, v in query.items()):
            return None
        self.doc.update(update.get("$set", {}))
        return dict(self.doc)


def test_approval_executor_receives_expected_account_version():
    database = SimpleNamespace(committee_approvals=ApprovalCollection())
    captured = {}

    def executor(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    execute_approval_once(
        database,
        user_id="u",
        run_id="r",
        idempotency_key="k",
        plan=_plan(),
        executor=executor,
    )
    assert captured["expected_account_version"] == 9


def test_account_toctou_rejects_version_change_and_aborts_journal(monkeypatch):
    from tests.test_paper_mutation import DB

    fake = DB()
    fake.paper_accounts.doc["account_version"] = 10
    monkeypatch.setattr(paper, "get_account_snapshot_atomic", lambda *a, **k: {
        "user_id": "u",
        "cash": 1000,
        "equity": 1000,
        "positions": [],
        "version": "10",
        "account_version": 10,
        "data_as_of": NOW,
    })
    with pytest.raises(RuntimeError, match="conflict"):
        paper._begin_account_mutation(
            fake,
            "u",
            kind="approval",
            expected_version=9,
            expected_updated_at=NOW,
            external_idempotency_key="key",
            lease_owner="owner",
        )
    journal = next(iter(fake.paper_mutations.docs.values()))
    assert journal["external_idempotency_key"] == "key"
    assert journal["status"] == "aborted"


def test_active_paper_lease_cannot_be_recovered(monkeypatch):
    from tests.test_paper_mutation import DB

    fake = DB()
    fake.paper_mutations.docs["m"] = {
        "user_id": "u",
        "mutation_id": "m",
        "status": "pending",
        "started_at": NOW - timedelta(hours=1),
        "lease_owner": "live",
        "lease_expires_at": NOW + timedelta(minutes=1),
    }
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    with pytest.raises(RuntimeError, match="active mutation lease"):
        paper.recover_pending_account_mutation("u", "m", _db=fake)
    assert fake.paper_mutations.docs["m"]["status"] == "pending"


def test_raw_graph_events_are_persisted_before_node_completed():
    calls = []

    class Repository:
        def upsert_artifact(self, *args, **kwargs):
            calls.append(("artifact", kwargs.get("kind")))

        def append_outbox_event(self, *args, **kwargs):
            calls.append(("event", kwargs["event_type"], kwargs["payload"]))
            return {
                "sequence": len(calls),
                "event_key": kwargs["event_key"],
            }

        def mark_event_published(self, *args, **kwargs):
            return None

    runtime = Mock()
    _persist_node_update(
        Repository(),
        runtime,
        "u",
        "r",
        attempt=1,
        node="risk",
        sequence=1,
        update={
            "events": [
                {
                    "event_id": "r:risk:veto",
                    "event_type": "veto",
                    "payload": {"reason": "limit"},
                }
            ],
            "risk_verdict": {"status": "rejected"},
        },
    )
    event_types = [item[1] for item in calls if item[0] == "event"]
    assert event_types == ["veto", "node_completed"]


def test_callback_version_conflict_rereads_terminal_state():
    class Repository:
        def __init__(self):
            self.reads = 0

        def get_run(self, *_args):
            self.reads += 1
            return SimpleNamespace(
                status=(
                    RunStatus.RUNNING
                    if self.reads == 1
                    else RunStatus.CANCELLED
                ),
                version=1,
                cancel_requested=False,
            )

        def transition_status(self, *_args, **_kwargs):
            raise VersionConflict("race")

    rq_failure_callback(
        SimpleNamespace(meta={"user_id": "u", "run_id": "r"}),
        object(),
        RuntimeError,
        RuntimeError("failed"),
        None,
        repository=Repository(),
    )


def test_real_memory_checkpointer_resumes_with_none_input():
    class State(TypedDict):
        value: int

    builder = StateGraph(State)
    builder.add_node("first", lambda state: {"value": state["value"] + 1})
    builder.add_node("second", lambda state: {"value": state["value"] + 1})
    builder.add_edge(START, "first")
    builder.add_edge("first", "second")
    builder.add_edge("second", END)
    graph = builder.compile(
        checkpointer=InMemorySaver(),
        interrupt_after=["first"],
    )
    config = {"configurable": {"thread_id": "committee:u:r"}}

    async def scenario():
        first = [
            item
            async for item in graph.astream(
                {"value": 0},
                config=config,
                stream_mode="updates",
            )
        ]
        paused = await graph.aget_state(config)
        second = [
            item
            async for item in graph.astream(
                None,
                config=config,
                stream_mode="updates",
            )
        ]
        completed = await graph.aget_state(config)
        return first, paused, second, completed

    import asyncio

    first, paused, second, completed = asyncio.run(scenario())
    assert first == [{"first": {"value": 1}}, {"__interrupt__": ()}]
    assert paused.next == ("second",)
    assert second == [{"second": {"value": 2}}]
    assert completed.values["value"] == 2


def test_outbox_counter_duplicate_retry_and_event_key_dedupe():
    from pymongo.errors import DuplicateKeyError
    from tests.test_committee_repository import FakeDatabase, _run

    class Counters:
        def __init__(self):
            self.calls = 0
            self.sequence = 0

        def find_one_and_update(self, query, update, **kwargs):
            self.calls += 1
            if kwargs.get("upsert") and self.calls == 1:
                raise DuplicateKeyError("counter raced")
            self.sequence += int(update["$inc"]["sequence"])
            return {"sequence": self.sequence}

    database = FakeDatabase()
    database.collections["committee_counters"] = Counters()
    repository = CommitteeRepository(database, clock=lambda: NOW)
    repository.create_run(_run("u", "r"))
    first = repository.append_outbox_event(
        "u",
        "r",
        attempt=1,
        node="risk",
        event_type="veto",
        event_key="attempt:1:risk:veto",
        payload={"reason": "limit"},
    )
    second = repository.append_outbox_event(
        "u",
        "r",
        attempt=1,
        node="risk",
        event_type="veto",
        event_key="attempt:1:risk:veto",
        payload={"reason": "limit"},
    )
    assert first["event_id"] == "1"
    assert second["event_id"] == "1"
    assert database.collections["committee_counters"].calls == 2


def test_second_worker_cannot_execute_same_run(monkeypatch):
    run = SimpleNamespace(
        status=RunStatus.RUNNING,
        run_id="r",
        user_id="u",
    )

    class Repository:
        def get_run(self, user_id, run_id):
            return run

        def transition_status(self, *args, **kwargs):
            pytest.fail("losing worker must not terminalize the run")

    lock = Mock()
    lock.acquire.return_value = False
    runtime = Mock()
    runtime.run_lock.return_value = lock
    monkeypatch.setattr(
        "app.advisor.committee.tasks.CommitteeRepository.from_default_database",
        lambda: Repository(),
    )
    monkeypatch.setattr(
        "app.advisor.committee.tasks.CommitteeRedisSettings.from_env",
        lambda: SimpleNamespace(lock_ttl=30),
    )
    monkeypatch.setattr(
        "app.advisor.committee.tasks.CommitteeRuntime",
        lambda _settings: runtime,
    )
    with pytest.raises(VersionConflict, match="Redis"):
        execute_committee_job("u", "r")


def test_watchdog_keeps_started_job_with_valid_execution_lease(monkeypatch):
    run = SimpleNamespace(
        user_id="u",
        run_id="r",
        status=RunStatus.RUNNING,
        version=3,
        cancel_requested=False,
        queue_job_id="job",
        updated_at=NOW - timedelta(minutes=10),
        job_heartbeat_at=NOW - timedelta(minutes=10),
        job_deadline_at=NOW + timedelta(minutes=5),
        execution_owner="owner",
        execution_heartbeat_at=NOW,
        execution_lease_expires_at=NOW + timedelta(minutes=2),
    )

    class Repository:
        def list_stale_runs(self, **kwargs):
            return [run]

        def transition_status(self, *args, **kwargs):
            pytest.fail("active started job must not fail")

        def request_cancel(self, *args, **kwargs):
            pytest.fail("active started job must not cancel")

    job = Mock()
    job.get_status.return_value = "started"
    monkeypatch.setattr(
        "app.advisor.committee.reconcile.Job.fetch",
        Mock(return_value=job),
    )
    assert reconcile_stale_runs(
        repository=Repository(),
        connection=object(),
        now=NOW,
    ) == []


def test_queued_execution_lease_can_renew_before_running():
    class Runs:
        def update_one(self, query, update):
            assert query["status"]["$in"] == ["queued", "running"]
            assert query["execution_owner"] == "owner"
            return SimpleNamespace(modified_count=1)

    repository = object.__new__(CommitteeRepository)
    repository._runs = Runs()
    repository._clock = lambda: NOW
    assert repository.renew_execution_lease(
        "u",
        "r",
        owner="owner",
        lease_seconds=30,
    )


def test_fencing_takeover_rejects_old_finish(monkeypatch):
    from tests.test_paper_mutation import DB

    fake = DB()
    monkeypatch.setattr(paper, "_now", lambda: NOW)
    mutation_id = paper._begin_account_mutation(
        fake,
        "u",
        kind="approval",
        expected_version=1,
        expected_updated_at=NOW,
        external_idempotency_key="key",
        lease_owner="old",
    )
    old_owner, old_token = paper._mutation_identity(
        fake, "u", mutation_id
    )
    new_token = paper._next_fencing_token(fake, "u")
    assert new_token > old_token
    fake.paper_mutations.docs[mutation_id].update(
        {"lease_owner": "new", "fencing_token": new_token}
    )
    fake.paper_accounts.doc.update(
        {
            "mutation_lease_owner": "new",
            "mutation_fencing_token": new_token,
        }
    )
    original_find = fake.paper_accounts.find_one

    def fenced_find(query, *args, **kwargs):
        doc = original_find(query, *args, **kwargs)
        for field in ("mutation_lease_owner", "mutation_fencing_token"):
            if field in query and query[field] != doc.get(field):
                return None
        return doc

    fake.paper_accounts.find_one = fenced_find
    with pytest.raises(RuntimeError, match="ownership"):
        paper._finish_account_mutation(
            fake,
            "u",
            mutation_id,
            lease_owner=old_owner,
            fencing_token=old_token,
        )


def test_position_fencing_plan_handles_first_buy_and_full_sell():
    assert paper._position_fencing_plan(
        original_positions={},
        final_positions={"510300": {"qty": 100}},
        touched={"510300"},
    ) == {
        "existing": set(),
        "new": {"510300"},
        "delete": set(),
        "update": set(),
    }
    assert paper._position_fencing_plan(
        original_positions={"600000": {"qty": 100}},
        final_positions={},
        touched={"600000"},
    ) == {
        "existing": {"600000"},
        "new": set(),
        "delete": {"600000"},
        "update": set(),
    }


def test_takeover_filters_reject_old_position_and_trade_tokens():
    current_position = {
        "mutation_lease_owner": "new",
        "mutation_fencing_token": 2,
    }
    assert not paper._fencing_matches(current_position, "old", 1)
    assert paper._fencing_matches(current_position, "new", 2)
    current_trade = {"lease_owner": "new", "fencing_token": 2}
    assert not paper._trade_fencing_allows(current_trade, 1)
    assert paper._trade_fencing_allows(current_trade, 2)


def test_checkpoint_reconciliation_repairs_missing_mongo_artifacts_idempotently():
    calls = {"artifacts": [], "events": []}

    class Repository:
        def upsert_artifact(self, *args, **kwargs):
            calls["artifacts"].append(kwargs)
            return kwargs

        def append_outbox_event(self, *args, **kwargs):
            calls["events"].append(kwargs)
            return {
                "sequence": len(calls["events"]),
                "event_key": kwargs["event_key"],
            }

        def mark_event_published(self, *args, **kwargs):
            return None

    runtime = Mock()
    state = {
        "snapshot": {"snapshot_id": "s" * 64, "items": []},
        "analyst_reports": [{"role": "technical", "thesis": "x"}],
        "final_decision": {"action": "hold"},
        "events": [
            {
                "event_id": "r:prepare:ready",
                "event_type": "ready",
                "payload": {},
            }
        ],
        "status": "completed",
    }

    reconcile_checkpoint_to_mongo(
        Repository(), runtime, "u", "r", state, attempt=1
    )

    assert {item["kind"] for item in calls["artifacts"]} == {
        "snapshot",
        "analyst_reports",
        "final_decision",
    }
    assert [item["event_type"] for item in calls["events"]] == [
        "ready",
        "checkpoint_reconciled",
    ]
    assert all("event_key" in item for item in calls["events"])


def test_normal_and_checkpoint_paths_share_canonical_semantic_event_key():
    event = {
        "event_id": "r:risk:veto",
        "node": "risk",
        "event_type": "veto",
        "payload": {"reason": "limit"},
    }
    normal = canonical_event_key(
        "r",
        1,
        "risk",
        "veto",
        event["payload"],
        semantic_id=event["event_id"],
    )
    reconciled = canonical_event_key(
        "r",
        1,
        event["node"],
        event["event_type"],
        event["payload"],
        semantic_id=event["event_id"],
    )

    assert normal == reconciled
    assert normal.startswith("run:r:attempt:1:node:risk:event:veto:")


def test_normal_publish_and_checkpoint_reconcile_do_not_duplicate_sequence():
    class Repository:
        def __init__(self):
            self.events = {}
            self.sequence = 0

        def upsert_artifact(self, *args, **kwargs):
            return kwargs

        def append_outbox_event(self, *args, **kwargs):
            key = kwargs["event_key"]
            if key not in self.events:
                self.sequence += 1
                self.events[key] = {
                    **kwargs,
                    "sequence": self.sequence,
                    "event_key": key,
                }
            return self.events[key]

        def mark_event_published(self, *args, **kwargs):
            return None

    repository = Repository()
    runtime = Mock()
    event = {
        "event_id": "r:risk:veto",
        "node": "risk",
        "event_type": "veto",
        "payload": {"reason": "limit"},
    }
    _persist_node_update(
        repository,
        runtime,
        "u",
        "r",
        attempt=1,
        node="risk",
        sequence=1,
        update={"events": [event]},
    )
    reconcile_checkpoint_to_mongo(
        repository,
        runtime,
        "u",
        "r",
        {"events": [event], "status": "running"},
        attempt=1,
    )

    vetoes = [
        item
        for item in repository.events.values()
        if item["event_type"] == "veto"
    ]
    assert len(vetoes) == 1
    assert vetoes[0]["sequence"] == 1


@pytest.mark.parametrize(
    ("outer_node", "event_node", "event_type", "payload"),
    [
        (
            "analyst_fan_in",
            "fan_in",
            "completed",
            {"report_count": 4},
        ),
        ("bull", "bull:1", "completed", {"round": 1, "role": "bull"}),
        ("bear", "bear:1", "completed", {"round": 1, "role": "bear"}),
    ],
)
def test_stream_and_reconcile_dedupe_normalized_graph_event_identity(
    outer_node,
    event_node,
    event_type,
    payload,
):
    class Repository:
        def __init__(self):
            self.events = {}
            self.sequence = 0

        def upsert_artifact(self, *args, **kwargs):
            return kwargs

        def append_outbox_event(self, *args, **kwargs):
            key = kwargs["event_key"]
            if key not in self.events:
                self.sequence += 1
                self.events[key] = {
                    **kwargs,
                    "sequence": self.sequence,
                }
            return self.events[key]

        def mark_event_published(self, *args, **kwargs):
            return None

    repository = Repository()
    runtime = Mock()
    event = {
        "event_id": f"r:{event_node}:{event_type}",
        "node": event_node,
        "event_type": event_type,
        "payload": payload,
    }
    _persist_node_update(
        repository,
        runtime,
        "u",
        "r",
        attempt=1,
        node=outer_node,
        sequence=1,
        update={"events": [event]},
    )
    reconcile_checkpoint_to_mongo(
        repository,
        runtime,
        "u",
        "r",
        {"events": [event], "status": "running"},
        attempt=1,
    )

    logical = [
        item
        for item in repository.events.values()
        if item["event_type"] == event_type
    ]
    assert len(logical) == 1
