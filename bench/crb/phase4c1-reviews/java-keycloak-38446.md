## Summary
8 files changed, 256 lines added, 31 lines deleted. 9 findings (4 critical, 4 improvements, 1 nitpick).
Recovery codes are transmitted to user storage as plaintext JSON instead of the hashed credential, introducing a defense-in-depth regression for MFA backup factors alongside an unguarded Optional.get() in the login-form render path and a broken one-time-use invariant in the test storage provider.

## Critical

:red_circle: [security] Recovery codes sent to user storage as plaintext JSON — hashed credential used only for local storage in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:109 (confidence: 97)
`createRecoveryCodesCredential` builds `UserCredentialModel("", credentialModel.getType(), recoveryCodesJson)` where `recoveryCodesJson` is `JsonSerialization.writeValueAsString(generatedCodes)` — the raw plaintext list of recovery codes. The already-hashed `RecoveryAuthnCodesCredentialModel` (whose codes are PBKDF2-hashed via `RecoveryAuthnCodesUtils.hashRawCode`) is passed only to the local provider path. Any user-storage provider accepting this input will persist recovery codes as plaintext, and the standard Keycloak verifier (which expects hashed secretData) will never match for federated users. Recovery codes are full MFA authentication factors; a compromised federated store yields working MFA bypass tokens for every federated user. This is a defense-in-depth regression of the same class as storing passwords in plaintext.
```suggestion
public static void createRecoveryCodesCredential(KeycloakSession session, RealmModel realm,
        UserModel user, RecoveryAuthnCodesCredentialModel credentialModel, List<String> generatedCodes) {
    var provider = session.getProvider(CredentialProvider.class,
            RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID);
    // credentialModel already contains PBKDF2-hashed codes via createFromValues(...)
    // Pass the hashed secretData payload, NOT the raw generatedCodes list.
    UserCredentialModel hashed = new UserCredentialModel("", credentialModel.getType(),
            credentialModel.getSecretData());
    boolean userStorageCreated = user.credentialManager().updateCredential(hashed);
    if (userStorageCreated) {
        logger.debugf("Created RecoveryCodes credential for user '%s' in user storage", user.getUsername());
    } else {
        provider.createCredential(realm, user, credentialModel);
    }
}
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cwe.mitre.org/data/definitions/256.html, https://cwe.mitre.org/data/definitions/522.html]

:red_circle: [security] BackwardsCompatibilityUserStorage persists recovery codes as plaintext and validates with non-constant-time string equality in testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:189 (confidence: 95)
The `updateCredential` branch for `RecoveryAuthnCodesCredentialModel.TYPE` executes `recoveryCodesModel.setCredentialData(input.getChallengeResponse())` with no hashing. The `isValid` method then deserializes that JSON and calls `generatedKeys.stream().anyMatch(key -> key.equals(input.getChallengeResponse()))` — direct plaintext string equality. This reference implementation becomes the blueprint other SPI implementors copy when adding recovery-code support to their user-storage providers, institutionalizing plaintext MFA-backup-code storage across the ecosystem. The plaintext comparison is also not constant-time, introducing a timing side-channel.
```suggestion
} else if (input.getType().equals(RecoveryAuthnCodesCredentialModel.TYPE)) {
    // Hash each raw code with PBKDF2 before persisting (mirrors OTP branch above).
    List<String> rawCodes = JsonSerialization.readValue(input.getChallengeResponse(), List.class);
    List<String> hashed = rawCodes.stream()
            .map(RecoveryAuthnCodesUtils::hashRawCode)
            .collect(Collectors.toList());
    recoveryCodesModel.setCredentialData(JsonSerialization.writeValueAsString(hashed));
    // ... continue as before
}

// in isValid:
List<String> storedHashes = JsonSerialization.readValue(storedRecoveryKeys.getCredentialData(), List.class);
String submittedHash = RecoveryAuthnCodesUtils.hashRawCode(input.getChallengeResponse());
return storedHashes.stream().anyMatch(h -> MessageDigest.isEqual(
        h.getBytes(StandardCharsets.UTF_8), submittedHash.getBytes(StandardCharsets.UTF_8)));
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cwe.mitre.org/data/definitions/208.html, https://cwe.mitre.org/data/definitions/759.html]

:red_circle: [security] Unguarded Optional.get() in login-form render path causes NoSuchElementException / HTTP 500 in services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java:199 (confidence: 97)
The refactored constructor calls `RecoveryAuthnCodesUtils.getCredential(user).get()` with no `isPresent()` or `orElseThrow` guard. `getCredential` now has two lookup paths (federated stream first, then stored-by-type); either may return empty when a credential is absent, deleted mid-session, or belongs to a user migrated from a federated store. An empty Optional throws `NoSuchElementException` during login-form rendering, producing an HTTP 500 with a server stack trace. The sibling call site in `RecoveryAuthnCodesFormAuthenticator` correctly guards with `isPresent()` — this site is inconsistently unguarded. It is an unauthenticated DoS vector for any user routed into the recovery-code form without an enrolled credential.
```suggestion
public RecoveryAuthnCodeInputLoginBean(KeycloakSession session, RealmModel realm, UserModel user) {
    CredentialModel credentialModel = RecoveryAuthnCodesUtils.getCredential(user)
            .orElseThrow(() -> new IllegalStateException(
                    "No recovery authentication codes credential configured"));
    RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel =
            RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModel);
    this.codeNumber = recoveryCodeCredentialModel.getNextRecoveryAuthnCode()
            .orElseThrow(() -> new IllegalStateException(
                    "Recovery codes credential exists but contains no remaining codes"))
            .getNumber();
}
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/248.html, https://cwe.mitre.org/data/definitions/209.html]

:red_circle: [correctness] Recovery code one-time-use invariant broken — used codes remain valid indefinitely in testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:340 (confidence: 95)
`isValid` matches a submitted code via `generatedKeys.stream().anyMatch(key -> key.equals(input.getChallengeResponse()))` and returns true without removing the matched code from the stored list. In the real Keycloak flow, each recovery code must be invalidated after first use. This implementation silently permits indefinite reuse of any recovery code. Any integration test that relies on this provider to verify the one-time-use invariant will produce a false positive, masking the defect. Because this PR is the one that wires the federated-codes path end-to-end, the first production user-storage integration inherits this broken semantics.
```suggestion
} else if (input.getType().equals(RecoveryAuthnCodesCredentialModel.TYPE)) {
    CredentialModel storedRecoveryKeys = myUser.recoveryCodes;
    if (storedRecoveryKeys == null) {
        log.warnf("No recovery credential for user %s", user.getUsername());
        return false;
    }
    List<String> generatedKeys;
    try {
        generatedKeys = new ArrayList<>(
                JsonSerialization.readValue(storedRecoveryKeys.getCredentialData(), List.class));
    } catch (IOException e) {
        log.warnf("Cannot deserialize recovery keys for user %s", user.getUsername());
        return false;
    }
    String submitted = input.getChallengeResponse();
    boolean removed = generatedKeys.removeIf(k -> k.equals(submitted));
    if (removed) {
        try {
            storedRecoveryKeys.setCredentialData(JsonSerialization.writeValueAsString(generatedKeys));
        } catch (IOException e) {
            log.warnf("Cannot persist consumed recovery key for user %s", user.getUsername());
            return false;
        }
    }
    return removed;
}
```
[References: https://cwe.mitre.org/data/definitions/613.html]

## Improvements

:yellow_circle: [correctness] updateCredential used for credential creation — silent failure if provider does not support create-via-update in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:118 (confidence: 92)
`user.credentialManager().updateCredential(...)` is designed to update an existing credential, not create one. If a user-storage provider does not support credential creation via `updateCredential`, the call returns false and execution falls through with no error; if a provider returns true erroneously without persisting, the credential is silently lost. There is no logging or exception on either failure branch, so partial-failure states are invisible to operators.
```suggestion
boolean userStorageCreated = user.credentialManager().updateCredential(recoveryCodesCredential);
if (userStorageCreated) {
    logger.debugf("Created RecoveryCodes credential for user '%s' in user storage", user.getUsername());
} else {
    logger.debugf("User storage declined RecoveryCodes for user '%s'; creating locally", user.getUsername());
    recoveryCodeCredentialProvider.createCredential(realm, user, credentialModel);
}
```

:yellow_circle: [correctness] getCredential silently shadows local credential when user has credentials in both federated and local storage in server-spi/src/main/java/org/keycloak/models/utils/RecoveryAuthnCodesUtils.java:53 (confidence: 85)
`getCredential` queries the federated stream first and falls back to local. If a user ends up with a recovery credential in both stores (possible via the new `createRecoveryCodesCredential` fall-through path firing for a user who already had a federated credential, or via partial migration), the local credential is silently ignored. Callers receive no indication that a shadow credential exists; the lookup ordering is not contractual and could change.
```suggestion
public static Optional<CredentialModel> getCredential(UserModel user) {
    List<CredentialModel> federated = user.credentialManager()
            .getFederatedCredentialsStream()
            .filter(c -> RecoveryAuthnCodesCredentialModel.TYPE.equals(c.getType()))
            .collect(Collectors.toList());
    List<CredentialModel> local = user.credentialManager()
            .getStoredCredentialsByTypeStream(RecoveryAuthnCodesCredentialModel.TYPE)
            .collect(Collectors.toList());
    if (!federated.isEmpty() && !local.isEmpty()) {
        logger.warnf("User has recovery codes in both federated and local storage; using federated");
    }
    return federated.stream().findFirst().or(() -> local.stream().findFirst());
}
```

:yellow_circle: [testing] No test coverage for local-DB fallback path when user is not in federated storage in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:501 (confidence: 88)
The new test exercises the federated user path only. The local-DB fallback path in `createRecoveryCodesCredential` (the else-branch after `userStorageCreated` is false) is never exercised. Any regression in that branch — which is the path all pre-existing non-federated users take — would go undetected by this suite.
```suggestion
@Test
public void testRecoveryKeysStoredLocallyForNonFederatedUser() throws Exception {
    try {
        configureBrowserFlowWithRecoveryAuthnCodes(testingClient, 0);
        String localUserId = ApiUtil.createUserAndResetPasswordWithAdminClient(
                testRealm(), UserBuilder.create().username("localuser").build(), "pass");
        getCleanup().addUserId(localUserId);
        List<String> keys = setupRecoveryKeysForUserWithRequiredAction(localUserId, true);

        testingClient.server().run(session -> {
            RealmModel realm = session.realms().getRealmByName("test");
            UserModel u = session.users().getUserByUsername(realm, "localuser");
            long count = u.credentialManager()
                    .getStoredCredentialsByTypeStream(RecoveryAuthnCodesCredentialModel.TYPE)
                    .count();
            Assert.assertEquals(1L, count);
        });

        TestAppHelper helper = new TestAppHelper(oauth, loginPage, appPage);
        helper.startLogin("localuser", "pass");
        enterRecoveryCodes(enterRecoveryAuthnCodePage, driver, 0, keys);
        enterRecoveryAuthnCodePage.clickSignInButton();
        appPage.assertCurrent();
        helper.logout();
    } finally {
        BrowserFlowTest.revertFlows(testRealm(), BROWSER_FLOW_WITH_RECOVERY_AUTHN_CODES);
    }
}
```

:yellow_circle: [testing] No negative test — invalid recovery code rejection is not covered in testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java:501 (confidence: 85)
The test suite has no assertion that an incorrect or never-issued recovery code is rejected at login. Without a negative test, a bug in the new `getCredential` lookup chain that silently returns the wrong credential could accept arbitrary inputs and the suite would still pass.
```suggestion
@Test
public void testInvalidRecoveryCodeIsRejected() throws Exception {
    try {
        configureBrowserFlowWithRecoveryAuthnCodes(testingClient, 0);
        String userId = addUserAndResetPassword("otp1", "pass");
        getCleanup().addUserId(userId);
        setupRecoveryKeysForUserWithRequiredAction(userId, true);

        TestAppHelper helper = new TestAppHelper(oauth, loginPage, appPage);
        helper.startLogin("otp1", "pass");
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

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: server-spi-private CredentialHelper is a foundational SPI utility with wide downstream impact; sensitive-path hits on credential / auth paths | Sensitive Paths: `*credential*`, `auth/`
AI-Authored Likelihood: N/A

(14 additional findings below confidence threshold 85)
