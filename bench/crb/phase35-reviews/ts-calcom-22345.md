## Summary
2 files changed, 114 lines added, 231 lines deleted. 7 findings (3 critical, 4 improvements).
Conversion of `InsightsBookingService` from Prisma `WhereInput` object notation to raw `Prisma.sql` fragments is largely mechanical and safely parameterized, but introduces a silent breaking API change (`findMany` removed), deletes the Caching test suite despite the cache still being present in production code, and ships with the author's own admission that the tests were never executed.

## Critical

:red_circle: [cross-file-impact] Removal of `InsightsBookingService.findMany` is a silent breaking change with no caller migration in `packages/lib/server/service/insightsBooking.ts`:65 (confidence: 95)
The public method `async findMany(findManyArgs: Prisma.BookingTimeStatusDenormalizedFindManyArgs)` is deleted and replaced with `getBaseConditions(): Promise<Prisma.Sql>`. Any consumer of this service that calls `service.findMany(...)` — which, based on the original `findMany` signature accepting a full `FindManyArgs`, is the intended primary entry point — will fail to compile (if TypeScript is strict) or fail at runtime with `TypeError: service.findMany is not a function`. The PR's diff touches only the service and its test file; there is no corresponding update to any callers in routers, tRPC procedures, Insights page handlers, or analytics jobs. Before merging, every call site of `InsightsBookingService.findMany` must be migrated to the new `getBaseConditions()` + `$queryRaw` pattern, OR `findMany` must be kept as a thin wrapper around `getBaseConditions` to preserve backward compatibility.
```suggestion
  async findMany<T>(
    query: (baseConditions: Prisma.Sql) => Promise<T>
  ): Promise<T> {
    const baseConditions = await this.getBaseConditions();
    return query(baseConditions);
  }

  async getBaseConditions(): Promise<Prisma.Sql> {
    const authConditions = await this.getAuthorizationConditions();
    const filterConditions = await this.getFilterConditions();
    // ...existing body...
  }
```
[References: https://www.prisma.io/docs/orm/prisma-client/using-raw-sql/raw-queries]

:red_circle: [testing] Caching `describe` block is deleted but the cache fields `cachedAuthConditions` / `cachedFilterConditions` remain in production in `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts`:173 (confidence: 92)
Lines 173–232 of the old test file — the entire `describe("Caching", ...)` block asserting that `getAuthorizationConditions()` and `getFilterConditions()` memoise their results on second invocation — are removed. However `insightsBooking.ts` still declares `private cachedAuthConditions?: Prisma.Sql;` and `private cachedFilterConditions?: Prisma.Sql | null;` and still has the early-return memoisation logic. This is a coverage regression on a non-trivial invariant: if a future refactor breaks caching (e.g., moves state to a per-call local or inadvertently re-invokes the expensive `isOrgOwnerOrAdmin` + `teamRepo.findAllByParentId` queries on every call), no test will catch it, yet the Insights pages fire this service on hot paths. Either restore the caching tests (they are trivially portable — only the expected shape changes from the object to the new `Prisma.sql` fragment), or remove the dead `cachedAuthConditions` / `cachedFilterConditions` state from the service.
```suggestion
    it("should cache authorization conditions", async () => {
      const testData = await createTestData({
        teamRole: MembershipRole.OWNER,
        orgRole: MembershipRole.OWNER,
      });

      const service = new InsightsBookingService({
        prisma,
        options: {
          scope: "user",
          userId: testData.user.id,
          orgId: testData.org.id,
        },
      });

      const conditions1 = await service.getAuthorizationConditions();
      const conditions2 = await service.getAuthorizationConditions();
      expect(conditions2).toBe(conditions1); // identity, not deep-equality — proves memoisation

      await testData.cleanup();
    });
```

:red_circle: [correctness] Author states integration tests were never executed in PR description (confidence: 90)
The PR body explicitly says: "I encountered test runner configuration issues, so please verify the tests actually pass." For a change that rewrites SQL authorization conditions (a well-known source of privilege-escalation bugs) and converts structural assertions (`toEqual({AND: [...]})`) into string-equality assertions against `Prisma.sql` template instances, the probability that at least one `toEqual` comparison fails at runtime is non-trivial — `Prisma.Sql` instances may compare by reference, by `{strings, values}` deep equality, or by `.sql` getter depending on the Prisma client version. Before merging, the committer MUST run `TZ=UTC yarn test packages/lib/server/service/__tests__/insightsBooking.integration-test.ts` and attach the passing output to the PR. A PR that admits its tests weren't run should not be merged on pattern-similarity with `InsightsRoutingService` alone.

## Improvements

:yellow_circle: [correctness] `toEqual` on `Prisma.Sql` instances is a fragile assertion in `packages/lib/server/service/__tests__/insightsBooking.integration-test.ts`:210 (confidence: 78)
Every new assertion of the form `expect(conditions).toEqual(Prisma.sql\`...\`)` relies on Vitest performing a deep-equality walk across the internal `{strings: string[], values: unknown[]}` shape of the tagged-template result. This is an undocumented shape that Prisma can (and has, in past majors) reorganised — e.g., flattening nested `Sql` values or interning the strings array. Prefer asserting on the stable public surface: compare `conditions.sql` (the rendered placeholder string) and `conditions.values` (the parameter array) separately. This is both more robust to Prisma upgrades and produces far more legible diff output on failure.
```suggestion
      const conditions = await service.getAuthorizationConditions();
      const expected = Prisma.sql`("userId" = ${testData.user.id}) AND ("teamId" IS NULL)`;
      expect(conditions.sql).toEqual(expected.sql);
      expect(conditions.values).toEqual(expected.values);
```

:yellow_circle: [correctness] `getBaseConditions` returns `NOTHING_CONDITION` in a fallthrough that is effectively unreachable, masking future bugs in `packages/lib/server/service/insightsBooking.ts`:69 (confidence: 72)
The new `getBaseConditions()` branches on `if (authConditions && filterConditions) ... else if (authConditions) ... else if (filterConditions) ... else return NOTHING_CONDITION`. But `getAuthorizationConditions()` never returns null/undefined — at worst it returns `NOTHING_CONDITION` (which is a truthy `Prisma.Sql` instance). So the third `else if (filterConditions)` and the final `else` are both dead code on the happy path; the only way to reach them is if `getAuthorizationConditions` is later refactored to return nullable. The truthiness check `if (authConditions && filterConditions)` also silently treats a `1=0` auth condition as "valid" and ANDs it with the filter, producing `(1=0) AND (...)` — semantically correct (empty result) but wastes a round-trip to the DB. Consider making auth unconditional and only branching on filter: `return filterConditions ? Prisma.sql\`(${authConditions}) AND (${filterConditions})\` : authConditions;`.
```suggestion
  async getBaseConditions(): Promise<Prisma.Sql> {
    const authConditions = await this.getAuthorizationConditions();
    const filterConditions = await this.getFilterConditions();
    return filterConditions
      ? Prisma.sql`(${authConditions}) AND (${filterConditions})`
      : authConditions;
  }
```

:yellow_circle: [consistency] `InsightsBookingServicePublicOptions` diverges from the Zod discriminated union and can accept runtime-invalid inputs in `packages/lib/server/service/insightsBooking.ts`:29 (confidence: 70)
The new public type declares `orgId: number` as required for all scopes, including `user`, whereas the internal `insightsBookingServiceOptionsSchema` is a `z.discriminatedUnion("scope", ...)` where the `user`-scope variant may not require `orgId`. Because the constructor runs `insightsBookingServiceOptionsSchema.safeParse(options)` but silently tolerates parse failures (the field is then stored as `null`), callers passing TS-valid but Zod-invalid inputs will get a service instance whose `this.options` is `null`, causing every `getAuthorizationConditions` call to return `NOTHING_CONDITION` with no observable error. Either make the public type a true mirror of the discriminated union (e.g., `{ scope: "user"; userId: number; orgId?: number } | { scope: "team"; teamId: number; ... }`), or have the constructor `throw` on parse failure instead of coercing to `null`.

:yellow_circle: [consistency] Unrelated rewrite of `isOrgOwnerOrAdmin` role check creates PR-scope creep in `packages/lib/server/service/insightsBooking.ts`:216 (confidence: 65)
The change from `([MembershipRole.OWNER, MembershipRole.ADMIN] as const).includes(membership.role)` to `(membership.role === MembershipRole.OWNER || membership.role === MembershipRole.ADMIN)` has no behavioural difference and is unrelated to the Prisma.sql conversion described in the PR title. Unrelated refactors bloat the reviewable surface area of an already risky auth change. Prefer shipping this in a separate micro-PR.

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: 2 files touched, but the removed `findMany` is likely called from Insights routers and analytics consumers outside the diff | Sensitive Paths: `server/service/` (authorization logic — owner/admin/member privilege gates)
AI-Authored Likelihood: HIGH (PR body identifies Devin session a5e216ec6c36...; author explicitly flags unrun tests, a common AI-authored PR failure mode)
