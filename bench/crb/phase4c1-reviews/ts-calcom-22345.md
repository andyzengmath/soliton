## Summary
2 files changed, 114 lines added, 231 lines deleted. 9 findings (6 critical, 3 improvements).
Top issue: `findMany()` removed with no compile-time guard, breaking all call sites across the monorepo.

## Critical

:red_circle: [testing] Caching logic entirely untested after removal of both caching describe blocks in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:1 (confidence: 95)
The entire `describe("Caching")` block containing tests for `cachedAuthConditions` and `cachedFilterConditions` was deleted. Production code still maintains these cached fields. The class-level invariant (memoization correctness) that was previously verified is now completely untested. Given the PR author has already disclosed that tests were never run, this gap means a memoization regression — stale cached `Prisma.Sql` across request lifetimes — would go entirely undetected.
```suggestion
describe("Caching", () => {
  it("should return the same Prisma.sql object on repeated calls to getAuthorizationConditions", async () => {
    const first = await service.getAuthorizationConditions();
    const second = await service.getAuthorizationConditions();
    expect(first).toBe(second);
  });
  it("should return the same Prisma.sql object on repeated calls to getFilterConditions", async () => {
    const first = await service.getFilterConditions();
    const second = await service.getFilterConditions();
    expect(first).toBe(second);
  });
});
```

:red_circle: [cross-file-impact] findMany() removed entirely — all call sites will fail to compile or crash at runtime in packages/trpc/server/routers/viewer/insights/:1 (confidence: 95)
The public `findMany()` method has been removed from `InsightsBookingService` and replaced with `getBaseConditions()`. Any file in the monorepo that calls `service.findMany(...)` will produce a TypeScript compile error. Given the number of insights tRPC procedures that historically delegated query execution to this method, the blast radius is high. The PR makes no mention of migrating call sites.
```suggestion
// Before merging, run:
//   grep -r "findMany" packages/trpc/server/routers/viewer/insights/ apps/web/
// For each call site, replace:
//   const results = await service.findMany(args)
// with:
const baseConditions = await service.getBaseConditions();
const results = await prisma.$queryRaw`
  SELECT ... FROM "BookingTimeStatusDenormalized"
  WHERE ${baseConditions}
`;
```

:red_circle: [cross-file-impact] Return type of getAuthorizationConditions and getFilterConditions changed from WhereInput to Prisma.Sql — callers will silently pass wrong type into Prisma ORM in packages/trpc/server/routers/viewer/insights/:1 (confidence: 92)
Both `getAuthorizationConditions()` (previously `Promise<BookingTimeStatusDenormalizedWhereInput>`) and `getFilterConditions()` (previously the same type or null) now return `Prisma.Sql` / `Prisma.Sql | null`. Any caller that passes these return values into a Prisma ORM `where:` clause will silently pass a `Prisma.Sql` tagged-template object where a plain object literal is expected. Depending on call-site TypeScript typing this may not surface as a compile error, making it a runtime data-corruption risk (queries matching everything or nothing).
```suggestion
// Audit all callers of both methods. Any usage in a Prisma ORM where: argument
// must be rewritten to use $queryRaw with the Prisma.Sql fragment directly.
// Consider a branded type to prevent this class of mistake:
//   type AuthSql = Prisma.Sql & { __brand: "AuthSql" };
```

:red_circle: [testing] PR author confirms tests were never executed — unverified test file committed in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:1 (confidence: 90)
The PR description states: "I encountered test runner configuration issues, so please verify the tests actually pass" and "manual verification of test execution is critical." The test file has been committed without the author confirming that it compiles, runs, or passes. Any syntax error, runtime type mismatch in the new `getBaseConditions` block, or broken import would go completely undetected. This is a hard blocker for merging an AI-authored PR touching authorization logic.
```suggestion
# Before merging, a human reviewer must run:
pnpm vitest run packages/lib/server/service/__tests__/insightsBooking.integration-test.ts
# and confirm a green pass — not a skip, not a timeout. Do not merge until this is verified.
```

:red_circle: [correctness] buildTeamAuthorizationCondition passes un-filtered nullable userId values into Prisma.sql ANY() — asymmetric with org scope in packages/lib/server/service/insightsBooking.ts:193 (confidence: 90)
In `buildTeamAuthorizationCondition`, membership `userId` values are mapped without null filtering: `const userIdsFromTeam = usersFromTeam.map((u) => u.userId);`. `Membership.userId` is typed as `Int?` (nullable) in the Prisma schema. The org scope equivalent (`buildOrgAuthorizationCondition`) explicitly guards against this with `.filter(u => u.userId !== null && typeof u.userId === "number")`. With raw `Prisma.sql\`ANY(${array})\``, an array containing null values causes Prisma to either throw a runtime type error constructing the typed array parameter or pass `ARRAY[1, 2, NULL]::int[]` to PostgreSQL — both are incorrect behaviors. This asymmetric null-handling regression was introduced by this PR: org scope filters nulls, team scope does not.
```suggestion
const userIdsFromTeam = usersFromTeam
  .filter((u): u is typeof u & { userId: number } => u.userId !== null && typeof u.userId === "number")
  .map((u) => u.userId);
```

:red_circle: [cross-file-impact] Constructor parameter type widened from discriminated union to flat optional type — team-scope callers without teamId silently return zero results in packages/trpc/server/routers/viewer/insights/:1 (confidence: 88)
The constructor parameter type was changed from the discriminated union `InsightsBookingServiceOptions` (which required `teamId` when `scope === "team"`) to the flat `InsightsBookingServicePublicOptions` (where `teamId` is optional regardless of scope). Internally, a `safeParse` sets `this.options = null` when the shape is invalid, causing every subsequent query to return the `NOTHING_CONDITION` (`1=0`) — a silent zero-result response rather than an error. Callers constructing the service from user-supplied query parameters where `teamId` might be absent in a team-scope context will silently serve empty dashboards to users, with no observable error.
```suggestion
// Restore the discriminated-union type on the public API so the compiler enforces
// that teamId is required when scope === "team":
export type InsightsBookingServicePublicOptions = InsightsBookingServiceOptions;

// OR, if the public type must stay flat, throw on failed parse instead of silently
// degrading:
const parsed = insightsBookingServiceOptionsSchema.safeParse(options);
if (!parsed.success) {
  throw new Error(`InsightsBookingService: invalid options — ${parsed.error.message}`);
}
this.options = parsed.data;
```

## Improvements

:yellow_circle: [correctness] getBaseConditions contains two permanently dead branches — misleading authorization contract in packages/lib/server/service/insightsBooking.ts:68 (confidence: 88)
`getAuthorizationConditions()` returns `Promise<Prisma.Sql>` and never returns null. The `else if (filterConditions)` and final `else` branches inside `getBaseConditions` are therefore unreachable dead code. This is more than a style concern: future maintainers may change `getAuthorizationConditions` to return `Prisma.Sql | null`, at which point the dead branch ordering could cause an authorization bypass (null auth condition falling through to return only `filterConditions`).
```suggestion
async getBaseConditions(): Promise<Prisma.Sql> {
  const authConditions = await this.getAuthorizationConditions();
  const filterConditions = await this.getFilterConditions();
  if (filterConditions) {
    return Prisma.sql`(${authConditions}) AND (${filterConditions})`;
  }
  return authConditions;
}
```

:yellow_circle: [testing] toEqual assertions on Prisma.Sql instances couple tests to internal SQL-builder representation, not SQL semantics in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:1 (confidence: 88)
Assertions use `.toEqual()` which performs deep structural equality on the internal `.strings` and `.values` arrays of `Prisma.Sql` objects. Any semantically equivalent refactor — reordering parentheses, adjusting whitespace, restructuring template literals — will break these tests even though the resulting SQL is identical. Tests are coupled to the implementation's internal builder representation rather than to observable query behavior.
```suggestion
// Replace structural equality with a behavioral assertion that executes the
// conditions against the test database and checks the result rows:
const rows = await prisma.$queryRaw<{ id: number }[]>`
  SELECT id FROM "BookingTimeStatusDenormalized" WHERE ${conditions}
`;
expect(rows.map(r => r.id)).toContain(expectedBooking.id);
expect(rows.map(r => r.id)).not.toContain(otherBooking.id);
```

:yellow_circle: [correctness] findMany() removal silently drops caller-supplied where clause merging — breaking contract with no compile-time guard in packages/lib/server/service/insightsBooking.ts:65 (confidence: 85)
The old `findMany()` accepted `findManyArgs` and merged caller-supplied `where` conditions with auth/filter conditions. The new `getBaseConditions()` returns only the auth+filter `Prisma.Sql` fragment with no mechanism for callers to compose additional predicates. Callers that previously passed `findManyArgs` with a populated `where` field now silently lose those filters. There is no API or type system enforcement to ensure the caller merges their predicates. Given this is AI-authored with unverified tests, this is precisely the kind of silent regression that escapes automated detection.
```suggestion
/**
 * Returns the base WHERE conditions (auth + filter) as a Prisma.Sql fragment.
 * Callers must compose any additional predicates manually:
 *   const base = await service.getBaseConditions();
 *   const rows = await prisma.$queryRaw`
 *     SELECT ... WHERE (${base}) AND "createdAt" > ${cutoff}
 *   `;
 */
async getBaseConditions(): Promise<Prisma.Sql> { ... }

// Optional combinator helper:
static combineConditions(...conditions: Prisma.Sql[]): Prisma.Sql {
  return conditions.reduce((acc, c) => Prisma.sql`(${acc}) AND (${c})`);
}
```

## Risk Metadata
Risk Score: 43/100 (MEDIUM) | Blast Radius: 50 (public method removal + constructor + return-type change, ~5 est. callers in trpc/insights, apps/web, features/insights) | Sensitive Paths: none hit (authorization logic lives outside a sensitive-path directory)
AI-Authored Likelihood: HIGH (branch `devin/convert-insights-booking-service-1752054886`, Devin session URL in PR body, `devin-ai-integration` bot comment, uniform mechanical Prisma.sql rewrite, author admits tests unverified)

(6 additional findings below confidence threshold)
