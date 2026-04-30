## Summary
9 files changed, 407 lines added, 24 lines deleted. 3 findings (3 critical, 0 improvements, 0 nitpicks).
9 files changed. 3 findings (3 critical). Build-breaking zero-arg call to `isConditionalPasskeysEnabled()` in `UsernameForm.java:47`, plus two regressions where the passkey Conditional UI is suppressed on anonymous login because the new helper requires `user != null`.

## Critical

:red_circle: [correctness] Call to non-existent zero-argument isConditionalPasskeysEnabled() causes compilation failure in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernameForm.java:47 (confidence: 99)
UsernameForm.authenticate() calls `isConditionalPasskeysEnabled()` with no arguments. The only definition of this method in the hierarchy is `protected boolean isConditionalPasskeysEnabled(UserModel user)` in UsernamePasswordForm — a single-argument method. No zero-argument overload exists anywhere in the class hierarchy. This is a build-breaking compilation error; the class will not compile and the authenticator will be entirely unavailable at runtime.
```suggestion
        if (context.getUser() != null && !isConditionalPasskeysEnabled(context.getUser())) {
```

:red_circle: [correctness] Passkey conditional UI suppressed for anonymous users in authenticate() — core passkey discoverable-key flow regressed in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:115 (confidence: 97)
Before this PR, the else-branch (user is null) unconditionally called `webauthnAuth.fillContextForm(context)` when passkeys were enabled. The passkey Conditional UI feature is specifically designed to work when no user is yet identified — the browser's OS credential picker selects a passkey and the user handle is returned in the WebAuthn response. After this PR, `fillContextForm` is gated by `isConditionalPasskeysEnabled(context.getUser())`, which requires `user != null`. On any anonymous first-visit to the login page (no loginHint, no rememberMe, no pre-set user), `context.getUser()` is null, so `fillContextForm` is never called. As a result, the WebAuthn challenge, RP ID, and `autocomplete="webauthn"` form attributes are never set, and the passkey conditional UI element is not rendered. The new test `webauthnLoginWithDiscoverableKey_reauthentication` does not exercise this anonymous-first-visit path and does not catch this regression.
```suggestion
    protected boolean isConditionalPasskeysEnabled() {
        return webauthnAuth != null && webauthnAuth.isPasskeysEnabled();
    }

    protected boolean isConditionalPasskeysEnabled(UserModel user) {
        return isConditionalPasskeysEnabled() && user != null;
    }
```

:red_circle: [correctness] challenge() inverts user-null condition — passkey UI not re-populated on error response for anonymous login in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:137 (confidence: 95)
Before this PR, `challenge(context, error, field)` had the guard `if (context.getUser() == null && webauthnAuth != null && webauthnAuth.isPasskeysEnabled())`. This fired for the anonymous case (user is null), which is correct: when a user submits a bad username, getUser() is still null at that point, and the error-redisplay page still needs the passkey conditional UI elements set so the browser can show the credential picker again. After this PR the condition is `if (isConditionalPasskeysEnabled(context.getUser()))`, which requires user != null. The condition has been effectively inverted for the anonymous flow: it now fires only when a known user is present, and skips precisely the anonymous case the original code was designed to handle. Any username validation error shown to an anonymous user will no longer include the passkey conditional UI form, breaking the autocomplete credential picker on the error page.
```suggestion
    @Override
    protected Response challenge(AuthenticationFlowContext context, String error, String field) {
        if (webauthnAuth != null && webauthnAuth.isPasskeysEnabled()) {
            webauthnAuth.fillContextForm(context);
        }
        return super.challenge(context, error, field);
    }
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: shim repo — importer count not measurable; all 6 production files in core auth flow (`AbstractUsernameFormAuthenticator`, `UsernamePasswordForm`, `UsernameForm`, `WebAuthnAuthenticator`, `WebAuthnConditionalUIAuthenticator`, `AuthenticatorUtils`) are widely subclassed/called in real Keycloak | Sensitive Paths: 6/6 production files match `auth/`
AI-Authored Likelihood: LOW
