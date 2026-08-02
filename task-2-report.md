# Task 2 report — override_allowed CTA fix

## Finding
Important: `RegimePage` showed「仍要看今日关注」for all `risk_off` states, ignoring API `override_allowed`.

## Fix
- Restored gate: `gateLevel === 'risk_off' && data.override_allowed` for override CTA.
- When `risk_off` and `override_allowed === false`, no primary/ghost CTA in btn-row (no override path).

## Tests
- Added: `hides override CTA when risk_off but override_allowed is false`.
- `npm test -- --run src/pages/RegimePage.test.tsx` → **5 passed**.

## Commit
`fix(advisor-ui): respect override_allowed on 今日闸门 CTA`
