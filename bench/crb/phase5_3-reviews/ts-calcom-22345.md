## Summary
2 files changed, 114 lines added, 231 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
Conversion of `InsightsBookingService` authorization/filter logic from Prisma object WHERE clauses to raw `Prisma.sql` template literals removes a public API method, deletes caching tests, and ships under the author's own admission that the integration tests were never executed locally — review carefully on a security-sensitive path.

## Critical
:red_circle: [cross-file-impact] Public API method `findMany` removed with no migration in this PR in packages/lib/server/service/insightsBooking.ts:60 (confidence: 88)
The previous public method `findMany(findManyArgs: Prisma.BookingTimeStatusDenormalizedFindManyArgs)` is deleted and replaced by `getBaseConditions(): Promise<Prisma.Sql>`, which only returns a WHERE fragment. Any caller outside this PR that previously invoked `service.findMany({ select: {...} })` will fail to compile (method missing) and at runtime will need to switch to `prisma.$queryRaw\`SELECT ... WHERE ${baseConditions}\``. The diff updates only the integration-test caller; production callers (e.g. `apps/web` insights routes, tRPC routers, dashboard handlers) that consumed `BookingTimeStatusDenormalized` rows via `findMany` are not touched here. Confirm that no other module imports `InsightsBookingService` and calls `findMany`, or stage a follow-up PR before merging this one.
```suggestion
// Either keep findMany as a thin wrapper around getBaseConditions for back-compat:
async findMany(findManyArgs: { select?: Prisma.BookingTimeStatusDenormalizedSelect }) {
  const where = await this.getBaseConditions();
  return this.prisma.$queryRaw`
    SELECT ${Prisma.raw(Object.keys(findManyArgs.select ?? { id: true }).map(c => `"${c}"`).join(", "))}
    FROM "BookingTimeStatusDenormalized"
    WHERE ${where}
  `;
}
// ...or update every caller in this same PR.
```

## Improvements
:yellow_circle: [testing] Caching tests deleted but caching code kept in packages/lib/server/service/__tests__/insightsBooking.integration-test.ts:461 (confidence: 92)
The entire `describe("Caching", ...)` block — `should cache authorization conditions` and `should cache filter conditions` — is removed. However, the service still maintains `cachedAuthConditions` / `cachedFilterConditions` and the `getAuthorizationConditions` / `getFilterConditions` getters still short-circuit on the cache. Removing the only tests that pinned this behavior leaves the cache as untested infrastructure: a future refactor that accidentally calls `buildAuthorizationConditions()` twice (e.g. dropping the `if (this.cachedAuthConditions === undefined)` guard) will pass CI silently. If the SQL conversion makes the previous assertions invalid, port them rather than drop them.
```suggestion
it("should cache authorization conditions", async () => {
  const testData = await createTestData({ teamRole: MembershipRole.OWNER, orgRole: MembershipRole.OWNER });
  const service = new InsightsBookingService({ prisma, options: { scope: "user", userId: testData.user.id, orgId: testData.org.id } });
  const c1 = await service.getAuthorizationConditions();
  const c2 = await service.getAuthorizationConditions();
  expect(c2).toBe(c1); // identity check — proves cache, not just equality
  await testData.cleanup();
});
```

:yellow_circle: [correctness] Unreachable `else` branch in `getBaseConditions` in packages/lib/server/service/insightsBooking.ts:68 (confidence: 86)
`getAuthorizationConditions()` is typed `Promise<Prisma.Sql>` and always returns a `Prisma.Sql` (either a real condition or `NOTHING_CONDITION = Prisma.sql\`1=0\``). A `Prisma.Sql` instance is always truthy, so in `getBaseConditions`:
```ts
if (authConditions && filterConditions) { ... }
else if (authConditions) { ... }
else if (filterConditions) { ... }
else { return NOTHING_CONDITION; }
```
the second branch always matches when `filterConditions` is `null`, the first always matches otherwise, and the third (`filterConditions` only, no `authConditions`) and fourth (`else`) branches are unreachable. This isn't a bug in current behavior, but the dead branches signal that the contract between this method and `getAuthorizationConditions` was not modeled clearly during the conversion — a future change that lets `authConditions` be nullable will silently expose unauthorized rows. Either tighten the types so the impossibility is enforced, or drop the dead arms.
```suggestion
async getBaseConditions(): Promise<Prisma.Sql> {
  const authConditions = await this.getAuthorizationConditions(); // always Prisma.Sql
  const filterConditions = await this.getFilterConditions();      // Prisma.Sql | null
  if (filterConditions === null) return authConditions;
  return Prisma.sql`(${authConditions}) AND (${filterConditions})`;
}
```

:yellow_circle: [correctness] Constructor type widened — discriminated union narrowing lost in packages/lib/server/service/insightsBooking.ts:53 (confidence: 85)
The constructor's `options` parameter type changed from `InsightsBookingServiceOptions` (a `z.discriminatedUnion("scope", [...])` of three variants where `teamId` is required only for `scope: "team"` and forbidden otherwise) to a new flat `InsightsBookingServicePublicOptions = { scope: "user" | "org" | "team"; userId: number; orgId: number; teamId?: number }`. The runtime `safeParse` still rejects bad shapes, but compile-time callers can now pass `{ scope: "team", userId, orgId }` (missing `teamId`) without a TypeScript error and only discover the failure at runtime when `safeParse` returns `success: false` and `this.options` becomes `null`. The original discriminated union gave callers an immediate compile-time guarantee on this security-sensitive path. Re-export the schema-derived type for the public surface, or keep the discriminated public alias.
```suggestion
export type InsightsBookingServicePublicOptions = z.infer<typeof insightsBookingServiceOptionsSchema>;
```

## Risk Metadata
Risk Score: 70/100 (HIGH) | Blast Radius: authorization SQL on `BookingTimeStatusDenormalized` view — every insights consumer of this service is downstream | Sensitive Paths: authorization/SQL construction, AI-authored (Devin)
AI-Authored Likelihood: HIGH

(2 additional findings below confidence threshold)
