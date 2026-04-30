## Summary
8 files changed, 256 lines added, 31 lines deleted. 2 findings (0 critical, 2 improvements, 0 nitpicks).
Externalises recovery-authentication-code storage into user-storage providers (mirrors the existing OTP pattern); approach is sound and covered by an integration test, but two unguarded error paths warrant tightening.

## Improvements
:yellow_circle: [correctness] Unguarded `Optional.get()` can throw `NoSuchElementException` in `services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java:21` (confidence: 88)
`RecoveryAuthnCodesUtils.getCredential(user)` returns `Optional<CredentialModel>`, but the bean immediately calls `credentialModelOpt.get()` without an `isPresent()` check. The previous implementation used `.findFirst().get()` and had the same hazard, but this rewrite is the right moment to harden it: the lookup now fans out to both federated and stored credential streams and silently empty results are conceivable (e.g. credential deleted between authenticator validation and form rendering, or a misconfigured user-storage provider). When that happens the constructor throws a 500 from inside Freemarker rendering instead of degrading gracefully.
```suggestion
        Optional<CredentialModel> credentialModelOpt = RecoveryAuthnCodesUtils.getCredential(user);
        if (credentialModelOpt.isEmpty()) {
            throw new IllegalStateException("Recovery authentication codes credential not found for user " + user.getUsername());
        }
        RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel =
                RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModelOpt.get());
```

:yellow_circle: [correctness] Generic `RuntimeException` swallows context for `JsonSerialization` failure in `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:117` (confidence: 86)
`createRecoveryCodesCredential` catches `IOException` from `JsonSerialization.writeValueAsString(generatedCodes)` and rethrows as a bare `RuntimeException(e)`. Because this method is invoked from `RecoveryAuthnCodesAction.processAction` while a user is mid-required-action, a serialization failure surfaces as an opaque 500 with no audit trail and no Keycloak-specific exception type. Other credential helpers in this file (and the rest of `services/`) raise `ModelException` / `RuntimeException` with a descriptive message; doing the same here keeps log triage and `EventBuilder` error reporting consistent. Equally important: `JsonSerialization.writeValueAsString` on a `List<String>` of generated codes effectively cannot fail under normal conditions, so the catch clause is mostly dead code — promoting it to a typed exception with a message keeps it honest if the input ever becomes more complex.
```suggestion
        try {
            recoveryCodesJson = JsonSerialization.writeValueAsString(generatedCodes);
        } catch (IOException e) {
            throw new RuntimeException("Failed to serialize recovery authentication codes for user '"
                    + user.getUsername() + "'", e);
        }
```

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: scoped to recovery-authentication-codes credential path; touches `CredentialHelper` (server-spi-private) and one Freemarker login bean, so any regression is contained to users enrolling/using recovery codes | Sensitive Paths: `services/.../authentication/`, `server-spi-private/.../utils/CredentialHelper.java` (credential management surface) | Test Coverage: new integration test `BackwardsCompatibilityUserStorageTest#testRecoveryKeysSetupAndLogin` exercises the user-storage path end-to-end
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
