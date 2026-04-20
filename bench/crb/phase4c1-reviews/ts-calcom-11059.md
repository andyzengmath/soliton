## Summary
40 files changed, 346 lines added, 118 lines deleted. 14 findings (8 critical, 6 improvements).
PR introduces an app-credential sync webhook plus an `APP_CREDENTIAL_SHARING_ENABLED`-gated path for refreshing OAuth tokens via a self-hosted endpoint. The webhook receiver and the shared `parseRefreshTokenResponse` / `refreshOAuthTokens` helpers have several correctness and security defects that will break multiple integrations when the feature is actually turned on.

## Critical

:red_circle: [correctness] `parseRefreshTokenResponse` result saved to DB without unwrapping `.data` in googlecalendar in packages/app-store/googlecalendar/lib/CalendarService.ts:96 (confidence: 98)
`parseRefreshTokenResponse` returns the whole Zod `SafeParseReturnType` (`{ success, data, ... }`), not the parsed object. The previous line was `const key = googleCredentialSchema.parse(googleCredentials);` which returned the parsed value. The replacement passes the wrapper into `prisma.credential.update({ data: { key } })`, so the stored credential becomes `{"success": true, "data": {...}}` instead of the token payload. All subsequent Google Calendar calls will read malformed credentials and fail.
```suggestion
        const parsed = parseRefreshTokenResponse(googleCredentials, googleCredentialSchema);
        const key = parsed.data;
        await prisma.credential.update({
          where: { id: credential.id },
          data: { key },
```

:red_circle: [cross-file-impact] `refreshOAuthTokens` return type diverges between sync and fallback paths, breaking library-shaped callers in packages/app-store/_utils/oauth/refreshOAuthTokens.ts:3 (confidence: 95)
When `APP_CREDENTIAL_SHARING_ENABLED` is on, the helper returns a `fetch` `Response` (with `.body` / `.json()`), but the fallback path returns whatever the library-specific `refreshFunction()` returned (a gaxios response, an Axios response, or a HubSpot SDK object). Callers that rely on library-specific shapes will explode as soon as a self-hoster enables sync: googlecalendar reads `res?.data`, hubspot casts the result to `HubspotToken` and reads token fields, zoho-bigin reads `tokenInfo.data.error`, and zohocrm reads `zohoCrmTokenInfo.data`. On the sync path none of those fields exist on a fetch `Response`. Either normalize the helper to always return the same shape (e.g., `await response.json()` on the sync branch) or make each caller branch on the feature flag.
```suggestion
const refreshOAuthTokens = async (refreshFunction: () => any, appSlug: string, userId: number | null) => {
  if (APP_CREDENTIAL_SHARING_ENABLED && process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId) {
    const response = await fetch(process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT, {
      method: "POST",
      body: new URLSearchParams({ calcomUserId: userId.toString(), appSlug }),
    });
    if (!response.ok) throw new Error(`Credential sync endpoint returned ${response.status}`);
    return await response.json();
  }
  return await refreshFunction();
};
```

:red_circle: [security] Webhook secret comparison is vulnerable to timing attacks in apps/web/pages/api/webhook/app-credential.ts:18 (confidence: 92)
`req.headers[...] !== process.env.CALCOM_WEBHOOK_SECRET` is a short-circuiting string compare. Attackers can iteratively probe bytes. Use `crypto.timingSafeEqual` on equal-length Buffers and reject non-string headers explicitly. `req.headers[name]` is `string | string[] | undefined`; an array header silently fails the comparison but still leaks timing.
```suggestion
import crypto from "crypto";
const expected = process.env.CALCOM_WEBHOOK_SECRET ?? "";
const headerName = process.env.CALCOM_WEBHOOK_HEADER_NAME || "calcom-webhook-secret";
const received = req.headers[headerName];
if (typeof received !== "string" || received.length !== expected.length ||
    !crypto.timingSafeEqual(Buffer.from(received), Buffer.from(expected))) {
  return res.status(403).json({ message: "Invalid webhook secret" });
}
```

:red_circle: [security] Webhook decrypts with empty-string fallback when `CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY` is missing in apps/web/pages/api/webhook/app-credential.ts:38 (confidence: 90)
`symmetricDecrypt(reqBody.keys, process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY || "")` silently falls back to an empty key if the env var is not set, which either crashes with a stack trace (information disclosure via 500) or, worse, succeeds against a predictable key under a misconfigured deployment. `APP_CREDENTIAL_SHARING_ENABLED` only gates on the webhook secret and encryption key being *present*, but there is no length/strength validation, and the `|| ""` swallows the misconfig. Refuse the request when the key is absent instead of falling back.
```suggestion
const encKey = process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY;
if (!encKey) return res.status(500).json({ message: "Server misconfiguration" });
const keys = JSON.parse(symmetricDecrypt(reqBody.keys, encKey));
```

:red_circle: [correctness] `minimumTokenResponseSchema` uses computed keys that evaluate to Zod object `toString()`, not dynamic fields in packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:5 (confidence: 97)
```ts
const minimumTokenResponseSchema = z.object({
  access_token: z.string(),
  [z.string().toString()]: z.number(),
  [z.string().optional().toString()]: z.unknown().optional(),
});
```
`[z.string().toString()]` is evaluated at module load and becomes a literal string (e.g. `"ZodString { ... }"`); it does not create a dynamic field validator. The schema therefore only actually validates `access_token`, plus two garbage-named literal keys. The comments ("Assume that any property with a number is the expiry", "Allow other properties") suggest the intent was `z.object({ access_token: z.string() }).passthrough()` or `z.record(z.unknown())`. As written, the schema silently accepts responses missing `expires_in`, `expiry_date`, etc., masking provider breakage.
```suggestion
const minimumTokenResponseSchema = z
  .object({ access_token: z.string() })
  .catchall(z.unknown());
```

:red_circle: [correctness] Webhook uses `schema.parse` (throws) instead of `safeParse`, so malformed bodies return 500 with a stack trace in apps/web/pages/api/webhook/app-credential.ts:22 (confidence: 90)
Every other endpoint in this PR uses `safeParse` / typed handling. Here `appCredentialWebhookRequestBodySchema.parse(req.body)` throws a `ZodError`, which propagates out of the handler and surfaces as a 500 with error details. A malformed (or missing) `req.body` from an attacker who already cleared the webhook secret check yields a stack trace and reveals internal field names. Validate and return 400 explicitly.
```suggestion
const parsed = appCredentialWebhookRequestBodySchema.safeParse(req.body);
if (!parsed.success) {
  return res.status(400).json({ message: "Invalid request body" });
}
const reqBody = parsed.data;
```

:red_circle: [correctness] Salesforce `CalendarService` fetches a new token on every service instantiation and never uses `refreshOAuthTokens` in packages/app-store/salesforce/lib/CalendarService.ts:75 (confidence: 88)
The new block unconditionally calls `https://login.salesforce.com/services/oauth2/token` every time the service is constructed — which is per request in most handlers. Previously jsforce refreshed lazily. This is (a) a major behavior change not flagged in the PR description, (b) bypasses the very `refreshOAuthTokens` / `CALCOM_CREDENTIAL_SYNC_ENDPOINT` indirection this PR is meant to introduce, so Salesforce token refresh will *not* hit the self-hoster's sync endpoint even when sharing is enabled, and (c) uses `response.statusText !== "OK"` which is unreliable (HTTP/2 responses often have an empty `statusText`, and Salesforce does not guarantee it). Gate the refresh on token expiry and route it through `refreshOAuthTokens`; check `!response.ok` rather than `statusText`.
```suggestion
if (isTokenExpired(credentialKey)) {
  const response = await refreshOAuthTokens(
    async () => fetch("https://login.salesforce.com/services/oauth2/token", { ... }),
    "salesforce",
    credential.userId
  );
  if (!response.ok) throw new HttpError({ statusCode: 400, message: response.statusText });
  ...
}
```

:red_circle: [security] Webhook accepts any HTTP method and no content-type check in apps/web/pages/api/webhook/app-credential.ts:10 (confidence: 85)
The handler never checks `req.method`, so `GET /api/webhook/app-credential` with the secret in a query parameter — or `DELETE`, `PUT` — all reach the decrypt/upsert path. Combined with Next.js default body parsing, a `GET` request with no body triggers the Zod `parse` which throws a 500 (see finding above). Restrict to `POST` and require `application/json`.
```suggestion
if (req.method !== "POST") {
  res.setHeader("Allow", "POST");
  return res.status(405).json({ message: "Method Not Allowed" });
}
```

## Improvements

:yellow_circle: [cross-file-impact] zoho-bigin passes `credentialId` instead of `credential.userId` to `refreshOAuthTokens` in packages/app-store/zoho-bigin/lib/CalendarService.ts:93 (confidence: 85)
Every other integration passes `credential.userId`. zoho-bigin passes `credentialId`. `refreshOAuthTokens` forwards this value to the sync endpoint as `calcomUserId`. The self-hoster will look up the wrong user, or no user at all, when rotating zoho-bigin tokens. Either this is a typo or `credentialId` happens to coincide with `userId` in test data — align with the rest of the codebase.
```suggestion
const tokenInfo = await refreshOAuthTokens(
  async () => await axios.post(accountsUrl, qs.stringify(formData), { ... }),
  "zoho-bigin",
  credential.userId
);
```

:yellow_circle: [consistency] `.env.example` says the encryption key must be 32 bytes but suggests `openssl rand -base64 24` in .env.example:240 (confidence: 90)
`rand -base64 24` produces 24 raw bytes encoded as a 32-character string; if `symmetricDecrypt` uses the decoded bytes (24B) it will fail AES-256 which requires 32B, and if it uses the encoded string (32 chars = 32 bytes ASCII) it happens to work but that's coincidence. The note "must be 32 bytes for AES256" contradicts the command. Either change the command to `openssl rand -base64 32` (44-char string, 32B decoded) or correct the comment to describe whatever format `symmetricDecrypt` actually expects. Self-hosters copy-pasting this today will produce keys of inconsistent length across parent/child instances.

:yellow_circle: [consistency] `APP_CREDENTIAL_SHARING_ENABLED` is typed as `string | "" | undefined`, not boolean in packages/lib/constants.ts:102 (confidence: 80)
The `&&` chain returns the last truthy value rather than coercing to boolean, so consumers using `!APP_CREDENTIAL_SHARING_ENABLED` work but `APP_CREDENTIAL_SHARING_ENABLED === true` does not. Wrap in `Boolean(...)` for a predictable type — especially since this flag is checked in hot paths across ten integrations and in a webhook handler.
```suggestion
export const APP_CREDENTIAL_SHARING_ENABLED = Boolean(
  process.env.CALCOM_WEBHOOK_SECRET && process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY
);
```

:yellow_circle: [correctness] Office365 `CalendarService` drops the previous `console.error` on failed token parse with no replacement in packages/app-store/office365calendar/lib/CalendarService.ts:264 (confidence: 75)
The old branch logged `"Outlook error grabbing new tokens ~ zodError:"` with the zod error + raw MS response, which was the only visible signal for the common "Microsoft returned an unexpected shape" failure. After the refactor `parseRefreshTokenResponse` throws, the error surfaces up the stack without the MS response attached. Re-attach the response payload to the thrown error so operators can debug MS-side regressions without instrumenting this path.

:yellow_circle: [consistency] zoho-bigin `api/add.ts` replaces `appConfig.slug` with a hardcoded string — unrelated to this PR in packages/app-store/zoho-bigin/api/add.ts:17 (confidence: 70)
`const redirectUri = WEBAPP_URL + \`/api/integrations/${appConfig.slug}/callback\`;` becomes `` `/api/integrations/zoho-bigin/callback` ``. This is a semantic change independent of the credential-sync feature; it should land in its own commit with a motivation. If `appConfig.slug` was wrong, fix `config.json`; if it was right, leave the template alone.

:yellow_circle: [testing] No tests for the new webhook endpoint or the `parseRefreshTokenResponse` / `refreshOAuthTokens` helpers in apps/web/pages/api/webhook/app-credential.ts:1 (confidence: 80)
The PR description explicitly says "I haven't added tests that prove my fix is effective or that my feature works." This endpoint decrypts caller-supplied ciphertext and upserts DB credentials — exactly the code path where tests matter most. Add at least: (1) rejected-secret returns 403, (2) wrong HTTP method returns 405, (3) malformed body returns 400 not 500, (4) existing credential is updated and new credential is created, (5) `parseRefreshTokenResponse` unwraps `.data` consistently, (6) `refreshOAuthTokens` returns the same shape from both branches.

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: 40 files across ten integrations + new webhook in sensitive auth path | Sensitive Paths: `apps/web/pages/api/webhook/*`, `.env.example`, `packages/app-store/_utils/oauth/*`, `packages/app-store/*/lib/CalendarService.ts`
AI-Authored Likelihood: MEDIUM — the `minimumTokenResponseSchema` computed-key Zod usage and the unused `accessTokenParsed.success` check after a throwing helper both pattern-match to LLM-drafted code that was not exercised.
