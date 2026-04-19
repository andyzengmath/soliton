## Summary
48 files changed, 343 lines added, 105 lines deleted. 6 findings (2 critical, 4 improvements).
Adds a build-time HTML sanitizer verifier for translation `.properties` files; main risk lies in the new `verifySafeHtml` logic that throws raw RuntimeExceptions and in subtle regex pitfalls that can cause false negatives/positives against certain translation files.

## Critical
:red_circle: [correctness] `verifySafeHtml` throws `RuntimeException` and bypasses the `MojoExecutionException` contract in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:111` (confidence: 92)
When the target bundle or the derived English bundle cannot be opened, `verifySafeHtml()` wraps `IOException` in a raw `RuntimeException`, but the enclosing `verify()` only catches `IOException` and rethrows as `MojoExecutionException`. The runtime will surface as an uncaught stack trace during the Maven build instead of a clean Mojo error, which defeats the plugin's error contract and makes CI failures harder to triage.
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
                .replaceAll("_[a-zA-Z_-]*\\.properties", "_en.properties");
        try (FileInputStream fis = new FileInputStream(englishFile)) {
            bundleEnglish = new PropertyResourceBundle(fis);
        } catch (IOException e) {
            throw new MojoExecutionException("unable to read file " + englishFile, e);
        }
        // ...
    }
```
<details><summary>More context</summary>

`verify()` declares `throws MojoExecutionException` and the existing `verifyNoDuplicateKeys` path already funnels IO failures through `MojoExecutionException`. Mixing raw `RuntimeException` here creates an inconsistency that will only be noticed when a locale file is genuinely missing (e.g. a translator commits a `_xx.properties` whose matching English source is not yet landed). Propagating `MojoExecutionException` also lets Maven honor `--fail-never` / `--fail-at-end`, which a `RuntimeException` short-circuits.
</details>

:red_circle: [correctness] Character class `[a-zA-Z-_]` silently matches `[ \ ] ^` because of unintended `Z-_` range in `VerifyMessageProperties.java:117` (confidence: 90)
Java's regex engine interprets `[a-zA-Z-_]` as three ranges: `a-z`, `A-Z`, then `Z-_` (ASCII 90..95), which additionally matches `[`, `\`, `]`, `^`. On pathological paths this lets the locale regex consume characters it should not; more importantly it shows the author did not intend a range, so the regex won't reliably match hyphenated locale suffixes such as `_sr-Latn`.
```suggestion
        String englishFile = file.getAbsolutePath().replaceAll("resources-community", "resources")
                .replaceAll("_[a-zA-Z_-]*\\.properties", "_en.properties");
```
<details><summary>More context</summary>

Put `-` at the end (or escape it) so the class is unambiguously `{letters, underscore, hyphen}`. Current behavior is not exploitable because the resulting English path is still fed through `FileInputStream`, but it is a latent bug that will bite as soon as a locale like `sr-Latn` or `zh-Hant` is added.
</details>

## Improvements
:yellow_circle: [correctness] `containsHtml` only detects lowercase HTML tags in `VerifyMessageProperties.java:184` (confidence: 85)
`HTML_TAGS = Pattern.compile("<[a-z]+[^>]*>")` never matches `<BR>`, `<P>`, or any capitalized tag that may appear in legacy English sources, so the policy silently downgrades to `POLICY_NO_HTML` and every translation that legitimately mirrors such tags is reported as illegal HTML — a hard-to-diagnose false positive that blocks builds.
```suggestion
    Pattern HTML_TAGS = Pattern.compile("<[a-zA-Z]+[^>]*>");
```

:yellow_circle: [correctness] Typo `santizeAnchors` + order-sensitive matching flags valid translations in `VerifyMessageProperties.java:192` (confidence: 80)
The method name is misspelled (`santize` → `sanitize`) and the algorithm iterates anchors positionally, so a translation that legitimately reorders `<a href="…">` tags for grammatical reasons is reported as "Didn't find anchor tag" even when the set of anchors is identical. Rename, and compare the set of allowed anchors from the English source instead of advancing both matchers in lock-step.
```suggestion
    private String sanitizeAnchors(String key, String value, String englishValue) {
        java.util.Set<String> allowed = new java.util.HashSet<>();
        Matcher englishMatcher = ANCHOR_PATTERN.matcher(englishValue);
        while (englishMatcher.find()) {
            allowed.add(englishMatcher.group());
        }
        Matcher matcher = ANCHOR_PATTERN.matcher(value);
        while (matcher.find()) {
            if (!allowed.contains(matcher.group())) {
                messages.add("Didn't find anchor tag " + matcher.group() + " in original string");
                break;
            }
            value = value.replaceFirst(Pattern.quote(matcher.group()), "");
        }
        return value;
    }
```
<details><summary>More context</summary>

Order-preserving translations are the common case, but reordered anchors do occur (e.g. German word order, RTL languages). The set-based variant also removes the subtle issue that `matcher` iterates the original string while `value.replaceFirst` mutates a copy — under duplicate anchors this can mis-count and leave an unremoved tag that then fails sanitization.
</details>

:yellow_circle: [consistency] `POLICY_SOME_HTML` / `POLICY_NO_HTML` should be `static final` in `VerifyMessageProperties.java:100` (confidence: 78)
These policy factories are stateless, identical for every instance, and relatively expensive to build. Declaring them as non-static instance fields creates fresh `PolicyFactory` objects for every verified `.properties` file, which for a repo with 40+ translations is wasteful and inconsistent with `ANCHOR_PATTERN` just below that IS `static final`.
```suggestion
    private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
            .allowElements("br", "p", "strong", "b")
            .toFactory();

    private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();
```

:yellow_circle: [cross-file-impact] Path rewrite uses `replaceAll("resources-community", "resources")` on an absolute path in `VerifyMessageProperties.java:116` (confidence: 70)
Because the replacement runs on `file.getAbsolutePath()`, any occurrence of `resources-community` anywhere in the path is rewritten — including user/workspace directory names. A developer whose workspace happens to include a segment containing `resources-community` (possible on shared CI agents or in reproducible-build sandboxes) will have that segment silently mangled and the English fallback file will not be found. Anchor the pattern to the known directory boundary.
```suggestion
        String englishFile = file.getAbsolutePath()
                .replace(File.separator + "resources-community" + File.separator,
                         File.separator + "resources" + File.separator)
                .replaceAll("_[a-zA-Z_-]*\\.properties", "_en.properties");
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: build-time Maven plugin (`misc/theme-verifier`) + 40+ translation property files; failures here gate the whole Keycloak build | Sensitive Paths: none directly (no auth/payment code), but enforces XSS-relevant sanitization policy for translated UI strings
AI-Authored Likelihood: LOW
