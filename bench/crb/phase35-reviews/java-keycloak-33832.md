## Summary
12 files changed, 673 lines added, 18 lines deleted. 8 findings (7 critical, 1 improvement, 0 nitpicks).
Most urgent: new abstract `order()` in `CryptoProvider.java` breaks binary compatibility for all third-party implementors.

## Critical

:red_circle: [correctness] Dead code creates and discards ASN1Encoder objects — no effect on output in authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:478 (confidence: 97)
Lines 478 and 479 construct ASN1Encoder instances, call write() on them, and discard the results. The actual DER sequence is correctly assembled on lines 481-485 using fresh encoders. Lines 478-479 are completely inert. Likely a copy-paste remnant where these were meant to be captured into local variables and passed into writeDerSeq(). Output is currently correct but the dead calls waste allocations and mislead readers.
```suggestion
ASN1Encoder rEncoder = ASN1Encoder.create().write(rBigInteger);
ASN1Encoder sEncoder = ASN1Encoder.create().write(sBigInteger);
return ASN1Encoder.create().writeDerSeq(rEncoder, sEncoder).toByteArray();
```

:red_circle: [correctness] readTagNumber multi-byte tag loop uses OR-then-shift — corrupts tags needing 3 or more continuation bytes in authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:159 (confidence: 95)
The loop body does `tagNo |= (b & 0x7f); tagNo <<= 7;` which places intermediate-byte bits at wrong positions for tags with 3+ continuation bytes. The correct BouncyCastle reference pattern is the atomic shift-then-OR: `tagNo = (tagNo << 7) | (b & 0x7f)`. For 2-byte tags the current code produces the right value coincidentally; for 3+-byte tags it silently produces wrong tag numbers, causing SEQUENCE/INTEGER type checks to accept or reject incorrectly. ECDSA DER signatures use only single-byte tags, so this is not triggered in the current call path, but the method is general-purpose decoder logic that is incorrect for any future consumer.
```suggestion
while ((b >= 0) && ((b & 0x80) != 0)) {
    tagNo = (tagNo << 7) | (b & 0x7f);
    b = read();
}
if (b < 0) throw new EOFException("EOF found inside tag value.");
tagNo = (tagNo << 7) | (b & 0x7f);
```

:red_circle: [testing] No tests for ASN1Decoder error-handling paths — malformed input goes entirely untested in authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:87 (confidence: 95)
ASN1Decoder contains approximately 10 distinct IOException/EOFException branches covering truncated tag, corrupted high-tag-number, negative length, length-exceeds-limit, EOF during read, and wrong tag type. The only test exercises ASN1Decoder indirectly through valid random ECDSA signatures, so every error path is dead code from a coverage perspective. A bug in any of these branches could cause silent truncation or unchecked exceptions propagating to callers.
```suggestion
Add a dedicated ASN1DecoderTest with crafted byte arrays covering: truncated input (stream ends mid-tag), wrong top-level tag byte, length field that exceeds buffer, negative-length-encoding (0x80 byte), and correct rejection of non-SEQUENCE wrappers.
```

:red_circle: [correctness] readSequence does not handle indefinite-length encoding — returns empty list silently or throws unchecked exception in authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:103 (confidence: 92)
readLength() returns -1 for BER indefinite-length (0x80 byte). readSequence() checks `while (length > 0)` which is immediately false for -1, returning an empty list silently without consuming further bytes or signaling an error. If a nested element uses indefinite-length, readNext() calls read(-1) which causes `new byte[-1]` to throw NegativeArraySizeException (unchecked), bypassing all IOException handling. DER is strict definite-length, so any indefinite-length input is malformed and should be rejected with a clear error rather than silent data loss.
```suggestion
if (length < 0) throw new IOException("Indefinite-length encoding is not supported");
```

:red_circle: [testing] Provider-ordering logic — the core behavioral change of this PR — has zero test coverage in common/src/main/java/org/keycloak/common/crypto/CryptoIntegration.java:54 (confidence: 92)
The diff changes detectProvider() from throwing on multiple providers to silently selecting the highest-order provider. The ordering, the sort direction, and the ignored-providers logging path are entirely untested. A reversed comparator or an off-by-one would silently select the wrong provider in production with no failing test to catch it. This behavioral change is the central purpose of the PR yet has no unit test.
```suggestion
Add unit tests: testHigherOrderProviderWins (two stub providers with different order() values, higher returned); testSingleProviderNoException (single provider does not throw as before); testNoProvidersThrows (empty provider list still throws IllegalStateException); testEqualOrdersDeterministic (two providers with equal order() produce a stable, documented result).
```

:red_circle: [correctness] New abstract order() method breaks binary compatibility for all third-party CryptoProvider implementors in common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java:746 (confidence: 90)
order() is added as an abstract interface method with no default implementation. The PR updates the four known implementations (DefaultCryptoProvider, WildFlyElytronCryptoProvider, FIPS1402CryptoProvider, AuthzClientCryptoProvider) but any out-of-tree implementor, test double, or downstream fork compiled against the old interface fails with AbstractMethodError at runtime when `Comparator.comparingInt(CryptoProvider::order)` is invoked, or with a compile error on recompile. CryptoProvider is a public API interface, making this a binary-breaking change. A default return value of 0 preserves backward compatibility and semantically places unknown providers below AuthzClientCryptoProvider (100) and Default/FIPS (200).
```suggestion
default int order() { return 0; }
```
[References: JLS §13.5.3 — Interface Member Declarations]

:red_circle: [correctness] CryptoIntegration.init() called unconditionally on every AuthzClient.create() — unsafe when already initialized in authz/client/src/main/java/org/keycloak/authorization/client/AuthzClient.java:94 (confidence: 90)
AuthzClient.create() now calls `CryptoIntegration.init(AuthzClient.class.getClassLoader())` on every invocation without checking whether a provider is already set. If the server (or a prior library call) has already initialized CryptoIntegration with FIPS1402Provider or DefaultCryptoProvider, this second call re-runs detectProvider using the authz-client ClassLoader. Depending on init()'s idempotence: (a) it may overwrite the active provider, (b) it may expose a brief window where concurrent threads see a mutating provider reference, or (c) it may throw if init() enforces strict single-initialization. FIPS deployments are particularly at risk because the server-side FIPS provider may not be reachable from authz-client's ClassLoader, allowing a lower-assurance provider to displace it. The PR does not ship a guard equivalent to `if (isProviderSet()) return`.
```suggestion
if (!CryptoIntegration.isCryptoProviderSet()) {
    CryptoIntegration.init(AuthzClient.class.getClassLoader());
}
```

## Improvements

:yellow_circle: [testing] ECDSA conversion boundary cases with zero-padded or sign-bit-set component bytes not tested in authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:439 (confidence: 88)
integerToBytes() has two non-trivial branches: (1) BigInteger.toByteArray() returns one extra sign-extension byte (the 33-byte case for ES256), and (2) BigInteger.toByteArray() returns fewer bytes than qLength (leading-zero component, value near zero). The round-trip test uses a randomly generated signature each run and is statistically unlikely to hit these boundary values. An off-by-one in arraycopy would produce silently incorrect signatures with no test failure.
```suggestion
Add deterministic boundary-value tests: rsConcat with r component having high bit set (0x80 leading, triggering sign-extension strip branch); rsConcat with r component shorter than qLength (leading-zero padding branch). Repeat for ES256, ES384, and ES512 curve sizes.
```

## Risk Metadata
Risk Score: 69/100 (HIGH) | Blast Radius: CryptoProvider/CryptoIntegration touch common module (25+ transitive importers) | Sensitive Paths: crypto/default/, crypto/elytron/, crypto/fips1402/ — core cryptographic subsystem
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
