from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_packaging_produces_worker_compose_without_committee(tmp_path):
    package_root = tmp_path / "project"
    scripts = package_root / "scripts"
    deploy = package_root / "deploy"
    runner = package_root / "sandbox" / "runner"
    controller = package_root / "sandbox" / "controller"
    scripts.mkdir(parents=True)
    deploy.mkdir()
    runner.mkdir(parents=True)
    controller.mkdir(parents=True)

    shutil.copy2(ROOT / "scripts/package-docker.sh", scripts / "package-docker.sh")
    for name in ("Dockerfile", ".env.example", "README.md"):
        shutil.copy2(ROOT / "deploy" / name, deploy / name)
    shutil.copy2(ROOT / "sandbox/runner/Dockerfile", runner / "Dockerfile")
    shutil.copy2(ROOT / "sandbox/controller/Dockerfile", controller / "Dockerfile")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$DOCKER_CALL_LOG\"\n"
        "if [[ \"$1 $2\" == \"image inspect\" ]]; then exit 0; fi\n"
        "if [[ \"$1\" == \"save\" ]]; then printf 'fake-image:%s' \"$2\"; exit 0; fi\n"
        "exit 1\n"
    )
    docker.chmod(0o755)
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DOCKER_CALL_LOG": str(tmp_path / "docker-calls.log"),
        "IMAGE_TAG": "test-review",
    }

    subprocess.run(
        [str(scripts / "package-docker.sh"), "--skip-build"],
        cwd=package_root,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    dist = package_root / "dist"
    assert (dist / "share-data-test-review.tar.gz").exists()
    assert (dist / "share-data-sandbox-controller-test-review.tar.gz").exists()
    assert (dist / "share-data-python-sandbox-test-review.tar.gz").exists()

    docker_calls = (tmp_path / "docker-calls.log").read_text()
    assert "image inspect share-data:test-review" in docker_calls
    assert "image inspect share-data-sandbox-controller:test-review" in docker_calls
    assert "image inspect share-data-python-sandbox:test-review" in docker_calls
    assert "save share-data:test-review" in docker_calls
    assert "save share-data-sandbox-controller:test-review" in docker_calls
    assert "save share-data-python-sandbox:test-review" in docker_calls

    compose = yaml.safe_load((dist / "docker-compose.yml").read_text())
    services = compose["services"]
    assert services["share-data"]["image"] == "share-data:test-review"
    assert services["share-data"]["environment"] == [
        "STATIC_ROOT=/app/static",
        "PORT=8000",
        "CORS_ORIGINS=*",
        "SANDBOX_URL=http://sandbox-controller:8090",
        "SANDBOX_TOKEN=${SANDBOX_TOKEN:?SANDBOX_TOKEN must be set to at least 32 bytes}",
    ]
    assert "volumes" not in services["share-data"]
    assert services["share-data"]["depends_on"] == {
        "sandbox-controller": {"condition": "service_healthy"}
    }
    assert "committee-worker" not in services
    assert "sandbox-runner" not in services
    assert "redis" not in services
    assert services["monitor-worker"]["image"] == "share-data:test-review"

    controller = services["sandbox-controller"]
    assert controller["image"] == "share-data-sandbox-controller:test-review"
    assert "env_file" not in controller
    assert controller["environment"] == [
        "SANDBOX_TOKEN=${SANDBOX_TOKEN:?SANDBOX_TOKEN must be set to at least 32 bytes}",
        "SANDBOX_RUNNER_IMAGE=share-data-python-sandbox:test-review",
    ]
    assert controller["volumes"] == ["/var/run/docker.sock:/var/run/docker.sock"]
    assert controller["expose"] == ["8090"]
    assert "ports" not in controller

    env_template = (dist / ".env.example").read_text()
    for name in (
        "MONGODB_URI",
        "JWT_SECRET",
        "LLM_ENCRYPTION_KEY",
        "SANDBOX_TOKEN",
    ):
        assert f"{name}=" in env_template
    assert "COMMITTEE_ENABLED" not in env_template
    assert "COMMITTEE_QUEUE_NAME" not in env_template
    assert "redis:" not in services

    release_readme = (dist / "README.md").read_text()
    assert "share-data-sandbox-controller-<tag>.tar.gz" in release_readme
    assert "share-data-python-sandbox-<tag>.tar.gz" in release_readme
    assert "SANDBOX_TOKEN" in release_readme
    assert "/api/advisor/committee/health" not in release_readme
    assert "committee-worker" not in release_readme
    assert "COMMITTEE_ENABLED" not in release_readme
    assert "轮换" in release_readme


def test_worker_module_command_is_importable():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.advisor.committee.worker",
        ],
        cwd=ROOT / "backend",
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_local_env_files_are_excluded_and_release_templates_have_no_secrets():
    gitignore = (ROOT / ".gitignore").read_text()
    dockerignore = (ROOT / ".dockerignore").read_text()
    package_script = (ROOT / "scripts/package-docker.sh").read_text()

    assert ".env" in gitignore
    assert "**/.env" in dockerignore
    assert "backend/.env" not in package_script
    for template in (
        ROOT / "backend/.env.example",
        ROOT / "deploy/.env.example",
    ):
        values = {
            key: value
            for line in template.read_text().splitlines()
            if "=" in line and not line.lstrip().startswith("#")
            for key, value in [line.split("=", 1)]
        }
        for name in (
            "MONGODB_URI",
            "JWT_SECRET",
            "LLM_ENCRYPTION_KEY",
        ):
            assert values[name] == ""
        assert "REDIS_HOST" not in values
        assert "COMMITTEE_ENABLED" not in values
        if template.name == ".env.example" and template.parent.name == "deploy":
            assert values["SANDBOX_TOKEN"] == ""
