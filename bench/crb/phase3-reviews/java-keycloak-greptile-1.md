## Summary
9 files changed, ~180 lines added, ~20 lines deleted. 6 findings (1 critical, 0 improvements, 0 nitpicks).
Passkey re-auth fix is well-structured with new integration tests, but a helper-method signature change introduced between commits may leave a caller in `UsernameForm.java` referencing a no-argument variant that does not exist in the parent class.

## Critical
:red_circle: [correctness] `isConditionalPasskeysEnabled()` called without required `UserModel` parameter in `UsernameForm.java` (confidence: 85)
File: `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernameForm.java:44`

The second commit of this PR, "Add user parameter requirement to isConditionalPasskeysEnabled method", changes the helper declared in `UsernamePasswordForm` from a no-arg method into `protected boolean isConditionalPasskeysEnabled(UserModel user)`. `UsernameForm` (which extends `UsernamePasswordForm`) was updated in hunk 1 to call `!isConditionalPasskeysEnabled()` with **no argument**. Unless `UsernameForm` defines its own no-arg override (not shown in the diff), this call resolves to a method that no longer exists and will fail to compile. Greptile's automated review flagged the same site independently.

```suggestion
if (context.getUser() != null && !isConditionalPasskeysEnabled(context.getUser())) {
```

Before merging, confirm either (a) `UsernameForm` declares its own `isConditionalPasskeysEnabled()` (no-arg) that shadows the parent, or (b) update this call site to pass `context.getUser()` as shown above. Run `mvn -pl services compile` to verify.

References:
- PR commit 2: "Add user parameter requirement to isConditionalPasskeysEnabled method"
- Greptile review comment on PR #1

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: authentication flow (UsernameForm, UsernamePasswordForm, WebAuthnAuthenticator, WebAuthnConditionalUIAuthenticator) — reachable from every browser-based login + OIDC `prompt=login` re-auth path; `USER_SET_BEFORE_USERNAME_PASSWORD_AUTH` visibility widened from `protected` → `public` extending public API surface | Sensitive Paths: `authentication/authenticators/browser/` (auth/*), `webauthn` passkeys
AI-Authored Likelihood: LOW — commits are coherent and incremental (second commit fixes first commit's API), tests mirror existing patterns in the test module, and the refactor extracts an `AuthenticatorUtils` helper in a way consistent with Keycloak style.

(5 additional findings below confidence threshold)

---

### Findings below confidence threshold (suppressed, shown for benchmark transparency)

:yellow_circle: [correctness] Possible logic inversion in `UsernamePasswordForm` passkey gating (confidence: 65)
File: `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:110-141`

Hunk 2 replaces a condition that previously required `context.getUser() == null` (first-phase login, before a user is chosen) with `isConditionalPasskeysEnabled(context.getUser())`, which returns `true` only when `user != null`. That is a semantic inversion of the original guard (`user == null && webauthnAuth != null && webauthnAuth.isPasskeysEnabled()`). The new integration tests exercise the re-auth path (user already known), so the scenario that previously triggered this branch (no user selected yet) may no longer be hit. Confirm that the flow that used to rely on `user == null` is now handled by `WebAuthnConditionalUIAuthenticator` (whose `shouldShowWebAuthnAuthenticators` now returns `false`) or by the hunk-1 codepath — and that there is no regression for discoverable-key first-phase login.

:yellow_circle: [consistency] Constant visibility widened from `protected` to `public` (confidence: 70)
File: `services/src/main/java/org/keycloak/authentication/authenticators/browser/AbstractUsernameFormAuthenticator.java:55`

`USER_SET_BEFORE_USERNAME_PASSWORD_AUTH` was broadened from `protected static final` to `public static final` so that `AuthenticatorUtils` (a sibling util class in the same module) can reference it via a static import. Since `AuthenticatorUtils` is in a sibling package, `protected` was insufficient, but a narrower alternative is to colocate the constant in `AuthenticatorUtils` itself or introduce a package-level constants holder — `public` exposes it as part of Keycloak's SPI surface, meaning future renames become a breaking change for downstream extensions. Consider moving the constant instead of widening its visibility.

:white_circle: [testing] Test assertion inversion may obscure original intent (confidence: 70)
File: `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysOrganizationAuthenticationTest.java:193,207`

Two assertions flipped from `Assert.assertThrows(NoSuchElementException.class, () -> driver.findElement(By.xpath("//form[@id='webauth']")))` (asserting the passkey form is *absent*) to `MatcherAssert.assertThat(driver.findElement(...), Matchers.notNullValue())` (asserting it is *present*). The surrounding comment was also rewritten from "passkeys disabled" to "Passkeys still enabled". This is consistent with the PR's intent of keeping passkeys available during re-auth, but the test method name and assertion sequence should make the new expectation obvious — verify the test name still reflects current behavior and consider adding a one-line comment explaining why passkeys must remain visible after the first authentication step in a re-auth flow.

:white_circle: [correctness] `isConditionalPasskeysEnabled(UserModel)` contains redundant null check on `webauthnAuth` (confidence: 55)
File: `services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:157`

`return webauthnAuth != null && webauthnAuth.isPasskeysEnabled() && user != null;` — if `webauthnAuth` is an instance field assigned once at authenticate time, the `!= null` check mirrors pre-existing style, but the new method consolidates three gates into one. Consider documenting when `webauthnAuth` can legitimately be null (e.g. passkey provider not configured on the realm) so the guard's purpose is not lost.

:white_circle: [consistency] Javadoc on `shouldShowWebAuthnAuthenticators` over-narrows the method's purpose (confidence: 55)
File: `services/src/main/java/org/keycloak/authentication/authenticators/browser/WebAuthnAuthenticator.java:120-128`

The Javadoc says "Typically during 2-factor authentication for example" but the method is also the seam used by `WebAuthnConditionalUIAuthenticator` to *disable* the UI during conditional passkey login. Consider rewording to: "@return true if the available webauthn authenticators should be shown on the screen (e.g. during 2FA); subclasses may override to suppress display for conditional passkey flows." Small doc-only change but the override contract is the point of the seam.
