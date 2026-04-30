## Summary
15 files changed, 555 lines added, 0 lines deleted. 16 findings (10 critical, 6 improvements).
New `viewer.bookings.addGuests` mutation has multiple high-confidence authorization, normalization, and atomicity defects that combine into a mass-mailing / privilege-bypass attack surface; recommendation: request changes.

## Critical
:red_circle: [security] Authorization check uses `&&` instead of `||` — team admins (non-owners) silently denied in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:471 (confidence: 99)
The variable is named `isTeamAdminOrOwner` but is computed as `(await isTeamAdmin(...)) && (await isTeamOwner(...))`. Owners are typically a subset of admins, so this conjunction reduces to "owner only", silently denying every non-owner team admin with FORBIDDEN. The intent expressed by the variable name is clearly OR, and matches the standard RBAC pattern used elsewhere in the codebase. Also short-circuit when `teamId` is missing rather than fall back to the magic value `0`.
```suggestion
const teamId = booking.eventType?.teamId;
const isTeamAdminOrOwner = teamId
  ? (await isTeamAdmin(user.id, teamId)) || (await isTeamOwner(user.id, teamId))
  : false;
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/285.html]

:red_circle: [security] Any attendee can add unbounded guests — mass-mail / phishing amplification in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:475 (confidence: 90)
`isAttendee = !!booking.attendees.find(a => a.email === user.email)` permits ANY attendee on a booking to mutate the guest list. Combined with the unbounded `guests` array (see separate finding) and Cal.com-branded outbound mail, an authenticated attendee on any booking they're invited to can trigger thousands of invitation emails to arbitrary addresses through Cal.com's sender reputation — a classic IDOR + amplification primitive. Attendees should not be able to alter the attendee list of bookings they did not create.
```suggestion
if (!isTeamAdminOrOwner && !isOrganizer) {
  throw new TRPCError({ code: "FORBIDDEN", message: "you_do_not_have_permission" });
}
```
If attendee-initiated guest additions are a deliberate product requirement, gate them behind an event-type setting (e.g. `eventType.allowAttendeesToAddGuests`) and enforce a strict per-call cap.
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/639.html]

:red_circle: [security] Email normalization missing — case-mixed addresses bypass blacklist and duplicate-attendee check in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:498 (confidence: 97)
`BLACKLISTED_GUEST_EMAILS` is normalized to lowercase, but the incoming `guest` value is compared as-is via `blacklistedGuestEmails.includes(guest)`. The duplicate check `booking.attendees.some(a => guest === a.email)` is also strict-case. An attacker submits `Banned@Evil.com` and the lowercased blacklist entry `banned@evil.com` does not match — blacklist defeated. The same case-mismatch lets `Victim@example.com` be added even when `victim@example.com` is already an attendee, generating duplicate invitation emails to the same mailbox.
```suggestion
const normalizedBlacklist = (process.env.BLACKLISTED_GUEST_EMAILS ?? "")
  .split(",")
  .map((e) => e.trim().toLowerCase())
  .filter(Boolean);

const existingEmails = new Set(booking.attendees.map((a) => a.email.toLowerCase()));
const uniqueGuests = [...new Set(guests.map((g) => g.toLowerCase()))]
  .filter((g) => !existingEmails.has(g) && !normalizedBlacklist.includes(g));
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/178.html]

:red_circle: [security] `sendAddGuestsEmails` invoked with original unfiltered `guests` instead of `uniqueGuests` — blacklisted/existing addresses still receive mail in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:592 (confidence: 96)
After filtering to `uniqueGuests` (removing existing attendees and blacklisted addresses), the call site passes the raw `guests` input to the mailer. Two bugs follow: (1) blacklisted addresses receive invitation emails — defeating the blacklist entirely; (2) inside `sendAddGuestsEmails`, the branch `if (newGuests.includes(attendee.email))` classifies attendees who happened to also appear in the input as "new", so previously-existing attendees receive `AttendeeScheduledEmail` ("you have a new booking") instead of `AttendeeAddGuestsEmail` ("guests were added"). The filtered list must be the source of truth.
```suggestion
await sendAddGuestsEmails(evt, uniqueGuests);
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/200.html]

:red_circle: [correctness] `createMany` accepts duplicates within a single input — duplicate attendee rows / unhandled P2002 in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:516 (confidence: 95)
`uniqueGuests` is filtered against existing attendees but not against itself. A client submitting `["a@x.com", "a@x.com"]` passes the filter twice, and `createMany` is called without `skipDuplicates: true`. Depending on the unique constraints on `(bookingId, email)` in the attendees table, the result is either silent duplicate rows or an unhandled `P2002` Prisma error surfacing as a 500 to the client. Deduplicate the input array and add `skipDuplicates: true` as a defense-in-depth safety net.
```suggestion
data: {
  attendees: {
    createMany: {
      data: guestsFullDetails,
      skipDuplicates: true,
    },
  },
},
```
(See the email-normalization finding for the deduplication of `uniqueGuests` itself.)

:red_circle: [correctness] Calendar update is not wrapped in try/catch — DB and external calendar permanently desync on failure in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:589 (confidence: 90)
`prisma.booking.update` (line 516) commits new attendee rows. `eventManager.updateCalendarAttendees` (line 589) then runs without any error handling. If the calendar provider fails (token expiry, network blip, provider error) the exception propagates as a 500, but the attendees are already committed: organizer/existing-attendee calendars never see the update, the DB shows the new guests, and there is no retry queue or compensating delete. Either surface the calendar failure with a specific error code or ordering the calendar call before the DB write.
```suggestion
try {
  await eventManager.updateCalendarAttendees(evt, booking);
} catch (err) {
  logger.error("Calendar update failed after attendees inserted", { bookingId, err });
  throw new TRPCError({
    code: "INTERNAL_SERVER_ERROR",
    message: "calendar_update_failed",
    cause: err,
  });
}
```

:red_circle: [correctness] Email-send catch swallows the error object — `console.log` with no `err`, no diagnostic signal in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:594 (confidence: 95)
`} catch (err) { console.log("Error sending AddGuestsEmails"); }` discards `err` entirely and uses `console.log` (not `console.error`) — invisible to stderr-based error monitors. Any SMTP failure, render error, or provider throttle produces no diagnostic trail; the caller still receives `{ message: "Guests added" }`. Combined with the lack of structured logging, abuse triage (e.g., detecting mailing-list bombing through bounce patterns) is impossible.
```suggestion
} catch (err) {
  logger.error("Error sending AddGuestsEmails", {
    err,
    bookingId,
    actorId: user.id,
    guestCount: uniqueGuests.length,
  });
}
```
Do NOT log raw guest email addresses (PII).
[References: https://owasp.org/Top10/A09_2021-Security_Logging_and_Monitoring_Failures/, https://cwe.mitre.org/data/definitions/778.html]

:red_circle: [cross-file-impact] `sendAddGuestsEmails` imported from `@calcom/emails` but the package barrel is not updated to re-export it in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:3 (confidence: 92)
The handler imports `sendAddGuestsEmails` from `@calcom/emails` (the package entry point). The diff adds the function to `packages/emails/email-manager.ts` but does not show any change to `packages/emails/index.ts` (the barrel that `@calcom/emails` resolves to). Unless the barrel already does `export * from "./email-manager"`, the named import will fail at runtime with "does not provide an export named 'sendAddGuestsEmails'" and the whole feature breaks. Verify the barrel and add an explicit re-export if it does not already wildcard-export `email-manager.ts`.
```suggestion
// packages/emails/index.ts
export { sendAddGuestsEmails } from "./email-manager";
```

:red_circle: [cross-file-impact] `renderEmail("AttendeeAddGuestsEmail", ...)` and `renderEmail("OrganizerAddGuestsEmail", ...)` depend on a registry that may not be updated in packages/emails/templates/attendee-add-guests-email.ts:29 (confidence: 90)
Both new email classes call `renderEmail("<TemplateName>", ...)`. In Cal.com the `renderEmail` helper looks up the React component by string key from a registry — `packages/emails/src/templates/index.ts` is updated to export the components, but if `renderEmail` reads from a separate map (a common pattern), the new entries must be added there too. If the registry is not updated, both invitation emails render as empty HTML at runtime. Locate the `renderEmail` map and confirm both keys are registered alongside the other email types; otherwise add them.

:red_circle: [cross-file-impact] Client and server schemas diverge on uniqueness — duplicates pass tRPC validation in apps/web/components/dialog/AddGuestsDialog.tsx:22 (confidence: 97)
`AddGuestsDialog.tsx` defines a local `ZAddGuestsInputSchema = z.array(z.string().email()).refine(...uniqueness)`, but the server schema in `packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts` is `z.object({ bookingId, guests: z.array(z.string().email()) })` — no `.refine()`, no uniqueness, no `.min(1)`, no `.max()`. The client guard is also shape-mismatched (validates a bare array, but the mutation is invoked with `{ bookingId, guests }`). Any non-UI caller (direct tRPC call, future component) bypasses uniqueness entirely. Move the canonical schema server-side and import it into the dialog.
```suggestion
// packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts
export const ZAddGuestsInputSchema = z.object({
  bookingId: z.number().int().positive(),
  guests: z.array(z.string().email().max(254))
    .min(1)
    .max(20)
    .refine((emails) => new Set(emails.map((e) => e.toLowerCase())).size === emails.length, {
      message: "emails_must_be_unique_valid",
    }),
});
```

## Improvements
:yellow_circle: [security] No upper bound on `guests` array — DoS / mass-mailing amplification in packages/trpc/server/routers/viewer/bookings/addGuests.schema.ts:5 (confidence: 90)
`z.array(z.string().email())` accepts an unbounded array. A single authenticated request can submit millions of addresses, causing DB write amplification via `createMany`, memory pressure building `guestsFullDetails` and `evt`, calendar-provider API flooding inside `eventManager.updateCalendarAttendees`, and an outbound mail flood. Combined with the over-broad `isAttendee` authorization, every authenticated user can trigger this on any booking they're a guest of.
```suggestion
guests: z.array(z.string().email().max(254)).min(1).max(20),
```
Also enforce a per-booking total cap server-side and apply tRPC rate limiting on the procedure.
[References: https://cwe.mitre.org/data/definitions/770.html, https://cwe.mitre.org/data/definitions/400.html]

:yellow_circle: [security] `teamId ?? 0` fallback relies on undocumented sentinel behavior of `isTeamAdmin`/`isTeamOwner` in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:471 (confidence: 88)
For non-team bookings `booking.eventType?.teamId` is null and the expression substitutes `0`, which is then passed to both team-check helpers. The current safety depends entirely on those helpers returning falsy for `id=0`. A future refactor that treats `0` as a wildcard, throws, or logs would silently flip the authorization gate. It also issues two spurious DB queries on every personal-event guest add. The correct primitive is to skip the team check entirely when there is no team.
```suggestion
const teamId = booking.eventType?.teamId ?? null;
const isTeamAdminOrOwner = teamId !== null
  ? (await isTeamAdmin(user.id, teamId)) || (await isTeamOwner(user.id, teamId))
  : false;
```

:yellow_circle: [correctness] Organizer email subject interpolates `attendees[0].name` which is empty for newly-added guests in packages/emails/templates/organizer-add-guests-email.ts:22 (confidence: 92)
`guestsFullDetails` sets `name: ""` for newly inserted guests, and `evt.attendees` includes both old and new attendees. If the first array element is a new guest the subject renders as "Guests Added: Stand-up with  at Monday, ..." (literal blank where the name should be). The sibling `attendee-add-guests-email.ts` already avoids this by using `this.calEvent.team?.name || this.calEvent.organizer.name` — mirror that here.
```suggestion
subject: `${this.t("guests_added_event_type_subject", {
  eventType: this.calEvent.type,
  name: this.calEvent.team?.name || this.calEvent.organizer.name,
  date: this.getFormattedDate(),
})}`,
```

:yellow_circle: [correctness] `multiEmailValue.length === 0` guard is dead code — initial state is `[""]` in apps/web/components/dialog/AddGuestsDialog.tsx:42 (confidence: 91)
`useState<string[]>([""])` initializes the array with one empty string, so the `length === 0` guard never fires at submit. The genuine empty-input case (one or more blank rows) falls through to Zod, which rejects `""` as an invalid email — so the user gets validation feedback by accident, not by design. Filter empty strings explicitly before validating to avoid silent dependence on email-format rejection of `""`.
```suggestion
const handleAdd = () => {
  const filledEmails = multiEmailValue.filter((e) => e.trim() !== "");
  if (filledEmails.length === 0) {
    setIsInvalidEmail(true);
    return;
  }
  const validationResult = ZAddGuestsInputSchema.safeParse(filledEmails);
  if (validationResult.success) {
    addGuestsMutation.mutate({ bookingId, guests: filledEmails });
  } else {
    setIsInvalidEmail(true);
  }
};
```

:yellow_circle: [cross-file-impact] 6 new i18n keys added only to `en/common.json` — non-English users see raw key strings in apps/web/public/static/locales/en/common.json:1122 (confidence: 98)
The diff adds `new_guests_added`, `guests_added_event_type_subject`, `guests_added`, `unable_to_add_guests`, `emails_must_be_unique_valid`, and `add_emails` exclusively to the English locale. No other locale file under `apps/web/public/static/locales/` is touched. `next-i18next` falls back to the literal key — so a French user sees `"emails_must_be_unique_valid"` instead of a phrase, and non-English attendees receive emails whose subject line is the raw key `"guests_added_event_type_subject"`. At minimum, copy the English values into every locale file as a placeholder until proper translations are sourced.

:yellow_circle: [correctness] `booking.userId || 0` passed to `findFirstOrThrow` masks "booking has no organizer" as a misleading P2025 error in packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts:484 (confidence: 85)
`booking.userId` is typed `number | null`. Falling back to `0` silently issues a query for a non-existent user, producing a Prisma `P2025 No record found` that propagates as a 500 with a confusing message rather than a domain-level TRPC error. Guard the null case explicitly.
```suggestion
if (!booking.userId) {
  throw new TRPCError({ code: "INTERNAL_SERVER_ERROR", message: "booking_has_no_organizer" });
}
const organizer = await prisma.user.findFirstOrThrow({
  where: { id: booking.userId },
  select: { name: true, email: true, timeZone: true, locale: true },
});
```

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: trpc booking router + email-manager (central email dispatch hub) + new UI re-export | Sensitive Paths: packages/trpc/server/routers/viewer/bookings/* (auth + DB mutation), packages/emails/email-manager.ts (mass-mail surface)
AI-Authored Likelihood: MEDIUM

(3 additional findings below confidence threshold)
