# PR Review — calcom/cal.com#22345

**Title:** feat: convert InsightsBookingService to use Prisma.sql raw queries
**State:** MERGED (base: `main`, head: `devin/convert-insights-booking-service-1752054886`)
**Author-surface:** Devin AI session (per PR description footer)
**Files:** 2 — `packages/lib/server/service/insightsBooking.ts` (+73 −82), `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts` (+41 −149)

## Summary
2 files changed, 114 lines added, 231 lines deleted. 8 findings (1 critical, 4 improvements, 3 nitpicks).
Refactor swaps Prisma WhereInput objects for `Prisma.sql` fragments and removes `findMany`. The authorization/SQL generation is parameterized safely, but the public API is narrowed, two caching regression tests are deleted, and the runtime discriminated-union guarantee is weakened.

## Critical

:red_circle: [cross-file-impact] Breaking API removal: `InsightsBookingService.findMany` deleted in `packages/lib/server/service/insightsBooking.ts:L60 (post-diff area ~65)` (confidence: 95)
`findMany(findManyArgs: Prisma.BookingTimeStatusDenormalizedFindManyArgs)` is removed and replaced with `getBaseConditions(): Promise<Prisma.Sql>`. Callers must now issue their own `$queryRaw` against `"BookingTimeStatusDenormalized"` and serialize results themselves. The PR does not update any callers in this diff, nor does the PR body list consumer updates. Upstream tsc/eslint CI may still have flagged this, but any in-tree consumer of `service.findMany(...)` is a compile-time break; any runtime consumer that was using the old `Prisma.BookingTimeStatusDenormalizedFindManyArgs`-shaped API loses the Prisma result-shape contract (`select`, `include`, relation hydration, BigInt→number coercion, Date parsing). Switching from `findMany` to raw `$queryRaw` silently drops Prisma's automatic type transformation — every downstream call site now has to re-specify column casts and row typing.
```suggestion
// Either (a) keep a thin findMany wrapper that calls getBaseConditions() +
// $queryRaw and re-shapes rows via z.parse(), or (b) ship caller migrations
// in the same PR so the graph stays green. Example wrapper preserving the
// public surface:
async findMany<T>(args: {
  select: (keyof BookingTimeStatusDenormalized)[];
}): Promise<T[]> {
  const where = await this.getBaseConditions();
  const cols = Prisma.join(args.select.map((c) => Prisma.raw(`"${String(c)}"`)));
  return this.prisma.$queryRaw<T[]>`
    SELECT ${cols} FROM "BookingTimeStatusDenormalized" WHERE ${where}
  `;
}
```
References: Prisma raw query docs — https://www.prisma.io/docs/orm/prisma-client/queries/raw-database-access/raw-queries

## Improvements

:yellow_circle: [correctness] Unreachable branches in `getBaseConditions` in `packages/lib/server/service/insightsBooking.ts:~68-82` (confidence: 90)
`getAuthorizationConditions()` is typed `Promise<Prisma.Sql>` and every branch of `buildAuthorizationConditions` returns either a real `Prisma.sql` or `NOTHING_CONDITION`. A `Prisma.Sql` is always a truthy object, so `authConditions && filterConditions` collapses to just `filterConditions`, and the `else if (filterConditions)` and final `else { return NOTHING_CONDITION }` branches are unreachable. The unreachable tail also hides an arguable semantic bug: if you ever did reach "no auth, no filter", returning `NOTHING_CONDITION` (`1=0`) silently returns zero rows instead of signaling a programming error. Either delete the dead branches or make `getAuthorizationConditions` nullable and explicit.
```suggestion
async getBaseConditions(): Promise<Prisma.Sql> {
  const authConditions = await this.getAuthorizationConditions(); // always non-null
  const filterConditions = await this.getFilterConditions();
  return filterConditions
    ? Prisma.sql`(${authConditions}) AND (${filterConditions})`
    : authConditions;
}
```

:yellow_circle: [consistency] Public constructor type widens discriminated union in `packages/lib/server/service/insightsBooking.ts:~29-57` (confidence: 85)
`InsightsBookingServicePublicOptions` is a plain object type (`scope: "user" | "org" | "team"`, `teamId?: number`). The runtime `insightsBookingServiceOptionsSchema` is a `z.discriminatedUnion("scope", …)` where the `team` variant *requires* `teamId`. By typing the constructor argument as `InsightsBookingServicePublicOptions`, TS now accepts `new InsightsBookingService({ prisma, options: { scope: "team", userId, orgId } })` — the zod parse will silently fail, set `this.options = null`, and `buildAuthorizationConditions` will return `NOTHING_CONDITION`, giving callers "empty results" instead of a compile-time or 400-level error. Prefer the discriminated union at the compile boundary too.
```suggestion
export type InsightsBookingServicePublicOptions =
  | { scope: "user"; userId: number; orgId: number }
  | { scope: "org"; userId: number; orgId: number }
  | { scope: "team"; userId: number; orgId: number; teamId: number };
```

:yellow_circle: [testing] Caching regression coverage deleted in `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:~174-243 (removed block)` (confidence: 90)
The two "should cache authorization conditions" / "should cache filter conditions" tests are dropped entirely, yet `cachedAuthConditions` / `cachedFilterConditions` still exist on the service. The caching is now un-tested and silently regressable. Because `Prisma.sql` values contain parameter arrays that can be mutated, a cache-miss regression would also change query plans at runtime. Restore equivalent tests, adapted to `Prisma.Sql` equality (e.g., compare `.strings`/`.values` or reference-equality on the cached object).
```suggestion
it("should cache authorization conditions", async () => {
  const testData = await createTestData({ teamRole: MembershipRole.OWNER, orgRole: MembershipRole.OWNER });
  const service = new InsightsBookingService({
    prisma,
    options: { scope: "user", userId: testData.user.id, orgId: testData.org.id },
  });
  const a = await service.getAuthorizationConditions();
  const b = await service.getAuthorizationConditions();
  expect(b).toBe(a); // identity, proving cache
  await testData.cleanup();
});
```

:yellow_circle: [correctness] Team-scope behavior change when `userIdsFromTeam` is empty in `packages/lib/server/service/insightsBooking.ts:~185-207` (confidence: 70)
Before: the team OR-clause *always* included `{ userId: { in: userIdsFromTeam }, isTeamBooking: false }`, even when the array was empty. After: the clause is gated on `userIdsFromTeam.length > 0`. For Postgres this produces `ANY('{}'::int[])` vs omitting the branch — functionally both return no rows, but `reduce` collapses a single-element array to just the team-booking clause, so the resulting SQL shape is now `("teamId" = $1) AND ("isTeamBooking" = true)` with no trailing `OR … isTeamBooking=false` wrapper. That is arguably the *more* correct shape (avoids `= ANY('{}')`), but it is a behavioral change that is not covered by any new test case — add a regression test for the empty-team-members path.

## Nitpicks

:white_circle: [consistency] `reduce` nesting produces deeply parenthesized SQL in `packages/lib/server/service/insightsBooking.ts:~98-102, ~167-171, ~199-203` (confidence: 75)
The `.reduce((acc, c, i) => i === 0 ? c : Prisma.sql\`(${acc}) AND (${c})\`)` idiom is repeated three times and generates `((a) AND (b)) AND (c)` style SQL. `Prisma.join(conditions, ' AND ')` (with pre-wrapped parens per fragment, or `Prisma.join(conditions.map((c) => Prisma.sql\`(${c})\`), ' AND ')`) is flatter and avoids the quadratic nesting growth.

:white_circle: [consistency] Duplicated `NOTHING_CONDITION` constant in `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:~10` and `packages/lib/server/service/insightsBooking.ts:~36` (confidence: 80)
Both files declare the same `const NOTHING_CONDITION = Prisma.sql\`1=0\`;`. Export it from the service module and import it in the test to prevent drift (e.g., if one side ever becomes `Prisma.sql\`FALSE\`` or adds a schema-qualifier, the other silently breaks `toEqual`).

:white_circle: [historical-context] Hardcoded table name in integration test in `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:~479-483` (confidence: 60)
`SELECT id FROM "BookingTimeStatusDenormalized" WHERE ${baseConditions}` duplicates the table name that Prisma previously resolved via the model. If a migration renames the view (e.g., versioned denormalized views are common in this repo area), the test breaks at runtime instead of at type-check. Consider a small helper that reads the table name from the Prisma DMMF, or document the coupling.

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: any caller of `InsightsBookingService.findMany` (removed); raw-SQL fragments now flow to `$queryRaw` sites; auth-logic path for org/team scopes rewritten | Sensitive Paths: `packages/lib/server/service/insightsBooking.ts` (authorization + SQL construction), tests for same
AI-Authored Likelihood: HIGH (PR body explicitly attributes authorship to a Devin AI session; "I encountered test runner configuration issues, so please verify the tests actually pass" is a classic AI-authored disclaimer; removal of caching tests without replacement is a common LLM-scope-drift pattern)

---

### Reviewer notes (out-of-band, not posted to PR)
- SQL injection surface was the single most important thing to check. Every `${…}` inside `Prisma.sql` is a parameter placeholder (including arrays via `= ANY(${arr})`), and nested `Prisma.Sql` values are stitched in as sub-SQL with their own parameters preserved. No injection vector was introduced by this PR.
- `MembershipRole` comparison simplification (`.includes(role)` → `role === OWNER || role === ADMIN`) is behavior-equivalent; the `as const` on the original tuple had been there to make `.includes()` narrow, which is no longer needed.
- The PR is MERGED and approved by `hbjORbj` ("LGTM!", with an earlier "non-blocking code nits" review). `cubic-dev-ai` reported "3 issues across 2 files" (contents not accessible from the CLI metadata). `delve-auditor` reported no security issues.
- `mfeuerstein` left a prior external-bot "approved" review on 2026-04-10. This benchmark review is independent and reaches a different verdict (HIGH risk, 1 critical) primarily because of the removed `findMany` surface, which purely-local file review cannot clear without also inspecting consumers.

### Metadata
- totalAgents: 0 dispatched (inline multi-lens review; budget-aware)
- completedAgents: 7 lenses applied inline (security, correctness, hallucination, cross-file-impact, consistency, test-quality, historical-context)
- failedAgents: none
- reviewDurationMs: 161874
- recommendation: request-changes (breaking API removal + deleted caching tests)
