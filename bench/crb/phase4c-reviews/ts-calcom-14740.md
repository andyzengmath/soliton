## Summary
15 files changed, 555 lines added, 0 lines deleted. 9 findings (4 critical, 5 improvements).
Feature adds a new TRPC mutation `viewer.bookings.addGuests` plus dialog/email templates. The authorization gate, the blacklist check, and the email-dispatch call each have bugs; no tests accompany the new authorization-sensitive path.

## Critical

:red_circle: [correctness] Authorization check uses AND where name implies OR in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:46 (confidence: 94)
`isTeamAdminOrOwner` combines the two role checks with `&&`, so the guard is only satisfied when the user is simultaneously a team admin AND a team owner. The variable name and comment in other bookings handlers indicate this was meant to be a disjunction. Team admins who are not owners — the vastly more common case — fail this branch and fall through to the `isOrganizer`/`isAttendee` checks, which is fine for them personally but means admins cannot manage guests on another member's booking even though the variable name advertises that capability. It also risks the opposite interpretation (owners who are not admins are excluded) in the unusual org setups where the roles diverge.
```suggestion
  const isTeamAdminOrOwner =
    (await isTeamAdmin(user.id, booking.eventType?.teamId ?? 0)) ||
    (await isTeamOwner(user.id, booking.eventType?.teamId ?? 0));
```

:red_circle: [correctness] `sendAddGuestsEmails` receives the raw input instead of the filtered list in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:167 (confidence: 91)
The handler passes `guests` (the raw request payload) to `sendAddGuestsEmails`, but the attendees were created from `uniqueGuests` — the list after duplicate and blacklist filtering. Inside `sendAddGuestsEmails` the `newGuests.includes(attendee.email)` branch picks the "welcome" template for brand-new guests vs. the "guests were added" notification for existing attendees. When `guests` contains a duplicate email that was already an attendee, that attendee is now wrongly classified as a "new guest" and is re-sent an `AttendeeScheduledEmail`. When `guests` contains a blacklisted email, the request pretends that address was added even though it was not, producing no email but misleading downstream logging. Pass `uniqueGuests` to match what was actually persisted.
```suggestion
  try {
    await sendAddGuestsEmails(evt, uniqueGuests);
  } catch (err) {
    console.error("Error sending AddGuestsEmails", err);
  }
```

:red_circle: [security] Blacklist bypass via email casing in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:65 (confidence: 89)
`blacklistedGuestEmails` is lowercased on load, but the incoming `guest` string is compared verbatim: `!blacklistedGuestEmails.includes(guest)`. A submitter can bypass the blacklist by capitalizing any letter in the domain or local-part (`Abuse@Example.com` vs. `abuse@example.com`). Because this endpoint causes cal.com infrastructure to send calendar invitations and emails to attacker-chosen addresses, the blacklist is the only server-side abuse guard and must match case-insensitively. The same blacklist logic already lowercases on load, so the input side is the only thing missing.
```suggestion
  const uniqueGuests = guests.filter(
    (guest) =>
      !booking.attendees.some((attendee) => guest.toLowerCase() === attendee.email.toLowerCase()) &&
      !blacklistedGuestEmails.includes(guest.toLowerCase())
  );
```

:red_circle: [security] No rate limiting on an authenticated email-amplification endpoint in packages/trpc/server/routers/viewer/bookings/_router.tsx:79 (confidence: 87)
`addGuests` is declared as a plain `authedProcedure` with no rate-limit middleware. Any user who is an attendee on any booking — including a booking they themselves made against their own event-type — can repeatedly invoke this mutation to make cal.com send calendar invitations and follow-up emails to arbitrary email addresses. The only server-side abuse control is `BLACKLISTED_GUEST_EMAILS`, which is an opt-in denylist and empty by default. Sibling mutations in the booking router that cause outbound email (`editLocation`, `requestReschedule`) historically use `authedRatelimitedProcedure` (or equivalent) precisely for this reason — please either wrap `addGuests` in the rate-limited procedure or add an explicit `checkRateLimitAndThrowError` call keyed on `ctx.user.id` + `bookingId`.
```suggestion
  addGuests: authedProcedure
    .input(ZAddGuestsInputSchema)
    .use(async ({ ctx, next }) => {
      await checkRateLimitAndThrowError({
        identifier: `addGuests.${ctx.user.id}`,
        rateLimitingType: "core",
      });
      return next();
    })
    .mutation(async ({ input, ctx }) => {
```

## Improvements

:yellow_circle: [correctness] Error is caught but never logged with its payload in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:169 (confidence: 92)
`catch (err) { console.log("Error sending AddGuestsEmails"); }` drops the actual error object, so when this path fails in production there is no stack trace, recipient list, SMTP status code, or template name to diagnose from. Attendee persistence has already completed by this point, so the user still receives a `"Guests added"` success while emails silently fail. At minimum log `err`; ideally keep the swallow behaviour (to avoid rolling back the DB write) but surface through the logger used by the rest of the package.
```suggestion
  try {
    await sendAddGuestsEmails(evt, uniqueGuests);
  } catch (err) {
    logger.error("Error sending AddGuestsEmails", err);
  }
```

:yellow_circle: [testing] New authorization-sensitive TRPC handler ships without tests in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:1 (confidence: 90)
This 174-line handler adds a new permission matrix (team admin/owner × organizer × attendee), an email blacklist, deduplication against existing attendees, and cascading email dispatch — none of which is exercised by a test. Sibling files under `packages/trpc/server/routers/viewer/bookings/` have accompanying `*.test.ts` specs (for example `editLocation`), and the change here is a strict superset of their complexity. Please add at least: (a) an attendee on an unrelated booking is rejected (FORBIDDEN), (b) a blacklisted email with differing case is rejected, (c) duplicates of existing attendees are silently dropped, (d) `sendAddGuestsEmails` receives the filtered list.
```suggestion
// packages/trpc/server/routers/viewer/bookings/addGuests.handler.test.ts
// cover: unauthorized caller → FORBIDDEN; blacklist case-insensitive; dedupe;
// email dispatch receives uniqueGuests.
```

:yellow_circle: [correctness] Zod schema instantiated on every render in apps/web/components/dialog/AddGuestsDialog.tsx:23 (confidence: 88)
`ZAddGuestsInputSchema` is declared inside the `AddGuestsDialog` component body, so the `z.array(...).refine(...)` chain is rebuilt on every render. Beyond the allocation cost, this is dead code — the schema is only used in `handleAdd` and the uniqueness refine is also enforced server-side — so hoisting to module scope (and giving it a distinct name from the server-side schema to avoid confusion) is the cleaner fix. The server schema in `addGuests.schema.ts` could usefully share the uniqueness refine as well.
```suggestion
const ZClientAddGuestsInputSchema = z.array(z.string().email()).refine((emails) => {
  const uniqueEmails = new Set(emails);
  return uniqueEmails.size === emails.length;
});

export const AddGuestsDialog = (props: IAddGuestsDialog) => {
  // ...
```

:yellow_circle: [consistency] Prop parameter is reassigned inside the component body in packages/ui/form/MultiEmail.tsx:12 (confidence: 86)
`value = value || [];` reassigns the destructured prop, which is flagged by the repo's typical `no-param-reassign` lint posture and makes the lifecycle of `value` harder to reason about in the closures below. Use a local `const` or default the destructuring.
```suggestion
function MultiEmail({ value = [], readOnly, label, setValue, placeholder }: MultiEmailProps) {
```

:yellow_circle: [correctness] `calEvent.attendees[0].name` assumed to exist in packages/emails/templates/organizer-add-guests-email.ts:26 (confidence: 85)
The organizer subject line accesses `this.calEvent.attendees[0].name` without a length or optional-chain guard. For normal bookings `attendees` is non-empty, but during reschedule/cancel flows or after a bulk attendee removal it can be empty, in which case this throws inside the email payload builder and the caller's `catch` (see separate finding) swallows it silently. Guard with `?.` and provide a fallback consistent with `this.calEvent.organizer.name`.
```suggestion
        name: this.calEvent.attendees[0]?.name ?? this.calEvent.organizer.name,
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: new TRPC mutation on bookings (authedProcedure), new email templates reaching external recipients, new UI dialog wired into `BookingListItem` | Sensitive Paths: `packages/trpc/server/routers/viewer/bookings/` (auth + outbound email), `packages/emails/` (external email delivery)
AI-Authored Likelihood: LOW
