## Summary
7 files changed, 31 lines added, 15 lines deleted. 7 findings (0 critical, 4 improvements, 3 nitpicks).
Replaces Rails' built-in `config.i18n.fallbacks = true` with a custom `FallbackLocaleList` that enforces a `[user_locale, site_locale, :en]` chain and consolidates the pluralization initializer; the design is reasonable but has a brittle backend-include pattern, a hot-path `[]` that rebuilds the fallback list on every translation lookup, and a silent behavior change for dev/test environments.

## Improvements

:yellow_circle: [consistency] `I18n.backend.class.send(:include, Fallbacks)` is brittle — should target `I18n::Backend::Simple` like the line above in `config/initializers/i18n.rb:8` (confidence: 85)
Two lines above this one, `I18n::Backend::Pluralization` is included into `I18n::Backend::Simple` directly by name. This line instead reaches through `I18n.backend.class`, which mutates whatever concrete class the global backend currently is. If a test swaps in a `Chain`/`KeyValue`/decorated backend, or if another initializer wraps the backend before this file loads, `Fallbacks` will either be included into the wrong class or silently skipped from inner backends of a chain. Include it into `I18n::Backend::Simple` by name to match the Pluralization pattern and make the include deterministic regardless of backend identity at initializer-run time.
```suggestion
I18n::Backend::Simple.send(:include, I18n::Backend::Fallbacks)
```

:yellow_circle: [performance] `FallbackLocaleList#[]` rebuilds the fallback array on every translation lookup in `config/initializers/i18n.rb:13-18` (confidence: 80)
The i18n backend calls `I18n.fallbacks[locale]` during every missing-key resolution, and Discourse issues many translation lookups per request. On each call this method does: `SiteSetting.default_locale` (may hit SiteSetting cache + potential DB read), `.to_sym` (allocation), `Array#uniq` (allocation), `Array#compact` (allocation). Under load this becomes a measurable hotspot. Cache the computed chain keyed by `(locale, SiteSetting.default_locale)` and invalidate on site-setting change (Discourse already emits `SiteSetting.refresh!` signals for this pattern).
```suggestion
class FallbackLocaleList < Hash
  def [](locale)
    site = SiteSetting.default_locale.to_sym
    @cache ||= {}
    @cache[[locale, site]] ||= [locale, site, :en].compact.uniq
  end

  def ensure_loaded!
    self[I18n.locale].each { |l| I18n.ensure_loaded! l }
  end
end
```

:yellow_circle: [correctness] Fallback behavior silently expanded to all environments (dev/test) in `config/environments/production.rb`, `config/environments/profile.rb`, `config/cloud/cloud66/files/production.rb` (confidence: 75)
Previously `config.i18n.fallbacks = true` was set only in production, profile, and cloud66 production. Development and test got no fallbacks. This PR removes those three sets and installs `I18n.fallbacks = FallbackLocaleList.new` unconditionally from `config/initializers/i18n.rb`, so dev and test now also fall back through `[user_locale, site_locale, :en]`. This is likely the intent but is not stated in the PR description ("Test 9"). Specs that previously asserted a missing-translation behavior in dev/test will now silently pass via fallback. Call out the scope change in the PR body and sweep the test suite for keys that relied on the prior no-fallback default.

:yellow_circle: [correctness] `ensure_loaded!` in `lib/freedom_patches/translate_accelerator.rb:62-65` has a lazy-init race on `@loaded_locales` (confidence: 70)
```ruby
def ensure_loaded!(locale)
  @loaded_locales ||= []
  load_locale locale unless @loaded_locales.include?(locale)
end
```
`@loaded_locales ||= []` and the `include?` guard both run outside `LOAD_MUTEX`. Under Puma (multi-threaded) two requests for different locales at boot can both observe a nil/empty array and both fall into `load_locale`, which does hold the mutex and is safe, but the guard above it can also reorder with a concurrent append done inside `load_locale`. The pre-existing `translate` method uses the same pattern so this is not a regression, but since this method exists specifically to be called from `FallbackLocaleList#ensure_loaded!` during every `set_locale`, the hot-path exposure is higher than before. Either move the guard inside the mutex or delegate unconditionally to `load_locale` (which is already idempotent+locked).
```suggestion
def ensure_loaded!(locale)
  load_locale(locale)
end
```

## Nitpicks

:white_circle: [consistency] `# order: after 02-freedom_patches.rb` comment in `config/initializers/i18n.rb:1` is not enforced (confidence: 80)
Rails loads initializers in alphabetical order; `i18n.rb` happens to sort after `02-freedom_patches.rb`, but the comment implies an explicit ordering mechanism (e.g. `before:`/`after:` in `Rails::Initializer`) that does not exist here. Either rename the file to `zz_i18n.rb` / `99_i18n.rb` to make the ordering obvious, or drop the comment.

:white_circle: [correctness] `FallbackLocaleList` inherits `Hash` but only overrides `[]` in `config/initializers/i18n.rb:12-22` (confidence: 60)
The i18n gem's own `I18n::Locale::Fallbacks` is a `Hash` subclass that overrides `[]`, `store`, `defaults`, etc. Callers (including `I18n::Backend::Fallbacks`) generally stick to `[]`, but any code path that calls `.fetch`, `.keys`, `.map`, `.to_a`, or serializes the hash will see the underlying (empty) Hash rather than the computed chain. Low risk in practice, but worth noting because the class name implies a full fallback container. Consider subclassing `I18n::Locale::Fallbacks` and overriding `compute` instead, so the rest of the Hash API stays consistent.

:white_circle: [style] `.uniq.compact` order in `config/initializers/i18n.rb:16` (confidence: 50)
`[locale, SiteSetting.default_locale.to_sym, :en].uniq.compact` — `uniq` before `compact` means two `nil` entries would dedupe to a single `nil` and then be removed, which works, but the idiomatic order is `.compact.uniq` (remove nils first, then dedupe non-nil values). In this specific expression none of the three elements should ever be nil so the result is identical, but the reversed order reads as if nils were expected.

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: every request (translation hot path + per-request `set_locale`) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW
