# PR Review — keycloak/keycloak#37634

**Title:** Encoding context to access token IDs
**Base:** `main` ← **Head:** `37118-access-token-ids-rebase`
**Closes:** #37118

## Summary

28 files changed, 722 lines added, 26 lines deleted. 6 findings (2 critical, 3 improvements, 1 nitpick).
The PR encodes session-type / token-type / grant-type shortcuts into the access-token `jti` (e.g. `onrtac:<uuid>`) via a new `TokenContextEncoder` SPI. Core idea is sound and well-factored, but two bugs slipped through — one is a copy-paste null-check in the core value object, the other silently defeats the new test-assertion matcher that was added to lock the behavior in. Both should be fixed before merge.

## Critical

:red_circle: [correctness] Copy-paste null check — `rawTokenId` is never validated in `AccessTokenContext` constructor (confidence: 98)
`services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java:56-62`

```java
public AccessTokenContext(SessionType sessionType, TokenType tokenType, String grantType, String rawTokenId) {
    Objects.requireNonNull(sessionType, "Null sessionType not allowed");
    Objects.requireNonNull(tokenType, "Null tokenType not allowed");
    Objects.requireNonNull(grantType, "Null grantType not allowed");
    Objects.requireNonNull(grantType, "Null rawTokenId not allowed");   // BUG: checks grantType twice
    ...
}
```

The fourth precondition argument should be `rawTokenId`, not `grantType`. As written, a caller passing `rawTokenId = null` never trips the guard; instead it's stored and later flows into `encodeTokenId(...)` which produces a malformed token ID like `"onrtac:null"` (because Java string concatenation on a null reference renders the literal `"null"`). This slips through all unit tests because every test constructs with a non-null string. Low-likelihood today, but the whole point of `Objects.requireNonNull` is fail-fast at the boundary — the boundary here is silent.

```suggestion
        Objects.requireNonNull(sessionType, "Null sessionType not allowed");
        Objects.requireNonNull(tokenType, "Null tokenType not allowed");
        Objects.requireNonNull(grantType, "Null grantType not allowed");
        Objects.requireNonNull(rawTokenId, "Null rawTokenId not allowed");
```

---

:red_circle: [testing] `AssertEvents.isAccessTokenId` matcher is effectively a no-op on grant type — inverted condition AND wrong substring indices (confidence: 95)
`testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/AssertEvents.java:476-490`

```java
public static Matcher<String> isAccessTokenId(String expectedGrantShortcut) {
    return new TypeSafeMatcher<String>() {
        @Override
        protected boolean matchesSafely(String item) {
            String[] items = item.split(":");
            if (items.length != 2) return false;
            // Grant type shortcut starts at character 4th char and is 2-chars long
            if (items[0].substring(3, 5).equals(expectedGrantShortcut)) return false;   // BUG x2
            return isUUID().matches(items[1]);
        }
        ...
    };
}
```

Two compounding defects:

1. **Wrong offsets.** The encoded format is `<st:2><tt:2><gt:2>:<uuid>`. The grant-type shortcut occupies indices `[4,6)`, not `[3,5)`. `substring(3, 5)` pulls the last char of `tokenType` plus the first char of `grantType` (e.g. for `onrtac`, it reads `"ta"` instead of `"ac"`). This is also inconsistent with `DefaultTokenContextEncoderProvider.getTokenContextFromTokenId` which correctly uses `substring(4, 6)`.
2. **Inverted condition.** The check returns `false` (mismatch) when the shortcut *does* match. It should early-return `false` only when it *doesn't* match.

Net effect: because (1) makes `substring(3, 5).equals(expectedGrantShortcut)` almost always false, the `return false` branch from (2) is rarely taken, so the matcher falls through to `isUUID().matches(items[1])` and passes on any valid UUID suffix — regardless of whether the grant shortcut matches what the test asserted. The callers (`expectCodeToToken`, `expectDeviceCodeToToken`, `expectRefresh`, `expectAuthReqIdToToken`) believe they are locking in the grant-specific shortcut; they are actually only locking in "some 6-char prefix + colon + UUID". A regression that flipped, say, `ac` with `rt` in the code path would silently pass these tests.

```suggestion
    public static Matcher<String> isAccessTokenId(String expectedGrantShortcut) {
        return new TypeSafeMatcher<String>() {
            @Override
            protected boolean matchesSafely(String item) {
                String[] items = item.split(":");
                if (items.length != 2) return false;
                if (items[0].length() != 6) return false;
                // Grant type shortcut occupies characters 4 and 5 (0-indexed)
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

Strongly recommend also adding a direct unit test for this matcher with a positive case (expect match) and a negative case (wrong grant shortcut → expect mismatch) to prevent this from regressing again.

## Improvements

:yellow_circle: [cross-file-impact] Removal of `Context(Context context)` copy constructor from `OAuth2GrantType` breaks extenders of this semi-public SPI (confidence: 80)
`server-spi-private/src/main/java/org/keycloak/protocol/oidc/grants/OAuth2GrantType.java:99-111`

The copy constructor of `OAuth2GrantType.Context` was deleted. While this class lives in `server-spi-private`, the name is used by keycloak-ext and third-party grants (search for `new OAuth2GrantType.Context(context)` in external providers — it's the documented pattern for wrapping an upstream context). Removing it is a source-incompatible change with no deprecation cycle.

Either:
- Re-add the copy constructor and have it also copy the new `grantType` field, or
- Keep it removed but explicitly mark it in the changelog / upgrade notes (and accept that downstream SPI code must migrate).

```suggestion
        public Context(Context context) {
            this.session = context.session;
            this.realm = context.realm;
            this.client = context.client;
            this.clientConfig = context.clientConfig;
            this.clientConnection = context.clientConnection;
            this.clientAuthAttributes = context.clientAuthAttributes;
            this.request = context.request;
            this.response = context.response;
            this.headers = context.headers;
            this.formParams = context.formParams;
            this.event = context.event;
            this.cors = context.cors;
            this.tokenManager = context.tokenManager;
            this.grantType = context.grantType;
        }
```

---

:yellow_circle: [correctness] Duplicate-shortcut validation doesn't cover SessionType / TokenType collisions, and `"rt"` is already reused across dimensions (confidence: 75)
`services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java:82-86`
`services/src/main/java/org/keycloak/protocol/oidc/grants/RefreshTokenGrantTypeFactory.java:33`

`postInit` validates only that **grant** shortcuts are unique among grants:

```java
if (grantsByShortcuts.size() != grantsToShortcuts.size()) {
    throw new IllegalStateException("...same ID or shortcut like other grants");
}
```

It does not assert that a grant shortcut isn't identical to a `SessionType` shortcut (`on`, `of`, `tr`, `un`) or `TokenType` shortcut (`rt`, `lt`, `un`). Today this is already violated: `TokenType.REGULAR.shortcut == "rt"` *and* `RefreshTokenGrantTypeFactory.GRANT_SHORTCUT == "rt"`. Positional decoding papers over it, but:
- It's a footgun for log parsing / grep-based tooling — `grep ':rt' token.log` no longer has a single semantic meaning.
- A future refactor that makes the format self-describing (which the PR body hints was tried in commit 1 before the tighter encoding won) would immediately collide.
- Any future grant factory author is only warned about collisions *within grants*, not across dimensions.

Either disambiguate the shortcuts or extend the collision check to the full (session ∪ token ∪ grant) shortcut space.

---

:yellow_circle: [consistency] Two different "unknown" sentinels — enums use `"un"`, grants use `"na"` (confidence: 70)
`services/src/main/java/org/keycloak/protocol/oidc/encode/AccessTokenContext.java:29,42`
`services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProvider.java:33`

`SessionType.UNKNOWN` and `TokenType.UNKNOWN` both encode as `"un"`, while the grant-type unknown sentinel is the literal string `"na"`. The factory seeds `grantsByShortcuts.put("na", "na")` so `"na"` round-trips, but having three "unknown" codes with two different spellings (and one that is a bare string rather than an enum member) adds friction for anyone reading encoded IDs. Consider:
- Standardizing on `"un"` across all three dimensions, or
- Promoting the grant-unknown case to a named constant exported from `AccessTokenContext` so all three are referenced from one place.

## Nitpicks

:white_circle: [consistency] Package-private test-visibility fields on `DefaultTokenContextEncoderProviderFactory` (confidence: 65)
`services/src/main/java/org/keycloak/protocol/oidc/encode/DefaultTokenContextEncoderProviderFactory.java:43-44`

```java
Map<String, String> grantsByShortcuts;
Map<String, String> grantsToShortcuts;
```

These are missing any explicit access modifier (package-private) so the new `DefaultTokenContextEncoderProviderTest` can reach in and mutate them. The `sessionTypesByShortcut` / `tokenTypesByShortcut` above are `private` — the asymmetry is incidental-looking. If test-visibility is the reason, add `@VisibleForTesting` or introduce a package-private setter; otherwise make them `private` and expose a small test seam.

## Risk Metadata

Risk Score: 72/100 (HIGH) | Blast Radius: every access-token issuance path (`TokenManager.initToken`), plus event logging / introspection consumers that parse `jti` | Sensitive Paths: `protocol/oidc/**`, `TokenManager.java`
AI-Authored Likelihood: LOW — copyright headers, explicit `@author` tags, the hand-shaped PR description about commit-1 → commit-2 shortening, and the two human-typical copy-paste errors (null-check and substring bounds) all read as human-authored.

## Recommendation

**request-changes** — merging as-is ships a silent assertion-weakening test matcher plus an unreachable null-guard. Both fixes are 1–2 line changes; after that, also worth considering whether to restore the removed `Context(Context)` copy constructor or document the SPI break.

## Metadata

- Files reviewed: 28 (18 production, 2 META-INF service files, 1 new unit test, 1 test-helper update, 1 constants file)
- Deterministic gate: skipped (feature-flagged off)
- Spec alignment: PR description ↔ code matches — shortcuts are indeed 2 chars, encoding is indeed `st|tt|gt:<uuid>`, only access tokens carry it (refresh/ID tokens untouched, confirmed by inspecting `TokenManager.initToken` callers)
- Existing maintainer reviews: @rmartinc LGTM; mfeuerstein's automated "28 files — looks good" comment is a rubber-stamp and did not catch either critical issue above
