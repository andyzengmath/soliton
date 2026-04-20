## Summary
40 files changed, 335 lines added, 121 lines deleted. 9 findings (6 critical, 3 improvements).
Security-sensitive PR introducing a credential-sync webhook, new OAuth refresh abstraction, and env-gated app-credential sharing — several crypto/auth, Zod mis-use, and type-contract issues need to be resolved before merge.

## Critical
:red_circle: [security] Timing-unsafe webhook secret comparison in apps/web/pages/api/webhook/app-credential.ts:25 (confidence: 95)
The webhook secret check uses plain string inequality (`!==`), which compares byte-by-byte and short-circuits on the first mismatch. An attacker with network access to the endpoint can use response-time differences to brute-force the secret one byte at a time. Since a successful comparison grants unrestricted ability to overwrite any user's third-party credentials, this is a high-severity oracle. Use `crypto.timingSafeEqual` over fixed-length Buffers after normalizing both sides.
```suggestion
  const providedSecret = req.headers[process.env.CALCOM_WEBHOOK_HEADER_NAME?.toLowerCase() || "calcom-webhook-secret"];
  const expectedSecret = process.env.CALCOM_WEBHOOK_SECRET;
  if (
    typeof providedSecret !== "string" ||
    !expectedSecret ||
    providedSecret.length !== expectedSecret.length ||
    !crypto.timingSafeEqual(Buffer.from(providedSecret), Buffer.from(expectedSecret))
  ) {
    return res.status(403).json({ message: "Invalid webhook secret" });
  }
```
[References: https://codahale.com/a-lesson-in-timing-attacks/, https://nodejs.org/api/crypto.html#cryptotimingsafeequala-b]

:red_circle: [correctness] `z.parse()` throws on invalid body, leaking stack/schema to caller in apps/web/pages/api/webhook/app-credential.ts:34 (confidence: 92)
`appCredentialWebhookRequestBodySchema.parse(req.body)` throws `ZodError` on any malformed request. Next.js has no default handler here, so the response becomes a 500 with the error message (including the full Zod issue list — field names, expected types, received values) echoed back to the caller. This both (a) breaks the clean 400-semantics contract of the endpoint and (b) leaks schema internals to unauthenticated callers who guessed the secret. Use `safeParse` and return a 400.
```suggestion
  const parseResult = appCredentialWebhookRequestBodySchema.safeParse(req.body);
  if (!parseResult.success) {
    return res.status(400).json({ message: "Invalid request body" });
  }
  const reqBody = parseResult.data;
```

:red_circle: [hallucination] `minimumTokenResponseSchema` computed-key pattern does not do what the comment claims in packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:5 (confidence: 95)
```
const minimumTokenResponseSchema = z.object({
  access_token: z.string(),
  [z.string().toString()]: z.number(),
  [z.string().optional().toString()]: z.unknown().optional(),
});
```
`z.string().toString()` returns the literal string `"ZodString"` (the class's default `toString`) — not a dynamic wildcard key. So this schema registers a property literally named `"ZodString"` of type `number`, and a second duplicate key (`"ZodString"` again from the optional branch) — the second silently overwrites the first in the object literal. There is no Zod API that lets you say "any property whose value is a number is valid" via a computed key. Use `z.record(z.string(), z.unknown())`, or `.passthrough()` on a base object with `access_token` explicitly required. As written, the schema accepts `{ access_token: "x" }` with any other keys ignored — the "expiry" validation the comment promises never runs.
```suggestion
const minimumTokenResponseSchema = z
  .object({
    access_token: z.string(),
    expires_in: z.number().optional(),
    expiry_date: z.number().optional(),
  })
  .passthrough();
```

:red_circle: [correctness] `refresh_token` is silently overwritten with the literal string `"refresh_token"` in packages/app-store/_utils/oauth/parseRefreshTokenResponse.ts:20 (confidence: 96)
```
if (!refreshTokenResponse.data.refresh_token) {
  refreshTokenResponse.data.refresh_token = "refresh_token";
}
```
When the upstream IdP (or the self-hoster's sync endpoint) returns a token response without `refresh_token` — which is normal for most providers on a refresh-grant — this code writes the literal placeholder string `"refresh_token"` into the credential. That string is then persisted via `prisma.credential.update({ data: { key } })` by every caller, and the next refresh cycle will send `"refresh_token"` as the actual refresh token, failing. Either preserve the existing refresh_token from the stored credential, or do not mutate the field at all.
```suggestion
  if (!refreshTokenResponse.data.refresh_token) {
    // Most providers omit refresh_token on re-refresh; preserve the caller's existing value.
    delete refreshTokenResponse.data.refresh_token;
  }
```

:red_circle: [correctness] `refreshOAuthTokens` return-type contract differs between branches in packages/app-store/_utils/oauth/refreshOAuthTokens.ts:9 (confidence: 90)
The function returns either (a) a whatwg `Response` object when sync is enabled (from `await fetch(...)`), or (b) whatever the caller's `refreshFunction` returns (Google: `{ access_token, expiry_date, ... }`; HubSpot: `HubspotToken`; Zoho: an `AxiosResponse` with `.data`; Office365: a `Response`). Every call site assumes a particular shape:
- `HubspotCalendarService.ts`: uses the value as a `HubspotToken` directly (would crash on `.response.body` access when the sync path returns a raw `Response`).
- `ZohoCrmCalendarService.ts` / `BiginCalendarService.ts`: reads `tokenInfo.data.error` / `tokenInfo.data.expiryDate` — fails on a `Response` from sync.
- `GoogleCalendarService.ts`: assigns `res.data.access_token` — also fails on sync-path `Response`.
When `CALCOM_CREDENTIAL_SYNC_ENDPOINT` is configured, every one of these callers will throw at first use. The wrapper needs to normalize both branches to a single shape (e.g., always return a `Response` and make the refreshFunction branch wrap its result, or always return parsed JSON), and the return type must be explicit.
```suggestion
const refreshOAuthTokens = async <T>(
  refreshFunction: () => Promise<T>,
  appSlug: string,
  userId: number | null
): Promise<T | Response> => {
  if (APP_CREDENTIAL_SHARING_ENABLED && process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT && userId) {
    const response = await fetch(process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT, {
      method: "POST",
      body: new URLSearchParams({ calcomUserId: userId.toString(), appSlug }),
    });
    if (!response.ok) {
      throw new Error(`Credential sync endpoint returned ${response.status}`);
    }
    return response;
  }
  return refreshFunction();
};
```
Additionally, the sync branch neither checks `response.ok` nor parses the body — errors from the self-hoster's endpoint are propagated as healthy tokens.

:red_circle: [correctness] Salesforce refresh now runs unconditionally on every service instantiation and references `.success` on a value that cannot fail in packages/app-store/salesforce/lib/CalendarService.ts:72 (confidence: 88)
The new block runs a refresh POST to `login.salesforce.com` every time `SalesforceCalendarService` is constructed — regardless of token expiry — and writes the result to the DB. This is both (a) a heavy perf regression (one extra network round-trip per calendar operation) and (b) rate-limit exposure against Salesforce. Additionally, `parseRefreshTokenResponse` throws when parsing fails, so `accessTokenParsed.success` can only ever be truthy at that point — the `if (!accessTokenParsed.success)` guard is dead code. Also, `response.statusText !== "OK"` is an unreliable check (HTTP/2 omits reason phrases, proxies can normalize casing); use `response.ok`.
```suggestion
    // Only refresh when the stored token is actually expiring.
    if (!isTokenExpiring(credentialKey)) return buildConnection(credentialKey);

    const response = await fetch("https://login.salesforce.com/services/oauth2/token", { /* ... */ });
    if (!response.ok) throw new HttpError({ statusCode: 400, message: await response.text() });
    const accessTokenJson = await response.json();
    const accessTokenParsed = parseRefreshTokenResponse(accessTokenJson, salesforceTokenSchema);
    await prisma.credential.update({
      where: { id: credential.id },
      data: { key: { ...accessTokenParsed.data, refresh_token: credentialKey.refresh_token } },
    });
```

## Improvements
:yellow_circle: [security] Missing per-user authorization — webhook can overwrite any user's credentials in apps/web/pages/api/webhook/app-credential.ts:42 (confidence: 86)
Once the webhook secret is supplied, the caller may specify an arbitrary `userId` and `appSlug` and will overwrite (or create) any existing credential for that pair. For self-hosters that's by design, but consider: (1) scoping the webhook secret to a known tenant/org and verifying that `userId` belongs to it, (2) logging every create/update with source IP + user-agent for audit, and (3) rate-limiting by IP. Currently a leaked secret equates to full credential-replacement across the entire install.
```suggestion
  // After `user` lookup, verify it belongs to the calling tenant (if multi-tenant):
  // if (user.tenantId !== inferredTenantFromSecret) return res.status(403).json({ message: "Forbidden" });
  // Also: audit log every create/update with req.socket.remoteAddress and req.headers["user-agent"].
```

:yellow_circle: [correctness] `APP_CREDENTIAL_SHARING_ENABLED` is a string|undefined, not a boolean; gate is also inconsistent with refresh path in packages/lib/constants.ts:103 (confidence: 87)
```
export const APP_CREDENTIAL_SHARING_ENABLED =
  process.env.CALCOM_WEBHOOK_SECRET && process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY;
```
This evaluates to the **last string** or `undefined` — the type is `string | undefined`, not `boolean`. It works in `if (!APP_CREDENTIAL_SHARING_ENABLED)` truthiness checks but leaks into public types and Zod-narrowed code paths. Moreover, `refreshOAuthTokens` additionally requires `CALCOM_CREDENTIAL_SYNC_ENDPOINT` to actually do anything, which means there are two different definitions of "enabled" — the webhook gate passes without the sync endpoint set, and vice versa. Prefer a single `Boolean(...)` coerce covering all three env vars, plus a runtime startup assertion that `CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY` is 32 bytes.
```suggestion
export const APP_CREDENTIAL_SHARING_ENABLED = Boolean(
  process.env.CALCOM_WEBHOOK_SECRET &&
    process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY &&
    process.env.CALCOM_CREDENTIAL_SYNC_ENDPOINT
);
```

:yellow_circle: [security] Empty-string fallback for AES256 encryption key in apps/web/pages/api/webhook/app-credential.ts:95 (confidence: 88)
```
symmetricDecrypt(reqBody.keys, process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY || "")
```
If the env var is unset or empty but `APP_CREDENTIAL_SHARING_ENABLED` somehow passed (e.g., only webhook-secret was set because of the gate inconsistency noted above), this passes an empty string to the decryption routine. Depending on `symmetricDecrypt`'s implementation this either throws a cryptic crypto-provider error or (worse) proceeds with a zero-padded key. Hard-fail explicitly, and check for the 32-byte (AES-256) requirement that the `.env.example` comment promises.
```suggestion
  const encryptionKey = process.env.CALCOM_APP_CREDENTIAL_ENCRYPTION_KEY;
  if (!encryptionKey || Buffer.byteLength(encryptionKey, "utf8") < 32) {
    return res.status(500).json({ message: "App credential encryption key is not configured correctly" });
  }
  const keys = JSON.parse(symmetricDecrypt(reqBody.keys, encryptionKey));
```

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: 40 files touched across webhook endpoint, crypto, and 10 OAuth integrations (Google, HubSpot, Lark, Office365, Salesforce, Stripe, Tandem, Webex, Zoho/Bigin, Zoom) — any error in the new refresh wrapper breaks every integration's refresh path. | Sensitive Paths: `apps/web/pages/api/webhook/*`, `_utils/oauth/*`, `lib/crypto` consumer, and all `*/lib/CalendarService.ts` / `*/lib/VideoApiAdapter.ts` refresh flows.
AI-Authored Likelihood: MEDIUM-HIGH — the `[z.string().toString()]: z.number()` computed-key pattern in `parseRefreshTokenResponse.ts` and the `refresh_token = "refresh_token"` placeholder are both archetypal "looks right, doesn't compile the way you think" LLM tells; surrounding code is clean and likely human-written.
