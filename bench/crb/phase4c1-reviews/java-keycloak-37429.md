## Summary
48 files changed, 343 lines added, 105 lines deleted. 7 findings (2 critical, 4 improvements, 1 nitpick).
Adds a build-time OWASP HTML sanitizer for translation `.properties` files (`VerifyMessageProperties.verifySafeHtml`). Logic is sound, but two auto-generated translation replacements are in the **wrong language**, and the Java change has a few correctness/style rough edges worth tightening before merge.

## Critical
:red_circle: [correctness] Lithuanian `totpStep1` contains Italian text in `themes/src/main/resources-community/theme/base/account/messages/messages_lt.properties`:101 (confidence: 95)
The replacement value is `Installa una delle seguenti applicazioni sul tuo cellulare:` — that is Italian, not Lithuanian, so every Lithuanian account user will see an Italian sentence on the TOTP setup screen. Looks like the automatic-translation round-trip noted in the PR description picked the wrong target locale. The Slovak/Swedish/Finnish/zh_CN siblings of this same key were translated correctly, so this stands out as a copy-paste/locale-selection slip. Replace with a genuine Lithuanian rendering (e.g. `Įdiekite vieną iš šių programų savo mobiliajame telefone:`).
```suggestion
totpStep1=Įdiekite vieną iš šių programų savo mobiliajame telefone:
```

:red_circle: [correctness] Simplified-Chinese file now contains Traditional Chinese for `totpStep1` in `themes/src/main/resources-community/theme/base/account/messages/messages_zh_CN.properties`:112 (confidence: 88)
The new value `在您的手機上安裝以下應用程式之一：` uses Traditional-Chinese characters (`手機`, `應用程式`, full-width colon `：`) inside the `zh_CN` (Simplified) bundle. Simplified readers should see `在您的手机上安装以下应用程序之一:` (`手机`, `应用程序`). Either route this text to `messages_zh_TW.properties` instead, or rewrite it in Simplified for `zh_CN`.
```suggestion
totpStep1=在您的手机上安装以下应用程序之一:
```

## Improvements
:yellow_circle: [correctness] `RuntimeException` from `verifySafeHtml` bypasses Mojo error reporting in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:108-122 (confidence: 80)
The two `FileInputStream` blocks that load the bundle and its English fallback throw `new RuntimeException(...)` on `IOException`. The enclosing `verify()` only catches `IOException` and wraps it in a `MojoExecutionException`, so a missing/unreadable properties file now surfaces as an unwrapped `RuntimeException` in the Maven log instead of the intended Mojo failure. Either let the method declare `IOException` (and rely on the existing try/catch in `verify()`) or wrap directly in `MojoExecutionException`.
```suggestion
    private void verifySafeHtml() throws MojoExecutionException {
        PropertyResourceBundle bundle;
        try (FileInputStream fis = new FileInputStream(file)) {
            bundle = new PropertyResourceBundle(fis);
        } catch (IOException e) {
            throw new MojoExecutionException("unable to read file " + file, e);
        }

        PropertyResourceBundle bundleEnglish;
        String englishFile = file.getAbsolutePath().replace("resources-community", "resources")
                .replaceAll("_[a-zA-Z_-]*\\.properties$", "_en.properties");
        try (FileInputStream fis = new FileInputStream(englishFile)) {
            bundleEnglish = new PropertyResourceBundle(fis);
        } catch (IOException e) {
            throw new MojoExecutionException("unable to read file " + englishFile, e);
        }
```

:yellow_circle: [correctness] Ambiguous character class in locale regex in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:117 (confidence: 70)
`replaceAll("_[a-zA-Z-_]*\\.properties", "_en.properties")` contains the char class `[a-zA-Z-_]`. The hyphen sits between `Z` and `_`, so it is interpreted as a range `Z`–`_` (ASCII 0x5A–0x5F) — it happens to swallow `[ \ ] ^ _` and works for today's locale codes by accident, but the intent is clearly "letters, `-`, or `_`". Also anchor the suffix so a file under a directory that contains `.properties` in its name isn't truncated early. Use `[A-Za-z_-]*\\.properties$`.

:yellow_circle: [consistency] Policy/pattern fields should be `private static final` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:100-105,181,187 (confidence: 78)
`POLICY_SOME_HTML`, `POLICY_NO_HTML`, `HTML_TAGS`, and `ANCHOR_PATTERN` are declared as package-private instance fields (only `ANCHOR_PATTERN` has `private static final`). Rebuilding an `HtmlPolicyBuilder` per instance is wasteful given the Mojo runs this class once per `.properties` file, and the inconsistent modifiers will encourage the class to grow as a mutable-state component later. Make all four `private static final`.
```suggestion
    private static final PolicyFactory POLICY_SOME_HTML = new org.owasp.html.HtmlPolicyBuilder()
            .allowElements("br", "p", "strong", "b")
            .toFactory();

    private static final PolicyFactory POLICY_NO_HTML = new org.owasp.html.HtmlPolicyBuilder().toFactory();

    private static final Pattern HTML_TAGS = Pattern.compile("<[a-z]+[^>]*>");
```

:yellow_circle: [consistency] Method name typo `santizeAnchors` in `misc/theme-verifier/src/main/java/org/keycloak/themeverifier/VerifyMessageProperties.java`:130,192 (confidence: 90)
`santizeAnchors` should be `sanitizeAnchors`. The method is private, so renaming has no blast radius beyond the single call site inside `verifySafeHtml`.
```suggestion
            value = sanitizeAnchors(key, value, englishValue);
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: build-time Maven plugin + 45 translation bundles touched; runtime blast radius is limited to the TOTP/email localized strings | Sensitive Paths: none (`misc/theme-verifier/**`, `themes/**`, `js/apps/**/messages/**`)
AI-Authored Likelihood: LOW (hand-authored Java; PR author explicitly flagged that *translations* — not code — were machine-translated, which is where the two critical findings originate)

(1 additional finding below confidence threshold — test fixtures `changedAnchor_*.properties`, `illegalHtmlTag_en.properties`, `noHtml_*.properties` are committed without a trailing newline; cosmetic.)
