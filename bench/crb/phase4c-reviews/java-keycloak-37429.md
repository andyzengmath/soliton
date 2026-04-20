## Summary
48 files changed, 209 lines added, 90 lines deleted. 3 findings (0 critical, 3 improvements, 0 nitpicks).
Adds a build-time HTML sanitizer to the `theme-verifier` Maven plugin so translated `.properties` messages cannot introduce HTML beyond what the English source allows; core logic is sound but contains a public typo, inconsistent exception handling, and a minor field-scope issue.

## Improvements
:yellow_circle: [consistency] Method name typo: `santizeAnchors` → `sanitizeAnchors` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:130 (confidence: 95)
The new helper is declared as `santizeAnchors` (missing `i`) on line 192 and called on line 130. The class is public and the method, while currently private, is part of a newly introduced API surface in a misc tool; the misspelling will propagate if any future refactor exposes it and is awkward in stack traces and IDE search. A pure rename has no behavioral impact.
```suggestion
    private String sanitizeAnchors(String key, String value, String englishValue) {
```

:yellow_circle: [correctness] Inconsistent exception type from `verifySafeHtml` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:111 (confidence: 88)
`verify()` declares `throws MojoExecutionException` and wraps I/O failures from `verifyNoDuplicateKeys` into one (line 94-96). The newly added `verifySafeHtml()` instead re-throws `IOException` as `RuntimeException` on both the target file (line 111-112) and the English reference file (line 120-121). That bypasses Maven's structured failure reporting: a missing/unreadable `messages_en.properties` surfaces as an opaque build crash instead of a `MojoExecutionException` with plugin context. Propagating `IOException` and letting the existing `catch` in `verify()` wrap it keeps behaviour uniform and preserves the file path the outer handler already logs.
```suggestion
    private void verifySafeHtml() throws IOException {
        PropertyResourceBundle bundle;
        try (FileInputStream fis = new FileInputStream(file)) {
            bundle = new PropertyResourceBundle(fis);
        }

        PropertyResourceBundle bundleEnglish;
        String englishFile = file.getAbsolutePath().replaceAll("resources-community", "resources")
                .replaceAll("_[a-zA-Z-_]*\\.properties", "_en.properties");
        try (FileInputStream fis = new FileInputStream(englishFile)) {
            bundleEnglish = new PropertyResourceBundle(fis);
        }
        // ... rest unchanged
    }
```

:yellow_circle: [consistency] `POLICY_SOME_HTML` / `POLICY_NO_HTML` should be `private static final` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:100 (confidence: 86)
Both `PolicyFactory` fields are declared as package-private instance fields and are re-constructed (via `HtmlPolicyBuilder`) every time a `VerifyMessageProperties` is instantiated. `PolicyFactory` is documented as immutable and thread-safe, and the nearby `ANCHOR_PATTERN` is already `private static final` (line 187). Matching that convention saves repeated allocation per verified properties file and tightens the class's public surface.
```suggestion
    private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
            .allowElements("br", "p", "strong", "b")
            .toFactory();

    private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();
```

## Risk Metadata
Risk Score: 22/100 (LOW) | Blast Radius: build-time verifier only (`misc/theme-verifier/`); no runtime, auth, or user-facing paths touched — all other diffs are translation string updates in `.properties` files | Sensitive Paths: none
AI-Authored Likelihood: LOW
