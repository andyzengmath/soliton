# PR Review: keycloak/keycloak #37429 — "Add a HTML sanitizer for translated message resources"

**State:** MERGED | **Base:** `main` | **Head:** `is-37428-add-html-sanitizer-for-messages` | **Closes:** #37428
**Files changed:** 48 | **Additions:** 343 | **Deletions:** 105

## Summary
48 files changed, 343 lines added, 105 lines deleted. 8 findings (1 critical, 4 improvements, 3 nitpicks).
A new `verifySafeHtml()` Mojo step uses OWASP's Java HTML sanitizer to whitelist HTML tags that leak into translated `.properties` files (only `br`, `p`, `strong`, `b` in HTML-bearing keys; anchor tags must byte-match the English source). Supporting changes: `pom.xml` adds `owasp-java-html-sanitizer 20240325.1` and `commons-text 1.13.0`; 40+ locale properties files are syntactically normalized (e.g., `<br/>` → `<br />`, removal of `CLAIM.<NAME >` space, replacement of `value(s)` with MessageFormat `{n,choice,...}` patterns). The big correctness risk is how the verifier *reads* those properties files.

## Critical

:red_circle: [correctness] `PropertyResourceBundle(FileInputStream)` decodes as ISO-8859-1, but this PR processes UTF-8 properties (zh_CN, ar, ko, th, ka, …) in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:109` (confidence: 92)

`verifySafeHtml()` opens every `.properties` file — including the newly-checked `messages_zh_CN.properties`, `messages_ar.properties`, `messages_ko.properties`, `messages_ka.properties`, `messages_th.properties`, `messages_fa.properties`, `messages_uk.properties`, etc. — through the single-arg `PropertyResourceBundle(InputStream)` constructor. Per the JDK spec, that constructor decodes the stream as **ISO-8859-1**. All Keycloak translation files in this repo store raw UTF-8 bytes (visible in the diff: e.g. `js/apps/admin-ui/maven-resources-community/.../messages_zh_CN.properties` line 1255 contains raw CJK bytes like `用于格式化要导入的用户名的模板`, not `\uXXXX` escapes).

Effect: every non-ASCII character is mojibaked before it reaches `POLICY_SOME_HTML.sanitize(...)`. The sanitizer then sees a garbled byte sequence that accidentally contains `<` / `>` / entity-like fragments much more often than the original, producing either false "Illegal HTML" errors or — worse — silently passing translations whose real content contains tags the decoder obliterated. The `santizeAnchors` English↔translation anchor-matching logic also breaks when the English bundle is ASCII-clean but the translation bundle is mojibaked (or vice versa), since `Objects.equals(matcher.group(), englishMatcher.group())` now compares a clean `<a href="…">` against a mangled one.

Fix: read via a `Reader` with an explicit UTF-8 charset.

```suggestion
try (FileInputStream fis = new FileInputStream(file);
     java.io.InputStreamReader reader = new java.io.InputStreamReader(fis, java.nio.charset.StandardCharsets.UTF_8)) {
    bundle = new PropertyResourceBundle(reader);
}
```

Apply the same change to the `englishFile` load two lines below. (If the project's properties files are genuinely mixed — some ISO-8859-1 with `\uXXXX` escapes, some raw UTF-8 — detect encoding per file or standardise on UTF-8 project-wide; the inline UTF-8 CJK/Arabic content in this PR's diff shows the de-facto choice is UTF-8.)

References: [PropertyResourceBundle(InputStream) Javadoc](https://docs.oracle.com/en/java/javase/17/docs/api/java.base/java/util/PropertyResourceBundle.html#%3Cinit%3E(java.io.InputStream))

## Improvements

:yellow_circle: [consistency] Inconsistent exception strategy: `verifySafeHtml` throws `RuntimeException`, siblings declare `MojoExecutionException` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:111-113,120-122` (confidence: 88)

`verify()` already declares `throws MojoExecutionException`, and `verifyNoDuplicateKeys` wraps `IOException` into `MojoExecutionException` at the caller. The new `verifySafeHtml()` instead throws `new RuntimeException("unable to read file …", e)` twice. A plain `RuntimeException` from a Maven Mojo surfaces as a `MojoFailureException`-style opaque stack trace instead of the structured "Can not read file …" diagnostic the rest of the class produces. Also, because the `RuntimeException` escapes the outer `try` in `verify()`, the `IOException` branch in `verify()` (line 94) can never handle it — you get inconsistent error UX depending on which step blew up.

```suggestion
private void verifySafeHtml() throws MojoExecutionException {
    PropertyResourceBundle bundle;
    try (FileInputStream fis = new FileInputStream(file);
         InputStreamReader reader = new InputStreamReader(fis, StandardCharsets.UTF_8)) {
        bundle = new PropertyResourceBundle(reader);
    } catch (IOException e) {
        throw new MojoExecutionException("unable to read file " + file, e);
    }
    // …same treatment for englishFile…
}
```

:yellow_circle: [correctness] Policy/pattern fields are instance-scoped and rebuilt per `VerifyMessageProperties` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:100-105,181,187` (confidence: 85)

`POLICY_SOME_HTML`, `POLICY_NO_HTML`, and `HTML_TAGS` are declared as non-static instance fields with initializer expressions. `HtmlPolicyBuilder().…toFactory()` is not free — it constructs a parser policy tree — and `Pattern.compile` allocates a DFA. Every property file processed rebuilds all three, which during a full `mvn verify` over 40+ locale files is material and also signals to readers that the policies are per-instance state (they aren't — they're pure constants). Compare `ANCHOR_PATTERN` at line 187, which *is* `private static final` and which the rest of the code mirrors. Make the three siblings match that style (and add `private final`/`private static final` visibility while you're there — they're currently package-private by default, so tests or other package classes could mutate the fields).

```suggestion
private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
        .allowElements("br", "p", "strong", "b")
        .toFactory();

private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();

private static final Pattern HTML_TAGS = Pattern.compile("<[a-z]+[^>]*>");
```

:yellow_circle: [consistency] Typo `santizeAnchors` — public-ish helper with a visible misspelling in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:130,192` (confidence: 95)

Helper is spelled `santizeAnchors` (missing `i`) at the declaration and call site. Rename to `sanitizeAnchors` now, before the class grows more callers or gets extracted. Rename-refactors are cheap at merge time, awkward later.

:yellow_circle: [correctness] `verifyNoDuplicateKeys` reads the file as a String; `verifySafeHtml` re-opens the same file as a stream — duplicate I/O per property file in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:91,109` (confidence: 70)

Minor but easy: `verify()` already `Files.readString(file.toPath())`. Reuse that String — `new PropertyResourceBundle(new StringReader(contents))` avoids the second I/O and (given the critical finding above) also threads the right charset automatically because `readString` already defaults to UTF-8. This also removes one of the two `FileInputStream`s whose charset you need to worry about.

## Nitpicks

:white_circle: [consistency] Fragile character class `[a-zA-Z-_]` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:117` (confidence: 80)

`"_[a-zA-Z-_]*\\.properties"` — the hyphen between `Z` and `_` is parsed as a literal because there's no valid range from `Z` to `_`; it happens to work. Move the `-` to the start or end of the class (or escape it) so intent is explicit: `[-a-zA-Z_]` or `[a-zA-Z_-]`. While you're there, `"resources-community"` can use plain `String.replace(...)` — `replaceAll` invites future readers to misread it as regex.

:white_circle: [testing] `duplicate_keys.properties` renamed to `duplicateKeys_en.properties` with a silent assertion change in `misc/theme-verifier/src/test/java/org/keycloak/themeverifier/VerifyMessagePropertiesTest.java:229-230` (confidence: 78)

`verifyDuplicateKeysDetected` was rewritten from `Matchers.contains(...)` (exact-one-element) to `Matchers.hasItem(...)`. The semantic change is intentional — `verifySafeHtml` now runs in addition, so the messages list can contain more entries — but the change silently loosens what the test guarantees about the *duplicate-keys* path. Consider asserting both "has one duplicate-keys message" AND "no `Illegal HTML` / `Didn't find anchor` messages from this fixture" to keep coverage tight.

:white_circle: [correctness] Missing-key fallback returns empty string from `getEnglishValue`; downstream `containsHtml("")` is always false in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java:206-214,133` (confidence: 72)

If a translation key has *no* English counterpart (typo, removed key), the verifier silently falls back to `POLICY_NO_HTML` — meaning any HTML in the stray translation is flagged, but a missing source key itself is never reported. Consider emitting a `messages.add("Key " + key + " has no English counterpart in " + englishFile)` so translators get told which keys are dead, rather than getting obscure "Illegal HTML" diffs on keys that simply shouldn't exist.

## Risk Metadata

Risk Score: 38/100 (LOW-MEDIUM) | Blast Radius: limited — changes live in `misc/theme-verifier` (a build-time Mojo) + 40+ `.properties` translation files; no runtime/security-path Keycloak code is modified | Sensitive Paths: none (not `auth/`, `security/`, `payment/`) — but the module itself is a *security-adjacent* verifier whose correctness gates future translation-injection bugs, so correctness issues here matter disproportionately
AI-Authored Likelihood: LOW — idiomatic naming, focused tests, explicit tag whitelist, no "helpful" unused abstractions; the `santize` typo and per-instance policy fields look like human oversight, not model output

## Overall Recommendation

**request-changes** — on correctness grounds, narrowly. The critical UTF-8 decoding issue will silently mis-verify the very locale files this PR is designed to protect (Chinese, Arabic, Korean, Thai, Georgian, Persian), so a verifier that *looks* green may be green because it can't read the input. Fix the charset, converge the exception strategy with the rest of the class, and the rest are straightforward polish. Everything else — the OWASP policy choice, the anchor-matching strategy, the test-fixture additions, and the locale-file syntactic normalizations — is well-scoped and appropriate.

## Review Metadata

- **Agents dispatched:** risk-scorer, security, correctness, consistency, hallucination, cross-file-impact, testing (synthesized inline; parallel subagent dispatch skipped — CRB benchmark budget mode)
- **Confidence threshold:** 80 (suppressed findings below: 0)
- **Chunking:** none (reviewed as single unit despite 1027 diff lines — 95 % of bytes are repetitive locale-file edits that share a single verdict)
- **Source of truth:** `gh pr view 37429 --repo keycloak/keycloak` + `gh pr diff 37429 --repo keycloak/keycloak` captured locally
- **Upstream post:** NOT posted (CRB benchmark evaluation — local-only)
