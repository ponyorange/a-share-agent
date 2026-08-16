from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.advisor import paper
from app.advisor.committee import routes
from app.advisor.committee.models import (
    BacktestVerdict,
    CommitteeRun,
    FinalDecision,
    RiskVerdict,
    RunStatus,
    TradeDirection,
    TradeProposal,
    VerdictStatus,
)
from app.advisor.committee.reconcile import reconcile_stale_runs
from app.advisor.committee.repository import CommitteeRepository
from app.advisor.committee.risk import proposal_semantics_hash
from app.advisor.committee.runtime import CommitteeRuntime
from app.advisor.committee.tasks import (
    _persist_node_update,
    _publish,
    reconcile_checkpoint_to_mongo,
)
from app.auth import get_current_user
from tests.committee_http_app import leftover_committee_app
from tests.test_paper_atomic_flow import Collection, Database


class CommitteeDatabase(Database):
    def __init__(self):
        super().__init__()
        self.committee_runs = Collection(
            unique=(
                ("user_id", "run_id"),
                ("user_id", "idempotency_key"),
            )
        )
        self.committee_artifacts = Collection(
            unique=(("user_id", "run_id", "artifact_id"),)
        )
        self.committee_events = Collection(
            unique=(
                ("user_id", "run_id", "event_id"),
                ("user_id", "run_id", "event_key"),
            )
        )
        self.committee_counters = Collection(
            unique=(("user_id", "run_id"),)
        )


class BytesRedis:
    def __init__(self):
        self.streams = {}

    def xadd(self, key, fields, id=None, **_kwargs):
        stream = self.streams.setdefault(key, [])
        event_id = id or f"{len(stream) + 1}-0"
        stream.append(
            (
                event_id.encode(),
                {
                    str(field).encode(): str(value).encode()
                    for field, value in fields.items()
                },
            )
        )
        return event_id.encode()

    def xread(self, streams, count=None, block=None):
        del block
        key, after = next(iter(streams.items()))
        after_number = int(str(after).split("-", 1)[0])
        rows = [
            row
            for row in self.streams.get(key, [])
            if int(row[0].decode().split("-", 1)[0]) > after_number
        ]
        return [(key.encode(), rows[:count])] if rows else []


class CapturingQueue:
    def __init__(self):
        self.call = None
        self.connection = object()

    def enqueue_call(self, **kwargs):
        self.call = kwargs
        return SimpleNamespace(id=kwargs["job_id"])


class ControlledRqWorker:
    def __init__(self, queue, runner):
        self.queue = queue
        self.runner = runner

    def perform_enqueued_job(self):
        assert self.queue.call is not None
        return self.runner(*self.queue.call["args"])


def _artifacts(run_id: str):
    now = datetime.now(timezone.utc)
    snapshot_id = "s" * 64
    proposal = TradeProposal(
        user_id="u",
        run_id=run_id,
        strategy_id="advisor-score-v2",
        strategy_version="v1",
        symbol="510300",
        direction=TradeDirection.BUY,
        target_weight=0.2,
        confidence=0.8,
        rationale="通过",
        order_type="limit",
        limit_price=10,
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )
    digest = proposal_semantics_hash((proposal,))
    backtest = BacktestVerdict(
        user_id="u",
        run_id=run_id,
        passed=True,
        score=0.8,
        metrics={"sample_count": 50, "trade_count": 5, "max_drawdown": 0.1},
        summary="通过",
        proposal_hash=digest,
        created_at=now,
    )
    risk = RiskVerdict(
        user_id="u",
        run_id=run_id,
        status=VerdictStatus.APPROVED,
        max_position=0.2,
        approved_weight=0.2,
        confidence=1,
        proposal_hash=digest,
        created_at=now,
    )
    decision = FinalDecision(
        user_id="u",
        run_id=run_id,
        action=TradeDirection.BUY,
        symbol="510300",
        target_weight=0.2,
        confidence=0.8,
        rationale="批准",
        risk_status=VerdictStatus.APPROVED,
        proposals=(proposal,),
        orders=(proposal,),
        proposal_hash=digest,
        created_at=now,
    )
    snapshot = {
        "snapshot_id": snapshot_id,
        "items": [
            {
                "name": "portfolio_account",
                "content": {"account_version": 0},
            },
            {
                "name": "kline",
                "content": {"510300": {"bars": [{"close": 10}]}},
            },
        ],
    }
    return snapshot, backtest, risk, decision


def test_authenticated_run_to_sse_approval_and_exactly_once_paper(monkeypatch):
    database = CommitteeDatabase()
    repository = CommitteeRepository(database)
    redis = BytesRedis()
    settings = routes.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "1", "REDIS_HOST": "redis.invalid"}
    )
    runtime = CommitteeRuntime(settings, client=redis)
    queue = CapturingQueue()

    monkeypatch.setattr(routes, "_repository", lambda: repository)
    monkeypatch.setattr(routes, "_infra", lambda: (settings, runtime, queue))
    monkeypatch.setattr(
        routes.CommitteeRedisSettings,
        "from_env",
        classmethod(lambda cls, environ=None: settings),
    )
    monkeypatch.setattr(routes, "CommitteeRuntime", lambda _settings: runtime)
    monkeypatch.setattr(routes, "get_db", lambda: database)
    monkeypatch.setattr(paper, "get_db", lambda: database)
    monkeypatch.setattr(
        routes,
        "_current_market_status",
        lambda symbol: {
            "quote": 10,
            "volume": 1000,
            "suspended": False,
            "limit_up": False,
            "limit_down": False,
            "locked": False,
            "source": "fixed",
            "as_of": datetime.now(timezone.utc),
        },
    )
    monkeypatch.setattr(
        routes,
        "get_account_snapshot_atomic",
        paper.get_account_snapshot_atomic,
    )
    test_app = leftover_committee_app()
    test_app.dependency_overrides[get_current_user] = lambda: {
        "id": "u",
        "username": "u",
    }

    try:
        client = TestClient(test_app)
        created = client.post(
            "/api/advisor/committee/runs",
            headers={"Idempotency-Key": "create-e2e"},
            json={
                "symbols": ["510300"],
                "boards": [],
                "horizon": "next_day",
                "strategy_version": "v1",
            },
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run_id"]
        assert queue.call["func"] == (
            "app.advisor.committee.tasks.execute_committee_job"
        )
        assert queue.call["args"] == ("u", run_id)
        assert queue.call["on_failure"].__name__ == "rq_failure_callback"

        def fake_graph_runner(user_id, queued_run_id):
            run = repository.get_run(user_id, queued_run_id)
            repository.transition_status(
                user_id,
                queued_run_id,
                expected_version=run.version,
                new_status=RunStatus.RUNNING,
            )
            snapshot, backtest, risk, decision = _artifacts(queued_run_id)
            _persist_node_update(
                repository,
                runtime,
                user_id,
                queued_run_id,
                attempt=1,
                node="analysts",
                sequence=1,
                update={
                    "events": [
                        {
                            "event_id": f"{queued_run_id}:analysts:done",
                            "event_type": "analysts_completed",
                            "payload": {"roles": 4},
                        }
                    ],
                    "snapshot": snapshot,
                },
            )
            _persist_node_update(
                repository,
                runtime,
                user_id,
                queued_run_id,
                attempt=1,
                node="chair",
                sequence=1,
                update={
                    "backtest_verdict": backtest,
                    "risk_verdict": risk,
                    "final_decision": decision,
                },
            )
            run = repository.get_run(user_id, queued_run_id)
            terminal = repository.transition_status(
                user_id,
                queued_run_id,
                expected_version=run.version,
                new_status=RunStatus.COMPLETED,
                snapshot_id="s" * 64,
            )
            _publish(
                repository,
                runtime,
                user_id,
                queued_run_id,
                "completed",
                {"version": terminal.version},
                attempt=1,
                node="worker",
                event_key="attempt:1:terminal:completed",
            )
            return terminal

        terminal = ControlledRqWorker(
            queue, fake_graph_runner
        ).perform_enqueued_job()
        assert terminal.status is RunStatus.COMPLETED

        resumed = client.get(
            f"/api/advisor/committee/runs/{run_id}/events",
            headers={"Last-Event-ID": "1"},
        )
        assert resumed.status_code == 200
        assert "event: queued" not in resumed.text
        assert "event: analysts_completed" in resumed.text
        assert "event: completed" in resumed.text

        detail = client.get(f"/api/advisor/committee/runs/{run_id}")
        assert detail.status_code == 200
        assert detail.json()["run"]["status"] == "completed"
        assert {
            item["kind"] for item in detail.json()["artifacts"]
        } >= {
            "snapshot",
            "backtest_verdict",
            "risk_verdict",
            "final_decision",
        }

        preview = client.get(
            f"/api/advisor/committee/runs/{run_id}/order-preview"
        )
        assert preview.status_code == 200, preview.text
        plan = preview.json()["preview"]
        bound = client.post(
            f"/api/advisor/committee/runs/{run_id}/order-preview",
            json={
                "decision_hash": plan["decision_hash"],
                "account_version": plan["account_version"],
            },
        )
        assert bound.status_code == 200, bound.text
        approval_body = {
            "preview_id": bound.json()["preview_id"],
            "decision_hash": plan["decision_hash"],
            "proposal_hash": plan["proposal_hash"],
            "account_version": plan["account_version"],
            "confirm": True,
        }
        approved = client.post(
            f"/api/advisor/committee/runs/{run_id}/approve",
            headers={"Idempotency-Key": "approve-e2e"},
            json=approval_body,
        )
        replay = client.post(
            f"/api/advisor/committee/runs/{run_id}/approve",
            headers={"Idempotency-Key": "approve-e2e"},
            json=approval_body,
        )

        assert approved.status_code == 200, approved.text
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert approved.json()["approval"] == replay.json()["approval"]
        assert len(
            [
                trade
                for trade in database.paper_trades.docs
                if not trade.get("voided")
            ]
        ) == 1
    finally:
        test_app.dependency_overrides.clear()


def test_checkpoint_crash_repairs_mongo_and_auto_resumes_same_run(monkeypatch):
    database = CommitteeDatabase()
    repository = CommitteeRepository(database)
    redis = BytesRedis()
    settings = routes.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "1", "REDIS_HOST": "redis.invalid"}
    )
    runtime = CommitteeRuntime(settings, client=redis)
    now = datetime.now(timezone.utc)
    repository.create_run(
        CommitteeRun(
            user_id="u",
            run_id="crashed",
            status=RunStatus.RUNNING,
            strategy_version="v1",
            universe=("510300",),
            as_of=now - timedelta(minutes=10),
            created_at=now - timedelta(minutes=10),
            updated_at=now - timedelta(minutes=10),
            started_at=now - timedelta(minutes=10),
            queue_job_id="lost-job",
            job_heartbeat_at=now - timedelta(minutes=10),
            job_deadline_at=now - timedelta(minutes=1),
        )
    )
    checkpoint_state = {
        "snapshot": {
            "snapshot_id": "s" * 64,
            "items": [
                {
                    "name": "portfolio_account",
                    "content": {"account_version": 0},
                }
            ],
        },
        "final_decision": {"action": "hold"},
        "events": [
            {
                "event_id": "crashed:chair:done",
                "event_type": "chair_completed",
                "payload": {},
            }
        ],
        "status": "running",
    }
    reconcile_checkpoint_to_mongo(
        repository,
        runtime,
        "u",
        "crashed",
        checkpoint_state,
        attempt=1,
    )
    monkeypatch.setattr(
        "app.advisor.committee.reconcile.Job.fetch",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("lost")),
    )

    resumed = reconcile_stale_runs(
        repository=repository,
        connection=object(),
        now=now,
        checkpoint_exists=lambda user_id, run_id: True,
        enqueue_resume=lambda user_id, run_id, attempt: SimpleNamespace(
            id=f"resume-{attempt}"
        ),
    )

    run = repository.get_run("u", "crashed")
    detail = repository.get_detail("u", "crashed")
    assert resumed == ["crashed"]
    assert run.status is RunStatus.QUEUED
    assert run.resume_attempts == 1
    assert run.queue_job_id == "resume-1"
    assert {item["kind"] for item in detail.artifacts} >= {
        "snapshot",
        "final_decision",
    }
    assert any(
        event["event_type"] == "resume_enqueued"
        for event in detail.events
    )
