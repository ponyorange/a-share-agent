"""Product-offline checks: committee is not on the public surface."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.advisor.agent.tools import build_tools
from app.main import app


def test_public_app_does_not_mount_committee_routes():
    client = TestClient(app)
    assert client.get("/api/advisor/committee/health").status_code == 404
    assert client.get("/api/advisor/committee/runs").status_code == 404


def test_agent_exposes_no_committee_tools(monkeypatch):
    monkeypatch.setattr(
        "app.advisor.agent.tools.load_portfolio",
        lambda _uid: {"positions": []},
    )
    names = {item.name for item in build_tools("u")}
    assert not any("committee" in name for name in names)
