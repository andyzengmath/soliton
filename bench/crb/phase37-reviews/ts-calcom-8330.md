## Summary
4 files changed, 111 lines added, 10 lines deleted. 10 findings (8 critical, 2 improvements, 0 nitpicks).
Multiple correctness bugs in `checkIfIsAvailable` (dayjs `===` reference equality, copy-paste `end = slotStartTime`, inverted `utcOffset * -1`) plus an availability-bypass that skips `busy[]` when a date override is active, and the round-robin fixed-host path — the headline fix — ships entirely untested.

## Critical
:red_circle: [correctness] Dayjs `===` reference equality in full-day-override guard is always false in packages/trpc/server/routers/viewer/slots.ts:112 (confidence: 99)
`dayjs(date.start).add(utcOffset, "minutes") === dayjs(date.end).add(utcOffset, "minutes")` compares two freshly constructed dayjs object references. In JavaScript, two distinct object instances are never reference-equal, so this condition is permanently `false`. The intent is to detect a "full-day unavailable" override where start equals end (a convention cal.com uses to represent all-day blocks); because the guard is dead code, such overrides are never matched and the code falls through to boundary checks that may incorrectly allow slots on fully-blocked days. Matches the `.isSame()` / `.isBefore()` idiom used two lines below, confirming the `===` was unintentional.
```suggestion
if (dayjs(date.start).add(utcOffset, "minutes").isSame(dayjs(date.end).add(utcOffset, "minutes"))) {
  return true;
}
```

:red_circle: [correctness] Copy-paste: `end` uses `slotStartTime` instead of `slotEndTime`, making the end-of-working-hours check a no-op in packages/trpc/server/routers/viewer/slots.ts:139 (confidence: 99)
Both `start` and `end` are computed from `slotStartTime.hour() * 60 + slotStartTime.minute()`, so `start === end` by construction. The subsequent predicate `end > workingHour.endTime` therefore collapses to `start > workingHour.endTime` and never catches a slot that starts inside working hours but ends outside them. Concretely, with `workingHour.endTime = 1080` (18:00) and a 60-minute slot starting at 17:45, `end = 1065 < 1080` passes, letting the slot run to 18:45 — 45 minutes past the advertised end of the workday.
```suggestion
const start = slotStartTime.hour() * 60 + slotStartTime.minute();
const end = slotEndTime.hour() * 60 + slotEndTime.minute();
if (start < workingHour.startTime || end > workingHour.endTime) {
  return true;
}
```

:red_circle: [correctness] `utcOffset * -1` negation converts UTC→local in the wrong direction for non-UTC organizers in packages/trpc/server/routers/viewer/slots.ts:105 (confidence: 97)
`dayjs.tz(date.start, organizerTimeZone).utcOffset()` already returns a signed offset in minutes (positive east of UTC). To shift a UTC instant into local time you add that offset; negating it subtracts, so for an organizer in UTC+5 the date-boundary comparison on line 108 is performed as if the override were 5 hours earlier than it actually is. That misplaces the `YYYY MM DD` match onto the wrong calendar day for every non-UTC organizer, silently hiding overrides or matching them on an adjacent day. Avoiding manual offset arithmetic and using `.tz(organizerTimeZone)` directly is both simpler and less error-prone.
```suggestion
const dateStartInOrganizerTz = organizerTimeZone
  ? dayjs(date.start).tz(organizerTimeZone)
  : dayjs(date.start).utc();
const slotStartInOrganizerTz = organizerTimeZone
  ? slotStartTime.tz(organizerTimeZone)
  : slotStartTime;
if (dateStartInOrganizerTz.format("YYYY MM DD") === slotStartInOrganizerTz.format("YYYY MM DD")) {
  // … compare boundaries also in organizer tz
}
```

:red_circle: [correctness] Override offset math produces a synthetic timezone rather than the invitee's local time in packages/lib/slots.ts:44 (confidence: 92)
The new code computes `offset = inviteeUtcOffset - organizerUtcOffset` and then does `dayjs(override.start).utc().add(offset, "minute")`. Adding the *difference* of two offsets to a UTC instant does not yield the time in either party's local zone — it yields a time in a synthetic zone whose offset from UTC is that difference. Example: organizer UTC+1 (offset 60), invitee UTC+9 (offset 540) → `offset = 480`, shifting UTC by 480 min = UTC+8, not UTC+9. Correct results only occur in the degenerate case where the organizer is already in UTC. The intent is simply to express `override.start`/`override.end` as minutes-since-midnight in the invitee's zone, which `dayjs(...).tz(timeZone)` does directly.
```suggestion
const overrides = activeOverrides.flatMap((override) => ({
  userIds: override.userId ? [override.userId] : [],
  startTime:
    dayjs(override.start).tz(timeZone).hour() * 60 +
    dayjs(override.start).tz(timeZone).minute(),
  endTime:
    dayjs(override.end).tz(timeZone).hour() * 60 +
    dayjs(override.end).tz(timeZone).minute(),
}));
```

:red_circle: [security] Date-override branch returns available without consulting `busy[]`, enabling double-booking in packages/trpc/server/routers/viewer/slots.ts:131 (confidence: 88)
When `dateOverrideExist` is true and the slot lies within the override window, `checkIfIsAvailable` returns `true` before reaching the `busy.every(...)` block that is the only check against already-confirmed bookings and external-calendar conflicts. `slots.getSchedule` is called from public booking pages (unauthenticated attendees), so any organizer who has both an existing confirmed booking and a date override on the same day will advertise that booked slot as available. If the downstream `book.event` mutation shares this predicate, an attendee lands a genuinely conflicting booking; even if it re-validates, the public API still leaks a misleading availability surface and widens TOCTOU windows between list and book. Date overrides should widen/narrow *permitted* hours, not override conflict detection.
```suggestion
// Always run the busy check — overrides never imply a free slot, only an allowed window.
let dateOverrideExist = false;
let slotWithinOverride = true;
for (const date of dateOverrides) {
  const overrideStart = organizerTimeZone
    ? dayjs(date.start).tz(organizerTimeZone)
    : dayjs(date.start).utc();
  const overrideEnd = organizerTimeZone
    ? dayjs(date.end).tz(organizerTimeZone)
    : dayjs(date.end).utc();
  const slotStartInTz = organizerTimeZone ? slotStartTime.tz(organizerTimeZone) : slotStartTime;
  if (overrideStart.format("YYYY MM DD") === slotStartInTz.format("YYYY MM DD")) {
    dateOverrideExist = true;
    if (overrideStart.isSame(overrideEnd)) return false;
    if (slotEndTime.isBefore(overrideStart) || slotStartTime.isAfter(overrideEnd)) {
      slotWithinOverride = false;
    }
    break;
  }
}
if (dateOverrideExist && !slotWithinOverride) return false;
if (!dateOverrideExist) { /* existing workingHours check */ }
return busy.every(/* unchanged busy predicate */);
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/841.html]

:red_circle: [testing] Round-robin fixed-host date-override path — the primary fix of this PR — has zero test coverage in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 98)
The PR body acknowledges "Tests for the fix of #8207 are still missing. I am working on it." The only new test covers the *timezone* fix in isolation (personal event, +6:00 invitee). The round-robin fixed-host path — where the PR adds per-user `schedule.timeZone` plumbing through `checkIfIsAvailable` and skips slots where the fixed host is unavailable — has no assertion at all. A regression in this path would be invisible to CI.
```suggestion
it("excludes slots where a round-robin fixed host has a date override making them unavailable", async () => {
  // Arrange: RR event with 1 fixed host + 1 RR host, both with 09–17 working hours.
  // Fixed host has a date override on plus2Date blocking 10:00–11:00.
  const schedule = await getSchedule(
    { eventTypeId: RR_FIXED_HOST_EVENT_ID, eventTypeSlug: "", startTime: `${plus1DateString}T00:00:00.000Z`, endTime: `${plus2DateString}T23:59:59.999Z`, timeZone: "UTC" },
    ctx
  );
  expect(schedule).not.toHaveTimeSlots(["10:00:00.000Z"], { dateString: plus2DateString });
  expect(schedule).toHaveTimeSlots(["09:00:00.000Z", "11:00:00.000Z"], { dateString: plus2DateString });
});
```

:red_circle: [testing] New offset arithmetic in packages/lib/slots.ts has no DST-boundary test in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 95)
The new `organizerUtcOffset` / `inviteeUtcOffset` subtraction is exactly the shape of code that silently off-by-one-hours across DST transitions. The single added test uses `Timezones["+6:00"]` (Asia/Dhaka, non-DST) so neither party crosses a DST boundary in any scenario. A host in Europe/London scheduling an override near the March/October clock-change will hit this arithmetic with no regression check.
```suggestion
it("positions override correctly when organizer is in a DST-observing zone (spring forward)", async () => {
  // organizer Europe/London, invitee UTC, override on the 2023-03-26 transition day
  const schedule = await getSchedule(
    { eventTypeId: EVENT_IN_EUROPE_LONDON, eventTypeSlug: "", startTime: `2023-03-26T00:00:00.000Z`, endTime: `2023-03-26T23:59:59.999Z`, timeZone: "UTC" },
    ctx
  );
  expect(schedule).toHaveTimeSlots([/* expected UTC slots after BST offset */], { dateString: "2023-03-26" });
});
```

:red_circle: [testing] Cross-midnight override window is untested in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 90)
When a date override spans midnight (e.g. 22:00 → 02:00 next day), the override's `end` is numerically earlier-of-day than its `start`. The new day-of comparison `dayjs(date.start).format("YYYY MM DD") === slotStartTime.format("YYYY MM DD")` matches only one of the two calendar days, so slots on the *other* side of midnight are handled by whichever branch the day-match happens to fall on — with the busy-bypass bug above, that can silently mark a cross-midnight day as "override day, slot available."
```suggestion
it("handles date overrides that span midnight", async () => {
  // Override: 22:00 UTC day N → 02:00 UTC day N+1
  const schedule = await getSchedule(
    { eventTypeId: CROSS_MIDNIGHT_OVERRIDE_EVENT_ID, eventTypeSlug: "", startTime: `${dayN}T21:00:00.000Z`, endTime: `${dayNplus1}T03:00:00.000Z`, timeZone: "UTC" },
    ctx
  );
  expect(schedule).toHaveTimeSlots(["22:00:00.000Z", "23:00:00.000Z"], { dateString: dayN });
  expect(schedule).toHaveTimeSlots(["00:00:00.000Z", "01:00:00.000Z"], { dateString: dayNplus1 });
});
```

## Improvements
:yellow_circle: [consistency] `.find()` callback mutates outer `dateOverrideExist` as a side effect with implicit-undefined returns in packages/trpc/server/routers/viewer/slots.ts:104 (confidence: 90)
The outer `if (dateOverrides.find(...))` is being used as a boolean predicate, but the callback also mutates `dateOverrideExist` in the enclosing scope and falls off the end (returning `undefined`) on non-matching branches. This conflates search-for-match with flag-setting, makes the truthy/undefined return contract hard to audit, and leaves a subtle dependence on array ordering (the first override whose day matches wins, but `dateOverrideExist` may or may not be set depending on which branch the callback exited). Prefer an explicit `for…of` that produces both pieces of state cleanly.
```suggestion
let dateOverrideExist = false;
let slotOutsideOverride = false;
for (const date of dateOverrides) {
  // … same checks as before, assigning booleans explicitly and breaking on day-match
}
if (slotOutsideOverride) return false;
if (dateOverrideExist) return true;
```

:yellow_circle: [testing] workingHours boundary branch in `checkIfIsAvailable` has no focused test in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 88)
The new `workingHours.find(...)` branch is distinct from the date-override branch and governs whether a slot is rejected for falling outside the organizer's normal working hours. The existing integration test does not exercise either end-of-day rejection or the override-supersedes-workingHours interaction — which is exactly where the end/start copy-paste bug sits — so the fix-forward even after addressing that bug has no regression check.
```suggestion
it("rejects a slot that starts in working hours but ends after them", async () => {
  // workingHours 09:00–17:00; request a 60-minute slot at 16:30
  const schedule = await getSchedule(/* … */);
  expect(schedule).not.toHaveTimeSlots(["16:30:00.000Z"], { dateString: targetDate });
});
it("allows a slot outside normal working hours when covered by a date override", async () => {
  // workingHours 09:00–17:00; override 07:00–20:00
  const schedule = await getSchedule(/* … */);
  expect(schedule).toHaveTimeSlots(["18:30:00.000Z"], { dateString: targetDate });
});
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: ~14 importers across packages/lib, packages/trpc, packages/types (capped 100) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold of 85)
