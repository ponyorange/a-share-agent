#!/usr/bin/env python3
"""Run a historical SignalGraph backfill over advisor recommendation pools.

Example:
  cd backend && .venv/bin/python ../scripts/run_signal_graph_backfill.py --days 60
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser(description="SignalGraph universe backfill")
    parser.add_argument("--days", type=int, default=60, help="trading days to replay")
    parser.add_argument("--end", type=str, default="", help="end trade date YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=8, help="prefetch workers")
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="do not clear existing graph before backfill",
    )
    parser.add_argument(
        "--boards",
        type=str,
        default="etf,hs,star",
        help="comma boards: etf,hs,star",
    )
    args = parser.parse_args()

    from app.advisor.signal_graph.backfill import run_universe_backfill

    boards = tuple(b.strip() for b in args.boards.split(",") if b.strip())

    def on_progress(ev: dict) -> None:
        phase = ev.get("phase")
        if phase == "universe":
            print(
                f"[universe] symbols={ev.get('symbols')} days={ev.get('trade_days')} "
                f"{ev.get('start')} → {ev.get('end')}",
                flush=True,
            )
        elif phase == "prefetch":
            print(
                f"[prefetch] {ev.get('done')}/{ev.get('total')} "
                f"ok={ev.get('ok')} err={ev.get('errors')}",
                flush=True,
            )
        elif phase == "replay":
            print(
                f"[replay] {ev.get('day_index')}/{ev.get('day_total')} "
                f"{ev.get('trade_date')} regime={ev.get('market_regime')} "
                f"signals={ev.get('signal_count')} settled={ev.get('settle_count')} "
                f"pending={ev.get('pending')} edges={ev.get('edges')}",
                flush=True,
            )

    result = run_universe_backfill(
        days=args.days,
        end_date=args.end or None,
        boards=boards,  # type: ignore[arg-type]
        reset=not args.no_reset,
        max_workers=args.workers,
        on_progress=on_progress,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
