## Summary
47 files changed, ~230 lines added, ~77 lines deleted. 4 findings (0 critical, 4 improvements, 0 nitpicks).
Adds an OWASP-sanitizer-based HTML safety check to the `theme-verifier` Maven plugin and fixes HTML syntax in translated message bundles; the verifier's I/O and policy setup has a few correctness/style issues worth addressing before merge.

## Improvements

:yellow_circle: [correctness] `PropertyResourceBundle(InputStream)` decodes as ISO-8859-1, likely misreading UTF-8 translations in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:109 (confidence: 86)
Both `new PropertyResourceBundle(fis)` calls (for the translated file and its English counterpart) use the `InputStream` constructor, which is contractually ISO-8859-1. Any non-ASCII bytes in a UTF-8 `.properties` file — common for ar/fa/ko/zh/th/uk translations in this PR — will be decoded as Latin-1, producing mojibake in bundle values. The verifier then feeds those bytes into the OWASP sanitizer and the anchor/error-message comparison, which can both silently pass incorrect strings and produce garbled `Illegal HTML in key ... '…'` messages that are hard to debug. Prefer the `Reader` overload so the charset is explicit and matches how these files are consumed elsewhere in the build.
```suggestion
try (java.io.Reader reader = java.nio.file.Files.newBufferedReader(file.toPath(), java.nio.charset.StandardCharsets.UTF_8)) {
    bundle = new PropertyResourceBundle(reader);
} catch (IOException e) {
    throw new MojoExecutionException("unable to read file " + file, e);
}
```

:yellow_circle: [consistency] `verifySafeHtml` throws `RuntimeException` instead of `MojoExecutionException` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:111 (confidence: 88)
The enclosing `verify()` method declares `throws MojoExecutionException` and the sibling failure in the outer `try` block is already wrapped as `MojoExecutionException`. Wrapping the I/O error here in a raw `RuntimeException` breaks that convention: Maven reports it as a plugin crash with a stack trace rather than as a graceful build-failure message, and any caller that catches `MojoExecutionException` to summarize theme-verification failures will miss it. Same issue on the English-bundle branch at line ~120.
```suggestion
        PropertyResourceBundle bundle;
        try (FileInputStream fis = new FileInputStream(file)) {
            bundle = new PropertyResourceBundle(fis);
        } catch (IOException e) {
            throw new MojoExecutionException("unable to read file " + file, e);
        }
```
(and propagate `MojoExecutionException` from `verifySafeHtml` / `verify`.)

:yellow_circle: [correctness] `PolicyFactory` fields are non-`static`, non-`final`, package-visible instance fields in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:100 (confidence: 86)
`POLICY_SOME_HTML` and `POLICY_NO_HTML` are rebuilt on every `VerifyMessageProperties` instance (one per file iterated over a large theme tree), even though `PolicyFactory` is immutable and thread-safe. They are also writable from outside the class, which invites accidental mutation in tests. The neighboring `HTML_TAGS` pattern is also declared as a plain instance field, while `ANCHOR_PATTERN` below it is correctly `private static final` — the inconsistency suggests these were oversights.
```suggestion
    private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
            .allowElements("br", "p", "strong", "b")
            .toFactory();

    private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();

    private static final Pattern HTML_TAGS = Pattern.compile("<[a-z]+[^>]*>");
```

:yellow_circle: [correctness] `verifySafeHtml` is invoked once per call to `verify()` but recomputes the English bundle path from the file being verified in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:116 (confidence: 85)
When `file` itself is an `_en.properties` file under `resources/` (not `resources-community/`), `englishFile` resolves to the same absolute path, so the English bundle is just the bundle under test — fine. But when `file` is `messages.properties` (no locale suffix, which exists in the repo as a fallback), the regex `_[a-zA-Z-_]*\.properties` does not match and `englishFile` equals `file` — again self-referential, which means no cross-check is performed and the method silently returns whatever the input happens to contain. Also, `replaceAll("resources-community", "resources")` uses a regex engine for a literal string replacement. Consider handling the no-locale case explicitly and switching to `replace(...)` for the literal.
```suggestion
String absolute = file.getAbsolutePath().replace("resources-community", "resources");
String englishFile = absolute.replaceAll("_[a-zA-Z][a-zA-Z_\\-]*\\.properties$", "_en.properties");
if (englishFile.equals(absolute) && !absolute.endsWith("_en.properties")) {
    // No locale suffix detected — skip the cross-bundle comparison instead of comparing the file to itself.
    return;
}
```

## Risk Metadata
Risk Score: 28/100 (LOW) | Blast Radius: build-time only — `misc/theme-verifier` is a dev-tooling Maven module, not shipped in the Keycloak server runtime; translation edits are HTML-syntax fixes to already-localized strings | Sensitive Paths: none hit
AI-Authored Likelihood: LOW
