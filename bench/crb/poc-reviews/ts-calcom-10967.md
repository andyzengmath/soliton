# PR Review — calcom/cal.com #10967

**Title:** fix: handle collective multiple host on destinationCalendar
**Base:** main ← **Head:** fix-collective-events-have-only-one-team-attendee-on-the-google-event-7754-cal-1267
**Fixes:** #7754 (CAL-1267) — Collective Events have only one team attendee on the google event

## Summary

22 files changed, ~366 lines added, ~216 lines deleted. 11 findings (4 critical, 5 improvements, 2 nitpicks).

Converts `CalendarEvent.destinationCalendar` from a single `DestinationCalendar | null` to `DestinationCalendar[] | null` so that every collective-event host can have their own calendar event created. The type change is propagated through ~22 call sites (payment webhooks, booking handlers, calendar services). The Google Calendar service is updated to iterate credentials; Lark and Office365 still only consume the first entry, which means the stated fix only actually lands for Google-host collective events. Several new regressions were introduced — a guaranteed NPE when `destinationCalendar` is empty, dead `find()` lookups that always return `undefined`, and a `null` fallback that prevents team calendars from being appended in collective flows.

## Critical

:red_circle: [correctness] Guaranteed TypeError when `destinationCalendar` is empty or undefined in `EventManager.send` in `packages/core/EventManager.ts:117` (confidence: 98)

The previous code read `evt.destinationCalendar?.integration !== "google_calendar"` with an optional chain so a missing destination calendar degraded to `undefined !== "google_calendar"` → `true`, safely entering the fallback. The refactor removes that safety:

```ts
const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
if (evt.location === MeetLocationType && mainHostDestinationCalendar.integration !== "google_calendar") {
  evt["location"] = "integrations:daily";
}
```

If `evt.destinationCalendar` is `[]`, `null`, or `undefined`, `mainHostDestinationCalendar` is `undefined` and `.integration` throws. This path is reached on every Meet-location booking where the user/event lacks a destination calendar — exactly the no-calendar-connected flow that previously worked.

```suggestion
const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar") {
  evt["location"] = "integrations:daily";
}
```

:red_circle: [correctness] `find(cal => cal.externalId === externalCalendarId)` with a falsy `externalCalendarId` always returns undefined in `packages/app-store/googlecalendar/lib/CalendarService.ts:256` (confidence: 97)

Two sites in GoogleCalendarService (`updateEvent`, `deleteEvent`) now do:

```ts
const selectedCalendar = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId;
```

The `else` branch is only taken when `externalCalendarId` is falsy, but it then searches for an entry whose `externalId` equals that same falsy value. The result is always `undefined`, and `calendar.events.update` / `calendar.events.delete` receive `calendarId: undefined`. Unlike `createEvent` — which at least falls back to `"primary"` — update/delete have no fallback. The original code used `event.destinationCalendar?.externalId` as the fallback; the replacement has no semantically equivalent expression. Likely intent was to look up by `credentialId`, matching `createEvent`.

```suggestion
const selectedCalendar =
  externalCalendarId ??
  event.destinationCalendar?.find((cal) => cal.credentialId === this.credential.id)?.externalId ??
  "primary";
```

:red_circle: [correctness] Collective team calendars silently dropped when organizer has no destination in `packages/features/bookings/lib/handleNewBooking.ts:1063` (confidence: 95)

```ts
destinationCalendar: eventType.destinationCalendar
  ? [eventType.destinationCalendar]
  : organizerUser.destinationCalendar
  ? [organizerUser.destinationCalendar]
  : null,
// ...
if (isTeamEventType && eventType.schedulingType === "COLLECTIVE") {
  evt.destinationCalendar?.push(...teamDestinationCalendars);
}
```

When both the event type and the organizer lack a destination calendar, `evt.destinationCalendar` is set to `null`, and the subsequent `?.push(...)` is silently skipped by the optional chain. Team hosts that DO have calendars connected will not receive events — i.e. the very bug the PR claims to fix (#7754) still occurs whenever the organizer's calendar is unset. Default to `[]` so `teamDestinationCalendars` is always appended.

```suggestion
destinationCalendar: eventType.destinationCalendar
  ? [eventType.destinationCalendar]
  : organizerUser.destinationCalendar
  ? [organizerUser.destinationCalendar]
  : [],
```

:red_circle: [correctness] Recurring cancellations will delete every event N times when multiple calendar references exist in `packages/features/bookings/lib/handleCancelBooking.ts:430` (confidence: 90)

The refactor wraps the existing recurring-cancellation block in a new outer `for (const reference of bookingCalendarReference)` loop. The inner block iterates **all** `user.credentials` and deletes **all** `updatedBookings` calendar events for each credential. Previously this happened once per booking; now it happens once per calendar reference. For a collective booking with N host-calendar references, each remaining recurring instance will be deleted N times (best case: 404 noise in logs; worst case: if the provider accepts redundant deletes and audit-logs them, you now have N× log volume and possibly rate-limit pressure). Move the recurring block out of the per-reference loop, or gate it on `reference === bookingCalendarReference[0]`.

## Improvements

:yellow_circle: [cross-file-impact] Multi-host fix only lands for Google Calendar — Lark & Office365 still write one calendar in `packages/app-store/larkcalendar/lib/CalendarService.ts:128` (confidence: 92)

The stated intent is "every host can get invited correctly." `GoogleCalendarService.createEvent` was updated to take a `credentialId` and select the matching destination entry, while `CalendarManager.createEvent` now iterates destinations and calls providers once per credential. But `LarkCalendarService.createEvent(event)` and `Office365CalendarService.createEvent(event)`:

1. Do not accept `credentialId` (they match the new interface only because TS allows narrower signatures).
2. Read only `event.destinationCalendar[0]` via `const [mainHostDestinationCalendar] = event.destinationCalendar ?? [];`.

Result: a collective booking where one host is on Lark/Office365 and another is on Google will create the Google host's event correctly but write the Lark/Office365 event only to the first destination's mailbox — the original bug is preserved for non-Google hosts. Either update Lark/Office365 to honor `credentialId`, or document the limitation and file a follow-up.

:yellow_circle: [correctness] Missing `externalId` in non-credentialId fallback of `createAllCalendarEvents` in `packages/core/EventManager.ts:375` (confidence: 85)

In `createAllCalendarEvents`, the `credentialId` branch passes `destination.externalId` through to `createEvent(credential, event, destination.externalId)`, but the `else` branch (no `credentialId`) does not:

```ts
createdEvents = createdEvents.concat(
  await Promise.all(destinationCalendarCredentials.map(async (c) => await createEvent(c, event)))
);
```

Downstream in `create()`, `result.externalId` is then stored on the booking reference as `externalCalendarId`. In this fallback branch the reference will have `externalCalendarId: undefined`, which later breaks update/delete flows that rely on it (see the `find` bug above). Pass `destination.externalId` through.

```suggestion
createdEvents = createdEvents.concat(
  await Promise.all(
    destinationCalendarCredentials.map(async (c) => await createEvent(c, event, destination.externalId))
  )
);
```

:yellow_circle: [consistency] Two different code paths for "booking or user destinationCalendar → array" in `packages/features/ee/payments/api/webhook.ts:101` (confidence: 80)

Six call sites (`bookingReminder.ts`, `handleCancelBooking.ts` ×2, `handleNewBooking.ts`, `paypal-webhook.ts`, `webhook.ts` ×2, `deleteCredential.handler.ts`, `confirm.handler.ts`, `editLocation.handler.ts`, `requestReschedule.handler.ts`) now expand `booking.destinationCalendar || user.destinationCalendar` into nested ternaries. The shape drifts: some use `: []`, some use `: null`, some go through an intermediate `selectedDestinationCalendar` variable. Extract a small helper to avoid the 10× duplicated boilerplate and the subtle `null` vs `[]` divergence that caused the collective-fallback regression (see finding #3).

```suggestion
// packages/types/Calendar.d.ts or a shared util
export const toDestinationCalendarArray = (
  ...candidates: Array<DestinationCalendar | null | undefined>
): DestinationCalendar[] =>
  candidates.filter((c): c is DestinationCalendar => Boolean(c)).slice(0, 1);
```

:yellow_circle: [correctness] `organization.slug` select dropped from dynamic-booking user query in `packages/features/bookings/lib/handleNewBooking.ts:737` (confidence: 75)

The pre-refactor query selected:

```ts
select: {
  ...userSelect.select,
  credentials: true,
  metadata: true,
  organization: { select: { slug: true } },
}
```

The refactored `loadUsers` removed the `organization` select. `organizerUser` and other downstream consumers in this file (and in `handleNewBooking` helpers) reference `user.organization?.slug` for org-scoped routing and username rendering. If `userSelect.select` does not already include `organization`, this is a regression for dynamic-group bookings on orgs. Verify `userSelect.select` or restore the explicit select.

:yellow_circle: [testing] No new tests for the most error-prone kind of refactor in `packages/core/EventManager.ts:335` (confidence: 90)

The PR converts a cardinality (one → many) on a type that flows through payment webhooks, booking creation, cancellation, rescheduling, and every calendar provider. The checklist in the PR description explicitly notes "I haven't added tests that prove my fix is effective." Given the NPE in `EventManager.send` (finding #1) and the silent team-calendar-drop (finding #3), at least one unit test asserting: (a) Meet-location booking with empty `destinationCalendar` doesn't throw, and (b) collective event with `eventType.destinationCalendar=null` still appends team calendars, would have caught both. Recommend gating merge on at least a smoke unit test for `EventManager.createAllCalendarEvents` with a collective input.

## Nitpicks

:white_circle: [consistency] `@NOTE:` comment contradicts the iteration in `packages/core/EventManager.ts:114` (confidence: 60)

The comment says "destinationCalendar it's an array now so as a fallback we will only check the first one" but the surrounding function `createAllCalendarEvents` DOES iterate all entries. The comment is true only for this specific Meet-location guard and is easy to misread as the whole file's policy. Tighten the wording.

:white_circle: [nitpick] Unrelated refactor mixed into a cross-cutting fix in `packages/trpc/server/routers/viewer/organizations/create.handler.ts:151` (confidence: 70)

```diff
- ...(!IS_TEAM_BILLING_ENABLED && { slug }),
+ ...(IS_TEAM_BILLING_ENABLED ? { slug } : {}),
  metadata: {
-   ...(IS_TEAM_BILLING_ENABLED && { requestedSlug: slug }),
+   ...(IS_TEAM_BILLING_ENABLED ? { requestedSlug: slug } : {}),
```

The first change flips the boolean — before: set `slug` when billing is **disabled**; after: set `slug` when billing is **enabled**. This is a behavior change, not a stylistic rewrite, and is completely unrelated to destinationCalendar arrays. Either revert or split into its own PR with its own review.

## Conflicts

(none)

## Risk Metadata

Risk Score: **74/100 (HIGH)** | Blast Radius: payment webhooks (Stripe + PayPal), core EventManager, all three calendar integrations, 5 tRPC booking handlers | Sensitive Paths: `packages/features/ee/payments/**`, `packages/core/EventManager.ts`, `packages/app-store/*/lib/CalendarService.ts`
AI-Authored Likelihood: LOW-MEDIUM (a couple of mechanically-correct-but-semantically-broken `find()` patterns and a `null`-default that defeats the appended-array step are typical of a human mid-refactor; the dead-find lookups smell like a search-and-replace that wasn't re-read)

## Recommendation

**request-changes** — Merge is not safe as-is. Must-fix before landing: findings #1 (NPE), #2 (dead find), #3 (collective drop). Should-fix: #4 (N× recurring delete), #5 (other providers don't honor credentialId), #6 (missing externalId), #8 (organization.slug select), at least one regression test. The unrelated `organization` boolean flip (nitpick #2) should be pulled out.

---

*Review scope: full unified diff of PR #10967 (1126 lines, 22 files). No comments posted to upstream PR — local CRB benchmark evaluation only.*
