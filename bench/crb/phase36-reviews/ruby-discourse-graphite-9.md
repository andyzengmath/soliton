## Summary
7 files changed, 31 lines added, 15 lines deleted. 5 findings (2 critical, 2 improvements, 1 nitpick).
Server-side i18n fallback refactor introduces a TOCTOU race in `ensure_loaded!` and an environment-config gap that can raise NoMethodError in dev/test.

## Critical

:red_circle: [correctness] TOCTOU race: `ensure_loaded!` reads `@loaded_locales` outside the mutex in lib/freedom_patches/translate_accelerator.rb:62 (confidence: 97)
The new `ensure_loaded!` does `@loaded_locales ||= []` and `@loaded_locales.include?(locale)` without holding `LOAD_MUTEX`, while the existing `load_locale` already guards the same variable inside `LOAD_MUTEX.synchronize`. Under Puma, two threads can lose a write to `@loaded_locales` or both enter `load_locale`, producing redundant loads or a corrupt array.
```suggestion
    def ensure_loaded!(locale)
      load_locale(locale)
    end
```
<details><summary>More context</summary>

`load_locale` already returns immediately when the locale is already in `@loaded_locales` because it re-checks `include?` inside the mutex. The outer unguarded check in `ensure_loaded!` adds no useful fast path and is pure race surface. The initialization `@loaded_locales ||= []` is particularly risky because on a cold boot two request threads can both observe `nil` and each create their own empty array before one writes — any locale appended by the loser is dropped.
</details>

:red_circle: [cross-file-impact] `I18n.fallbacks.ensure_loaded!` can NoMethodError in dev/test in app/controllers/application_controller.rb:158 (confidence: 85)
The PR removes `config.i18n.fallbacks = true` from `production.rb`, `profile.rb`, and `cloud66/files/production.rb`, but not from `development.rb` or `test.rb`. If either of those still sets it, Rails's i18n Railtie replaces `I18n.fallbacks` with a plain `I18n::Locale::Fallbacks` that does not respond to `ensure_loaded!`, causing a NoMethodError on every request.
```suggestion
    I18n.fallbacks.ensure_loaded! if I18n.fallbacks.respond_to?(:ensure_loaded!)
```
<details><summary>More context</summary>

The defensive `respond_to?` guard above is a short-term safety net — the real fix is to also delete `config.i18n.fallbacks = true` from `config/environments/development.rb` and `config/environments/test.rb` so `I18n.fallbacks` is consistently the `FallbackLocaleList` instance set by the new initializer. Because the Rails Railtie re-applies `config.i18n.fallbacks` after initializers in some Rails versions, the ordering is version-sensitive; don't rely on "initializers run last."
</details>

## Improvements

:yellow_circle: [correctness] `FallbackLocaleList#ensure_loaded!` reads `I18n.locale` implicitly in config/initializers/i18n.rb:20 (confidence: 88)
The method calls `self[I18n.locale]` instead of taking the locale as a parameter, so any caller running before `I18n.locale=` is set (background jobs, boot-time eager loading, a `reload!` cycle) silently loads only `:en` and skips the site locale. The call-site in `set_locale` already computed the locale locally and should pass it explicitly.
```suggestion
  def ensure_loaded!(locale = I18n.locale)
    self[locale].each { |l| I18n.ensure_loaded!(l) }
  end
```

:yellow_circle: [correctness] `FallbackLocaleList#[]` allocates a new Array on every call, defeating Hash caching in config/initializers/i18n.rb:13 (confidence: 85)
The class subclasses `Hash` precisely so the i18n backend can memoize resolved fallback chains, but the overridden `[]` ignores `Hash` storage and rebuilds `[locale, SiteSetting.default_locale.to_sym, :en]` plus calls `SiteSetting.default_locale` on every translation lookup. Under Discourse's translation volume this is measurable allocation and DB-cache pressure for zero reason.
```suggestion
  def [](locale)
    super(locale) || (self[locale] = [locale, SiteSetting.default_locale.to_sym, :en].uniq.compact)
  end
```
<details><summary>More context</summary>

If `SiteSetting.default_locale` can change at runtime (admin panel), add a `reset!` method that calls `clear` and invoke it from the SiteSetting-change hook so the memoized chains don't go stale.
</details>

## Nitpicks

:large_blue_circle: [consistency] Non-idiomatic `I18n.backend.class.send(:include, Fallbacks)` pattern in config/initializers/i18n.rb:9 (confidence: 88)
Three agents flagged this: the preceding Pluralization line targets `I18n::Backend::Simple` by name, but the Fallbacks line goes through `I18n.backend.class`, which (a) is inconsistent with the line above, (b) silently misses the new class if any test helper or plugin later reassigns `I18n.backend`, and (c) uses `send` despite `include` being public since Ruby 2.1.
```suggestion
I18n::Backend::Simple.include(I18n::Backend::Fallbacks)
```

## Risk Metadata
Risk Score: 33/100 (MEDIUM) | Blast Radius: high (application_controller.rb + translate_accelerator.rb are globally consumed) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
