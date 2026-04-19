## Summary
12 files changed, 82 lines added, 107 lines deleted. 8 findings (5 critical, 2 improvements, 1 nitpick).
Refactor converts `app-store` to eager dynamic-import Promises and propagates `async`/`await` through `getCalendar`, `getVideoAdapters`, and payment lookups — but three `forEach(async …)` blocks, an unawaited `getCalendar` inside `getCalendarCredentials`, and unprotected eager `import()` promises introduce real regressions in the booking/cancel path.

## Critical

:red_circle: [correctness] `forEach(async …)` swallows rejections and does not await deletes in `packages/app-store/vital/lib/reschedule.ts:122` (confidence: 95)
The callback passed to `bookingRefsFiltered.forEach` was changed from sync to `async` while the caller still relies on `forEach`. `Array.prototype.forEach` ignores the Promise returned by an async callback, so (a) the surrounding `try/catch` cannot catch rejections from `await getCalendar(...)` or `calendar?.deleteEvent(...)` — they become unhandled rejections (process-terminating on Node 15+), and (b) the outer function returns before any of the calendar deletes have actually been issued. The same anti-pattern was correctly avoided at `handleCancelBooking.ts:474` (rewritten as `for…of`), but this file was missed.
```suggestion
for (const bookingRef of bookingRefsFiltered) {
  if (bookingRef.uid) {
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      await calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
    } else if (bookingRef.type.endsWith("_video")) {
      await deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  }
}
```

:red_circle: [correctness] `forEach(async …)` swallows rejections and does not await deletes in `packages/app-store/wipemycalother/lib/reschedule.ts:122` (confidence: 95)
Identical regression to `vital/lib/reschedule.ts`: the reschedule loop iterates booking references via `forEach(async (bookingRef) => { … await getCalendar … })`. Calendar/video deletions now fire-and-forget, the outer `try/catch` is bypassed, and the function resolves before its side effects complete. Fix by converting to `for…of` (same shape as the already-correct `handleCancelBooking.ts:474` block).
```suggestion
for (const bookingRef of bookingRefsFiltered) {
  if (bookingRef.uid) {
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      await calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
    } else if (bookingRef.type.endsWith("_video")) {
      await deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  }
}
```

:red_circle: [correctness] `forEach(async …)` in tRPC reschedule path drops calendar-delete promises in `packages/trpc/server/routers/viewer/bookings.tsx:550` (confidence: 95)
Same root cause as the two `reschedule.ts` files — `bookingRefsFiltered.forEach(async (bookingRef) => { … await getCalendar … calendar?.deleteEvent(…) … })`. Because this path runs inside a tRPC mutation, the RPC will respond success while the calendar deletions are still pending (or silently rejected), leading to orphaned external calendar events and a silent data-integrity hazard visible to end users. Note the payment-app `await appStore[…]` block a few hundred lines down (line ~964) is correctly awaited; this forEach is the outlier.
```suggestion
for (const bookingRef of bookingRefsFiltered) {
  if (bookingRef.uid) {
    if (bookingRef.type.endsWith("_calendar")) {
      const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
      await calendar?.deleteEvent(
        bookingRef.uid,
        /* existing event args */
      );
    } else if (bookingRef.type.endsWith("_video")) {
      await deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
    }
  }
}
```

:red_circle: [correctness] Eagerly-started `import()` promises have no rejection handler — one broken app module crashes startup in `packages/app-store/index.ts:1-30` (confidence: 90)
Each value in the `appStore` object literal (`applecalendar: import("./applecalendar")`, …) is a Promise that begins executing at module-evaluation time. All ~28 promises start simultaneously the first time `packages/app-store` is imported anywhere in the process. No rejection handler is attached until a caller later does `await appStore[key]`. If any one of the 28 modules fails to load (e.g., missing peer dependency at import time, syntax error, module-level runtime throw), Node.js records an `unhandledRejection` *before* the first consumer has a chance to `await` it — which under Node 15+ default policy terminates the process. The previous `import *` approach failed loudly at boot with a stack trace; the new pattern fails silently until the rejection fires.
```suggestion
const appStore = {
  applecalendar: import("./applecalendar").catch((e) => {
    console.error("Failed to load app 'applecalendar':", e);
    return null;
  }),
  // …repeat for each entry, or refactor into a helper:
  // const safeImport = (name: string, p: Promise<unknown>) =>
  //   p.catch((e) => { console.error(`Failed to load app '${name}':`, e); return null; });
  // sylapsvideo: safeImport("sylapsvideo", import("./sylapsvideo")),
};
```

:red_circle: [correctness] `getCalendarCredentials` now returns a `calendar` field typed `Promise<Calendar|null>` — any non-awaiting consumer silently operates on a Promise in `packages/core/CalendarManager.ts:28-31` (confidence: 88)
Inside `getCalendarCredentials` the call was left as `const calendar = getCalendar(credential);` — no `await`. Because `getCalendar` is now `async`, each element in the returned array has `calendar: Promise<Calendar|null>` instead of `Calendar|null`. The diff updates `getConnectedCalendars` to destructure and `await item.calendar`, but `getCalendarCredentials` is a shared utility and any other caller that treats `calendar` as a resolved value (e.g., `item.calendar.createEvent(...)`) now calls a method on a Promise, producing `TypeError: calendar.createEvent is not a function`. This is especially dangerous because TypeScript inference will silently widen the return type rather than force a compile error at unchanged call sites. Either make `getCalendarCredentials` itself `async` and `await` internally, or annotate the return type explicitly so every caller is forced to handle the Promise.
```suggestion
export const getCalendarCredentials = async (credentials: Array<CredentialPayload>) => {
  const calendarCredentials = await Promise.all(
    credentials
      .filter((credential) => credential.type.endsWith("_calendar"))
      .flatMap(async (credential) => {
        const app = getApp(credential.type.split("_").join(""));
        if (!app) return [];
        const calendar = await getCalendar(credential);
        return app.variant === "calendar" ? [{ integration: app, credential, calendar }] : [];
      })
  );
  return calendarCredentials.flat();
};
```

## Improvements

:yellow_circle: [correctness] `await getCalendar` placed inside a pre-existing `forEach(async …)` that was not rewritten in `packages/features/bookings/lib/handleCancelBooking.ts:460` (confidence: 80)
At line 460 the PR updates `const calendar = getCalendar(credential);` to `const calendar = await getCalendar(credential);` but leaves the enclosing `.forEach(async (credential) => { … })` intact. The newly-added `await` buys nothing — the outer `forEach` still discards the returned Promise, so the recurring-event cleanup at this branch has the same fire-and-forget behavior as the three critical `forEach(async)` findings. The PR already demonstrates the correct fix a few lines down at line 474–480 (converting to `for…of`); apply the same rewrite here.
```suggestion
for (const credential of bookingToDelete.user.credentials.filter(
  (credential) => credential.type.endsWith("_calendar")
)) {
  const calendar = await getCalendar(credential);
  for (const updBooking of updatedBookings) {
    const bookingRef = updBooking.references.find((ref) => ref.type.includes("_calendar"));
    if (bookingRef) {
      await calendar?.deleteEvent(/* existing args */);
    }
  }
}
```

:yellow_circle: [performance/correctness] `getVideoAdapters` serializes dynamic-import awaits that could run in parallel in `packages/core/videoClient.ts:20-35` (confidence: 70)
The new implementation uses `for (const cred of withCredentials) { const app = await appStore[appName]; … }`, so each iteration awaits a dynamic-import Promise sequentially. After the first load these promises are resolved instantly (import cache), so steady-state cost is minimal — but the *first* request that triggers `getBusyVideoTimes` will pay the serial import latency for every credential. Since the imports are independent, build the list with `Promise.all`:
```suggestion
const getVideoAdapters = async (
  withCredentials: CredentialPayload[]
): Promise<VideoApiAdapter[]> => {
  const apps = await Promise.all(
    withCredentials.map(async (cred) => {
      const appName = cred.type.split("_").join("");
      const app = await appStore[appName as keyof typeof appStore];
      return { cred, app };
    })
  );
  return apps.flatMap(({ cred, app }) => {
    if (app && "lib" in app && "VideoApiAdapter" in app.lib) {
      const makeVideoApiAdapter = app.lib.VideoApiAdapter as VideoApiAdapterFactory;
      return [makeVideoApiAdapter(cred)];
    }
    return [];
  });
};
```

## Nitpicks

:white_circle: [correctness] Payment-app lookups don't guard against undefined `dirName` before indexing into `appStore` in `packages/features/bookings/lib/handleCancelBooking.ts:588` (confidence: 60)
`await appStore[paymentAppCredential?.app?.dirName as keyof typeof appStore]` — when `paymentAppCredential?.app?.dirName` is `undefined` the optional chain short-circuits, the cast hides the `undefined` from TypeScript, and `appStore[undefined]` resolves to `undefined`. The subsequent `"lib" in paymentApp` check does catch the undefined case here via a falsy check on `paymentApp`, so this is defensive-style only, but the same pattern in `bookings.tsx:964`, `deletePayment.ts`, and `handlePayment.ts` would benefit from an explicit narrow on `dirName` before the cast to avoid relying on JavaScript's `undefined in object` semantics.

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: core booking/cancel/payment path across 12 files; exported `getCalendar`, `getVideoAdapters`, `getCalendarCredentials` signatures change; affects every app-store consumer transitively. | Sensitive Paths: `packages/lib/payment/*` (payment), `packages/features/bookings/*` (booking lifecycle), `packages/trpc/server/routers/viewer/bookings.tsx` (RPC surface).
AI-Authored Likelihood: LOW — human-style refactor with inconsistent application (three `forEach` misses, one partial fix), matching the `techdebt/async-app-store-load` branch name and the terse human PR description.

Note: bundle-analysis bot reported "no changes to the javascript bundle" on this PR. For a refactor that switches ~28 static imports to dynamic `import()`, one would expect at minimum a code-splitting delta. Worth confirming with the author whether the dynamic imports are actually being chunked by webpack/SWC or whether the bundler is inlining them back (in which case the advertised lazy-loading benefit isn't being realized and only the async correctness hazards remain).
