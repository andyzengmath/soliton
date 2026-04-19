## Summary
2 files changed, 114 lines added, 231 lines deleted. 6 findings (3 critical, 3 improvements).
AI-generated (Devin) conversion of `InsightsBookingService` from Prisma `WhereInput` objects to raw `Prisma.sql` template literals; the logical SQL looks equivalent, but the public API is broken, regression tests were deleted, and the author explicitly did not run the tests.

## Critical

:red_circle: [cross-file-impact] `findMany` removed without updating any caller in `packages/lib/server/service/insightsBooking.ts`:68 (confidence: 90)
The public `findMany(findManyArgs)` method is replaced by `getBaseConditions()` which returns raw SQL, but this PR only edits the service and its integration test — every production caller of `InsightsBookingService#findMany` (insights dashboards / tRPC routers) will throw `TypeError: ... is not a function` at runtime. Either keep `findMany` as a thin wrapper that wraps `$queryRaw` around `getBaseConditions()`, or update every caller in the same PR.
```suggestion
  async findMany<T>(findManyArgs: Prisma.BookingTimeStatusDenormalizedFindManyArgs) {
    // Preserve prior API: callers may depend on this. Prefer getBaseConditions + $queryRaw
    // for new code; see InsightsRoutingService for the pattern.
    const baseConditions = await this.getBaseConditions();
    return this.prisma.$queryRaw<T[]>`
      SELECT * FROM "BookingTimeStatusDenormalized"
      WHERE ${baseConditions}
    `;
  }

  async getBaseConditions(): Promise<Prisma.Sql> {
```
<details><summary>More context</summary>

The diff touches only `packages/lib/server/service/insightsBooking.ts` and its sibling integration test. A service named `InsightsBookingService` almost certainly has external consumers (insights dashboard router, admin analytics endpoints, etc.). Grep the monorepo for `new InsightsBookingService(` and `insightsBookingService.findMany` before merging. If callers exist, they need to be migrated in this PR — a rename-only API break should not ship uncoordinated.
</details>

:red_circle: [testing] Caching regression tests deleted while caching logic is kept in `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts`:173 (confidence: 95)
The entire `describe("Caching", ...)` block that covered `cachedAuthConditions` and `cachedFilterConditions` is removed, but `insightsBooking.ts` still memoizes both in the `getAuthorizationConditions` / `getFilterConditions` methods. Memoization bugs (e.g., cache key collisions across scopes, stale cache across option mutations) will no longer be caught.
```suggestion
  describe("Caching", () => {
    it("should cache authorization conditions", async () => {
      const testData = await createTestData({
        teamRole: MembershipRole.OWNER,
        orgRole: MembershipRole.OWNER,
      });
      const service = new InsightsBookingService({
        prisma,
        options: { scope: "user", userId: testData.user.id, orgId: testData.org.id },
      });
      const conditions1 = await service.getAuthorizationConditions();
      expect(conditions1).toEqual(
        Prisma.sql`("userId" = ${testData.user.id}) AND ("teamId" IS NULL)`
      );
      const conditions2 = await service.getAuthorizationConditions();
      expect(conditions2).toEqual(conditions1);
      await testData.cleanup();
    });

    it("should cache filter conditions", async () => {
      const testData = await createTestData();
      const service = new InsightsBookingService({
        prisma,
        options: { scope: "user", userId: testData.user.id, orgId: testData.org.id },
        filters: { eventTypeId: testData.eventType.id },
      });
      const conditions1 = await service.getFilterConditions();
      expect(conditions1).toEqual(
        Prisma.sql`("eventTypeId" = ${testData.eventType.id}) OR ("eventParentId" = ${testData.eventType.id})`
      );
      const conditions2 = await service.getFilterConditions();
      expect(conditions2).toEqual(conditions1);
      await testData.cleanup();
    });
  });
```

:red_circle: [testing] Tests were never executed by the author (confidence: 90)
The PR description states verbatim: *"I encountered test runner configuration issues, so please verify the tests actually pass."* The new `expect(conditions).toEqual(Prisma.sql\`...\`)` assertions rely on exact structural equality of the `Prisma.Sql` internals — the service builds them via nested `reduce` calls while the tests build them as a single monolithic template literal, so the `strings`/`values` arrays may not be identical even when the emitted SQL is semantically equivalent.
```suggestion
// Before merging, run locally and paste the output:
//   TZ=UTC yarn test packages/lib/server/service/__tests__/insightsBooking.integration-test.ts
// If toEqual fails on structural comparison, switch the assertion style to compare the rendered
// SQL and parameter array, e.g.:
//   expect(conditions.sql).toBe(expected.sql);
//   expect(conditions.values).toEqual(expected.values);
```
<details><summary>More context</summary>

`Prisma.Sql` implements a custom `text`/`sql`/`values` shape; `.toEqual` on two instances is a deep structural match. When the service does `Prisma.sql\`(${acc}) OR (${cond})\`` recursively, the resulting `strings` array has different fragmentation than the test's single-template baseline, even though the serialized SQL text ends up the same. This was almost certainly the test runner "configuration issue" the author saw — it is a real test failure, not an environment bug.
</details>

## Improvements

:yellow_circle: [correctness] Unreachable branches in `getBaseConditions` in `packages/lib/server/service/insightsBooking.ts`:68 (confidence: 90)
`getAuthorizationConditions()` is typed `Promise<Prisma.Sql>` and always returns a truthy `Prisma.Sql` (even `NOTHING_CONDITION`), so the `else if (filterConditions)` and `else return NOTHING_CONDITION` branches are dead. Simplify to a single ternary so future readers don't misread the auth-less path as reachable.
```suggestion
  async getBaseConditions(): Promise<Prisma.Sql> {
    const authConditions = await this.getAuthorizationConditions();
    const filterConditions = await this.getFilterConditions();

    return filterConditions
      ? Prisma.sql`(${authConditions}) AND (${filterConditions})`
      : authConditions;
  }
```

:yellow_circle: [correctness] Dead `index === 0` guard inside `reduce` in `packages/lib/server/service/insightsBooking.ts`:104 (confidence: 95)
`Array.prototype.reduce` invoked without an `initialValue` starts with `acc = arr[0]` and `index = 1`, so `if (index === 0) return condition;` can never fire; for single-element arrays the callback is never called at all. The guard is confusing dead code.
```suggestion
    return conditions.reduce((acc, condition) => Prisma.sql`(${acc}) AND (${condition})`);
```
<details><summary>More context</summary>

Same pattern appears in three places: `buildFilterConditions`, `buildOrgAuthorizationCondition`, and `buildTeamAuthorizationCondition`. All three can drop the `index === 0` check. Consider factoring a tiny helper:

```ts
const joinSql = (parts: Prisma.Sql[], sep: "AND" | "OR") =>
  parts.reduce((acc, p) => Prisma.sql`(${acc}) ${Prisma.raw(sep)} (${p})`);
```
</details>

:yellow_circle: [consistency] `InsightsBookingServicePublicOptions` loses discriminated-union safety in `packages/lib/server/service/insightsBooking.ts`:29 (confidence: 85)
The new public constructor type allows `{ scope: "team", userId, orgId }` with `teamId` optional, so callers can compile an invalid payload that only fails at runtime via `zod.safeParse`. Use a discriminated union (or re-export the existing `InsightsBookingServiceOptions` inferred from the zod schema) so the type system catches the missing-`teamId` case.
```suggestion
export type InsightsBookingServicePublicOptions =
  | { scope: "user"; userId: number; orgId: number }
  | { scope: "org"; userId: number; orgId: number }
  | { scope: "team"; userId: number; orgId: number; teamId: number };
```

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: single service + tests, but `findMany` is a public method likely consumed by insights routers across the monorepo | Sensitive Paths: authorization + raw SQL construction
AI-Authored Likelihood: HIGH (Devin session linked in PR body; idiomatic tells — unused `reduce` index guard, unreachable truthy-check branches, loose public-options type, and author-admitted lack of local test verification)
