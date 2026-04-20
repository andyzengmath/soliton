## Summary
28 files changed, 722 lines added, 26 lines deleted. 7 findings (2 critical, 5 improvements).
Introduces a `TokenContextEncoder` SPI that packs session-type / token-type / grant-type shortcuts into the access-token `jti`; implementation is solid but contains two outright correctness bugs (one in production code, one in the shared test matcher) and several backward-compat / concurrency concerns.

## Critical

:red_circle: [correctness] Null-check validates the wrong parameter in `AccessTokenContext` constructor in services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java:207 (confidence: 99)
The fourth `Objects.requireNonNull` is supposed to guard `rawTokenId` but passes `grantType` again — so `rawTokenId` is never validated. A null `rawTokenId` will propagate silently into `encodeTokenId` and surface far from the real caller. The duplicate-argument message ("Null rawTokenId not allowed" attached to a `grantType` check) makes the intent unmistakable.
```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

:red_circle: [testing] `AssertEvents.isAccessTokenId` matcher has wrong substring indices AND inverted return in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:1124 (confidence: 99)
Two defects in the same three lines make every `expectCodeToToken` / `expectDeviceCodeToToken` / `expectRefresh` / `expectAuthReqIdToToken` assertion unreliable:

1. The token-ID layout is `sessionType(0..2) + tokenType(2..4) + grantType(4..6)`, but the matcher reads `substring(3, 5)` — straddling the tokenType/grantType boundary. For `"onrtac"`, chars 3–5 are `"ta"`, not the intended `"ac"`.
2. The conditional is inverted: `if (items[0].substring(3, 5).equals(expectedGrantShortcut)) return false;` — a *matching* shortcut causes the matcher to return `false`. Combined with (1) it means the test asserts "grant shortcut is NOT this value, at the wrong offset" and still passes by accident because (1) almost never yields the expected string.

Any regression in grant-type encoding will sail through these assertions unnoticed.
```suggestion
    public static Matcher<String> isAccessTokenId(String expectedGrantShortcut) {
        return new TypeSafeMatcher<String>() {
            @Override
            protected boolean matchesSafely(String item) {
                String[] items = item.split(":");
                if (items.length != 2) return false;
                if (items[0].length() != 6) return false;
                // Grant type shortcut occupies chars [4,6)
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

:yellow_circle: [cross-file-impact] Adding abstract `getShortcut()` to `OAuth2GrantTypeFactory` breaks every existing implementation in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeFactory.java:30 (confidence: 92)
`OAuth2GrantTypeFactory` is an SPI with documented extension points (referenced in `docs/documentation/server_development/topics/providers.adoc` and used by third-party grant plugins). Adding a non-default abstract method is a source- and binary-incompatible change — any external `OAuth2GrantTypeFactory` implementation will fail to compile against Keycloak 26.x+. Give the method a `default` implementation returning e.g. `"xx"` (or `getId().substring(0, 2)`) so extenders can opt in, and add a `postInit` validation that logs a WARN when a grant registers without overriding it. Do the same for the removed `Context(Context context)` copy constructor below if any downstream provider subclasses `Context`.
```suggestion
    /**
     * @return usually like 2-letters shortcut of specific grants. It can be useful for example in the tokens when the amount of characters should be limited and hence using full grant name
     * is not ideal. Shortcut should be unique across grants. Custom grants that do not override this will fall back to {@link DefaultTokenContextEncoderProvider#UNKNOWN}.
     */
    default String getShortcut() {
        return DefaultTokenContextEncoderProvider.UNKNOWN;
    }
```

:yellow_circle: [cross-file-impact] `OAuth2GrantType.Context` copy constructor was removed in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantType.java:102 (confidence: 78)
The public `Context(Context context)` copy constructor is deleted as part of this refactor. Even though the enclosing module is `server-spi-private`, grant providers outside the main tree (e.g. internal Red Hat distros, OAuth2 extensions) can legally extend `Context` and invoke the copy constructor. Prefer deprecating and delegating: keep the constructor, call the primary constructor with a reconstructed formParams map (or cache `grantType` from the source context), and remove it after a release cycle.

:yellow_circle: [correctness] Dynamic refresh of `grantsByShortcuts` / `grantsToShortcuts` is not atomic and can leave the invariant in `postInit` silently broken in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java:107 (confidence: 72)
`getShortcutByGrantType` and `getGrantTypeByShortcut` populate both maps with two separate `put` calls. If two threads concurrently resolve a new grant whose `getShortcut()` collides with an existing one, they can leave the maps with `grantsByShortcuts.size() > grantsToShortcuts.size()` (or vice versa), which the `postInit` size check was designed to catch — but that check only runs once at startup. Hold a lock around both puts, or use `computeIfAbsent` with a single combined key → `Map.Entry<String,String>` structure, and short-circuit if a collision is detected (log + skip rather than silently overwrite).
```suggestion
    protected synchronized String getShortcutByGrantType(String grantType) {
        String grantShortcut = grantsToShortcuts.get(grantType);
        if (grantShortcut == null) {
            OAuth2GrantTypeFactory factory = (OAuth2GrantTypeFactory) sessionFactory.getProviderFactory(OAuth2GrantType.class, grantType);
            if (factory != null) {
                String shortcut = factory.getShortcut();
                String existing = grantsByShortcuts.putIfAbsent(shortcut, grantType);
                if (existing != null && !existing.equals(grantType)) {
                    throw new IllegalStateException("Shortcut collision for grant '" + grantType + "' vs '" + existing + "' on shortcut '" + shortcut + "'");
                }
                grantsToShortcuts.put(grantType, shortcut);
            }
            grantShortcut = grantsToShortcuts.get(grantType);
        }
        return grantShortcut;
    }
```

:yellow_circle: [hallucination] Javadoc on `OAuth2GrantTypeFactory.getShortcut` says "3-letters shortcut" but every implementation uses 2 characters and `DefaultTokenContextEncoderProvider` hard-codes length 6 assuming 2+2+2 in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeFactory.java:29 (confidence: 95)
```
* @return usually like 3-letters shortcut of specific grants.
```
All concrete shortcuts in this PR are exactly 2 characters (`ac`, `cc`, `rt`, `ro`, `te`, `ci`, `dg`, `pc`, `pg`), matching the decoder's hard-coded `encodedContext.length() != 6` check (`DefaultTokenContextEncoderProvider.java:196`). A 3-char shortcut would blow the length validator at runtime. Fix the Javadoc to say "2-character shortcut" and consider defining a `GRANT_SHORTCUT_LENGTH = 2` constant referenced by both the encoder length check and the SPI contract so the two cannot drift.
```suggestion
     * @return a 2-character shortcut for this grant. The length is fixed because the decoder in
     * {@link DefaultTokenContextEncoderProvider#getTokenContextFromTokenId} assumes a 6-character
     * prefix composed of sessionType(2) + tokenType(2) + grantType(2). The shortcut must be unique
     * across all registered grants.
```

:yellow_circle: [testing] `DefaultTokenContextEncoderProviderTest.testIncorrectGrantType` swallows `RuntimeException` and declares an unused variable in services/src/test/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderTest.java:78 (confidence: 68)
The test catches `RuntimeException` (broad enough to mask bugs unrelated to the expected `IllegalArgumentException`) and assigns the result of `getTokenContextFromTokenId` to `ctx` which is never used, producing an "unused" compiler warning and suggesting the author intended a different assertion. Tighten the catch, or use `Assert.assertThrows(IllegalArgumentException.class, ...)` which is idiomatic JUnit 4.12+.
```suggestion
    @Test
    public void testIncorrectGrantType() {
        Assert.assertThrows(IllegalArgumentException.class,
                () -> provider.getTokenContextFromTokenId("ofrtac:5678"));
    }
```

:yellow_circle: [consistency] Token-ID format change is a public ecosystem contract and should be documented + introspection-endpoint verified in services/src/main/java/org/keycloak/protocol/oidc/TokenManager.java:1053 (confidence: 70)
Access-token `jti` has historically been a bare UUID; the introspection endpoint, audit logs, log-aggregators, SIEM tools, and RFC 7662-compatible consumers may treat `jti` as opaque but many operators grep/index it. Switching to `<6-char-prefix>:<uuid>` is a wire-level change worth calling out in release notes and the upgrade guide under server-spi-private, plus a regression test that the introspection response still round-trips the new `jti` unchanged. Also confirm that `session.getProvider(TokenContextEncoderProvider.class)` returns a non-null provider in every code path that runs `initToken` — including test-suite profiles that disable the default SPI — otherwise `initToken` will NPE. A `Objects.requireNonNull(encoder, ...)` with a clear error message here is cheap insurance.

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: wide — touches access-token ID format used by every OIDC flow, introspection endpoint, audit logs, and downstream introspection consumers; adds a new SPI that must resolve at startup; removes a public copy constructor and adds an abstract SPI method | Sensitive Paths: `services/src/main/java/org/keycloak/protocol/oidc/**` (auth / token issuance), `server-spi-private/**` (SPI contract), `META-INF/services/**` (provider registration)
AI-Authored Likelihood: LOW

---

**Review metadata**
- Mode: local synthesis (no upstream agent dispatch) for CRB benchmark evaluation
- PR: keycloak/keycloak#37634 — "Encoding context to access token IDs"
- Base: main — Head: 37118-access-token-ids-rebase
- Files: 28 (722 additions, 26 deletions, ~1,138 diff lines)
- Output: local markdown only; no comments posted to GitHub per invocation instruction.
