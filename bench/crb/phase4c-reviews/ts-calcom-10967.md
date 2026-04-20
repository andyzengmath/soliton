## Summary
22 files changed, 365 lines added, 215 lines deleted. 11 findings (8 critical, 3 improvements, 0 nitpicks).
Inverted boolean in create.handler.ts silently assigns slug under wrong billing condition; multiple null-derefs and dead-branch fallbacks in the destinationCalendar array refactor.

## Critical
:red_circle: [correctness] Inverted boolean logic — slug assignment reversed when refactoring `&&` to ternary in packages/trpc/server/routers/viewer/organizations/create.handler.ts:151 (confidence: 98)
Original `...(!IS_TEAM_BILLING_ENABLED && { slug })` spreads `slug` when billing is DISABLED. The replacement `...(IS_TEAM_BILLING_ENABLED ? { slug } : {})` spreads `slug` when billing is ENABLED — the negation was silently dropped. The adjacent `requestedSlug` line preserved its polarity correctly, confirming this was a transcription error and not an intentional behavior change. This is entirely outside the PR's stated scope (destinationCalendar refactor) and is a classic "AI dropped a negation while normalizing an idiom" regression. Organizations with team billing enabled will receive a slug when they should not, and vice versa.
```suggestion
...(!IS_TEAM_BILLING_ENABLED ? { slug } : {}),
```

:red_circle: [correctness] Unconditional property access on potentially-undefined `mainHostDestinationCalendar` causes runtime crash in packages/core/EventManager.ts:277 (confidence: 98)
The destructured `const [mainHostDestinationCalendar] = evt.destinationCalendar ?? []` yields `undefined` when `evt.destinationCalendar` is null, undefined, or an empty array — all valid states (e.g., any booking without a configured destination calendar). The following line accesses `mainHostDestinationCalendar.integration` without optional chaining, throwing `TypeError: Cannot read properties of undefined (reading 'integration')`. This crashes the Google Meet location fallback for a large class of standard bookings. The old code used `evt.destinationCalendar?.integration` which was null-safe.
```suggestion
if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar") {
  evt["location"] = "integrations:daily";
}
```

:red_circle: [correctness] Dead-branch logic in `updateEvent` — `find()` against falsy `externalCalendarId` always returns undefined in packages/app-store/googlecalendar/lib/CalendarService.ts:253 (confidence: 97)
The ternary `const selectedCalendar = externalCalendarId ? externalCalendarId : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId` reaches the `find` branch only when `externalCalendarId` is falsy, so it searches for `cal.externalId === <falsy>` and can never match. `selectedCalendar` is always `undefined` in the fallback path. The Google Calendar `events.update` call then receives `calendarId: undefined`. The pre-refactor fallback was `event.destinationCalendar?.externalId`, which returned a scalar value.
```suggestion
const selectedCalendar = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.[0]?.externalId;
```

:red_circle: [correctness] Dead-branch logic in `deleteEvent` — calendarId silently becomes undefined in packages/app-store/googlecalendar/lib/CalendarService.ts:315 (confidence: 97)
Identical dead-branch bug in `deleteEvent`: `const calendarId = externalCalendarId ? externalCalendarId : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId`. When `externalCalendarId` is falsy, the `.find` never matches. Unlike `createEvent`, there is no `|| "primary"` fallback — `calendar.events.delete` receives `calendarId: undefined`, producing a Google API error or silently targeting the wrong calendar. Note also that `defaultCalendarId = "primary"` is defined but never used.
```suggestion
const calendarId = externalCalendarId || event.destinationCalendar?.[0]?.externalId || defaultCalendarId;
```

:red_circle: [correctness] `push()` called on null — team destination calendars silently lost when organizer has no calendar in packages/features/bookings/lib/handleNewBooking.ts:861 (confidence: 95)
`evt.destinationCalendar` is initialized via a ternary that returns `null` when neither `eventType.destinationCalendar` nor `organizerUser.destinationCalendar` is set. The immediately following block calls `evt.destinationCalendar?.push(...teamDestinationCalendars)`. Optional chaining on `null` makes the push a silent no-op, discarding every team calendar. This is precisely the scenario the PR is meant to fix (collective events with multiple team members) and it reproduces the original broken behavior in this edge case.
```suggestion
destinationCalendar: eventType.destinationCalendar
  ? [eventType.destinationCalendar]
  : organizerUser.destinationCalendar
  ? [organizerUser.destinationCalendar]
  : [],
```

:red_circle: [cross-file-impact] `LarkCalendarService.createEvent` signature not updated — credentialId silently dropped for collective events in packages/app-store/larkcalendar/lib/CalendarService.ts:125 (confidence: 92)
The `Calendar` interface's `createEvent` now requires `credentialId: number` (reflected in GoogleCalendarService and CalendarManager's call site `calendar.createEvent(calEvent, credential.id)`). LarkCalendarService's signature remains `createEvent(event: CalendarEvent)`. TypeScript's structural typing lets the call compile — `credentialId` is passed but silently ignored. Lark always uses `destinationCalendar[0]` regardless of which credential is active, defeating the multi-calendar routing this PR introduces.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
  let eventId = "";
  let eventRespData;
  const selectedDestinationCalendar =
    event.destinationCalendar?.find((cal) => cal.credentialId === credentialId) ??
    event.destinationCalendar?.[0];
  const calendarId = selectedDestinationCalendar?.externalId;
```

:red_circle: [cross-file-impact] `Office365CalendarService.createEvent` signature not updated — credentialId silently dropped for collective events in packages/app-store/office365calendar/lib/CalendarService.ts:72 (confidence: 92)
Same issue as LarkCalendarService. The body was updated to destructure `destinationCalendar[0]` via `const [mainHostDestinationCalendar] = event.destinationCalendar ?? []`, but the method signature remains `createEvent(event: CalendarEvent)`. The `credentialId` passed at the call site is silently ignored. All Office 365 collective event bookings always target the first calendar in the array, regardless of the invoked credential.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
  const selectedDestinationCalendar =
    event.destinationCalendar?.find((cal) => cal.credentialId === credentialId) ??
    event.destinationCalendar?.[0];
```

:red_circle: [cross-file-impact] `BaseCalendarService.createEvent` signature not updated — all CalDAV integrations drop credentialId in packages/lib/CalendarService.ts:153 (confidence: 88)
`BaseCalendarService` is the abstract base for CalDAV integrations (Apple Calendar, generic CalDAV). Its `createEvent` signature was not updated to accept `credentialId`. All subclasses inherit this gap. When `CalendarManager.createEvent` passes `credential.id` as the second argument, CalDAV implementations silently discard it and route to `destinationCalendar[0]` only, breaking per-credential calendar selection for these integrations in collective events.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
  // ... use credentialId to pick the matching destination before filtering calendars
```

## Improvements
:yellow_circle: [correctness] User-level `destinationCalendar` fallback dropped — inconsistent with all other booking handlers in packages/trpc/server/routers/viewer/bookings/requestReschedule.handler.ts:1054 (confidence: 90)
Every other analogous handler in this PR (handleCancelBooking, confirm, editLocation, paypal-webhook, stripe webhook, deleteCredential.handler, bookingReminder) follows: `booking.destinationCalendar ? [booking.destinationCalendar] : user.destinationCalendar ? [user.destinationCalendar] : []`. The `requestReschedule` handler was updated to wrap `booking.destinationCalendar` in an array but omits the user-level fallback entirely. When a booking has no destination calendar set but the user does, rescheduled events will have an empty `destinationCalendar` array instead of inheriting the user's calendar — falling through to the `else` branch in `createAllCalendarEvents` with unpredictable credential selection.
```suggestion
destinationCalendar: bookingToReschedule?.destinationCalendar
  ? [bookingToReschedule.destinationCalendar]
  : bookingToReschedule?.user?.destinationCalendar
  ? [bookingToReschedule.user.destinationCalendar]
  : [],
```

:yellow_circle: [correctness] `loadUsers` refactor drops `organization.slug` include — silent undefined downstream in packages/features/bookings/lib/handleNewBooking.ts:764 (confidence: 88)
The refactored `loadUsers` function omits `organization: { select: { slug: true } }` from the Prisma query on the dynamic-user code path. Any downstream code reading `user.organization?.slug` for dynamically-looked-up users will now silently receive `undefined` instead of the actual organization slug. This is a regression introduced by rewriting the query rather than surgically adapting it, and is outside the stated scope of the destinationCalendar refactor.
```suggestion
select: {
  ...userSelect.select,
  credentials: true,
  metadata: true,
  organization: {
    select: { slug: true },
  },
},
```

:yellow_circle: [correctness] Over-defensive `loadUsers` rewrite introduces dead guards and swallows real errors in packages/features/bookings/lib/handleNewBooking.ts:754 (confidence: 88)
`loadUsers` was rewritten with `Array.isArray` guards on values that are either zod-validated (`dynamicUserList`) or Prisma-typed arrays (`eventType.hosts`) — making the guards dead code. A catch-all `try/catch` converts all Prisma errors into a generic `HttpError 500 "Unable to load users"`, erasing context that aids debugging. The rewrite also regresses the `|| []` fallback: the original returned `eventType.users || []`; the new version returns `eventType.users` which can be `undefined`, causing a downstream crash on `users.slice(1)`.
```suggestion
const loadUsers = async () =>
  !eventTypeId
    ? await prisma.user.findMany({
        where: { username: { in: dynamicUserList } },
        select: {
          ...userSelect.select,
          credentials: true,
          metadata: true,
          organization: { select: { slug: true } },
        },
      })
    : eventType.hosts?.length
    ? eventType.hosts.map(({ user, isFixed }) => ({ ...user, isFixed }))
    : eventType.users ?? [];
```

## Risk Metadata
Risk Score: 74/100 (HIGH) | Blast Radius: CalendarEvent type is foundational across the monorepo (22 files updated; ~40+ consumer files in practice); Calendar interface change forces every integration to update | Sensitive Paths: payments/paypal-webhook.ts, payments/webhook.ts, loggedInViewer/deleteCredential.handler.ts
AI-Authored Likelihood: HIGH

(7 additional findings below confidence threshold)
