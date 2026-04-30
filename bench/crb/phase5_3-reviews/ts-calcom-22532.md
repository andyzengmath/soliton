## Summary
17 files changed, 379 lines added, 31 lines deleted. 6 findings (5 critical, 1 improvement).
AI-authored (Devin) PR introduces a `deleteCache` tRPC mutation, a new `CredentialActionsDropdown` UI, and a `CalendarCache.updatedAt` migration; review uncovered authorization gaps, a no-op Prisma `updateMany`, a feature-flag-bypass instantiation, a type-mismatch on the new repository method, and an unsafe shell helper script.

## Critical

:red_circle: [security] Missing team/org credential authorization (broken access control / IDOR) in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:1 (confidence: 85)
The handler authorizes only by `{ id: credentialId, userId: user.id }`. In Cal.com, calendar credentials can be owned by a team/organization (where `Credential.teamId` is set and `userId` is null, or where the credential belongs to a team the user is not authorized to administer). Two distinct problems result: (1) IDOR-adjacent — a non-admin team member who is not the credential owner cannot delete a team-shared cache, but conversely a user whose `userId` matches a team-administered credential can wipe cache for a team-shared resource without a team-admin role check; (2) Privilege gap — legitimate team/org admins receive "access denied" for credentials they administer. Cache deletion is also a state-changing operation that forces expensive re-syncs against Google/Office365, enabling sustained API quota burn.
```suggestion
import { TRPCError } from "@trpc/server";
import { prisma } from "@calcom/prisma";
import { MembershipRole } from "@calcom/prisma/enums";

export const deleteCacheHandler = async ({ ctx, input }: DeleteCacheOptions) => {
  const { user } = ctx;
  const { credentialId } = input;

  const credential = await prisma.credential.findUnique({
    where: { id: credentialId },
    select: { id: true, userId: true, teamId: true },
  });

  if (!credential) {
    throw new TRPCError({ code: "NOT_FOUND" });
  }

  const isOwner = credential.userId === user.id;
  let isTeamAdmin = false;
  if (credential.teamId) {
    const membership = await prisma.membership.findFirst({
      where: {
        userId: user.id,
        teamId: credential.teamId,
        accepted: true,
        role: { in: [MembershipRole.ADMIN, MembershipRole.OWNER] },
      },
    });
    isTeamAdmin = !!membership;
  }

  if (!isOwner && !isTeamAdmin) {
    throw new TRPCError({ code: "FORBIDDEN" });
  }

  await prisma.calendarCache.deleteMany({ where: { credentialId } });
  return { success: true };
};
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/639.html]

:red_circle: [security] Insecure /tmp file usage and unsanitized sed write enable file hijacking and env injection in scripts/test-gcal-webhooks.sh:1 (confidence: 90)
Three compounding issues in the new dev script: (1) `LOG_FILE="/tmp/tmole.log"` is a fixed predictable path in a world-writable directory — on a multi-user system or shared CI runner, an attacker can pre-create `/tmp/tmole.log` as a symlink to a victim-owned file (e.g., `~/.ssh/authorized_keys`, `~/.bashrc`) and the script's redirect will follow the symlink and clobber the target (CWE-377 / CWE-59). (2) `sed -i '' -E "s|^GOOGLE_WEBHOOK_URL=.*|GOOGLE_WEBHOOK_URL=$TUNNEL_URL|" "$ENV_FILE"` interpolates `$TUNNEL_URL` (harvested from a third-party tunnelmole response) unescaped into the sed replacement; a malicious tunnel response containing `|`, `&`, `\` or newlines can corrupt `.env` or inject arbitrary lines such as `DATABASE_URL=postgres://attacker/...` that are loaded on next dotenv parse. (3) `sed -i ''` is BSD/macOS syntax; on Linux it interprets `''` as an input filename, producing different (often destructive) behavior.
```suggestion
set -euo pipefail

LOG_FILE="$(mktemp -t tmole.XXXXXX)"
trap 'rm -f "$LOG_FILE"' EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../.env"

if ! [[ "$TUNNEL_URL" =~ ^https://[a-zA-Z0-9.-]+\.tunnelmole\.net(/.*)?$ ]]; then
  echo "Refusing untrusted TUNNEL_URL: $TUNNEL_URL" >&2
  exit 1
fi

python3 - "$ENV_FILE" "$TUNNEL_URL" <<'PY'
import sys, re, pathlib
path, url = sys.argv[1], sys.argv[2]
p = pathlib.Path(path)
text = p.read_text() if p.exists() else ""
new, n = re.subn(r"(?m)^GOOGLE_WEBHOOK_URL=.*$", f"GOOGLE_WEBHOOK_URL={url}", text)
if n == 0:
    new = (text + ("\n" if text and not text.endswith("\n") else "")) + f"GOOGLE_WEBHOOK_URL={url}\n"
p.write_text(new)
PY
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/377.html, https://cwe.mitre.org/data/definitions/59.html]

:red_circle: [correctness] updateManyByCredentialId called with empty data {} silently fails to bump updatedAt in packages/app-store/googlecalendar/lib/CalendarService.ts:1022 (confidence: 95)
The new line `await SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {});` passes an empty object as the update payload. The intent (per the new comment) is to refresh `SelectedCalendar.updatedAt` for all rows under this credential via the `@updatedAt` decorator. However, Prisma's `updateMany` with `data: {}` does not emit a SQL `UPDATE ... SET` with a synthesized `updatedAt = NOW()` — `@updatedAt` only fires when at least one other column is being set, so an empty `data` is a no-op at the DB level. The `updatedAt` column will never be refreshed by this call, defeating the feature; consumers reading the timestamp will see permanently-stale values.
```suggestion
// In CalendarService.ts
await SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {
  updatedAt: new Date(),
});
```
(Also fix the parameter type — see related Critical finding on the repository method signature.)

:red_circle: [correctness] CalendarCacheRepository instantiated directly, bypassing the feature-flag mock in packages/trpc/server/routers/viewer/calendars/connectedCalendars.handler.ts:27 (confidence: 90)
`new CalendarCacheRepository()` is constructed unconditionally in the handler. The rest of the codebase resolves the calendar-cache repository through a factory that returns `CalendarCacheRepositoryMock` when the calendar-cache feature flag is disabled (which is why the mock exists and was updated alongside the interface in this same PR). By hard-coding the real repository here, the handler will issue live Prisma `groupBy` queries against `CalendarCache` even on deployments where the feature is disabled. On environments where the feature is off and either the table is absent or the new `updatedAt` column has not been migrated yet, every `connectedCalendars` tRPC call will throw, breaking the calendar settings page for all users.
```suggestion
// Use the same factory the rest of the codebase uses to obtain a repository instance:
import { getCalendarCacheRepository } from "@calcom/features/calendar-cache/calendar-cache.repository.factory";

const cacheRepository = await getCalendarCacheRepository();
const cacheStatuses = await cacheRepository.getCacheStatusByCredentialIds(credentialIds);
```

:red_circle: [cross-file-impact] Wrong Prisma type on updateManyByCredentialId — UpdateInput vs UpdateManyMutationInput in packages/lib/server/repository/selectedCalendar.ts:400 (confidence: 92)
`updateManyByCredentialId` declares its `data` parameter as `Prisma.SelectedCalendarUpdateInput`, but `prisma.selectedCalendar.updateMany({ data })` requires `Prisma.SelectedCalendarUpdateManyMutationInput` (or `Prisma.SelectedCalendarUncheckedUpdateManyInput`). These are distinct generated types — `UpdateInput` is the per-record shape used by `update`, while `updateMany` constrains to a stricter shape that excludes nested writes/relation operations. TypeScript will emit a compile error at the `updateMany` call inside this method as soon as a caller passes any non-empty payload that exercises an `UpdateInput`-only field; the empty `{}` from `CalendarService.ts` happens not to trip it today, but the method's public type signature is structurally wrong and any future caller will be misled.
```suggestion
static async updateManyByCredentialId(
  credentialId: number,
  data: Prisma.SelectedCalendarUpdateManyMutationInput
) {
  return await prisma.selectedCalendar.updateMany({
    where: { credentialId },
    data,
  });
}
```

## Improvements

:yellow_circle: [correctness] deleteCacheMutation does not invalidate connectedCalendars query after success in packages/features/apps/components/CredentialActionsDropdown.tsx:30 (confidence: 88)
After a successful cache deletion, `deleteCacheMutation.onSuccess` calls `onSuccess?.()` and shows a toast but never invalidates the `connectedCalendars` tRPC query. The `cacheUpdatedAt` timestamp shown in the dropdown is sourced from that query's cached data, so after deletion the UI continues to display the old (now stale) timestamp until an unrelated refetch — the user sees no visual confirmation beyond the toast. Compare with `disconnectMutation` in the same file, which correctly calls `await utils.viewer.calendars.connectedCalendars.invalidate()` in `onSettled`.
```suggestion
const utils = trpc.useUtils(); // move this declaration above deleteCacheMutation

const deleteCacheMutation = trpc.viewer.calendars.deleteCache.useMutation({
  onSuccess: () => {
    showToast(t("cache_deleted_successfully"), "success");
    onSuccess?.();
  },
  onError: () => {
    showToast(t("error_deleting_cache"), "error");
  },
  async onSettled() {
    await utils.viewer.calendars.connectedCalendars.invalidate();
  },
});
```

## Risk Metadata
Risk Score: 74/100 (HIGH) | Blast Radius: ~33 estimated importers across 11 production files (CalendarService, selectedCalendar repository, user repository, getConnectedDestinationCalendars, connectedCalendars handler, settings wrapper) | Sensitive Paths: deleteCache.handler.ts (credential ownership), prisma migration adding NOT NULL column, schema.prisma change, new authedProcedure mutation surface, repository updateMany with open-ended input
AI-Authored Likelihood: HIGH (Devin AI session — branch `devin/calendar-cache-tooltip-1752595047`; templated boilerplate in CredentialActionsDropdown and deleteCache.handler; auto-generated migration warning comment retained verbatim)

(9 additional findings below confidence threshold)
