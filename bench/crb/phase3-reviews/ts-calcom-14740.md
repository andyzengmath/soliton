## Summary
15 files changed, 555 lines added, 0 lines deleted. 11 findings (3 critical, 5 improvements, 3 nitpicks).
Adds an "Add Guests" feature for existing bookings (tRPC mutation, dialog UI, MultiEmail input, new attendee/organizer email templates). Authorization logic, blacklist case handling, and email-dispatch filtering all have defects that warrant changes before merging.

## Critical

:red_circle: [security] Authorization gate uses `&&` instead of `||` and falls back to `teamId ?? 0` in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:28 (confidence: 95)
`isTeamAdminOrOwner` is computed as `(await isTeamAdmin(user.id, booking.eventType?.teamId ?? 0)) && (await isTeamOwner(user.id, booking.eventType?.teamId ?? 0))`. This requires the caller to be BOTH admin AND owner (owners typically aren't returned by `isTeamAdmin` in this codebase, so the conjunction is nearly always false). Worse, for non-team bookings `teamId ?? 0` coerces to `0`, which either short-circuits to `false` (silently denying legitimate admins) or, depending on the helper implementation, matches records with a `0` id. The name "AdminOrOwner" also clearly signals the author intended disjunction.
```suggestion
const teamId = booking.eventType?.teamId;
const isTeamAdminOrOwner = teamId
  ? (await isTeamAdmin(user.id, teamId)) || (await isTeamOwner(user.id, teamId))
  : false;
```

:red_circle: [security] Any attendee can add arbitrary guests to any booking in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:34 (confidence: 85)
The authorization check permits the mutation if `isAttendee` is true. Because `attendees` is built from whoever was previously added as a guest, a user that was *itself* added as a guest earlier can transitively add more guests — no organizer consent, no rate limit, no cap on guest count. Combined with the calendar update + email fan-out below, this is a spam and social-engineering vector. At minimum, gate attendee-initiated additions behind an event-type allowlist (e.g. a `disableGuests`-style flag already exists on `EventType`), cap the number of guests per request, and require rate limiting via the existing `checkRateLimitAndThrowError` helper used on other booking mutations.
```suggestion
if (!isTeamAdminOrOwner && !isOrganizer) {
  throw new TRPCError({ code: "FORBIDDEN", message: "you_do_not_have_permission" });
}
// If you genuinely want attendees to invite, gate it explicitly and rate-limit:
// if (isAttendee && booking.eventType?.disableGuests) throw new TRPCError(...);
// await checkRateLimitAndThrowError({ identifier: `addGuests:${user.id}`, rateLimitingType: "core" });
```

:red_circle: [security] Blacklist bypass via case mismatch in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:56 (confidence: 90)
`BLACKLISTED_GUEST_EMAILS` is lowercased on load, but each incoming `guest` is compared without normalization (`!blacklistedGuestEmails.includes(guest)`). Any blacklisted address submitted with a differing case (e.g. `Evil@Example.com`) slips past the filter and is persisted verbatim, then receives a calendar invite and email. The duplicate-attendee check on the same line has the same bug: attendee emails in Prisma may be lowercased while the submitted guest isn't, so a single address can be added twice.
```suggestion
const normalized = guests.map((g) => g.trim().toLowerCase());
const existingEmails = new Set(booking.attendees.map((a) => a.email.toLowerCase()));
const uniqueGuests = Array.from(new Set(normalized)).filter(
  (g) => !existingEmails.has(g) && !blacklistedGuestEmails.includes(g)
);
```

## Improvements

:yellow_circle: [correctness] Email dispatch uses raw `guests` instead of `uniqueGuests` in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:166 (confidence: 90)
`await sendAddGuestsEmails(evt, guests)` passes the original input, not the filtered `uniqueGuests`. Inside `sendAddGuestsEmails`, `newGuests.includes(attendee.email)` is used to pick `AttendeeScheduledEmail` vs `AttendeeAddGuestsEmail`. If the caller re-submits an already-existing attendee's email, that pre-existing attendee is re-notified with a fresh "you're scheduled" email — confusing and potentially leaking a calendar-invite ICS for an event they already had. Pass `uniqueGuests` so only newly-added addresses receive the onboarding email.
```suggestion
await sendAddGuestsEmails(evt, uniqueGuests);
```

:yellow_circle: [correctness] Partial failure leaves DB / calendar / email in an inconsistent state in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:147 (confidence: 80)
The handler writes attendees to the database (`prisma.booking.update`) before calling `eventManager.updateCalendarAttendees` and `sendAddGuestsEmails`. If either downstream step throws, the DB already reflects the new guests but the external calendars and invitees are out of sync. The email failure is swallowed with `console.log("Error sending AddGuestsEmails")` — no stack, no structured logger, no metrics — so operators won't see it. At minimum: (a) wrap downstream calls and compensate (or use an outbox/queue), (b) use the project's `logger` with the actual error, (c) don't silence calendar-update failures.
```suggestion
import logger from "@calcom/lib/logger";
// ...
try {
  await sendAddGuestsEmails(evt, uniqueGuests);
} catch (err) {
  logger.error("Error sending AddGuestsEmails", { bookingId, err });
}
```

:yellow_circle: [correctness] Zod schema allows duplicates; client dedup refinement is not enforced server-side in `packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts`:3 (confidence: 75)
`ZAddGuestsInputSchema` is `z.object({ bookingId, guests: z.array(z.string().email()) })` — no min length, no uniqueness refinement. The client-side `ZAddGuestsInputSchema` in `AddGuestsDialog.tsx` *does* enforce uniqueness, but the server schema is authoritative. Mirror the refinement on the server, and require at least one guest so the "you need to add at least one email" UX is consistent:
```suggestion
export const ZAddGuestsInputSchema = z.object({
  bookingId: z.number().int().positive(),
  guests: z
    .array(z.string().email())
    .min(1)
    .max(10)
    .refine((emails) => new Set(emails.map((e) => e.toLowerCase())).size === emails.length, {
      message: "emails_must_be_unique_valid",
    }),
});
```

:yellow_circle: [correctness] `OrganizerAddGuestsEmail` crashes when `attendees` is empty in `packages/emails/templates/organizer-add-guests-email.ts`:22 (confidence: 80)
`this.calEvent.attendees[0].name` is accessed unconditionally to build the subject line. If `sendAddGuestsEmails` is ever called with a CalendarEvent whose `attendees` array is empty (e.g. all new guests were filtered by the blacklist in a future code path, or a team-member-only invite), this throws `Cannot read properties of undefined`. Guard with a fallback — the existing `organizer-scheduled-email.ts` uses `this.calEvent.attendees[0]?.name ?? this.calEvent.organizer.name`.
```suggestion
name: this.calEvent.attendees[0]?.name ?? this.calEvent.organizer.name,
```

:yellow_circle: [testing] No tests added for a handler that mutates bookings, calendars, and sends email in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:1 (confidence: 85)
The 174-line handler touches authorization, Prisma, the external calendar manager, and email dispatch, and introduces a new env-var contract (`BLACKLISTED_GUEST_EMAILS`). No unit test, no integration test, no e2e test, and no test was added for `MultiEmail` or `AddGuestsDialog`. Given that each of the critical issues above would have been caught by a minimal permission/blacklist test fixture, please add at least: (1) authorization matrix tests (organizer / team admin / team owner / random attendee / unrelated user), (2) blacklist + case-insensitive dedup, (3) an e2e happy path in `apps/web/playwright`.

## Nitpicks

:white_circle: [correctness] Initial `[""]` forces the MultiEmail list to render an empty field on open in `apps/web/components/dialog/AddGuestsDialog.tsx`:32 (confidence: 70)
`useState<string[]>([""])` ships a non-empty array, so the `value.length ?` branch renders the list (with one empty `EmailField`) instead of the "Add emails" CTA button on first open. If that's intentional, consider seeding `[]` and letting the CTA path handle the first add; otherwise the two code paths in `MultiEmail.tsx` for `value.length` vs `!value.length` are dead code in this flow.

:white_circle: [consistency] `MultiEmail` reassigns its destructured prop in `packages/ui/form/MultiEmail.tsx`:11 (confidence: 65)
`value = value || []` mutates the parameter binding and violates the immutability convention used elsewhere in `packages/ui`. Use `const safeValue = value ?? []` and reference `safeValue` below. Also, the `placeholder` prop is declared but never passed through to `EmailField`.

:white_circle: [consistency] The `UNSTABLE_HANDLER_CACHE` comment "Unreachable code but required for type safety" is misleading in `packages/trpc/server/routers/viewer/bookings/_router.tsx`:82 (confidence: 55)
The reachable-or-not analysis is identical for every other route in the file, but none carries this comment. Either drop the comment or align all routes.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: tRPC booking router, email-manager fan-out, UI shell (`packages/ui/index.tsx`), common.json i18n keys | Sensitive Paths: `packages/trpc/server/routers/viewer/bookings/**` (authorization + booking mutation), `packages/emails/**` (transactional email), new env var `BLACKLISTED_GUEST_EMAILS`
AI-Authored Likelihood: LOW

(0 additional findings below confidence threshold)
