## Summary
28 files changed, 722 lines added, 26 lines deleted. 5 findings (4 critical, 1 improvement).
Two copy-paste bugs land in core token-ID code (`AccessTokenContext` constructor and the new `isAccessTokenId` test matcher), and a new abstract method on a server-spi-private interface will break any third-party `OAuth2GrantTypeFactory` impl at startup.

## Critical
:red_circle: [correctness] Copy-paste bug: rawTokenId null-check silently checks grantType in `services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java`:207 (confidence: 99)
The `AccessTokenContext` constructor has two consecutive `Objects.requireNonNull` calls but both validate `grantType`. The second call's message is `"Null rawTokenId not allowed"` but the expression evaluated is still `grantType`, so `rawTokenId` is never validated. Constructing `new AccessTokenContext(st, tt, gt, null)` silently stores a null `rawTokenId`; the failure surfaces later inside `encodeTokenId` where `':' + tokenContext.getRawTokenId()` produces the literal string `"null"` (Java string concatenation does not throw on null), yielding a malformed token id like `onrtac:null`. That collides deterministically with any other null-rawTokenId token of the same context, breaking jti uniqueness — directly weakening replay protection / introspection-by-jti and audit dedup keyed on token id. All four fields are `final`, so this constructor is the only line of defense.
```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

:red_circle: [correctness] `isAccessTokenId` matcher is doubly broken — wrong substring offset and inverted return condition in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java`:1125 (confidence: 99)
The encoded prefix is six characters in three two-char pairs: `[0-1]` session, `[2-3]` token, `[4-5]` grant. The matcher uses `items[0].substring(3, 5)`, which slices across the tokenType/grantType boundary (e.g. for `onrtac:UUID` it returns `"ta"`, not `"ac"`). And the return condition is inverted: `if (items[0].substring(3, 5).equals(expectedGrantShortcut)) return false;` returns `false` (no match) precisely when the shortcut equals the expected value, then falls through to `return isUUID().matches(items[1])` for any other prefix. Net effect: every assertion in `expectCodeToToken`, `expectRefresh`, `expectAuthReqIdToToken`, and `expectDeviceCodeToToken` silently passes for the *wrong* grant shortcut and would only fail when the encoder is correct. The integration test suite provides zero coverage of the new grant-type encoding it was supposed to validate, and there is no unit test for the matcher itself to catch this.
```suggestion
            @Override
            protected boolean matchesSafely(String item) {
                String[] items = item.split(":", 2);
                if (items.length != 2) return false;
                if (items[0].length() != 6) return false;
                // Grant type shortcut occupies chars [4,5] (0-indexed)
                if (!items[0].substring(4, 6).equals(expectedGrantShortcut)) return false;
                return isUUID().matches(items[1]);
            }
```

:red_circle: [cross-file-impact] New abstract `getShortcut()` on `OAuth2GrantTypeFactory` will break third-party grant impls at startup in `server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeFactory.java`:29 (confidence: 95)
`OAuth2GrantTypeFactory` is a server-spi-private interface implemented by every grant factory. The PR adds `String getShortcut()` as a non-default abstract method. `DefaultTokenContextEncoderProviderFactory.postInit()` then iterates *every* registered `OAuth2GrantType` provider factory, casts unconditionally to `OAuth2GrantTypeFactory`, and calls `getShortcut()`. Any out-of-tree implementation compiled against the previous interface (custom grant types, SAML/social bridges, downstream fork extensions) will trigger `AbstractMethodError` during boot. Even if the impl somehow loads, `encodeTokenId` later throws `IllegalStateException("Cannot encode token with unknown grantType")` for any grant whose shortcut is null, so token issuance is broken for that grant. The 9 first-party impls are all updated, but downstream consumers get no warning and no migration path.
```suggestion
    /**
     * @return usually a 2-3 letter shortcut for the grant. Returning {@code null} keeps
     * the legacy unencoded behaviour for this grant. Shortcut should be unique across grants.
     */
    default String getShortcut() {
        return null;
    }
```
And in `DefaultTokenContextEncoderProviderFactory.postInit()`, skip factories whose `getShortcut()` returns null, and have `encodeTokenId` fall back to the `UNKNOWN` ("na") shortcut for grants with no registered shortcut instead of throwing.

:red_circle: [correctness] `Constants.GRANT_TYPE` may be unset on the `clientSessionCtx` for the authorization-code flow, which would make every `ac` token encode as `onrtna:` and break the (already-buggy) `expectCodeToToken` assertion in `services/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeBase.java`:107 (confidence: 86)
The PR sets `clientSessionCtx.setAttribute(Constants.GRANT_TYPE, …)` in only four places: `OAuth2GrantTypeBase.createTokenResponse()`, `PreAuthorizedCodeGrantType.process()`, `ResourceOwnerPasswordCredentialsGrantType.process()`, and `StandardTokenExchangeProvider.exchangeClientToOIDCClient()`. `TokenManager.validateToken()` (refresh path) sets it to `OAuth2Constants.REFRESH_TOKEN` directly. Every other flow that reaches `TokenManager.initToken()` must therefore route through one of those setters, otherwise `getTokenContextFromClientSessionContext()` falls through to `grantType = "na"` (UNKNOWN) and the resulting jti is `onrtna:UUID` instead of the expected `onrtac:UUID` — a regression in the audit-trail correctness this PR aims to add. The buggy `isAccessTokenId` matcher (above finding) hides this regression in CI: tests pass even when the grant shortcut is wrong, so this defect would only surface in production.
```suggestion
// Set the GRANT_TYPE attribute centrally in TokenManager.initToken (or in
// createClientAccessToken) using a sensible default derived from the
// AuthorizationManager's client-session note, so the encoder never falls back
// to "na" silently. After fixing isAccessTokenId, add an integration test
// asserting the real authorization-code flow produces a token id that starts
// with "onrtac:".
```

## Improvements
:yellow_circle: [correctness] Non-atomic cross-map update in `getShortcutByGrantType` / `getGrantTypeByShortcut` lazy refresh in `services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java`:471 (confidence: 85)
Both lazy-populate methods perform a read-then-double-write pattern: read map A; on miss, look up the factory and `put` into both `grantsByShortcuts` and `grantsToShortcuts`. The two puts target distinct `ConcurrentHashMap` instances and are not wrapped in any synchronization, so a concurrent thread reading the second map between the two puts sees an inconsistent half-populated state. The `postInit` size-equality guard fires only at startup and does not protect this path. Hot-deployed grant-type plugins are the realistic trigger.
```suggestion
    protected synchronized String getShortcutByGrantType(String grantType) {
        String grantShortcut = grantsToShortcuts.get(grantType);
        if (grantShortcut == null) {
            OAuth2GrantTypeFactory factory = (OAuth2GrantTypeFactory) sessionFactory.getProviderFactory(OAuth2GrantType.class, grantType);
            if (factory != null) {
                String shortcut = factory.getShortcut();
                if (shortcut != null) {
                    grantsByShortcuts.put(shortcut, grantType);
                    grantsToShortcuts.put(grantType, shortcut);
                }
            }
            grantShortcut = grantsToShortcuts.get(grantType);
        }
        return grantShortcut;
    }
```
Apply the same `synchronized` discipline to `getGrantTypeByShortcut`.

## Risk Metadata
Risk Score: 71/100 (HIGH) | Blast Radius: TokenManager + 9 grant factories + AbstractOIDCProtocolMapper visibility change (capped at 100) | Sensitive Paths: TokenManager.java, AccessTokenContext.java, DefaultTokenContextEncoderProvider*, TokenContextEncoderSpi.java (all match `*token*`)
AI-Authored Likelihood: LOW

(8 additional findings below confidence threshold)
