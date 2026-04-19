## Summary
10 files changed, 241 lines added, 203 lines deleted. 5 findings (3 critical, 2 improvements).
Fix correctly defers SendGrid cancellation to a cron to avoid the 100-pending-cancellation cap, but unawaited async cancellations and a batch-abort bug in the cron can leave reminders in inconsistent states.

## Critical
:red_circle: [correctness] Unawaited async cancellations inside `forEach` break the try/catch and race the reschedule in `packages/features/bookings/lib/handleNewBooking.ts`:964 (confidence: 95)
`deleteScheduledEmailReminder` and `deleteScheduledSMSReminder` are `async`, but they are invoked inside `originalRescheduledBooking.workflowReminders.forEach(...)` without `await`. Because `forEach` ignores returned promises: (1) the surrounding `try { ... } catch (error) { log.error(...) }` cannot observe failures from those calls — the `catch` will only ever fire on a synchronous throw, which is essentially never here; (2) `eventManager.reschedule(evt, ...)` runs before any reminder cancellation completes, so the old reminder can be sent while the new one is already being scheduled. The same pattern appears in `packages/trpc/server/routers/viewer/bookings.tsx`:485, `packages/trpc/server/routers/viewer/workflows.tsx`:212, :376, :519, :573. Use `Promise.all(reminders.map(async (r) => { ... }))` and await it.
```suggestion
    try {
      await Promise.all(
        originalRescheduledBooking.workflowReminders.map(async (reminder) => {
          if (reminder.method === WorkflowMethods.EMAIL) {
            await deleteScheduledEmailReminder(reminder.id, reminder.referenceId, true);
          } else if (reminder.method === WorkflowMethods.SMS) {
            await deleteScheduledSMSReminder(reminder.id, reminder.referenceId);
          }
        })
      );
    } catch (error) {
      log.error("Error while canceling scheduled workflow reminders", error);
    }
```

:red_circle: [correctness] Cron batch aborts on first SendGrid failure, orphaning remaining flagged reminders in `packages/features/ee/workflows/api/scheduleEmailReminders.ts`:43 (confidence: 92)
The new cron loop issues each SendGrid cancel with `await client.request(...)` inside a single `try`, then collects DB-delete promises and awaits them once after the loop. If any SendGrid call throws (network error, 4xx, rate limit), the `catch` fires and the loop exits — every `remindersToCancel` entry after the failing one is never processed this run, AND the DB `delete` for the already-cancelled reminders is never awaited (because `Promise.all(workflowRemindersToDelete)` is past the throw point). The user-visible effect is the exact regression this PR is trying to fix: a transient SendGrid error on one reminder causes the rest to be sent anyway. Wrap each reminder's cancel+delete in its own try/catch (or `Promise.allSettled`) so one bad reminder doesn't poison the whole batch, and log per-reminder so these silent drops are observable.
```suggestion
  for (const reminder of remindersToCancel) {
    try {
      await client.request({
        url: "/v3/user/scheduled_sends",
        method: "POST",
        body: { batch_id: reminder.referenceId, status: "cancel" },
      });
      await prisma.workflowReminder.delete({ where: { id: reminder.id } });
    } catch (error) {
      console.error(`Failed to cancel reminder ${reminder.id}: ${error}`);
    }
  }
```

:red_circle: [correctness] Cron selects cancelled reminders without filtering on `referenceId`, so rows without a SendGrid `batch_id` will 400 SendGrid in `packages/features/ee/workflows/api/scheduleEmailReminders.ts`:43 (confidence: 88)
`remindersToCancel` queries only `{ cancelled: true, scheduledDate: { lte: +1h } }`. `referenceId` is nullable on `WorkflowReminder`, and `deleteScheduledEmailReminder` itself handles the `!referenceId` case by deleting the row directly — but reminders that hit the non-immediate branch after being queued (`cancelled=true`) *could* still have `referenceId=null` if they were flagged before SendGrid scheduling completed. When the cron then posts `batch_id: null` to `/v3/user/scheduled_sends`, SendGrid rejects, the whole batch aborts (see finding above), and every subsequent reminder in the run is leaked. Add `referenceId: { not: null }` to the cron's `where` clause; collect null-referenceId rows separately and delete them directly.
```suggestion
  const remindersToCancel = await prisma.workflowReminder.findMany({
    where: {
      cancelled: true,
      scheduledDate: { lte: dayjs().add(1, "hour").toISOString() },
      referenceId: { not: null },
    },
  });
```

## Improvements
:yellow_circle: [consistency] Regression in `import type` usage introduces value imports for type-only symbols in `packages/trpc/server/routers/viewer/workflows.tsx`:1 (confidence: 85)
Pre-PR: `import type { Prisma, PrismaPromise } from "@prisma/client";`. Post-PR: both `Prisma` and `PrismaPromise` were moved into the value `import { ... } from "@prisma/client"` block alongside runtime enums. `PrismaPromise` is a type-only export, and `Prisma` is used here only for the `Prisma.BatchPayload` namespace type, so both should stay under `import type` — otherwise `isolatedModules`/`verbatimModuleSyntax` bundlers will emit a runtime reference that `@prisma/client`'s ESM shim may not provide, and it defeats tree-shaking. Same issue in `packages/features/bookings/lib/handleNewBooking.ts`:1 where `App`, `Credential`, `EventTypeCustomInput`, and `Prisma` were all demoted from `import type` to value imports. Split them back into a `import type { ... }` line.
```suggestion
import type { Prisma, PrismaPromise } from "@prisma/client";
import {
  WorkflowTemplates,
  WorkflowActions,
  WorkflowTriggerEvents,
  BookingStatus,
  WorkflowMethods,
  TimeUnit,
} from "@prisma/client";
```

:yellow_circle: [correctness] Silent catch in `deleteScheduledEmailReminder` swallows the DB-flag failure, so a caller that thinks the reminder was cancelled may still fire in `packages/features/ee/workflows/lib/reminders/emailReminderManager.ts`:197 (confidence: 78)
The `catch (error) { console.log(...) }` block covers three very different failure modes — `prisma.workflowReminder.delete` (no-referenceId branch), `client.request` (immediate-delete branch), and `prisma.workflowReminder.update({ cancelled: true })` (deferred branch). In the deferred branch this is the worst case: if the `update` fails, the row stays `cancelled=false`/`NULL`, the cron never picks it up, and the reminder is sent at its scheduled time — the exact bug #7225. At minimum, propagate the error (or at least surface it via the existing `log`er rather than `console.log`) so callers can react, and consider returning a boolean success indicator so the handlers can record the failure.

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: 10 files across bookings, workflow cron, workflow tRPC routers, Prisma schema (migration); every booking-create/cancel/reschedule path and all workflow-edit mutations touch the changed code | Sensitive Paths: 1 migration (adds nullable `cancelled BOOLEAN`)
AI-Authored Likelihood: LOW
