## Summary
7 files changed, 31 lines added, 15 lines deleted. 2 findings (2 critical, 0 improvements, 0 nitpicks).
Localization fallbacks refactor introduces a thread-safety race in `ensure_loaded!` and a nil-dereference on the request hot path.

## Critical

:red_circle: [correctness] Race condition — `ensure_loaded!` is not mutex-protected (TOCTOU + non-atomic `@loaded_locales` init) in lib/freedom_patches/translate_accelerator.rb:62 (confidence: 95)
The new `ensure_loaded!` performs check-then-act (`@loaded_locales.include?(locale)` then `load_locale`) without holding `LOAD_MUTEX`, which the surrounding `translate`/`load_locale` logic acquires. Under multi-threaded Puma, two requests can both observe the locale as absent and both enter `load_locale`, yielding a double-load race. `@loaded_locales ||= []` is itself a non-atomic read-modify-write on a shared instance variable; Ruby's Array is not thread-safe for concurrent mutation, so concurrent double-init can corrupt state.
```suggestion
    def ensure_loaded!(locale)
      LOAD_MUTEX.synchronize do
        @loaded_locales ||= []
        load_locale locale unless @loaded_locales.include?(locale)
      end
    end
```

:red_circle: [correctness] `NoMethodError` on every request when `SiteSetting.default_locale` is nil in config/initializers/i18n.rb:14 (confidence: 92)
`FallbackLocaleList#[]` calls `SiteSetting.default_locale.to_sym` unconditionally. If `default_locale` is nil — unset setting, early boot, migrations, or tests without a seeded DB — `.to_sym` raises `NoMethodError`. The `.compact` in the array literal runs after `.to_sym` is evaluated, so it does not protect against nil. This method is on the hot request path via `set_locale` → `I18n.fallbacks.ensure_loaded!` → `self[I18n.locale]`, meaning a nil value breaks every request.
```suggestion
  def [](locale)
    default = SiteSetting.default_locale&.to_sym
    [locale, default, :en].uniq.compact
  end
```

## Risk Metadata
Risk Score: 19/100 (LOW) | Blast Radius: application_controller.rb is a Rails base class, translate_accelerator.rb monkey-patches core I18n — wide runtime reach | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold 85: FallbackLocaleList Hash-contract gap [82], initializer load-order comment-only [80], development.rb possibly missed [70])
