## Summary
10 files changed, 241 lines added, 203 lines deleted. 11 findings (2 critical, 6 improvements, 3 nitpicks).
Sound diagnosis and largely correct fix for the SendGrid cancel-then-uncancel bug, but the `immediateDelete=true` email path leaks DB rows, async forEach swallows errors, and the PR bundles an unrelated UI refactor with no regression tests.

## Critical

:red_circle: [correctness] `immediateDelete=true` leaks WorkflowReminder rows in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:210` (confidence: 94)
When `immediateDelete` is true the function POSTs the SendGrid cancel and `return`s — it never deletes the `workflowReminder` row and never sets `cancelled: true`. Compare with the `!referenceId` branch (deletes the row) and the SMS sibling `deleteScheduledSMSReminder` (always deletes the row). Callers that pass `immediateDelete: true`:
- `handleNewBooking.ts:964` (reschedule path) — every reschedule now leaves the old reminder row in the DB forever. The CRON job queries `cancelled: true` so it won't clean these up either.
- `workflows.tsx:211` (deleted workflow) and `workflows.tsx:517` (deleted step) — saved by cascade delete on `workflowStep`, but only by accident.

At minimum the reschedule path leaks a row per email reminder per reschedule. Over time this skews metrics and can cause duplicate sends if the row's `scheduledDate` is ever re-examined.
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
        where: { id: reminderId },
      });
      return;
    }
```

:red_circle: [correctness] Fire-and-forget async in `forEach` defeats the surrounding try/catch in `packages/features/bookings/lib/handleNewBooking.ts:962` (confidence: 92)
```
try {
  originalRescheduledBooking.workflowReminders.forEach((reminder) => {
    if (reminder.method === WorkflowMethods.EMAIL) {
      deleteScheduledEmailReminder(reminder.id, reminder.referenceId, true);
    } else if (reminder.method === WorkflowMethods.SMS) {
      deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
    }
  });
} catch (error) {
  log.error("Error while canceling scheduled workflow reminders", error);
}
```
`deleteScheduledEmailReminder`/`deleteScheduledSMSReminder` are async; `forEach` discards the returned promises. Rejections escape the surrounding `try/catch` (they become unhandled rejections), and `eventManager.reschedule(...)` on the next line runs before SendGrid/Twilio have been told to cancel — so the old reminder can fire between reschedule and cancel. Same fire-and-forget pattern also exists in `packages/trpc/server/routers/viewer/workflows.tsx:209, 374, 517, 569` (pre-existing, but this PR newly relies on it for correctness since each call now has DB side-effects, not just an HTTP call).
```suggestion
    try {
      await Promise.all(
        originalRescheduledBooking.workflowReminders.map((reminder) => {
          if (reminder.method === WorkflowMethods.EMAIL) {
            return deleteScheduledEmailReminder(reminder.id, reminder.referenceId, true);
          }
          if (reminder.method === WorkflowMethods.SMS) {
            return deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
          }
          return undefined;
        })
      );
    } catch (error) {
      log.error("Error while canceling scheduled workflow reminders", error);
    }
```

## Improvements

:yellow_circle: [correctness] CRON query should filter out null `referenceId` in `packages/features/ee/workflows/api/scheduleEmailReminders.ts:43` (confidence: 82)
`remindersToCancel` selects `where: { cancelled: true, scheduledDate: { lte: ... } }` but passes `reminder.referenceId` to the SendGrid body without a null check. Today the invariant holds (only `deleteScheduledEmailReminder` sets `cancelled: true`, and it only does so when `referenceId` is non-null), but the query is one schema change away from sending `batch_id: null` to SendGrid. Add a defensive filter.
```suggestion
  const remindersToCancel = await prisma.workflowReminder.findMany({
    where: {
      cancelled: true,
      scheduledDate: {
        lte: dayjs().add(1, "hour").toISOString(),
      },
      referenceId: { not: null },
    },
  });
```

:yellow_circle: [correctness] Sequential `await` in CRON loop will bottleneck under backlog in `packages/features/ee/workflows/api/scheduleEmailReminders.ts:55` (confidence: 74)
The whole point of this change is that the old implementation hit SendGrid's 100-pending-cancel ceiling. The new CRON awaits each SendGrid POST one at a time inside a single `try` — if 50 cancels are due and one network hiccup throws at item 5, items 6–50 are skipped this run and retry 15 minutes later. And if a batch grows past a few hundred it will approach the 5-minute Vercel function timeout. Consider `Promise.allSettled` with a small concurrency cap, and move the try/catch inside the loop so one failure doesn't starve the rest.

:yellow_circle: [consistency] Asymmetric email/SMS cancellation paths in `packages/features/ee/workflows/lib/reminders/smsReminderManager.ts:177` (confidence: 78)
Email goes through the new `cancelled: true` queue drained by the CRON; SMS still calls `twilio.cancelSMS` synchronously and deletes the row. That's defensible (Twilio has no 100-cancel ceiling), but the asymmetry is undocumented and means an SMS cancel failure is unrecoverable — it `console.log`s and drops on the floor, and the reminder will still fire. Either add a comment explaining why SMS bypasses the queue, or route it through the same deferred-cancel path for uniformity.

:yellow_circle: [hallucination] Internal Prisma type used as a public type in `packages/features/ee/workflows/api/scheduleEmailReminders.ts:52` (confidence: 88)
```
const workflowRemindersToDelete: Prisma.Prisma__WorkflowReminderClient<WorkflowReminder, never>[] = [];
```
`Prisma__WorkflowReminderClient` is a generated internal type (the double-underscore prefix is Prisma's convention for "do not depend on this"). It can and does change shape between Prisma versions. Use `Prisma.PrismaPromise<WorkflowReminder>[]` (the documented public type for fluent-API returns) or drop the explicit annotation entirely.
```suggestion
    const workflowRemindersToDelete: Prisma.PrismaPromise<WorkflowReminder>[] = [];
```

:yellow_circle: [testing] No tests added for a production-bug fix (confidence: 85)
The PR reports a regression that shipped silently from #6991 — cancelled bookings kept sending reminders once the 100-pending-cancel ceiling hit. No unit or integration test is added for the new CRON path (`remindersToCancel` drain), `deleteScheduledEmailReminder`'s three branches (null `referenceId`, `immediateDelete=true`, queued cancel), or the reschedule cleanup in `handleNewBooking`. Without tests the same regression class can reoccur. At minimum add a unit test that exercises each branch of `deleteScheduledEmailReminder` with a mocked Prisma client and SendGrid request.

:yellow_circle: [consistency] Unrelated UI refactor bundled into a bug-fix PR in `packages/features/ee/workflows/components/WorkflowStepContainer.tsx:390-460` (confidence: 70)
The change drops the `(isPhoneNumberNeeded || isSenderIdNeeded) && (...)` outer guard and reflows ~120 lines of JSX. It looks behavior-preserving (the inner body was gated on `isPhoneNumberNeeded` in the old code too, so the `isSenderIdNeeded`-only case never rendered anything inside this block), but it is unrelated to "cancelled/rescheduled workflow emails," balloons the reviewer surface, and obscures the actual fix. Split into its own PR or revert.

## Nitpicks

:white_circle: [consistency] Nullable boolean column could be `NOT NULL DEFAULT FALSE` in `packages/prisma/migrations/20230217230604_add_cancelled_to_workflow_reminder/migration.sql:2` (confidence: 72)
`ADD COLUMN "cancelled" BOOLEAN` with no default produces tri-state `null | false | true`. All current call sites treat `null` as "not cancelled," but `where: { cancelled: false }` would not match pre-migration rows, which is a subtle footgun for any future query. Prefer `BOOLEAN NOT NULL DEFAULT false` with an explicit backfill.

:white_circle: [consistency] `console.log` for error paths in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts:232` and `scheduleEmailReminders.ts:70` (confidence: 60)
Matches the existing file style but quietly swallows failures that affect billing-adjacent user-visible behavior. Prefer `logger.error` / the project's `log.error` so these surface in observability.

:white_circle: [consistency] Redundant import reshuffle in `packages/trpc/server/routers/viewer/workflows.tsx:1-10` (confidence: 55)
The `import type { Prisma, PrismaPromise }` line is folded into the value import; `PrismaPromise` is no longer used in this file after the `deleteReminderPromise` cleanup at line 372. Remove `PrismaPromise` from the import.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: bookings router + new-booking/cancel-booking handlers + workflow CRON + schema migration — touches every reminder lifecycle entry point | Sensitive Paths: `packages/prisma/migrations/*`, `packages/features/bookings/**`, workflow reminder managers
AI-Authored Likelihood: LOW
