## Summary
16 files changed, ~280 lines added, ~36 lines deleted. 7 findings (0 critical, 7 improvements, 0 nitpicks).
Functional 2FA backup-code implementation with correct crypto flow, but the rollout is incomplete: existing 2FA users have no way to generate backup codes, the download path leaks an unrevoked Blob URL holding plaintext codes, and the E2E suite skips the backup-code login/disable flows the PR is meant to ship.

## Improvements

:yellow_circle: [correctness] Existing 2FA users cannot generate backup codes — `missing_backup_codes` points them at a non-existent settings UI in `apps/web/components/settings/EnableTwoFactorModal.tsx`:181 (confidence: 90)
Backup codes are only generated inside `/api/auth/two-factor/totp/setup`, which is invoked during initial 2FA *enable*; every user who already had `twoFactorEnabled = true` before this migration has `backupCodes = NULL` forever, yet the error string `"No backup codes found. Please generate them in your settings."` promises a settings flow that this PR does not add. The realistic workaround (disable 2FA, then re-enable) requires the user to have working TOTP *and* temporarily removes 2FA protection between the two modal submissions.
```suggestion
// apps/web/pages/api/auth/two-factor/totp/regenerate-backup-codes.ts (new)
// Password + TOTP re-auth, then overwrite only `backupCodes`, leaving
// `twoFactorSecret` / `twoFactorEnabled` untouched.

// apps/web/components/settings/RegenerateBackupCodesModal.tsx (new)
// Reuse EnableTwoFactorModal's DisplayBackupCodes step.
```
<details><summary>More context</summary>

Two compounding problems:

1. **Broken last-mile.** The `incorrect_backup_code` and `missing_backup_codes` branches in `DisableTwoFactorModal.tsx` and `login.tsx` assume the user has an actionable "go generate them" path. They don't. From the user's perspective the error message lies.
2. **Security regression via workaround.** Telling the user to disable and re-enable 2FA as the way to obtain codes means there's a window during which their account is TOTP-less. If the goal of backup codes is to *reduce* lockout risk, the absence of a standalone regenerate flow partly defeats it.

Standard practice (GitHub, Google, GitLab) is a dedicated "Regenerate backup codes" action under security settings that requires password re-auth, leaves `twoFactorSecret` intact, and overwrites only the `backupCodes` column. The crypto primitives needed already exist (`symmetricEncrypt`, `crypto.randomBytes`) — this is a missing route + UI, not missing infrastructure.

It would also be worth marking the migration non-destructive: the new `backupCodes TEXT` column is nullable (good), but the rollout plan should either (a) ship the regenerate flow in the same PR, or (b) ship a banner to existing 2FA users telling them to do the disable/re-enable dance before they ever hit the "Lost access" button.
</details>

:yellow_circle: [security] Backup codes stored via reversible symmetric encryption instead of hashed in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:60 (confidence: 85)
`backupCodes` are persisted as `symmetricEncrypt(JSON.stringify(backupCodes), CALENDSO_ENCRYPTION_KEY)` and decrypted on every verification, meaning a single leak of `CALENDSO_ENCRYPTION_KEY` plus a DB dump yields every valid backup code for every user. Backup codes are functionally one-time passwords (verified by equality, never re-materialized), so the industry norm — and what GitHub/Google/GitLab do — is to hash each code individually (bcrypt / argon2 / scrypt) and compare with a constant-time check.
```suggestion
// setup.ts
import { hash } from "bcryptjs";
const backupCodes = Array.from({ length: 10 }, () => crypto.randomBytes(5).toString("hex"));
const backupCodeHashes = await Promise.all(backupCodes.map((c) => hash(c, 12)));
await prisma.user.update({
  where: { id: session.user.id },
  data: { backupCodes: JSON.stringify(backupCodeHashes), /* ... */ },
});
return res.json({ secret, keyUri, dataUri, backupCodes }); // only the cleartext is returned, never re-shown

// next-auth-options.ts
const hashes: string[] = JSON.parse(user.backupCodes);
const submitted = credentials.backupCode.replaceAll("-", "");
let matchedIndex = -1;
for (let i = 0; i < hashes.length; i++) {
  if (hashes[i] && (await compare(submitted, hashes[i]))) { matchedIndex = i; break; }
}
if (matchedIndex === -1) throw new Error(ErrorCode.IncorrectBackupCode);
hashes[matchedIndex] = null; // consume
await prisma.user.update({ where: { id: user.id }, data: { backupCodes: JSON.stringify(hashes) } });
```
<details><summary>More context</summary>

I acknowledge the codebase already uses `symmetricEncrypt` for `twoFactorSecret`, so the pattern is internally consistent. The distinction is that a TOTP secret *must* be reversible to compute codes server-side, whereas a backup code only ever needs to be compared — so there's no crypto reason to keep them recoverable.

Two secondary wins from hashing:

- Removes the server-side trust boundary on `CALENDSO_ENCRYPTION_KEY` for backup codes specifically. Even if the env leaks, attackers get salted hashes, not cleartext.
- Side-steps the non-constant-time `Array.prototype.indexOf` comparison in the current implementation, because bcrypt/scrypt `compare` is already constant-time.

Trade-off: bcrypt/argon2 per login with a backup code is ~100ms instead of ~1ms. That's fine for this path — backup codes are not in the hot login flow.

Not flagging as critical because it's consistent with the project's existing 2FA-secret storage and isn't directly exploitable absent a full-key compromise; raising it as an improvement because backup codes' semantics make hashing the unambiguously stronger choice.
</details>

:yellow_circle: [correctness] `backupCodesUrl` Blob is never revoked on modal close/unmount — plaintext backup codes remain reachable in browser memory in `apps/web/components/settings/EnableTwoFactorModal.tsx`:96 (confidence: 88)
`URL.createObjectURL(textBlob)` is called once per `handleSetup` success and only revoked by the replacement path (`if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl)`), so when the user clicks Cancel, clicks Close on the backup-codes step, or simply closes the dialog, the blob URL — whose contents are the newline-joined plaintext backup codes — stays alive for the life of the page. Anything that can read from the same origin (a later XSS, a browser extension, a heap dump) can `fetch(backupCodesUrl)` after the modal is gone.
```suggestion
// Inside EnableTwoFactorModal, alongside resetState:
useEffect(() => {
  return () => {
    if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl);
  };
}, [backupCodesUrl]);

// And in the Close button handler:
onClick={(e) => {
  e.preventDefault();
  if (backupCodesUrl) { URL.revokeObjectURL(backupCodesUrl); setBackupCodesUrl(""); }
  setBackupCodes([]);
  resetState();
  onEnable();
}}
```

:yellow_circle: [testing] E2E coverage skips the core backup-code flows the PR ships — only download/copy UI buttons are exercised in `apps/web/playwright/login.2fa.e2e.ts`:12 (confidence: 94)
The only new assertions check that the download button produces a file named `cal-backup-codes.txt` and that the copy button triggers a toast; there is no test that (a) a user can log in with a backup code, (b) a user can disable 2FA with a backup code, (c) a consumed backup code is rejected on re-use, or (d) the "Lost access" toggle swaps TOTP for backup-code input. The PR itself acknowledges this — `// TODO: add more backup code tests, e.g. login + disabling 2fa with backup`. Shipping a credentials feature whose primary verification paths have no test coverage means future refactors to `next-auth-options.ts`'s authorize() branching won't be caught by CI.
```suggestion
// Add tests covering:
// 1. enable 2fa, capture backup codes via download, logout, login with password + backup code (success)
// 2. re-use the same backup code → IncorrectBackupCode
// 3. disable 2fa via backup code (success), verify `backupCodes` is null post-disable
// 4. user without backupCodes attempting lost-access path → "missing_backup_codes" message
// 5. switch between TOTP and backup-code forms, confirm the other field is cleared
```

:yellow_circle: [correctness] `DisplayBackupCodes` step has no "I have saved these codes" confirmation — user can Close without downloading/copying and permanently lose them in `apps/web/components/settings/EnableTwoFactorModal.tsx`:258 (confidence: 85)
The Close button (`data-testid=backup-codes-close`) is enabled from first render of the DisplayBackupCodes step and calls `onEnable()` unconditionally; there is no required "I've saved these codes" checkbox or two-button confirmation pattern gating closure. Combined with the previous finding (no regenerate flow), a user who dismisses the modal without downloading is locked out of ever seeing those codes again — they'd have to disable and re-enable 2FA to produce a fresh set.
```suggestion
const [codesSaved, setCodesSaved] = useState(false);
// ...
<label className="mt-4 flex items-center gap-2 text-sm">
  <input type="checkbox" checked={codesSaved} onChange={(e) => setCodesSaved(e.target.checked)} />
  {t("backup_codes_saved_confirmation")}
</label>
// then in the Close button:
disabled={!codesSaved}
```

:yellow_circle: [correctness] Copy button fires `showToast("backup_codes_copied", "success")` without awaiting `navigator.clipboard.writeText` in `apps/web/components/settings/EnableTwoFactorModal.tsx`:272 (confidence: 86)
`navigator.clipboard.writeText` returns a Promise that can reject (insecure context over HTTP, permissions denied, or clipboard-blocked iframes); the current handler ignores the return value and unconditionally renders success, so the user sees "Backup codes copied!" while the clipboard actually contains the previous value. For a credentials feature whose whole point is "let the user capture these codes once", a silent copy failure is the worst UX outcome.
```suggestion
onClick={async (e) => {
  e.preventDefault();
  try {
    await navigator.clipboard.writeText(backupCodes.map(formatBackupCode).join("\n"));
    showToast(t("backup_codes_copied"), "success");
  } catch {
    showToast(t("copy_to_clipboard_failed"), "error");
  }
}}
```

:yellow_circle: [consistency] `useState([])` on `backupCodes` infers `never[]`, suppressing type checks on `body.backupCodes` in `apps/web/components/settings/EnableTwoFactorModal.tsx`:64 (confidence: 85)
Because `setBackupCodes([])` is declared without a type argument, TS widens the state to `never[]`; `body.backupCodes` flows in from `await response.json()` (untyped) and gets stored without any compile-time contract that it's `string[]`. A future backend change that returns, say, `backupCodes: { code: string; used: boolean }[]` would silently break the `formatBackupCode(code)` call without a single TS error.
```suggestion
const [backupCodes, setBackupCodes] = useState<string[]>([]);
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: auth-critical path (login + 2FA disable + setup), adds `backupCodes TEXT` to `users`, changes NextAuth `authorize()` branching, touches 16 files across apps/web and packages/features | Sensitive Paths: `packages/features/auth/`, `apps/web/pages/api/auth/two-factor/`, `packages/prisma/schema.prisma`, `packages/prisma/migrations/*`
AI-Authored Likelihood: LOW (consistent with project patterns; self-aware TODOs and FIXMEs flagging known test gaps suggest iterative human authorship)

(2 additional findings below confidence threshold: non-constant-time `Array.prototype.indexOf` comparison of backup codes — likely not practically exploitable given the small match space and existing rate limiting; and 40-bit backup-code entropy — acceptable given 10-code total and password-gated access, but below the 64-bit floor some services use.)
