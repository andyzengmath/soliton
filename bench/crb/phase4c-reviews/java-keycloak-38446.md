## Summary
8 files changed, ~280 lines added, ~25 lines deleted. 7 findings (3 critical, 4 improvements).
Adds a fallback path so recovery-authentication codes can be persisted in federated user storage when the storage provider supports it, otherwise falls back to local Keycloak DB. The federated-storage write path is a security regression (raw codes + timing-unsafe equality) and the login-bean refactor introduces an unguarded `Optional.get()`.

## Critical

:red_circle: [security] Recovery codes persisted as plaintext JSON in federated storage in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:115 (confidence: 92)
`createRecoveryCodesCredential` serializes the `List<String> generatedCodes` to JSON and wraps it in a `UserCredentialModel` whose `challengeResponse` is the raw-code JSON. When the federated `CredentialInputUpdater.updateCredential` accepts that input, the storage provider receives the plaintext codes — confirmed by the `BackwardsCompatibilityUserStorage` reference impl (services/.../federation/BackwardsCompatibilityUserStorage.java:189) which writes `input.getChallengeResponse()` straight into `credentialData`. The local path (`recoveryCodeCredentialProvider.createCredential(... credentialModel)`) stores PBKDF2-hashed codes via `RecoveryAuthnCodesCredentialModel.createFromValues`, so a user whose federated provider accepts the update has materially weaker protection than one who falls through to local DB. This asymmetry is security-visible even if an individual federated implementation chooses to hash internally — the API shape hands them plaintext. Either (a) require federated providers to accept a pre-hashed `RecoveryAuthnCodesCredentialModel` (matching the local path), or (b) document that federated storage MUST hash and add a compliance test. The PR author's own question in the PR comments ("Should we do the same approach here? At the moment I'm sending the passkeys hashed?") indicates this decision was never resolved and the final code ships the unhashed variant.
```suggestion
    public static void createRecoveryCodesCredential(KeycloakSession session, RealmModel realm, UserModel user,
                                                     RecoveryAuthnCodesCredentialModel credentialModel, List<String> generatedCodes) {
        // Try federated storage first with the *hashed* credential model, matching local-storage semantics.
        // Federated providers that want raw codes must opt in explicitly via a separate SPI.
        UserCredentialModel recoveryCodesCredential = new UserCredentialModel(
                credentialModel.getId() == null ? "" : credentialModel.getId(),
                credentialModel.getType(),
                credentialModel.getCredentialData()); // already hashed by RecoveryAuthnCodesCredentialModel

        boolean userStorageCreated = user.credentialManager().updateCredential(recoveryCodesCredential);
        if (userStorageCreated) {
            logger.debugf("Created RecoveryCodes credential for user '%s' in the user storage", user.getUsername());
            return;
        }
        CredentialProvider<?> provider = session.getProvider(CredentialProvider.class, "keycloak-recovery-authn-codes");
        if (provider == null) {
            throw new IllegalStateException("RecoveryAuthnCodes credential provider not available; cannot persist credential");
        }
        provider.createCredential(realm, user, credentialModel);
    }
```

:red_circle: [security] Recovery-code validation uses non-constant-time `String.equals` on plaintext codes in testsuite/integration-arquillian/.../BackwardsCompatibilityUserStorage.java:336 (confidence: 88)
`isValid` deserializes the stored list and then `generatedKeys.stream().anyMatch(key -> key.equals(input.getChallengeResponse()))`. Two independent problems: (1) `String.equals` is not constant-time, so an attacker observing response timing can distinguish prefix-match lengths on a recovery code — recovery codes are a last-resort credential and are typically a short fixed alphanumeric, exactly the regime where timing side-channels matter. (2) The stored form is the plaintext JSON array because `updateCredential` wrote `input.getChallengeResponse()` directly. Even if this is "only" the test/reference user storage, the BackwardsCompatibilityUserStorage is used by Keycloak as the canonical example that downstream federated providers copy; shipping a timing-unsafe plaintext comparison here normalizes the pattern for real deployments. Use `MessageDigest.isEqual` on byte arrays and hash codes before storage.
```suggestion
        } else if (input.getType().equals(RecoveryAuthnCodesCredentialModel.TYPE)) {
            CredentialModel storedRecoveryKeys = myUser.recoveryCodes;
            if (storedRecoveryKeys == null) {
                log.warnf("Not found credential for the user %s", user.getUsername());
                return false;
            }
            List<String> generatedKeys;
            try {
                generatedKeys = JsonSerialization.readValue(storedRecoveryKeys.getCredentialData(), List.class);
            } catch (IOException e) {
                log.warnf("Cannot deserialize recovery keys credential for the user %s", user.getUsername());
                return false;
            }
            byte[] candidate = input.getChallengeResponse().getBytes(StandardCharsets.UTF_8);
            boolean matched = false;
            for (String stored : generatedKeys) {
                // Constant-time to avoid prefix-timing leak; still linear in code count which is fine (~12 codes).
                matched |= MessageDigest.isEqual(stored.getBytes(StandardCharsets.UTF_8), candidate);
            }
            return matched;
        }
```

:red_circle: [correctness] Unguarded `Optional.get()` on an absent credential throws `NoSuchElementException` during login form rendering in services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java:20 (confidence: 90)
The refactor replaces `.findFirst().get()` on a direct stream with `RecoveryAuthnCodesUtils.getCredential(user)` which returns `Optional<CredentialModel>`, and then immediately calls `.get()` with no presence check:
```java
Optional<CredentialModel> credentialModelOpt = RecoveryAuthnCodesUtils.getCredential(user);
RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel =
        RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModelOpt.get());
```
The new `getCredential` returns empty when neither federated nor local credential is present — a scenario that can happen if (a) the user's federated provider was disabled between login pages, (b) the credential was deleted via admin REST concurrently, or (c) a misconfigured flow schedules the recovery-codes authenticator without the user having registered codes. In all three cases this throws `NoSuchElementException` out of the Freemarker template render path, which Keycloak surfaces as a 500 rather than as a proper `AuthenticationFlowError.CREDENTIAL_SETUP_REQUIRED`. The old code had the same shape but was called only after the caller had already filtered to users with codes; `getCredential`'s broader semantics mean the invariant no longer holds. Handle the empty case explicitly.
```suggestion
    public RecoveryAuthnCodeInputLoginBean(KeycloakSession session, RealmModel realm, UserModel user) {
        CredentialModel credentialModel = RecoveryAuthnCodesUtils.getCredential(user)
                .orElseThrow(() -> new IllegalStateException(
                        "User " + user.getUsername() + " has no recovery-authn-codes credential in federated or local storage"));
        RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel =
                RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModel);
        this.codeNumber = recoveryCodeCredentialModel.getNextRecoveryAuthnCode().get().getNumber();
    }
```

## Improvements

:yellow_circle: [correctness] Magic-string provider ID instead of the factory constant in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:113 (confidence: 86)
`session.getProvider(CredentialProvider.class, "keycloak-recovery-authn-codes")` hardcodes the provider ID, while the rest of the codebase (including the call site this PR replaces in `RecoveryAuthnCodesAction`) uses `RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID`. The old `RecoveryAuthnCodesAction` had exactly that import; this PR removes it from `RecoveryAuthnCodesAction.java` and then reintroduces the raw string in `CredentialHelper`. If the constant is ever renamed, this helper silently returns `null`.
```suggestion
        var recoveryCodeCredentialProvider = session.getProvider(CredentialProvider.class,
                RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID);
```

:yellow_circle: [correctness] Null `recoveryCodeCredentialProvider` not guarded before fallback call in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:125 (confidence: 82)
`session.getProvider(CredentialProvider.class, ...)` returns `null` when the RECOVERY_CODES feature is disabled or the provider failed to register. The code proceeds to `recoveryCodeCredentialProvider.createCredential(realm, user, credentialModel)` unconditionally in the else branch, producing an `NPE` rather than a meaningful error. Combined with the fact that `createRecoveryCodesCredential` is invoked from `RecoveryAuthnCodesAction.processAction` — a required-action entry point that can be reached through misconfiguration — this will surface as a stack trace in the login flow. Null-check and throw a feature-disabled error.

:yellow_circle: [correctness] `getCredential` hides local credential when a federated one also exists in server-spi/src/main/java/org/keycloak/models/utils/RecoveryAuthnCodesUtils.java:54 (confidence: 78)
The utility returns the first federated credential of the type, falling back to local only when no federated credential exists. This is a reasonable default for lookup but silently masks a legitimate state: a user migrated *from* local storage *to* federated still has the local credential row, and after this change every read path (`RecoveryAuthnCodeInputLoginBean`, `RecoveryAuthnCodesFormAuthenticator.isRecoveryAuthnCodeInputValid`) will see only the federated copy — even if the federated codes were never set up and the federated provider returns a stub. The pre-change behavior (local-only lookup) at least made this state impossible. Consider returning both or explicitly documenting the precedence on the public method.

:yellow_circle: [testing] No regression test for users who created recovery codes before this change in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:239 (confidence: 80)
The new `testRecoveryKeysSetupAndLogin` covers the green-field case — a user who has never had codes, sets them up post-change, logs in via federated storage. It does NOT cover the question ahus1 raised explicitly in the PR thread: "Will those recovery codes created in the past still work after this change?" The backward-compatibility path is exactly what the test suite file name promises, and `RecoveryAuthnCodesUtils.getCredential` has two branches (federated, then local) precisely to support this migration. Add a test that seeds a recovery-codes credential in the local DB before the PR's new code path runs, then logs in and confirms authentication succeeds via the local fallback — that's the only mechanism keeping upgrades non-breaking, and right now nothing enforces it.

## Risk Metadata
Risk Score: 68/100 (HIGH) | Blast Radius: 8 files across server-spi, services, and testsuite — touches auth credential persistence and login-form rendering | Sensitive Paths: server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java, services/src/main/java/org/keycloak/authentication/**, services/src/main/java/org/keycloak/forms/login/** (credential/auth subsystem)
AI-Authored Likelihood: LOW
