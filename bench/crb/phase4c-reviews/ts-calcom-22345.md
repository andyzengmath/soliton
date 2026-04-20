## Summary
2 files changed, 114 lines added, 231 lines deleted. 7 findings (3 critical, 4 improvements, 0 nitpicks).
Converts `InsightsBookingService` from Prisma `WhereInput` objects to `Prisma.sql` raw template literals; the mechanical rewrite is safe, but it removes a public `findMany()` method, deletes caching test coverage, and widens the constructor's public options type in a way that silently swallows misconfiguration.

## Critical

:red_circle: [cross-file-impact] Breaking removal of public `findMany()` method in packages/lib/server/service/insightsBooking.ts:68 (confidence: 90)
`InsightsBookingService.findMany(findManyArgs)` is a public, exported method that has been deleted and replaced with `getBaseConditions(): Promise<Prisma.Sql>`. Every external caller of `findMany` must now handcraft a `$queryRaw` block, supply its own `SELECT`/`ORDER BY`/pagination, and lose Prisma's typed select inference. The PR description mentions no migration of callers, and the integration test was itself rewritten to use `$queryRaw` — suggesting callers elsewhere in the monorepo have not been updated. Either keep `findMany` as a thin wrapper around `getBaseConditions` or update all callers in the same PR so the type-check fails loudly instead of shipping a runtime break.
```suggestion
async findMany<T extends { id: number }>(): Promise<T[]> {
  const baseConditions = await this.getBaseConditions();
  return this.prisma.$queryRaw<T[]>`SELECT * FROM "BookingTimeStatusDenormalized" WHERE ${baseConditions}`;
}

async getBaseConditions(): Promise<Prisma.Sql> { /* unchanged */ }
```

:red_circle: [correctness] Public options type silently accepts invalid shapes and returns empty results in packages/lib/server/service/insightsBooking.ts:29 (confidence: 85)
The new `InsightsBookingServicePublicOptions = { scope: "user" | "org" | "team"; userId: number; orgId: number; teamId?: number }` is a flat object that loses the Zod `discriminatedUnion("scope")` guarantee. Callers can now pass `scope: "team"` without `teamId`, or `scope: "user"` with a `teamId`. The constructor uses `safeParse` and, on failure, sets `this.options = null`, which causes every subsequent method to return `NOTHING_CONDITION` (`1=0`) — so a misconfigured caller silently receives "zero rows" instead of a compile-time or runtime error. This is the same hazard pattern that originally motivated the Zod discriminated union. Either export the inferred `InsightsBookingServiceOptions` as the public type, or throw on `safeParse` failure so misconfiguration surfaces immediately.
```suggestion
constructor({ prisma, options, filters }: {
  prisma: typeof readonlyPrisma;
  options: InsightsBookingServiceOptions; // use the Zod-narrowed type publicly
  filters?: InsightsBookingServiceFilterOptions;
}) {
  this.prisma = prisma;
  const result = insightsBookingServiceOptionsSchema.safeParse(options);
  if (!result.success) {
    throw new Error(`Invalid InsightsBookingService options: ${result.error.message}`);
  }
  this.options = result.data;
  this.filters = filters;
}
```

:red_circle: [testing] Caching implementation retained but both caching tests deleted in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:469 (confidence: 95)
The two "should cache authorization conditions" and "should cache filter conditions" tests (≈80 lines) are removed wholesale, but the service still carries `cachedAuthConditions` / `cachedFilterConditions` and the `if (undefined) build(); return cached` cache guard in `getAuthorizationConditions` / `getFilterConditions`. That leaves a live feature (memoization) with zero test coverage — any future refactor that accidentally disables caching (for example, always recomputing) will pass CI. Restore the caching tests, rewritten against the new `Prisma.Sql` return type (reference equality `toBe` is the cleanest check for memoization).
```suggestion
it("should cache authorization conditions", async () => {
  const testData = await createTestData({ teamRole: MembershipRole.OWNER, orgRole: MembershipRole.OWNER });
  const service = new InsightsBookingService({
    prisma,
    options: { scope: "user", userId: testData.user.id, orgId: testData.org.id },
  });
  const first = await service.getAuthorizationConditions();
  const second = await service.getAuthorizationConditions();
  expect(second).toBe(first); // reference equality proves the cache returned the same Sql
  await testData.cleanup();
});
```

## Improvements

:yellow_circle: [correctness] Unreachable branches in `getBaseConditions` in packages/lib/server/service/insightsBooking.ts:68 (confidence: 85)
`getAuthorizationConditions()` returns `Promise<Prisma.Sql>` (never null — it returns `NOTHING_CONDITION` on the sad paths). That means the `else if (filterConditions)` and final `else { return NOTHING_CONDITION; }` branches of `getBaseConditions` are unreachable: `authConditions` is always truthy. Flattening the logic removes dead code and stops future readers from reasoning about a branch that cannot fire.
```suggestion
async getBaseConditions(): Promise<Prisma.Sql> {
  const authConditions = await this.getAuthorizationConditions();
  const filterConditions = await this.getFilterConditions();
  return filterConditions
    ? Prisma.sql`(${authConditions}) AND (${filterConditions})`
    : authConditions;
}
```

:yellow_circle: [testing] PR author explicitly states tests were not executed locally in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:1 (confidence: 100)
The PR description says: "I encountered test runner configuration issues, so please verify the tests actually pass." Given this PR rewrites authorization-boundary SQL and replaces `findMany` with raw `$queryRaw`, landing it without any local test run is risky. Require at minimum a local run of `TZ=UTC yarn test packages/lib/server/service/__tests__/insightsBooking.integration-test.ts` and a green CI job on the new `$queryRaw` integration test before merge — the existing "LGTM" reviews do not appear to have reproduced the run either.
```suggestion
// No code change — gating condition: block merge until CI shows a green
// run of insightsBooking.integration-test.ts on PostgreSQL, since local
// execution was skipped by the author.
```

:yellow_circle: [correctness] `Prisma.Sql` equality via `toEqual` is a fragile assertion in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:210 (confidence: 70)
`expect(conditions).toEqual(Prisma.sql\`...\`)` relies on Vitest deep-equaling the private `.strings` / `.values` arrays of two `Prisma.Sql` instances. This works today but has two footguns: (1) `.strings` is a `TemplateStringsArray` with a `raw` property that `toEqual` may or may not compare depending on Vitest version, and (2) minor shifts in how Prisma composes nested `Prisma.sql` fragments (e.g. extra parentheses or whitespace differences from `join`/`raw` helpers) would produce structurally-equivalent SQL that fails `toEqual`. Prefer a small helper that compares `{ strings: [...sql.strings], values: sql.values }` and use it consistently — or assert on the rendered SQL string via a test-only `sqlToString(sql)` helper.
```suggestion
function toSqlShape(sql: Prisma.Sql) {
  return { strings: [...sql.strings], values: sql.values };
}
// ...
expect(toSqlShape(conditions)).toEqual(
  toSqlShape(Prisma.sql`("userId" = ${testData.user.id}) AND ("teamId" IS NULL)`)
);
```

:yellow_circle: [correctness] `conditions.reduce(...)` without an initial value throws on empty arrays in packages/lib/server/service/insightsBooking.ts:109 (confidence: 65)
Three places (`buildFilterConditions`, `buildOrgAuthorizationCondition`, `buildTeamAuthorizationCondition`) now do `conditions.reduce((acc, condition, index) => index === 0 ? condition : Prisma.sql`(${acc}) AND/OR (${condition})`)`. Each call-site is currently guarded (length check in `buildFilterConditions`, seeded with one element in the other two), so this is safe today — but the pattern is brittle. Prisma ships `Prisma.join(fragments, ' AND ')` which is explicit, handles empty arrays (returns an empty fragment), and communicates intent far better than `reduce` with a magic `index === 0`. Switching removes an easy-to-introduce regression if a future edit removes the seed/guard.
```suggestion
import { Prisma } from "@prisma/client";
// ...
return conditions.length > 1
  ? Prisma.sql`(${Prisma.join(conditions, ") AND (")})`
  : conditions[0];
// or, for the OR combinators, Prisma.join(conditions, ") OR (")
```

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: 2 files, 1 core authorization service touched, unknown external callers of `findMany` in monorepo | Sensitive Paths: server/service authorization + SQL composition
AI-Authored Likelihood: HIGH (Devin AI session, PR author = devin-ai-integration, test runner skipped per PR body)
