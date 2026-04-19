## Summary
8 files changed, 256 lines added, 31 lines deleted. 9 findings (6 critical, 3 improvements).
Externalizes recovery-code credential management to `UserStorageProvider` (mirroring the OTP pattern), but the new `createRecoveryCodesCredential` helper hands raw plaintext codes across the SPI boundary, the login bean unwraps `Optional` without guards, and test coverage only exercises the happy path.

## Critical

:red_circle: [security] Recovery codes transmitted to user storage in plaintext JSON in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:112 (confidence: 92)
`createRecoveryCodesCredential` serializes the raw generated codes via `JsonSerialization.writeValueAsString(generatedCodes)` and passes them as the `challengeResponse` of a `UserCredentialModel` that is then forwarded to `user.credentialManager().updateCredential(...)`, i.e. to the configured user-storage provider. Before this PR, recovery codes reached persistence only via `RecoveryAuthnCodesCredentialProvider.createCredential`, which invokes `RecoveryAuthnCodesUtils.hashRawCode` (PBKDF2-SHA512) first. The new path ships unhashed, password-equivalent secrets to arbitrary external SPIs — implementers following the reference test provider shipped in this same PR will persist them at rest in plaintext. PR discussion shows this was an intentional choice for consistency with OTP/password, but the risk surface (custom LDAP/REST user storages, backend logs, backup exposure) is materially different from a short-lived auth input.
```suggestion
public static void createRecoveryCodesCredential(KeycloakSession session, RealmModel realm, UserModel user,
        RecoveryAuthnCodesCredentialModel credentialModel, List<String> generatedCodes) {
    var provider = session.getProvider(CredentialProvider.class,
            RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID);
    // Transport only the hashed credential data, never raw codes.
    UserCredentialModel hashed = new UserCredentialModel("", credentialModel.getType(),
            credentialModel.getCredentialData());
    boolean userStorageCreated = user.credentialManager().updateCredential(hashed);
    if (userStorageCreated) {
        logger.debugf("Created RecoveryCodes credential for user '%s' in the user storage", user.getUsername());
    } else {
        provider.createCredential(realm, user, credentialModel);
    }
}
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cwe.mitre.org/data/definitions/256.html, https://cwe.mitre.org/data/definitions/312.html]

:red_circle: [security] Reference test UserStorage persists recovery codes in plaintext in testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:192 (confidence: 85)
The new `updateCredential` branch for `RecoveryAuthnCodesCredentialModel.TYPE` takes `input.getChallengeResponse()` (the raw JSON list of plaintext codes produced by `CredentialHelper.createRecoveryCodesCredential`) and stores it verbatim as `credentialData` on the in-memory `MyUser.recoveryCodes`. `BackwardsCompatibilityUserStorage` is the canonical backwards-compatibility reference for external `UserStorageProvider` implementers — shipping an example that stores password-equivalent recovery codes at rest with no KDF actively encourages vulnerable downstream implementations and violates OWASP ASVS V2.4.
```suggestion
} else if (input.getType().equals(RecoveryAuthnCodesCredentialModel.TYPE)) {
    List<String> rawCodes = JsonSerialization.readValue(input.getChallengeResponse(), List.class);
    List<String> hashed = rawCodes.stream()
            .map(RecoveryAuthnCodesUtils::hashRawCode)
            .collect(Collectors.toList());
    CredentialModel recoveryCodesModel = new CredentialModel();
    recoveryCodesModel.setId(KeycloakModelUtils.generateId());
    recoveryCodesModel.setType(input.getType());
    recoveryCodesModel.setCredentialData(JsonSerialization.writeValueAsString(hashed));
    recoveryCodesModel.setCreatedDate(Time.currentTimeMillis());
    users.get(translateUserName(user.getUsername())).recoveryCodes = recoveryCodesModel;
    return true;
}
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cwe.mitre.org/data/definitions/256.html]

:red_circle: [correctness] Unguarded Optional.get() on getCredential() result throws NoSuchElementException in services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java:196 (confidence: 98)
The constructor replaces the previous unguarded `.findFirst().get()` with a call to `RecoveryAuthnCodesUtils.getCredential(user)`, which returns `Optional<CredentialModel>`, and then immediately unwraps it via `credentialModelOpt.get()` with no `isPresent()` check. If neither federated nor local storage holds a recovery-codes credential — e.g., the credential was deleted mid-flow, or `BackwardsCompatibilityUserStorage.getCredentials()` swallowed an `IOException` and returned an empty stream — `getCredential()` returns `Optional.empty()` and the login flow dies with an uncaught `NoSuchElementException`, producing a 500 instead of a graceful auth-failure challenge. This is a hot path on every recovery-code login.
```suggestion
public RecoveryAuthnCodeInputLoginBean(KeycloakSession session, RealmModel realm, UserModel user) {
    CredentialModel credentialModel = RecoveryAuthnCodesUtils.getCredential(user)
            .orElseThrow(() -> new IllegalStateException(
                    "No recovery-codes credential found for user " + user.getUsername()));
    RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel =
            RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModel);
    this.codeNumber = recoveryCodeCredentialModel.getNextRecoveryAuthnCode()
            .orElseThrow(() -> new IllegalStateException("Recovery codes exhausted"))
            .getNumber();
}
```

:red_circle: [testing] No test for invalid-code rejection in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:501 (confidence: 95)
`testRecoveryKeysSetupAndLogin` drives the full flow with a valid code and asserts success, but never submits a wrong code. The failure path through `BackwardsCompatibilityUserStorage.isValid()` and the `failureChallenge(INVALID_CREDENTIALS, ...)` branch in `RecoveryAuthnCodesFormAuthenticator` is completely uncovered. A regression that accidentally accepts any string, or that drops the `anyMatch` predicate, would pass CI.
```suggestion
@Test
public void testRecoveryKeysLoginWithInvalidCodeIsRejected() throws Exception {
    try {
        configureBrowserFlowWithRecoveryAuthnCodes(testingClient, 0);
        String userId = addUserAndResetPassword("otp1", "pass");
        getCleanup().addUserId(userId);
        setupRecoveryKeysForUserWithRequiredAction(userId, true);

        TestAppHelper helper = new TestAppHelper(oauth, loginPage, appPage);
        helper.startLogin("otp1", "pass");
        enterRecoveryAuthnCodePage.setDriver(driver);
        enterRecoveryAuthnCodePage.assertCurrent();
        enterRecoveryAuthnCodePage.enterRecoveryAuthnCode("INVALID-CODE-XXXX");
        enterRecoveryAuthnCodePage.clickSignInButton();

        enterRecoveryAuthnCodePage.assertCurrent();
        Assert.assertNotNull(enterRecoveryAuthnCodePage.getError());
    } finally {
        BrowserFlowTest.revertFlows(testRealm(), BROWSER_FLOW_WITH_RECOVERY_AUTHN_CODES);
    }
}
```

:red_circle: [testing] No test for code exhaustion (one-time-use semantics) in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:501 (confidence: 92)
Recovery codes are one-time-use. The test logs in once with code #0 but never retries the same code on a second login, so the test-provider `isValid()`'s `anyMatch` implementation — which does not remove the matched code — is never caught allowing unlimited reuse. A bug where the backing store never consumes the used code would pass silently.
```suggestion
// After the first successful login and logout, try to reuse the same code
testAppHelper.startLogin("otp1", "pass");
enterRecoveryAuthnCodePage.setDriver(driver);
enterRecoveryAuthnCodePage.assertCurrent();
enterRecoveryAuthnCodePage.enterRecoveryAuthnCode(recoveryKeys.get(0));
enterRecoveryAuthnCodePage.clickSignInButton();
enterRecoveryAuthnCodePage.assertCurrent();
Assert.assertNotNull(enterRecoveryAuthnCodePage.getError());
```

:red_circle: [testing] Local CredentialProvider fallback branch in createRecoveryCodesCredential has zero coverage in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:50 (confidence: 88)
The new helper has an explicit `if (userStorageCreated) { ... } else { recoveryCodeCredentialProvider.createCredential(realm, user, credentialModel); }` branch. The only integration test uses `BackwardsCompatibilityUserStorage`, which always claims the credential via `updateCredential` → `true`, so the `else` branch is never exercised. A regression passing the wrong argument, invoking the wrong provider, or failing to initialise `recoveryCodeCredentialProvider` would not be caught. Add a test with a plain local user (no user-storage federation) that sets up recovery codes, asserts the credential lands in local DB (not federated storage), and then logs in.

## Improvements

:yellow_circle: [correctness] Dual-path createRecoveryCodesCredential sends structurally different payloads with no contract documentation in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:50 (confidence: 85)
The user-storage path receives a raw JSON `List<String>` of plaintext codes as `challengeResponse`; the local fallback receives the already-hashed `credentialModel`. These payloads are not interchangeable, and the asymmetry is implicit and undocumented. A future user-storage implementor who tries to delegate validation back to `CredentialProvider.isValid()` (which hashes the input before comparing) will get permanent auth failures with no error. Add Javadoc to `createRecoveryCodesCredential()` documenting that user-storage receives raw codes and is responsible for its own storage/validation, while the local path stores the pre-hashed model.
```suggestion
/**
 * Creates a RecoveryCodes credential. If the user is backed by a user-storage
 * provider that claims the credential (updateCredential() returns true), the
 * raw plain-text codes are forwarded as a JSON array in the challengeResponse
 * of the UserCredentialModel, and the provider is responsible for storage and
 * validation. Otherwise, the already-hashed credentialModel is stored locally.
 * The two paths use different on-the-wire representations — user-storage
 * providers must NOT delegate validation to the local CredentialProvider.
 */
```

:yellow_circle: [consistency] Bare RuntimeException wrap of IOException drops context in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:56 (confidence: 85)
`throw new RuntimeException(e);` loses the operation name and makes logs noisier. Wrap with a descriptive message matching the rest of the codebase's error-handling style.
```suggestion
throw new RuntimeException("Failed to serialize recovery codes for user " + user.getUsername(), e);
```

:yellow_circle: [testing] enterRecoveryCodes helper hardcodes expectedCode=0 and sole call site only tests code #0 in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:603 (confidence: 85)
The helper accepts `expectedCode` but the only invocation passes `0`, so an off-by-one in the credential counter after user-storage round-trip (e.g., `getNextRecoveryAuthnCode()` returning index 1 on first login) would surface as a confusing assertion error rather than a targeted bug. Add a second login iteration that consumes code #1 and asserts the server advances the counter.
```suggestion
// First login — server must present code #0 on a fresh credential
enterRecoveryCodes(enterRecoveryAuthnCodePage, driver, 0, recoveryKeys);
enterRecoveryAuthnCodePage.clickSignInButton();
appPage.assertCurrent();
testAppHelper.logout();

// Second login — server must advance to code #1
testAppHelper.startLogin("otp1", "pass");
enterRecoveryCodes(enterRecoveryAuthnCodePage, driver, 1, recoveryKeys);
enterRecoveryAuthnCodePage.clickSignInButton();
appPage.assertCurrent();
```

## Risk Metadata
Risk Score: 53/100 (MEDIUM) | Blast Radius: CredentialHelper is a widely-imported shared utility; 5 production files + 3 test-infra files changed; core change isolated to recovery-code flows | Sensitive Paths: `*credential*` (CredentialHelper.java), `auth/` (RecoveryAuthnCodes*Authenticator.java, RecoveryAuthnCodesAction.java)
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold 85)
