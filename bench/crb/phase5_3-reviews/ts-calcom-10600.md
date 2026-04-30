## Summary
16 files changed, 280 lines added, 38 lines deleted. 9 findings (6 critical, 3 improvements, 0 nitpicks).
2FA backup codes feature lands the recovery flow but ships several auth-critical defects: bearer-credential codes are stored under a single reversible key instead of hashed, the consume-after-verify step is non-atomic and split across endpoints (TOCTOU + leaked codes on disable), and decrypt/parse/clipboard error paths silently swallow failures.

## Critical
:red_circle: [security] Backup codes stored with reversible symmetric encryption instead of hashed in apps/web/pages/api/auth/two-factor/totp/setup.ts:60 (confidence: 90)
Backup codes are persisted via `symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY)`. This is reversible — anyone with read access to the database AND the `CALENDSO_ENCRYPTION_KEY` (a single shared secret across all users) can recover every user's backup codes in plaintext and bypass 2FA at will. Backup codes are bearer credentials equivalent to a second-factor token and should be stored as one-way hashes (bcrypt/argon2/scrypt) so that even full DB + key compromise does not yield usable codes. Storing in plaintext-encrypted form also forces the verification path (`next-auth-options.ts`, `disable.ts`) to perform plaintext array comparison, preventing constant-time verification. Industry guidance (NIST SP 800-63B, GitHub, Google) is to hash recovery codes.
```suggestion
import { hash, compare } from "bcryptjs";

// setup.ts — store hashes, return plaintext to user once
const backupCodes = Array.from({ length: 10 }, () =>
  crypto.randomBytes(8).toString("hex")
);
const hashedBackupCodes = await Promise.all(
  backupCodes.map((c) => hash(c, 12))
);
await prisma.user.update({
  where: { id: session.user.id },
  data: { backupCodes: JSON.stringify(hashedBackupCodes), /* ... */ },
});
return res.json({ secret, keyUri, dataUri, backupCodes });

// verification (next-auth-options.ts / disable.ts) — iterate all entries; do not break on match
const stored: (string | null)[] = JSON.parse(user.backupCodes);
const submitted = credentials.backupCode.replaceAll("-", "");
let matchedIndex = -1;
for (let i = 0; i < stored.length; i++) {
  if (stored[i] && (await compare(submitted, stored[i]!))) matchedIndex = i;
}
if (matchedIndex === -1) throw new Error(ErrorCode.IncorrectBackupCode);
stored[matchedIndex] = null;
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cwe.mitre.org/data/definitions/257.html, https://pages.nist.gov/800-63-3/sp800-63b.html]

:red_circle: [correctness] Race condition — same backup code accepted twice under concurrent login requests in packages/features/auth/lib/next-auth-options.ts:128 (confidence: 92)
The backup-code login path reads the codes blob, finds the matching index, sets `backupCodes[index] = null`, then writes back to the database as a separate `prisma.user.update`. There is no atomic compare-and-swap or row-level locking. Two simultaneous login requests with the same code both read the same stored blob (before either write commits), both find `index !== -1`, and both proceed to authenticate; the subsequent `update`s simply both null the same slot. The window between read and write is wide because it spans decrypt + JSON.parse + re-encrypt. This is a classic TOCTOU on a security-critical check and turns each backup code into an effectively multi-use credential under any concurrent request load.
```suggestion
await prisma.$transaction(async (tx) => {
  const fresh = await tx.user.findUnique({
    where: { id: user.id },
    select: { backupCodes: true },
  });
  if (!fresh?.backupCodes) throw new Error(ErrorCode.MissingBackupCodes);
  const codes = JSON.parse(symmetricDecrypt(fresh.backupCodes, process.env.CALENDSO_ENCRYPTION_KEY));
  const index = codes.indexOf(credentials.backupCode.replaceAll("-", ""));
  if (index === -1) throw new Error(ErrorCode.IncorrectBackupCode);
  codes[index] = null;
  await tx.user.update({
    where: { id: user.id },
    data: { backupCodes: symmetricEncrypt(JSON.stringify(codes), process.env.CALENDSO_ENCRYPTION_KEY) },
  });
});
```

:red_circle: [correctness] Backup code checked but never consumed during 2FA-disable — same code can disable 2FA repeatedly in apps/web/pages/api/auth/two-factor/totp/disable.ts:359 (confidence: 88)
In the disable endpoint, when a backup code is used the code is verified (index check) but explicitly NOT marked as consumed — the inline comment reads "we delete all stored backup codes at the end, no need to do this here." The final `prisma.user.update` sets `backupCodes: null` only after all subsequent logic. If the disable operation fails after the backup-code check (DB error, validation failure further down), the code remains valid and can be retried. More critically, the lack of intermediate invalidation means two concurrent disable requests with the same backup code will both pass the index check before either reaches the final write — the same TOCTOU pattern as the login path, but here without even a partial nulling write. Unlike the login path (which at least writes back nulled codes inline), this path makes no intermediate write at all.
```suggestion
// After index !== -1 check, immediately consume the code:
backupCodes[index] = null;
await prisma.user.update({
  where: { id: session.user.id },
  data: {
    backupCodes: symmetricEncrypt(
      JSON.stringify(backupCodes),
      process.env.CALENDSO_ENCRYPTION_KEY
    ),
  },
});
// then proceed with the rest of the disable logic.
```

:red_circle: [correctness] Unguarded `JSON.parse(symmetricDecrypt(...))` throws untyped error on corrupt backup codes in packages/features/auth/lib/next-auth-options.ts:645 (confidence: 92)
The new backup-code login branch calls `JSON.parse(symmetricDecrypt(user.backupCodes, key))` with no surrounding try/catch. If the stored ciphertext is corrupt, was written with a rotated key, or was manually altered, `symmetricDecrypt` throws a low-level crypto error and `JSON.parse` may throw a SyntaxError. Neither maps to a typed `ErrorCode`; the raw Error propagates out of the NextAuth `authorize` callback as an unhandled exception, which NextAuth surfaces as a generic auth failure. The actual cause is silently swallowed — there is no way to distinguish "user supplied wrong code" from "stored backup codes are unreadable", and a targeted attacker who can corrupt that single column can soft-DoS the user's recovery path. The env-var guard above (lines 638–641) only handles the missing-key case, not decryption or JSON failures. The same pattern exists at apps/web/pages/api/auth/two-factor/totp/disable.ts:369 — fix both sites.
```suggestion
let backupCodes: (string | null)[];
try {
  backupCodes = JSON.parse(
    symmetricDecrypt(user.backupCodes, process.env.CALENDSO_ENCRYPTION_KEY)
  );
  if (!Array.isArray(backupCodes)) throw new Error("malformed");
} catch (e) {
  console.error("Failed to decrypt/parse backup codes for user", user.id, e);
  throw new Error(ErrorCode.IncorrectBackupCode);
}
```

:red_circle: [correctness] Unhandled Promise rejection from `navigator.clipboard.writeText` — success toast fires unconditionally in apps/web/components/settings/EnableTwoFactorModal.tsx:315 (confidence: 88)
`navigator.clipboard.writeText(...)` returns a `Promise<void>` that rejects when the browser denies clipboard permission (common in non-HTTPS contexts and when the user has blocked clipboard access). The returned Promise is neither awaited nor given a `.catch()` handler. The very next line calls `showToast(t("backup_codes_copied"), "success")` unconditionally — the user sees a "Backup codes copied!" success notification even when the copy silently failed. This is a security-adjacent UX failure because the user may believe they have safely captured their codes when they have not, and may close the modal without an alternative (download) capture. The unhandled rejection also surfaces as an `unhandledrejection` event in the browser.
```suggestion
onClick={async (e) => {
  e.preventDefault();
  try {
    await navigator.clipboard.writeText(backupCodes.map(formatBackupCode).join("\n"));
    showToast(t("backup_codes_copied"), "success");
  } catch {
    showToast(t("something_went_wrong"), "error");
  }
}}
```

:red_circle: [security] Missing rate limiting / lockout on backup code verification in packages/features/auth/lib/next-auth-options.ts:128 (confidence: 85)
The new backup-code branch in `authorize()` (and the parallel branch in `disable.ts`) performs unbounded verification attempts. `IncorrectBackupCode` is thrown on each failure but no per-user counter or lockout is applied, and the visible diff does not invoke `checkRateLimitAndThrowError` (already imported elsewhere in this file) on this branch. The codes are 40-bit (`crypto.randomBytes(5)`) and there are 10 per user — an attacker who has already obtained a valid email+password can mount an online guessing attack against the recovery factor unimpeded, defeating the purpose of 2FA. Backup codes deserve stricter rate limiting than TOTP because they are long-lived static secrets.
```suggestion
if (user.twoFactorEnabled && credentials.backupCode) {
  await checkRateLimitAndThrowError({
    identifier: `backup-code.${user.id}`,
    rateLimitingType: "core", // tune to e.g. 5 per 15 min
  });
  // ... existing decrypt + indexOf + consume logic
}
// Apply equivalent guard in apps/web/pages/api/auth/two-factor/totp/disable.ts before the indexOf check.
```
[References: https://owasp.org/Top10/A07_2021-Identification_and_Authentication_Failures/, https://cwe.mitre.org/data/definitions/307.html]

## Improvements
:yellow_circle: [correctness] Calling setup again when 2FA is already enabled silently invalidates all existing backup codes in apps/web/pages/api/auth/two-factor/totp/setup.ts:406 (confidence: 85)
The setup endpoint generates new backup codes and writes them to `backupCodes` unconditionally on every POST, regardless of whether `twoFactorEnabled` is already `true`. A user who already has 2FA enabled and re-triggers setup (direct API call, browser back button, UI race) receives a fresh set of codes while their previously-saved codes are silently overwritten. The endpoint also flips `twoFactorEnabled: false` so the user must re-confirm — meaning anyone who can replay a setup request to an authenticated user briefly degrades their 2FA. The silent code rotation is the immediate correctness concern.
```suggestion
if (user.twoFactorEnabled) {
  return res.status(400).json({ error: ErrorCode.TwoFactorAlreadyEnabled });
}
```

:yellow_circle: [correctness] Always-passing assertion `expect(locator.isChecked()).toBeTruthy()` masks real test failure in apps/web/playwright/login.2fa.e2e.ts:546 (confidence: 90)
`expect(page.locator(...).isChecked()).toBeTruthy()` asserts that the *Promise object* returned by `isChecked()` is truthy — which it always is, since any non-null object is truthy. The boolean result of whether the element is actually checked is never inspected. The test never fails even if 2FA setup did not complete. The PR even acknowledges this in a FIXME comment added on line 543 ("FIXME: this passes even when switch is not checked"), but ships the broken assertion alongside the comment. Since this test is the only e2e coverage of the new backup-codes setup flow, the broken assertion provides false safety on the most security-critical change in the PR.
```suggestion
await expect(page.locator(`[data-testid=two-factor-switch]`)).toBeChecked();
```

## Risk Metadata
Risk Score: 80/100 (HIGH) | Blast Radius: 16 files in auth/login + Prisma migration + next-auth credentials provider | Sensitive Paths: apps/web/pages/api/auth/**, packages/features/auth/**, packages/prisma/migrations/**
AI-Authored Likelihood: LOW

(12 additional findings below confidence threshold)
