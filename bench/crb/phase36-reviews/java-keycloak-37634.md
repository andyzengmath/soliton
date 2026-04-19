## Summary
28 files changed, 762 lines added, 26 lines deleted. 6 findings (2 critical, 4 improvements).
PR introduces a new `TokenContextEncoder` SPI that encodes session type, token type, and grant type into a 6-char prefix on access token IDs (e.g. `onrtac:<uuid>`). Core encoding/decoding logic and SPI wiring look correct, but there is a silent null-validation bug in `AccessTokenContext` and the new test matcher `AssertEvents.isAccessTokenId` is double-broken (wrong substring indices + inverted return), so token-ID assertions in the test suite never actually check the grant shortcut.

## Critical

:red_circle: [correctness] `rawTokenId` is never null-checked in `AccessTokenContext` constructor in services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java:65 (confidence: 99)
Lines 64–65 call `Objects.requireNonNull(grantType, ...)` twice — the second call's message says `"Null rawTokenId not allowed"` but still validates `grantType`, so a null `rawTokenId` is silently accepted and later produces a token id like `"onrtac:null"`. This leaks past the guard and corrupts every access-token ID path that relies on this class.
```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

:red_circle: [testing] `AssertEvents.isAccessTokenId` matcher never enforces the grant shortcut in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:1118 (confidence: 98)
Two independent bugs compound: (1) `items[0].substring(3, 5)` reads positions 3–4, but the grant shortcut lives at positions 4–5 of the 6-char context prefix, and (2) the conditional `if (...equals(expectedGrantShortcut)) return false` is inverted — it fails the match when the shortcut is correct and accepts it otherwise. Net effect: every `expectCodeToToken` / `expectRefresh` / `expectDeviceCodeToToken` / `expectAuthReqIdToToken` assertion passes as long as the raw id is a UUID, silently validating nothing about grant typing.
```suggestion
    public static Matcher<String> isAccessTokenId(String expectedGrantShortcut) {
        return new TypeSafeMatcher<String>() {
            @Override
            protected boolean matchesSafely(String item) {
                String[] items = item.split(":");
                if (items.length != 2) return false;
                if (items[0].length() != 6) return false;
                // Grant shortcut is last 2 chars of the 6-char context prefix
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
<details><summary>More context</summary>

The PR's stated motivation (see description: "Grant type knowledge can be useful for example for the token-exchange use-case") relies on this being verifiable end-to-end. With the matcher broken, the integration tests no longer regression-guard the grant-shortcut wiring — a future refactor that accidentally swaps shortcut positions or drops the prefix entirely will still see the test suite go green. This is the kind of defect that tends to ship with the PR and decay silently for several releases.

Reproducible check: run `DefaultTokenContextEncoderProviderTest` style round-trip on `"onrtac:<uuid>"` — the canonical format puts `ac` at indices 4–5, matching the implementation's encoding order `sessionType.shortcut + tokenType.shortcut + grantShort + ':' + rawTokenId` in `DefaultTokenContextEncoderProvider.encodeTokenId`. The matcher's `substring(3, 5)` would read `"ta"` from `"onrtac"`, which will never equal any valid grant shortcut — hence the inverted-equals check accidentally lets every token through.
</details>

## Improvements

:yellow_circle: [correctness] Backward-compat regression for legacy/custom token IDs that contain `:` in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProvider.java:68 (confidence: 86)
`getTokenContextFromTokenId` splits on the first `:` and then rejects anything whose prefix is not exactly 6 characters. A pre-existing access-token ID from an older Keycloak version (or a customized non-UUID id) that happens to contain a colon will now throw `IllegalArgumentException` instead of being treated as a legacy UNKNOWN token, breaking introspection / userinfo flows on rolling upgrades.
```suggestion
            if (encodedContext.length() != 6) {
                // Not our encoded format — treat as legacy/opaque token id for back-compat
                return new AccessTokenContext(AccessTokenContext.SessionType.UNKNOWN,
                        AccessTokenContext.TokenType.UNKNOWN, UNKNOWN, encodedTokenId);
            }
```

:yellow_circle: [consistency] Inconsistent exception type between encode and decode paths in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProvider.java:87 (confidence: 75)
`getTokenContextFromTokenId` throws `IllegalArgumentException` for malformed input, but `encodeTokenId` throws `IllegalStateException` for the symmetric condition (unknown session/token/grant type). Callers catching one but not the other will see asymmetric failure handling across the same provider.
```suggestion
        if (tokenContext.getSessionType() == AccessTokenContext.SessionType.UNKNOWN) {
            throw new IllegalArgumentException("Cannot encode token with unknown sessionType");
        }
        if (tokenContext.getTokenType() == AccessTokenContext.TokenType.UNKNOWN) {
            throw new IllegalArgumentException("Cannot encode token with unknown tokenType");
        }
```

:yellow_circle: [correctness] Grant-shortcut collision detection runs after corruption in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java:54 (confidence: 78)
If two `OAuth2GrantTypeFactory` implementations return the same 2-letter shortcut, the second `put` into `grantsByShortcuts` silently overwrites the first, leaving the maps in an inconsistent state before `postInit` finally detects the size mismatch and throws. Additionally, there is no validation that `getShortcut()` returns a non-null, 2-character string — a plugin returning `null` or `"xxx"` will produce malformed token ids with no up-front failure.
```suggestion
        factory.getProviderFactoriesStream(OAuth2GrantType.class)
                .forEach((factory1) -> {
                    OAuth2GrantTypeFactory gtf = (OAuth2GrantTypeFactory) factory1;
                    String grantName = gtf.getId();
                    String grantShortcut = gtf.getShortcut();
                    if (grantShortcut == null || grantShortcut.length() != 2) {
                        throw new IllegalStateException("Grant factory " + gtf.getClass().getName()
                                + " returned invalid shortcut: '" + grantShortcut + "'. Must be exactly 2 characters.");
                    }
                    if (grantsByShortcuts.containsKey(grantShortcut)) {
                        throw new IllegalStateException("Duplicate grant shortcut '" + grantShortcut
                                + "' for grant '" + grantName + "' (already used by '"
                                + grantsByShortcuts.get(grantShortcut) + "')");
                    }
                    grantsByShortcuts.put(grantShortcut, grantName);
                    grantsToShortcuts.put(grantName, grantShortcut);
                });
```

:yellow_circle: [correctness] `RefreshTokenGrantTypeFactory.GRANT_SHORTCUT = "rt"` collides with `TokenType.REGULAR = "rt"` in services/src/main/java/org/keycloak/protocol/oidc/grants/RefreshTokenGrantTypeFactory.java:33 (confidence: 70)
Because shortcuts are positional in the 6-char prefix, positional decoding still works, but a refresh-token-initiated regular access token encodes as `onrtrt:<uuid>` — four identical characters in a row, which is fragile for log scraping, human debugging, and any future attempt to widen the encoding scheme (e.g. delimiter-based). Consider a distinct shortcut such as `rf` to avoid the visual collision with the regular-token marker.
```suggestion
    public static final String GRANT_SHORTCUT = "rf";
```

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: 28 files across core token-issuance paths (`TokenManager`, every `OAuth2GrantType` factory, new SPI, integration-test matcher) | Sensitive Paths: `protocol/oidc/encode/*` (token ID generation), `TokenManager.initToken` (access-token creation), `AssertEvents` (entire OIDC integration test harness depends on it)
AI-Authored Likelihood: LOW
