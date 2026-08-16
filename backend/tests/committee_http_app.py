"""Leftover committee HTTP surface for tests that still exercise stored code."""

from fastapi import APIRouter, FastAPI

from app.advisor.committee.routes import router as committee_router


def leftover_committee_app() -> FastAPI:
    test_app = FastAPI()
    parent = APIRouter(prefix="/api/advisor")
    parent.include_router(committee_router)
    test_app.include_router(parent)
    return test_app
