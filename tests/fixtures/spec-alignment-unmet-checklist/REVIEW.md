# Project review guidelines (fixture)

## Acceptance Criteria

- Every public API handler MUST call `logRequest(req)` as its first line.
- Every public API handler MUST have a unit test in `__tests__/handlers/`.

## Wiring Verification

- `src/handlers/getUser.ts` MUST contain `logRequest(req)`
