## Summary
48 files changed, ~250 lines added, ~80 lines deleted. 6 findings (0 critical, 6 improvements, 0 nitpicks).
New HTML sanitizer for translated message resources looks directionally right, but the core `VerifyMessageProperties.java` has several correctness/robustness issues in exception handling, field scoping, and test strictness.

## Improvements

:yellow_circle: [correctness] Anchor-stripping mutates `value` out-of-sync with the matcher in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:192` (confidence: 85)
`santizeAnchors` iterates `matcher` against the ORIGINAL `value`, but inside the loop reassigns `value = value.replaceFirst(Pattern.quote(englishMatcher.group()), "")`. The `replaceFirst` targets the first occurrence in the *current* `value`, not the position `matcher` is iterating at, so when a translation has multiple identical anchors with differing surrounding text (or anchors appear in different order than in English), the removal may consume the wrong occurrence and the downstream sanitization check compares a malformed residual string. Safer: collect anchor tags from each side into lists, compare order/equality explicitly, and rebuild `value` with `matcher.appendReplacement` / `appendTail`.
```suggestion
private String santizeAnchors(String key, String value, String englishValue) {
    Matcher matcher = ANCHOR_PATTERN.matcher(value);
    Matcher englishMatcher = ANCHOR_PATTERN.matcher(englishValue);
    StringBuilder sb = new StringBuilder();
    while (matcher.find()) {
        if (englishMatcher.find() && Objects.equals(matcher.group(), englishMatcher.group())) {
            matcher.appendReplacement(sb, "");
        } else {
            messages.add("Didn't find anchor tag " + matcher.group() + " in original string");
            return value; // bail out; caller will still flag diff
        }
    }
    matcher.appendTail(sb);
    return sb.toString();
}
```

:yellow_circle: [performance] `PolicyFactory` fields should be `private static final` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:100` (confidence: 90)
`POLICY_SOME_HTML` and `POLICY_NO_HTML` are declared as package-private instance fields and re-initialized on every `VerifyMessageProperties` construction. `HtmlPolicyBuilder` is non-trivial to build, and the sanitizer is invoked once per file in a multi-module Maven build, so this allocates thousands of identical policies unnecessarily. The constant-style SCREAMING_CASE naming also implies the author intended them to be constants.
```suggestion
private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
        .allowElements("br", "p", "strong", "b")
        .toFactory();

private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();
```

:yellow_circle: [correctness] `RuntimeException` wraps `IOException` inside a Maven plugin in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:112` (confidence: 85)
The enclosing `verify()` method already declares `throws MojoExecutionException` and the existing code path wraps `IOException` in `MojoExecutionException`. `verifySafeHtml()` instead throws raw `RuntimeException`, which Maven surfaces as an uncaught plugin failure rather than a structured build error with the standard "Can not read file" context. This breaks error-reporting consistency and produces worse CI failure messages.
```suggestion
private void verifySafeHtml() throws MojoExecutionException {
    PropertyResourceBundle bundle;
    try (FileInputStream fis = new FileInputStream(file)) {
        bundle = new PropertyResourceBundle(fis);
    } catch (IOException e) {
        throw new MojoExecutionException("Unable to read file " + file, e);
    }
    // ... same for englishFile
}
```

:yellow_circle: [testing] Test assertion weakened from exact-match to `hasItem` in `misc/theme-verifier/src/test/java/org/keycloak/themeverifier/VerifyMessagePropertiesTest.java:230` (confidence: 80)
`verifyDuplicateKeysDetected` previously used `Matchers.contains(...)`, which asserts the returned list is exactly `[matching element]`. Switching to `Matchers.hasItem(...)` only asserts presence, so regressions that cause spurious additional messages (e.g., false-positive HTML warnings on the same fixture) will now pass silently. The fixture `duplicateKeys_en.properties` is a minimal file where only the duplicate-keys message is expected; keep the strict contract.
```suggestion
List<String> verify = getFile("duplicateKeys_en.properties").verify();
MatcherAssert.assertThat(verify, Matchers.contains(Matchers.containsString("Duplicate keys in file")));
```

:yellow_circle: [performance] English bundle re-read per file in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:117` (confidence: 75)
`verifySafeHtml()` re-opens and re-parses the same `messages_en.properties` file every time `verify()` is invoked on a non-English sibling. For a repo with dozens of locales and multiple theme roots, the English bundle can be loaded 40+ times per build. Cache the parsed bundle by English-file path in a `static Map<String, PropertyResourceBundle>` (or pass it in via the Mojo) to cut redundant I/O.

:yellow_circle: [correctness] Regex character class treats `-` as a range in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:117` (confidence: 70)
`"_[a-zA-Z-_]*\\.properties"` — inside the class `[a-zA-Z-_]` the `-` between `Z` and `_` is parsed as a range `Z`-`_`, which additionally matches `[`, `\`, `]`, `^`. Locale codes never contain those characters in practice, so there's no observable bug today, but the regex is ambiguous and brittle; several regex linters flag it. Put the literal dash at the start or end of the class.
```suggestion
.replaceAll("_[a-zA-Z_-]*\\.properties", "_en.properties");
```

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: adds new compile-scope dependency (OWASP java-html-sanitizer, commons-text) pulled into `theme-verifier` plugin used at build time; 45 translation `.properties` files touched (substantive edits confined to 1 Java file) | Sensitive Paths: security-adjacent (HTML sanitization), no auth/payment/secret paths touched
AI-Authored Likelihood: LOW
