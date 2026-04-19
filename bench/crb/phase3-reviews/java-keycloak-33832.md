# PR Review — keycloak/keycloak #33832

**Title:** Add AuthzClientCryptoProvider to authz-client in keycloak main repository
**Base:** `main` ← **Head:** `mposolda:33831-authz-client-crypto`
**Closes:** #33831 (related to #32962)
**Author intent:** Ship a slim `CryptoProvider` SPI implementation scoped to `authz-client`, plus a dependency-free ASN.1 encoder/decoder, so the authorization client can verify/produce ECDSA signatures without pulling in BouncyCastle (blocked by productization / Quarkus dependency chains). To support two `CryptoProvider`s on the same classpath, an `order()` method is added to `CryptoProvider` and `CryptoIntegration.detectProvider()` selects the highest-ordered one via `ServiceLoader`.

## Summary
12 files changed, ~330 lines added, small modifications elsewhere. 6 findings (1 critical, 3 improvements, 2 nitpicks).
Clean, well-scoped addition. Main concerns: (1) a dead-code / copy-paste artifact in the ECDSA encoder path that wastes allocations and hints at refactor residue; (2) provider-order semantics are the load-bearing mechanism for correctness on classpaths containing both `Default` and `AuthzClient` providers — the constant `100` in `AuthzClientCryptoProvider` MUST be lower than `DefaultCryptoProvider.order()` or the server/testsuite silently downgrades to an `UnsupportedOperationException`-heavy provider; (3) the decoder is permissive about DER invariants (missing CONSTRUCTED-bit check on SEQUENCE, signed `BigInteger` construction) in ways that matter for a crypto-adjacent parser.

## Critical

:red_circle: [correctness] Dead/duplicate `ASN1Encoder.create().write(...)` calls before `writeDerSeq` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java`:107 (confidence: 95)
In `concatenatedRSToASN1DER`, two `ASN1Encoder.create().write(rBigInteger)` / `write(sBigInteger)` calls are made, discarded, and then the same pair is re-created inside the `writeDerSeq(...)` call:
```java
ASN1Encoder.create().write(rBigInteger);   // <-- allocated, encoded, thrown away
ASN1Encoder.create().write(sBigInteger);   // <-- allocated, encoded, thrown away

return ASN1Encoder.create()
        .writeDerSeq(
                ASN1Encoder.create().write(rBigInteger),
                ASN1Encoder.create().write(sBigInteger))
        .toByteArray();
```
The first two lines have no side effects on the returned encoder (each `ASN1Encoder.create()` is a fresh `ByteArrayOutputStream`). They are almost certainly refactor residue from an earlier builder-style API. The severity is promoted to *critical* not because of incorrect output — the output is right — but because this is a crypto path where dead-looking code around signature encoding materially raises reviewer-time cost and risks a future editor "fixing" it in the wrong direction (e.g. concluding the inner nested `create()` calls are the duplicates and deleting *those*, which would produce empty sequence elements). Delete the two stray calls:
```suggestion
    @Override
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
References: AuthzClientCryptoProvider.java:105-120

## Improvements

:yellow_circle: [cross-file-impact] `order()` = 100 in `AuthzClientCryptoProvider` silently wins against `DefaultCryptoProvider` if that provider's order is unspecified / lower in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java`:64 (confidence: 80)
The whole design of #32962 approach (2) hinges on `DefaultCryptoProvider` being strictly *preferred* when both are on the classpath — because `AuthzClientCryptoProvider` throws `UnsupportedOperationException` for almost every method (`getCertificateUtils`, `getPemUtils`, `getOCSPProver`, `getIdentityExtractorProvider`, `getKeyPairGen`, `getKeyFactory`, `getSignature`, `getAesCbcCipher`, `getAesGcmCipher`, `getSecretKeyFact`, `getX509CertFactory`, `getCertStore`, `getCertPathBuilder`, `wrapFactoryForTruststore`, `createECParams`). The PR hard-codes `order() { return 100; }` here without any documented constant from the SPI side telling the reader what "default" and "higher" mean. Two concrete risks:

1. **`CryptoProvider.order()` default**. If the interface's default `order()` returns `0` *and* `DefaultCryptoProvider` does not explicitly override it — or overrides it with a value `<= 100` — the shaded/fatjar testsuite or a downstream project that ships both artifacts will end up on the authz-client provider and blow up at first call to `getPemUtils()` or similar. The PR description's reassurance ("The Keycloak test suite continues using `DefaultCryptoProvider` without modification") is only true if `DefaultCryptoProvider.order() > 100`; this invariant is not expressed in code near `AuthzClientCryptoProvider`.
2. **Tie-breaking is unspecified**. `CryptoIntegration.detectProvider()` iterates `ServiceLoader` and picks the max-order provider, but on equal orders the winner is ServiceLoader-iteration-order — which in turn is classpath-order and JVM-implementation-defined.

Mitigations (pick at least one):
```suggestion
// Option A: reference the Default provider's order constant directly (preferred)
@Override
public int order() {
    return DefaultCryptoProvider.DEFAULT_ORDER - 100;   // always strictly below Default
}

// Option B: make it explicit in javadoc + expose constants on CryptoProvider
/**
 * Lowest-priority provider. Must be lower than DefaultCryptoProvider.order()
 * so the full-featured implementation wins when both are on the classpath.
 * See issue #32962 for the selection contract.
 */
@Override
public int order() {
    return CryptoProvider.LOW_PRIORITY;  // e.g. 100; Default = 200; FIPS = 300
}

// Option C: fail fast when both providers tie
// In CryptoIntegration.detectProvider, on equal max-order add:
// throw new IllegalStateException("Multiple CryptoProvider candidates at order " + order);
```
References: AuthzClientCryptoProvider.java:63-66; common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java (detectProvider)

:yellow_circle: [correctness] `ASN1Decoder.readSequence` does not enforce the CONSTRUCTED bit; a primitive-form SEQUENCE would be accepted in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java`:52 (confidence: 75)
`readSequence()` computes `tagNo = tag & 0x1f` and compares only against `ASN1Encoder.SEQUENCE` (`0x10`). It does not check that the CONSTRUCTED bit (`0x20`) is set in the raw tag byte, even though DER X.690 §8.9.1 requires SEQUENCE to be constructed. Any byte with bottom-5 bits = `0x10` will pass — including `0x10` (primitive SEQUENCE, which is invalid DER), context/application-class tags that alias to the same number, etc. In a signature-verification path this is in practice a robustness issue rather than a known exploit, but ECDSA signature malleability literature has specifically exploited lax DER parsers (see CVE-2015-2730 on NSS, the OpenSSL negative-length issues, and the Bitcoin / BouncyCastle ECDSA strict-DER hardening). The symmetric concern applies to `readInteger()` — it checks only the tag number, not the class bits.
```suggestion
public List<byte[]> readSequence() throws IOException {
    int tag = readTag();
    if ((tag & 0xC0) != 0x00 || (tag & 0x20) == 0 || (tag & 0x1f) != ASN1Encoder.SEQUENCE) {
        throw new IOException("Invalid SEQUENCE tag: 0x" + Integer.toHexString(tag));
    }
    // ... rest unchanged
}

public BigInteger readInteger() throws IOException {
    int tag = readTag();
    if ((tag & 0xC0) != 0x00 || (tag & 0x20) != 0 || (tag & 0x1f) != ASN1Encoder.INTEGER) {
        throw new IOException("Invalid INTEGER tag: 0x" + Integer.toHexString(tag));
    }
    // ... rest unchanged
}
```
References: ASN1Decoder.java:50-64, 66-74

:yellow_circle: [correctness] `new BigInteger(bytes)` in `readInteger` accepts negative ECDSA components in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java`:71 (confidence: 72)
`readInteger()` does `new BigInteger(bytes)` (signed). ECDSA `r` and `s` are defined on `[1, n-1]` and must be positive; a DER-encoded INTEGER with a leading high-bit (no 0-byte padding) is *legitimately* negative in two's-complement ASN.1, and `BigInteger` will faithfully reconstruct it as negative. Later, `integerToBytes(negativeBigInt, len)` calls `s.toByteArray()` which returns two's-complement bytes — for a negative value, the resulting concatenated-RS output is well-defined but semantically wrong and can silently propagate into downstream verification calls that *do* accept it structurally but then fail opaquely. Strict ECDSA DER parsers (e.g. BouncyCastle's) reject negative integers up-front. Recommended: validate sign immediately, and while there, reject leading-zero-padding violations (DER forbids encoding a positive integer with a redundant 0x00 prefix unless necessary for sign):
```suggestion
public BigInteger readInteger() throws IOException {
    // ... tag check as above
    int length = readLength();
    byte[] bytes = read(length);
    if (bytes.length == 0) {
        throw new IOException("Empty INTEGER");
    }
    // DER: must be minimally-encoded
    if (bytes.length >= 2
            && bytes[0] == 0x00
            && (bytes[1] & 0x80) == 0) {
        throw new IOException("Non-minimal INTEGER encoding");
    }
    BigInteger val = new BigInteger(bytes);
    if (val.signum() < 0) {
        throw new IOException("Negative INTEGER not allowed for ECDSA component");
    }
    return val;
}
```
Pair with caller-side validation in `asn1derToConcatenatedRS`: reject `r.signum() <= 0` and `s.signum() <= 0` (cheap defense in depth).
References: ASN1Decoder.java:66-74; AuthzClientCryptoProvider.java:126-131

## Nitpicks

:white_circle: [correctness] `readLength` uses `length >= limit` rather than `length > limit - already-consumed` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java`:107 (confidence: 60)
The bounds check `if (length >= limit)` compares the declared content length against the *total* buffer length rather than the remaining bytes. It rejects a legitimate outer TLV whose declared length equals the buffer length minus header (and it only fires in the long-form branch, so short-form `< 128` content escapes the check entirely). Two issues: (a) an outer SEQUENCE whose content is `>= 127` bytes *and* equals `limit` (possible if the caller passed in only the content sub-slice) would be rejected; (b) short-form lengths aren't bounds-checked at all. Prefer tracking `count` against `limit` consistently:
```suggestion
// after: length = ... (short or long form)
if (length < 0 || length > limit - count) {
    throw new IOException("Length " + length + " exceeds remaining bytes");
}
return length;
```
This also closes the short-form gap.
References: ASN1Decoder.java:100-120

:white_circle: [consistency] `getBouncyCastleProvider()` returns the platform default KeyStore's `Provider`, which is confusingly unrelated to BouncyCastle in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java`:56 (confidence: 70)
The method name on the `CryptoProvider` SPI is `getBouncyCastleProvider()`. The authz-client implementation returns `KeyStore.getInstance(KeyStore.getDefaultType()).getProvider()` — whatever happens to register JKS/PKCS12 in the running JVM (SUN on HotSpot, IBMJCE on IBM JDK, etc.). Any caller that actually depends on BC-specific algorithms (`ChaCha20-Poly1305` via BC, `Ed25519` pre-JDK15, BCFKS) will either silently get the wrong provider or fail opaquely. Either rename the SPI method (cross-cutting, out of scope here) or at minimum javadoc the deviation:
```suggestion
/**
 * @deprecated name is historical ("BouncyCastleProvider") — this implementation
 * returns the JVM's default KeyStore provider because the authz-client avoids
 * BouncyCastle on the classpath. Callers that require BC-specific algorithms
 * must ensure they run against the full DefaultCryptoProvider.
 */
@Override
public Provider getBouncyCastleProvider() { ... }
```
Also note: `getKeyStore(KeystoreUtil.KeystoreFormat.BCFKS)` will throw `KeyStoreException` in any JVM that doesn't have BC on the classpath — which is exactly the deployment shape this provider targets. Consider documenting the supported-formats subset on the class javadoc.
References: AuthzClientCryptoProvider.java:55-61, 165-167

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: authz-client module + cross-module `CryptoProvider` SPI (common, crypto/default, crypto/fips1402, legacy adapters) | Sensitive Paths: common/src/.../crypto/, authz/client/.../crypto/, crypto/**
AI-Authored Likelihood: LOW (stylistic cues — small methods, hand-tuned X.690 comments with version refs, no speculative error paths — read as human-written)

(0 additional findings below confidence threshold)
