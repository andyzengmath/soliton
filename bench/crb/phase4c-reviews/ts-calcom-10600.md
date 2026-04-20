## Summary
16 files changed, 280 lines added, 36 lines deleted. 13 findings (5 critical, 8 improvements, 0 nitpicks).
2FA backup-codes feature lands core flows correctly but ships with weak code entropy, reversible DB storage, case-sensitive comparison, missing rate limiting, and potential JWT leak — all security-impacting for an auth primitive.

## Critical

:red_circle: [correctness] Backup-code comparison is case-sensitive against lowercase-hex storage in `apps/web/pages/api/auth/two-factor/totp/disable.ts`:372 (confidence: 95)
`crypto.randomBytes(5).toString("hex")` produces lowercase hex. `backupCodes.indexOf(req.body.backupCode.replaceAll("-", ""))` does an exact string match. A user who types the displayed code in uppercase ("ABCDE-12345") — a natural instinct on mobile auto-caps or when reading from a printed copy — will be rejected as `IncorrectBackupCode` even though the code is correct. Same defect exists at `packages/features/auth/lib/next-auth-options.ts`:650. Normalize both the stored and submitted values before compare.
```suggestion
    const index = backupCodes.indexOf(req.body.backupCode.replaceAll("-", "").toLowerCase());
```

:red_circle: [security] Backup codes have only 40 bits of entropy in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:408 (confidence: 92)
`crypto.randomBytes(5).toString("hex")` → 10 hex chars → 40 bits per code. NIST SP 800-63B §5.1.2 requires ≥112 bits for single-factor one-time codes, and peer implementations (GitHub, 1Password, Auth0) use ≥64 bits encoded in base32. Because these codes fully bypass TOTP and there is no per-user rate limiting on the backup-code branch (see separate finding), an attacker with a stolen password can parallelize 2^40 attempts over 10 live codes in hours on commodity hardware. Widen to ≥8 random bytes and prefer base32 (RFC 4648) over hex for user-friendly entry.
```suggestion
  // 80 bits of entropy, base32-encoded for user entry (20 chars, 4 groups of 5)
  const backupCodes = Array.from(Array(10), () =>
    crypto.randomBytes(10).toString("base64").replace(/[^A-Z2-7]/gi, "").slice(0, 16).toUpperCase()
  );
```
References: NIST SP 800-63B §5.1.2; OWASP ASVS v4 §2.8.4.

:red_circle: [security] Backup codes stored with reversible symmetric encryption instead of one-way hash in `packages/features/auth/lib/next-auth-options.ts`:660 (confidence: 90)
`symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY)` means any compromise that exposes both the database and `CALENDSO_ENCRYPTION_KEY` (backup, SSRF read of env, compromised CI artifact) yields all users' plaintext backup codes. Backup codes should be stored as bcrypt/argon2 hashes — verification iterates and `bcrypt.compare`s the submitted code. The codes are short-lived (one-shot) and memorized-secret equivalents, and OWASP ASVS §2.4.1 requires password-equivalent secrets to be hashed, not encrypted. Same pattern at `apps/web/pages/api/auth/two-factor/totp/setup.ts`:413.
```suggestion
  import bcrypt from "bcryptjs";
  // ...
  const plaintextCodes = Array.from(Array(10), () => crypto.randomBytes(10).toString("hex"));
  const hashedCodes = await Promise.all(plaintextCodes.map((c) => bcrypt.hash(c, 10)));
  // Return plaintextCodes to the client ONCE; persist only hashedCodes.
  await prisma.user.update({
    where: { id: session.user.id },
    data: { backupCodes: JSON.stringify(hashedCodes), twoFactorEnabled: false, twoFactorSecret: symmetricEncrypt(secret, process.env.CALENDSO_ENCRYPTION_KEY) },
  });
```
References: OWASP ASVS v4 §2.4.1; CWE-257.

:red_circle: [security] Encrypted backupCodes blob added to authorize `select`, risking JWT/session leakage in `packages/features/auth/lib/next-auth-options.ts`:88 (confidence: 80)
The `select` block now pulls `backupCodes: true`. NextAuth's `authorize` return object is passed to the JWT callback; unless downstream code (not visible in this diff) explicitly strips it, the encrypted backup-codes ciphertext lands in the session JWT cookie. That cookie is client-observable and, combined with leakage of `CALENDSO_ENCRYPTION_KEY`, recovers backup codes without DB access. Either don't select the field here (load it lazily inside the backup-code branch) or explicitly delete it from the object returned by `authorize`.
```suggestion
      // Load only when needed, inside the backup-code branch:
      if (user.twoFactorEnabled && credentials.backupCode) {
        const { backupCodes } = await prisma.user.findUniqueOrThrow({
          where: { id: user.id },
          select: { backupCodes: true },
        });
        // ...use backupCodes, then do NOT attach to the returned user object
      }
```

:red_circle: [security] No rate limiting on the backup-code verification branch in `packages/features/auth/lib/next-auth-options.ts`:637 (confidence: 85)
The file already uses `checkRateLimitAndThrowError` for login attempts generally, but the newly added backup-code branch issues a direct `indexOf` against an attacker-controlled string without any per-user attempt cap or lockout. Combined with 40-bit entropy, a credential-stuffing attacker who already knows the password can burn through live codes quickly. Apply a per-user limiter (e.g. 5 attempts / 15 min) keyed on `user.id` at the top of the branch, and mirror it at `apps/web/pages/api/auth/two-factor/totp/disable.ts`:359.
```suggestion
  if (user.twoFactorEnabled && credentials.backupCode) {
    await checkRateLimitAndThrowError({
      identifier: `backup-code:${user.id}`,
      rateLimitingType: "core",
    });
    // ...existing logic
  }
```

## Improvements

:yellow_circle: [consistency] `BackupCode.tsx` exports a function literally named `TwoFactor` in `apps/web/components/auth/BackupCode.tsx`:7 (confidence: 95)
`export default function TwoFactor({ center = true }) { ... }` in a file called `BackupCode.tsx` is a copy-paste from the sibling `TwoFactor.tsx`. It works (default export), but it breaks React DevTools display names, stack traces, and grep-by-name searches, and will confuse anyone debugging the login flow where both components are used.
```suggestion
export default function BackupCode({ center = true }) {
```

:yellow_circle: [correctness] `resetState` leaves sensitive plaintext and blob URL in memory in `apps/web/components/settings/EnableTwoFactorModal.tsx`:203 (confidence: 88)
```
const resetState = () => {
  setPassword("");
  setErrorMessage(null);
  setStep(SetupStep.ConfirmPassword);
};
```
After this runs on cancel/close, `backupCodes` (plaintext array), `backupCodesUrl` (object URL pointing to a plaintext Blob of the codes), `dataUri`, and `secret` all remain in React state and heap. The object URL also leaks until page navigation. Clear them and revoke the URL.
```suggestion
  const resetState = () => {
    setPassword("");
    setErrorMessage(null);
    setStep(SetupStep.ConfirmPassword);
    setBackupCodes([]);
    if (backupCodesUrl) URL.revokeObjectURL(backupCodesUrl);
    setBackupCodesUrl("");
    setDataUri("");
    setSecret("");
  };
```

:yellow_circle: [correctness] Missing zod validation on `disable.ts` request body in `apps/web/pages/api/auth/two-factor/totp/disable.ts`:359 (confidence: 78)
`req.body.backupCode`, `req.body.password`, and `req.body.code` are accessed without schema validation. If a client submits `backupCode` as an array or number, `.replaceAll("-", "")` throws a `TypeError`, bubbling as an opaque 500 instead of a clean 400. Other `apps/web/pages/api` endpoints use zod; stay consistent.
```suggestion
  const schema = z.object({
    password: z.string().min(1),
    code: z.string().optional(),
    backupCode: z.string().optional(),
  });
  const parsed = schema.safeParse(req.body);
  if (!parsed.success) return res.status(400).json({ error: ErrorCode.IncorrectPassword });
  const { password, code, backupCode } = parsed.data;
```

:yellow_circle: [correctness] `useState([])` is inferred as `never[]` in `apps/web/components/settings/EnableTwoFactorModal.tsx`:196 (confidence: 82)
`const [backupCodes, setBackupCodes] = useState([]);` — TypeScript infers the element type as `never`, so `setBackupCodes(body.backupCodes)` only works because `body` is implicitly `any`. Any strict-mode tightening of the fetch typing will surface a silent error. Annotate explicitly.
```suggestion
  const [backupCodes, setBackupCodes] = useState<string[]>([]);
```

:yellow_circle: [security] `setup.ts` silently overwrites existing backup codes and resets `twoFactorEnabled` to false in `apps/web/pages/api/auth/two-factor/totp/setup.ts`:408 (confidence: 72)
The update writes `backupCodes: <new>`, `twoFactorEnabled: false`, `twoFactorSecret: <new>` unconditionally. If the route lacks a `twoFactorEnabled === false` precondition (not visible in this diff — needs verification in the context above the shown lines), a still-authenticated attacker who has phished only the password can replay POST `/setup` to blow away the victim's working 2FA + backup codes. At minimum, confirm that upstream logic in this handler rejects the call when `user.twoFactorEnabled` is true, and that the behavior of silently rotating codes mid-session is surfaced in the UI.

:yellow_circle: [correctness] Last-code exhaustion leaves user permanently locked out of backup path in `packages/features/auth/lib/next-auth-options.ts`:653 (confidence: 80)
`backupCodes[index] = null` preserves array length; after 10 successful uses every slot is `null` and no future backup login is possible, with no user-visible warning. A successful login via the last non-null code should either (a) trigger regeneration with a one-time surface in the UI, or (b) flash an alert reminding the user to regenerate. Otherwise this silently degrades account recoverability.
```suggestion
        backupCodes[index] = null;
        const remaining = backupCodes.filter((c) => c !== null).length;
        // TODO: surface `remaining <= 2` to the UI/email to prompt regeneration
        await prisma.user.update({
          where: { id: user.id },
          data: { backupCodes: symmetricEncrypt(JSON.stringify(backupCodes), process.env.CALENDSO_ENCRYPTION_KEY) },
        });
```

:yellow_circle: [testing] E2E coverage for the new feature is almost entirely TODO in `apps/web/playwright/login.2fa.e2e.ts`:109 (confidence: 90)
The only real assertion added is `download.suggestedFilename() === "cal-backup-codes.txt"`. Clipboard content is a `TODO`, downloaded file content is a `TODO`, and neither of the two primary flows the PR description calls out — logging in with a backup code, disabling 2FA with a backup code — has any test. For an auth primitive this is the minimum that should ship.
```suggestion
  // Add: test("login with backup code succeeds and consumes the code", ...)
  // Add: test("disable 2FA with backup code clears backupCodes in DB", ...)
  // Add: test("reused backup code is rejected with IncorrectBackupCode", ...)
```

:yellow_circle: [consistency] Nested ternary hurts readability of login UI branch in `apps/web/pages/auth/login.tsx`:522 (confidence: 70)
```
{twoFactorRequired ? !twoFactorLostAccess ? <TwoFactor center /> : <BackupCode center /> : null}
```
Three-level nested ternary in JSX. Extract to a named variable or use `&&`/early-return for symmetry with the `TwoFactorFooter` block above it.
```suggestion
  const twoFactorInput = !twoFactorRequired
    ? null
    : twoFactorLostAccess
    ? <BackupCode center />
    : <TwoFactor center />;
  // ...
  {twoFactorInput}
```

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: 16 files (1 prisma schema + migration, 2 auth API routes, 1 NextAuth options, 4 React auth components, 1 i18n file, 1 e2e test, 1 test builder, 1 shared input component) | Sensitive Paths: `packages/features/auth/`, `apps/web/pages/api/auth/`, `packages/prisma/migrations/`, `apps/web/components/auth/`
AI-Authored Likelihood: LOW (comment style, idiomatic React + NextAuth patterns, pre-existing FIXME/TODO notes preserved)
