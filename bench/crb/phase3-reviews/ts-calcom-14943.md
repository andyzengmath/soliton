## Summary
3 files changed, 38 lines added, 6 lines deleted. 7 findings (2 critical, 4 improvements, 1 nitpick).
Adds a `retryCount` column to `WorkflowReminder` and deletes rows after two failed SMS schedules, but the new cleanup OR-branch is not scoped to `method = SMS` and can double-increment on DB errors.

## Critical

:red_circle: [correctness] deleteMany OR clause deletes non-SMS reminders with retryCount > 1 in packages/features/ee/workflows/api/scheduleSMSReminders.ts:28 (confidence: 97)
The second OR branch `{ retryCount: { gt: 1 } }` has no `method` filter, so the SMS cron will delete any `WorkflowReminder` — EMAIL, WHATSAPP, or otherwise — whose `retryCount` ever exceeds 1. Today nothing else in this diff increments `retryCount` for non-SMS reminders, but the new column is on the shared `WorkflowReminder` model and the next PR that adds a retry path for email/WhatsApp (or any backfill) will cause silent cross-method data loss from this cron. Scope the cleanup to SMS.
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

:red_circle: [correctness] Double retryCount increment when the else-branch update throws in packages/features/ee/workflows/api/scheduleSMSReminders.ts:175 (confidence: 91)
Both the `else` branch (SMS returned no sid) and the outer `catch` increment `retryCount`. If the `prisma.workflowReminder.update` inside the `else` throws (connection blip, deadlock, etc.), the catch fires and runs a second increment against the same `reminder.retryCount` snapshot — bumping it from 0 to 2 on a single real failure and triggering premature deletion on the next cron tick. Promote the increment to a single post-try path so the catch handles only Twilio failures, and use Prisma's atomic `increment` operator while you are at it (see improvement below).
```suggestion
      let failed = false;
      try {
        const scheduledSMS = await twilio.messages.create({ /* ... */ });
        if (scheduledSMS.sid) {
          await prisma.workflowReminder.update({
            where: { id: reminder.id },
            data: { referenceId: scheduledSMS.sid },
          });
        } else {
          failed = true;
        }
      } catch (error) {
        failed = true;
        console.error(`Error scheduling SMS`, error);
      }
      if (failed) {
        await prisma.workflowReminder.update({
          where: { id: reminder.id },
          data: { retryCount: { increment: 1 } },
        });
      }
```

## Improvements

:yellow_circle: [correctness] Non-atomic retryCount increment (lost-update race) in packages/features/ee/workflows/api/scheduleSMSReminders.ts:178 (confidence: 82)
`data: { retryCount: reminder.retryCount + 1 }` reads the count into application memory and writes back a literal. Two overlapping runs (e.g., a cron retrigger while the prior invocation is still iterating, or a future parallelized version) can both read `retryCount = 0` and both write `1`, losing one increment and letting a reminder exceed its effective retry budget. Prisma supports the atomic `{ increment: 1 }` operator — prefer it.
```suggestion
await prisma.workflowReminder.update({
  where: { id: reminder.id },
  data: { retryCount: { increment: 1 } },
});
```

:yellow_circle: [cross-file-impact] PartialWorkflowReminder select helpers likely still omit retryCount in packages/features/ee/workflows/lib/reminders/reminderScheduler.ts:1 (confidence: 78)
This file inlines `retryCount: true` into `select` but the shared `PartialWorkflowReminder` type / base `select` object defined elsewhere in `packages/features/ee/workflows` does not. Other consumers (email/WhatsApp schedulers, workflow reminder readers) will continue to see `retryCount` as undefined at runtime even though the column is populated, which will mask future retry-logic extensions. Audit the shared selects and the `PartialWorkflowReminder` alias and add `retryCount` at the base so this cron does not need to spread-and-patch.

:yellow_circle: [correctness] Retry-threshold boundary does not match the PR description precisely in packages/features/ee/workflows/api/scheduleSMSReminders.ts:41 (confidence: 80)
The PR says "after scheduling failed twice we delete". `gt: 1` deletes when `retryCount >= 2`, which means the row is purged at the start of the *next* cron run — **before** a potential third attempt. That matches "two retries" but only if you count the very first send as attempt 0. Worth either (a) confirming intent is "2 total send attempts before giving up" (current behaviour) vs "attempt, then 2 retries = 3 total" (would need `gt: 2`), and (b) extracting a named constant so the intent is self-documenting.
```suggestion
const MAX_SMS_SEND_ATTEMPTS = 2; // total attempts before giving up
// ...
{ method: WorkflowMethods.SMS, retryCount: { gte: MAX_SMS_SEND_ATTEMPTS } },
```

:yellow_circle: [testing] No tests cover the new retry / delete behaviour in packages/features/ee/workflows/api/scheduleSMSReminders.ts:1 (confidence: 72)
The handler gained three new code paths (retry increment on Twilio error, retry increment on empty-sid response, cleanup of `retryCount > 1` rows) with no test changes. A small unit/integration test around `handler()` that seeds a reminder with `retryCount = 1`, mocks Twilio to throw, and asserts (a) `retryCount` becomes 2 and (b) the next invocation deletes it would pin the contract and catch regressions in the OR-clause scoping fix above.

## Nitpicks

:white_circle: [consistency] Use console.error for failures instead of console.log in packages/features/ee/workflows/api/scheduleSMSReminders.ts:197 (confidence: 60)
The catch block logs via `console.log`; every other error log in this package uses `console.error` (or the project logger) which preserves stderr routing and stack propagation to the log aggregator. Also consider logging `reminder.id` so operators can correlate the failure with the row that was incremented.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 3 files, 1 shared Prisma model (`WorkflowReminder`), 1 additive migration (NOT NULL DEFAULT 0 — safe on Postgres ≥11 fast-default) | Sensitive Paths: 1 (`packages/prisma/migrations/*`)
AI-Authored Likelihood: LOW
