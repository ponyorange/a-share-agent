import importlib
from unittest.mock import Mock

import pytest


def _redis_module():
    return importlib.import_module("app.advisor.committee.redis_client")


def _jobs_module():
    return importlib.import_module("app.advisor.committee.jobs")


def _checkpoint_module():
    return importlib.import_module("app.advisor.committee.checkpoint")


def _worker_module():
    return importlib.import_module("app.advisor.committee.worker")


def test_disabled_by_default_and_import_has_no_redis_side_effect(monkeypatch):
    redis = pytest.importorskip("redis")
    ping_calls = []
    monkeypatch.setattr(redis.Redis, "ping", lambda self: ping_calls.append(self))

    module = _redis_module()
    settings = module.CommitteeRedisSettings.from_env({})

    assert settings.enabled is False
    assert module.health_check(settings) == {
        "enabled": False,
        "ok": False,
        "status": "disabled",
    }
    assert ping_calls == []


def test_disabled_committee_does_not_affect_legacy_health(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main
    from app.main import app

    redis_module = _redis_module()
    monkeypatch.setenv("COMMITTEE_ENABLED", "0")
    monkeypatch.setattr(main.providers, "list_sources", lambda: [])
    monkeypatch.setattr(main, "mongo_ping", lambda: {"ok": True})
    monkeypatch.setattr(
        redis_module,
        "create_client",
        lambda *_args, **_kwargs: pytest.fail("legacy health touched Redis"),
    )

    response = TestClient(app).get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_settings_parse_connection_and_queue_values():
    module = _redis_module()
    settings = module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "cache.internal",
            "REDIS_PORT": "6380",
            "REDIS_PASSWORD": "super-secret",
            "REDIS_SSL": "yes",
            "REDIS_DB": "4",
            "COMMITTEE_QUEUE_NAME": "advisor-committee",
            "COMMITTEE_JOB_TIMEOUT": "1200",
            "COMMITTEE_KEY_PREFIX": "share:data",
            "COMMITTEE_RESULT_TTL": "7200",
            "COMMITTEE_FAILURE_TTL": "172800",
            "COMMITTEE_LOCK_TTL": "180",
        }
    )

    assert settings.enabled is True
    assert settings.queue_name == "advisor-committee"
    assert settings.job_timeout == 1200
    assert settings.result_ttl == 7200
    assert settings.failure_ttl == 172800
    assert settings.lock_ttl == 180
    assert settings.connection_kwargs() == {
        "host": "cache.internal",
        "port": 6380,
        "db": 4,
        "password": "super-secret",
        "ssl": True,
        "decode_responses": False,
        "socket_connect_timeout": 2.0,
        "socket_timeout": 2.0,
    }
    assert settings.key("lock", "daily") == "share:data:lock:daily"


def test_password_is_redacted_from_repr_url_and_configuration_errors():
    module = _redis_module()
    secret = "pa ss/@word"
    settings = module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "1",
            "REDIS_HOST": "redis.example",
            "REDIS_PASSWORD": secret,
            "REDIS_SSL": "true",
        }
    )

    assert secret not in repr(settings)
    assert secret not in settings.safe_url
    assert settings.safe_url == "rediss://:***@redis.example:6379/0"
    pool = module.create_pool(settings)
    client = module.create_client(settings, pool=pool)
    assert secret not in repr(pool)
    assert secret not in repr(client)

    redis = pytest.importorskip("redis")
    assert issubclass(pool.connection_class, redis.SSLConnection)

    with pytest.raises(module.CommitteeConfigurationError) as exc_info:
        module.CommitteeRedisSettings.from_env(
            {
                "COMMITTEE_ENABLED": "true",
                "REDIS_HOST": "redis.example",
                "REDIS_PASSWORD": secret,
                "REDIS_PORT": "not-a-port",
            }
        )
    assert secret not in str(exc_info.value)


def test_enabled_configuration_requires_host_and_valid_ranges():
    module = _redis_module()

    with pytest.raises(module.CommitteeConfigurationError, match="REDIS_HOST"):
        module.CommitteeRedisSettings.from_env({"COMMITTEE_ENABLED": "true"})
    with pytest.raises(module.CommitteeConfigurationError, match="REDIS_DB"):
        module.CommitteeRedisSettings.from_env(
            {
                "COMMITTEE_ENABLED": "true",
                "REDIS_HOST": "redis.example",
                "REDIS_DB": "-1",
            }
        )


@pytest.mark.parametrize(
    "malicious_host",
    [
        "redis://user:top-secret@cache.internal/0",
        "user:top-secret@cache.internal",
        "cache.internal/path",
        "cache.internal?password=top-secret",
        "cache.internal#top-secret",
    ],
)
def test_redis_host_rejects_url_credentials_and_paths_without_echoing_input(
    malicious_host,
):
    module = _redis_module()

    with pytest.raises(module.CommitteeConfigurationError) as exc_info:
        module.CommitteeRedisSettings.from_env(
            {"COMMITTEE_ENABLED": "true", "REDIS_HOST": malicious_host}
        )

    assert "REDIS_HOST" in str(exc_info.value)
    assert malicious_host not in str(exc_info.value)
    assert "top-secret" not in str(exc_info.value)


def test_pool_client_lock_and_health_use_injected_fakes_without_network():
    module = _redis_module()
    settings = module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.example",
            "COMMITTEE_KEY_PREFIX": "committee",
            "COMMITTEE_LOCK_TTL": "45",
        }
    )
    pool = module.create_pool(settings)
    assert pool.connection_kwargs["host"] == "redis.example"

    class FakeRedis:
        def __init__(self):
            self.locks = []
            self.values = []

        def ping(self):
            return True

        def lock(self, name, timeout, blocking_timeout, thread_local=False):
            lock = (name, timeout, blocking_timeout)
            self.locks.append(lock)
            return lock

        def set(self, name, value, ex):
            self.values.append((name, value, ex))
            return True

    fake = FakeRedis()
    assert module.health_check(settings, client=fake) == {
        "enabled": True,
        "ok": True,
        "status": "ready",
        "endpoint": "redis://redis.example:6379/0",
    }
    assert module.distributed_lock("rebalance", settings, client=fake) == (
        "committee:lock:rebalance",
        45,
        0,
    )
    assert module.set_with_ttl("result", "job-1", b"done", settings, client=fake)
    assert fake.values == [("committee:result:job-1", b"done", 86400)]


def test_health_redacts_connection_exception_and_disabled_client_is_safe():
    module = _redis_module()
    jobs = _jobs_module()
    disabled = module.CommitteeRedisSettings.from_env({})
    with pytest.raises(module.CommitteeDisabledError):
        module.create_client(disabled)
    with pytest.raises(module.CommitteeDisabledError):
        jobs.create_queue(disabled, connection=object())

    enabled = module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.example",
            "REDIS_PASSWORD": "do-not-leak",
        }
    )

    class BrokenRedis:
        def ping(self):
            raise RuntimeError("AUTH failed for do-not-leak")

    health = module.health_check(enabled, client=BrokenRedis())
    assert health["ok"] is False
    assert health["status"] == "unavailable"
    assert health["error"] == "RuntimeError"
    assert "do-not-leak" not in repr(health)


def test_falsey_injected_client_is_used_without_falling_back(monkeypatch):
    module = _redis_module()
    settings = module.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "true", "REDIS_HOST": "redis.example"}
    )

    class FalseyRedis:
        def __bool__(self):
            return False

        def ping(self):
            return True

        def lock(self, name, timeout, blocking_timeout, thread_local=False):
            return name

        def set(self, name, value, ex):
            return True

    client = FalseyRedis()
    monkeypatch.setattr(
        module,
        "create_client",
        lambda *_args, **_kwargs: pytest.fail("injected client was ignored"),
    )

    assert module.health_check(settings, client=client)["ok"] is True
    assert module.distributed_lock("daily", settings, client=client).endswith(
        ":lock:daily"
    )
    assert module.set_with_ttl("result", "1", "ok", settings, client=client)


def test_queue_construction_uses_configured_name_timeout_and_connection():
    redis_module = _redis_module()
    jobs = _jobs_module()
    settings = redis_module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.example",
            "COMMITTEE_QUEUE_NAME": "committee-high",
            "COMMITTEE_JOB_TIMEOUT": "333",
        }
    )
    connection = object()

    queue = jobs.create_queue(settings, connection=connection)

    assert queue.name == "sharedata:committee:queue:committee-high"
    assert queue.key == "rq:queue:sharedata:committee:queue:committee-high"
    assert queue.connection is connection
    assert queue._default_timeout == 333


def test_enqueue_passes_rq_26_timeout_ttls_and_prefixed_job_id():
    redis_module = _redis_module()
    jobs = _jobs_module()
    settings = redis_module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.example",
            "COMMITTEE_JOB_TIMEOUT": "42",
            "COMMITTEE_RESULT_TTL": "600",
            "COMMITTEE_FAILURE_TTL": "1200",
            "COMMITTEE_KEY_PREFIX": "tenant:committee",
        }
    )

    class FalseyQueue:
        def __init__(self):
            self.call = None

        def __bool__(self):
            return False

        def enqueue_call(self, **kwargs):
            self.call = kwargs
            return "job"

    queue = FalseyQueue()
    job = jobs.enqueue("pkg.module.task", 1, name="demo", settings=settings, queue=queue)

    assert job == "job"
    assert queue.call["func"] == "pkg.module.task"
    assert queue.call["args"] == (1,)
    assert queue.call["kwargs"] == {"name": "demo"}
    assert queue.call["timeout"] == 42
    assert queue.call["result_ttl"] == 600
    assert queue.call["failure_ttl"] == 1200
    assert queue.call["job_id"].startswith("tenant:committee:job:")
    assert "job_timeout" not in queue.call


def test_worker_disabled_and_enabled_construction_are_testable():
    redis_module = _redis_module()
    worker = _worker_module()
    disabled = redis_module.CommitteeRedisSettings.from_env({})
    assert worker.run_worker(settings=disabled) == 0

    enabled = redis_module.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "true", "REDIS_HOST": "redis.example"}
    )
    client = object()
    queue = object()
    worker_instance = Mock()
    worker_class = Mock(return_value=worker_instance)

    assert (
        worker.run_worker(
            settings=enabled,
            client_factory=Mock(return_value=client),
            queue_factory=Mock(return_value=queue),
            worker_class=worker_class,
        )
        == 0
    )
    worker_class.assert_called_once_with([queue], connection=client)
    worker_instance.work.assert_called_once_with(with_scheduler=True)


def test_worker_configuration_and_runtime_errors_return_nonzero(monkeypatch):
    redis_module = _redis_module()
    worker = _worker_module()
    enabled = redis_module.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "true", "REDIS_HOST": "redis.example"}
    )
    monkeypatch.setattr(
        worker.CommitteeRedisSettings,
        "from_env",
        Mock(side_effect=redis_module.CommitteeConfigurationError("invalid")),
    )
    assert worker.run_worker() == 2

    worker_instance = Mock()
    worker_instance.work.side_effect = RuntimeError("worker failed")
    assert (
        worker.run_worker(
            settings=enabled,
            client_factory=Mock(return_value=object()),
            queue_factory=Mock(return_value=object()),
            worker_class=Mock(return_value=worker_instance),
        )
        == 1
    )


def test_worker_scheduler_parameter_is_forwarded():
    redis_module = _redis_module()
    worker = _worker_module()
    settings = redis_module.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "true", "REDIS_HOST": "redis.example"}
    )
    worker_instance = Mock()

    assert (
        worker.run_worker(
            settings=settings,
            client_factory=Mock(return_value=object()),
            queue_factory=Mock(return_value=object()),
            worker_class=Mock(return_value=worker_instance),
            with_scheduler=False,
        )
        == 0
    )
    worker_instance.work.assert_called_once_with(with_scheduler=False)


def test_ipv6_safe_url_uses_brackets():
    module = _redis_module()
    settings = module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "2001:db8::1",
            "REDIS_PORT": "6380",
        }
    )

    assert settings.safe_url == "redis://[2001:db8::1]:6380/0"


def test_checkpoint_factory_uses_shared_client_prefix_and_ttl():
    redis_module = _redis_module()
    checkpoint = _checkpoint_module()
    settings = redis_module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.example",
            "COMMITTEE_KEY_PREFIX": "tenant:committee",
            "COMMITTEE_RESULT_TTL": "7200",
        }
    )
    client = object()
    saver_class = Mock(return_value=object())

    saver = checkpoint.create_checkpoint_saver(
        settings, client=client, saver_class=saver_class
    )

    assert saver is saver_class.return_value
    saver_class.assert_called_once_with(
        redis_client=client,
        checkpoint_prefix="tenant:committee:checkpoint",
        checkpoint_write_prefix="tenant:committee:checkpoint-write",
        ttl={"default_ttl": 120, "refresh_on_read": False},
    )


def test_async_bridged_saver_normalizes_redis_channel_version_types():
    import asyncio
    from langgraph.checkpoint.base import CheckpointTuple

    checkpoint = _checkpoint_module()

    class SyncSaver:
        def get_tuple(self, config):
            return CheckpointTuple(
                config=config,
                checkpoint={
                    "v": 1,
                    "ts": "2026-07-23T00:00:00+00:00",
                    "id": "cp-1",
                    "channel_values": {},
                    "channel_versions": {
                        "branch:to:prepare": "3",
                        "status": "2",
                    },
                    "versions_seen": {
                        "prepare": {"branch:to:prepare": 2},
                        "fan_out": {"branch:to:fan_out": "4"},
                    },
                },
                metadata={"source": "update", "step": 1, "parents": {}},
                parent_config=None,
                pending_writes=None,
            )

        def put(self, config, checkpoint_value, metadata, new_versions):
            return {"put": config, "versions": new_versions}

        def put_writes(self, config, writes, task_id, task_path=""):
            return None

        def list(self, config, *, filter=None, before=None, limit=None):
            yield ("listed", config, filter, before, limit)

        def delete_thread(self, thread_id):
            return f"deleted:{thread_id}"

    bridged = checkpoint.AsyncBridgedRedisSaver(SyncSaver())

    async def exercise():
        loaded = await bridged.aget_tuple({"thread": 1})
        assert loaded is not None
        assert loaded.checkpoint["channel_versions"] == {
            "branch:to:prepare": 3,
            "status": 2,
        }
        assert loaded.checkpoint["versions_seen"] == {
            "prepare": {"branch:to:prepare": 2},
            "fan_out": {"branch:to:fan_out": 4},
        }
        assert (
            loaded.checkpoint["channel_versions"]["branch:to:prepare"]
            > loaded.checkpoint["versions_seen"]["prepare"]["branch:to:prepare"]
        )
        assert await bridged.aput({"c": 1}, {"cp": 1}, {"m": 1}, {"v": 1}) == {
            "put": {"c": 1},
            "versions": {"v": 1},
        }
        await bridged.aput_writes({"c": 1}, [("ch", 1)], "task")
        listed = [item async for item in bridged.alist({"c": 1}, filter={"k": 1})]
        assert listed == [("listed", {"c": 1}, {"k": 1}, None, None)]
        await bridged.adelete_thread("t1")

    asyncio.run(exercise())


def test_async_bridged_saver_implements_langgraph_async_apis():
    import asyncio

    checkpoint = _checkpoint_module()

    class SyncSaver:
        def get_tuple(self, config):
            return ("tuple", config)

        def put(self, config, checkpoint_value, metadata, new_versions):
            return {"put": config, "versions": new_versions}

        def put_writes(self, config, writes, task_id, task_path=""):
            return None

        def list(self, config, *, filter=None, before=None, limit=None):
            yield ("listed", config, filter, before, limit)

        def delete_thread(self, thread_id):
            return f"deleted:{thread_id}"

    bridged = checkpoint.AsyncBridgedRedisSaver(SyncSaver())

    async def exercise():
        assert await bridged.aget_tuple({"thread": 1}) == (
            "tuple",
            {"thread": 1},
        )
        assert await bridged.aput({"c": 1}, {"cp": 1}, {"m": 1}, {"v": 1}) == {
            "put": {"c": 1},
            "versions": {"v": 1},
        }
        await bridged.aput_writes({"c": 1}, [("ch", 1)], "task")
        listed = [item async for item in bridged.alist({"c": 1}, filter={"k": 1})]
        assert listed == [("listed", {"c": 1}, {"k": 1}, None, None)]
        await bridged.adelete_thread("t1")

    asyncio.run(exercise())


def test_checkpoint_setup_failure_is_diagnostic_and_non_throwing():
    redis_module = _redis_module()
    checkpoint = _checkpoint_module()
    settings = redis_module.CommitteeRedisSettings.from_env(
        {"COMMITTEE_ENABLED": "true", "REDIS_HOST": "redis.example"}
    )
    broken_saver = Mock()
    broken_saver.setup.side_effect = RuntimeError("AUTH top-secret")

    result = checkpoint.initialize_checkpoint_saver(
        settings, saver_factory=Mock(return_value=broken_saver)
    )

    assert result.saver is None
    assert result.ok is False
    assert result.status == "unavailable"
    assert result.error == "RuntimeError"
    assert "top-secret" not in repr(result)


def test_checkpoint_reports_missing_redis_stack_capability_explicitly():
    redis_module = _redis_module()
    checkpoint = _checkpoint_module()
    settings = redis_module.CommitteeRedisSettings.from_env(
        {
            "COMMITTEE_ENABLED": "true",
            "REDIS_HOST": "redis.example",
            "REDIS_PASSWORD": "do-not-leak",
        }
    )
    broken_saver = Mock()
    broken_saver.setup.side_effect = RuntimeError(
        "unknown command 'JSON.SET'; password=do-not-leak"
    )

    result = checkpoint.initialize_checkpoint_saver(
        settings, saver_factory=Mock(return_value=broken_saver)
    )

    assert result.ok is False
    assert result.status == "redis_stack_required"
    assert result.error == "RuntimeError"
    assert "do-not-leak" not in repr(result)
