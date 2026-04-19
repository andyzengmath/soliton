# PR Review: calcom/cal.com #10967 — fix: handle collective multiple host on destinationCalendar

## Summary
22 files changed, 368 lines added, 216 lines deleted. 14 findings (5 critical, 6 improvements, 3 nitpicks).
Converts `CalendarEvent.destinationCalendar` from a nullable single object to a nullable array so collective team events can invite every host's calendar. The refactor is mechanically thorough across call sites but introduces several regressions: a null-dereference crash in Google Meet fallback, two broken `.find` lookups in the Google Calendar service (a self-referential filter that can never match), a silently-dropped select in `loadUsers` (`organization.slug`), an inverted boolean in the organization-create handler that is unrelated to the stated fix, and missing guards that pass `undefined` credentials into `updateEvent`. No unit tests were added despite the breadth of the change.

## Critical

:red_circle: [correctness] NullPointerException in Google Meet fallback when destinationCalendar is empty in packages/core/EventManager.ts:277 (confidence: 98)
`const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];` produces `undefined` whenever the array is empty or null. The very next line does `mainHostDestinationCalendar.integration !== "google_calendar"` *without* optional chaining, so any booking whose location is `MeetLocationType` and whose `destinationCalendar` resolves to `[]` (which the rest of the PR now routinely produces — see `bookingReminder.ts`, `webhook.ts`, `paypal-webhook.ts`, `handleCancelBooking.ts`) will throw `TypeError: Cannot read properties of undefined (reading 'integration')`. The previous code used `evt.destinationCalendar?.integration !== "google_calendar"`, which did not crash.
```suggestion
    const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
    if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar") {
      evt["location"] = "integrations:daily";
    }
```

:red_circle: [correctness] Self-referential `.find` always returns undefined in updateEvent in packages/app-store/googlecalendar/lib/CalendarService.ts:256 (confidence: 99)
```
const selectedCalendar = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId;
```
The else-branch is only reached when `externalCalendarId` is falsy (null/undefined/""). Inside the branch, the predicate compares each calendar's `externalId` against that same falsy `externalCalendarId`, so it can only match a calendar whose `externalId` is also falsy — i.e. never a real calendar. Before the PR the fallback was `event.destinationCalendar?.externalId` (the single host's calendar); the new behaviour yields `undefined` in every call where `externalCalendarId` was not already provided. Google's `calendar.events.update` is then invoked with `calendarId: undefined` and will 404.
```suggestion
      const [mainHostDestinationCalendar] = event.destinationCalendar ?? [];
      const selectedCalendar = externalCalendarId
        ? externalCalendarId
        : mainHostDestinationCalendar?.externalId;
```

:red_circle: [correctness] Same self-referential `.find` in deleteEvent in packages/app-store/googlecalendar/lib/CalendarService.ts:315 (confidence: 99)
```
const calendarId = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId;
```
Identical bug to the updateEvent case: the else branch is reached only when `externalCalendarId` is falsy, so the `.find` predicate can never match. Old bookings whose calendar reference has no `externalCalendarId` will silently fail to delete the event on the primary calendar (the pre-PR fallback).
```suggestion
      const [mainHostDestinationCalendar] = event.destinationCalendar ?? [];
      const calendarId = externalCalendarId ? externalCalendarId : mainHostDestinationCalendar?.externalId;
```

:red_circle: [correctness] Inverted billing-enabled condition unrelated to the stated fix in packages/trpc/server/routers/viewer/organizations/create.handler.ts:151 (confidence: 97)
Before: `...(!IS_TEAM_BILLING_ENABLED && { slug })` — include `slug` only when billing is **disabled**.
After:  `...(IS_TEAM_BILLING_ENABLED ? { slug } : {})` — include `slug` only when billing is **enabled**.
The condition is inverted. This is outside the scope of "destinationCalendar array" and is almost certainly an accidental refactor during lint/style cleanup. Org creation in billing-disabled environments will now be missing the slug (breaking handle-based routing), and billing-enabled environments will set both `slug` and `metadata.requestedSlug`, which is exactly the split this logic was designed to avoid.
```suggestion
            ...(!IS_TEAM_BILLING_ENABLED && { slug }),
            metadata: {
              ...(IS_TEAM_BILLING_ENABLED && { requestedSlug: slug }),
```

:red_circle: [correctness] `updateEvent` called with possibly-undefined credential in packages/core/EventManager.ts:454 (confidence: 92)
In `updateAllCalendarEvents`, when the in-memory `calendarCredentials` miss and the DB fetch either returns null or returns a row without `app.slug`, `credential` remains `undefined`, yet the code unconditionally calls `result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId));`. `updateEvent` dereferences `credential.type`/`credential.appName` and will throw. The parallel `createAllCalendarEvents` path correctly guards with `if (credential) { ... }` — the update path needs the same guard.
```suggestion
          if (credential) {
            result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId));
          }
```

## Improvements

:yellow_circle: [correctness] `organization.slug` silently dropped from `loadUsers` select in packages/features/bookings/lib/handleNewBooking.ts:762 (confidence: 88)
The rewrite of `loadUsers` removed the `organization: { select: { slug: true } }` include that the pre-PR query carried for the dynamic-user-list branch. Downstream code (org-scoped routing, org-aware link generation, email templates) expects `user.organization?.slug` to be populated. The change is unrelated to the destinationCalendar refactor and will cause organization users booking via dynamic username routes to lose their org slug in emitted events/emails. Restore the include or move the query back to its previous shape.

:yellow_circle: [correctness] Collective team destinationCalendars silently dropped when organizer has none in packages/features/bookings/lib/handleNewBooking.ts:1060 (confidence: 90)
```
destinationCalendar: eventType.destinationCalendar
  ? [eventType.destinationCalendar]
  : organizerUser.destinationCalendar
  ? [organizerUser.destinationCalendar]
  : null,
...
if (isTeamEventType && eventType.schedulingType === "COLLECTIVE") {
  evt.destinationCalendar?.push(...teamDestinationCalendars);
}
```
If both the event type and the organizer lack a destinationCalendar (common for orgs whose hosts configure their own calendars), `evt.destinationCalendar` is `null` and the `?.push` is a no-op — the teamDestinationCalendars gathered above are dropped, defeating the PR's stated purpose for those bookings.
```suggestion
    destinationCalendar: eventType.destinationCalendar
      ? [eventType.destinationCalendar]
      : organizerUser.destinationCalendar
      ? [organizerUser.destinationCalendar]
      : [],
...
  if (isTeamEventType && eventType.schedulingType === "COLLECTIVE" && teamDestinationCalendars.length) {
    evt.destinationCalendar = [...(evt.destinationCalendar ?? []), ...teamDestinationCalendars];
  }
```

:yellow_circle: [correctness] `loadUsers` now throws on empty dynamicUserList where the old code returned `[]` in packages/features/bookings/lib/handleNewBooking.ts:727 (confidence: 80)
The old expression was `!eventTypeId ? prisma.user.findMany({ where: { username: { in: dynamicUserList } } }) : ...`. The new version raises `HttpError(400, "dynamicUserList is not properly defined or empty")` when `dynamicUserList` is empty. This is a behavioural change the PR does not justify; callers that relied on Prisma returning `[]` and being handled downstream will now see a 400. Either keep the old permissive behaviour or document the new contract and audit callers.

:yellow_circle: [correctness] `hosts` fallback in `loadUsers` returns shape-mismatched users in packages/features/bookings/lib/handleNewBooking.ts:802 (confidence: 78)
```
const users = hosts.map(({ user, isFixed }) => ({ ...user, isFixed }));
return users.length ? users : eventType.users;
```
`eventType.users` does not carry the `isFixed` property, so callers (`users.filter((u) => u.isFixed)`, etc.) will misclassify every host as non-fixed when the event has `users[]` but no `hosts[]`. Either map `eventType.users` into the same shape or document the asymmetry.

:yellow_circle: [cross-file-impact] `externalId` never threaded through the non-credentialId createAllCalendarEvents branch in packages/core/EventManager.ts:365 (confidence: 85)
In the `else` branch (`destination.credentialId` absent), `createEvent(c, event)` is called without the third `externalId` argument, so `result.externalId` is `undefined` for every event created via that path. Downstream the new `externalCalendarId: result.externalId` assignment in `createAllCalendarEvents` (lines 167-170) then stores `undefined`, which re-surfaces as the broken `.find` predicate in the Google service above. Thread `destination.externalId` through.
```suggestion
          createdEvents = createdEvents.concat(
            await Promise.all(
              destinationCalendarCredentials.map(async (c) => await createEvent(c, event, destination.externalId))
            )
          );
```

:yellow_circle: [testing] 22-file, cross-cutting type change lands with zero tests in packages/core/EventManager.ts (confidence: 92)
The PR description confirms: "I haven't added tests that prove my fix is effective or that my feature works." This is a type-shape change that alters every integration calendar adapter, every webhook, and every booking lifecycle handler. At minimum add: (a) a unit test that `EventManager.create` fans out to all collective hosts; (b) a regression test for Meet fallback when destinationCalendar is empty; (c) a regression test for Google `updateEvent`/`deleteEvent` when `externalCalendarId` is not supplied.

## Nitpicks

:white_circle: [consistency] Empty-state representation inconsistent: `[]` vs `null` in packages/features/bookings/lib/handleNewBooking.ts:1063 (confidence: 75)
Some call sites converge on `[]` (bookingReminder, webhook.ts, paypal-webhook.ts) and others on `null` (handleNewBooking, builder.ts types). Pick one (prefer `[]` now that every consumer already handles iteration) and apply consistently to make downstream null-checks uniform.

:white_circle: [consistency] Typo fix and import-split bundled into a destinationCalendar PR in packages/core/CalendarManager.ts:228, packages/core/builders/CalendarEvent/builder.ts:1 (confidence: 90)
`"organiser"` → `"organizer"` and the `Prisma`/`type Booking` import split are unrelated cleanup. They make the diff harder to bisect; prefer a separate commit.

:white_circle: [consistency] `eventType.hosts?.length` check removed from loadUsers in packages/features/bookings/lib/handleNewBooking.ts:789 (confidence: 70)
The old code had `eventType.hosts?.length ? hosts.map(...) : eventType.users || []`. The new code always takes the hosts branch and only falls back to `eventType.users` when the mapped array is empty. For events where `hosts` is populated but some hosts lack a `user`, the mapped objects will spread `undefined`. Restore the length check or filter nullish hosts.

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: 22 files across core booking, calendar adapters (Google, Office365, Lark, CalDAV), payment webhooks (Stripe/PayPal), tRPC viewer/org routers, and shared type definitions | Sensitive Paths: payment webhooks, credential handlers, cron endpoints
AI-Authored Likelihood: MEDIUM (stylistic signals: stub `firstName`/`lastName` additions, wordy generic try/catch in `loadUsers`, inverted-then-reformatted boolean in `organizations/create.handler.ts`, defensive DB-refetch blocks duplicated verbatim between `createAllCalendarEvents` and `updateAllCalendarEvents`)
