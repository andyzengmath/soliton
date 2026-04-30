## Summary
12 files changed, 673 lines added, 9 lines deleted. 7 findings (1 critical, 6 improvements).
Adds a hand-rolled ASN.1 DER encoder/decoder and a minimal `AuthzClientCryptoProvider` to let `authz-client` perform ECDSA R||S ↔ DER conversion without BouncyCastle, plus an `order()` method on the `CryptoProvider` SPI to disambiguate when multiple providers share a classpath.

## Critical
:red_circle: [cross-file-impact] Non-default abstract method `order()` added to public SPI interface — breaks third-party `CryptoProvider` implementations in `common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java:746` (confidence: 95)
`CryptoProvider` is a public Java interface registered as a Java SPI (`META-INF/services/org.keycloak.common.crypto.CryptoProvider`). The PR adds `int order();` as a plain abstract method (no `default` body). Any third-party implementation that was compiled against the previous version of the interface — including custom Keycloak distributions, FIPS variants, and the separate `keycloak-client` repository called out in the PR description — will fail to compile when recompiled against this version, and existing compiled JARs that don't provide `order()` will throw `java.lang.AbstractMethodError` at runtime the moment `CryptoIntegration.detectProvider` invokes `CryptoProvider::order` inside the new `Comparator.comparingInt(CryptoProvider::order).reversed()` sort. This fires on every `CryptoIntegration.init()` call, including the newly-instrumented `AuthzClient.create(Configuration)` path. Making this a default method is a one-line, binary-compatible alternative.
```suggestion
    /**
     * Order of this provider. This allows to specify which CryptoProvider will have preference in case that more of them are on the classpath.
     *
     * The higher number has preference over the lower number
     */
    default int order() {
        return 0;
    }
```

## Improvements
:yellow_circle: [correctness] `readInteger()` passes `readLength()`'s indefinite-length sentinel (-1) directly to `read()` causing `NegativeArraySizeException` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:113` (confidence: 95)
`readLength()` returns `-1` when it sees the BER indefinite-length octet (`0x80`). `readInteger()` stores that into `length` and immediately calls `read(length)`, which executes `new byte[-1]` and throws `NegativeArraySizeException` — a `RuntimeException`, not `IOException`. Callers that catch only `IOException` (the declared throw of `asn1derToConcatenatedRS`) will not intercept it. Since this is a DER-only decoder, the cleanest fix is to reject indefinite-length at the source.
```suggestion
    int readLength() throws IOException {
        int length = read();
        if (length < 0) {
            throw new EOFException("EOF found when length expected");
        }

        if (length == 0x80) {
            throw new IOException("Indefinite-length encoding is not supported in DER");
        }
        // ... rest unchanged
```

:yellow_circle: [security] `readInteger()` / `readSequence()` accept non-canonical DER and trailing bytes in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:96` (confidence: 85)
`readInteger()` calls `new BigInteger(bytes)` without enforcing canonical DER (no superfluous leading `0x00` unless required for sign, no leading `0xFF` on negatives, no zero-length INTEGER). `readSequence()`'s loop only checks `while (length > 0)` and never asserts that the inner content length exactly equals the declared SEQUENCE length, nor that the underlying buffer was fully consumed. Multiple distinct DER byte strings therefore decode to the same `R||S` output. While this is not a verification bypass — `Signature.verify` still operates on the math — it weakens DER strictness in a security-sensitive code path and silently accepts malformed inputs that BouncyCastle's `ASN1InputStream` would reject. Pair this with a length-mismatch check at the end of `readSequence()` and a "stream fully consumed" assertion in the entry points (`asn1derToConcatenatedRS`).
```suggestion
        int length = readLength();
        if (length <= 0) {
            throw new IOException("Invalid INTEGER length: " + length);
        }
        byte[] bytes = read(length);
        return new BigInteger(bytes);
```

:yellow_circle: [correctness] `readInteger()` with zero-length value throws `NumberFormatException` rather than `IOException` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:113` (confidence: 92)
A DER INTEGER TLV with `length=0` (malformed but constructible from untrusted input) makes `read(0)` return an empty array, and `new BigInteger(new byte[0])` throws `NumberFormatException("Zero length BigInteger")` — an unchecked `RuntimeException`. The method declares `throws IOException`, so callers that wrap parsing in `catch (IOException)` will miss this and an unchecked exception will escape `asn1derToConcatenatedRS`. Add an explicit length guard.
```suggestion
        int length = readLength();
        if (length <= 0) {
            throw new IOException("Invalid INTEGER length: " + length);
        }
        byte[] bytes = read(length);
        return new BigInteger(bytes);
```

:yellow_circle: [correctness] `readTagNumber()` high-tag-number loop has no bound on continuation bytes — silent integer overflow in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java:84` (confidence: 85)
In the high-tag-number form (`tag & 0x1f == 0x1f`), the loop accumulates `tagNo |= (b & 0x7f); tagNo <<= 7;` while the high bit is set, with no iteration cap. Java `int` is 32 bits, so after 5 continuation bytes `tagNo` has been left-shifted 35 bits and silently overflows into a wrong (and potentially negative) value. An attacker supplying a long chain of `0x80` continuation bytes can wrap `tagNo` to match a legitimate value (e.g. `INTEGER=2` or `SEQUENCE=16`), bypassing the tag check in `readSequence()`/`readInteger()`. Standard ASN.1 tag numbers fit in a single byte, so >4 continuation bytes is always invalid.
```suggestion
        if (tagNo == 0x1f) {
            tagNo = 0;
            int b = read();
            if ((b & 0x7f) == 0) {
                throw new IOException("corrupted stream - invalid high tag number found");
            }
            int iterations = 0;
            while ((b >= 0) && ((b & 0x80) != 0)) {
                tagNo |= (b & 0x7f);
                tagNo <<= 7;
                b = read();
                if (++iterations > 4) {
                    throw new IOException("corrupted stream - tag number too large");
                }
            }
            if (b < 0) {
                throw new EOFException("EOF found inside tag value.");
            }
            tagNo |= (b & 0x7f);
        }
```

:yellow_circle: [correctness] Dead code in `concatenatedRSToASN1DER` — two `ASN1Encoder.create().write(...)` results are discarded in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java:114` (confidence: 90)
The two statements `ASN1Encoder.create().write(rBigInteger);` and `ASN1Encoder.create().write(sBigInteger);` build encoders, write to them, and immediately discard the result. The actual encoding happens on the following `writeDerSeq(...)` chain. These look like refactor leftovers — they perform `BigInteger.toByteArray()` and `ByteArrayOutputStream.write` work that is immediately GC'd. In a cryptographic primitive, dead code is a maintainability and audit-trail risk; remove both lines.
```suggestion
                BigInteger rBigInteger = new BigInteger(r);
                BigInteger sBigInteger = new BigInteger(s);

                return ASN1Encoder.create()
                        .writeDerSeq(
                                ASN1Encoder.create().write(rBigInteger),
                                ASN1Encoder.create().write(sBigInteger))
                        .toByteArray();
```

:yellow_circle: [cross-file-impact] `AuthzClient.create(InputStream)` is not modified — verify it routes through `create(Configuration)` so crypto is initialized in `authz/client/src/main/java/org/keycloak/authorization/client/AuthzClient.java:91` (confidence: 85)
`CryptoIntegration.init(AuthzClient.class.getClassLoader())` is added only to `create(Configuration configuration)`. The sibling `create(InputStream configStream)` overload (and any other public factory) is unmodified. If `create(InputStream)` constructs `new AuthzClient(...)` directly after parsing — rather than delegating to `create(Configuration)` — callers using the `InputStream` overload bypass crypto initialization, and any later operation that resolves a provider via `CryptoIntegration.getProvider()` will throw at runtime. Either confirm the delegation in code or add the `init(...)` call to every public entry point (including any other `create(...)` overloads or constructors).
```suggestion
    public static AuthzClient create(InputStream configStream) throws RuntimeException {
        CryptoIntegration.init(AuthzClient.class.getClassLoader());
        // ... existing body
    }
```

## Risk Metadata
Risk Score: 61/100 (HIGH) | Blast Radius: `CryptoProvider` is a public SPI in `keycloak-common` referenced across server, adapters, and downstream `keycloak-client`; ~20+ referencing files | Sensitive Paths: `crypto/` and `authz/client/util/crypto/` (hand-rolled DER on the ECDSA signature path)
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
