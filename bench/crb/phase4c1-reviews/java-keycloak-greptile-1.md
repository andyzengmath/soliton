## Summary
9 files changed, 407 lines added, 24 lines deleted. 5 findings (1 critical, 2 improvements, 2 nitpicks).
Re-auth-with-passkeys fix in Keycloak browser authenticators: extracts a shared reauth-error helper into `AuthenticatorUtils`, keeps the username form on-screen when conditional passkeys are enabled, and always runs WebAuthn form setup when the feature is on. Core logic changes are surgical (~55 lines across 6 service files) with broad integration-test coverage (~360 lines), but the semantics of `isConditionalPasskeysEnabled` and the unqualified call site in `UsernameForm` deserve a second look.

## Critical
:red_circle: [correctness] Unresolved zero-arg call `isConditionalPasskeysEnabled()` in `UsernameForm.authenticate` in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernameForm.java:47 (confidence: 80)
The new condition `if (context.getUser() != null && !isConditionalPasskeysEnabled())` calls a zero-argument method, but the only definition introduced by this PR is `isConditionalPasskeysEnabled(UserModel user)` in `UsernamePasswordForm` (which is the parent class of `UsernameForm` in Keycloak). There is no zero-arg overload added or visible in the diff. Unless an overload already exists on `UsernameForm` or an ancestor prior to this PR, this will not compile. Verify the method is either (a) an existing zero-arg method on `UsernameForm` preserved outside the diff, or (b) a mistaken call that should be `!isConditionalPasskeysEnabled(context.getUser())`. Given the intent — skip the form unless passkeys are available for the already-identified user — the latter fix is almost certainly what was meant.
```suggestion
        if (context.getUser() != null && !isConditionalPasskeysEnabled(context.getUser())) {
```

## Improvements
:yellow_circle: [correctness] WebAuthn form setup no longer runs when `user == null` — silent behavior inversion in services/src/main/java/org/keycloak/authentication/authenticators/browser/UsernamePasswordForm.java:110 (confidence: 78)
Previously, `authenticate()` called `webauthnAuth.fillContextForm(context)` inside the `if (context.getUser() == null)` branch whenever passkeys were enabled, so the initial-login path always populated WebAuthn form data. After the refactor the call is moved outside the `if` block and gated by `isConditionalPasskeysEnabled(user)`, whose body is `webauthnAuth != null && webauthnAuth.isPasskeysEnabled() && user != null`. The `user != null` clause flips the guard: the webauthn setup now runs only when the user IS already selected, i.e. the exact opposite of the old guard. If `WebAuthnConditionalUIAuthenticator` ahead of this in the flow is expected to handle the `user == null` case, that assumption is load-bearing and should be asserted by a test that exercises `UsernamePasswordForm` *without* a preceding conditional-UI authenticator (e.g. a stock browser flow where `UsernamePasswordFormFactory` is used directly). Otherwise initial passkey-usernameless login via this path silently loses its WebAuthn form payload.
```suggestion
        }
        // setup webauthn data whenever passkeys are enabled; fillContextForm itself
        // decides (via shouldShowWebAuthnAuthenticators) whether to show the
        // authenticators list or the conditional-UI form for usernameless login
        if (webauthnAuth != null && webauthnAuth.isPasskeysEnabled()) {
            webauthnAuth.fillContextForm(context);
        }
```

:yellow_circle: [consistency] Visibility of `USER_SET_BEFORE_USERNAME_PASSWORD_AUTH` promoted to `public` only to cross a package boundary in services/src/main/java/org/keycloak/authentication/authenticators/browser/AbstractUsernameFormAuthenticator.java:58 (confidence: 75)
The constant is now `public` solely so `AuthenticatorUtils` (in `...authenticators.util`) can read it. This leaks an implementation-detail session-note key into the global API surface, which other integrators may begin to rely on. Two cleaner alternatives: (1) keep the helper `setupReauthenticationInUsernamePasswordFormError` as a `protected static` method on `AbstractUsernameFormAuthenticator` itself and call it from subclasses / `WebAuthnConditionalUIAuthenticator` via inheritance or a dedicated helper, or (2) expose a small accessor `isUserAlreadySetBeforeUsernamePasswordAuth(AuthenticationSessionModel)` instead of the raw key. Prefer either over widening the constant.
```suggestion
    protected static final String USER_SET_BEFORE_USERNAME_PASSWORD_AUTH = "USER_SET_BEFORE_USERNAME_PASSWORD_AUTH";
```

## Risk Metadata
Risk Score: 48/100 (MEDIUM) | Blast Radius: auth-sensitive (browser authenticators + shared `AuthenticatorUtils`), 9 files, ~55 non-test service lines, ~360 test lines | Sensitive Paths: `authentication/authenticators/browser/*`, `authentication/authenticators/util/AuthenticatorUtils.java`
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold: misleading method names `isConditionalPasskeysEnabled` / `setupReauthenticationInUsernamePasswordFormError` — the latter is now invoked from a WebAuthn error path, not only a username/password error path; and test style inconsistency in `reauthenticationOfUserWithoutPasskey` which uses a `try/fail/catch` idiom while the same class uses `Assert.assertThrows` elsewhere.)
