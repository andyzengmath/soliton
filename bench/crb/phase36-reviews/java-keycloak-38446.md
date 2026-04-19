## Summary
8 files changed, 256 lines added, 31 lines deleted. 3 findings (0 critical, 3 improvements).
Externalizes recovery-codes credential management to user storage providers, mirroring the existing OTP pattern; logic is sound and well-tested, but a few latent NPE/robustness issues remain.

## Improvements
:yellow_circle: [correctness] Unguarded `Optional.get()` on missing recovery-codes credential in `RecoveryAuthnCodeInputLoginBean` in services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java:18 (confidence: 85)
`RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModelOpt.get())` throws `NoSuchElementException` if the user has no recovery-codes credential in either federated or local storage. The refactor preserved the pre-existing unchecked-`get()` pattern, but the new dual-source lookup widens the surface where this bean can be constructed (e.g., when a federated provider transiently hides the credential) and should be made defensive.
```suggestion
        Optional<CredentialModel> credentialModelOpt = RecoveryAuthnCodesUtils.getCredential(user);
        if (credentialModelOpt.isEmpty()) {
            throw new IllegalStateException("No recovery-codes credential found for user " + user.getUsername());
        }
        RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel = RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModelOpt.get());
```
<details><summary>More context</summary>

The login bean is instantiated during the recovery-codes form rendering. The old code (`findFirst().get()` on stored credentials) had the same latent throw, but replacing it with a chained federated+stored lookup is a good opportunity to return a clear error instead of a raw `NoSuchElementException` bubbling up into the Freemarker template layer, which is hard to diagnose for operators.
</details>

:yellow_circle: [correctness] JSON serialization failure wrapped in bare `RuntimeException` in `CredentialHelper.createRecoveryCodesCredential` in server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java:115 (confidence: 85)
The new helper catches `IOException` from `JsonSerialization.writeValueAsString(generatedCodes)` and rethrows as a naked `RuntimeException(e)` with no context, losing the user/realm/operation that failed. Either propagate a typed `ModelException` or at least add a contextual message so operators can correlate the failure with the credential-registration required action.
```suggestion
        try {
            recoveryCodesJson = JsonSerialization.writeValueAsString(generatedCodes);
        } catch (IOException e) {
            throw new ModelException("Failed to serialize recovery codes for user " + user.getUsername(), e);
        }
```
<details><summary>More context</summary>

`JsonSerialization.writeValueAsString` on a `List<String>` is essentially non-failing in practice, but Keycloak's other credential-manipulation helpers (see `CredentialHelper.createOTPCredential` callers) surface failures as `ModelException` so that the authentication-flow layer can render a proper error page. A naked `RuntimeException` here would surface as a 500 with no diagnostic info, and also breaks the convention used elsewhere in this same file.
</details>

:yellow_circle: [correctness] Swallowed deserialization error silently drops recovery-codes credential in `BackwardsCompatibilityUserStorage.getCredentials` in testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java:240 (confidence: 85)
If `JsonSerialization.readValue(myUser.recoveryCodes.getCredentialData(), List.class)` throws, the code logs `"Could not deserialize  credential of type: recovery-codes"` (note the double space and missing user context) and returns the remaining credentials stream without the recovery-codes entry, making the user silently appear to have no recovery-codes credential — which then causes downstream `Optional.get()` calls to NPE rather than report the underlying cause.
```suggestion
            } catch (IOException e) {
                log.errorf(e, "Could not deserialize recovery-codes credential for user '%s'", user.getUsername());
            }
```
<details><summary>More context</summary>

This is test-provider code, but it is the reference implementation used by the new `BackwardsCompatibilityUserStorageTest.testRecoveryKeysSetupAndLogin` test and by any third-party `UserStorageProvider` authors copying the pattern. The combination of (a) swallowing the exception without the stack trace, (b) returning an incomplete stream, and (c) downstream code assuming the Optional is present (see Finding 1 above) turns a recoverable serialization bug into a confusing NPE at the Freemarker layer.
</details>

## Risk Metadata
Risk Score: 48/100 (MEDIUM) | Blast Radius: 8 files across server-spi, server-spi-private, services, and testsuite; shared `CredentialHelper` utility touched; recovery-credentials authentication path affected | Sensitive Paths: server-spi-private/utils/CredentialHelper.java (credential-management helper), authentication/requiredactions/RecoveryAuthnCodesAction.java (required-action executor), authentication/authenticators/browser/RecoveryAuthnCodesFormAuthenticator.java (credential validator)
AI-Authored Likelihood: LOW
