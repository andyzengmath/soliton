## Summary
10 files changed, 241 lines added, 203 lines deleted. 8 findings (5 critical, 3 improvements).
The new `cancelled`-flag + CRON cancellation scheme has a broken contract between `deleteScheduledEmailReminder` and its callers that leaves orphaned `WorkflowReminder` rows in the DB, plus a single-try/catch CRON loop that silently abandons remaining cancellations on the first SendGrid failure.

## Critical

:red_circle: [correctness] `immediateDelete=true` branch never deletes the Prisma row in packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:212 (confidence: 97)
When `immediateDelete=true` and `referenceId` is non-null, the function POSTs to SendGrid's cancel endpoint and then `return`s. There is no `prisma.workflowReminder.delete` on that path, so the row is neither deleted nor marked `cancelled: true`, and the CRON sweep (which filters on `cancelled: true`) will never touch it. Callers that pass `true` — the reschedule path in `handleNewBooking.ts`, the delete-workflow and edit-step paths in `workflows.tsx` — accumulate permanent orphans (cascade saves the delete-workflow and delete-step cases, but reschedule and edit-step have no cascade path).
```suggestion
    if (immediateDelete) {
      await client.request({
        url: "/v3/user/scheduled_sends",
        method: "POST",
        body: { batch_id: referenceId, status: "cancel" },
      });
      await prisma.workflowReminder.delete({ where: { id: reminderId } });
      return;
    }
```

:red_circle: [correctness] Single try/catch around the CRON for-loop silently abandons remaining cancellations in packages/features/ee/workflows/api/scheduleEmailReminders.ts:51 (confidence: 95)
The entire `for (const reminder of remindersToCancel)` loop is wrapped in one try/catch. A SendGrid error on reminder N exits the loop, so reminders N+1..end are never cancelled and their Prisma rows are never deleted. Those rows re-surface in the next CRON run 15 min later, but if SendGrid continues to reject them (e.g. invalid/already-sent batch), they accumulate permanently — and any customer whose reminder sat behind the failed one in this batch receives the email they were supposed to be spared.
```suggestion
  for (const reminder of remindersToCancel) {
    try {
      if (!reminder.referenceId) {
        await prisma.workflowReminder.delete({ where: { id: reminder.id } });
        continue;
      }
      await client.request({
        url: "/v3/user/scheduled_sends",
        method: "POST",
        body: { batch_id: reminder.referenceId, status: "cancel" },
      });
      await prisma.workflowReminder.delete({ where: { id: reminder.id } });
    } catch (error) {
      console.error(`Error cancelling reminder ${reminder.id}:`, error);
    }
  }
```

:red_circle: [correctness] `remindersToCancel` query missing `referenceId: { not: null }` filter — null `batch_id` sent to SendGrid in packages/features/ee/workflows/api/scheduleEmailReminders.ts:43 (confidence: 92)
The query filters on `cancelled: true` and `scheduledDate <= now+1h` only. A `cancelled: true` row can legitimately have a null `referenceId` (reminder was flipped before it was ever scheduled with SendGrid). The CRON then POSTs `{ batch_id: null, status: "cancel" }` directly to SendGrid — bypassing the `!referenceId` guard that exists in `deleteScheduledEmailReminder`. The invalid request errors, which combined with the single-catch above wipes out the rest of the batch.
```suggestion
  const remindersToCancel = await prisma.workflowReminder.findMany({
    where: {
      cancelled: true,
      scheduled: true,
      referenceId: { not: null },
      scheduledDate: { lte: dayjs().add(1, "hour").toISOString() },
    },
  });
```

:red_circle: [cross-file-impact] `handleCancelBooking` does not pass `immediateDelete=true` — EMAIL reminders beyond the 1-hour CRON window are never cleaned up in packages/features/bookings/lib/handleCancelBooking.ts:484 (confidence: 90)
The old code called `deleteScheduledEmailReminder(referenceId)` and then explicitly ran `prisma.workflowReminder.deleteMany` on every reminder row in the same transaction via `prismaPromises`. The new code drops the explicit `deleteMany` AND calls the helper without `immediateDelete`, so the non-null-`referenceId` branch only sets `cancelled: true` and relies on the CRON to finish the job. For reminders whose `scheduledDate` is more than 1 hour in the future (the common case for day-ahead/2-day-ahead reminders on a freshly-cancelled booking), the CRON never picks them up and the row orphans indefinitely — the user's DB grows one WorkflowReminder row per cancelled booking reminder forever.
```suggestion
  updatedBookings.forEach((booking) => {
    booking.workflowReminders.forEach((reminder) => {
      if (reminder.method === WorkflowMethods.EMAIL) {
        deleteScheduledEmailReminder(reminder.id, reminder.referenceId, true);
      } else if (reminder.method === WorkflowMethods.SMS) {
        deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
      }
    });
  });
```

:red_circle: [cross-file-impact] `bookings.tsx` reschedule path has the same missing `immediateDelete` flag in packages/trpc/server/routers/viewer/bookings.tsx:484 (confidence: 90)
Same pattern as `handleCancelBooking.ts`: the previous `Promise.all(remindersToDelete)` is gone and the helper is called without `immediateDelete`, so the reminder rows for the previous (rescheduled-away) booking soft-cancel but never delete unless the reminder fires within 1h. Inconsistent with the sibling calls in `workflows.tsx` (delete workflow, delete step) which do pass `true`.
```suggestion
        bookingToReschedule.workflowReminders.forEach((reminder) => {
          if (reminder.method === WorkflowMethods.EMAIL) {
            deleteScheduledEmailReminder(reminder.id, reminder.referenceId, true);
          } else if (reminder.method === WorkflowMethods.SMS) {
            deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
          }
        });
```

## Improvements

:yellow_circle: [correctness] `isSenderIdNeeded` UI branch silently dropped from the outer conditional in packages/features/ee/workflows/components/WorkflowStepContainer.tsx:390 (confidence: 88)
The outer guard changed from `(isPhoneNumberNeeded || isSenderIdNeeded)` to just `isPhoneNumberNeeded`. The inner content (phone input + verification flow) was preserved but is now only reachable when a phone number is needed. If any action type sets `isSenderIdNeeded=true` with `isPhoneNumberNeeded=false`, the configuration panel that used to render is no longer visible. This change is not mentioned in the PR description, which is scoped to workflow email cancellation — it reads as an accidental regression from the refactor.
```suggestion
              {(isPhoneNumberNeeded || isSenderIdNeeded) && (
                <div className="mt-2 rounded-md bg-gray-50 p-4 pt-0">
```

:yellow_circle: [security] Authorization filter removed from the workflowReminder delete in the event-type disable path in packages/trpc/server/routers/viewer/workflows.tsx:373 (confidence: 78)
The previous code deleted via `ctx.prisma.workflowReminder.deleteMany({ where: { id: reminder.id, booking: { userId: ctx.user.id } } })`, providing a defense-in-depth check that the reminder being deleted actually belongs to the caller. The new code drops that `deleteMany` entirely and the helper functions delete by `id` alone. Correctness relies entirely on the upstream `remindersToDeletePromise` query correctly scoping to the caller — a future refactor or shared-workflow feature would silently turn this into an IDOR.
```suggestion
      remindersToDelete.flat().forEach(async (reminder) => {
        const owned = await ctx.prisma.workflowReminder.findFirst({
          where: { id: reminder.id, booking: { userId: ctx.user.id } },
          select: { id: true },
        });
        if (!owned) return;
        if (reminder.method === WorkflowMethods.EMAIL) {
          deleteScheduledEmailReminder(reminder.id, reminder.referenceId);
        } else if (reminder.method === WorkflowMethods.SMS) {
          deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
        }
      });
```

:yellow_circle: [correctness] CRON batches Prisma deletes via `Promise.all` only on SendGrid success — partial progress is lost on failure in packages/features/ee/workflows/api/scheduleEmailReminders.ts:58 (confidence: 82)
Even with the loop-level try/catch fixed, the current design collects `prisma.workflowReminder.delete` promises into `workflowRemindersToDelete` and awaits them after the loop. If any delete rejects, `Promise.all` short-circuits and the remaining collected deletes do not run, yet the single surrounding try swallows the error — the corresponding SendGrid batches are cancelled but their DB rows stay `cancelled: true`, so the next CRON retries a no-op SendGrid cancel forever. The cleanest fix is to delete each row immediately inside the loop rather than batching (as in the Finding above).
```suggestion
      await client.request({ /* ... */ });
      await prisma.workflowReminder.delete({ where: { id: reminder.id } });
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: 10 files, 2 critical paths (booking cancel, booking reschedule), 1 CRON job, 1 DB migration | Sensitive Paths: packages/prisma/migrations/*, packages/trpc/server/routers/viewer/*
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold — unstructured `console.log(error)` leakage risk in the 3 new catch blocks; nullable `cancelled` column with no `DEFAULT false` backfill inviting future tri-state logic bugs.)
