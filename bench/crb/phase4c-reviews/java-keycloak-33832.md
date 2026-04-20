## Summary
12 files changed, 683 lines added, 9 lines deleted. 6 findings (1 critical, 5 improvements).
Adds an `AuthzClientCryptoProvider` so the `authz-client` can run without BouncyCastle; introduces a new `CryptoProvider.order()` method and switches `CryptoIntegration.detectProvider` from fail-fast-on-multiple to highest-order-wins. The behavioral and API changes warrant attention even though the ASN.1 codec and ECDSA plumbing look correct.

## Critical
:red_circle: [cross-file-impact] New abstract method `order()` added to public `CryptoProvider` interface breaks binary/source compatibility in `common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java:39-45` (confidence: 92)
`CryptoProvider` is a public SPI (loaded via `ServiceLoader` and documented to be implementable by consumers — see the `META-INF/services/org.keycloak.common.crypto.CryptoProvider` registration pattern this PR itself uses). Adding `int order();` with no `default` implementation forces every downstream implementor (Keycloak extensions, third-party providers, forks productizing Keycloak) to recompile and add an `order()` method or face `AbstractMethodError` at runtime and compile failure on rebuild. All four first-party providers in this PR are updated, but external implementors are not — and this is an OSS SPI.
```suggestion
    /**
     * Order of this provider. This allows to specify which CryptoProvider will have preference in case that more of them are on the classpath.
     *
     * The higher number has preference over the lower number.
     */
    default int order() {
        return 0;
    }
```
[References: https://docs.oracle.com/javase/specs/jls/se17/html/jls-13.html#jls-13.5.6 (binary compatibility for interfaces)]

## Improvements
:yellow_circle: [correctness] `CryptoIntegration.init(...)` unconditionally called from `AuthzClient.create(Configuration)` mutates global static state in `authz/client/src/main/java/org/keycloak/authorization/client/AuthzClient.java:94` (confidence: 84)
`CryptoIntegration.init` writes to the static `CryptoProvider provider` field in `CryptoIntegration`. When `authz-client` is embedded in a host application that has already initialized Keycloak's crypto (e.g. a Keycloak server, a Quarkus app using `keycloak-core`, or a FIPS-configured deployment), every call to `AuthzClient.create(Configuration)` re-runs `ServiceLoader` against the `AuthzClient` class's classloader and can overwrite the previously selected provider. Since this overload is the one every other `create(...)` overload funnels into, the side effect fires on every client construction. At minimum, guard with `if (CryptoIntegration.getProvider() == null)` or move initialization to a static initializer; document the contract explicitly.
```suggestion
    public static AuthzClient create(Configuration configuration) {
        if (CryptoIntegration.getProvider() == null) {
            CryptoIntegration.init(AuthzClient.class.getClassLoader());
        }
        return new AuthzClient(configuration);
    }
```

:yellow_circle: [correctness] Silent loss of fail-fast safety net when multiple `CryptoProvider`s are on the classpath in `common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:56-73` (confidence: 80)
The previous implementation threw `IllegalStateException` with the list of providers when more than one was present, which caught classpath misconfigurations (for example, an accidental drag-in of both `crypto-default` and `crypto-fips1402` in the same deployment). The new implementation only logs the ignored providers at `debug` level, which is off by default in production. In a FIPS deployment where a non-FIPS provider leaks onto the classpath and happens to have equal `order()` (both are 200 in this PR), the selection becomes `ServiceLoader` iteration order — non-deterministic across JVMs. Raise the "ignored providers" log to `warn`, or make equal-order collision still throw.
```suggestion
            logger.debugf("Detected crypto provider: %s", foundProviders.get(0).getClass().getName());
            if (foundProviders.size() > 1) {
                StringBuilder builder = new StringBuilder("Ignored crypto providers: ");
                for (int i = 1 ; i < foundProviders.size() ; i++) {
                    builder.append(foundProviders.get(i).getClass().getName()).append(", ");
                }
                logger.warn(builder.toString());
                if (foundProviders.get(0).order() == foundProviders.get(1).order()) {
                    throw new IllegalStateException("Multiple CryptoProviders with equal order found on classpath: " + foundProviders);
                }
            }
            return foundProviders.get(0);
```

:yellow_circle: [consistency] Misleading semantics of `getBouncyCastleProvider()` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:56-63` (confidence: 78)
The method is named `getBouncyCastleProvider` and is expected (per its use in `DefaultCryptoProvider`/`FIPS1402Provider`) to return a BouncyCastle `Provider`. This implementation returns whatever `Provider` backs the default `KeyStore` type — on stock OpenJDK that is SUN, not BouncyCastle. Any caller that keys off provider name (`provider.getName().startsWith("BC")`, which is a common BC idiom) will silently take the wrong branch. Either return `null` and document that ECDSA is the only supported flow, or rename the interface method to something truthful like `getCryptoJcaProvider()` while you are already changing the SPI shape.

:yellow_circle: [correctness] Dead code in `concatenatedRSToASN1DER` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:114-120` (confidence: 95)
Two `ASN1Encoder.create().write(rBigInteger)` / `.write(sBigInteger)` statements create throwaway encoders whose return values are discarded — they do nothing observable. Likely leftover from a refactor where the encoder was not yet chained. Remove.
```suggestion
            public byte[] concatenatedRSToASN1DER(byte[] signature, int signLength) throws IOException {
                int len = signLength / 2;
                int arraySize = len + 1;

                byte[] r = new byte[arraySize];
                byte[] s = new byte[arraySize];
                System.arraycopy(signature, 0, r, 1, len);
                System.arraycopy(signature, len, s, 1, len);
                BigInteger rBigInteger = new BigInteger(r);
                BigInteger sBigInteger = new BigInteger(s);

                return ASN1Encoder.create()
                        .writeDerSeq(
                                ASN1Encoder.create().write(rBigInteger),
                                ASN1Encoder.create().write(sBigInteger))
                        .toByteArray();
            }
```

:yellow_circle: [correctness] Fragile `UnsupportedOperationException` stubs in 14 of 18 `CryptoProvider` methods in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java` (confidence: 72)
The provider only supports `getEcdsaCryptoProvider`, `getBouncyCastleProvider`, and `getKeyStore`; the remaining 14 methods throw `UnsupportedOperationException("Not supported yet.")`. This is an implicit, undeclared contract — if any shared code in `common` / `authz-client` (now or after future refactors) calls, say, `getX509CertFactory` or `getPemUtils` through `CryptoIntegration.getProvider()` on an authz-client-only classpath, the failure is a late-bound `UnsupportedOperationException` with a misleading message ("Not supported yet." implies "coming soon", rather than "not part of this provider's contract"). At minimum, give each stub a clearer message (`"AuthzClientCryptoProvider does not provide X; add BouncyCastle on the classpath if required"`); ideally, audit `authz-client` transitive call sites to confirm none of these methods are reachable.

## Risk Metadata
Risk Score: 65/100 (HIGH) | Blast Radius: public SPI change affecting all `CryptoProvider` implementors + every `AuthzClient.create` caller; crypto/auth-sensitive code paths | Sensitive Paths: `crypto/*`, `authz/client/*`, `common/.../crypto/*`
AI-Authored Likelihood: LOW (idiomatic, matches the existing Keycloak codebase style — rmartinc attribution is consistent with prior ASN.1 work in Keycloak)
