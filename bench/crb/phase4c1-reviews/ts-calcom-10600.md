## Summary
16 files changed, ~280 lines added, ~36 lines deleted. 9 findings (2 critical, 7 improvements).
Adds 2FA backup codes for Cal.com login and 2FA disable flows; storage-at-rest uses reversible symmetric encryption instead of one-way hashing, and code entropy is below NIST guidance.

## Critical
:red_circle: [security] Backup codes stored as reversibly-encrypted plaintext rather than one-way hashes in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:60 (confidence: 92)
The setup route persists backup codes via `symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY)`, and the authorize flow in `packages/features/auth/lib/next-auth-options.ts`:128 and the disable route in `apps/web/pages/api/auth/two-factor/totp/disable.ts`:59 both `symmetricDecrypt` them back into memory for an `Array.indexOf` compare. Backup codes are single-use second-factor bypass secrets — NIST SP 800-63B §5.1.2 and OWASP ASVS V2.8 treat them as memorized secrets that MUST be stored with a one-way password hash (bcrypt/argon2/scrypt). With the current design, any attacker who obtains a DB snapshot *and* `CALENDSO_ENCRYPTION_KEY` (one compromise surface — often a single leaked env file or SSRF to the orchestrator) instantly recovers every user's valid 2FA bypass codes. Hashing decouples the two compromises: stolen DB alone is unusable, and the encryption key becomes irrelevant for this column. Every existing encrypted column in this codebase already has the same property; reusing that pattern for *auth bypass tokens* is the weak link.
```suggestion
// At generation (setup.ts):
const backupCodes = Array.from({ length: 10 }, () => crypto.randomBytes(10).toString("hex"));
const hashedCodes = await Promise.all(backupCodes.map((c) => hash(c, 12))); // bcrypt
await prisma.user.update({
  where: { id: session.user.id },
  data: { backupCodes: JSON.stringify(hashedCodes), /* ... */ },
});
return res.json({ secret, keyUri, dataUri, backupCodes }); // plaintext returned ONCE

// At verification (next-auth-options.ts / disable.ts):
const hashes: string[] = JSON.parse(user.backupCodes);
let matchedIndex = -1;
for (let i = 0; i < hashes.length; i++) {
  if (hashes[i] && (await compare(credentials.backupCode.replaceAll("-", ""), hashes[i]))) {
    matchedIndex = i;
    break;
  }
}
if (matchedIndex === -1) throw new Error(ErrorCode.IncorrectBackupCode);
hashes[matchedIndex] = null; // invalidate
```
[References: NIST SP 800-63B §5.1.2; OWASP ASVS 4.0 V2.8; GitHub/Google/AWS all hash recovery codes]

:red_circle: [security] Backup code entropy is 40 bits, well below threshold for a factor-bypass secret in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:60 (confidence: 85)
`crypto.randomBytes(5).toString("hex")` produces 10 hex chars == 40 bits of entropy per code. Because a backup code fully bypasses the second factor, NIST SP 800-63B treats it as a memorized secret with a recommended 64-bit minimum, and OWASP ASVS V2.8.5 calls for ≥20 random characters (~80+ bits). Industry implementations are at 64-160 bits (GitHub 160, AWS 64, Google ≥80). With 10 valid codes × 40 bits and no per-code lock-out visible in this diff on the `/api/auth/two-factor/totp/disable` handler, a session-hijacked attacker or an attacker that bypasses per-IP rate limiting can brute-force a single user's codes in feasible time (2^40 / 10 ≈ 2^36 avg trials per user, parallelizable per-account). Increase to 8-10 bytes (64-80 bits) minimum; this is a one-line change and costs the user nothing since the code is displayed in XXXXX-XXXXX-style grouping.
```suggestion
// Use ≥8 bytes (64 bits) — 10 bytes gives 80 bits and formats cleanly as XXXXX-XXXXX-XXXXX-XXXXX.
const backupCodes = Array.from({ length: 10 }, () => crypto.randomBytes(10).toString("hex"));
// Update BackupCode.tsx minLength/maxLength to match the new length.
```
[References: NIST SP 800-63B §5.1.2; OWASP ASVS V2.8.5]

## Improvements
:yellow_circle: [security] Non-constant-time comparison of decrypted backup code in `packages/features/auth/lib/next-auth-options.ts`:149 (confidence: 78)
`backupCodes.indexOf(credentials.backupCode.replaceAll("-", ""))` compares secret material using V8's native string equality, which short-circuits on the first differing byte and leaks position information across multiple authentication attempts. The same pattern is in `apps/web/pages/api/auth/two-factor/totp/disable.ts`:67. Practical exploitation is hard on 10 codes, but once you switch to hashed storage (Critical #1) the fix is free — bcrypt `compare` / `timingSafeEqual` both run in constant time. Flagging so the fix lands together with the hashing change.
```suggestion
// After switching to hashed storage, iterate with bcrypt.compare (constant time per hash):
let matchedIndex = -1;
for (let i = 0; i < hashes.length; i++) {
  if (hashes[i] && (await bcrypt.compare(submitted, hashes[i]))) { matchedIndex = i; break; }
}
```

:yellow_circle: [security] No rate limiting on the 2FA-disable endpoint when backup code is the factor in `apps/web/pages/api/auth/two-factor/totp/disable.ts`:44 (confidence: 72)
The login path funnels through `checkRateLimitAndThrowError` in `next-auth-options.ts`, but the `/api/auth/two-factor/totp/disable` handler only guards on session + password. An attacker who has phished a password can freely retry 40-bit backup codes from within the authenticated session to disable 2FA and pivot. Add the same `checkRateLimitAndThrowError({ identifier: ... })` before the backup-code branch, keyed on user id.
```suggestion
if (user.twoFactorEnabled && req.body.backupCode) {
  await checkRateLimitAndThrowError({
    identifier: `2fa-disable-backup:${session.user.id}`,
    rateLimitingType: "core",
  });
  // ... existing decrypt + indexOf logic
}
```

:yellow_circle: [correctness] Setup endpoint silently overwrites existing backup codes and TOTP secret for already-enrolled users in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:63 (confidence: 80)
`/api/auth/two-factor/totp/setup` writes `backupCodes` and `twoFactorSecret` unconditionally, even if `user.twoFactorEnabled === true`. A user who re-opens the setup modal while 2FA is still enabled and then abandons the flow will have silently lost both their TOTP secret and their real (valid) backup codes — the authenticator app now produces codes the server doesn't accept, and the printed backup codes they saved earlier no longer work. The enable endpoint runs before the user confirms TOTP entry, so the window is wide. Guard the write: require `!user.twoFactorEnabled` (or require re-auth and a delete-first flow) before overwriting.
```suggestion
if (user.twoFactorEnabled) {
  return res.status(400).json({ error: ErrorCode.TwoFactorAlreadyEnabled });
}
```

:yellow_circle: [correctness] Leaked blob URL when the enable modal closes without hitting the Close button in `apps/web/components/settings/EnableTwoFactorModal.tsx`:80 (confidence: 62)
`URL.createObjectURL(textBlob)` is only revoked on the next setup call (`if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl)`). The Esc/backdrop close paths and `resetState` do not revoke. Add a `useEffect` cleanup and revoke inside `resetState`.
```suggestion
useEffect(() => {
  return () => { if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl); };
}, [backupCodesUrl]);

const resetState = () => {
  if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl);
  setBackupCodesUrl("");
  setBackupCodes([]);
  setPassword("");
  setErrorMessage(null);
  setStep(SetupStep.ConfirmPassword);
};
```

:yellow_circle: [correctness] `useState([])` infers `never[]`, erasing type-safety on backup-code data in `apps/web/components/settings/EnableTwoFactorModal.tsx`:71 (confidence: 70)
`const [backupCodes, setBackupCodes] = useState([]);` — TypeScript infers `never[]`, so `setBackupCodes(body.backupCodes)` and `backupCodes.map(formatBackupCode)` both silently cast from `any`. Annotate the generic.
```suggestion
const [backupCodes, setBackupCodes] = useState<string[]>([]);
```

:yellow_circle: [testing] Pre-existing flaky-test FIXME left unaddressed; new backup-code path lacks negative tests in `apps/web/playwright/login.2fa.e2e.ts`:46 (confidence: 65)
The diff adds a `// FIXME: this passes even when switch is not checked` note but ships it, and the accompanying `// TODO: add more backup code tests, e.g. login + disabling 2fa with backup` acknowledges missing coverage of the most security-critical path this PR introduces. At minimum add one happy-path login-with-backup-code test and one reuse-denied test, so regressions to the `backupCodes[index] = null` invalidation logic in `next-auth-options.ts`:151 are caught. Existing test exercises only the download/copy/close UI during setup.

:yellow_circle: [consistency] Password-toggle button removed from tab order; accessibility regression in `packages/ui/components/form/inputs/Input.tsx`:47 (confidence: 55)
Adding `tabIndex={-1}` to the show/hide-password toggle makes it unreachable by keyboard. Users on screen readers or keyboard-only navigation can no longer verify what they typed. The PR description notes this was "increasingly annoying while testing" — consider restoring tabbability and instead adjusting focus order (e.g., `tabIndex={0}` but last in the form, or an explicit label association). This is a shared UI component used beyond the 2FA flow, so the blast radius is every PasswordField in the product.
```suggestion
// Remove tabIndex={-1} and use aria-label; let users tab through but not submit.
<button
  className="text-emphasis h-9"
  type="button"
  aria-label={textLabel}
  onClick={() => toggleIsPasswordVisible()}>
```

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: authentication path for all users, shared PasswordField component across the product, new DB column on `users` table | Sensitive Paths: `apps/web/pages/api/auth/two-factor/**`, `packages/features/auth/lib/next-auth-options.ts`, `packages/prisma/migrations/**`
AI-Authored Likelihood: LOW
