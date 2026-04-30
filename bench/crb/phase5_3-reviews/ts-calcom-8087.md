## Summary
12 files changed, 82 lines added, 107 lines deleted. 6 findings (5 critical, 1 improvement).
Async refactor of `appStore` and `getCalendar` introduces several `forEach(async ...)` fire-and-forget bugs and one Promise-leaked-into-data-shape bug; outer try/catch and `Promise.all(apiDeletes)` no longer behave as the surrounding code assumes.

## Critical

:red_circle: [correctness] `forEach(async ...)` in vital reschedule defeats outer try/catch and fires deletes-and-forgets in `packages/app-store/vital/lib/reschedule.ts:122` (confidence: 98)
`Array.prototype.forEach` discards the Promise returned by each async callback. The surrounding `try { ... } catch` block completes synchronously the moment `forEach` returns, so any rejection from `await getCalendar(...)` or `calendar?.deleteEvent(...)` becomes an unhandled promise rejection and the function continues before any delete completes. The pre-PR sync `getCalendar()` did not need the callback to be async, so this is a regression introduced by the await ripple, not a pre-existing bug.
```suggestion
      await Promise.all(
        bookingRefsFiltered.map(async (bookingRef) => {
          if (bookingRef.uid) {
            if (bookingRef.type.endsWith("_calendar")) {
              const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
              return calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
            } else if (bookingRef.type.endsWith("_video")) {
              return deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
            }
          }
        })
      );
```

:red_circle: [correctness] `forEach(async ...)` in wipemycalother reschedule — identical fire-and-forget bug in `packages/app-store/wipemycalother/lib/reschedule.ts:122` (confidence: 98)
Same pattern as vital: changing the callback to `async` so that `await getCalendar(...)` compiles makes `forEach` swallow the returned Promises. The outer try/catch never sees rejections, and `Reschedule()` resolves before any of the `_calendar` deleteEvent / `_video` deleteMeeting work has actually finished.
```suggestion
      await Promise.all(
        bookingRefsFiltered.map(async (bookingRef) => {
          if (bookingRef.uid) {
            if (bookingRef.type.endsWith("_calendar")) {
              const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
              return calendar?.deleteEvent(bookingRef.uid, builder.calendarEvent);
            } else if (bookingRef.type.endsWith("_video")) {
              return deleteMeeting(credentialsMap.get(bookingRef.type), bookingRef.uid);
            }
          }
        })
      );
```

:red_circle: [correctness] `forEach(async ...)` in tRPC bookings router — mutation returns to client before deletes finish in `packages/trpc/server/routers/viewer/bookings.tsx:553` (confidence: 98)
The reschedule mutation now awaits `getCalendar()` inside a `forEach(async ...)`. `forEach` does not await, so the mutation handler returns to the tRPC client while the calendar delete promises are still in flight (or already rejected as unhandled). Failures will be silently dropped and the client will see a successful reschedule that never actually cleared the previous calendar event.
```suggestion
        await Promise.all(
          bookingRefsFiltered.map(async (bookingRef) => {
            if (bookingRef.uid) {
              if (bookingRef.type.endsWith("_calendar")) {
                const calendar = await getCalendar(credentialsMap.get(bookingRef.type));
                return calendar?.deleteEvent(
                  bookingRef.uid,
                  builder.calendarEvent,
                  bookingRef.externalCalendarId
                );
              }
              // ... existing video branch
            }
          })
        );
```

:red_circle: [correctness] `apiDeletes.push` inside `forEach(async ...)` races against the outer `Promise.all(apiDeletes)` in `packages/features/bookings/lib/handleCancelBooking.ts:461` (confidence: 95)
The recurring-booking branch fans out work via `.filter(...).forEach(async (credential) => { const calendar = await getCalendar(credential); ... apiDeletes.push(...); })`. `forEach` returns synchronously, and the outer code reaches `await Promise.all(apiDeletes)` before any of the async callbacks have resumed past `await getCalendar(...)`. The `apiDeletes.push(...)` calls therefore execute *after* `Promise.all` has already been called on a shorter array, so the recurring-booking calendar deletes are never awaited and their failures never surface — the user sees the cancel succeed even when the upstream calendar delete fails.
```suggestion
      await Promise.all(
        bookingToDelete.user.credentials
          .filter((credential) => credential.type.endsWith("_calendar"))
          .map(async (credential) => {
            const calendar = await getCalendar(credential);
            for (const updBooking of updatedBookings) {
              const bookingRef = updBooking.references.find((ref) => ref.type.includes("_calendar"));
              if (bookingRef) {
                apiDeletes.push(
                  calendar?.deleteEvent(bookingRef.uid, evt, bookingRef.externalCalendarId) as Promise<unknown>
                );
              }
            }
          })
      );
```

:red_circle: [correctness] Unawaited Promise leaked into `calendar` field of `getCalendarCredentials` result in `packages/core/CalendarManager.ts:28` (confidence: 92)
`const calendar = getCalendar(credential)` no longer returns a `Calendar | null` — it returns `Promise<Calendar | null>`. That Promise is then placed straight into the returned shape `{ integration, credential, calendar }`. The single in-diff consumer (`getConnectedCalendars` at line 47) was updated to do `const calendar = await item.calendar`, but every other consumer of `getCalendarCredentials` outside this diff still expects a `Calendar` instance. A Promise is truthy and has no `.getAvailability` / `.createEvent` method, so calls like `item.calendar.getAvailability(...)` will throw `TypeError: ... is not a function` at runtime, and any TS check that still types the field as `Calendar | null` will not catch it.
```suggestion
        const calendar = await getCalendar(credential);
        return app.variant === "calendar" ? [{ integration: app, credential, calendar }] : [];
```
Resolve the Promise at construction time so the public shape of `getCalendarCredentials` stays `Calendar | null` and no caller needs to know the field changed.

## Improvements

:yellow_circle: [cross-file-impact] Async ripple is incomplete — audit every external caller of `appStore[k]`, `getCalendar`, and `getVideoAdapters` in `packages/app-store/index.ts:1` (confidence: 86)
Three public type contracts changed in this PR: `appStore[k]` is now `Promise<typeof import("./mod")>`, `getCalendar()` returns `Promise<Calendar | null>`, and `getVideoAdapters()` returns `Promise<VideoApiAdapter[]>`. The diff updates the call sites visible inside `packages/core`, `packages/features/bookings`, `packages/lib/payment`, and the two `app-store/*/lib/reschedule.ts` files, but these symbols are exported from `packages/core` and `packages/app-store` and are reachable from `apps/web`, `apps/api`, and the per-app `lib/` modules. Any unmigrated consumer that does `appStore[key].lib.X(...)`, `getCalendar(cred).getAvailability(...)`, or `getVideoAdapters(creds).forEach(...)` will either silently call a method on a `Promise` (returns `undefined`) or hard-crash with `TypeError: forEach is not a function`. Run a monorepo-wide grep for `appStore[`, `appStore.`, `getCalendar(`, and `getVideoAdapters(` and confirm every site has an `await` in front, or codemod the changes.

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: cross-package — `packages/core` and `packages/app-store` exports change shape, hits `apps/web`, `apps/api`, every per-app integration | Sensitive Paths: `packages/lib/payment/*`, `packages/features/bookings/*`
AI-Authored Likelihood: LOW
