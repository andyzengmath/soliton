## Summary
28 files changed, 722 lines added, 26 lines deleted. 10 findings (6 critical, 4 improvements).
PR adds a 6-char `<sessionType><tokenType><grantType>:` prefix to access-token IDs via a new `TokenContextEncoderProvider` SPI. Feature is cleanly factored, but two copy-paste defects (broken `rawTokenId` guard and broken `isAccessTokenId` matcher — off-by-one AND inverted boolean) ship as bugs, and three backwards-compatibility breaks (new required SPI method, removed `Context` copy-constructor, hard-fail on unknown grant in `encodeTokenId`) land without a deprecation cycle.

## Critical

:red_circle: [correctness] Constructor validates `grantType` twice instead of `rawTokenId` in services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java:207 (confidence: 99)
The fourth `Objects.requireNonNull` passes `grantType` as the reference but uses the message "Null rawTokenId not allowed". `rawTokenId` is never validated. A null `rawTokenId` propagates into `encodeTokenId()`, where Java string concatenation yields the literal token ID `"<prefix>:null"` rather than failing fast. `AccessTokenContext` is constructed on every token issuance via `TokenManager.initToken`, so the advertised fail-fast contract is silently broken and no test in the PR exercises the path.
```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

:red_circle: [testing] `isAccessTokenId` matcher uses wrong substring offset — reads across the tokenType/grantType boundary in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:1125 (confidence: 99)
The encoded prefix is exactly 6 characters with layout `<sessionType:0-1><tokenType:2-3><grantType:4-5>`, so the grant shortcut must be extracted via `substring(4, 6)`. The matcher uses `substring(3, 5)`, which returns char 3 (last of tokenType) and char 4 (first of grantType) — a mixed 2-char string that can never equal any defined grant shortcut. For the round-trip fixture `"trltcc:1234"`, `substring(3, 5)` yields `"tc"` instead of the expected `"cc"`. The inline comment "starts at 4th char" conflates 1-indexed and 0-indexed positions.
```suggestion
                if (!items[0].substring(4, 6).equals(expectedGrantShortcut)) return false;
```

:red_circle: [testing] `isAccessTokenId` matcher has inverted boolean — accepts wrong grant shortcuts and rejects correct ones in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:1125 (confidence: 99)
`if (items[0].substring(3, 5).equals(expectedGrantShortcut)) return false;` returns `false` (no match) exactly when the grant shortcut matches the expected value, and falls through to the UUID check only when it does not match. All four call sites — `expectCodeToToken`, `expectDeviceCodeToToken`, `expectRefresh`, `expectAuthReqIdToToken` — therefore certify the opposite of what is intended: integration tests pass when the wrong grant shortcut is encoded and fail when the correct one is, providing zero protection against regressions in token-ID encoding. The fix needs to negate the condition and correct the indices together.
```suggestion
                if (!items[0].substring(4, 6).equals(expectedGrantShortcut)) return false;
```

:red_circle: [cross-file-impact] `OAuth2GrantTypeFactory.getShortcut()` added as non-default abstract method — all out-of-tree grant implementations break in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeFactory.java:80 (confidence: 97)
`OAuth2GrantTypeFactory` lives in `server-spi-private`, which third-party extensions compile against. Adding `String getShortcut();` without a `default` breaks every extension that provides a custom grant type: compile-time error on recompile, `AbstractMethodError` at server startup for pre-compiled jars (because `DefaultTokenContextEncoderProviderFactory.postInit()` invokes `gtf.getShortcut()` on every registered factory). This is a breaking SPI change with no deprecation window.
```suggestion
    /**
     * @return a 2-letter shortcut unique across grants. Used when encoding grant context
     * into access-token IDs where size is limited. Default returns the {@code UNKNOWN}
     * sentinel; custom grants should override to preserve grant identity in token IDs.
     */
    default String getShortcut() {
        return "na";
    }
```

:red_circle: [cross-file-impact] `OAuth2GrantType.Context` copy-constructor removed with no replacement in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantType.java:99 (confidence: 90)
The `public Context(Context context)` copy-constructor was part of the `server-spi-private` public API and supported grant types that fork or adapt a nested grant with a modified context. Its removal is source-breaking (compile error) and binary-breaking (`NoSuchMethodError`) for any third-party grant that used `new Context(other)`. No `copy()` or `withFormParams()` builder is introduced. If removal is deliberate (to ensure `grantType` is always captured from `formParams` at construction), add a replacement that carries `grantType` forward from the source and deprecate-for-removal.
```suggestion
        @Deprecated(forRemoval = true, since = "26")
        public Context(Context context) {
            this(context.session, context.clientConfig, context.clientAuthAttributes,
                 context.formParams, context.event, context.cors, context.tokenManager);
            this.grantType = context.grantType;
        }
```

:red_circle: [correctness] Duplicate-shortcut guard in `postInit` silently misses collisions in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java:447 (confidence: 92)
`if (grantsByShortcuts.size() != grantsToShortcuts.size())` assumes that distinct grants with colliding shortcuts will produce maps of differing sizes, but that does not hold: if grants A and B both return shortcut `"cc"`, the second `put` on `grantsByShortcuts` (shortcut→grant) silently overwrites A, while `grantsToShortcuts` (grant→shortcut) gains both entries — the maps end up with `(n-1)+1 = n` entries each and the size-equality invariant holds. The collision is undetected and tokens issued under grant A will decode as grant B permanently. The correct check uses `putIfAbsent` and inspects the return value. This also interacts with a NPE vector: a third-party factory returning `null` from `getShortcut()` will crash `postInit` on `ConcurrentHashMap.put(null, …)` without diagnostic.
```suggestion
    @Override
    public void postInit(KeycloakSessionFactory factory) {
        this.sessionFactory = factory;
        grantsByShortcuts = new ConcurrentHashMap<>();
        grantsToShortcuts = new ConcurrentHashMap<>();

        factory.getProviderFactoriesStream(OAuth2GrantType.class)
                .forEach((factory1) -> {
                    OAuth2GrantTypeFactory gtf = (OAuth2GrantTypeFactory) factory1;
                    String grantName = gtf.getId();
                    String grantShortcut = gtf.getShortcut();
                    if (grantShortcut == null) {
                        throw new IllegalStateException("OAuth2GrantTypeFactory '" + grantName
                                + "' returned null from getShortcut(); shortcuts must be non-null");
                    }
                    String prev = grantsByShortcuts.putIfAbsent(grantShortcut, grantName);
                    if (prev != null && !prev.equals(grantName)) {
                        throw new IllegalStateException("Shortcut collision: '" + grantShortcut
                                + "' is shared by grants '" + prev + "' and '" + grantName + "'");
                    }
                    grantsToShortcuts.put(grantName, grantShortcut);
                });
        grantsByShortcuts.put(DefaultTokenContextEncoderProvider.UNKNOWN, DefaultTokenContextEncoderProvider.UNKNOWN);
        grantsToShortcuts.put(DefaultTokenContextEncoderProvider.UNKNOWN, DefaultTokenContextEncoderProvider.UNKNOWN);
    }
```

## Improvements

:yellow_circle: [correctness] `validateToken` unconditionally stamps `GRANT_TYPE = refresh_token` on every refreshed session in services/src/main/java/org/keycloak/protocol/oidc/TokenManager.java:248 (confidence: 88)
`clientSessionCtx.setAttribute(Constants.GRANT_TYPE, OAuth2Constants.REFRESH_TOKEN)` runs unconditionally in `validateToken`, overwriting whatever grant type established the session. The encoder then stamps `rt` as the grant shortcut in the refreshed access-token ID regardless of the original grant (authorization_code, password, token-exchange, …). The stated benefit of encoding grant type — that later endpoints can optimize based on the grant that minted the session — is defeated for every refreshed token, which is the common case. Guard the assignment with a set-if-absent check so the original grant survives refresh.
```suggestion
        if (clientSessionCtx.getAttribute(Constants.GRANT_TYPE, String.class) == null) {
            clientSessionCtx.setAttribute(Constants.GRANT_TYPE, OAuth2Constants.REFRESH_TOKEN);
        }
```

:yellow_circle: [testing] `getTokenContextFromClientSessionContext` has no unit-test coverage in services/src/test/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderTest.java:0 (confidence: 92)
This method holds the production logic for deriving `SessionType` (TRANSIENT vs OFFLINE vs ONLINE), `TokenType` (lightweight via `AbstractOIDCProtocolMapper.getShouldUseLightweightToken`), and the `Constants.GRANT_TYPE` fallback to `UNKNOWN`. All five existing unit tests exercise only `getTokenContextFromTokenId` (decode) and the encode round-trip. A regression in any of the three branch decisions — including the ordering of `TRANSIENT` vs `isOffline()` — has no guard.
```suggestion
    @Test
    public void testGetTokenContextOfflineLightweight() {
        KeycloakSession session = mockSessionWithLightweight(true);
        ClientSessionContext ctx = mockCtx(SessionPersistenceState.PERSISTENT, /*offline=*/true,
                                           OAuth2Constants.CLIENT_CREDENTIALS);
        AccessTokenContext result = new DefaultTokenContextEncoderProvider(session, factory)
                .getTokenContextFromClientSessionContext(ctx, "raw-1");
        Assert.assertEquals(AccessTokenContext.SessionType.OFFLINE, result.getSessionType());
        Assert.assertEquals(AccessTokenContext.TokenType.LIGHTWEIGHT, result.getTokenType());
        Assert.assertEquals(OAuth2Constants.CLIENT_CREDENTIALS, result.getGrantType());
    }

    @Test
    public void testGetTokenContextTransientWithNullGrantFallsBackToUnknown() {
        ClientSessionContext ctx = mockCtx(SessionPersistenceState.TRANSIENT, /*offline=*/false,
                                           /*grantType=*/null);
        AccessTokenContext result = new DefaultTokenContextEncoderProvider(mockSessionWithLightweight(false), factory)
                .getTokenContextFromClientSessionContext(ctx, "raw-2");
        Assert.assertEquals(AccessTokenContext.SessionType.TRANSIENT, result.getSessionType());
        Assert.assertEquals(DefaultTokenContextEncoderProvider.UNKNOWN, result.getGrantType());
    }
```

:yellow_circle: [testing] `testIncorrectGrantType` catches the wrong exception type and has an unused variable in services/src/test/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderTest.java:1027 (confidence: 90)
The test catches `RuntimeException` (supertype) rather than the specific `IllegalArgumentException` the production code throws. A future regression causing `NullPointerException`, `ArrayIndexOutOfBoundsException`, or any other unchecked exception to escape `getTokenContextFromTokenId` will silently pass, defeating the safety net. Additionally, the variable `ctx` is assigned from a call that is expected to throw, so the assignment is unreachable and generates an unused-variable warning. Tighten the catch and drop the assignment.
```suggestion
    @Test
    public void testIncorrectGrantType() {
        String tokenId = "ofrtac:5678";
        try {
            provider.getTokenContextFromTokenId(tokenId);
            Assert.fail("Expected IllegalArgumentException for unregistered grant shortcut 'ac'");
        } catch (IllegalArgumentException expected) {
            Assert.assertTrue(expected.getMessage().contains("ac"));
        }
    }
```

:yellow_circle: [cross-file-impact] `encodeTokenId` throws for any grant unseen at `postInit` — diverges from the decode path's UNKNOWN fallback in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProvider.java:337 (confidence: 85)
`encodeTokenId` is on the hot path of every access-token issuance. When `factory.getShortcutByGrantType(grantType)` returns null, it throws `IllegalStateException("Cannot encode token with unknown grantType: ...")`. In contrast, `getTokenContextFromClientSessionContext` silently falls back to `UNKNOWN` when the grant attribute is missing, and `getTokenContextFromTokenId` (mostly) treats unknown input as `UNKNOWN`. The encode/decode paths therefore disagree on how to handle unknown grants: a third-party grant factory compiled against the pre-PR interface (no `getShortcut()`) causes every access-token issuance for that grant to fail with an uncaught 500. Falling back to the `UNKNOWN` shortcut keeps the server up while still flagging the unrecognized grant in logs.
```suggestion
        String grantShort = factory.getShortcutByGrantType(tokenContext.getGrantType());
        if (grantShort == null) {
            // Stay symmetric with getTokenContextFromClientSessionContext, which falls back
            // to UNKNOWN when grant is missing. Hard-failing here turns every token issuance
            // for an unrecognized (e.g. out-of-tree) grant into a 500.
            grantShort = DefaultTokenContextEncoderProvider.UNKNOWN;
        }
```

## Risk Metadata
Risk Score: 66/100 (HIGH) | Blast Radius: `TokenManager.initToken` is on every OIDC access-token issuance; `OAuth2GrantTypeFactory` is implemented by 8 in-tree factories plus arbitrary third-party extensions; `AssertEvents` matcher is referenced by hundreds of integration tests | Sensitive Paths: all files under `services/protocol/oidc/encode/**`, `services/protocol/oidc/grants/**`, and `TokenManager.java` match the `*token*` / `auth/` sensitive-path patterns
AI-Authored Likelihood: LOW

(Below-threshold findings suppressed: log injection via unsanitized `encodedTokenId` echoed in `IllegalArgumentException` messages [78]; jti metadata disclosure — grant type and session type now visible in every token ID [65]; `getShouldUseLightweightToken` visibility change from package-private instance to `public static` can silently bypass in-package overrides [85]; lazy cache-refresh in `getShortcutByGrantType` / `getGrantTypeByShortcut` has a non-atomic two-map update window [82]; inconsistent `GRANT_SHORTCUT` constant-vs-literal pattern across grant factories [82]; Javadoc on `getShortcut()` says "3-letters" but all implementations are 2 letters [75])
