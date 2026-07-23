from __future__ import annotations

import asyncio
import time

import pytest

import app.advisor.committee.agents as agents
from app.advisor.committee.state import BudgetLimits


def analyst_body():
    return {
        "chat_message": "技术面观点",
        "thesis": "结论",
        "confidence": 0.5,
        "evidence_ids": [],
        "symbols": [],
    }


def execute(executor, **overrides):
    values = {
        "user_id": "u",
        "run_id": "r",
        "role": "technical",
        "prompt": "frozen prompt",
        "model_tier": "quick",
        "timeout_seconds": 0.2,
        "deadline_at": time.time() + 1,
        "idempotency_key": "r:technical",
    }
    values.update(overrides)
    return executor.aexecute(**values)


@pytest.mark.parametrize(
    "schema",
    [
        agents.AnalystOutput,
        agents.DebateOutput,
        agents.TraderOutput,
        agents.ChairOutput,
    ],
)
def test_role_schema_requires_chat_message(schema):
    assert schema.model_fields["chat_message"].is_required()


def test_each_retry_attempt_reserves_budget_and_failed_attempt_is_audited():
    class InvalidRunner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(content={"bad": "shape"}, model_name="fake")

    async def scenario():
        runner = InvalidRunner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=1,
                max_tokens=100,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        with pytest.raises(agents.RoleBudgetError) as caught:
            await execute(executor)
        assert runner.calls == 1
        assert caught.value.usage.calls == 1
        assert len(caught.value.records) == 1
        assert caught.value.records[0].status == "invalid"
        assert caught.value.records[0].error

    asyncio.run(scenario())


def test_parallel_attempt_budget_reservation_is_atomic():
    class Runner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            await asyncio.sleep(0)
            return agents.ModelResponse(content=analyst_body(), model_name="fake")

    async def scenario():
        runner = Runner()
        ledger = agents.BudgetLedger()
        executor = agents.RoleAgentExecutor(runner, budget=ledger)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=2,
                max_tokens=100,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        results = await asyncio.gather(
            *(execute(executor, idempotency_key=f"r:technical:{i}") for i in range(4)),
            return_exceptions=True,
        )
        assert runner.calls == 2
        assert sum(isinstance(item, agents.RoleBudgetError) for item in results) == 2

    asyncio.run(scenario())


def test_sync_role_runner_is_rejected_before_side_effect():
    side_effects = []

    class SyncRunner:
        def __call__(self, request):
            side_effects.append("executed")
            return agents.ModelResponse(content=analyst_body(), model_name="fake")

    async def scenario():
        executor = agents.RoleAgentExecutor(SyncRunner())
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 5)
        with pytest.raises(TypeError, match="async coroutine"):
            await execute(executor)
        assert side_effects == []

    asyncio.run(scenario())


def test_async_timeout_cancels_runner_without_late_side_effect():
    class SlowRunner:
        cancelled = False
        late_effect = False

        async def __call__(self, request):
            try:
                await asyncio.sleep(0.05)
                self.late_effect = True
                return agents.ModelResponse(content=analyst_body(), model_name="fake")
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def scenario():
        runner = SlowRunner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 1)
        with pytest.raises(agents.RoleTimeoutError) as caught:
            await execute(executor, timeout_seconds=0.01)
        await asyncio.sleep(0.06)
        assert runner.cancelled is True
        assert runner.late_effect is False
        assert caught.value.records[0].status == "timeout"
        assert caught.value.usage.elapsed_seconds < 1

    asyncio.run(scenario())


def test_total_deadline_cancels_async_runner():
    class SlowRunner:
        cancelled = False
        late_effect = False

        async def __call__(self, request):
            try:
                await asyncio.sleep(0.05)
                self.late_effect = True
                return agents.ModelResponse(content=analyst_body(), model_name="fake")
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def scenario():
        runner = SlowRunner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 0.01)
        with pytest.raises(agents.RoleTimeoutError):
            await execute(
                executor,
                timeout_seconds=1,
                deadline_at=time.time() + 0.01,
            )
        await asyncio.sleep(0.06)
        assert runner.cancelled is True
        assert runner.late_effect is False

    asyncio.run(scenario())


def test_external_cancellation_settles_reservation_fail_closed():
    class Runner:
        started = asyncio.Event()

        async def __call__(self, request):
            self.started.set()
            await asyncio.sleep(10)

    async def scenario():
        runner = Runner()
        ledger = agents.BudgetLedger()
        executor = agents.RoleAgentExecutor(runner, budget=ledger)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=1,
                max_tokens=100,
                node_timeout_seconds=30,
                total_timeout_seconds=30,
            ),
            deadline_at=time.time() + 30,
        )
        task = asyncio.create_task(
            execute(executor, prompt="p", timeout_seconds=30)
        )
        await runner.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        usage = ledger.usage("u", "r")
        assert usage.tokens == 100
        assert usage.reserved_tokens == 0

    asyncio.run(scenario())


def test_role_result_cache_prevents_duplicate_external_call():
    class Runner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(
                content=analyst_body(),
                model_name="fake",
                input_tokens=0,
                output_tokens=0,
            )

    async def scenario():
        runner = Runner()
        ledger = agents.BudgetLedger()
        executor = agents.RoleAgentExecutor(runner, budget=ledger)
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 5)
        first = await execute(executor)
        second = await execute(executor)
        assert runner.calls == 1
        assert first.output == second.output
        assert second.records[0].cached is True

    asyncio.run(scenario())


def test_role_result_cache_is_scoped_by_user():
    class Runner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(content=analyst_body(), model_name="fake")

    async def scenario():
        runner = Runner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run("u1", "r", BudgetLimits(), deadline_at=time.time() + 5)
        executor.begin_run("u2", "r", BudgetLimits(), deadline_at=time.time() + 5)
        await execute(executor, user_id="u1")
        await execute(executor, user_id="u2")
        assert runner.calls == 2

    asyncio.run(scenario())


def test_role_result_cache_is_scoped_by_business_attempt():
    class Runner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(
                content=analyst_body(),
                model_name="fake",
                input_tokens=0,
                output_tokens=0,
            )

    async def scenario():
        runner = Runner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 5)
        first = await execute(executor, attempt=1)
        second = await execute(executor, attempt=2)
        assert runner.calls == 2
        assert first.output == second.output

    asyncio.run(scenario())


def test_format_retry_reuses_message_id_and_increments_generation():
    requests = []

    class Runner:
        async def __call__(self, request):
            requests.append(request)
            body = analyst_body()
            if len(requests) == 1:
                body.pop("confidence")
            return agents.ModelResponse(
                content=body,
                model_name="fake",
                input_tokens=0,
                output_tokens=0,
            )

    async def scenario():
        executor = agents.RoleAgentExecutor(Runner())
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 5)
        result = await execute(executor)
        assert result.output.chat_message == "技术面观点"
        assert [row.message_id for row in requests] == [
            requests[0].message_id,
            requests[0].message_id,
        ]
        assert [row.generation for row in requests] == [1, 2]

    asyncio.run(scenario())


def test_chat_runner_uses_json_mode_and_response_metadata_token_fallback(monkeypatch):
    captured = {}

    class Message:
        content = '{"thesis":"x","confidence":0.5,"evidence_ids":[],"symbols":[]}'
        usage_metadata = None
        response_metadata = {
            "model_name": "quick",
            "token_usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }

    class Bound:
        async def ainvoke(self, messages):
            return Message()

    class Model:
        model_name = "quick"

        def bind(self, **kwargs):
            captured.update(kwargs)
            return Bound()

    monkeypatch.setattr(agents, "build_chat_model", lambda *args, **kwargs: Model())
    runner = agents.ChatModelRoleRunner()
    request = agents.RoleRequest(
        user_id="u",
        run_id="r",
        role="technical",
        prompt="prompt",
        output_schema=agents.AnalystOutput,
        model_tier="quick",
        idempotency_key="r:technical:attempt:1",
        timeout_seconds=1,
        deadline_at=time.time() + 1,
        max_output_tokens=9,
        message_id="message",
        generation=1,
        round_index=None,
        attempt=1,
    )
    response = asyncio.run(runner(request))
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["max_tokens"] == 9
    assert response.input_tokens == 7
    assert response.output_tokens == 3
    assert response.token_usage_known is True


def test_unknown_token_usage_is_explicit():
    response = agents.ModelResponse(content=analyst_body(), model_name="fake")
    assert response.input_tokens is None
    assert response.output_tokens is None
    assert response.token_usage_known is False


def test_unknown_token_usage_consumes_remaining_budget_and_blocks_retry():
    class InvalidUnknownUsage:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(content={"bad": "shape"}, model_name="fake")

    async def scenario():
        runner = InvalidUnknownUsage()
        executor = agents.RoleAgentExecutor(runner)
        budget = agents.estimate_input_tokens("frozen prompt") + 1
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=2,
                max_tokens=budget,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        with pytest.raises(agents.RoleBudgetError) as caught:
            await execute(executor)
        assert runner.calls == 1
        assert caught.value.usage.tokens == budget
        assert caught.value.usage.unknown_token_calls == 1
        assert len(caught.value.records) == 1

    asyncio.run(scenario())


def test_response_that_exceeds_token_budget_is_audited_and_stops():
    estimate = agents.estimate_input_tokens("frozen prompt")

    class Runner:
        async def __call__(self, request):
            return agents.ModelResponse(
                content=analyst_body(),
                model_name="fake",
                input_tokens=estimate + 2,
                output_tokens=2,
            )

    async def scenario():
        runner = Runner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=2,
                max_tokens=estimate + 3,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        with pytest.raises(agents.RoleBudgetError) as caught:
            await execute(executor)
        assert caught.value.usage.tokens == estimate + 3
        assert caught.value.records[0].status == "error"
        assert "token" in caught.value.records[0].error
        with pytest.raises(agents.RoleBudgetError):
            await execute(executor)

    asyncio.run(scenario())


def test_budget_ledger_restores_checkpoint_usage_before_reserving():
    class Runner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(content=analyst_body(), model_name="fake")

    async def scenario():
        runner = Runner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=1,
                max_tokens=100,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
            initial_usage=agents.BudgetUsage(calls=1),
        )
        with pytest.raises(agents.RoleBudgetError):
            await execute(executor)
        assert runner.calls == 0

    asyncio.run(scenario())


def test_wrong_runner_response_type_is_audited_as_failed_attempt():
    class Runner:
        async def __call__(self, request):
            return {"not": "a ModelResponse"}

    async def scenario():
        executor = agents.RoleAgentExecutor(Runner())
        executor.begin_run("u", "r", BudgetLimits(), deadline_at=time.time() + 5)
        with pytest.raises(agents.RoleExecutionError) as caught:
            await execute(executor)
        assert caught.value.usage.calls == 1
        assert caught.value.records[0].status == "error"
        assert "ModelResponse" in caught.value.records[0].error

    asyncio.run(scenario())


def test_runner_exception_with_unknown_usage_exhausts_token_budget():
    class Runner:
        async def __call__(self, request):
            raise RuntimeError("failed")

    async def scenario():
        executor = agents.RoleAgentExecutor(Runner())
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=2,
                max_tokens=50,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        with pytest.raises(agents.RoleExecutionError) as caught:
            await execute(executor)
        assert caught.value.usage.tokens == 50
        assert caught.value.usage.unknown_token_calls == 1

    asyncio.run(scenario())


def test_input_token_estimate_is_reserved_before_model_call():
    prompt = "abc中国"
    estimate = agents.estimate_input_tokens(prompt)

    class Runner:
        calls = 0
        max_output_tokens = None

        async def __call__(self, request):
            self.calls += 1
            self.max_output_tokens = request.max_output_tokens
            return agents.ModelResponse(
                content=analyst_body(),
                model_name="fake",
                input_tokens=estimate,
                output_tokens=1,
            )

    async def scenario():
        blocked = Runner()
        blocked_executor = agents.RoleAgentExecutor(blocked)
        blocked_executor.begin_run(
            "u",
            "blocked",
            BudgetLimits(
                max_calls=1,
                max_tokens=estimate,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        with pytest.raises(agents.RoleBudgetError):
            await execute(
                blocked_executor,
                run_id="blocked",
                prompt=prompt,
            )
        assert blocked.calls == 0

        runner = Runner()
        executor = agents.RoleAgentExecutor(runner)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=1,
                max_tokens=estimate + 5,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        result = await execute(executor, prompt=prompt)
        assert runner.calls == 1
        assert runner.max_output_tokens == 5
        assert result.usage.tokens == estimate + 1

    asyncio.run(scenario())


def test_unknown_usage_stays_fail_closed_after_concurrent_known_completion():
    ledger = agents.BudgetLedger()
    limits = BudgetLimits(
        max_calls=2,
        max_tokens=100,
        node_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    ledger.begin("u", "r", limits, time.time() + 5)
    unknown = ledger.reserve_attempt(
        "u",
        "r",
        reservation_id="unknown",
        estimated_input_tokens=20,
    )
    known = ledger.reserve_attempt(
        "u",
        "r",
        reservation_id="known",
        estimated_input_tokens=20,
    )
    ledger.account(
        "u",
        "r",
        input_tokens=None,
        output_tokens=None,
        token_usage_known=False,
        elapsed_seconds=0,
        reservation_id=unknown.reservation_id,
    )
    usage = ledger.account(
        "u",
        "r",
        input_tokens=2,
        output_tokens=1,
        token_usage_known=True,
        elapsed_seconds=0,
        reservation_id=known.reservation_id,
    )
    assert usage.tokens == limits.max_tokens


def test_four_parallel_analysts_atomically_reserve_output_quota():
    class Runner:
        active = 0
        max_active = 0
        actual_tokens = 0
        ready = asyncio.Event()

        async def __call__(self, request):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 4:
                self.ready.set()
            await asyncio.wait_for(self.ready.wait(), timeout=0.2)
            input_tokens = agents.estimate_input_tokens(request.prompt)
            output_tokens = request.max_output_tokens
            self.actual_tokens += input_tokens + output_tokens
            self.active -= 1
            return agents.ModelResponse(
                content={
                    "chat_message": f"{request.role}观点",
                    "thesis": request.role,
                    "confidence": 0.5,
                    "evidence_ids": [],
                    "symbols": [],
                },
                model_name="fake",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

    async def scenario():
        runner = Runner()
        ledger = agents.BudgetLedger()
        executor = agents.RoleAgentExecutor(runner, budget=ledger)
        executor.begin_run(
            "u",
            "r",
            BudgetLimits(
                max_calls=20,
                max_tokens=200,
                node_timeout_seconds=1,
                total_timeout_seconds=5,
            ),
            deadline_at=time.time() + 5,
        )
        roles = ("fundamental", "technical", "news", "quant")
        await asyncio.gather(
            *(
                execute(
                    executor,
                    role=role,
                    prompt="p",
                    idempotency_key=f"r:{role}",
                )
                for role in roles
            )
        )
        assert runner.max_active == 4
        assert runner.actual_tokens <= 200
        assert ledger.usage("u", "r").tokens == runner.actual_tokens

    asyncio.run(scenario())


def test_real_executor_restores_checkpoint_budget_before_next_agent():
    class Runner:
        calls = 0

        async def __call__(self, request):
            self.calls += 1
            return agents.ModelResponse(
                content=analyst_body(),
                model_name="fake",
                input_tokens=agents.estimate_input_tokens(request.prompt),
                output_tokens=1,
            )

    async def scenario():
        limits = BudgetLimits(
            max_calls=2,
            max_tokens=100,
            node_timeout_seconds=1,
            total_timeout_seconds=30,
        )
        first_runner = Runner()
        first = agents.RoleAgentExecutor(first_runner)
        first.ensure_run(
            "u",
            "r",
            limits,
            deadline_at=time.time() + 30,
        )
        completed = await execute(first, role="technical")

        resumed_runner = Runner()
        resumed = agents.RoleAgentExecutor(resumed_runner)
        resumed.ensure_run(
            "u",
            "r",
            limits,
            deadline_at=time.time() + 30,
            initial_usage=completed.usage,
        )
        await execute(
            resumed,
            role="fundamental",
            idempotency_key="r:fundamental",
        )
        with pytest.raises(agents.RoleBudgetError):
            await execute(
                resumed,
                role="news",
                idempotency_key="r:news",
            )
        assert resumed_runner.calls == 1
        assert resumed._budget.usage("u", "r").calls == 2

    asyncio.run(scenario())


def test_budget_ledger_restores_outstanding_checkpoint_reservation():
    limits = BudgetLimits(
        max_calls=2,
        max_tokens=100,
        node_timeout_seconds=1,
        total_timeout_seconds=30,
    )
    original = agents.BudgetLedger()
    original.begin("u", "r", limits, time.time() + 30)
    reserved = original.reserve_attempt(
        "u",
        "r",
        reservation_id="r:technical:attempt:1",
        estimated_input_tokens=20,
    )
    checkpoint = original.usage("u", "r")

    restored = agents.BudgetLedger()
    restored.begin(
        "u",
        "r",
        limits,
        time.time() + 30,
        initial_usage=checkpoint,
    )
    replay = restored.reserve_attempt(
        "u",
        "r",
        reservation_id="r:technical:attempt:1",
        estimated_input_tokens=20,
    )

    assert replay.input_tokens == reserved.input_tokens
    assert replay.max_output_tokens == reserved.max_output_tokens
    assert replay.usage.calls == 1
