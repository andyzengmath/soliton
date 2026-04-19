## Summary
15 files changed, 555 lines added, 0 lines deleted. 6 findings (5 critical, 1 improvement).
New "add guests to booking" feature has a broken team-admin authorization predicate, ships an email-flooding amplification vector (no rate limit, no cap), mis-classifies existing attendees as new guests due to passing the wrong list to the email sender, and lacks any test coverage for authorization, deduplication, or email routing logic.

## Critical

:red_circle: [correctness] `isTeamAdminOrOwner` uses `&&` instead of `||` — team admins are denied access in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:470 (confidence: 98)
The permission check computes `isTeamAdminOrOwner` as `(await isTeamAdmin(user.id, teamId)) && (await isTeamOwner(user.id, teamId))`, but `isTeamAdmin` and `isTeamOwner` check mutually exclusive membership roles (ADMIN vs OWNER) — no user holds both concurrently, so this predicate is effectively always `false`. The variable name explicitly states "AdminOrOwner" and the intent is OR. As written, every legitimate team admin or owner who is neither the organizer nor an attendee of the booking is incorrectly blocked from adding guests. Additionally, the `teamId ?? 0` fallback queries a non-existent team 0 when `booking.eventType?.teamId` is null, masking the bug further. This is a straightforward authorization regression that an `||` fixes.
```suggestion
const teamId = booking.eventType?.teamId;
const isTeamAdminOrOwner = teamId
  ? (await isTeamAdmin(user.id, teamId)) || (await isTeamOwner(user.id, teamId))
  : false;
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/285.html]

:red_circle: [correctness] `sendAddGuestsEmails` receives unfiltered `guests` — pre-existing attendees get wrong email type in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:592 (confidence: 95)
The handler filters the input `guests` list into `uniqueGuests` (removing entries already in `booking.attendees` and blacklisted addresses). Only `uniqueGuests` are persisted to the database. However, the subsequent email dispatch is called with the original, unfiltered `guests` list: `await sendAddGuestsEmails(evt, guests)`. Inside `sendAddGuestsEmails`, `newGuests.includes(attendee.email)` decides which template to send — attendees in `newGuests` receive a full `AttendeeScheduledEmail` (new booking invite with ICS), others receive `AttendeeAddGuestsEmail` (a "guests were added" notification). When the caller submits an email that was already an attendee, that pre-existing attendee is mis-classified as "new" and resent a duplicate booking confirmation. The case-sensitive `.includes` comparison compounds this. Pass `uniqueGuests` and normalize case.
```suggestion
await sendAddGuestsEmails(evt, uniqueGuests);
```
[References: https://cwe.mitre.org/data/definitions/670.html]

:red_circle: [security] No rate limit / guest-cap on addGuests — email spam amplification vector in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:76 (confidence: 90)
Any authenticated user who is an attendee or organizer on a booking can invoke `addGuests` with an arbitrary array of email addresses. For each new guest, Cal.com sends an `AttendeeScheduledEmail` (with ICS calendar invite) via Cal.com's own SMTP infrastructure. The schema `z.array(z.string().email())` imposes no maximum length, the procedure has no `checkRateLimitAndThrowError` wrapper, and the handler does not require email-verification of the target. A low-effort attacker who holds a single booking can weaponize Cal.com to (1) blast arbitrary third-party addresses with fake meeting invites, (2) abuse Cal.com's sending-domain reputation for phishing-style invites where the subject line carries the user-controlled event type, (3) pollute victims' calendar clients. This is an A04 Insecure Design issue compounded by A09-adjacent abuse potential.
```suggestion
// addGuests.schema.ts — cap payload size
export const ZAddGuestsInputSchema = z.object({
  bookingId: z.number(),
  guests: z.array(z.string().email().toLowerCase()).max(10),
});

// addGuests.handler.ts — rate limit per caller
import { checkRateLimitAndThrowError } from "@calcom/lib/checkRateLimitAndThrowError";
await checkRateLimitAndThrowError({
  rateLimitingType: "core",
  identifier: `addGuests:${user.id}`,
});
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/770.html]

:red_circle: [security] Case-sensitive email comparison allows duplicate attendees and blacklist bypass in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:498 (confidence: 88)
The dedup/blacklist filter performs exact-string comparison: `guest === attendee.email` and `blacklistedGuestEmails.includes(guest)`. The blacklist is lowercased at load time, but the user-supplied `guest` value is never normalized. In practice all major mail providers treat addresses case-insensitively, so: (1) a caller bypasses `BLACKLISTED_GUEST_EMAILS` by submitting `Evil@Attacker.com` when `evil@attacker.com` is blacklisted; (2) the same real mailbox can be re-added repeatedly as `user@x.com`, `User@x.com`, `USER@X.COM`, each time triggering a fresh booking email; (3) the downstream `newGuests.includes(attendee.email)` check in `sendAddGuestsEmails` inherits the same case-sensitivity. Normalize in the schema using `.transform((e) => e.toLowerCase())` and compare against `attendee.email.toLowerCase()` throughout.
```suggestion
// addGuests.schema.ts
guests: z.array(z.string().email().transform((e) => e.toLowerCase())).max(10),

// addGuests.handler.ts
const existing = new Set(booking.attendees.map((a) => a.email.toLowerCase()));
const uniqueGuests = [...new Set(guests.map((g) => g.toLowerCase()))].filter(
  (g) => !existing.has(g) && !blacklistedGuestEmails.includes(g)
);
```
[References: https://cwe.mitre.org/data/definitions/178.html]

:red_circle: [testing] Zero test coverage for the entire add-guests flow — authorization, dedup, blacklist, and email branching all ship untested in `packages/trpc/server/routers/viewer/bookings/addGuests.handler.ts`:1 (confidence: 95)
The PR adds 174 lines of new server logic across authorization (team admin/owner, organizer, attendee branches), deduplication (duplicate-attendee filtering), blacklist filtering (env-driven), database mutation (attendee creation), calendar sync (`eventManager.updateCalendarAttendees`), and differentiated email dispatch (scheduled vs. add-guests template) — and adds zero test files. The `isTeamAdminOrOwner` bug above would have been caught immediately by a "team admin but not owner can add guests" case. The mis-passed `guests` vs `uniqueGuests` bug would be caught by a "caller submits an already-existing attendee" case. The email-error-swallow policy is undocumented and would silently regress. Similarly, `packages/emails/email-manager.ts::sendAddGuestsEmails` has no tests for its new-guest-vs-existing-attendee branching, and `MultiEmail.tsx` / `AddGuestsDialog.tsx` have no component tests for their validation and add/remove behaviors.
```suggestion
// packages/trpc/server/routers/viewer/bookings/addGuests.handler.test.ts
import { describe, it, expect, vi } from "vitest";
import { addGuestsHandler } from "./addGuests.handler";
import { TRPCError } from "@trpc/server";

it("allows a team admin (non-owner) to add guests", async () => {
  vi.mocked(isTeamAdmin).mockResolvedValue(true);
  vi.mocked(isTeamOwner).mockResolvedValue(false);
  // ... set up booking with teamId and non-organizer caller ...
  const result = await addGuestsHandler({
    ctx: { user: { id: 5, email: "admin@example.com" } },
    input: { bookingId: 1, guests: ["guest@example.com"] },
  });
  expect(result.message).toBe("Guests added");
});

it("throws BAD_REQUEST when all submitted guests are already attendees", async () => {
  // booking.attendees already contains "already@example.com"
  await expect(
    addGuestsHandler({ ctx, input: { bookingId: 1, guests: ["already@example.com"] } })
  ).rejects.toThrow(TRPCError);
});

it("filters blacklisted guest emails case-insensitively", async () => {
  process.env.BLACKLISTED_GUEST_EMAILS = "bad@example.com";
  await expect(
    addGuestsHandler({ ctx, input: { bookingId: 1, guests: ["BAD@example.com"] } })
  ).rejects.toThrow(TRPCError);
});

it("routes existing attendees to AttendeeAddGuestsEmail and new guests to AttendeeScheduledEmail", async () => {
  // assert email-manager is called with the filtered uniqueGuests list
  expect(sendAddGuestsEmails).toHaveBeenCalledWith(
    expect.anything(),
    ["new@example.com"] // NOT the original mixed ["existing@example.com", "new@example.com"]
  );
});
```
FINDING_END

## Improvements

:yellow_circle: [correctness] `AddGuestsDialog.handleAdd` guard is dead code — `[""]` initial state always bypasses `length === 0` check in `apps/web/components/dialog/AddGuestsDialog.tsx`:101 (confidence: 88)
The component initializes state as `useState<string[]>([""])` — a one-element array with an empty string, not an empty array. The early-return guard `if (multiEmailValue.length === 0) { return; }` therefore never triggers under normal usage; clicking "Add" with no input runs Zod validation against `[""]`, fails, and shows the generic "emails must be unique and valid" error. This is confusing UX — a user who hasn't typed anything gets a validation error instead of either a no-op or a "please fill in at least one email" hint. It also means empty-string entries the user leaves behind (from clicking "add another" and never filling it) are sent to the server, which rejects them via Zod. Filter empty strings before validation and submit.
```suggestion
const handleAdd = () => {
  const filledEmails = multiEmailValue.map((e) => e.trim()).filter((e) => e.length > 0);
  if (filledEmails.length === 0) {
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

## Risk Metadata
Risk Score: 44/100 (MEDIUM) | Blast Radius: ~65 importers (ui/index.tsx barrel + email-manager.ts are high-fan-out; new files are 0-importer) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(9 additional findings below confidence threshold 85: Reply-To header leaks all attendee emails to every organizer/team-member recipient; email send errors swallowed with bare `console.log` losing audit trail; `eventManager.updateCalendarAttendees` method invocation is not verifiable from the diff and no definition is added in this PR; `addGuestsMutation.isPending` may be undefined if project is on React-Query v4 where `isLoading` is the correct property; `booking.userPrimaryEmail` field existence is not confirmed by any Prisma schema change in this PR; `MultiEmail` component has zero tests across its empty/readOnly/editable render branches; `AddGuestsDialog.handleAdd` validation paths are untested; `sendAddGuestsEmails` silent-swallow policy has no test documenting the contract; `ZAddGuestsInputSchema`'s `.refine` uniqueness check has no custom error message and no server-side enforcement.)
