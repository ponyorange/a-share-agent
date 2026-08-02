# Task 4 Report: Mongo Store + Collector Facade Inputs

## Status

Done.

## Implemented

- Added `backend/app/advisor/regime/store.py` with `upsert_daily`, `get_daily`, and `list_daily` over `market_regime_daily`.
- Added lazy unique index creation for `trade_date`.
- Added `backend/app/advisor/regime/collector.py` with `collect_raw` and injectable fetch callables for sealed, broken, limit-down, and trend features.
- Reused `app.limitup` normalization, merge, and ladder helpers.
- Added default trend feature wrapper using existing market and index MA helpers.

## Tests

- Added `backend/tests/test_regime_store.py`.
- Verified red before implementation for missing modules.
- Verified collector error aggregation and default trade date behavior.
- Verified default trend feature wrapper derives MA stack, drawdown, breadth, and volume ratio.

Command:

```bash
cd backend && /Users/orange/Desktop/code/share-data/backend/.venv/bin/python -m pytest tests/test_regime_store.py -v
```

Result: `5 passed, 1 warning`.

## Concerns

- The only warning is an existing `passlib`/Python 3.13 `crypt` deprecation warning during test startup.
