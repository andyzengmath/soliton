## Summary
12 files changed, 673 lines added, 9 lines deleted. 5 findings (1 critical, 4 improvements).
Introduces a lightweight `AuthzClientCryptoProvider` plus custom ASN.1 encoder/decoder, and adds a non-default `order()` method to the public `CryptoProvider` interface — the interface change is source-/binary-incompatible for any external implementer and deserves a backward-compatible form.

## Critical
:red_circle: [cross-file-impact] New abstract `order()` method breaks binary compatibility of public `CryptoProvider` SPI in `common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java`:39 (confidence: 92)
`CryptoProvider` is a public SPI loaded via `ServiceLoader` from third-party classpaths (the file that ships it is literally `META-INF/services/org.keycloak.common.crypto.CryptoProvider`). Adding an **abstract** `int order();` method (no `default` keyword) immediately breaks source and binary compatibility for any downstream implementer — existing deployments that bundle their own `CryptoProvider` (e.g., internal forks, WildFly integrations, custom FIPS providers outside this repo) will fail to link with `AbstractMethodError` at `detectProvider()` time. This is an unannounced breaking change to an extension point. Make it a `default` method so the new ordering logic degrades safely for providers compiled against the previous interface.
```suggestion
    /**
     * Order of this provider. This allows to specify which CryptoProvider will have preference in case that more of them are on the classpath.
     *
     * The higher number has preference over the lower number. Providers compiled against the previous interface default to 0.
     */
    default int order() {
        return 0;
    }
```
[References: https://docs.oracle.com/javase/specs/jls/se11/html/jls-13.html#jls-13.4.12]

## Improvements
:yellow_circle: [correctness] Dead encoder allocations in `concatenatedRSToASN1DER` in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/AuthzClientCryptoProvider.java`:478 (confidence: 95)
Two `ASN1Encoder.create().write(...)` calls are made and their results immediately discarded — only the third invocation (the one passed into `writeDerSeq`) contributes to the output. These two statements can be removed without changing behavior; they look like leftover debug / refactor residue.
```suggestion
                BigInteger rBigInteger = new BigInteger(r);
                BigInteger sBigInteger = new BigInteger(s);

                return ASN1Encoder.create()
                        .writeDerSeq(
                                ASN1Encoder.create().write(rBigInteger),
                                ASN1Encoder.create().write(sBigInteger))
                        .toByteArray();
```

:yellow_circle: [security] `ASN1Decoder` silently accepts indefinite-length and non-constructed SEQUENCE tags in `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/ASN1Decoder.java`:34 (confidence: 78)
Two DER-compliance gaps in a parser that runs on ECDSA signature bytes:
1. `readLength()` returns `-1` for the indefinite-length form (`0x80`). `readSequence` then enters `while (length > 0)` with `-1`, skips the loop, and returns an empty list. DER (X.690 §10.1) forbids indefinite-length encoding, and the caller in `asn1derToConcatenatedRS` would then fail with the generic "Invalid sequence with size different to 2" instead of a clear parse-error — and more worryingly the ambiguity makes it harder to reason about malleability of supplied signatures.
2. `readSequence` compares `tagNo != ASN1Encoder.SEQUENCE` (i.e. `!= 0x10`) but `readTagNumber` already masks off the constructed bit with `tag & 0x1f`. A primitive-form `0x10` tag (which DER also forbids for SEQUENCE) is therefore accepted. Check the raw tag byte for the `CONSTRUCTED` bit as well.
Reject both cases explicitly so malformed inputs fail fast with a descriptive error.
```suggestion
    public List<byte[]> readSequence() throws IOException {
        int tag = readTag();
        if ((tag & ASN1Encoder.CONSTRUCTED) == 0) {
            throw new IOException("SEQUENCE must be constructed (DER)");
        }
        int tagNo = readTagNumber(tag);
        if (tagNo != ASN1Encoder.SEQUENCE) {
            throw new IOException("Invalid Sequence tag " + tagNo);
        }
        int length = readLength();
        if (length < 0) {
            throw new IOException("Indefinite-length encoding is not allowed in DER");
        }
        List<byte[]> result = new ArrayList<>();
        while (length > 0) {
            byte[] bytes = readNext();
            result.add(bytes);
            length = length - bytes.length;
        }
        return result;
    }
```
[References: https://www.itu.int/rec/T-REC-X.690-202102-I/en]

:yellow_circle: [consistency] `order()` magnitudes are unlabeled magic numbers spread across four providers in `common/src/main/java/org/keycloak/common/crypto/CryptoProvider.java`:39 (confidence: 70)
`AuthzClientCryptoProvider.order()` returns `100`; `DefaultCryptoProvider`, `WildFlyElytronProvider` and `FIPS1402Provider` all return `200`. With no shared constants or documented "buckets" (e.g., `FALLBACK=100`, `STANDARD=200`, `HIGH=300`), future providers have to grep four files to pick a value, and there's no central record of why 100 vs 200 was chosen. Consider exposing named constants on the interface (`static final int ORDER_FALLBACK = 100; static final int ORDER_DEFAULT = 200;`) or an ordering section in the interface javadoc.
```suggestion
    /**
     * Order of this provider. Built-in providers use:
     *   ORDER_FALLBACK (100) — "last-resort" providers (e.g., authz-client)
     *   ORDER_DEFAULT  (200) — default full-featured providers
     * The higher number has preference over the lower number.
     */
    int ORDER_FALLBACK = 100;
    int ORDER_DEFAULT  = 200;

    default int order() {
        return ORDER_FALLBACK;
    }
```

:yellow_circle: [testing] Only happy-path round-trip coverage for new DER signature handling in `authz/client/src/test/java/org/keycloak/authorization/client/test/ECDSAAlgorithmTest.java`:32 (confidence: 74)
`ECDSAAlgorithmTest` only exercises `ES256`/`ES384`/`ES512` with signatures produced by the JDK's own `Signature`. Given this is a hand-rolled ASN.1 parser dealing with attacker-influenceable bytes (signatures arrive from the network when verifying tokens), the test set should also cover:
- malformed DER (truncated length, indefinite-length `0x80`, non-constructed SEQUENCE, wrong element count)
- boundary `BigInteger` cases (values with leading `0x00` padding, values whose high bit is set)
- `signLength` mismatch against the actual signature length
Failing to assert on the error path means regressions that silently accept malformed signatures (see the DER-compliance finding above) will not be caught by CI.
```suggestion
    @Test(expected = IOException.class)
    public void testAsn1derToRS_rejectsTruncatedSequence() throws Exception {
        new AuthzClientCryptoProvider().getEcdsaCryptoProvider()
            .asn1derToConcatenatedRS(new byte[] {0x30, 0x05, 0x02, 0x01}, 64);
    }

    @Test(expected = IOException.class)
    public void testAsn1derToRS_rejectsIndefiniteLength() throws Exception {
        new AuthzClientCryptoProvider().getEcdsaCryptoProvider()
            .asn1derToConcatenatedRS(new byte[] {0x30, (byte)0x80, 0x00, 0x00}, 64);
    }
```

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: public SPI change touches 4 `CryptoProvider` implementations + `ServiceLoader` detection in `CryptoIntegration`; new code is on the signature-verification path | Sensitive Paths: `common/src/main/java/org/keycloak/common/crypto/*`, `crypto/*`, `authz/client/src/main/java/org/keycloak/authorization/client/util/crypto/*`
AI-Authored Likelihood: LOW
