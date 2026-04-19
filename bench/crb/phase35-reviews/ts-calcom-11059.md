## Summary
40 files changed, 330 lines added, 110 lines deleted. 16 findings (7 critical, 9 improvements).
Credential-sync webhook is vulnerable to timing-attack secret recovery, and the refresh-token plumbing has a runtime shape mismatch that breaks every OAuth app refresh when the sync endpoint is enabled — consistent with the upstream revert for broken Google Calendar bookings.

## Critical

:red_circle: [correctness] refreshOAuthTokens returns raw fetch Response, breaking every consumer in packages/app-store/_utils/oauth/refreshOAuthTokens.ts:14 (confidence: 98)
When `APP_CREDENTIAL_SHARING_ENABLED` is true and `CALCOM_CREDENTIAL_SYNC_ENDPOINT` is set, the function returns a raw `fetch` `Response` object. Every consumer expects an SDK-shaped result: Google does `res?.data` (undefined), HubSpot does `hubspotRefreshToken.body.access_token` (undefined), zoho-bigin does `tokenInfo.data.error` (undefined — TypeError), Zoom/Webex/Office365 pass the object to helpers that call `.json()`. First token refresh after enabling sync throws `TypeError: Cannot read properties of undefined`. This is the most likely cause of the production revert.
```suggestion
const refreshOAuthTokens = async (refreshFunction: () => any, appSlug: string, userId: number | null) => {
  if (APP_CREDENTIAL_SHARING_ENABLED && process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId) {
    const response = await fetch(process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT, {
      method: "POST",
      body: new URLSearchParams({ calcomUserId: userId.toString(), appSlug }),
    });
    if (!response.ok) {
      throw new Error(`Credential sync endpoint returned ${response.status}`);
    }
    return await response.json();
  }
  return await refreshFunction();
};
```

:red_circle: [correctness] Null dereference on token.access_token when sync is enabled in packages/app-store/googlecalendar/lib/CalendarService.ts:96 (confidence: 97)
After `refreshOAuthTokens`, the code does `const token = res?.data; googleCredentials.access_token = token.access_token;`. When sync is enabled `res` is a `Response` (no `.data`), so `token` is undefined and the next line throws a TypeError. Even if `refreshOAuthTokens` is fixed to return the parsed JSON body, the sync endpoint returns a flat token object — still no `.data` wrapper. This is the exact runtime failure path that would block Google Calendar bookings.
```suggestion
const res = await refreshOAuthTokens(
  async () => {
    const fetchTokens = await myGoogleAuth.refreshToken(googleCredentials.refresh_token);
    return fetchTokens.res?.data;
  },
  "google-calendar",
  credential.userId
);
const token = res;
googleCredentials.access_token = token?.access_token;
googleCredentials.expiry_date = token?.expiry_date;
```

:red_circle: [correctness] Hardcoded literal string "refresh_token" written as the refresh_token value, permanently breaking future refreshes in packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:21 (confidence: 96)
`if (!refreshTokenResponse.data.refresh_token) { refreshTokenResponse.data.refresh_token = "refresh_token"; }` stores the string literal `"refresh_token"` as the credential's refresh token whenever a provider omits it on refresh (Google and most OAuth providers omit `refresh_token` after the initial authorization). The next refresh cycle then sends the string `"refresh_token"` as the actual token, receives `invalid_grant`, and the credential is unrecoverable until the user reconnects. Combined with the Google shape bug, this guarantees permanent credential corruption post-enablement.
```suggestion
const parseRefreshTokenResponse = (
  response: any,
  schema: z.ZodTypeAny,
  existingRefreshToken?: string
) => {
  // ... safeParse logic ...
  if (!refreshTokenResponse.success) {
    throw new Error("Invalid refreshed tokens were returned");
  }
  if (!refreshTokenResponse.data.refresh_token && existingRefreshToken) {
    refreshTokenResponse.data.refresh_token = existingRefreshToken;
  }
  return refreshTokenResponse;
};
```

:red_circle: [correctness] Broken zod schema — computed keys collapse to a single literal string, removing all validation of expiry in packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:4 (confidence: 95)
`[z.string().toString()]: z.number()` and `[z.string().optional().toString()]: z.unknown().optional()` invoke `.toString()` on a `ZodString`/`ZodOptional` instance — both return the same object-descriptor string (e.g. `"[object Object]"`), so the second computed property silently overwrites the first and the schema becomes effectively `{ access_token: z.string(), "[object Object]": z.unknown().optional() }`. There is no validation of the expiry field in the sync path. The intent was almost certainly `.passthrough()`.
```suggestion
const minimumTokenResponseSchema = z
  .object({
    access_token: z.string(),
  })
  .passthrough();
```

:red_circle: [security] Timing-unsafe webhook secret comparison enables remote secret recovery in apps/web/pages/api/webhook/app-credential.ts:21 (confidence: 95)
The header is compared with `!==`, which short-circuits on the first differing byte. An unauthenticated attacker can recover the webhook secret one byte at a time via response-timing measurements, then forge arbitrary writes to any user's `credential.key` through the endpoint. Privileged writes demand constant-time comparison.
```suggestion
import { timingSafeEqual } from "crypto";

function safeEqual(a: string | undefined, b: string | undefined): boolean {
  if (!a || !b) return false;
  const ab = Buffer.from(a);
  const bb = Buffer.from(b);
  if (ab.length !== bb.length) return false;
  return timingSafeEqual(ab, bb);
}

const headerName = process.env.CALCOM_WEBHOOK_HEADER_NAME || "calcom-webhook-secret";
const providedSecret = req.headers[headerName];
const expectedSecret = process.env.CALCOM_WEBHOOK_SECRET;
if (!expectedSecret || typeof providedSecret !== "string" || !safeEqual(providedSecret, expectedSecret)) {
  return res.status(403).json({ message: "Invalid webhook secret" });
}
```
[References: CWE-208, OWASP A02:2021, OWASP A07:2021]

:red_circle: [security] Fallback to empty encryption key silently decrypts with zero/derived key in apps/web/pages/api/webhook/app-credential.ts:37 (confidence: 92)
`symmetricDecrypt(reqBody.keys, process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY || "")` coerces a missing env var to the empty string. Because `APP_CREDENTIAL_SHARING_ENABLED` is evaluated at module load and only checks truthiness once, a later unset or misconfiguration reaches the decrypt path with `""` as the key — either enabling deterministic-key decryption by an attacker or producing an unhandled crypto exception leaking internals. The decrypted output is then written directly to `credential.key`.
```suggestion
const encryptionKey = process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY;
if (!encryptionKey) {
  console.error("CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY is not configured");
  return res.status(500).json({ message: "Server misconfiguration" });
}

let keys: unknown;
try {
  keys = JSON.parse(symmetricDecrypt(reqBody.keys, encryptionKey));
} catch {
  return res.status(400).json({ message: "Invalid encrypted payload" });
}
```
[References: CWE-321, CWE-1188, OWASP A02:2021]

:red_circle: [security] Decrypted JSON written unvalidated to credential.key (arbitrary shape injection) in apps/web/pages/api/webhook/app-credential.ts:42 (confidence: 88)
After `JSON.parse(symmetricDecrypt(...))`, the result is passed verbatim as `data: { key: keys }` to `prisma.credential.update`/`create`. There is no schema check that `keys` matches the expected OAuth-credential shape for `appMetadata.type`. A caller with the webhook secret can store arbitrary structures, including values consumed unsafely by downstream refresh code, or oversized payloads causing DB bloat.
```suggestion
const appKeySchemas: Record<string, z.ZodTypeAny> = {
  google_calendar: z.object({
    access_token: z.string(),
    refresh_token: z.string(),
    expiry_date: z.number().int(),
    scope: z.string().optional(),
    token_type: z.string().optional(),
  }),
  // ... other apps
};

const schema = appKeySchemas[appMetadata.slug];
if (!schema) {
  return res.status(400).json({ message: "App does not support credential sync" });
}
const validatedKeys = schema.parse(keys);

await prisma.credential.update({
  where: { id: appCredential.id },
  data: { key: validatedKeys },
});
```
[References: CWE-20, CWE-502, OWASP A08:2021]

## Improvements

:yellow_circle: [correctness] Salesforce token refresh bypasses refreshOAuthTokens entirely — sync feature non-functional in packages/app-store/salesforce/lib/CalendarService.ts:75 (confidence: 92)
The Salesforce CalendarService calls `fetch("https://login.salesforce.com/...")` directly instead of wrapping it in `refreshOAuthTokens` like the other 7 integrations in this PR. When sync is enabled the source-of-truth credential store is never notified, and the Salesforce app silently diverges from every other app in the store.
```suggestion
import refreshOAuthTokens from "../../_utils/oauth/refreshOAuthTokens";

const response = await refreshOAuthTokens(
  async () =>
    await fetch("https://login.salesforce.com/services/oauth2/token", {
      method: "POST",
      body: new URLSearchParams({
        grant_type: "refresh_token",
        client_id: consumer_key,
        client_secret: consumer_secret,
        refresh_token: credentialKey.refresh_token,
        format: "json",
      }),
    }),
  "salesforce",
  credential.userId
);
```

:yellow_circle: [correctness] response.statusText !== "OK" is an unreliable HTTP success check in packages/app-store/salesforce/lib/CalendarService.ts:86 (confidence: 95)
Under HTTP/2 (and in Node 18+ undici) `response.statusText` is frequently empty even on 200. The expression `response.statusText !== "OK"` therefore trips on successful responses, throwing an `HttpError` on every Salesforce token refresh. The canonical check is `!response.ok`.
```suggestion
if (!response.ok) {
  throw new HttpError({ statusCode: response.status, message: response.statusText || "Token refresh failed" });
}
```

:yellow_circle: [correctness] Dead/unreachable `if (!accessTokenParsed.success)` branch in packages/app-store/salesforce/lib/CalendarService.ts:92 (confidence: 90)
`parseRefreshTokenResponse` throws when the safeParse fails, so control never reaches a `.success === false` state. The guard and its `Promise.reject(...)` are unreachable and mislead future maintainers about the error-handling contract.
```suggestion
await prisma.credential.update({
  where: { id: credential.id },
  data: { key: { ...accessTokenParsed.data, refresh_token: credentialKey.refresh_token } },
});
```

:yellow_circle: [correctness] Misleading `tokenResponse.success && tokenResponse.data` spread — error branch was deleted in packages/app-store/office365calendar/lib/CalendarService.ts:261 (confidence: 88)
Since `parseRefreshTokenResponse` throws on failure, `tokenResponse.success` is always true here. The prior `if (!tokenResponse.success) console.error(...)` block was removed; the boolean guard that remained masks that fact. Simplify the spread and ensure the caller's try/catch handles the thrown error.
```suggestion
o365AuthCredentials = { ...o365AuthCredentials, ...tokenResponse.data };
```

:yellow_circle: [correctness] Unhandled ZodError leaks schema structure via 500 in apps/web/pages/api/webhook/app-credential.ts:31 (confidence: 85)
`appCredentialWebhookRequestBodySchema.parse(req.body)` throws on invalid input. No surrounding try/catch, so Next.js returns a 500 with a Zod stack trace that reveals internal schema field names. Every other error path in this handler returns an explicit 4xx.
```suggestion
const parsed = appCredentialWebhookRequestBodySchema.safeParse(req.body);
if (!parsed.success) {
  return res.status(400).json({ message: "Invalid request body", errors: parsed.error.flatten() });
}
const reqBody = parsed.data;
```

:yellow_circle: [security] No HTTP method check — non-POST requests reach credential write logic in apps/web/pages/api/webhook/app-credential.ts:13 (confidence: 90)
The handler never asserts `req.method === "POST"`. GET/PUT/DELETE/OPTIONS all reach the zod parse and mutation logic, broadening the attack surface (CSRF-style GETs on proxies that parse query as body, cache-poisoning on intermediaries).
```suggestion
if (req.method !== "POST") {
  res.setHeader("Allow", "POST");
  return res.status(405).json({ message: "Method not allowed" });
}
```
[References: CWE-352, OWASP A05:2021]

:yellow_circle: [security] No rate limiting on credential-sync webhook in apps/web/pages/api/webhook/app-credential.ts:1 (confidence: 85)
The endpoint performs no rate limiting or lockout on 403s, leaving webhook-secret brute force and auth-failure flooding unmitigated. Cal.com already ships `checkRateLimitAndThrowError` for exactly this pattern.
```suggestion
import { checkRateLimitAndThrowError } from "@calcom/lib/checkRateLimitAndThrowError";

await checkRateLimitAndThrowError({
  rateLimitingType: "core",
  identifier: `webhook-app-credential:${req.headers["x-forwarded-for"] ?? req.socket.remoteAddress}`,
});
```
[References: CWE-307, OWASP A04:2021]

:yellow_circle: [security] Outbound credential-sync fetch lacks authentication, TLS enforcement, and timeout in packages/app-store/_utils/oauth/refreshOAuthTokens.ts:5 (confidence: 82)
The POST to `CALCOM_CREDENTIAL_SYNC_ENDPOINT` carries no signature, bearer, or HMAC. It does not require `https://`, does not enforce a timeout/abort, and trusts the response body implicitly. A misconfigured `http://` endpoint or a DNS compromise lets a MITM return attacker-chosen tokens that the adapter code trusts; a hung remote blocks OAuth refresh paths indefinitely.
```suggestion
const endpoint = process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT;
if (!endpoint || !endpoint.startsWith("https://")) {
  throw new Error("CALCOM_CREDENTIAL_SYNC_ENDPOINT must be an https URL");
}
const body = new URLSearchParams({
  calcomUserId: userId.toString(),
  appSlug,
  timestamp: Date.now().toString(),
});
const signature = crypto
  .createHmac("sha256", process.env.CALCOM_WEBHOOK_SECRET ?? "")
  .update(body.toString())
  .digest("hex");
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 10_000);
try {
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", "x-cal-signature": signature },
    body,
    signal: controller.signal,
  });
  return response;
} finally {
  clearTimeout(timer);
}
```
[References: CWE-306, CWE-319, OWASP A10:2021]

:yellow_circle: [security] TOCTOU between findFirst and update/create allows duplicate credential rows in apps/web/pages/api/webhook/app-credential.ts:43 (confidence: 80)
Two concurrent webhook deliveries for the same `(userId, appSlug)` can both observe `null` from `findFirst` and race to `create`, producing duplicate credentials. Downstream code that assumes one credential per (user, app) may pick a stale one — an attacker that can trigger the race with an older encrypted payload effectively gets credential rollback.
```suggestion
// Requires a @@unique([userId, appId]) in schema.prisma
await prisma.credential.upsert({
  where: { userId_appId: { userId: reqBody.userId, appId: appMetadata.slug } },
  update: { key: validatedKeys },
  create: {
    key: validatedKeys,
    userId: reqBody.userId,
    appId: appMetadata.slug,
    type: appMetadata.type,
  },
});
```
[References: CWE-367, OWASP A04:2021]

## Risk Metadata
Risk Score: 85/100 (HIGH) | Blast Radius: 40 files across auth/OAuth for 10+ integrations, reverted upstream due to broken Google Calendar bookings | Sensitive Paths: new public webhook under `apps/web/pages/api/webhook/`, symmetric encryption key handling, `credential.key` writes for arbitrary users
AI-Authored Likelihood: LOW
