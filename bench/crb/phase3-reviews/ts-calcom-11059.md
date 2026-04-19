# PR Review: calcom/cal.com#11059 — feat: Sync app credentials between Cal.com & self-hosted platforms

## Summary
40 files changed, 375 lines added, 119 lines deleted. 14 findings (6 critical, 5 improvements, 3 nitpicks).
The core abstraction (`refreshOAuthTokens` + `parseRefreshTokenResponse`) is broken in several ways that silently corrupt stored OAuth credentials and crash refresh flows whenever the new sync feature is enabled. The webhook endpoint also has unsafe handling of missing secrets and an empty-JSDoc placeholder. Recommend request-changes until the critical type/shape bugs are fixed and tests are added.

## Critical

:red_circle: [correctness] `parseRefreshTokenResponse` returns a `SafeParseReturnType`, not the parsed data — callers store the wrapper object in the DB in `packages/app-store/googlecalendar/lib/CalendarService.ts`:96 (confidence: 98)
Previously `googleCredentialSchema.parse(googleCredentials)` returned the parsed credential object directly, and that value was written to `prisma.credential.update({ data: { key } })`. The new helper returns Zod's `SafeParseReturnType` (an object with `{ success, data, error }` — not the credentials themselves). The caller assigns this wrapper to `key` without unwrapping `.data`, so the DB `credential.key` column now contains `{ success: true, data: {...creds}, error: undefined }` instead of the credentials. Every subsequent Google Calendar call that casts `credential.key` as `GoogleToken` will break (access_token undefined, etc.). The same pattern exists in office365calendar (`tokenResponse.success && tokenResponse.data` spread) — the spread of the whole wrapper mixes `success`/`error` into the credentials object.
```suggestion
        const parsed = parseRefreshTokenResponse(googleCredentials, googleCredentialSchema);
        if (!parsed.success) throw new Error("Invalid refreshed tokens were returned");
        const key = parsed.data;
        await prisma.credential.update({
          where: { id: credential.id },
          data: { key },
        });
```

:red_circle: [correctness] `refreshOAuthTokens` returns heterogeneous shapes that break every caller when sync is enabled in `packages/app-store/_utils/oauth/refreshOAuthTokens.ts`:4 (confidence: 97)
When `APP_CREDENTIAL_SHARING_ENABLED && CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId`, the helper returns a raw `fetch` `Response` object. When not enabled, it returns whatever `refreshFunction()` returns — a `jsforce` response, a `hubspotClient.oauth.tokensApi.createToken` return value, an `axios` response (`.data.error`), a google-auth `res`, a fetch `Response`, etc. Callers type the result to the expected shape:
- `hubspot/lib/CalendarService.ts`: `const hubspotRefreshToken: HubspotToken = await refreshOAuthTokens(...)` — in sync-enabled mode this is a `Response`, not a `HubspotToken`. The immediately-following accesses (`.accessToken`, `.expiresIn`, etc. per `HubspotToken`) crash.
- `zoho-bigin/lib/CalendarService.ts` and `zohocrm/lib/CalendarService.ts`: access `tokenInfo.data.error` / `.data.access_token`. A `fetch` `Response` has no `.data`, causing `TypeError: Cannot read properties of undefined`.
- `googlecalendar/lib/CalendarService.ts`: expects `res.data` (the unwrapped `refreshToken` body from `google-auth-library`). A raw `Response` here yields `undefined`.
- `office365calendar`, `office365video`, `webex`, `zoomvideo`, `larkcalendar`: subsequent calls use `handleErrorsJson`/`handleLarkError`/etc. which only accept a fetch `Response` — these happen to work. Everything using axios/jsforce/SDK clients does not.

Any self-hoster who flips on the sync feature will see all Hubspot/Zoho/Bigin refreshes crash in production. Either normalize the response shape before returning, or make every caller fetch-based and pass a consistent JSON-body contract.
```suggestion
const refreshOAuthTokens = async (refreshFunction: () => any, appSlug: string, userId: number | null) => {
  if (APP_CREDENTIAL_SHARING_ENABLED && process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId) {
    const response = await fetch(process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT, {
      method: "POST",
      body: new URLSearchParams({ calcomUserId: userId.toString(), appSlug }),
    });
    if (!response.ok) throw new Error(`Credential sync endpoint failed: ${response.status}`);
    const json = await response.json();
    // Callers expect the token JSON directly; wrap to match each client's expected shape at the call site,
    // or standardize on always returning the parsed JSON body.
    return json;
  }
  return refreshFunction();
};
```

:red_circle: [hallucination] `minimumTokenResponseSchema` uses `z.string().toString()` as a dynamic object key — this is not a valid Zod pattern and does not match what the comment claims in `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts`:5 (confidence: 99)
```
const minimumTokenResponseSchema = z.object({
  access_token: z.string(),
  [z.string().toString()]: z.number(),
  [z.string().optional().toString()]: z.unknown().optional(),
});
```
`z.string()` is a `ZodString` instance; calling `.toString()` on it returns the fixed string `"ZodString"` (or similar representation) — **not** a wildcard. Both computed keys produce the same literal string `"ZodString"`, so the second entry overwrites the first and the schema effectively becomes `{ access_token: z.string(), ZodString: z.unknown().optional() }`. The intent expressed by the comment — "any property with a number is the expiry" — cannot be expressed this way. Real fixes are either `z.record(z.unknown())`, `.passthrough()`, or declare the specific expected keys (e.g. `expires_in`/`expiry_date`).
```suggestion
const minimumTokenResponseSchema = z
  .object({
    access_token: z.string(),
  })
  .passthrough();
```

:red_circle: [correctness] Synthetic `"refresh_token"` string silently replaces a missing refresh token in `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts`:20 (confidence: 95)
```
if (!refreshTokenResponse.data.refresh_token) {
  refreshTokenResponse.data.refresh_token = "refresh_token";
}
```
If the provider response omits a refresh token, this assigns the literal string `"refresh_token"` — which is then persisted to `credential.key.refresh_token` and used on the next refresh attempt. The next refresh call posts this garbage to the provider's token endpoint and fails (likely with `invalid_grant`), silently breaking the user's integration and leaving no trace that a real refresh token was ever missing. Either keep the previously-stored refresh token, or surface an explicit error; do not paper over the missing value.
```suggestion
// If the provider did not return a new refresh_token, the caller must preserve the
// previously-stored refresh_token rather than overwriting with a placeholder.
```

:red_circle: [correctness] Salesforce `get conn()` now fires a network refresh on *every* access in `packages/app-store/salesforce/lib/CalendarService.ts`:75 (confidence: 93)
The new block performs an unconditional `fetch("https://login.salesforce.com/services/oauth2/token", ...)` inside the `conn` getter before returning a new `jsforce.Connection`. Previously jsforce handled refresh lazily via `refreshFn` only when tokens were actually expired. Consequences:
- Every `CalendarService` operation now costs an extra round-trip to Salesforce + a DB write (`prisma.credential.update`), regardless of whether the existing access token is still valid.
- Under even moderate load this will trip Salesforce's per-connected-app rate limits and fan out DB writes.
- The refreshed token is written before the returned `jsforce.Connection` is even used, so a caller that obtains `conn` twice refreshes twice.
Also note the unreachable branch: `parseRefreshTokenResponse` throws on `!success`, so the subsequent `if (!accessTokenParsed.success) return Promise.reject(...)` can never run. Restore lazy refresh (gate on `isTokenExpired(credentialKey)`) or push this refresh into jsforce's `refreshFn` callback.
```suggestion
// Only refresh when the stored token is actually expired; otherwise reuse it and let
// jsforce's refreshFn handle lazy refresh on 401.
if (isTokenExpired(credentialKey)) {
  // ...fetch + prisma.credential.update...
}
```

:red_circle: [security] Webhook accepts requests when secrets are only partially configured and compares secrets non-constant-time in `apps/web/pages/api/webhook/app-credential.ts`:14 (confidence: 88)
Two issues here:
1. `APP_CREDENTIAL_SHARING_ENABLED` is defined as `process.env.CALCOM_WEBHOOK_SECRET && process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY` in `packages/lib/constants.ts`. It does not require `CALCOM_CREDENTIAL_SYNC_ENDPOINT`, so half of the refresh-path checks (`APP_CREDENTIAL_SHARING_ENABLED && CALCOM_CREDENTIAL_SYNC_ENDPOINT`) and half of the write-path checks diverge. Operators who set the secrets but not the sync endpoint will enable the webhook without enabling the refresh side — easy to get wrong.
2. `req.headers[...] !== process.env.CALCOM_WEBHOOK_SECRET` is a non-constant-time comparison. Combined with a custom header name that defaults to lowercase (`calcom-webhook-secret`), this is guessable if the secret has low entropy and the attacker can time responses. Use `crypto.timingSafeEqual` over Buffers of equal length.
3. `symmetricDecrypt(reqBody.keys, process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY || "")` falls back to the empty string when the env var is missing. Depending on `symmetricDecrypt`'s implementation this either silently decrypts with a zero-key or throws — either way, the handler should refuse the request rather than attempt decryption with `""`.
```suggestion
  if (!process.env.CALCOM_WEBHOOK_SECRET || !process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY) {
    return res.status(403).json({ message: "Credential sharing is not enabled" });
  }
  const headerName = process.env.CALCOM_WEBHOOK_HEADER_NAME || "calcom-webhook-secret";
  const provided = req.headers[headerName];
  if (typeof provided !== "string") return res.status(403).json({ message: "Invalid webhook secret" });
  const a = Buffer.from(provided);
  const b = Buffer.from(process.env.CALCOM_WEBHOOK_SECRET);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return res.status(403).json({ message: "Invalid webhook secret" });
  }
```

## Improvements

:yellow_circle: [correctness] `credentialId` vs `credential.userId` inconsistency in zoho-bigin in `packages/app-store/zoho-bigin/lib/CalendarService.ts`:94 (confidence: 80)
The other CalendarServices pass `credential.userId` as the third argument to `refreshOAuthTokens`; zoho-bigin passes `credentialId`. If `credentialId` refers to the credential row's primary key (not the user id), the sync endpoint will receive the wrong identifier in its `calcomUserId` URL-encoded field. Please confirm the variable in scope is the Cal.com user id, or rename/convert.
```suggestion
      "zoho-bigin",
      credential.userId
```

:yellow_circle: [security] No rate limiting or audit logging on the credential webhook in `apps/web/pages/api/webhook/app-credential.ts`:1 (confidence: 75)
Endpoint accepts a `userId` + arbitrary encrypted key payload, then upserts `prisma.credential`. There is no rate limiting, no per-user audit trail, and no method guard (a GET with matching headers would pass all checks and attempt to parse an empty body as JSON through Zod — it will 500 rather than 405). Add `if (req.method !== "POST") return res.status(405)...`, a rate limiter, and log the userId + appSlug + source IP for every credential update.
```suggestion
  if (req.method !== "POST") {
    return res.setHeader("Allow", "POST").status(405).json({ message: "Method not allowed" });
  }
```

:yellow_circle: [correctness] Unreachable `.success` branch after `parseRefreshTokenResponse` in `packages/app-store/salesforce/lib/CalendarService.ts`:88 (confidence: 90)
`parseRefreshTokenResponse` throws on parse failure, so `if (!accessTokenParsed.success) return Promise.reject(...)` can never execute. Either stop throwing inside the helper (return the `SafeParseReturnType` and let callers branch) or remove the dead branches across all callers.
```suggestion
// `parseRefreshTokenResponse` throws on failure; the success check is unreachable.
const accessTokenParsed = parseRefreshTokenResponse(accessTokenJson, salesforceTokenSchema);
await prisma.credential.update({
  where: { id: credential.id },
  data: { key: { ...accessTokenParsed.data, refresh_token: credentialKey.refresh_token } },
});
```

:yellow_circle: [consistency] `zoho-bigin/api/add.ts` replaces `appConfig.slug` with hardcoded `"zoho-bigin"` in `packages/app-store/zoho-bigin/api/add.ts`:17 (confidence: 70)
This removes a single source of truth. If the slug ever changes in `config.json`, the redirect URI will silently diverge. Unrelated to the credential-sync feature — likely an accidental edit that should be reverted or moved into its own PR.
```suggestion
    const redirectUri = WEBAPP_URL + `/api/integrations/${appConfig.slug}/callback`;
```

:yellow_circle: [testing] Zero tests for a breaking-change feature (confidence: 85)
The PR description explicitly checks the "Breaking change" box and acknowledges tests are not included. This feature decrypts attacker-controlled ciphertext, writes to the `credential` table for any `userId`, and fundamentally alters every OAuth-refresh path in the codebase. At minimum: unit tests for `parseRefreshTokenResponse` / `refreshOAuthTokens` shape contracts, an integration test that exercises the webhook under both enabled/disabled modes, and a regression test for the Google/O365 store-the-wrapper bug called out above.

## Nitpicks

:white_circle: [consistency] Empty `/** */` JSDoc on the webhook handler in `apps/web/pages/api/webhook/app-credential.ts`:16 (confidence: 95)
Placeholder docblock with no content. Either fill in a real description (auth model, expected payload, response codes) or remove it.

:white_circle: [consistency] `turbo.json` globalEnv additions are not alphabetical in `turbo.json`:200 (confidence: 90)
`CALCOM_WEBHOOK_SECRET` is inserted after `CALENDSO_ENCRYPTION_KEY`, breaking the alphabetical ordering used throughout the rest of the list. Move it up two lines.

:white_circle: [consistency] `.env.example` block uses a divergent 24-byte key for "AES256" encryption in `.env.example`:243 (confidence: 85)
Comment says "must be 32 bytes for AES256" but the adjacent suggested command is `openssl rand -base64 24` (24 random bytes → 32 base64 chars, not 32 bytes). Either change the command to `openssl rand -base64 32` for a 32-byte key, or clarify that 32 characters (post-base64) is what is expected. This will confuse self-hosters and may cause `symmetricDecrypt` to fail depending on its normalization.

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: touches every OAuth-refresh path in the app store (google, o365 calendar, o365 video, hubspot, lark, salesforce, webex, zoho-bigin, zohocrm, zoom); introduces a new public webhook that writes directly to `credential` | Sensitive Paths: new `api/webhook/app-credential.ts`, `symmetricDecrypt` usage, `.env.example` secret additions, new `APP_CREDENTIAL_SHARING_ENABLED` constant
AI-Authored Likelihood: MEDIUM — the `z.string().toString()` computed-key pattern and the `"refresh_token"` placeholder literal are characteristic of LLM-generated code that hallucinates API shapes; surrounding code in the PR is consistent with hand-written style.

## Recommendation
**request-changes** — The Zod schema bug, the SafeParseReturnType-as-credential bug, the shape-mismatch `refreshOAuthTokens` contract, the placeholder `"refresh_token"` assignment, and the unconditional Salesforce refresh are all deployment blockers. The webhook should also gain constant-time comparison, an explicit env-var presence check, and method/rate-limit guards before shipping.

---

_Review metadata: 40 files, 375 additions, 119 deletions, base `main`, head `feat/sync-app-credentials`. Review performed locally; no comments posted to the upstream PR._
