## Summary
48 files changed, 343 lines added, 105 lines deleted. 2 findings (0 critical, 2 improvements, 0 nitpicks).
Build-time HTML sanitizer for translated `.properties` is well-scoped defense-in-depth; the new `VerifyMessageProperties.verifySafeHtml()` has two correctness/style issues worth fixing before merge.

## Improvements

:yellow_circle: [correctness] `POLICY_SOME_HTML`, `POLICY_NO_HTML`, and `HTML_TAGS` should be `static final` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:60` (confidence: 90)
These three fields are immutable, stateless constants — `PolicyFactory` is documented as thread-safe and `Pattern` is thread-safe — but they are declared as instance fields and so are rebuilt every time a `VerifyMessageProperties` instance is constructed. The sibling field `ANCHOR_PATTERN` in the same class is correctly declared `private static final Pattern`, so the instance-field declarations are inconsistent within this file as well as wasteful. Promoting them to `static final` removes per-instance allocation and matches the existing convention.
```suggestion
    private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
            .allowElements("br", "p", "strong", "b")
            .toFactory();

    private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();

    private static final Pattern HTML_TAGS = Pattern.compile("<[a-z]+[^>]*>");
```

:yellow_circle: [correctness] `verifySafeHtml` throws raw `RuntimeException` from a Maven Mojo helper in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:71` (confidence: 85)
The enclosing `verify()` method already declares `throws MojoExecutionException` and converts `IOException` from `Files.readString` into `MojoExecutionException` to give Maven a plugin-friendly error. The new `verifySafeHtml()` instead wraps both the primary-file `IOException` and the English-source `IOException` as plain `RuntimeException`, so a missing or unreadable English file surfaces as a generic stack trace rather than a structured Mojo failure with the file path in the message. This is inconsistent with the existing convention in the same method and degrades the build error message that downstream Maven users see. Throw `MojoExecutionException` directly (declaring it on `verifySafeHtml`) so the `verify()` try/catch is no longer needed for this branch and the message includes the offending file path.
```suggestion
    private void verifySafeHtml() throws MojoExecutionException {
        PropertyResourceBundle bundle;
        try (FileInputStream fis = new FileInputStream(file)) {
            bundle = new PropertyResourceBundle(fis);
        } catch (IOException e) {
            throw new MojoExecutionException("unable to read file " + file, e);
        }

        PropertyResourceBundle bundleEnglish;
        String englishFile = file.getAbsolutePath().replaceAll("resources-community", "resources")
                .replaceAll("_[a-zA-Z-_]*\\.properties", "_en.properties");
        try (FileInputStream fis = new FileInputStream(englishFile)) {
            bundleEnglish = new PropertyResourceBundle(fis);
        } catch (IOException e) {
            throw new MojoExecutionException("unable to read English source file " + englishFile, e);
        }
        // ... rest unchanged
    }
```

## Risk Metadata
Risk Score: 25/100 (LOW) | Blast Radius: build-time `misc/theme-verifier` Maven plugin only (does not enter Keycloak runtime classpath); 48 files but ~80 % are i18n `.properties` updates with mechanical `{{n}}` -> `{n}` and ICU choice fixes | Sensitive Paths: none (no `auth/`, `security/`, or credential code touched); new dependencies `owasp-java-html-sanitizer:20240325.1` and `commons-text:1.13.0` are current and free of known CVEs (Text4Shell CVE-2022-42889 was fixed in commons-text 1.10.0).
AI-Authored Likelihood: LOW

(8 additional findings below confidence threshold)
