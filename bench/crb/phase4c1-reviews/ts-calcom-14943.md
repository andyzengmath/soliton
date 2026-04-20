## Summary
3 files changed, 27 lines added, 5 lines deleted. 6 findings (4 critical, 2 improvements, 0 nitpicks).
PR adds a retryCount cap to the SMS reminder cron, but the OR clause in deleteMany omits the SMS method filter and the increment path is non-atomic and double-fire-prone — together these can silently drop customer reminders across all notification methods.

## Critical

:red_circle: [correctness] deleteMany OR branch deletes non-SMS reminders with retryCount > 1 in packages/features/ee/workflows/api/scheduleSMSReminders.ts:28 (confidence: 98)
The second OR branch `{ retryCount: { gt: 1 } }` has no `method: WorkflowMethods.SMS` constraint. WorkflowReminder stores SMS, EMAIL, and WHATSAPP rows in one table. As soon as any sibling scheduler (scheduleEmailReminders.ts, scheduleWhatsappReminders.ts) or any future code path sets retryCount > 1 on a non-SMS row, this SMS cron will silently hard-delete it. The SMS handler is deleting rows it does not own.
```suggestion
  await prisma.workflowReminder.deleteMany({
    where: {
      OR: [
        {
          method: WorkflowMethods.SMS,
          scheduledDate: {
            lte: dayjs().toISOString(),
          },
        },
        {
          method: WorkflowMethods.SMS,
          retryCount: {
            gt: 1,
          },
        },
      ],
    },
  });
```

:red_circle: [correctness] Non-atomic read-modify-write on retryCount creates race under overlapping cron runs in packages/features/ee/workflows/api/scheduleSMSReminders.ts:175 (confidence: 92)
Both increment sites compute `reminder.retryCount + 1` where `reminder.retryCount` is the value read by the initial findMany at the top of the handler. If two cron invocations overlap (slow run + next scheduled run both reach the same reminder), both read retryCount=0 and both write retryCount=1 — only one failure is counted instead of two. Reminders may never reach the deletion threshold, defeating the whole purpose of this PR. Prisma supports an atomic `increment` operator that avoids this entirely.
```suggestion
          await prisma.workflowReminder.update({
            where: {
              id: reminder.id,
            },
            data: {
              retryCount: {
                increment: 1,
              },
            },
          });
```

:red_circle: [correctness] Double-increment window when else-branch update throws and catch block then increments in packages/features/ee/workflows/api/scheduleSMSReminders.ts:178 (confidence: 95)
The `else` branch awaits a Prisma update that increments retryCount; if that update itself throws (DB timeout, deadlock, connection blip), execution falls into the outer `catch`, which unconditionally runs a second `retryCount + 1` write from the same stale base. Combined with the `gt: 1` delete threshold, a single failed attempt followed by a transient DB hiccup can push retryCount from 0 to 2 in one cron pass and cause the reminder to be deleted on the next run — after only one real scheduling attempt. Restructure so only one retry-increment site runs per attempt; combining with the atomic-increment fix above makes this simpler.
```suggestion
    let schedulingSucceeded = false;
    try {
      const scheduledSMS = await twilio.scheduleSMS(/* ... */);
      if (scheduledSMS.sid) {
        await prisma.workflowReminder.update({
          where: { id: reminder.id },
          data: { scheduled: true, referenceId: scheduledSMS.sid },
        });
        schedulingSucceeded = true;
      }
    } catch (error) {
      console.log(`Error scheduling SMS with error ${error}`);
    }
    if (!schedulingSucceeded) {
      await prisma.workflowReminder.update({
        where: { id: reminder.id },
        data: { retryCount: { increment: 1 } },
      });
    }
```

:red_circle: [testing] No tests added for retry-count gating that can silently drop customer reminders in packages/features/ee/workflows/api/scheduleSMSReminders.ts:28 (confidence: 95)
This PR introduces three new behaviors — conditional deletion on `retryCount > 1`, increment on missing Twilio SID, and increment in the catch block — and does not add any automated test. The feature's whole job is to permanently hard-delete rows after 2 failed attempts, which is the exact kind of logic that must not regress silently. An off-by-one in the threshold, a mismatch between increment sites, or the missing method filter (see finding 1) would all go undetected without tests. At minimum, cover: (a) threshold semantics (retryCount=1 survives, retryCount=2 is deleted), (b) sid-missing increment, (c) catch-path increment, (d) OR-clause shape asserting both branches are method-scoped to SMS.
```suggestion
// scheduleSMSReminders.test.ts
it("deleteMany keeps both OR branches scoped to WorkflowMethods.SMS", async () => {
  const deleteManyMock = jest.fn().mockResolvedValue({ count: 0 });
  prismaMock.workflowReminder.deleteMany = deleteManyMock;
  await handler(mockReq, mockRes);
  const { where } = deleteManyMock.mock.calls[0][0];
  expect(where.OR).toHaveLength(2);
  expect(where.OR[0]).toMatchObject({ method: WorkflowMethods.SMS });
  expect(where.OR[1]).toMatchObject({ method: WorkflowMethods.SMS, retryCount: { gt: 1 } });
});

it("increments retryCount when Twilio returns no sid", async () => {
  twilioMock.scheduleSMS.mockResolvedValue({ sid: null });
  prismaMock.workflowReminder.findMany.mockResolvedValue([{ id: 1, retryCount: 0, /* ... */ }]);
  await handler(mockReq, mockRes);
  expect(prismaMock.workflowReminder.update).toHaveBeenCalledWith(
    expect.objectContaining({ where: { id: 1 }, data: expect.objectContaining({ retryCount: expect.anything() }) }),
  );
});

it("increments retryCount exactly once when scheduling throws", async () => {
  twilioMock.scheduleSMS.mockRejectedValue(new Error("429 rate limit"));
  prismaMock.workflowReminder.findMany.mockResolvedValue([{ id: 2, retryCount: 0, /* ... */ }]);
  await expect(handler(mockReq, mockRes)).resolves.not.toThrow();
  expect(prismaMock.workflowReminder.update).toHaveBeenCalledTimes(1);
});
```

## Improvements

:yellow_circle: [correctness] Prisma update in catch block is unguarded and will abort the reminder batch on DB failure in packages/features/ee/workflows/api/scheduleSMSReminders.ts:184 (confidence: 88)
The catch block now runs `await prisma.workflowReminder.update(...)`. If this write rejects (DB unavailable, deadlock, constraint error), the exception propagates out of the catch and — since there is no try/catch scoping the per-reminder loop iteration — aborts processing of every remaining reminder in the batch. Before this PR the catch block only logged, so this is a newly introduced failure mode that can cause a single bad DB operation to block an entire cron run's worth of SMS reminders. Either wrap the update in its own try/catch, or use the restructure suggested in the double-increment finding which keeps the increment outside the scheduling catch block.
```suggestion
    } catch (error) {
      console.log(`Error scheduling SMS with error ${error}`);
      try {
        await prisma.workflowReminder.update({
          where: { id: reminder.id },
          data: { retryCount: { increment: 1 } },
        });
      } catch (updateError) {
        console.log(`Failed to increment retryCount for reminder ${reminder.id}: ${updateError}`);
      }
    }
```

:yellow_circle: [correctness] findMany does not exclude reminders at or above the deletion threshold in packages/features/ee/workflows/api/scheduleSMSReminders.ts:53 (confidence: 82)
After the deleteMany runs, the findMany selects unscheduled SMS reminders with no upper bound on `retryCount`. If the deleteMany is ever skipped, partially rolled back, or races with a concurrent writer, rows at retryCount=2 are fetched, reprocessed, and incremented to retryCount=3 — wasting Twilio quota and producing log noise for reminders that are about to be purged. Adding `retryCount: { lte: 1 }` to the findMany where-clause makes the query self-consistent with the deletion policy and provides a small defense in depth.
```suggestion
  const unscheduledReminders = (await prisma.workflowReminder.findMany({
    where: {
      method: WorkflowMethods.SMS,
      scheduled: false,
      scheduledDate: {
        lte: dayjs().add(7, "day").toISOString(),
      },
      retryCount: {
        lte: 1,
      },
    },
    select: {
      ...select,
      retryCount: true,
    },
  })) as (PartialWorkflowReminder & { retryCount: number })[];
```

## Conflicts
None — security, correctness, cross-file-impact, and test-quality agents all agree on the central bugs.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: Prisma schema migration + cron handler that touches all WorkflowReminder rows across SMS/EMAIL/WHATSAPP; customer-visible notification loss on failure | Sensitive Paths: packages/prisma/migrations/* (schema change with NOT NULL + DEFAULT 0 — safe backfill), packages/features/ee/workflows/* (notification delivery)
AI-Authored Likelihood: LOW

Recommendation: request-changes. The OR-clause method leak, non-atomic increment, and double-increment path are a combination that can silently delete customer reminders across notification methods and miscount attempts. The underlying PR intent is sound — cap retries — but the three fixes together (method guard on both OR branches, `increment: 1` atomic operator, single increment site via the restructure) are small and low-risk and should land before merge, along with at least the four tests listed above.
