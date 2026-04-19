## Summary
9 files changed, 346 lines added, 24 lines deleted. 5 findings (3 critical, 2 improvements).
Re-auth passkey refactor introduces a compile-time error in `UsernameForm` (no-arg `isConditionalPasskeysEnabled()` call against a one-arg overload) and inverts the fresh-login passkey gate in `UsernamePasswordForm`, breaking the `user==null` conditional-UI path that the PR's own tests assert.

## Critical
:red_circle: [correctness] No-arg `isConditionalPasskeysEnabled()` call does not match the only defined overload (one-arg), causing a compile error in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernameForm.java:47 (confidence: 95)
`UsernameForm.authenticate()` is changed to `if (context.getUser() != null && !isConditionalPasskeysEnabled())` — zero arguments. The only overload introduced in this PR is `protected boolean isConditionalPasskeysEnabled(UserModel user)` on the parent `UsernamePasswordForm`. No zero-arg overload exists in the diff or in any visible ancestor (`AbstractUsernameFormAuthenticator`, `AbstractFormAuthenticator`). Java will fail to resolve this call with `method isConditionalPasskeysEnabled in class UsernamePasswordForm cannot be applied to given types; required: UserModel; found: no arguments`. The build will not compile. Cross-verified by the hallucination and cross-file-impact agents: grep for `isConditionalPasskeysEnabled` across the working copy returned exactly four occurrences — three call sites (this one, plus two correctly-formed calls in `UsernamePasswordForm`) and one one-arg declaration. No no-arg definition anywhere.
```suggestion
        if (context.getUser() != null && !isConditionalPasskeysEnabled(context.getUser())) {
```

:red_circle: [correctness] `authenticate()` no longer populates webauthn form attributes for fresh login (user==null), breaking the initial conditional-UI passkey prompt in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:54-56 (confidence: 92)
Before this PR, `webauthnAuth.fillContextForm(context)` was invoked inside the `if (context.getUser() == null)` block — the fresh-login path where no user is yet identified and a conditional-UI passkey picker should be shown. The PR moves the call outside that block and gates it on `isConditionalPasskeysEnabled(context.getUser())`, which the new helper defines as `webauthnAuth != null && webauthnAuth.isPasskeysEnabled() && user != null`. On initial page load of the combined username+password form, `context.getUser()` is null, so the helper returns false and `fillContextForm` is never called. The conditional passkey UI (`//form[@id='webauth']` element) will not be rendered during fresh login. The PR's own modified test `PasskeysUsernamePasswordFormTest.passwordLoginWithNonDiscoverableKey` asserts the webauth form IS present on the initial login page, which this change would cause to fail. The correct design (already implemented via `shouldShowWebAuthnAuthenticators`) is to let `WebAuthnAuthenticator` / `WebAuthnConditionalUIAuthenticator` decide based on user presence; callers should simply call `fillContextForm` whenever passkeys are enabled.
```suggestion
        if (webauthnAuth != null && webauthnAuth.isPasskeysEnabled()) {
            webauthnAuth.fillContextForm(context);
        }
```

:red_circle: [correctness] `challenge(context, error, field)` override no longer populates webauthn attributes for fresh login (user==null), breaking initial passkey form render in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:65-68 (confidence: 90)
The prior guard was `context.getUser() == null && webauthnAuth != null && webauthnAuth.isPasskeysEnabled()` — fill webauthn data when the user is not yet identified (the conditional-UI case). The replacement `isConditionalPasskeysEnabled(context.getUser())` inverts this: it returns true only when `user != null`. `challenge()` is the entry point that renders the login form; on first load, `user` is null, so no webauthn attributes are set and the passkey UI element is absent. The user-presence check has been inverted from `== null` to `!= null` by way of the new helper, conflating the "render form on entry" and "render form after error during re-auth" cases into a single gate that only the latter satisfies. The correct per-subclass polymorphism already exists via the new `shouldShowWebAuthnAuthenticators` hook — the outer guard should not also encode user presence.
```suggestion
    @Override
    protected Response challenge(AuthenticationFlowContext context, String error, String field) {
        if (webauthnAuth != null && webauthnAuth.isPasskeysEnabled()) {
            webauthnAuth.fillContextForm(context);
        }
        return super.challenge(context, error, field);
    }
```

## Improvements
:yellow_circle: [testing] `reauthenticationOfUserWithoutPasskey` uses try/catch to assert element absence rather than the idiomatic `assertThrows` pattern already used elsewhere in this test family in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:526-583 (confidence: 90)
The test uses two blocks of `try { assertThat(driver.findElement(...), nullValue()); fail(...); } catch (Exception nsee) { /* expected */ }` to verify the webauth form is absent. `driver.findElement()` throws `NoSuchElementException` before the matcher runs, so the `nullValue()` assertion is dead code. If WebDriver behavior ever changes to return null instead of throwing, `assertThat(null, nullValue())` passes and `fail()` is never reached — the test silently passes while the webauth form IS present. `PasskeysOrganizationAuthenticationTest.passwordLoginWithNonDiscoverableKey` (pre-PR) uses `Assert.assertThrows(NoSuchElementException.class, () -> driver.findElement(...))`, which is the established idiom in this codebase.
```suggestion
            Assert.assertThrows(NoSuchElementException.class,
                    () -> driver.findElement(By.xpath("//form[@id='webauth']")));
```

:yellow_circle: [testing] Re-auth UI contract (USERNAME_HIDDEN + REGISTRATION_DISABLED) has no direct assertion in any new test, leaving the new `setupReauthenticationInUsernamePasswordFormError` utility covered only indirectly in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/webauthn/passwordless/PasskeysUsernamePasswordFormTest.java:460-521 (confidence: 85)
The refactored `badPasswordHandler` and the new `AuthenticatorUtils.setupReauthenticationInUsernamePasswordFormError` exist to guarantee that after a bad-password submission during re-auth, the form is re-rendered with the username hidden/pre-filled and registration disabled. The new tests (`webauthnLoginWithExternalKey_reauthentication`, `reauthenticationOfUserWithoutPasskey`, `webauthnLoginWithExternalKey_reauthenticationWithPasswordOrPasskey`) either skip the bad-password leg or omit assertions on the re-auth UI shape after it. The observable contract (`USERNAME_HIDDEN` ⇒ username input is absent, `getAttemptedUsername()` returns the pre-set user) is therefore not asserted anywhere, so a regression that re-renders the editable username field on re-auth error would pass CI.
```suggestion
            // After failed re-auth password, the re-auth UI must hide the username input
            // and pre-fill the attempted user.
            MatcherAssert.assertThat(loginPage.getAttemptedUsername(), Matchers.is(user.getUsername()));
            Assert.assertThrows(NoSuchElementException.class,
                    () -> driver.findElement(By.id("username")));
```

## Risk Metadata
Risk Score: 52/100 (MEDIUM) | Blast Radius: 6 production files touching core Keycloak auth SPI (`AbstractUsernameFormAuthenticator`, `UsernameForm`, `UsernamePasswordForm`, `WebAuthnAuthenticator`, `WebAuthnConditionalUIAuthenticator`, `AuthenticatorUtils`); `AbstractUsernameFormAuthenticator` and `WebAuthnAuthenticator` are base classes with known subclasses outside the diff. | Sensitive Paths: `services/src/main/java/org/keycloak/authentication/authenticators/browser/` and `.../util/` match the `auth/` sensitive pattern and directly handle WebAuthn credential presentation, session-note inspection, and OIDC `prompt=login` re-auth gating.
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
