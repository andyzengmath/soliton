## Summary
4 files changed, 111 lines added, 10 lines deleted. 15 findings (7 critical, 8 improvements, 0 nitpicks).
Copy-paste bug in working-hours check lets out-of-hours slots pass unchecked in `packages/trpc/server/routers/viewer/slots.ts`; multiple inverted predicates in the new date-override logic can mark unavailable slots as available (and vice-versa), enabling bookings outside the organizer's real availability.

## Critical

:red_circle: [correctness] Copy-paste bug — `end` duplicates `start` in working-hours check, allowing out-of-hours bookings in packages/trpc/server/routers/viewer/slots.ts:75 (confidence: 99)
Inside the `workingHours.find()` callback, both `start` and `end` are computed from `slotStartTime`:

```
const start = slotStartTime.hour() * 60 + slotStartTime.minute();
const end   = slotStartTime.hour() * 60 + slotStartTime.minute(); // identical
```

Because `end === start` always, the guard `end > workingHour.endTime` never triggers. A 60-minute event starting at 17:59 with `endTime=18:00` is not filtered out. The security agent independently identified a compounding issue: `slotStartTime` is expressed in UTC but compared directly against organizer-local minute-of-day values, so for any organizer not in UTC the comparison is additionally wrong by the organizer's UTC offset.
```suggestion
const start = slotStartTime.hour() * 60 + slotStartTime.minute();
const end   = slotEndTime.hour() * 60 + slotEndTime.minute();
// Additionally convert slotStartTime/slotEndTime into organizerTimeZone
// before comparing against workingHour.startTime/endTime (which are minute-of-day
// in organizer-local), and validate organizerTimeZone is a known IANA zone.
```
[References: OWASP A04, CWE-840, CWE-20]

:red_circle: [correctness] Dayjs objects compared with `===` — zero-duration "day off" override silently ignored in packages/trpc/server/routers/viewer/slots.ts:75 (confidence: 97)
The condition:

```
if (dayjs(date.start).add(utcOffset, "minutes") === dayjs(date.end).add(utcOffset, "minutes"))
```

compares two distinct object references that are never the same pointer, so the equality is always `false`. A zero-duration date override intended to mark a full day off is silently skipped, meaning the organizer's "day off" override has no effect and slots remain available.
```suggestion
if (dayjs(date.start).add(utcOffset, "minutes").isSame(dayjs(date.end).add(utcOffset, "minutes"))) {
  return true;
}
```

:red_circle: [correctness] Inverted UTC offset sign shifts date override times in the wrong direction in packages/trpc/server/routers/viewer/slots.ts:75 (confidence: 95)
The UTC offset for date override comparison is negated:

```
const utcOffset = organizerTimeZone
  ? dayjs.tz(date.start, organizerTimeZone).utcOffset() * -1
  : 0;
```

The `* -1` causes organizer date override boundaries to shift in the opposite direction from intended. For example, a UTC+5 organizer's overrides would be treated as if they were UTC-5.
```suggestion
const utcOffset = organizerTimeZone
  ? dayjs.tz(date.start, organizerTimeZone).utcOffset()
  : 0;
```

:red_circle: [correctness] Split-shift working hours rejected incorrectly — `find()` short-circuits on first non-matching window in packages/trpc/server/routers/viewer/slots.ts:105 (confidence: 97)
When an organizer has split working hours (e.g. 09:00–12:00 and 14:00–18:00), `workingHours.find()` short-circuits on the first window. A slot at 15:00 is rejected because it falls outside 09:00–12:00, even though it falls within 14:00–18:00. The find callback currently returns `true` on a non-match (triggering the outer `return false` unavailable branch), rather than searching for a window that accepts the slot.
```suggestion
if (
  workingHours.length > 0 &&
  !workingHours.some((workingHour) => {
    if (!workingHour.days.includes(slotStartTime.day())) return false;
    const start = slotStartTime.hour() * 60 + slotStartTime.minute();
    const end   = slotEndTime.hour() * 60 + slotEndTime.minute();
    return start >= workingHour.startTime && end <= workingHour.endTime;
  })
) {
  return false; // no working-hour window accepts this slot
}
```

:red_circle: [security] Inverted date-override logic allows booking into blocked ranges on override days in packages/trpc/server/routers/viewer/slots.ts:100 (confidence: 82)
In the `dateOverrides.find()` callback, the return logic is inverted: when a slot lies OUTSIDE an override window the callback returns `true`, which causes the outer logic to mark the slot unavailable. When a slot lies INSIDE an override window the callback returns `undefined` (falls through), which lands at `if (dateOverrideExist) return true` and marks the slot available — crucially skipping the busy-time check below. On days that have any date override, attendees can book into already-blocked/booked ranges, enabling double-booking.
```suggestion
const overridesForDay = dateOverrides.filter((date) => {
  const utcOffset = organizerTimeZone ? dayjs.tz(date.start, organizerTimeZone).utcOffset() : 0;
  return dayjs(date.start).add(utcOffset, "minutes").format("YYYY-MM-DD") ===
         slotStartTime.format("YYYY-MM-DD");
});

if (overridesForDay.length > 0) {
  const fitsAny = overridesForDay.some((date) => {
    const utcOffset = organizerTimeZone ? dayjs.tz(date.start, organizerTimeZone).utcOffset() : 0;
    const start = dayjs(date.start).add(utcOffset, "minutes");
    const end   = dayjs(date.end).add(utcOffset, "minutes");
    if (start.isSame(end)) return false; // zero-length = blocked day
    return !slotStartTime.isBefore(start) && !slotEndTime.isAfter(end);
  });
  if (!fitsAny) return false;
  // fall through and still run the busy-time check below
}
```
[References: OWASP A04, CWE-697, CWE-841]

:red_circle: [testing] Round-robin fixed host date override path has zero test coverage in packages/trpc/server/routers/viewer/slots.ts:1 (confidence: 98)
The primary bug targeted by this PR (round-robin fixed host date override, issue #8207) was merged without tests. The PR author explicitly noted: *"Tests for the fix of #8207 are still missing. I am working on it."* No test exercises a `ROUND_ROBIN` event type combined with a fixed host and a date override. All four critical logic bugs above therefore have no automated regression guard.
```suggestion
// In apps/web/test/lib/getSchedule.test.ts
test("round-robin with fixed host and date override only offers override slots", async () => {
  // Build a ROUND_ROBIN event whose fixed host has a dateOverride on `plus2DateString`
  // that restricts availability to 10:00-12:00 UTC even though working hours are 09:00-17:00.
  const result = await getSchedule(/* … */, ctx);
  expect(result).toHaveTimeSlots(
    ["10:00:00.000Z", "11:00:00.000Z"],
    { dateString: plus2DateString }
  );
  expect(result).not.toHaveTimeSlots(["09:00:00.000Z"], { dateString: plus2DateString });
});
```

:red_circle: [testing] Out-of-hours slot rejection is untested — working-hours guard is never negatively validated in packages/trpc/server/routers/viewer/slots.ts:1 (confidence: 95)
No test requests a slot that falls outside working hours and asserts it is absent from results. The only new test added by this PR confirms that valid slots are returned; it does not assert that invalid slots are rejected. A test requesting a slot at 22:00–24:00 UTC would immediately expose the `end` copy-paste bug identified above.
```suggestion
test("slot requested outside working hours returns no availability", async () => {
  const out = await getSchedule({
    eventTypeId: 1,
    eventTypeSlug: "",
    startTime: `${plus1DateString}T22:00:00.000Z`,
    endTime: `${plus1DateString}T23:59:59.999Z`,
    timeZone: "UTC",
  }, ctx);
  expect(out).toHaveTimeSlots([], { dateString: plus1DateString });
});
```

## Improvements

:yellow_circle: [consistency] Repeated recomputation of `dayjs(date.start).add(utcOffset, "minutes")` across the same callback in packages/trpc/server/routers/viewer/slots.ts:75 (confidence: 90)
The expression `dayjs(date.start).add(utcOffset, "minutes")` (and its `date.end` counterpart) is evaluated 5+ times within the same `dateOverrides.find()` callback iteration, creating unnecessary object allocations and making the code harder to read.
```suggestion
const adjustedStart = dayjs(date.start).add(utcOffset, "minutes");
const adjustedEnd   = dayjs(date.end).add(utcOffset, "minutes");
// then reference adjustedStart / adjustedEnd throughout
```

:yellow_circle: [consistency] Side-effect mutation inside `Array.find()` callback in packages/trpc/server/routers/viewer/slots.ts:75 (confidence: 85)
`dateOverrideExist = true` is set as a side effect inside `dateOverrides.find()`. Using `find()` for side effects rather than returning a predicate value is a misuse of the API and makes control flow harder to reason about.
```suggestion
const dayHasOverride = dateOverrides.some((date) => isSameLocalDay(date, slotStartTime, organizerTimeZone));
const slotFitsInAnyOverride = dateOverrides.some((date) =>
  slotWithinOverride(date, slotStartTime, slotEndTime, organizerTimeZone)
);
if (dayHasOverride && !slotFitsInAnyOverride) return false;
```

:yellow_circle: [security] Attendee-controlled timezone not validated; Date stringification is DST-unsafe in packages/lib/slots.ts:208 (confidence: 72, retained — distinct file and OWASP-tagged)
`offset = inviteeUtcOffset - organizerUtcOffset` uses the attendee-supplied `input.timeZone` value, which is not validated against an IANA allowlist at the tRPC boundary. An adversarially chosen zone can shift override window masking. Additionally, `dayjs(override.start.toString()).tz(...)` stringifies a `Date` object before passing it to dayjs, which loses timezone context and is unsafe around DST transitions (spring-forward / fall-back).
```suggestion
// At the tRPC input boundary:
const getScheduleSchema = z.object({
  // … existing fields
  timeZone: z.string().refine((tz) => isValidIANA(tz), "Invalid IANA timezone"),
});

// In getSlots / checkIfIsAvailable, drop the .toString():
const organizerUtcOffset = dayjs.tz(override.start, override.timeZone).utcOffset();
const inviteeUtcOffset   = dayjs.tz(override.start, timeZone).utcOffset();
```
[References: OWASP A04, A03, CWE-20]

:yellow_circle: [consistency] Repeated `dayjs()` instantiation for identical datetime values per iteration in packages/lib/slots.ts:210 (confidence: 80)
`dayjs(override.start).utc().add(offset, "minute")` and `dayjs(override.end).utc().add(offset, "minute")` are each called twice per loop iteration.
```suggestion
const startUtc = dayjs(override.start).utc().add(offset, "minute");
const endUtc   = dayjs(override.end).utc().add(offset, "minute");
return {
  userIds: override.userId ? [override.userId] : [],
  startTime: startUtc.hour() * 60 + startUtc.minute(),
  endTime:   endUtc.hour()   * 60 + endUtc.minute(),
};
```

:yellow_circle: [testing] Negative UTC offset timezones not tested for date overrides in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 92)
The new tests only exercise UTC+6:00. Negative-offset zones such as America/Los_Angeles or America/New_York have the local calendar date behind UTC, creating an asymmetry in date-override day-matching that is entirely untested.
```suggestion
test("date override with negative-offset attendee timezone", async () => {
  // Mirror the +6 test using America/New_York (UTC-5 in winter).
  // Assert the correct UTC-shifted slots are returned.
});
```

:yellow_circle: [testing] Midnight-boundary date overrides not tested for negative-offset timezones in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 90)
Negative UTC offsets straddle UTC midnight differently from positive offsets; an override on a local "Monday" may span two UTC days. This boundary case is not tested.
```suggestion
test("override on LA-local date straddles UTC midnight", async () => {
  // Set organizer in America/Los_Angeles with an override on plus1 local date.
  // Query from plus1 T00:00Z (which is still plus0 in LA) and assert the
  // override slots appear only in the LA-local day window.
});
```

:yellow_circle: [testing] DST-transitioning dates not tested for date overrides in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 85)
`utcOffset` is captured as a point-in-time snapshot. On DST transition days the UTC offset changes mid-day, which can cause date override start/end boundaries computed from a pre-transition offset to be off by one hour for the post-transition portion of the day. No DST-day test exists.
```suggestion
test("override spanning DST spring-forward day", async () => {
  // America/New_York, 2024-03-10 — clocks jump 02:00 -> 03:00 local.
  // Assert override slots throughout the day have correct UTC boundaries.
});
```

:yellow_circle: [testing] `checkIfIsAvailable` has no direct unit tests — only indirect integration coverage in packages/trpc/server/routers/viewer/slots.ts:1 (confidence: 82)
The function is exercised only through the full `getSchedule` integration path. Direct unit tests would isolate the `dateOverride` and `workingHours` branches, making it far easier to catch and pin down the bugs identified above.
```suggestion
// Create packages/trpc/server/routers/viewer/slots.test.ts with direct
// unit tests for checkIfIsAvailable covering:
//   - slot inside working hours
//   - slot outside working hours (would catch the `end` copy-paste)
//   - slot on override day, inside override window
//   - slot on override day, outside override window (would catch the inversion)
//   - zero-duration override (would catch the === bug)
//   - split-shift working hours (would catch the find() short-circuit)
```

:yellow_circle: [testing] Zero-length (start === end) date override not tested in apps/web/test/lib/getSchedule.test.ts:1 (confidence: 80)
A zero-duration date override is a degenerate input that the code does not guard against (the `===` object-reference bug identified above means it is also silently mishandled). No test covers this case.
```suggestion
test("zero-length date override blocks the entire day", async () => {
  // Configure a dateOverride where start and end timestamps are identical.
  // Assert the whole day returns no available slots (or whichever behavior
  // is explicitly intended — document the decision).
});
```

## Risk Metadata
Risk Score: 31/100 (MEDIUM) | Blast Radius: core scheduling primitives (`packages/lib/slots.ts`, `packages/trpc/server/routers/viewer/slots.ts`) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(1 additional nitpick finding below the confidence threshold of 80 was suppressed.)

**Recommendation:** request-changes — seven critical findings across correctness, security-impacting business-logic inversion, and test-coverage gaps. The PR was merged in this state; these findings represent follow-up work needed to make the fix correct and safe.
