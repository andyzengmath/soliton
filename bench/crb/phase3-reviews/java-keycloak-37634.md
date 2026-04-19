# PR Review — keycloak/keycloak #37634

**Title:** Encoding context to access token IDs
**Base:** `main` ← **Head:** `37118-access-token-ids-rebase`
**Author intent:** Encode session-type / token-type / grant-type shortcuts into access-token IDs (`onrtac:<uuid>`) so downstream consumers (introspection, userInfo, admin REST) can recognize token context without custom claims. Introduces a new `TokenContextEncoderSpi`.

## Summary
28 files changed, 722 lines added, 22 lines deleted. 7 findings (2 critical, 3 improvements, 2 nitpicks).
Well-scoped SPI addition with clear intent, but contains two clear copy-paste bugs — one in a production constructor's null check, one in a test matcher whose logic is inverted — and a backward-compatibility risk for persisted/introspected token IDs.

## Critical

:red_circle: [correctness] Inverted equality and wrong substring indices in `isAccessTokenId` matcher in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java`:476 (confidence: 98)
The new `isAccessTokenId` matcher used by `expectCodeToToken`, `expectRefresh`, `expectDeviceCodeToToken`, and `expectAuthReqIdToToken` has two bugs that together make it effectively useless — and arguably make the tests green for the wrong reason:

1. **Inverted equality** — the matcher returns `false` when the shortcut *matches* the expected one (`if (items[0].substring(3, 5).equals(expectedGrantShortcut)) return false;`). It should be `!equals`.
2. **Wrong substring bounds** — the encoded prefix layout is `<sess:0-2><tok:2-4><grant:4-6>`, so the grant shortcut lives at indices `[4, 6)`, not `[3, 5)` as written. The comment says "starts at character 4th char and is 2-chars long" which is itself ambiguous (1-indexed vs 0-indexed) and disagrees with the code.

Combined, the matcher will only return `true` when a specific wrong character at position 3 happens to land — in practice the assertions are likely passing because `isUUID().matches(items[1])` on the raw UUID suffix is always true and the early `return false` path is never reached for the *wrong* reason. Either way: event-level token-ID shape is not actually being validated.

```suggestion
    public static Matcher<String> isAccessTokenId(String expectedGrantShortcut) {
        return new TypeSafeMatcher<String>() {
            @Override
            protected boolean matchesSafely(String item) {
                String[] items = item.split(":");
                if (items.length != 2) return false;
                if (items[0].length() != 6) return false;
                // Layout: <sessionType:0-2><tokenType:2-4><grantType:4-6>
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
References: AssertEvents.java:476-488

:red_circle: [correctness] Copy-paste bug: second `requireNonNull` checks `grantType` instead of `rawTokenId` in `services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java`:72 (confidence: 99)
```java
Objects.requireNonNull(grantType, "Null grantType not allowed");
Objects.requireNonNull(grantType, "Null rawTokenId not allowed");   // <-- wrong variable
```
A `null` `rawTokenId` will silently pass validation, then surface later as a `NullPointerException` in `encodeTokenId` when it concatenates `':' + tokenContext.getRawTokenId()`, or — worse — as a legitimate-looking encoded prefix with a bare trailing `:` (e.g. `"onrtac:null"` if toString is implied somewhere, or `"onrtac:"`). The error message is even correct; only the checked reference is wrong.
```suggestion
    public AccessTokenContext(SessionType sessionType, TokenType tokenType, String grantType, String rawTokenId) {
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
        this.sessionType = sessionType;
        this.tokenType = tokenType;
        this.grantType = grantType;
        this.rawTokenId = rawTokenId;
    }
```
References: AccessTokenContext.java:70-76

## Improvements

:yellow_circle: [cross-file-impact] Token ID format change is a breaking contract for persisted / introspected `jti` in `services/src/main/java/org/keycloak/protocol/oidc/TokenManager.java`:1051 (confidence: 85)
Access-token IDs previously were bare UUIDs (36 chars). They are now 6-char-prefix + `:` + UUID (43 chars). Any downstream consumer that:
- persists `jti` in a UUID-typed column,
- uses regex-based UUID validation on `jti`,
- logs/indexes token IDs with UUID-aware parsers,
- or compares `jti` as a stable identifier across an in-flight refresh
…will encounter a shape change without migration. The decoder does gracefully handle legacy UUID-only IDs (`getTokenContextFromTokenId` returns `UNKNOWN` when no `:`), but consumers outside Keycloak's SPI surface won't. At minimum this deserves a release-note callout; ideally a compatibility-mode flag to keep bare-UUID IDs. Also worth confirming token-introspection tests and any SAML/OIDC bridge code-paths still treat the encoded ID opaquely.
```suggestion
// Consider: gate the new format behind a realm attribute / feature flag for one release cycle,
// or document explicitly in the upgrade guide that `jti` format has changed from UUID to
// `<6char-prefix>:<uuid>` with backward-compatible decoding.
```

:yellow_circle: [correctness] Refresh flow overwrites the original grant type, losing provenance in `services/src/main/java/org/keycloak/protocol/oidc/TokenManager.java`:248 (confidence: 80)
`validateToken` unconditionally sets `clientSessionCtx.setAttribute(Constants.GRANT_TYPE, OAuth2Constants.REFRESH_TOKEN)` during refresh. The PR description motivates this design ("grant type knowledge can be useful for token-exchange"), but after the first refresh the encoded token will always say `rt` (refresh_token), erasing the original grant (e.g. `ac` from authorization_code). If downstream consumers reason about "what grant originally produced this token chain", they cannot — and the PR description's stated use case for grant-type encoding is weakened. Consider either:
- preserving the original grant type on the client session and threading it through refresh, or
- documenting explicitly that the encoded grant is "grant used to mint *this* access token" (not the session's originating grant).
```suggestion
// Option A: preserve original grant type
// String originalGrant = clientSession.getNote(Constants.ORIGINAL_GRANT_TYPE);
// clientSessionCtx.setAttribute(Constants.GRANT_TYPE, originalGrant != null ? originalGrant : OAuth2Constants.REFRESH_TOKEN);
```

:yellow_circle: [correctness] `DefaultTokenContextEncoderProviderFactory`: shortcut-collision check runs only at `postInit`; dynamic registration can silently overwrite in `services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java`:114 (confidence: 75)
`postInit` populates both maps and validates via `grantsByShortcuts.size() != grantsToShortcuts.size()`. However the "refresh maps in case new grant type was deployed" paths in `getShortcutByGrantType` and `getGrantTypeByShortcut` do plain `put()` without any collision check. A deployed extension grant with a shortcut that collides with an existing one (e.g. another extension reusing `"ac"`) will silently overwrite the prior mapping in one direction and leave the reverse map inconsistent. Also, the two maps are updated with two separate `put()` calls — no atomic bulk update — so concurrent readers can observe a torn state where `grantsByShortcuts` has the new entry but `grantsToShortcuts` does not.
```suggestion
// In both refresh paths, guard with a collision check before put, e.g.:
String existing = grantsByShortcuts.putIfAbsent(shortcut, grantName);
if (existing != null && !existing.equals(grantName)) {
    throw new IllegalStateException("Shortcut collision: '" + shortcut + "' already registered to '" + existing + "'");
}
grantsToShortcuts.putIfAbsent(grantName, shortcut);
```

## Nitpicks

:white_circle: [consistency] `UNKNOWN` sentinel value `"na"` lives in the same namespace as real grant shortcuts in `services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProvider.java`:28 (confidence: 60)
Seeding `grantsByShortcuts.put("na", "na")` in `postInit` means the literal string `"na"` is both a valid shortcut and a valid grant type. A future custom grant factory returning `getId() == "na"` or `getShortcut() == "na"` would trip the duplicate-maps check — or, worse, produce an odd self-referential mapping. Consider namespacing the sentinel (e.g. `"__unknown__"` or a non-2-char value that fails the length check by design, so the decoder path stays consistent).

:white_circle: [consistency] `Constants.GRANT_TYPE` is a redundant alias for `OAuth2Constants.GRANT_TYPE` in `server-spi-private/src/main/java/org/keycloak/models/Constants.java`:206 (confidence: 70)
```java
public static final String GRANT_TYPE = OAuth2Constants.GRANT_TYPE;
```
Two different fully-qualified names for the same string invites future drift — someone will update one side and not the other. The comment says this key is "used in clientSessionContext" but the value is the same constant used as the HTTP form parameter name, which risks implicit coupling. Either reuse `OAuth2Constants.GRANT_TYPE` directly at the call sites, or use a distinct key (e.g. `"grant_type_note"`) to disambiguate form-param vs session-attribute semantics.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 28 files across `services`, `server-spi-private`, `testsuite`; touches token minting and event assertions across all OAuth2 grant flows | Sensitive Paths: token minting / auth flow (services/src/main/java/org/keycloak/protocol/oidc/**)
AI-Authored Likelihood: LOW — style, commentary, and SPI patterns are idiomatic Keycloak and consistent with co-author Marek Posolda's prior contributions; the two copy-paste defects found are classic human oversights rather than hallucination signatures.

## Recommendation
**Request changes.** Both critical findings are unambiguous bugs with concrete fixes (the test matcher is arguably shipping green for the wrong reason, which is worse than a failing test). The improvements around token-ID backward compatibility and grant-type provenance on refresh are worth deciding explicitly before merge rather than discovering in a downstream integration.

---
*Review generated from diff-level static analysis only. No runtime / integration-test execution performed.*
