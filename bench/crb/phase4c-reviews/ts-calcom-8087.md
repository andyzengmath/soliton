## Summary
12 files changed, 82 lines added, 107 lines deleted. 6 findings (2 critical, 3 improvements, 1 nitpick).
Refactor converts eager `import * as X` to dynamic `import("X")` in `packages/app-store/index.ts`, cascading `async`/`await` through every caller of `appStore[...]`, `getCalendar()`, and `getVideoAdapters()`. Mostly mechanical, but three of the call-site conversions use `Array.prototype.forEach(async …)`, which silently discards the returned promise — so the new `await getCalendar(...)` calls do not propagate errors or block the surrounding flow.

## Critical

:red_circle: [correctness] `forEach(async …)` drops awaited promises and swallows errors in `getCalendar` in `packages/app-store/vital/lib/reschedule.ts`:125 (confidence: 95)
`bookingRefsFiltered.forEach(async (bookingRef) => { … const calendar = await getCalendar(…); return calendar?.deleteEvent(…); })` — `Array.forEach` ignores the promise returned by an async callback. The outer `try/catch` in `Reschedule` can no longer catch a rejection from `getCalendar` (e.g., a failed dynamic `import()` of an app package), which now becomes an unhandled promise rejection. The outer function also returns before the per-ref deletes finish, so downstream code that depended on the reschedule cleanup completing (bookings state updates) runs against a not-yet-cleaned-up calendar. Previously `getCalendar` was synchronous so this anti-pattern was masked; making it async surfaces the bug. Apply the same fix to `packages/app-store/wipemycalother/lib/reschedule.ts`:125 and `packages/trpc/server/routers/viewer/bookings.tsx`:553, which have the identical pattern.
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

:red_circle: [correctness] Pre-refactor branch's `forEach(async …)` no longer awaits recurring-event calendar deletes in `packages/features/bookings/lib/handleCancelBooking.ts`:460 (confidence: 90)
Inside the recurring-event cleanup block, `.filter(...).forEach(async (credential) => { const calendar = await getCalendar(credential); for (const updBooking of updatedBookings) { … calendar?.deleteEvent(bookingRef.uid, …) } })`. The outer `forEach` drops the async callback's promise, so the surrounding `handler` continues (responding to the user and running subsequent Stripe/payment logic further down) while these deletes are still in flight. Pre-PR, `getCalendar` was synchronous, so the loop body ran to completion and only the returned `deleteEvent` promises were fire-and-forget; post-PR the whole iteration is fire-and-forget, including the `await` on `getCalendar`. A failed dynamic import of the calendar app becomes an unhandled rejection. The sibling branch 14 lines below (lines 474-482) was correctly converted to `for (const credential of calendarCredentials)` — the same conversion should apply here.
```suggestion
          for (const credential of bookingToDelete.user.credentials.filter(
            (credential) => credential.type.endsWith("_calendar")
          )) {
            const calendar = await getCalendar(credential);
            for (const updBooking of updatedBookings) {
              const bookingRef = updBooking.references.find((ref) => ref.type.includes("_calendar"));
              if (bookingRef) {
                await calendar?.deleteEvent(
                  bookingRef.uid,
                  updatedEvt,
                  bookingRef.externalCalendarId
                );
              }
            }
          }
```

## Improvements

:yellow_circle: [correctness] `getCalendarCredentials` now stores a `Promise<Calendar|null>` in the `calendar` field without updating its type in `packages/core/CalendarManager.ts`:29 (confidence: 80)
The unchanged line `const calendar = getCalendar(credential);` used to produce `Calendar | null`; post-refactor it produces `Promise<Calendar | null>`. `getConnectedCalendars` (line 47) was updated to `const calendar = await item.calendar;`, but TypeScript will infer the tuple's `calendar` field as `Calendar | null` from the legacy type (or leave it implicit) and any consumer that treats it synchronously will now silently get a Promise. Either `await` inside `getCalendarCredentials` or explicitly type the return as `{ integration, credential, calendar: Promise<Calendar | null> }` so the compiler catches future misuses.
```suggestion
      const credentials = apps.flatMap((app) => {
        const credentialsList = credentials.filter((c) => c.type === app.type);
        return credentialsList.map((credential) => {
          const calendar: Promise<Calendar | null> = getCalendar(credential);
          return app.variant === "calendar" ? [{ integration: app, credential, calendar }] : [];
        });
      });
```

:yellow_circle: [consistency] `getVideoAdapters` serializes what should be parallel awaits in `packages/core/videoClient.ts`:22 (confidence: 70)
The new `for (const cred of withCredentials) { … const app = await appStore[appName]; … }` loop awaits each dynamic import before starting the next lookup. All 27 `import()` calls in `app-store/index.ts` are kicked off at module-evaluation time and race in parallel, so at steady state this is free — but on a cold start the sequential await means each app's first-time resolve delays the next. `cross-file-impact`: `getCalendarCredentials` in `CalendarManager.ts` uses the parallel `Promise.all(... .map(…))` form (line 141), so adopting the same idiom here is also a consistency win.
```suggestion
const getVideoAdapters = async (withCredentials: CredentialPayload[]): Promise<VideoApiAdapter[]> => {
  const apps = await Promise.all(
    withCredentials.map(async (cred) => {
      const appName = cred.type.split("_").join("");
      const app = await appStore[appName as keyof typeof appStore];
      return { cred, app };
    })
  );
  return apps.reduce<VideoApiAdapter[]>((acc, { cred, app }) => {
    if (app && "lib" in app && "VideoApiAdapter" in app.lib) {
      const makeVideoApiAdapter = app.lib.VideoApiAdapter as VideoApiAdapterFactory;
      acc.push(makeVideoApiAdapter(cred));
    }
    return acc;
  }, []);
};
```

:yellow_circle: [consistency] PR title "Async imports of all apps" implies lazy loading, but the new `import("./X")` expressions still fire eagerly at module evaluation in `packages/app-store/index.ts`:3 (confidence: 75)
Placing `import("./X")` directly as an object-literal value executes each dynamic import as soon as `app-store/index.ts` is loaded. This changes the import graph from a 27-module synchronous tree to 27 parallel async loads, which is genuinely faster on cold start — but it is **not** lazy loading, which is what "async imports" usually suggests. If the intent was actually lazy (import on first access), wrap each entry in a `() => import(...)` thunk and update callers to invoke the thunk. If the intent is what was implemented, a one-line comment clarifying "kicked off in parallel at startup, not on demand" prevents future contributors from assuming they can rely on tree-shaking or first-access deferral.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: wide (`appStore` is the root of the integrations graph; touches booking create/cancel/reschedule, payments, calendars, video) | Sensitive Paths: `packages/lib/payment/*.ts`, `packages/features/bookings/lib/handleCancelBooking.ts` (payment + booking cancellation flows)
AI-Authored Likelihood: LOW (mechanical refactor style consistent with human authorship; `forEach(async)` pattern is a known human mistake)
