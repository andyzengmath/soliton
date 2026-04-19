## Summary
16 files changed, 280 lines added, 36 lines deleted. 9 findings (2 critical, 4 improvements, 3 nitpicks).
Adds 2FA backup codes (generation, encrypted storage, login + disable flows, setup UI, e2e test); security fundamentals need hardening before ship — codes are reversibly encrypted (not hashed) and the verification path has no rate limiting, which combined with 40-bit entropy is brute-forceable.

## Critical

:red_circle: [security] Backup codes stored reversibly encrypted instead of hashed in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:60 (confidence: 90)
`symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY)` persists the plaintext codes under a single symmetric key. If `CALENDSO_ENCRYPTION_KEY` is ever exposed (log capture, IaC leak, repo checkout + DB snapshot), every user's backup codes are recoverable — defeating their purpose as a last-line-of-defense factor. The same pattern on line ~152 of `next-auth-options.ts` requires decrypting to verify, so attackers who compromise the key also bypass 2FA for every user at once. GitHub, Google, and Okta all hash recovery codes with a slow KDF (bcrypt/argon2) so DB/key compromise cannot reveal them — only verify them.
```suggestion
// setup.ts — store hashes, return plaintext to the user once
import { hash } from "bcryptjs";
const backupCodes = Array.from(Array(10), () => crypto.randomBytes(8).toString("hex"));
const backupCodeHashes = await Promise.all(backupCodes.map((c) => hash(c, 10)));
await prisma.user.update({
  where: { id: session.user.id },
  data: {
    backupCodes: JSON.stringify(backupCodeHashes),
    twoFactorEnabled: false,
    twoFactorSecret: symmetricEncrypt(secret, process.env.CALENDSO_ENCRYPTION_KEY),
  },
});
return res.json({ secret, keyUri, dataUri, backupCodes }); // plaintext only in response
```
Then verify with `compare(userSupplied, storedHash)` per element on login/disable, and null out (or remove) the matched entry after use. This is a schema-scope change (same `backupCodes: String?` column can hold the JSON of hashes), but the migration in `20230804153419_add_backup_codes/migration.sql` is cheap to amend now vs. after public users enroll.
References: NIST SP 800-63B §5.1.2, OWASP ASVS v4 2.5.2

:red_circle: [security] No rate limiting on backup-code verification in `packages/features/auth/lib/next-auth-options.ts`:130 (confidence: 88)
The new branch verifies `credentials.backupCode` against decrypted codes without invoking `checkRateLimitAndThrowError`, which is already imported in this file for other paths. `apps/web/pages/api/auth/two-factor/totp/disable.ts` likewise contains no rate-limit call for the backup-code branch (lines 46–65). Backup codes here are 10 hex chars = 40 bits of entropy (`crypto.randomBytes(5).toString("hex")` in `setup.ts`:60). With 10 active codes per user, the expected guess cost is ~2^37 — feasible given no throttling and NextAuth's `authorize` returning structured errors that an attacker can distinguish. Rate limiting is required for NIST SP 800-63B AAL2 (≤100 failed attempts before account lock).
```suggestion
// next-auth-options.ts — mirror the pattern used elsewhere in this file
if (user.twoFactorEnabled && credentials.backupCode) {
  await checkRateLimitAndThrowError({
    identifier: `backup-code.${user.id}`,
    rateLimitingType: "core",
  });
  // ... existing decrypt + indexOf logic
}
```
Apply the same guard in `disable.ts` before the backup-code branch. Without this, finding #3 (40-bit entropy) becomes exploitable rather than theoretical.
References: NIST SP 800-63B §5.2.2, OWASP ASVS v4 2.2.1

## Improvements

:yellow_circle: [security] Backup-code entropy is 40 bits — below industry norm in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:60 (confidence: 80)
`crypto.randomBytes(5).toString("hex")` yields a 10-hex-char code = 40 bits per code. GitHub uses 10 alphanumeric chars (~51 bits), Google uses 8 digits × 10 codes, 1Password uses 34-char base32. With rate limiting in place 40 bits is survivable, but NIST recommends ≥ 64 bits for long-lived recovery credentials. Doubling to 8 bytes (16 hex chars, 64 bits) is free — only the XXXXX-XXXXX UI placeholder and `minLength`/`maxLength` in `BackupCode.tsx` need to follow. Since this is pre-release (migration not yet shipped), bump entropy before any user enrolls.
```suggestion
const backupCodes = Array.from(Array(10), () => crypto.randomBytes(8).toString("hex"));
// BackupCode.tsx: placeholder="XXXX-XXXX-XXXX-XXXX", minLength={16}, maxLength={19}
```

:yellow_circle: [correctness] TOCTOU when consuming a backup code in `packages/features/auth/lib/next-auth-options.ts`:145 (confidence: 78)
The sequence decrypt → `indexOf` → `backupCodes[index] = null` → re-encrypt → `prisma.user.update` is non-atomic. Two concurrent logins submitting two different valid codes can both read the same ciphertext, each mark their own slot null locally, and the slower write overwrites the faster one — leaving one of the "consumed" codes re-usable. Worse, a replay of the same code in two parallel requests passes the `indexOf` check twice before either write commits, allowing a single code to be used twice. Fix with an optimistic-concurrency check (write conditional on the prior ciphertext) or wrap read-modify-write in `prisma.$transaction` with `SERIALIZABLE` isolation.
```suggestion
const updated = await prisma.user.updateMany({
  where: { id: user.id, backupCodes: user.backupCodes }, // CAS on prior ciphertext
  data: { backupCodes: symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY) },
});
if (updated.count === 0) throw new Error(ErrorCode.IncorrectBackupCode); // lost race — retry client-side
```

:yellow_circle: [security] Backup-code input is a plain `TextField`, not masked in `apps/web/components/auth/BackupCode.tsx`:17 (confidence: 70)
The code is a credential that should be treated like a password at entry time (shoulder-surfing, screen-recording demos, bug-report screenshots). Author already acknowledged this in the PR thread as a "follow-up PR" — worth landing in this PR since the component is new. Use `PasswordField` (already imported in sibling files) with a show/hide toggle; this also aligns with the `PasswordField` upgrade this PR makes in `EnableTwoFactorModal.tsx`.

:yellow_circle: [correctness] Backup-code input is not normalized before comparison in `packages/features/auth/lib/next-auth-options.ts`:144 (confidence: 65)
`credentials.backupCode.replaceAll("-", "")` strips dashes only. A user pasting `"abcde-12345 "` (trailing space from a copy), `"ABCDE-12345"` (auto-capitalized on iOS), or `"abcde 12345"` (space instead of dash, common for printed codes) gets `IncorrectBackupCode` even though the code is correct. Same issue in `disable.ts`:56.
```suggestion
const normalized = credentials.backupCode.trim().toLowerCase().replace(/[-\s]/g, "");
const index = backupCodes.indexOf(normalized);
```

## Nitpicks

:white_circle: [consistency] `useState([])` inferred as `never[]` in `apps/web/components/settings/EnableTwoFactorModal.tsx`:62 (confidence: 72)
`const [backupCodes, setBackupCodes] = useState([]);` — TypeScript infers `never[]`, making `backupCodes.map(...)` a type error under `strict` + `noImplicitAny`. It compiles today only because the runtime `setBackupCodes(body.backupCodes)` widens the actual value. Fix: `useState<string[]>([])`.

:white_circle: [correctness] Blob URL leaks if modal unmounts before re-entry in `apps/web/components/settings/EnableTwoFactorModal.tsx`:85 (confidence: 70)
`URL.createObjectURL(textBlob)` is revoked only on the next `handleSetup` call (`if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl)`). If the user closes the modal after one round-trip without retrying, the blob leaks until page unload. Revoke in a `useEffect` cleanup:
```suggestion
useEffect(() => () => { if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl); }, [backupCodesUrl]);
```

:white_circle: [testing] Self-acknowledged TODOs left in e2e test in `apps/web/playwright/login.2fa.e2e.ts`:50 (confidence: 60)
`// FIXME: this passes even when switch is not checked` and `// TODO: check file content` / `// TODO: check clipboard content` indicate known gaps in the new test coverage. The PR description also says "I haven't added tests that prove my fix is effective" while the diff *does* add tests — that checklist should be flipped, and the FIXME on the pre-existing assertion should either be resolved or filed. The backup-code-on-login and backup-code-on-disable flows (the actual new behavior) have no e2e coverage — only the display/copy/download on setup is exercised.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: auth (next-auth credential provider + 2FA disable endpoint + login UI + schema migration) — touches the primary authentication path for every user | Sensitive Paths: `packages/features/auth/**`, `apps/web/pages/api/auth/**`, `packages/prisma/migrations/**`, `packages/prisma/schema.prisma`
AI-Authored Likelihood: LOW (human-idiomatic comments, TODO/FIXME markers, conversational PR-thread iterations with reviewer)
