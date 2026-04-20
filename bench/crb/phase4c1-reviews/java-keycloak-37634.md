## Summary
28 files changed, 722 lines added, 26 lines deleted. 5 findings (2 critical, 3 improvements).
PR introduces a new `TokenContextEncoderProvider` SPI that prefixes access-token IDs with 6-char session/token/grant-type shortcuts (e.g. `onrtac:<uuid>`). Core design is sound, but two bugs slipped in: a copy-paste error in `AccessTokenContext` validation that leaves `rawTokenId` unchecked, and a doubly-broken `AssertEvents.isAccessTokenId` matcher that silently no-ops the grant-type assertions across CIBA/device/authcode/refresh event tests.

## Critical

:red_circle: [correctness] `rawTokenId` null-check is missing; `grantType` is validated twice in `AccessTokenContext.java:207` (confidence: 99)
Lines 205-211 of the new `AccessTokenContext` constructor call `Objects.requireNonNull` four times, but two of them target the same field (`grantType`) while no check exists for `rawTokenId`. The second `grantType` check uses the message `"Null rawTokenId not allowed"` — an obvious copy-paste slip. Effect: a caller can construct an `AccessTokenContext` with `rawTokenId == null`, and the subsequent `encodeTokenId(...)` will emit `"onrtac:null"` via string concatenation rather than failing fast at construction. The real `grantType` contract is also weaker than intended because redundant guards hide intent.
```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

:red_circle: [testing] `AssertEvents.isAccessTokenId` uses wrong substring bounds AND an inverted equality check, making the new matcher effectively a no-op in `AssertEvents.java:1118` (confidence: 95)
The encoded token ID format is 6 chars + `:` + UUID, laid out as `ss tt gg : uuid` (session 0-1, token 2-3, grant 4-5). The new matcher does `items[0].substring(3, 5).equals(expectedGrantShortcut)` — that reads positions 3 and 4 (last char of token-type + first char of grant-type), not the grant shortcut. On top of that the guard is inverted: `if (... .equals(expectedGrantShortcut)) return false;` returns `false` exactly when the grant *matches*, so the only path to `true` is when the extracted (wrong) substring does NOT equal the expected shortcut. Net effect across `expectCodeToToken`, `expectDeviceCodeToToken`, `expectRefresh`, and `expectAuthReqIdToToken`: the matcher succeeds for essentially any well-formed token ID regardless of grant, silently defeating the integration-test assertions that are supposed to pin the new encoding.
```suggestion
    public static Matcher<String> isAccessTokenId(String expectedGrantShortcut) {
        return new TypeSafeMatcher<String>() {
            @Override
            protected boolean matchesSafely(String item) {
                String[] items = item.split(":");
                if (items.length != 2) return false;
                if (items[0].length() != 6) return false;
                // Grant type shortcut occupies chars 4-5 of the encoded prefix
                if (!items[0].substring(4, 6).equals(expectedGrantShortcut)) return false;
                return isUUID().matches(items[1]);
            }

            @Override
            public void describeTo(Description description) {
                description.appendText("Not a Token ID with expected grant: " + expectedGrantShortcut);
            }
        };
    }
```

## Improvements

:yellow_circle: [consistency] `RefreshTokenGrantTypeFactory.GRANT_SHORTCUT = "rt"` collides with `AccessTokenContext.TokenType.REGULAR.shortcut = "rt"` in `RefreshTokenGrantTypeFactory.java:32` (confidence: 85)
The encoded ID is position-indexed so there is no functional ambiguity today, but having the literal string `"rt"` mean *two different things* in the same 6-character encoded string (regular token at positions 2-3, refresh-token grant at positions 4-5) is a real readability / future-refactor hazard. A future change that swaps to a delimited format (like the earlier `st.on_tt.rt_gt.rt` format mentioned in the PR description) or that reuses the same shortcut registry for both axes would silently break. Recommend picking a distinct shortcut for the refresh-token grant (e.g. `"rf"`) so each two-letter code is globally unique across the three axes.
```suggestion
    public static final String GRANT_SHORTCUT = "rf";
```

:yellow_circle: [cross-file-impact] Removal of public copy-constructor `OAuth2GrantType.Context(Context)` is a breaking SPI change in `OAuth2GrantType.java:99` (confidence: 88)
The previous `Context(Context context)` copy-constructor is deleted outright. Although `OAuth2GrantType` lives under `server-spi-private`, it is still a public class with a public nested constructor — downstream forks, extensions, and out-of-tree grant providers that extend `OAuth2GrantTypeBase` can legitimately invoke it (e.g. to clone a context and mutate form params). Removing it without a deprecation cycle will surface as a compile break for those consumers. If no internal caller needs it, preserve the constructor and delegate to the new one to keep wire-compat:
```suggestion
        public Context(Context context) {
            this(context.session, context.clientConfig, context.clientAuthAttributes,
                    context.formParams, context.event, context.cors, context.tokenManager);
            this.realm = context.realm;
            this.client = context.client;
            this.clientConnection = context.clientConnection;
            this.request = context.request;
            this.response = context.response;
            this.headers = context.headers;
        }
```

:yellow_circle: [correctness] Runtime cache refresh in `DefaultTokenContextEncoderProviderFactory` can silently overwrite duplicate shortcuts, bypassing the one-time `postInit` uniqueness check in `DefaultTokenContextEncoderProviderFactory.java:98` (confidence: 85)
`postInit` validates that `grantsByShortcuts.size() == grantsToShortcuts.size()` so duplicate shortcuts fail fast at startup. But `getShortcutByGrantType` and `getGrantTypeByShortcut` both lazily refresh the cache when a grant type is introduced at runtime (dynamic provider deployment) and do the two `.put(...)` calls with no uniqueness check. A third-party provider registered after startup with a shortcut that collides with an existing grant will silently clobber one side of the bidirectional mapping — decoding will then map one shortcut to the *wrong* grant type, which is a correctness issue in audit/event logs and a latent security-telemetry confusion vector. Before putting, verify the shortcut is not already mapped to a different grant type and throw `IllegalStateException` to match the startup contract.
```suggestion
    protected String getShortcutByGrantType(String grantType) {
        String grantShortcut = grantsToShortcuts.get(grantType);
        if (grantShortcut == null) {
            OAuth2GrantTypeFactory factory = (OAuth2GrantTypeFactory) sessionFactory.getProviderFactory(OAuth2GrantType.class, grantType);
            if (factory != null) {
                String shortcut = factory.getShortcut();
                String existing = grantsByShortcuts.putIfAbsent(shortcut, grantType);
                if (existing != null && !existing.equals(grantType)) {
                    throw new IllegalStateException("Duplicate grant shortcut '" + shortcut + "' for grant types '" + existing + "' and '" + grantType + "'");
                }
                grantsToShortcuts.putIfAbsent(grantType, shortcut);
            }
            grantShortcut = grantsToShortcuts.get(grantType);
        }
        return grantShortcut;
    }
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 28 files, SPI-level change touching every OAuth2 grant factory + core TokenManager token-ID path (every access token issued after this change carries the new encoding) | Sensitive Paths: `protocol/oidc/*`, `protocol/oidc/encode/*` (new SPI), `TokenManager.java`, every grant factory, test assertion helpers
AI-Authored Likelihood: LOW (consistent Keycloak style, hand-written license headers, coherent design doc in PR body, author-attributed comments)

(0 additional findings below confidence threshold)
