## Summary
17 files changed, 379 lines added, 31 lines deleted. 5 findings (2 critical, 3 improvements).
Adds calendar-cache status UI plus a `deleteCache` tRPC mutation; core concerns are an unguarded write on a hot availability path, a raw `Error` throw that bypasses tRPC error mapping, and a credential-lookup scope that silently excludes team/delegation credentials the UI still offers to delete.

## Critical
:red_circle: [correctness] Unnecessary `SelectedCalendar.updateMany` on every availability fetch in packages/app-store/googlecalendar/lib/CalendarService.ts:1022 (confidence: 92)
`fetchAvailabilityAndSetCache` is called on the booking hot path (every availability/slot lookup). The new `SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {})` issues an `UPDATE "SelectedCalendar" SET ... WHERE credentialId = $1` on every invocation, even when nothing changed and when the cache was already fresh. Worse, the PR description itself states "SelectedCalendar.updatedAt ... is not displayed in frontend. Frontend uses cacheUpdatedAt from CalendarCache. Consider if SelectedCalendar.updatedAt update should be removed." — the write is acknowledged as unused. This adds write amplification (and row-lock contention on the hot `SelectedCalendar` table) for no consumer.
```suggestion
      const data = await this.fetchAvailability(parsedArgs);
      await this.setAvailabilityInCache(parsedArgs, data);
    }
  }
```

:red_circle: [security] `deleteCacheHandler` throws raw `Error`, not `TRPCError`, and conflates not-found with forbidden in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:22 (confidence: 90)
`throw new Error("Credential not found or access denied")` surfaces to the client as an uncoded `INTERNAL_SERVER_ERROR` (tRPC's default for non-`TRPCError` throws), so the UI cannot distinguish expected authorization failures from server faults, and error-logging/alerting treats benign 403s as 500s. It also lumps NOT_FOUND and FORBIDDEN under one opaque message — an unauthenticated-path oracle the rest of this router avoids. Use `TRPCError` with the right code so the `onError` toast path in `CredentialActionsDropdown` can behave correctly.
```suggestion
import { TRPCError } from "@trpc/server";

// ...
  if (!credential) {
    throw new TRPCError({ code: "NOT_FOUND", message: "Credential not found or access denied" });
  }
```

## Improvements
:yellow_circle: [correctness] `deleteCache` authorization excludes team/delegation credentials that the UI still shows a delete button for in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:12 (confidence: 88)
`prisma.credential.findFirst({ where: { id: credentialId, userId: user.id } })` only matches credentials whose `userId` equals the caller. Team credentials (`userId = null`, `teamId` set) and some delegation credentials have no `userId`, so the lookup returns `null` and the mutation rejects. But `CredentialActionsDropdown` only hides the cache row behind `hasCache = isGoogleCalendar && cacheUpdatedAt` — it does not gate on ownership — so a user looking at a team-owned Google Calendar credential will see "Delete cached data", click it, and get an opaque error. Either (a) widen the check to include team membership via `CredentialRepository`, or (b) hide the cache controls in the UI when the credential isn't personally owned.
```suggestion
  const credential = await prisma.credential.findFirst({
    where: {
      id: credentialId,
      OR: [
        { userId: user.id },
        { team: { members: { some: { userId: user.id, accepted: true } } } },
      ],
    },
  });
```

:yellow_circle: [testing] No unit or integration tests for the new tRPC mutation, repository method, or dropdown in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:1 (confidence: 86)
This PR introduces a new write-path mutation (`deleteCache`), a new repository method (`getCacheStatusByCredentialIds`), and a credential-scoped UI control — none covered by tests. The PR body explicitly notes "Local testing was limited due to no installed calendar integrations — end-to-end verification ... is essential," which in practice means the authorization branch, the Prisma `groupBy` aggregate, and the delegation/team-credential UI gating have never been executed. At minimum, add a handler test that asserts (1) non-owner callers are rejected with the correct tRPC code, (2) `deleteMany` is scoped to the matched credential, and (3) `getCacheStatusByCredentialIds` handles empty input and multi-credential aggregation.

:yellow_circle: [consistency] `scripts/test-gcal-webhooks.sh` uses BSD-only `sed -i ''` in scripts/test-gcal-webhooks.sh:63 (confidence: 87)
`sed -i '' -E "s|..."` is BSD/macOS syntax; on GNU sed (Linux, most CI containers, and Docker dev environments) the empty `''` is consumed as the script rather than the backup-suffix argument, producing `sed: -e expression #1, char 0: no previous regular expression`. Since the rest of the script targets a tunneling dev loop, it will silently fail for Linux contributors. Either switch to portable `sed` (write to a temp file and `mv`) or fork on `uname`.
```suggestion
if grep -q '^GOOGLE_WEBHOOK_URL=' "$ENV_FILE"; then
  tmp=$(mktemp)
  sed -E "s|^GOOGLE_WEBHOOK_URL=.*|GOOGLE_WEBHOOK_URL=$TUNNEL_URL|" "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
else
  echo "GOOGLE_WEBHOOK_URL=$TUNNEL_URL" >> "$ENV_FILE"
fi
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: UserRepository + SelectedCalendarRepository + calendars tRPC router (widely imported); schema migration on `CalendarCache` | Sensitive Paths: `packages/prisma/migrations/**`, new authed tRPC mutation, `google-calendar` service hot path
AI-Authored Likelihood: HIGH (PR body explicitly credits a Devin session; style signals — duplicate `Dialog` import, redundant `|| null`, dead `SelectedCalendar.updatedAt` write flagged by the author themselves — are consistent with agent-generated scaffolding that wasn't pruned)
