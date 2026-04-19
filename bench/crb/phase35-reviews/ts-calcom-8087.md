## Summary
12 files changed, 82 lines added, 107 lines deleted. 6 findings (5 critical, 1 improvement, 0 nitpicks).
Async refactor of appStore introduces multiple `forEach(async …)` fire-and-forget bugs and leaves `getCalendarCredentials` returning un-awaited Promises, silently breaking calendar cleanup on reschedule/cancel and any caller that uses `.calendar` directly.

## Critical

:red_circle: [correctness] forEach(async …) discards Promise — calendar deleteEvent is fire-and-forget on reschedule in packages/app-store/vital/lib/reschedule.ts:125 (confidence: 97)
`bookingRefsFiltered.forEach(async (bookingRef) => { … const calendar = await getCalendar(...); return calendar?.deleteEvent(...); })`. `Array.prototype.forEach` discards every Promise returned by the async callback. The outer `Reschedule` function returns before any `getCalendar` resolves or `deleteEvent` runs, so calendar events are not reliably deleted and the surrounding `try/catch` cannot observe rejections — they become unhandled promise rejections. Before the PR this was sync and safe; converting `getCalendar` to async turned the same shape into a real bug.
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

:red_circle: [correctness] forEach(async …) discards Promise — identical fire-and-forget in packages/app-store/wipemycalother/lib/reschedule.ts:125 (confidence: 97)
Same anti-pattern as vital: `bookingRefsFiltered.forEach(async (bookingRef) => { … await getCalendar(...) … calendar?.deleteEvent(...) })`. The async callback's Promise is thrown away by `forEach`, so the function returns before deletions complete and errors are swallowed instead of caught by the enclosing `try/catch`.
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

:red_circle: [correctness] forEach(async credential …) in recurring-cancel branch drops calendar deletes in packages/features/bookings/lib/handleCancelBooking.ts:461 (confidence: 96)
Inside the `recurringEvent` branch, the PR keeps `.forEach(async (credential) => { const calendar = await getCalendar(credential); for (const updBooking of updatedBookings) { … calendar?.deleteEvent(...) } })`. `forEach` does not await the async callbacks, and the inner `calendar?.deleteEvent(...)` is neither awaited nor pushed into `apiDeletes`, so recurring-event cancellations fire-and-forget their calendar cleanup — even if `forEach` were replaced, the delete result would still be silently dropped. Note the sibling branch at line ~478 was correctly converted to `for…of` with `apiDeletes.push(...)` — apply the same fix here for consistency.
```suggestion
      const recurringCalendarCredentials = bookingToDelete.user.credentials.filter(
        (credential) => credential.type.endsWith("_calendar")
      );
      for (const credential of recurringCalendarCredentials) {
        const calendar = await getCalendar(credential);
        for (const updBooking of updatedBookings) {
          const bookingRef = updBooking.references.find((ref) => ref.type.includes("_calendar"));
          if (bookingRef) {
            apiDeletes.push(
              calendar?.deleteEvent(bookingRef.uid, evt, bookingRef.externalCalendarId) as Promise<unknown>
            );
          }
        }
      }
```

:red_circle: [correctness] forEach(async …) discards Promise — duplicate calendar events on reschedule via tRPC in packages/trpc/server/routers/viewer/bookings.tsx:553 (confidence: 96)
The reschedule handler uses `bookingRefsFiltered.forEach(async (bookingRef) => { … const calendar = await getCalendar(credentialsMap.get(bookingRef.type)); return calendar?.deleteEvent(...); })`. `forEach` ignores the returned Promise, so the tRPC mutation can respond to the client before the old calendar event is deleted — the new event is created while the stale event still exists, producing duplicate entries on the user's calendar. Any rejection surfaces as an unhandled promise rejection rather than flowing into the handler's error path.
```suggestion
      await Promise.all(
        bookingRefsFiltered.map(async (bookingRef) => {
          if (bookingRef.uid && bookingRef.type.endsWith("_calendar")) {
            const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
            return calendar?.deleteEvent(
              bookingRef.uid,
              bookingToReschedule,
              bookingRef.externalCalendarId
            );
          }
        })
      );
```

:red_circle: [cross-file-impact] getCalendarCredentials stores an un-awaited Promise in `calendar` — a Promise is always truthy, silently breaking every non-patched consumer in packages/core/CalendarManager.ts:28 (confidence: 95)
`getCalendar` is now `async` and returns `Promise<Calendar | null>`, but the call inside `getCalendarCredentials` remains `const calendar = getCalendar(credential)` (no `await`; the only diff here is a blank line). The returned shape changes from `{ …, calendar: Calendar | null }` to `{ …, calendar: Promise<Calendar | null> }`. `getConnectedCalendars` was updated to `await item.calendar` and is safe, but `getCalendarCredentials` is an exported API — any other caller that destructures `{ calendar }` or calls `item.calendar.getEvents(...)` / `item.calendar.createEvent(...)` will invoke methods on a Promise and throw `TypeError: calendar.getEvents is not a function` at runtime. Worse, guards like `if (calendar)` / `if (!calendar)` flip silently, because a Promise is always truthy even when it resolves to `null` — null-calendar paths will appear "present" and take the wrong branch.
```suggestion
// Make the factory async and await the Calendar resolution so the returned
// object carries a real Calendar | null (not a Promise).
export const getCalendarCredentials = (credentials: Array<CredentialPayload>) => {
  const calendarCredentials = getApps(credentials)
    .filter((app) => app.type.endsWith("_calendar"))
    .flatMap((app) => {
      const credentials = app.credentials.flatMap(async (credential) => {
        const calendar = await getCalendar(credential);
        return app.variant === "calendar" ? [{ integration: app, credential, calendar }] : [];
      });
      return credentials.length ? credentials : [];
    });
  return Promise.all(calendarCredentials).then((rows) => rows.flat());
};
```
(Callers of `getCalendarCredentials` must then `await` its result and await `item.calendar` is no longer required.)

## Improvements

:yellow_circle: [correctness] Sequential awaits in getVideoAdapters serialize every dynamic import — O(N) cold-start latency per credential in packages/core/videoClient.ts:241 (confidence: 85)
The new `for (const cred of withCredentials) { const app = await appStore[appName …]; … }` awaits each dynamic import before starting the next. Dynamic `import()` is naturally parallelizable; under cold-start or cache-miss conditions a user with N video credentials (Zoom + Google Meet + Daily, etc.) pays O(N) latency that the prior static-import version paid once at module load. Use `Promise.all` over `map` to parallelize.
```suggestion
const getVideoAdapters = async (withCredentials: CredentialPayload[]): Promise<VideoApiAdapter[]> => {
  const results = await Promise.all(
    withCredentials.map(async (cred) => {
      const appName = cred.type.split("_").join("");
      const app = await appStore[appName as keyof typeof appStore];
      if (app && "lib" in app && "VideoApiAdapter" in app.lib) {
        const makeVideoApiAdapter = app.lib.VideoApiAdapter as VideoApiAdapterFactory;
        return makeVideoApiAdapter(cred);
      }
      return null;
    })
  );
  return results.filter((a): a is VideoApiAdapter => a !== null);
};
```

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: app-store/index.ts is the central registry imported widely across the monorepo; core/CalendarManager, EventManager, videoClient, and booking handlers are all hot paths | Sensitive Paths: packages/lib/payment/deletePayment.ts, packages/lib/payment/handlePayment.ts, booking-cancel credential handling
AI-Authored Likelihood: MEDIUM (mechanical, uniform `await` + `for…of` substitution across 12 files; no AI co-author signature verifiable from metadata)
