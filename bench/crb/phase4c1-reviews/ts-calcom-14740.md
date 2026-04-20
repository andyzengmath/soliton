## Summary
15 files changed, 555 lines added, 0 lines deleted. 7 findings (2 critical, 5 improvements).
Adds a booking guest-invite feature but ships with a broken team-admin authorization check, a case-sensitivity blacklist bypass, and several email-flow correctness issues.

## Critical
:red_circle: [security] Authorization check requires user to be BOTH admin AND owner in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:42 (confidence: 97)
The variable name `isTeamAdminOrOwner` implies an OR, but the logic uses `&&`, so a user must simultaneously satisfy `isTeamAdmin` and `isTeamOwner`. In Cal.com's team membership model a user typically holds a single role, so this condition is effectively always `false` — team admins and team owners who are not also the booking organizer or an attendee will be denied with `FORBIDDEN`. Additionally, `booking.eventType?.teamId ?? 0` passes `0` into `isTeamAdmin`/`isTeamOwner` for personal (non-team) event types, which is a meaningless team id and masks the logic error during local testing.
```suggestion
  const teamId = booking.eventType?.teamId;
  const isTeamAdminOrOwner = teamId
    ? (await isTeamAdmin(user.id, teamId)) || (await isTeamOwner(user.id, teamId))
    : false;
```

:red_circle: [security] Blacklist check is case-sensitive while blacklist entries are lowercased in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:65 (confidence: 95)
`BLACKLISTED_GUEST_EMAILS` is parsed with `.map((email) => email.toLowerCase())`, but `blacklistedGuestEmails.includes(guest)` compares against the raw `guest` string from user input. An attacker can trivially bypass the blacklist by submitting `Evil@Example.com` when `evil@example.com` is blocked. Email comparison must be normalized (typically lowercased) on both sides.
```suggestion
  const uniqueGuests = guests.filter((guest) => {
    const normalized = guest.toLowerCase();
    return (
      !booking.attendees.some((attendee) => attendee.email.toLowerCase() === normalized) &&
      !blacklistedGuestEmails.includes(normalized)
    );
  });
```

## Improvements
:yellow_circle: [correctness] Raw `guests` (not `uniqueGuests`) passed to `sendAddGuestsEmails` in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:164 (confidence: 90)
`sendAddGuestsEmails(evt, guests)` is called with the original user input rather than the filtered `uniqueGuests`. Downstream, `email-manager.ts` uses `newGuests.includes(attendee.email)` to decide whether to send `AttendeeScheduledEmail` (new guest) or `AttendeeAddGuestsEmail` (pre-existing attendee). If the caller passes an already-existing attendee's email, that attendee will receive a spurious "newly scheduled" email instead of the "guests added" notification. This both confuses recipients and can be used to re-send `AttendeeScheduledEmail` to existing attendees on demand.
```suggestion
  await sendAddGuestsEmails(evt, uniqueGuests);
```

:yellow_circle: [correctness] Duplicate-attendee check is case-sensitive in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:68 (confidence: 88)
`!booking.attendees.some((attendee) => guest === attendee.email)` will treat `Foo@bar.com` and `foo@bar.com` as distinct emails, allowing the same person to be inserted twice into `Attendee` rows (and receive duplicated invites/ICS updates). The server-side input schema (`addGuests.schema.ts`) and the client-side `ZAddGuestsInputSchema` in `AddGuestsDialog.tsx` also enforce uniqueness only on the raw strings. Normalize all email comparisons (and consider storing lowercase) to a single canonical form.

:yellow_circle: [correctness] Email send error is swallowed silently after DB state has changed in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:168 (confidence: 85)
```
try {
  await sendAddGuestsEmails(evt, guests);
} catch (err) {
  console.log("Error sending AddGuestsEmails");
}
```
At this point the booking's `Attendee` rows have been written and the calendar has been updated via `eventManager.updateCalendarAttendees`, but the error object itself is discarded — not only is there no `err` in the log, there's also no telemetry or user-visible indication that the invitees won't receive notifications. Use the project logger (`logger.error("…", { err })` or equivalent) and at minimum include the error details so on-call engineers can diagnose bounces/SMTP failures.
```suggestion
  try {
    await sendAddGuestsEmails(evt, uniqueGuests);
  } catch (err) {
    logger.error("Error sending AddGuestsEmails", { bookingId, err });
  }
```

:yellow_circle: [correctness] No transaction boundary between DB insert and calendar update in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:82 (confidence: 75)
`prisma.booking.update` writes the new attendees, then `eventManager.updateCalendarAttendees(evt, booking)` mutates external calendars. If the external calendar call throws, the DB already reflects the new guests but the calendar does not — the handler will bubble up the error and leave the system in an inconsistent state with no compensation logic. Consider either (a) running the external side-effects first and only persisting on success, or (b) wrapping the flow so a failure triggers an explicit rollback / re-queue of the calendar operation.

:yellow_circle: [testing] No tests added for the new `addGuests` handler in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:1 (confidence: 90)
The PR introduces a non-trivial authorization matrix (organizer / attendee / team admin / team owner), a blacklist, dedup logic, and a fan-out of emails, but ships no unit or integration tests for any of it. Given the two authorization/blacklist defects surfaced in this review, the absence of tests is material — a single happy-path + forbidden-path + blacklist test would very likely have caught the `&&` bug. Add coverage for: organizer permitted, random user denied, blacklist enforced (mixed case), duplicate attendee dedup, and the `uniqueGuests.length === 0` bad-request path.

:yellow_circle: [correctness] `prisma.user.findFirstOrThrow` with `id: booking.userId || 0` in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:50 (confidence: 70)
If `booking.userId` is null (possible for some booking rows), the query falls back to `id: 0`, which will always throw at `findFirstOrThrow`, surfacing a generic Prisma error to the caller instead of a domain-specific one. Either guard explicitly (`if (!booking.userId) throw new TRPCError({ code: "NOT_FOUND", message: "booking_has_no_organizer" })`) or use `prisma.booking.findUnique({ include: { user: … } })` above and reuse the joined user rather than re-fetching.

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: new tRPC mutation surfaced to any authenticated user; touches email-manager, UI shell, and booking attendees table | Sensitive Paths: `packages/trpc/server/routers/viewer/bookings/*`, `packages/emails/*`
AI-Authored Likelihood: LOW
