## Summary
3 files changed, 38 lines added, 6 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
PR adds `retryCount` to `WorkflowReminder` and cleans up after 2 SMS send failures; the new delete clause is missing a `method` filter so it can reap reminders of any method, and the retry paths ship without tests.

## Critical
:red_circle: [correctness] `retryCount > 1` delete branch has no `method` filter — can delete non-SMS reminders in `packages/features/ee/workflows/api/scheduleSMSReminders.ts`:30 (confidence: 95)
The refactored `deleteMany` uses `OR: [{ method: SMS, scheduledDate: past }, { retryCount: { gt: 1 } }]`. The second branch has no `method` constraint and no `scheduledDate` constraint. The `WorkflowReminder` table is shared across SMS, EMAIL, and WHATSAPP workflows, and the new `retryCount` column lives on every row regardless of method. Today only this SMS cron increments `retryCount`, so the damage is latent — but the instant any other handler (the existing email/WhatsApp crons, a future feature, or a backfill that sets a non-zero default) writes `retryCount > 1`, this SMS cron silently deletes those rows on its next tick. The stated intent is "delete SMS reminders that failed twice", so the method predicate belongs on the retry branch too.
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
:yellow_circle: [correctness] Reminder is attempted a third time before the delete clause fires in `packages/features/ee/workflows/api/scheduleSMSReminders.ts`:47 (confidence: 90)
The fetch for `unscheduledReminders` has no upper bound on `retryCount`, so a reminder at `retryCount = 1` (already one prior failure) is picked up and a third send is attempted in the same cron run that bumps it to `retryCount = 2`. The delete only fires on the *following* cron run when `retryCount > 1` matches. The PR description states "after scheduling failed twice we delete", which readers will interpret as "at most two attempts"; current logic permits three (0→1, 1→2, then deleted next cycle). Either lower the threshold to `gt: 0` or exclude exhausted rows from the fetch.
```suggestion
  const unscheduledReminders = (await prisma.workflowReminder.findMany({
    where: {
      method: WorkflowMethods.SMS,
      scheduled: false,
      scheduledDate: { lte: dayjs().add(7, "day").toISOString() },
      retryCount: { lt: 2 },
    },
    select: { ...select, retryCount: true },
  })) as (PartialWorkflowReminder & { retryCount: number })[];
```

:yellow_circle: [testing] No tests cover the new retry/delete paths or the `gt: 1` boundary in `packages/features/ee/workflows/api/scheduleSMSReminders.ts`:28 (confidence: 92)
Three new behaviors are introduced with zero test coverage: (1) the OR-clause cleanup that deletes on `retryCount > 1`, (2) the else-branch increment when Twilio returns no `sid`, and (3) the catch-block increment on thrown errors. The exact boundary (`gt: 1` vs `gte: 1`, i.e. "delete after failing twice" vs "delete after failing once") is a bare integer literal — a future refactor can silently flip the contract. Add a unit/integration test for `scheduleSMSReminders.ts` that mocks Prisma and the SMS provider and pins each path, including a regression test that a reminder at `retryCount: 1` survives cleanup and only a row at `retryCount: 2` is deleted.
```suggestion
// packages/features/ee/workflows/api/__tests__/scheduleSMSReminders.test.ts
it("increments retryCount when SMS provider returns no sid", async () => {
  smsSender.mockResolvedValue({ sid: null });
  prisma.workflowReminder.findMany.mockResolvedValue([{ id: "r1", retryCount: 0, ...rest }]);
  await handler(req, res);
  expect(prisma.workflowReminder.update).toHaveBeenCalledWith({
    where: { id: "r1" },
    data: { retryCount: 1 },
  });
});

it("increments retryCount when SMS provider throws", async () => {
  smsSender.mockRejectedValue(new Error("Twilio 500"));
  prisma.workflowReminder.findMany.mockResolvedValue([{ id: "r2", retryCount: 1, ...rest }]);
  await handler(req, res);
  expect(prisma.workflowReminder.update).toHaveBeenCalledWith({
    where: { id: "r2" },
    data: { retryCount: 2 },
  });
});

it("retains reminder at retryCount 1 but deletes at retryCount 2", async () => {
  await handler(req, res);
  const deleteCall = prisma.workflowReminder.deleteMany.mock.calls[0][0];
  const retryClause = deleteCall.where.OR.find((c) => c.retryCount);
  expect(retryClause.retryCount).toEqual({ gt: 1 });
});
```

:yellow_circle: [correctness] `else` and `catch` blocks both issue a `retryCount` increment using the same stale in-memory value in `packages/features/ee/workflows/api/scheduleSMSReminders.ts`:178 (confidence: 82)
The structure is `try { if (sid) { mark scheduled } else { update retryCount+1 } } catch { update retryCount+1 }`. When the Twilio call succeeds without a `sid`, the `else` update runs; if that Prisma `update` itself throws (DB timeout, connection reset), execution falls into the `catch`, which runs a second `update` writing the same stale `reminder.retryCount + 1`. Because both writes use the same number the DB isn't double-incremented in this narrow case, but the catch path then logs `Error scheduling SMS with error …` — misleading, since the failure was a DB error, not an SMS error. Centralize the retry-count bump in one place (either by throwing from the `else` branch so the `catch` alone owns the increment, or by moving the bump to a `finally`-style helper with a flag).
```suggestion
try {
  const scheduledSMS = await twilio.scheduleSMS(...);
  if (scheduledSMS?.sid) {
    await prisma.workflowReminder.update({
      where: { id: reminder.id },
      data: { scheduled: true, referenceId: scheduledSMS.sid },
    });
  } else {
    throw new Error("Twilio returned no SID");
  }
} catch (error) {
  await prisma.workflowReminder.update({
    where: { id: reminder.id },
    data: { retryCount: reminder.retryCount + 1 },
  });
  console.log(`Error scheduling SMS with error ${error}`);
}
```

## Risk Metadata
Risk Score: 42/100 (MEDIUM) | Blast Radius: ~3 direct importers for `scheduleSMSReminders.ts`; `schema.prisma` drives generated Prisma client used across the monorepo | Sensitive Paths: `packages/prisma/migrations/20240508134359_add_retry_count_to_workflow_reminder/migration.sql` matches `*migration*`
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold: unstructured `console.log(error)` may leak Twilio SIDs and attendee phone numbers; transient vs permanent Twilio failures treated identically so outages silently reap reminders after 2 ticks; an attacker controlling the recipient phone can self-delete their own reminder evidence; cron endpoint auth/rate-limit not strengthened despite new destructive path; scheduleEmailReminders/scheduleWhatsAppReminders remain inconsistent with the new `retryCount` contract; retry increment also fires for DB/infra errors unrelated to SMS delivery.)
