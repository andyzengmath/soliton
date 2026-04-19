## Summary
28 files changed, 722 lines added, 26 lines deleted. 9 findings (6 critical, 3 improvements).
Introduces a new `TokenContextEncoderProvider` SPI that encodes session/token/grant context into the access-token `jti` as a 6-char prefix. Ships with a real null-check defect in `AccessTokenContext`, a double-defect `AssertEvents.isAccessTokenId` matcher (inverted guard + wrong substring window) that silently no-ops the integration-test coverage, an unannounced abstract-method addition to the `OAuth2GrantTypeFactory` SPI that breaks every third-party grant extension, removal of a public copy constructor on `OAuth2GrantType.Context`, and an NPE introduced into the pre-existing `Context` constructor.

## Critical

:red_circle: [correctness] `AccessTokenContext` never null-checks `rawTokenId` — second guard re-checks `grantType` in services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java:207 (confidence: 99)
The constructor calls `Objects.requireNonNull(grantType, ...)` twice; the second invocation was clearly intended to validate `rawTokenId` (its message even says "Null rawTokenId not allowed"), but the argument is still `grantType`. A null `rawTokenId` is therefore silently accepted, and `DefaultTokenContextEncoderProvider.encodeTokenId` will later emit a token ID literally ending in `":null"` and `getTokenContextFromTokenId` round-tripping will produce a garbage raw ID instead of failing fast as the contract implies.
```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

:red_circle: [testing] `AssertEvents.isAccessTokenId` matcher inverts the match condition in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:1125 (confidence: 99)
`if (items[0].substring(3, 5).equals(expectedGrantShortcut)) return false;` returns `false` when the shortcut **matches** the expectation — the exact inverse of the intended guard. Every event assertion that was migrated to `isAccessTokenId(...)` in this PR (`expectCodeToToken`, `expectDeviceCodeToToken`, `expectRefresh`, `expectAuthReqIdToToken`) therefore silently accepts wrong grant shortcuts and rejects correct ones — erasing the integration-test coverage of the new encoding. Fix the polarity and (per the next finding) the window.
```suggestion
                if (!items[0].substring(4, 6).equals(expectedGrantShortcut)) return false;
```

:red_circle: [correctness] `AssertEvents.isAccessTokenId` reads the wrong substring window — grant shortcut lives at 4-5, not 3-4 in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:1125 (confidence: 97)
The encoder concatenates `sessionType(2) + tokenType(2) + grantType(2)` per `DefaultTokenContextEncoderProvider.encodeTokenId`. That puts `grantType` at 0-indexed positions `[4, 6)`, not `[3, 5)`. For a prefix like `onrtac`, `substring(3, 5)` extracts `"ta"` instead of `"ac"`. Even after correcting the inversion in the previous finding, the window is still wrong. The inline comment "starts at character 4th char and is 2-chars long" is also ambiguous and contradicts the code.
```suggestion
                String[] items = item.split(":");
                if (items.length != 2) return false;
                // Grant type shortcut occupies 0-indexed positions 4-5 of the 6-char context prefix
                if (!items[0].substring(4, 6).equals(expectedGrantShortcut)) return false;
                return isUUID().matches(items[1]);
```

:red_circle: [cross-file-impact] Adding non-default abstract `getShortcut()` to `OAuth2GrantTypeFactory` is a hard SPI break in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeFactory.java:29 (confidence: 95)
`OAuth2GrantTypeFactory` is a documented, externally extended SPI. Declaring `String getShortcut();` without a default implementation means (a) every third-party grant-type factory fails to recompile against the new interface, and (b) classes compiled against the old interface throw `AbstractMethodError` at runtime when `DefaultTokenContextEncoderProviderFactory.postInit` iterates providers. There is no deprecation cycle, no default method, no migration note. Ship a safe default derived from `getId()` and allow overrides.
```suggestion
    /**
     * @return a 2-character shortcut uniquely identifying the grant type. The encoder
     * reserves exactly 2 characters for the grant field in the access-token ID prefix;
     * the default derives from getId() and SHOULD be overridden with a stable,
     * collision-free shortcut. Shortcut must be unique across all registered grants.
     */
    default String getShortcut() {
        String id = getId();
        return id != null && id.length() >= 2 ? id.substring(0, 2).toLowerCase() : "un";
    }
```

:red_circle: [cross-file-impact] Unannounced removal of public `OAuth2GrantType.Context(Context)` copy constructor in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantType.java:100 (confidence: 88)
`public Context(Context context)` on the public `OAuth2GrantType.Context` class is deleted outright. Any downstream or third-party grant implementation that used `new Context(existing)` to clone or adapt a context (a pattern present in Keycloak quickstarts and custom grant extensions) fails to compile against the new release. Restore the constructor for one release cycle with `@Deprecated(forRemoval = true)`, or ship a `Context.copyOf(Context)` factory as a drop-in replacement.
```suggestion
        @Deprecated(forRemoval = true)
        public Context(Context context) {
            this(context.session, context.clientConfig, context.clientAuthAttributes,
                 context.formParams, context.event, context.cors, context.tokenManager);
            this.grantType = context.grantType;
        }
```

:red_circle: [correctness] New `Context` constructor NPEs on null `formParams` — used to be tolerated in services/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantType.java:102 (confidence: 90)
The pre-existing 7-arg constructor simply stored the `formParams` reference (the class even exposes `setFormParams(...)` for deferred population). The new line `this.grantType = formParams.getFirst(OAuth2Constants.GRANT_TYPE);` dereferences `formParams` unconditionally and throws NPE at construction when it is null. This silently tightens the constructor's precondition and breaks any caller that constructs a `Context` and populates form params afterwards. Additionally, `setFormParams` does not refresh `grantType`, so deferred-init callers that escape the NPE would still end up with a stale `null`.
```suggestion
            this.grantType = (formParams != null) ? formParams.getFirst(OAuth2Constants.GRANT_TYPE) : null;
        }

        public void setFormParams(MultivaluedHashMap<String, String> formParams) {
            this.formParams = formParams;
            this.grantType = (formParams != null) ? formParams.getFirst(OAuth2Constants.GRANT_TYPE) : null;
```

## Improvements

:yellow_circle: [correctness] `postInit` duplicate-shortcut guard is not robust under multi-collision and is bypassed by lazy refresh in services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java:447 (confidence: 85)
The size-equality guard `grantsByShortcuts.size() != grantsToShortcuts.size()` catches a single shortcut collision (forward map loses one entry via `put` overwrite, reverse map keeps both, sizes differ) but slips two symmetric collisions through (each map loses exactly one entry, sizes tie again). The lazy-refresh paths in `getShortcutByGrantType` / `getGrantTypeByShortcut` also bypass this guard entirely, so a provider deployed at runtime with a colliding shortcut silently overwrites the existing binding. Replace with a `putIfAbsent` check that raises on any real collision.
```suggestion
        factory.getProviderFactoriesStream(OAuth2GrantType.class)
                .forEach((factory1) -> {
                    OAuth2GrantTypeFactory gtf = (OAuth2GrantTypeFactory) factory1;
                    String grantName = gtf.getId();
                    String grantShortcut = gtf.getShortcut();
                    String existingGrant = grantsByShortcuts.putIfAbsent(grantShortcut, grantName);
                    if (existingGrant != null && !existingGrant.equals(grantName)) {
                        throw new IllegalStateException("Duplicate grant shortcut '" + grantShortcut +
                                "' used by '" + existingGrant + "' and '" + grantName + "'");
                    }
                    grantsToShortcuts.put(grantName, grantShortcut);
                });
```

:yellow_circle: [consistency] Javadoc on `getShortcut()` says "usually like 3-letters" but every implementation uses 2 and the parser enforces 2 in server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantTypeFactory.java:29 (confidence: 90)
All in-tree factories return 2-char values (`ac`, `rt`, `cc`, `pg`, `pc`, `ro`, `te`, `ci`, `dg`) plus sentinels (`un`, `na`). The encoder hardcodes a 6-char prefix (2+2+2), and `DefaultTokenContextEncoderProvider.getTokenContextFromTokenId` throws `IllegalArgumentException` unless `encodedContext.length() == 6`. A 3-letter shortcut from a third-party grant would therefore break parsing. The Javadoc misleads extenders and contradicts the decoder contract.
```suggestion
    /**
     * @return a 2-character shortcut uniquely identifying the grant type. The encoder
     * reserves exactly 2 characters for this field; other lengths will fail parsing in
     * DefaultTokenContextEncoderProvider. Shortcut must be unique across all grants.
     */
    String getShortcut();
```

:yellow_circle: [consistency] Grant-shortcut constants are exposed inconsistently across factories in services/src/main/java/org/keycloak/protocol/oidc/grants/ClientCredentialsGrantTypeFactory.java:38 (confidence: 85)
Four factories expose `public static final String GRANT_SHORTCUT` (`AuthorizationCodeGrantTypeFactory`, `RefreshTokenGrantTypeFactory`, `CibaGrantTypeFactory`, `DeviceGrantTypeFactory`) and the other five use a bare string literal in `getShortcut()` (`ClientCredentialsGrantTypeFactory` → `"cc"`, `PermissionGrantTypeFactory` → `"pg"`, `PreAuthorizedCodeGrantTypeFactory` → `"pc"`, `ResourceOwnerPasswordCredentialsGrantTypeFactory` → `"ro"`, `TokenExchangeGrantTypeFactory` → `"te"`). This asymmetry is exactly why `AssertEvents` can only import constants from four of the nine factories. Either expose the constant on every factory, or remove the constants on the four and have consumers go through `OAuth2GrantTypeFactory.getShortcut()` on the resolved factory instance.
```suggestion
public class ClientCredentialsGrantTypeFactory implements OAuth2GrantTypeFactory {

    public static final String GRANT_SHORTCUT = "cc";

    @Override
    public String getId() {
        return OAuth2Constants.CLIENT_CREDENTIALS;
    }

    @Override
    public String getShortcut() {
        return GRANT_SHORTCUT;
    }
```

## Risk Metadata
Risk Score: 70/100 (HIGH) | Blast Radius: `TokenManager.initToken` is on the universal access-token creation path, touching every grant flow and every relying party that reads `jti`; `OAuth2GrantTypeFactory` is implemented by all 10 in-tree grants plus an unbounded set of third-party extensions; `AbstractOIDCProtocolMapper.getShouldUseLightweightToken` visibility is widened (package-private → `public static`), enlarging the OIDC mapper API surface | Sensitive Paths: `*token*`, `*grant*`, `*auth*` matched across 20+ changed files under `services/protocol/oidc/**`
AI-Authored Likelihood: LOW

(Additional lower-confidence observations below the 85 threshold: form-param `grant_type` trust without re-validation; `UNKNOWN` fail-open behavior for legacy tokens in `encodeTokenId` throws but decode does not — cross-version mix may surface confusingly; `jti` information-disclosure of session/token/grant metadata to resource servers; redundant double-assignment of `Constants.GRANT_TYPE` in ROPC flow where `OAuth2GrantTypeBase.createTokenResponse` will overwrite it; `catch (RuntimeException)` in `testIncorrectGrantType` too broad; missing negative tests for invalid-length prefix and `UNKNOWN`-tokenType encoding paths.)
