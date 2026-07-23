"""Short-term trading advisor (rules + backtest)."""

from .routes import router
from .committee.routes import router as committee_router

router.include_router(committee_router)

__all__ = ["router"]
