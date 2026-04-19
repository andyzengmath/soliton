## Summary
16 files changed, 280 lines added, 35 lines deleted. 14 findings (7 critical, 7 improvements).
Security-critical auth feature (2FA backup codes) with reversible-encryption storage, no rate limiting, session/JWT over-selection, and insufficient test coverage of the happy and failure paths.

## Critical
:red_circle: [security] Backup codes stored with reversible symmetric encryption instead of one-way hash in packages/features/auth/lib/next-auth-options.ts:128 (confidence: 95)
Backup codes are stored as `symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY)` (setup.ts and next-auth-options.ts). Symmetric encryption is reversible: any compromise of `CALENDSO_ENCRYPTION_KEY` together with the `users` table (DB dump, read-only SQLi, insider) discloses every user's plaintext backup codes and is a complete 2FA bypass. Because `twoFactorSecret` uses the same key, a single-key compromise eliminates 2FA entirely for all users. Industry practice (GitHub, Google, AWS) is to store a one-way hash and compare in constant time.
```suggestion
// setup.ts — hash with salted scrypt instead of symmetric encryption
const plainCodes = Array.from({ length: 10 }, () => crypto.randomBytes(8).toString("hex"));
const hashedCodes = plainCodes.map((code) => {
  const salt = crypto.randomBytes(16).toString("hex");
  const hash = crypto.scryptSync(code, salt, 64).toString("hex");
  return `${salt}:${hash}`;
});
await prisma.user.update({ where: { id: session.user.id }, data: { backupCodes: JSON.stringify(hashedCodes) } });
return res.json({ secret, keyUri, dataUri, backupCodes: plainCodes });

// next-auth-options.ts — verify with timingSafeEqual and null the used slot
const stored: (string | null)[] = JSON.parse(user.backupCodes);
const supplied = credentials.backupCode.replaceAll("-", "");
let matchIndex = -1;
for (let i = 0; i < stored.length; i++) {
  const entry = stored[i];
  if (!entry) continue;
  const [salt, hashHex] = entry.split(":");
  const candidate = crypto.scryptSync(supplied, salt, 64);
  const expected = Buffer.from(hashHex, "hex");
  if (candidate.length === expected.length && crypto.timingSafeEqual(candidate, expected)) {
    matchIndex = i;
    break;
  }
}
if (matchIndex === -1) throw new Error(ErrorCode.IncorrectBackupCode);
stored[matchIndex] = null;
await prisma.user.update({ where: { id: user.id }, data: { backupCodes: JSON.stringify(stored) } });
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cheatsheetseries.owasp.org/cheatsheets/Multifactor_Authentication_Cheat_Sheet.html#recovery-codes]

:red_circle: [security] No rate limiting on backup-code verification enables online brute force in packages/features/auth/lib/next-auth-options.ts:128 (confidence: 90)
Each backup code has only 40 bits of entropy (`crypto.randomBytes(5).toString("hex")` = 10 hex chars). With 10 active codes, a single random guess hits ~2^-37 — small, but with no rate limiter on either the `credentials.backupCode` authorize branch or the `/api/auth/two-factor/totp/disable` backup-code branch, an attacker who has the user's password can attempt millions of guesses cheaply. The file already imports `checkRateLimitAndThrowError` but does not invoke it on the new branch. Combined with distinguishable error codes (IncorrectBackupCode vs MissingBackupCodes) and no per-account lockout, brute force becomes feasible for targeted accounts.
```suggestion
if (user.twoFactorEnabled && credentials.backupCode) {
  await checkRateLimitAndThrowError({
    rateLimitingType: "core",
    identifier: `backup-code.${user.id}`,
  });
  if (!process.env.CALENDSO_ENCRYPTION_KEY) throw new Error(ErrorCode.InternalServerError);
  if (!user.backupCodes) throw new Error(ErrorCode.IncorrectBackupCode); // unify with IncorrectBackupCode to avoid enumeration
  // ...existing match/null/re-encrypt logic...
}
```
Apply the same in `apps/web/pages/api/auth/two-factor/totp/disable.ts`.
[References: https://cwe.mitre.org/data/definitions/307.html, https://owasp.org/Top10/A07_2021-Identification_and_Authentication_Failures/]

:red_circle: [security] `backupCodes` added to authorize() select propagates into user return value and likely into the JWT/session in packages/features/auth/lib/next-auth-options.ts:86 (confidence: 90)
`backupCodes: true` is added to the Prisma select inside `authorize()`. The returned user object is passed to next-auth's `jwt`/`session` callbacks. cal.com's standard JWT callback spreads/picks from `user` to populate the token; the encrypted `backupCodes` blob will end up inside the signed JWT cookie (persisted across every session), returned by `getServerSession()` / `getToken()`, and potentially serialized to the client session. Ciphertext leakage widens the attack surface (oracle for future crypto flaws, cookie size, accidental log exposure). The PR does not update the `jwt`/`session` callbacks to strip this field.
```suggestion
// At the end of authorize(), strip sensitive fields before returning
const { backupCodes: _bc, twoFactorSecret: _tfs, ...safeUser } = user;
return {
  id: safeUser.id,
  username: safeUser.username,
  email: safeUser.email,
  name: safeUser.name,
  role: validateRole(safeUser.role),
  belongsToActiveTeam,
  organizationId: safeUser.organizationId,
  locale: safeUser.locale,
};
// Also audit jwt/session callbacks to confirm backupCodes is not propagated anywhere.
```
[References: https://cwe.mitre.org/data/definitions/200.html]

:red_circle: [security] Single-use enforcement gap in the 2FA-disable backup-code path in apps/web/pages/api/auth/two-factor/totp/disable.ts:58 (confidence: 92)
In `disable.ts`, when a backup code matches, the handler does NOT mark the consumed slot as `null` before `prisma.user.update({ data: { backupCodes: null, twoFactorEnabled: false, twoFactorSecret: null } })`. The comment "we delete all stored backup codes at the end, no need to do this here" is correct only in the single-request, success-on-final-update case. Two concurrent disable requests bearing the same code can both pass the `indexOf` check before either write commits, and — more importantly — if the downstream update fails (DB timeout, exception after the check), the code remains valid. Compared to the login path, which nulls-and-re-encrypts atomically before returning, the disable path leaves a window in which a captured-but-still-valid backup code can be re-submitted.
```suggestion
const backupCodes = JSON.parse(symmetricDecrypt(user.backupCodes, process.env.CALENDSO_ENCRYPTION_KEY));
const supplied = req.body.backupCode.replaceAll("-", "");
const index = backupCodes.findIndex((c: string | null) => c !== null && c === supplied);
if (index === -1) {
  return res.status(400).json({ error: ErrorCode.IncorrectBackupCode });
}
// Mark consumed before any further action — defence in depth against a failing final update.
backupCodes[index] = null;
await prisma.user.update({
  where: { id: session.user.id },
  data: { backupCodes: symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY) },
});
```
[References: https://cwe.mitre.org/data/definitions/294.html]

:red_circle: [correctness] Missing `CALENDSO_ENCRYPTION_KEY` guard before encrypting backup codes in setup handler in apps/web/pages/api/auth/two-factor/totp/setup.ts:60 (confidence: 92)
Both `disable.ts` and `next-auth-options.ts` explicitly check `if (!process.env.CALENDSO_ENCRYPTION_KEY)` before calling `symmetricEncrypt/Decrypt`. The `setup.ts` handler introduced here does not — it passes `process.env.CALENDSO_ENCRYPTION_KEY` directly. If the env var is unset (misconfigured deploy, test env), `symmetricEncrypt` receives `undefined` as the key and throws an uncontrolled error to the client. Inconsistent with the rest of the PR's own error handling for the same env var.
```suggestion
if (!process.env.CALENDSO_ENCRYPTION_KEY) {
  console.error("Missing encryption key; cannot proceed with 2FA setup.");
  return res.status(500).json({ error: ErrorCode.InternalServerError });
}
const backupCodes = Array.from(Array(10), () => crypto.randomBytes(5).toString("hex"));
await prisma.user.update({
  where: { id: session.user.id },
  data: {
    backupCodes: symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY),
    twoFactorEnabled: false,
    twoFactorSecret: symmetricEncrypt(secret, process.env.CALENDSO_ENCRYPTION_KEY),
  },
});
```

:red_circle: [correctness] Unguarded `.map()` on `body.backupCodes` crashes modal if response is malformed in apps/web/components/settings/EnableTwoFactorModal.tsx:216 (confidence: 91)
In the `response.status === 200` branch of `handleSetup`, `setBackupCodes(body.backupCodes)` is followed immediately by `body.backupCodes.map(formatBackupCode).join("\n")` to build the Blob. If `body.backupCodes` is `undefined` (partial JSON, API version mismatch, future server regression), `.map()` on `undefined` throws a TypeError, leaving the modal in a broken state with no user-visible error. The render-time `backupCodes.map(...)` in the `DisplayBackupCodes` step would also crash because state was just set to `undefined`.
```suggestion
if (response.status === 200) {
  const codes: string[] = Array.isArray(body.backupCodes) ? body.backupCodes : [];
  setBackupCodes(codes);
  const textBlob = new Blob([codes.map(formatBackupCode).join("\n")], { type: "text/plain" });
  if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl);
  setBackupCodesUrl(URL.createObjectURL(textBlob));
  setDataUri(body.dataUri);
  setSecret(body.secret);
  setStep(SetupStep.DisplayQrCode);
  return;
}
```

:red_circle: [testing] No unit test for the backup-code crypto round-trip (generate, encrypt, decrypt, match, null, re-encrypt) in apps/web/pages/api/auth/two-factor/totp/setup.ts:60 (confidence: 95)
The entire feature hinges on a crypto round-trip across three files: `setup.ts` generates and encrypts, `next-auth-options.ts` and `disable.ts` decrypt, JSON-parse, match, mutate, and re-encrypt. No unit test validates that codes written by setup can be matched by login/disable. A key mismatch, encoding error, or serialization bug would silently break all backup-code paths — and the author explicitly acknowledged: "I haven't added tests that prove my fix is effective or that my feature works."
```suggestion
// packages/lib/test/backupCodes.test.ts
import crypto from "crypto";
import { symmetricEncrypt, symmetricDecrypt } from "@calcom/lib/crypto";

const KEY = "01234567890123456789012345678901";

describe("backup code crypto round-trip", () => {
  it("encrypts and decrypts without data loss", () => {
    const codes = Array.from(Array(10), () => crypto.randomBytes(5).toString("hex"));
    const enc = symmetricEncrypt(JSON.stringify(codes), KEY);
    expect(JSON.parse(symmetricDecrypt(enc, KEY))).toEqual(codes);
  });
  it("consumed (null) slot never matches a user-supplied code", () => {
    const codes: (string | null)[] = Array.from(Array(10), () => crypto.randomBytes(5).toString("hex"));
    const used = codes[3] as string;
    codes[3] = null;
    expect(codes.indexOf(used)).toBe(-1);
  });
});
```

## Improvements
:yellow_circle: [security] Non-constant-time comparison of backup codes via Array.indexOf in packages/features/auth/lib/next-auth-options.ts:128 (confidence: 88)
`backupCodes.indexOf(credentials.backupCode.replaceAll("-", ""))` (and the same pattern in disable.ts) performs string equality that short-circuits byte-by-byte. For 40-bit codes with no rate limiting, this is a theoretical timing-oracle surface. Once codes are hashed (see the critical finding), use `crypto.timingSafeEqual` on equal-length buffers.
```suggestion
const supplied = Buffer.from(credentials.backupCode.replaceAll("-", "").padEnd(10, "\0"));
let matchIndex = -1;
for (let i = 0; i < backupCodes.length; i++) {
  const stored = backupCodes[i];
  if (!stored) continue;
  const storedBuf = Buffer.from(stored.padEnd(10, "\0"));
  if (storedBuf.length === supplied.length && crypto.timingSafeEqual(storedBuf, supplied)) {
    matchIndex = i;
    break;
  }
}
```

:yellow_circle: [correctness] `resetState()` does not clear `backupCodes` or revoke `backupCodesUrl`, leaking codes and blob URL on modal reuse in apps/web/components/settings/EnableTwoFactorModal.tsx:203 (confidence: 88)
`resetState()` resets `password`, `errorMessage`, `step`, but leaves `backupCodes` and `backupCodesUrl` intact. The "Close" button on the `DisplayBackupCodes` step calls `resetState()` then `onEnable()`; the blob URL is only revoked when a *new* one is created, so on modal close it leaks. If the parent keeps the modal mounted with `open={false}`, stale codes and the live object URL survive.
```suggestion
const resetState = () => {
  setPassword("");
  setErrorMessage(null);
  setStep(SetupStep.ConfirmPassword);
  setBackupCodes([]);
  if (backupCodesUrl) {
    URL.revokeObjectURL(backupCodesUrl);
    setBackupCodesUrl("");
  }
};
```

:yellow_circle: [testing] FIXME left in production test code — 2FA-enabled assertion always passes in apps/web/playwright/login.2fa.e2e.ts:543 (confidence: 97)
The new FIXME reads: "this passes even when switch is not checked". The assertion `expect(page.locator(...).isChecked()).toBeTruthy()` returns truthy on the `Promise` object itself regardless of the underlying state, so the test gives false confidence that 2FA was enabled. The working pattern already exists later in the same file.
```suggestion
await expect(
  page.locator(`[data-testid=two-factor-switch][data-state="checked"]`)
).toBeVisible();
```

:yellow_circle: [testing] No test for logging in with a backup code (happy path or error paths) in packages/features/auth/lib/next-auth-options.ts:128 (confidence: 98)
The `authorize()` backup-code branch — the most security-sensitive code in the PR — is never exercised. The test file's own TODO ("add more backup code tests, e.g. login + disabling 2fa with backup") acknowledges this. A regression in decrypt/match/null would be invisible in CI.
```suggestion
test("can login with a backup code, and the same code fails on re-use", async ({ page, users }) => {
  const user = await users.create();
  await user.login();
  const { backupCodes } = await setup2FA(page);
  const code = backupCodes[0];
  await user.logout();

  // first use — succeeds
  await loginWithBackupCode(page, user, code);
  await expect(page).toHaveURL("/");
  await user.logout();

  // second use — rejected
  await loginWithBackupCode(page, user, code);
  await expect(page.locator('[data-testid="alert-message"]'))
    .toContainText("Backup code is incorrect");
});
```

:yellow_circle: [testing] No test for disabling 2FA with a backup code in apps/web/pages/api/auth/two-factor/totp/disable.ts:58 (confidence: 97)
The disable endpoint's new `backupCode` branch (happy path, `IncorrectBackupCode`, and `MissingBackupCodes` paths) is untested. The PR's own TODO names this gap.
```suggestion
test("can disable 2FA with a backup code", async ({ page, users }) => {
  const user = await users.create();
  await user.login();
  const { backupCodes } = await setup2FA(page);

  await page.goto("/settings/security/two-factor-auth");
  await page.getByTestId("two-factor-switch").click();
  await page.getByText("Lost access").click();
  await page.fill('[name="password"]', user.password);
  await page.fill('[id="backup-code"]', backupCodes[0]);
  await page.getByText("Disable").click();

  await expect(
    page.locator('[data-testid=two-factor-switch][data-state="unchecked"]')
  ).toBeVisible();
});
```

:yellow_circle: [testing] Download and clipboard content not verified — TODOs left in committed test code in apps/web/playwright/login.2fa.e2e.ts:559 (confidence: 90)
For a feature whose value proposition is offline recovery, not asserting the content of the downloaded file or the clipboard means a `formatBackupCode` regression or an empty-file bug would not be caught.
```suggestion
const download = await promise;
expect(download.suggestedFilename()).toBe("cal-backup-codes.txt");
const stream = await download.createReadStream();
const content = await new Promise<string>((r) => {
  const chunks: Buffer[] = [];
  stream!.on("data", (c) => chunks.push(c));
  stream!.on("end", () => r(Buffer.concat(chunks).toString("utf8")));
});
const lines = content.trim().split("\n");
expect(lines).toHaveLength(10);
lines.forEach((line) => expect(line).toMatch(/^[0-9a-f]{5}-[0-9a-f]{5}$/));
```

:yellow_circle: [testing] No test for dash-normalization of user-supplied backup codes in apps/web/pages/api/auth/two-factor/totp/disable.ts:370 (confidence: 95)
Both the login and disable paths strip dashes with `replaceAll("-", "")` before matching. Neither path has a test that submits `XXXXX-XXXXX` and verifies it matches the stored raw code. A future refactor that drops normalization would silently break user-facing input.
```suggestion
it("accepts dash-formatted backup code (XXXXX-XXXXX)", async () => {
  // seed user.backupCodes with JSON.stringify(["aabbccddee", ...])
  const response = await callDisableEndpoint({
    password: correctPassword,
    backupCode: "aabbc-cddee",
  });
  expect(response.status).toBe(200);
});
```

## Risk Metadata
Risk Score: 66/100 (HIGH) | Blast Radius: 8 high-fan-out files across auth/, schema.prisma, and Input.tsx | Sensitive Paths: 10 of 16 changed files match auth/ or migration patterns
AI-Authored Likelihood: LOW (disable.ts and next-auth-options.ts backup-code blocks are near-identical, consistent with copy-paste or AI assist, but not conclusive)

(2 additional findings below confidence threshold: plaintext codes returned without one-time-view enforcement; user enumeration via distinct MissingBackupCodes vs IncorrectBackupCode — both 70–75 confidence)
