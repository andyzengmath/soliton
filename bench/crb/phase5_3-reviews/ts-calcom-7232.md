## Summary
10 files changed, 241 lines added, 203 lines deleted. 11 findings (6 critical, 5 improvements).
Refactor of workflow-reminder cancellation introduces an orphaned-row leak in the `immediateDelete` email path, a dead try/catch wrapping fire-and-forget async calls, and a cron loop that aborts the batch on the first SendGrid failure; the same async/forEach anti-pattern appears at six call sites and the prior `await Promise.all(remindersToDelete)` was removed without an awaiting replacement.

## Critical
:red_circle: [correctness] `immediateDelete` branch cancels at SendGrid but never deletes or marks the DB row in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts`:213 (confidence: 97)
When `immediateDelete` is true, the function calls the SendGrid `scheduled_sends` cancel endpoint and `return`s without touching the `WorkflowReminder` row. Every other branch keeps DB and SendGrid in sync: the no-`referenceId` branch deletes the row, the default branch marks `cancelled: true` for the cron to sweep, and the SMS path always deletes synchronously. The only caller passing `immediateDelete=true` for the email path is `handleNewBooking.ts` at the reschedule site (and `workflows.tsx` workflow/step deletes, where cascade on `WorkflowStep.onDelete: Cascade` masks the leak). For the reschedule case there is no cascade, so every old email reminder row remains in the database forever — invisible to the cron sweep (filter is `cancelled: true`) and never deleted.
```suggestion
    if (immediateDelete) {
      await client.request({
        url: "/v3/user/scheduled_sends",
        method: "POST",
        body: {
          batch_id: referenceId,
          status: "cancel",
        },
      });

      await prisma.workflowReminder.delete({
        where: {
          id: reminderId,
        },
      });
      return;
    }
```

:red_circle: [correctness] Outer `try/catch` cannot catch rejections from un-awaited async calls inside `forEach` in `packages/features/bookings/lib/handleNewBooking.ts`:961 (confidence: 97)
`deleteScheduledEmailReminder` and `deleteScheduledSMSReminder` are both `async`. Inside `forEach` they are invoked without `await`, so each returned Promise is fire-and-forget. `forEach` does not await its callback; the outer `try/catch` only catches synchronous throws (none possible here), so the `log.error` line is dead code. Any SendGrid or Prisma rejection is silently swallowed by the inner `console.log` in the callee, and the reschedule proceeds even when cancellation of the previous booking's reminders has failed — risking duplicate emails/SMS to the attendee.
```suggestion
    try {
      // cancel workflow reminders from previous rescheduled booking
      await Promise.all(
        originalRescheduledBooking.workflowReminders.map((reminder) => {
          if (reminder.method === WorkflowMethods.EMAIL) {
            return deleteScheduledEmailReminder(reminder.id, reminder.referenceId, true);
          } else if (reminder.method === WorkflowMethods.SMS) {
            return deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
          }
          return Promise.resolve();
        })
      );
    } catch (error) {
      log.error("Error while canceling scheduled workflow reminders", error);
    }
```

:red_circle: [correctness] Cron `for await` loop aborts on first SendGrid failure, leaving partially-cancelled reminders permanently stuck in `packages/features/ee/workflows/api/scheduleEmailReminders.ts`:51 (confidence: 95)
The loop awaits `client.request(...)` sequentially inside one `try/catch`. If a single SendGrid call rejects (e.g. 4xx on an already-cancelled batch, expired batch, or transient 5xx) the loop exits, the catch logs via `console.log`, and `await Promise.all(workflowRemindersToDelete)` is never reached. Every preceding reminder had its SendGrid batch cancelled but its DB row was *not* deleted, leaving rows in `cancelled: true` state. On the next run, those rows are picked up again and re-cancelled at SendGrid — if SendGrid keeps rejecting (already-cancelled), the same row aborts every subsequent run forever. Reminders whose `scheduledDate` falls outside the next-1h window before the next tick are silently abandoned with `cancelled: true` rows that the cron will never re-select, but the original email may still fire.
```suggestion
  const results = await Promise.allSettled(
    remindersToCancel.map(async (reminder) => {
      try {
        if (reminder.referenceId) {
          await client.request({
            url: "/v3/user/scheduled_sends",
            method: "POST",
            body: {
              batch_id: reminder.referenceId,
              status: "cancel",
            },
          });
        }
        await prisma.workflowReminder.delete({
          where: { id: reminder.id },
        });
      } catch (error) {
        console.error(`Error cancelling reminder ${reminder.id}: ${error}`);
        throw error;
      }
    })
  );
  const failed = results.filter((r) => r.status === "rejected");
  if (failed.length) {
    console.error(`${failed.length}/${remindersToCancel.length} reminders failed to cancel`);
  }
```

:red_circle: [correctness] `deleteScheduledEmailReminder` / `deleteScheduledSMSReminder` swallow all errors via broad catch + `console.log` and return `void` on both paths in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts`:234 (confidence: 95)
Both functions catch every exception and emit `console.log` only — not `log.error`, not a re-throw, not a discriminated result. Function signatures return `Promise<void>` for both success and failure, so every call site in this PR (`handleCancelBooking.ts`, `handleNewBooking.ts`, `bookings.tsx`, `workflows.tsx`) discards the return value and cannot distinguish a real cancellation from a silent failure. In `smsReminderManager.ts` the same pattern groups the Twilio cancel and the Prisma delete inside a single try, so a Twilio rejection skips the row deletion silently and a Prisma rejection leaves the SMS cancelled but the row resident — neither outcome is observable to callers. This nullifies retry logic and turns broken cancellations into invisible production bugs.
```suggestion
  } catch (error) {
    log.error("Error canceling email reminder", { reminderId, referenceId, error });
    throw error;
  }
```

:red_circle: [correctness] Async `deleteScheduledEmailReminder` / `deleteScheduledSMSReminder` calls are fire-and-forget inside nested `forEach`; previous `await Promise.all(remindersToDelete)` was removed in `packages/features/bookings/lib/handleCancelBooking.ts`:485 (confidence: 92)
The handler builds `prismaPromises = [attendeeDeletes, bookingReferenceDeletes]` and awaits that array, but the new code drops the reminder cancellations from the awaited set entirely (the prior `remindersToDelete` array is gone). Each `forEach` iteration calls async cancellation without `await` and without a surrounding try/catch, so the API response can return success while reminder cancellations are still pending or have already silently failed. Combined with the `console.log`-only catch inside the callees, attendees of the cancelled booking can still receive workflow emails because the cancel races the scheduled-send time.
```suggestion
  const cancellationPromises = updatedBookings.flatMap((booking) =>
    booking.workflowReminders.map((reminder) => {
      if (reminder.method === WorkflowMethods.EMAIL) {
        return deleteScheduledEmailReminder(reminder.id, reminder.referenceId);
      } else if (reminder.method === WorkflowMethods.SMS) {
        return deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
      }
      return Promise.resolve();
    })
  );
  const prismaPromises: Promise<unknown>[] = [
    attendeeDeletes,
    bookingReferenceDeletes,
    ...cancellationPromises,
  ];
```

:red_circle: [correctness] `forEach(async (reminder) => …)` callback discards every returned Promise in `packages/trpc/server/routers/viewer/workflows.tsx`:572 (confidence: 93)
`remindersToUpdate.forEach(async (reminder) => { … })` is the canonical async/forEach trap: `forEach` ignores the Promises returned by async callbacks, so all `deleteScheduledEmailReminder`/`deleteScheduledSMSReminder` calls are abandoned. The `async` keyword on the callback creates a false visual cue that the work is awaited; no rejection bubbles anywhere. The same fire-and-forget pattern (without the misleading `async` keyword) repeats in this file at `scheduledReminders.forEach` (workflow-delete, ~line 490) and `remindersToDelete.flat().forEach` (event-type disablement, ~line 510), and at `bookingToReschedule.workflowReminders.forEach` in `packages/trpc/server/routers/viewer/bookings.tsx` line 488 — the prior code at the latter awaited `Promise.all(remindersToDelete)` and that await is now removed.
```suggestion
          //cancel all workflow reminders from steps that were edited
          for (const reminder of remindersToUpdate) {
            if (reminder.method === WorkflowMethods.EMAIL) {
              await deleteScheduledEmailReminder(reminder.id, reminder.referenceId);
            } else if (reminder.method === WorkflowMethods.SMS) {
              await deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
            }
          }
```

## Improvements
:yellow_circle: [correctness] Removing the `if (reminder.scheduled && reminder.referenceId)` guard means the cron and inline cancel paths can race for reminders whose `scheduledDate` is within the next hour in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts`:194 (confidence: 86)
For a reminder with a valid `referenceId` and `immediateDelete=false` (the cancel-booking and disable-event-type paths), the function only writes `cancelled: true` and relies on the cron at `scheduleEmailReminders` to actually call SendGrid. The cron runs every 15 minutes and filters `scheduledDate <= now + 1h`. If a booking is cancelled, say, 50 minutes before the scheduled send, SendGrid may still fire the email before the next cron tick processes the row. The `immediateDelete` parameter is intentionally not passed for cancelled bookings — but for sub-1h scheduled times this defers cancellation past the actual send. Either always immediate-cancel when `scheduledDate` is within the cron window, or shorten the cron interval and document the timing window.
```suggestion
    const scheduledSoon = reminder.scheduledDate &&
      dayjs(reminder.scheduledDate).isBefore(dayjs().add(1, "hour"));
    if (immediateDelete || scheduledSoon) {
      await client.request({ url: "/v3/user/scheduled_sends", method: "POST", body: { batch_id: referenceId, status: "cancel" } });
      await prisma.workflowReminder.delete({ where: { id: reminderId } });
      return;
    }
```

:yellow_circle: [consistency] Asymmetric DB cleanup between SMS and email cancel paths in `packages/features/ee/workflows/lib/reminders/smsReminderManager.ts`:174 (confidence: 90)
`deleteScheduledSMSReminder` always calls `prisma.workflowReminder.delete` after Twilio cancel, while `deleteScheduledEmailReminder` has three different DB outcomes depending on `referenceId` and `immediateDelete`. There is no soft-cancel via `cancelled: true` on the SMS path, so the cron cannot defer SMS cancellation, and a future change that adds `immediateDelete` semantics for SMS will likely re-introduce the same leak just fixed for email. Align the two functions on a single contract — both should set `cancelled: true` (or both should hard-delete) — and pass the policy explicitly rather than via two diverging signatures.
```suggestion
export const deleteScheduledSMSReminder = async (
  reminderId: number,
  referenceId: string | null,
  immediateDelete?: boolean
) => {
  // mirror the email path: soft-cancel by default, hard-delete on immediateDelete
};
```

:yellow_circle: [correctness] Boolean trap: `deleteScheduledEmailReminder(reminderId, referenceId, immediateDelete?)` overloads three different behaviors on one optional flag in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts`:194 (confidence: 87)
The function dispatches across three branches based on `(referenceId, immediateDelete)`: delete row, cancel-then-return, or mark-cancelled. The optional `immediateDelete?: boolean` is a classic boolean trap — call sites read `deleteScheduledEmailReminder(id, refId, true)` with no indication of *which* of the three paths runs. Split into two named exports (`cancelEmailReminderImmediately` and `markEmailReminderCancelled`) or use a discriminated `mode: "immediate" | "deferred"` parameter so call sites are self-documenting.
```suggestion
export const cancelEmailReminderImmediately = async (reminderId: number, referenceId: string | null) => { /* SendGrid cancel + DB delete */ };
export const markEmailReminderCancelled = async (reminderId: number, referenceId: string | null) => { /* DB cancelled=true (or DB delete if no referenceId) */ };
```

:yellow_circle: [consistency] `Prisma.Prisma__WorkflowReminderClient<WorkflowReminder, never>[]` typing leaks Prisma internals and is unused after the eventual `Promise.all` in `packages/features/ee/workflows/api/scheduleEmailReminders.ts`:50 (confidence: 86)
The double-underscore `Prisma__WorkflowReminderClient` is an internal Prisma generic intended for fluent extensions; using it as the array type couples this code to a generated symbol that may rename across Prisma upgrades. Since the array is only consumed by `Promise.all`, `Promise<unknown>[]` (matching the pattern used in `handleCancelBooking.ts`) or simply inlining the await per iteration is preferable. (This finding becomes moot after the loop is restructured per the cron-batch finding above.)
```suggestion
const workflowRemindersToDelete: Promise<unknown>[] = [];
```

:yellow_circle: [correctness] Removed `(isPhoneNumberNeeded || isSenderIdNeeded)` outer condition is unrelated to the workflow-reminder cancellation refactor and silently changes UI rendering in `packages/features/ee/workflows/components/WorkflowStepContainer.tsx`:387 (confidence: 85)
The wrapping conditional was reduced from `(isPhoneNumberNeeded || isSenderIdNeeded) && (…)` to `isPhoneNumberNeeded && (…)`, and all of the inner JSX (which was previously gated by an additional inner `isPhoneNumberNeeded` check) is now directly inside. When only `isSenderIdNeeded` is true (no phone needed), the wrapper `<div>` is no longer rendered at all. There is no mention of sender-ID UI changes in the PR description, which is scoped to "Fixes that workflow reminders of cancelled and rescheduled bookings are still sent." Either restore the original condition or split this UI cleanup into a separate PR with its own description.
```suggestion
              {(isPhoneNumberNeeded || isSenderIdNeeded) && (
                <div className="mt-2 rounded-md bg-gray-50 p-4 pt-0">
                  {isPhoneNumberNeeded && (
                    <>
                      {/* existing phone-number UI block */}
                    </>
                  )}
                </div>
              )}
```

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: HIGH (booking lifecycle + workflow scheduler + Prisma migration affecting all workflow reminders) | Sensitive Paths: `packages/prisma/migrations/20230217230604_add_cancelled_to_workflow_reminder/migration.sql`, `packages/prisma/schema.prisma`
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
