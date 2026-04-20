## Summary
12 files changed, 82 lines added, 107 lines deleted. 8 findings (6 critical, 2 improvements, 0 nitpicks).
No tests for dynamic import failure in EventManager.ts while `forEach(async)` anti-pattern silently drops calendar/video deletions across reschedule and cancel paths.

## Critical

:red_circle: [testing] No tests for dynamic import failure in createEvent/updateEvent/deleteEvent in packages/core/EventManager.ts:485 (confidence: 95)
The refactor introduces a new runtime failure mode — dynamic `import()` can reject — with zero tests in the changeset. Previously, static imports failed at module load time (loud and immediate). Now they fail asynchronously inside already-running booking flows; rejected imports become unhandled Promise rejections that are invisible in test runs and logs. This is a material change in failure semantics with no safety net.
```suggestion
// Add Jest tests that mock the dynamic import to reject and assert
// createEvent rejects with an actionable error rather than silently
// failing. Cover the update and delete paths as well.
it("createEvent rejects with clear error when app dynamic import fails", async () => {
  jest.spyOn(global, "import" as any).mockRejectedValueOnce(new Error("Module not found"));
  await expect(manager.createEvent(mockCalEvent)).rejects.toThrow("Module not found");
});
```

:red_circle: [cross-file-impact] getVideoAdapters callers outside the 12-file changeset may still be synchronous in packages/core/EventManager.ts:1 (confidence: 92)
`getVideoAdapters` was converted from sync to async. EventManager's create/update/reschedule paths call `getVideoAdapters`, but the PR diff only touches a subset of call sites. If any other EventManager method still iterates on the old sync return shape, video conference links will not attach to new bookings and no error will be thrown — the adapter array will simply be an unresolved Promise being iterated as an object.
```suggestion
// Audit all getVideoAdapters call sites in packages/core/EventManager.ts
// and any downstream API route handlers. Run:
//   grep -rn "getVideoAdapters" packages apps
// and verify every call site either awaits or is inside an async context
// updated by this PR.
```

:red_circle: [testing] forEach(async) fire-and-forget cancel path has no tests in packages/features/bookings/lib/handleCancelBooking.ts:1 (confidence: 92)
The async migration introduces (or retains) fire-and-forget `.forEach(async)` in the reschedule and cancel paths. No integration tests in the changeset assert that async side-effects (external calendar deletions, video room teardown) complete before the booking is marked cancelled, or that a failure in those side-effects is surfaced to the caller. This means the fire-and-forget behaviour described in the critical finding at lines 458-470 cannot be caught by CI.
```suggestion
it("surfaces calendar deletion errors instead of swallowing them in cancel flow", async () => {
  const mockCalendar = {
    deleteEvent: jest.fn().mockRejectedValue(new Error("Calendar API down")),
  };
  await expect(handleCancelBooking(mockCancelParams)).rejects.toThrow();
});
```

:red_circle: [testing] Payment async migration has zero test coverage in packages/lib/payment/handlePayment.ts:1 (confidence: 88)
`handlePayment` and `deletePayment` are both in the changeset and control the creation and refund of financial transactions. The async migration changes how the payment app is resolved at runtime. Without tests, booking-confirmed-without-payment or payment-without-booking inconsistencies introduced by the migration cannot be caught. This is a particularly high-risk gap given the payment path's sensitivity (risk factor: `sensitive_paths` = 100).
```suggestion
it("does not confirm booking when payment app dynamic import fails", async () => {
  jest.mock("@calcom/app-store/stripepayment", () => { throw new Error("Stripe module failed to load"); });
  await expect(handlePayment(mockPaymentParams)).rejects.toThrow();
  expect(mockDb.booking.update).not.toHaveBeenCalledWith(
    expect.objectContaining({ status: "ACCEPTED" })
  );
});
```

:red_circle: [security] forEach(async) on reschedule path silently drops external calendar deletions in packages/trpc/server/routers/viewer/bookings.tsx:550 (confidence: 85)
`bookingRefsFiltered.forEach(async (bookingRef) => { const calendar = await getCalendar(...); return calendar?.deleteEvent(...) })`. `Array.prototype.forEach` discards the Promise returned by the async callback, so `calendar?.deleteEvent` is fire-and-forget. The tRPC reschedule mutation resolves before external calendar deletions complete. After a reschedule, old meeting invites remain live on attendees' calendars: the organizer sees the new time in-app while attendees can still dial into the old slot. Rejections are unhandled and escape the enclosing try/catch. This violates OWASP A08 (Software and Data Integrity Failures) and A04 (Insecure Design — race on state mutation). `videoClient.ts` in the same PR demonstrates the correct pattern (for-of loop) but this call site was not migrated.
```suggestion
await Promise.all(
  bookingRefsFiltered.map(async (bookingRef) => {
    if (!bookingRef.uid) return;
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      return calendar?.deleteEvent(
        bookingRef.uid,
        builder.calendarEvent,
        bookingRef.externalCalendarId
      );
    }
    if (bookingRef.type.endsWith("_video")) {
      return deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  })
);
```
[References: https://owasp.org/Top10/A08_2021-Software_and_Data_Integrity_Failures/]

:red_circle: [security] forEach(async) fire-and-forget on recurring cancel — apiDeletes populated after it is consumed in packages/features/bookings/lib/handleCancelBooking.ts:458 (confidence: 85)
Recurring-booking cancellation uses `.forEach(async credential => { const calendar = await getCalendar(credential); ... apiDeletes.push(calendar?.deleteEvent(...)); })`. `forEach` discards the async callback's Promise entirely. `apiDeletes.push(...)` executes after the outer `Promise.all(apiDeletes)` has already consumed the array (which was empty at that point). External calendar deletions are silently dropped, unhandled rejections escape the try/catch, and the booking is marked cancelled while attendees' Google/Office365/CalDAV calendars continue to show the event — the meeting link remains active. This is a data-integrity violation per OWASP A08 and a logging failure per OWASP A09. The same file correctly uses a `for-of` loop for the `calendarCredentials` loop at approximately line 477, making this inconsistency visible in the diff.
```suggestion
const calendarCreds = bookingToDelete.user.credentials.filter(
  (c) => c.type.endsWith("_calendar")
);
for (const credential of calendarCreds) {
  const calendar = await getCalendar(credential);
  for (const updBooking of updatedBookings) {
    const bookingRef = updBooking.references.find((ref) => ref.type.includes("_calendar"));
    if (bookingRef) {
      apiDeletes.push(
        calendar?.deleteEvent(bookingRef.uid, evt, bookingRef.externalCalendarId) as Promise<unknown>
      );
    }
  }
}
```
[References: https://owasp.org/Top10/A08_2021-Software_and_Data_Integrity_Failures/]

## Improvements

:yellow_circle: [consistency] forEach(async) anti-pattern — async callback promise discarded in packages/app-store/vital/lib/reschedule.ts:122 (confidence: 85)
The inner callback in `bookingRefsFiltered.forEach(async (bookingRef) => {...})` is async and awaits `getCalendar` inside it, but `Array.prototype.forEach` does not await async callbacks. The returned Promise — including `calendar?.deleteEvent(...)` — is fire-and-forget. Rejections escape the enclosing try/catch as unhandled, and the reschedule flow resolves before external-calendar deletions complete. `videoClient.ts` in the same PR demonstrates the correct pattern (for-of loop) but this file was not migrated. See the Conflicts section: the security agent rates this critical (below threshold, surfaced for context).
```suggestion
await Promise.all(
  bookingRefsFiltered.map(async (bookingRef) => {
    if (!bookingRef.uid) return;
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      return calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
    }
    if (bookingRef.type.endsWith("_video")) {
      return deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  })
);
```

:yellow_circle: [consistency] forEach(async) anti-pattern in wipemycalother reschedule in packages/app-store/wipemycalother/lib/reschedule.ts:122 (confidence: 85)
Same pattern as `vital/lib/reschedule.ts`. The async callback returned to `forEach` is ignored — calendar deletions are fire-and-forget, rejections are unhandled, and the try/catch cannot observe them. See the Conflicts section: the security agent rates this critical (below threshold, surfaced for context).
```suggestion
await Promise.all(
  bookingRefsFiltered.map(async (bookingRef) => {
    if (!bookingRef.uid) return;
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      return calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
    }
    if (bookingRef.type.endsWith("_video")) {
      return deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  })
);
```

## Conflicts

:zap: Agents disagree on packages/app-store/vital/lib/reschedule.ts:122 — consistency (improvement, confidence: 85) vs security (critical, confidence: 80, below threshold)

Discussion point: consistency treats this as a code-quality issue; security treats it as a data-integrity failure with observable user-facing consequences (stale calendar events, live video rooms after cancellation). Because the identical logic at `packages/trpc/server/routers/viewer/bookings.tsx` and `packages/features/bookings/lib/handleCancelBooking.ts` was rated critical by both agents, the security agent's framing appears the stronger position for this file as well — but the below-threshold confidence requires human review.

:zap: Agents disagree on packages/app-store/wipemycalother/lib/reschedule.ts:122 — consistency (improvement, confidence: 85) vs security (critical, confidence: 80, below threshold)

Discussion point: Same disagreement as above. The agents agree on the mechanics (forEach(async) drops the Promise) but diverge on severity. The critical findings at `bookings.tsx` and `handleCancelBooking.ts` establish that this mechanism causes real data-integrity problems; the `wipemycalother` path should be evaluated with that context in mind.

## Risk Metadata
Risk Score: 71/100 (HIGH) | Blast Radius: 12 files across central app-store registry, core booking/calendar/video/payment paths (~45 estimated importers, capped at 100) | Sensitive Paths: `packages/lib/payment/handlePayment.ts`, `packages/lib/payment/deletePayment.ts` (match `payment/`)
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold: `packages/lib/payment/handlePayment.ts` uncaught dynamic import rejection (conf 70), `packages/lib/payment/deletePayment.ts` uncaught refund import rejection (conf 70), `packages/core/CalendarManager.ts` getCalendarCredentials returns Promise-valued `.calendar` (conf 80))

_Review metadata: 5 of 6 agents completed (correctness agent failed due to environment path issue; overlap fully covered by security and consistency agents). Review duration: 461426 ms._
