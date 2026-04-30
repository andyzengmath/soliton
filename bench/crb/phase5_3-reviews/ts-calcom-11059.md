## Summary
40 files changed, ~336 lines added, ~119 lines deleted. 11 findings (5 critical, 4 improvements, 2 nitpicks).
PR introduces app-credential sync for self-hosters by wrapping every OAuth refresh path through a new `refreshOAuthTokens` helper, plus a webhook endpoint for credential ingest. The wrapper has a return-type mismatch that breaks the non-sync path on multiple integrations (matches the upstream revert reason — "preventing bookings with gCal"); the `parseRefreshTokenResponse` zod schema is broken; the webhook handler has timing-attack-vulnerable secret comparison and unhandled-throw paths.

## Critical

:red_circle: [correctness] `refreshOAuthTokens` return shape diverges between sync and non-sync paths — breaks Google Calendar bookings in `packages/app-store/googlecalendar/lib/CalendarService.ts:84` (confidence: 95)

In the non-sync path the inner `refreshFunction` returns `fetchTokens.res` (a `GaxiosResponse` whose `.data` carries the token). In the sync path the helper returns `await fetch(CALCOM_CREDENTIAL_SYNC_ENDPOINT, …)` — a raw `Response` with no `.data` field and an unparsed body. Callers immediately do `const token = res?.data; googleCredentials.access_token = token.access_token;` so when sync is enabled `token` is `undefined` and the next line throws `Cannot read properties of undefined (reading 'access_token')`, blocking every Google Calendar booking that triggers a token refresh. Same return-shape mismatch repeats in `office365calendar/lib/CalendarService.ts`, `office365video/lib/VideoApiAdapter.ts`, `webex/lib/VideoApiAdapter.ts`, `zoomvideo/lib/VideoApiAdapter.ts`, `hubspot/lib/CalendarService.ts`, `larkcalendar/lib/CalendarService.ts`, `zoho-bigin/lib/CalendarService.ts`, `zohocrm/lib/CalendarService.ts`. This is consistent with the maintainer comment on the PR ("This had to be reverted. It was preventing bookings with gCal (other cals could be affected)").
```suggestion
// In refreshOAuthTokens.ts, normalise the sync-path return so it has the
// same shape callers expect, e.g. parse the JSON body and wrap it:
const refreshOAuthTokens = async (refreshFunction, appSlug, userId) => {
  if (APP_CREDENTIAL_SHARING_ENABLED && process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId) {
    const response = await fetch(process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT, {
      method: "POST",
      body: new URLSearchParams({ calcomUserId: userId.toString(), appSlug }),
    });
    if (!response.ok) throw new Error(`Credential sync endpoint failed: ${response.status}`);
    const data = await response.json();
    return { data }; // match GaxiosResponse-like shape used by callers
  }
  return refreshFunction();
};
```

:red_circle: [correctness] `minimumTokenResponseSchema` does not actually allow extra keys — silently rejects every real OAuth response in `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:5` (confidence: 95)

```
const minimumTokenResponseSchema = z.object({
  access_token: z.string(),
  [z.string().toString()]: z.number(),                     // key is the literal "ZodString"
  [z.string().optional().toString()]: z.unknown().optional(), // key is the literal "ZodOptional"
});
```
Computed property keys are evaluated to strings at object-literal construction time. `z.string().toString()` returns the class-name string `"ZodString"`, not a wildcard matcher; the schema therefore declares two literal fields named `"ZodString"` and `"ZodOptional"` and is *not* a passthrough/catchall. With the default zod strictness, any real provider response (`access_token`, `expires_in`, `refresh_token`, `scope`, …) will either be silently stripped of the unknown fields or fail the safeParse depending on the strict mode in use, and the downstream sync-path tokens will be missing `expiry_date`/`expires_in`. Use `z.passthrough()` or `.catchall(z.unknown())` to express "minimum required, allow others".
```suggestion
const minimumTokenResponseSchema = z
  .object({
    access_token: z.string(),
  })
  .catchall(z.unknown());
```

:red_circle: [security] Non-constant-time secret comparison in webhook handler — timing-attack vulnerable in `apps/web/pages/api/webhook/app-credential.ts:25` (confidence: 90)

```
if (
  req.headers[process.env.CALCOM_WEBHOOK_HEADER_NAME || "calcom-webhook-secret"] !==
  process.env.CALCOM_WEBHOOK_SECRET
) {
  return res.status(403).json({ message: "Invalid webhook secret" });
}
```
JavaScript `!==` short-circuits on the first byte that differs, leaking the shared secret one byte at a time over a sufficient sample. Webhook secrets that gate a credential-ingest endpoint must be compared in constant time. Also, an attacker can send the header as a string array (`?h[]=a&h[]=b`) — `req.headers[name]` then becomes `string[]`, the strict-equals returns false, but the type annotation is silently widened.
```suggestion
import crypto from "crypto";
const headerName = process.env.CALCOM_WEBHOOK_HEADER_NAME || "calcom-webhook-secret";
const provided = req.headers[headerName];
const expected = process.env.CALCOM_WEBHOOK_SECRET;
if (
  typeof provided !== "string" ||
  typeof expected !== "string" ||
  provided.length !== expected.length ||
  !crypto.timingSafeEqual(Buffer.from(provided), Buffer.from(expected))
) {
  return res.status(403).json({ message: "Invalid webhook secret" });
}
```
[References: https://owasp.org/www-community/attacks/Timing_attack]

:red_circle: [correctness] Webhook handler throws on bad input → 500 instead of 400; decryption errors leak as 500 in `apps/web/pages/api/webhook/app-credential.ts:31` (confidence: 90)

`appCredentialWebhookRequestBodySchema.parse(req.body)` throws `ZodError` on a malformed payload, which Next.js surfaces as an unhandled-error 500. Worse, `JSON.parse(symmetricDecrypt(reqBody.keys, …))` will throw on either an invalid ciphertext (decryption failure) or malformed JSON, again returning 500 without context. A self-hoster integrating this webhook will be unable to distinguish "bad encryption key" from a server bug. Wrap in safeParse and a try/catch and return 400 with a descriptive message.
```suggestion
const parsed = appCredentialWebhookRequestBodySchema.safeParse(req.body);
if (!parsed.success) {
  return res.status(400).json({ message: "Invalid request body", issues: parsed.error.issues });
}
const reqBody = parsed.data;
// ...
let keys;
try {
  keys = JSON.parse(
    symmetricDecrypt(reqBody.keys, process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY || "")
  );
} catch (e) {
  return res.status(400).json({ message: "Failed to decrypt or parse keys" });
}
```

:red_circle: [correctness] Salesforce token refresh now runs unconditionally inside `getIntegrationsListItem` constructor in `packages/app-store/salesforce/lib/CalendarService.ts:75` (confidence: 85)

The new `fetch("https://login.salesforce.com/services/oauth2/token", …)` block is added at the top of the constructor and runs on **every** `new SalesforceCalendarService(credential)`, even when the existing access token is still valid. Three problems: (1) it adds a synchronous round-trip to Salesforce on every booking lookup that touches the service, (2) it ignores `expiry_date` on the existing credential, (3) `if (response.statusText !== "OK") throw new HttpError(...)` makes the whole constructor reject on any transient Salesforce 5xx, breaking every event flow that touches Salesforce. Gate the refresh on `isExpired(credentialKey.expiry_date)` and don't throw out of the constructor — defer to the existing jsforce refresh hook instead.

## Improvements

:yellow_circle: [silent-failure] Office 365 token-parse error logging removed — silent failures will mask refresh-token bugs in `packages/app-store/office365calendar/lib/CalendarService.ts:262` (confidence: 90)

The previous code logged on parse failure:
```
if (!tokenResponse.success) {
  console.error("Outlook error grabbing new tokens ~ zodError:", tokenResponse.error, "MS response:", responseJson);
}
```
This block was deleted as part of the `parseRefreshTokenResponse` migration. Combined with `parseRefreshTokenResponse` now silently swallowing parse failures (it returns a `safeParse` result that callers never inspect for `.success`) and the `refresh_token = "refresh_token"` placeholder fallback, Outlook token refresh failures will now be invisible in logs while persisting a bogus token to the DB.
```suggestion
const tokenResponse = parseRefreshTokenResponse(responseJson, refreshTokenResponseSchema);
if (!tokenResponse.success) {
  console.error(
    "Outlook error grabbing new tokens ~ zodError:",
    tokenResponse.error,
    "MS response:",
    responseJson
  );
}
```

:yellow_circle: [correctness] `parseRefreshTokenResponse` silently writes the literal string `"refresh_token"` when missing in `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:21` (confidence: 90)

```
if (!refreshTokenResponse.data.refresh_token) {
  refreshTokenResponse.data.refresh_token = "refresh_token";
}
```
Persisting the literal `"refresh_token"` as the actual refresh token poisons the credential row — the next refresh will POST `refresh_token=refresh_token` to the provider, which fails, and the user is left in a broken integration with no log trace. The prior schema validation `googleCredentialSchema.parse(...)` would have rejected this. Either preserve the existing `refresh_token` from the DB credential, or fail loudly when the provider didn't return one.
```suggestion
if (!refreshTokenResponse.data.refresh_token) {
  // Provider omitted refresh_token (Google rotates only on first grant).
  // Caller is responsible for merging the previously-stored refresh_token.
  delete refreshTokenResponse.data.refresh_token;
}
```

:yellow_circle: [security] Webhook endpoint has no rate limiting and accepts arbitrary `userId` in `apps/web/pages/api/webhook/app-credential.ts:17` (confidence: 80)

The handler creates/updates `prisma.credential` rows for any `userId` provided in the body once the shared secret is known. Combined with the timing-attack issue above, a leaked secret allows arbitrary credential injection across all tenants. Even with a fixed comparison, this endpoint should be rate-limited and ideally bound to an admin scope — at minimum, log the `userId` and `appSlug` for every accepted call. Consider adding `checkRateLimitAndThrowError` middleware as used elsewhere in the codebase.

:yellow_circle: [consistency] `parseRefreshTokenResponse` typed with `any` for response in `packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:13` (confidence: 80)

`(response: any, schema: z.ZodTypeAny)` defeats the type-safety the rest of the OAuth refactor is trying to introduce. Use `unknown` and let the schema narrow it.
```suggestion
const parseRefreshTokenResponse = <T extends z.ZodTypeAny>(response: unknown, schema: T) => {
```

## Risk Metadata
Risk Score: 90/100 (CRITICAL) | Blast Radius: every OAuth-based app integration (Google Calendar, Office 365, Outlook video, Zoom, Webex, HubSpot, Lark, Salesforce, Zoho CRM, Zoho Bigin) + new public webhook endpoint accepting encrypted credentials | Sensitive Paths: `apps/web/pages/api/webhook/app-credential.ts` (auth + crypto + credential write), `_utils/oauth/*` (token refresh fan-in), `.env.example` (new secret keys: `CALCOM_WEBHOOK_SECRET`, `CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY`)
AI-Authored Likelihood: MEDIUM — the broken `[z.string().toString()]` computed-key idiom is a characteristic LLM hallucination of how to express "passthrough" in zod; otherwise the changes look hand-authored.

(2 additional findings below confidence threshold)
