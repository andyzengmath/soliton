## Summary
4 files changed, 111 lines added, 10 lines deleted. 8 findings (4 critical, 4 improvements).
Date-override timezone fix ships with multiple latent correctness bugs in the new `checkIfIsAvailable` logic, and the primary stated fix (round-robin fixed-host overrides, #8207) is merged with zero test coverage — the author's own comment acknowledged the gap.

## Critical

:red_circle: [correctness] Working-hours check uses `slotStartTime` for both `start` and `end` in `packages/trpc/server/routers/viewer/slots.ts`:140 (confidence: 99)
Both variables are computed from `slotStartTime.hour()*60 + slotStartTime.minute()`, so `end` can never differ from `start` and the `end > workingHour.endTime` condition is effectively dead. A slot that starts inside working hours but extends past them will incorrectly pass the availability check.
```suggestion
        const start = slotStartTime.hour() * 60 + slotStartTime.minute();
        const end = slotEndTime.hour() * 60 + slotEndTime.minute();
        if (start < workingHour.startTime || end > workingHour.endTime) {
          return true;
        }
```

:red_circle: [correctness] `dayjs(...) === dayjs(...)` compares object identity, never true in `packages/trpc/server/routers/viewer/slots.ts`:112 (confidence: 99)
The guard `if (dayjs(date.start).add(utcOffset,"minutes") === dayjs(date.end).add(utcOffset,"minutes"))` is dead code — two distinct dayjs wrappers are never strict-equal, so the intended "full-day block" early-return never fires and fully blocked days can leak bookable slots.
```suggestion
        if (dayjs(date.start).add(utcOffset, "minutes").isSame(dayjs(date.end).add(utcOffset, "minutes"))) {
          return true;
        }
```

:red_circle: [correctness] Working-hours `.find()` callback returns `undefined` for non-matching days, silently treating them as available in `packages/trpc/server/routers/viewer/slots.ts`:137 (confidence: 95)
When `slotStartTime.day()` isn't included in any `workingHour.days` (e.g., Sunday for a Mon–Fri schedule), every callback invocation returns `undefined`, `.find()` returns `undefined`, the outer `if` is false, and the slot falls through to the busy check as if it were inside working hours.
```suggestion
  const withinWorkingHours = workingHours.some((workingHour) => {
    if (!workingHour.days.includes(slotStartTime.day())) return false;
    const start = slotStartTime.hour() * 60 + slotStartTime.minute();
    const end = slotEndTime.hour() * 60 + slotEndTime.minute();
    return start >= workingHour.startTime && end <= workingHour.endTime;
  });
  if (workingHours.length > 0 && !withinWorkingHours) {
    return false;
  }
```

:red_circle: [testing] Round-robin fixed-host date-override fix (#8207) has zero test coverage in `apps/web/test/lib/getSchedule.test.ts`:784 (confidence: 98)
The PR's headline fix — preventing round-robin events from being bookable when a fixed host has a blocking date override — touches three call sites in `getSchedule` and the entire new `checkIfIsAvailable` branch, but no assertion exercises a `schedulingType: "ROUND_ROBIN"` scenario with a fixed host. The author explicitly noted "Tests for the fix of #8207 are still missing. I am working on it," and the PR was merged without them.
```suggestion
    test("fixed host date override blocks round-robin slots", async () => {
      // schedulingType: "ROUND_ROBIN" with one isFixed:true host whose
      // dateOverride spans 10:00–12:00 UTC; assert those slots are absent
      // while surrounding slots remain available.
    });
```

## Improvements

:yellow_circle: [correctness] Side-effect mutation of `dateOverrideExist` inside `.find()` predicate in `packages/trpc/server/routers/viewer/slots.ts`:99 (confidence: 85)
`.find()` short-circuits on the first truthy return, so if the first same-day override is a blocking one, `.find()` stops iterating and a later override on the same day that *would* cover the slot is never seen — the slot is rejected incorrectly. The flag also makes the two-phase logic dependent on iteration order.
```suggestion
  const dayOverrides = dateOverrides.filter((date) => {
    const utcOffset = organizerTimeZone ? dayjs.tz(date.start, organizerTimeZone).utcOffset() * -1 : 0;
    return dayjs(date.start).add(utcOffset, "minutes").format("YYYY MM DD") === slotStartTime.format("YYYY MM DD");
  });
  if (dayOverrides.length) {
    const covered = dayOverrides.some((date) => { /* check slot is inside this override */ });
    return covered;
  }
```

:yellow_circle: [correctness] `override.start.toString()` is locale-dependent and may mis-parse in dayjs in `packages/lib/slots.ts`:210 (confidence: 82)
`Date.prototype.toString()` returns a host-locale string (e.g., `"Sat Apr 19 2026 10:00:00 GMT+0530 (IST)"`). dayjs parses this without an explicit format and may interpret the embedded offset inconsistently across runtimes, corrupting the `utcOffset` used for the slot shift. Pass the `Date` directly so dayjs treats it as a UTC epoch.
```suggestion
      const organizerUtcOffset = dayjs(override.start).tz(override.timeZone).utcOffset();
      const inviteeUtcOffset = dayjs(override.start).tz(timeZone).utcOffset();
```

:yellow_circle: [testing] Timezone coverage limited to `+6:00`; negative offsets and midnight-crossing untested in `apps/web/test/lib/getSchedule.test.ts`:787 (confidence: 90)
The only new assertion uses `Timezones["+6:00"]`, making `inviteeUtcOffset − organizerUtcOffset` positive. Negative-offset invitees (e.g., `America/New_York` = UTC-5) exercise the opposite arithmetic branch and can shift slots backward across midnight, which is a distinct edge case the current test does not cover.
```suggestion
      // Add an assertion with a negative-offset invitee timezone
      // (e.g., "America/New_York") to exercise the negative offset branch
      // and a midnight-crossing case where the shifted slot lands on
      // the previous calendar day.
```

:yellow_circle: [testing] Full-day date override (`start === end`) branch untested in `apps/web/test/lib/getSchedule.test.ts` (confidence: 85)
The new `checkIfIsAvailable` has an explicit short-circuit for full-day blocks, but no test covers it. A test here would also incidentally catch the `===`-on-dayjs reference-equality bug flagged above.
```suggestion
    test("full-day date override blocks all slots on that day", async () => {
      // set up an override where start and end resolve to the same instant
      // and assert getSchedule returns zero slots for that day
    });
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: booking availability for all round-robin events and any user with a date override (core scheduling path) | Sensitive Paths: none hit
AI-Authored Likelihood: LOW
