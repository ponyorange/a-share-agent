# Task 10 Report: Compatibility mapping + verification sweep

## Changes

- Added `regime.use_for_market_score: true` to `backend/app/advisor/config.yaml`.
- Added compatibility mapping in `backend/app/advisor/market_context.py` so `enrich_symbol_context` maps `gate_level` to `market_score` when enabled:
  - `aggressive` -> `0.8`
  - `normal` -> `0.55`
  - `defensive` -> `0.35`
  - `risk_off` -> `0.2`
- Added backend coverage for enabled mapping and opt-out legacy `market.score` behavior.
- Added a minimal RegimePage recent history strip using `fetchRegimeHistory(20)` to satisfy the "近 N 日周期" acceptance item.
- Extended RegimePage coverage for recent history display.

## Acceptance sweep

1. `/regime/current` returns dual-axis state, `gate_level`, `position_cap`, `evidence`: covered by existing route tests.
2. Daily archive write and two-day promotion input: covered by store/service/sentiment tests.
3. Sentiment detail fields: covered by sentiment and route tests.
4. `risk_off` blocks buys, override returns warnings: covered by gate and recommendation tool tests.
5. `defensive` shrink/threshold behavior with `position_cap`: covered by gate tests and config.
6. Agent `get_market_regime` and prompt rule coverage: covered by agent tool tests.
7. Missing sources produce `degraded`/`failed` and conservative behavior: covered by service tests.
8. Regime dashboard, limit-up sentiment strip, configurable matrix: dashboard history strip added; limit-up/topbar tests pass; matrix remains in config.
9. Unit coverage for synthesize, gate rewrite, degraded behavior, APIs/tools: full `test_regime_*.py` pass.

## Verification

- Backend: `cd backend && /Users/orange/Desktop/code/share-data/backend/.venv/bin/python -m pytest tests/test_regime_*.py -v`
  - Result: 37 passed, 2 warnings.
- Frontend: `cd frontend-advisor && npm test -- --run src/pages/RegimePage.test.tsx src/pages/LimitUpPage.test.tsx src/components/TopbarNav.test.tsx`
  - Result: 3 files passed, 12 tests passed.
- Lints: no diagnostics for edited backend/frontend files.

## Gaps

- No blocking gaps found against acceptance criteria 1-9.

## Quality follow-up (gate_level market_score mapping)

- Extended `tests/test_regime_market_context.py`:
  - Parametrized all four `gate_level` values → `market_score` (0.8 / 0.55 / 0.35 / 0.2).
  - When `market` lacks `gate_level`, `get_current_regime` → `risk_off` yields `market_score` 0.2.
  - When `get_current_regime` raises, falls back to legacy `market.score`.
- Re-run: `pytest tests/test_regime_market_context.py tests/test_regime_*.py -q` → **42 passed**, 2 warnings.
