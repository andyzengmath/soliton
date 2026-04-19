## Summary
7 files changed, 31 lines added, 15 lines deleted. 4 findings (4 critical, 0 improvements, 0 nitpicks).
Race condition on @loaded_locales in translate_accelerator.rb:62 and unsafe SiteSetting.default_locale call inside FallbackLocaleList#[] dominate the review.

## Critical

:red_circle: [testing] ensure_loaded! has no test coverage — idempotency and concurrency behaviour untested in lib/freedom_patches/translate_accelerator.rb:62 (confidence: 95)
The new `ensure_loaded!` method initialises `@loaded_locales` on first call and guards `load_locale` with an include-check. No spec exists for this method. Idempotency (calling twice does not call `load_locale` twice) and thread-safety (non-atomic read-modify-write) are both unverified. This gap means the race condition identified in the correctness review has no regression harness.
```suggestion
# Add spec at spec/lib/freedom_patches/translate_accelerator_spec.rb
RSpec.describe "I18n.ensure_loaded!" do
  before { I18n.instance_variable_set(:@loaded_locales, nil) }

  it "calls load_locale on first invocation" do
    expect(I18n).to receive(:load_locale).with(:fr).once
    I18n.ensure_loaded!(:fr)
  end

  it "is idempotent for the same locale" do
    allow(I18n).to receive(:load_locale)
    I18n.ensure_loaded!(:fr)
    expect(I18n).not_to receive(:load_locale)
    I18n.ensure_loaded!(:fr)
  end

  it "loads distinct locales independently" do
    expect(I18n).to receive(:load_locale).with(:fr).once
    expect(I18n).to receive(:load_locale).with(:de).once
    I18n.ensure_loaded!(:fr)
    I18n.ensure_loaded!(:de)
  end
end
```

:red_circle: [correctness] Race condition on @loaded_locales in ensure_loaded! under Puma multi-threading in lib/freedom_patches/translate_accelerator.rb:62 (confidence: 92)
`ensure_loaded!` performs a non-atomic check-then-act on `@loaded_locales`. In a multi-threaded Puma server, two concurrent requests for a locale not yet loaded can both pass the `@loaded_locales.include?(locale)` check simultaneously and both enter `load_locale`. The `@loaded_locales ||= []` initialization has the same race: two threads can both observe nil and both assign a new array, leading to duplicate locale loads and potential translation-table corruption. The pre-existing `translate` method avoided this because locales were loaded at boot; `ensure_loaded!` is now invoked per-request with arbitrary locales, so it is fully exposed to this race.
```suggestion
LOAD_LOCALE_MUTEX = Mutex.new

def ensure_loaded!(locale)
  return if @loaded_locales&.include?(locale)
  LOAD_LOCALE_MUTEX.synchronize do
    @loaded_locales ||= []
    load_locale locale unless @loaded_locales.include?(locale)
  end
end
```

:red_circle: [testing] FallbackLocaleList — [] override and ensure_loaded! have zero test coverage in config/initializers/i18n.rb:12 (confidence: 92)
`FallbackLocaleList` is entirely new and has no specs. Critical untested scenarios include: deduplication when the user locale equals the site default; deduplication when both locale and site default are `:en`; nil returned from `SiteSetting.default_locale` causing `to_sym` to raise before `.compact` can act; and `ensure_loaded!` iterating and loading every locale in the chain.
```suggestion
# Add spec at spec/config/initializers/i18n_spec.rb
RSpec.describe FallbackLocaleList do
  let(:list) { FallbackLocaleList.new }

  before { allow(SiteSetting).to receive(:default_locale).and_return("fr") }

  describe "#[]" do
    it "returns [locale, site_locale, :en]" do
      expect(list[:de]).to eq([:de, :fr, :en])
    end

    it "deduplicates when locale equals site default" do
      expect(list[:fr]).to eq([:fr, :en])
    end

    it "deduplicates when everything is :en" do
      allow(SiteSetting).to receive(:default_locale).and_return("en")
      expect(list[:en]).to eq([:en])
    end
  end

  describe "#ensure_loaded!" do
    it "loads every locale in the fallback chain" do
      allow(I18n).to receive(:locale).and_return(:de)
      expect(I18n).to receive(:ensure_loaded!).with(:de)
      expect(I18n).to receive(:ensure_loaded!).with(:fr)
      expect(I18n).to receive(:ensure_loaded!).with(:en)
      list.ensure_loaded!
    end
  end
end
```

:red_circle: [correctness] SiteSetting.default_locale called on every I18n fallback lookup — unsafe outside request context in config/initializers/i18n.rb:13 (confidence: 88)
`FallbackLocaleList#[]` is assigned to `I18n.fallbacks` and is invoked by I18n on every translation lookup, not just during `ensure_loaded!`. Any translation call from a background job, Rake task, asset pipeline, console session, or test suite will reach into `SiteSetting.default_locale` via this path. In those contexts the database may not be seeded or the connection pool may be unavailable, producing `ActiveRecord::StatementInvalid` / `PG::ConnectionBad` during translation. The previous approach (`config.i18n.fallbacks = true`) was safe because it read `I18n.default_locale`, a plain Ruby value set at boot. Additionally, `SiteSetting.default_locale.to_sym` raises `NoMethodError` if `default_locale` returns nil, before the trailing `.compact` can rescue the array.
```suggestion
class FallbackLocaleList < Hash
  def [](locale)
    site_locale =
      begin
        SiteSetting.default_locale&.to_sym || :en
      rescue StandardError
        I18n.default_locale
      end
    [locale, site_locale, :en].uniq.compact
  end

  def ensure_loaded!
    self[I18n.locale].each { |l| I18n.ensure_loaded! l }
  end
end
```

## Risk Metadata
Risk Score: 37/100 (MEDIUM) | Blast Radius: application_controller.rb + translate_accelerator.rb + i18n.rb initializer all globally reached (blast factor score 90) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
