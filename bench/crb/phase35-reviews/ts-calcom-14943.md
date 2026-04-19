## Summary
3 files changed, 38 lines added, 6 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
PR adds a `retryCount` column and logic to stop retrying failing SMS workflow reminders after 2 attempts; the cleanup query and retry increments have correctness and concurrency issues.

## Critical
:red_circle: [correctness] Cleanup OR clause deletes non-SMS workflow reminders in packages/features/ee/workflows/api/scheduleSMSReminders.ts:31 (confidence: 92)
The new `deleteMany` uses `OR: [{ method: SMS, scheduledDate: { lte: now } }, { retryCount: { gt: 1 } }]`. The second branch has no `method` filter, so this SMS-specific cron will delete *any* `WorkflowReminder` whose `retryCount > 1`, including EMAIL and other-method reminders populated by other code paths. Because the migration adds `retryCount` to the base model (not per-method), any future/concurrent email-retry logic that increments the same column will have its rows silently removed by this job. Scope the retry-exhaustion branch to SMS.
```suggestion
  await prisma.workflowReminder.deleteMany({
    where: {
      method: WorkflowMethods.SMS,
      OR: [
        { scheduledDate: { lte: dayjs().toISOString() } },
        { retryCount: { gt: 1 } },
      ],
    },
  });
```

## Improvements
:yellow_circle: [correctness] Non-atomic retryCount increment is racy under overlapping cron runs in packages/features/ee/workflows/api/scheduleSMSReminders.ts:178 (confidence: 88)
Both the `else` branch and the `catch` write `retryCount: reminder.retryCount + 1` using the in-memory value from the initial `findMany`. If two cron executions overlap (or the handler is retried), both read the same value and both write the same `N+1`, losing one increment — the reminder will attempt 3+ sends before the `gt: 1` guard removes it. Use Prisma's atomic increment so the DB resolves concurrent writes.
```suggestion
await prisma.workflowReminder.update({
  where: { id: reminder.id },
  data: { retryCount: { increment: 1 } },
});
```

:yellow_circle: [correctness] Successful SMS can be counted as a failure and eventually deleted in packages/features/ee/workflows/api/scheduleSMSReminders.ts:188 (confidence: 78)
Inside the `try`, if `scheduledSMS` is truthy, `twilio.scheduleSMS` has already been called, but the subsequent `workflowReminder.update` that writes the `referenceId` is still inside the same try. If that update throws (transient DB error, deadlock, etc.), control falls into the catch and increments `retryCount` for a reminder whose SMS was actually sent. On the next cron tick the reminder has no `referenceId` but `retryCount` is now 1; after one more transient write failure it will be purged by the new `gt: 1` deletion — silently dropping a successfully-delivered reminder from bookkeeping. Narrow the try to the Twilio call, or guard the catch with `if (!scheduledSMS)` before incrementing.
```suggestion
    } catch (error) {
      if (!scheduledSMS) {
        await prisma.workflowReminder.update({
          where: { id: reminder.id },
          data: { retryCount: { increment: 1 } },
        });
      }
      console.log(`Error scheduling SMS with error ${error}`);
    }
```

:yellow_circle: [consistency] Magic number `1` encodes the retry limit in packages/features/ee/workflows/api/scheduleSMSReminders.ts:40 (confidence: 70)
The PR description says "after scheduling failed twice we delete the workflow reminder", but that contract lives implicitly in the literal `gt: 1`. Future readers have to reason about the off-by-one (`>1` ⇒ 2 attempts counted ⇒ actually 2 failures before delete) from the call site. Extract a named constant (e.g., `const MAX_SMS_RETRY_COUNT = 1;`) co-located with the increment sites so the threshold and the compare stay in lockstep.
```suggestion
const MAX_SMS_RETRY_COUNT = 1;
// ...
await prisma.workflowReminder.deleteMany({
  where: {
    method: WorkflowMethods.SMS,
    OR: [
      { scheduledDate: { lte: dayjs().toISOString() } },
      { retryCount: { gt: MAX_SMS_RETRY_COUNT } },
    ],
  },
});
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: cron-job touching shared `WorkflowReminder` table + Prisma migration | Sensitive Paths: packages/prisma/migrations/, ee/workflows/
AI-Authored Likelihood: LOW
