# PR Review: keycloak/keycloak#38446 — Create recovery keys in user storage or local

**Base:** `main` ← **Head:** `issue-38445`
**Scope:** 8 files, +256 / -31 lines
**Closes:** https://github.com/keycloak/keycloak/issues/38445

## Summary
8 files changed, 256 lines added, 31 lines deleted. 9 findings (2 critical, 5 improvements, 2 nitpicks).
Enables recovery-code credentials to live in a federated user-storage provider instead of always in Keycloak's local DB, with a single consolidated helper (`CredentialHelper.createRecoveryCodesCredential`) and a lookup utility (`RecoveryAuthnCodesUtils.getCredential`) that prefers federated credentials and falls back to local. Production call sites (`RecoveryAuthnCodesAction`, `RecoveryAuthnCodesFormAuthenticator`, `RecoveryAuthnCodeInputLoginBean`) are migrated to the new helpers, and the `BackwardsCompatibilityUserStorage` test fixture gains recovery-code support plus an integration test. Security-adjacent; backward-compatibility of existing local-only installs looks safe but a couple of edges around absent-credential handling, raw-code propagation to federation providers, and a missing fallback test need attention.

## Critical

:red_circle: [security] Plaintext recovery codes forwarded to federated user-storage provider in `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java`:113-127 (confidence: 85)

`createRecoveryCodesCredential` serialises the raw `List<String> generatedCodes` to JSON and hands it to `user.credentialManager().updateCredential(new UserCredentialModel("", type, recoveryCodesJson))`. When the backing `CredentialInputUpdater` is a federated provider that accepts the type (i.e. `userStorageCreated == true`), the raw codes leave Keycloak in cleartext. The existing local-only path (`RecoveryAuthnCodesCredentialProvider.createCredential`) stores only PBKDF2 hashes (see `RecoveryAuthnCodesCredentialModel.createFromValues` → `hashRawCode`), so this PR silently changes the trust model: anyone integrating an LDAP/JPA/SPI federation store that supports the `kc-recovery-authn-codes` type now receives plaintext recovery secrets. That should at minimum be called out in the javadoc (and ideally in release notes), and the helper should prefer passing already-hashed material where possible, or the API should require the federation provider to opt in to raw-code handling with a documented contract.

```suggestion
    /**
     * Create RecoveryCodes credential either in user storage (if the configured provider supports
     * the type) or in local Keycloak storage.
     *
     * <p><strong>Security note:</strong> when a federated user-storage provider handles the write,
     * the raw recovery codes are passed to it as the challenge response. Federation providers are
     * therefore expected to hash codes at rest; callers delegating to untrusted providers should
     * not use this method.
     */
    public static void createRecoveryCodesCredential(KeycloakSession session, RealmModel realm, UserModel user,
                                                     RecoveryAuthnCodesCredentialModel credentialModel, List<String> generatedCodes) {
        var recoveryCodeCredentialProvider = session.getProvider(CredentialProvider.class,
                RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID);
        if (recoveryCodeCredentialProvider == null) {
            throw new IllegalStateException("Recovery-codes credential provider is not registered");
        }
        String recoveryCodesJson;
        try {
            recoveryCodesJson = JsonSerialization.writeValueAsString(generatedCodes);
        } catch (IOException e) {
            throw new RuntimeException("Failed to serialize recovery codes", e);
        }
        UserCredentialModel recoveryCodesCredential = new UserCredentialModel("", credentialModel.getType(), recoveryCodesJson);
        if (user.credentialManager().updateCredential(recoveryCodesCredential)) {
            logger.debugf("Created RecoveryCodes credential for user '%s' in the user storage", user.getUsername());
        } else {
            recoveryCodeCredentialProvider.createCredential(realm, user, credentialModel);
        }
    }
```

References: `services/src/main/java/org/keycloak/credential/RecoveryAuthnCodesCredentialProvider.java`, `server-spi/src/main/java/org/keycloak/models/credential/RecoveryAuthnCodesCredentialModel.java`

---

:red_circle: [correctness] `credentialModelOpt.get()` with no presence check throws on missing credential in `services/src/main/java/org/keycloak/forms/login/freemarker/model/RecoveryAuthnCodeInputLoginBean.java`:17-21 (confidence: 90)

`RecoveryAuthnCodesUtils.getCredential(user)` returns `Optional<CredentialModel>`, but the bean immediately calls `.get()` without checking `isPresent()`. Previously the bean relied on `.findFirst().get()` against a single storage tier, which is itself fragile but at least colocated with the stream; the new abstraction invites the caller to drop the guard. Under the new semantics the Optional can legitimately be empty — e.g. racing credential deletion, a federated provider that transiently returns nothing, or a flow misconfiguration that routes a user without recovery codes to this page — and the template renderer will surface an opaque `NoSuchElementException: No value present`. This code path runs on every recovery-codes login screen, so the blast radius is a user-facing 500.

```suggestion
    public RecoveryAuthnCodeInputLoginBean(KeycloakSession session, RealmModel realm, UserModel user) {
        CredentialModel credentialModel = RecoveryAuthnCodesUtils.getCredential(user)
                .orElseThrow(() -> new IllegalStateException(
                        "No recovery-codes credential found for user " + user.getUsername()));

        RecoveryAuthnCodesCredentialModel recoveryCodeCredentialModel = RecoveryAuthnCodesCredentialModel.createFromCredentialModel(credentialModel);

        this.codeNumber = recoveryCodeCredentialModel.getNextRecoveryAuthnCode().get().getNumber();
    }
```

---

## Improvements

:yellow_circle: [correctness] Magic string replaces the `PROVIDER_ID` constant in `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java`:114 (confidence: 88)

`session.getProvider(CredentialProvider.class, "keycloak-recovery-authn-codes")` hardcodes the provider id, whereas the code this PR *deletes* in `RecoveryAuthnCodesAction.processAction` used `RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID`. If that constant ever changes (rename, vendor fork, SPI consolidation), this call silently degrades to `null` and we fall into the NPE branch on the next line. Import and reuse the constant.

```suggestion
        var recoveryCodeCredentialProvider = session.getProvider(
                CredentialProvider.class, RecoveryAuthnCodesCredentialProviderFactory.PROVIDER_ID);
```

---

:yellow_circle: [correctness] No null guard before dereferencing the local provider in `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java`:125 (confidence: 80)

When `user.credentialManager().updateCredential(...)` returns `false` we fall through to `recoveryCodeCredentialProvider.createCredential(...)`. If `session.getProvider(...)` returned `null` (feature disabled, SPI not registered, relocation), this throws NPE with no context. Given this method is the single entry point from `RecoveryAuthnCodesAction.processAction`, the failure mode is a 500 during a required-action step. Either fail fast at lookup time (see Critical #1 suggestion) or log and return an actionable error.

---

:yellow_circle: [correctness] Federated-first lookup can mask stale local credentials in `server-spi/src/main/java/org/keycloak/models/utils/RecoveryAuthnCodesUtils.java`:50-60 (confidence: 70)

`getCredential` returns the first federated match and only falls back to local storage when there is no federated match. In mixed states — e.g. a realm that previously stored recovery codes locally, then gained a federation provider that also exposes recovery codes — both tiers can simultaneously hold credentials and the federated one silently wins even if it's older/rotated. Worth documenting the precedence explicitly and considering whether a validity/creation-time tiebreak is more correct. At minimum, add a log line at `debug` level when both tiers hold credentials so admins can diagnose "why don't my codes work" tickets.

---

:yellow_circle: [testing] No coverage for the local-storage fallback path in `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java`:237 (confidence: 85)

`testRecoveryKeysSetupAndLogin` exercises only the federated-success branch (`userStorageCreated == true`). The core motivation of the PR is "user storage **or** local" — there is no test that asserts Keycloak still writes to local DB when the federation provider returns `false` from `updateCredential`, nor a test for the absent-credential branch on login (which is where the Critical `credentialModelOpt.get()` issue would bite). Add:
1. A federation provider that rejects recovery-code updates → expect local credential created (`assertUserHasRecoveryKeysCredentialInUserStorage(false)` + assert local DB has it).
2. A login attempt for a user with no recovery-codes credential → expect the specific failure mode, not a generic 500.

---

:yellow_circle: [correctness] Recovery code validation in the test storage is replayable in `testsuite/integration-arquillian/servers/auth-server/services/testsuite-providers/src/main/java/org/keycloak/testsuite/federation/BackwardsCompatibilityUserStorage.java`:323-338 (confidence: 65)

`isValid` accepts any code that matches any entry in the stored JSON list and never consumes or invalidates the used code. Production `RecoveryAuthnCodesCredentialProvider.isValid` marks entries as consumed, which is the whole point of single-use recovery codes. Because this is a *test* fixture the defect doesn't ship, but the fixture is specifically meant to mirror legacy/federated behaviour — if someone writes a real integration test against replay defences using this fixture, the tests will pass incorrectly. Add a TODO and/or track consumed codes on `MyUser.recoveryCodes`.

---

## Nitpicks

:white_circle: [consistency] Whitespace-only reformatting mixed into the substantive diff in `server-spi-private/src/main/java/org/keycloak/utils/CredentialHelper.java`:72-82, `testsuite/integration-arquillian/tests/base/src/test/java/org/keycloak/testsuite/federation/storage/BackwardsCompatibilityUserStorageTest.java`:328 and 461 (confidence: 95)

The method signature and body of `getConfigurableAuthenticatorFactory` is re-indented from 5 spaces to 4 in `CredentialHelper.java` with no logic change — pure noise that makes the diff harder to review. Same in `BackwardsCompatibilityUserStorageTest` around the `doDelete(...)` chain and the `new TypeReference<>() {}` closer. Split unrelated reformats into a separate commit, or drop them.

---

:white_circle: [consistency] Typo and ambiguous contract in javadoc in `server-spi/src/main/java/org/keycloak/models/utils/RecoveryAuthnCodesUtils.java`:43-48 (confidence: 95)

"a optional  credential model" → "an Optional CredentialModel"; double space after "optional"; and the summary should state the precedence ("federated storage wins over local storage"). Small fix, but this is user-facing SPI javadoc.

---

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 8 files across server-spi, server-spi-private, services, and integration-arquillian testsuite; touches credential creation + validation flow for the `RECOVERY_CODES` feature | Sensitive Paths: `server-spi-private/.../CredentialHelper.java`, `services/.../authenticators/browser/RecoveryAuthnCodesFormAuthenticator.java`, `services/.../requiredactions/RecoveryAuthnCodesAction.java` — credential + auth code paths.
AI-Authored Likelihood: LOW (consistent with keycloak's existing style; one leftover `var` plus the `.get()` smell look human-authored; diff noise from IDE reformatting also fits a human contributor)

**Recommendation:** request-changes — the `credentialModelOpt.get()` NPE and the plaintext-codes-to-federation security surface should be addressed before merge; the missing fallback test is the next most important gap.
