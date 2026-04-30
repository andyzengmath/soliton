## Summary
7 files changed, 31 lines added, 19 lines deleted. 2 findings (2 critical, 0 improvements, 0 nitpicks).
TOCTOU race condition in lib/freedom_patches/translate_accelerator.rb:62

## Critical

:red_circle: [correctness] TOCTOU race condition in ensure_loaded! on shared module-level @loaded_locales in lib/freedom_patches/translate_accelerator.rb:62 (confidence: 92)
The new `ensure_loaded!` method reads and initializes `@loaded_locales` outside of any mutex. In a multi-threaded server (Puma), `@loaded_locales` is a module-level instance variable on the `I18n` module — shared across all threads. Two threads can both evaluate `@loaded_locales.include?(locale)` as false simultaneously and both proceed to call `load_locale`. More critically, `@loaded_locales ||= []` is itself non-atomic: if two threads race when `@loaded_locales` is nil, one may overwrite the other's initialized array, or both may see nil and initialize separate arrays — one of which is then discarded. The existing `translate` method has the same pattern but `load_locale` is already correctly designed to be idempotent under a mutex. The redundant unsynchronized guard in `ensure_loaded!` adds race exposure without adding safety. The fix is to remove the guard and delegate directly to `load_locale`, which already owns the mutex.
```suggestion
def ensure_loaded!(locale)
  load_locale(locale)
end
```

:red_circle: [correctness] FallbackLocaleList#ensure_loaded! implicitly reads I18n.locale — fragile ordering dependency in config/initializers/i18n.rb:20 (confidence: 85)
`FallbackLocaleList#ensure_loaded!` reads `I18n.locale` at call time with no argument. In `set_locale`, this is called at the end of the method. If `I18n.locale =` has not yet been assigned when `ensure_loaded!` executes, `I18n.locale` will return the locale from the previous request (thread-local) or `:en` (the default), causing the wrong set of fallback locales to be pre-loaded. The implicit read of global/thread-local state makes this fragile and order-sensitive. Passing the locale explicitly eliminates the dependency on call ordering and makes the method safe to call at any point.
```suggestion
# In config/initializers/i18n.rb — make the locale parameter explicit:
def ensure_loaded!(locale = I18n.locale)
  self[locale].each { |l| I18n.ensure_loaded!(l) }
end

# At the call site in set_locale, pass the locale directly:
I18n.fallbacks.ensure_loaded!(I18n.locale)
```

## Risk Metadata
Risk Score: 30/100 (LOW) | Blast Radius: framework-level (ApplicationController, I18n module patches) | Sensitive Paths: none
AI-Authored Likelihood: MEDIUM

(1 additional findings below confidence threshold)
