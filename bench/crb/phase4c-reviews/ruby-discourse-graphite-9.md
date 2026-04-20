## Summary
7 files changed, 31 lines added, 15 lines deleted. 4 findings (4 critical, 0 improvements, 0 nitpicks).
FallbackLocaleList has zero test coverage for fallback-order logic.

## Critical

:red_circle: [testing] FallbackLocaleList has zero test coverage for fallback-order logic in config/initializers/i18n.rb:1 (confidence: 97)
No tests exercise: (1) user=site dedup via uniq; (2) compact when default_locale is nil; (3) user=:en collapsing; (4) ensure_loaded! calls I18n.ensure_loaded! for each resolved locale. Regressions in any of these behaviors will be silent.
```suggestion
RSpec.describe FallbackLocaleList do
  let(:list) { FallbackLocaleList.new }
  before { SiteSetting.default_locale = "fr" }

  it "returns [user, site, :en] in order" do
    expect(list[:de]).to eq([:de, :fr, :en])
  end

  it "dedups when user==site" do
    SiteSetting.default_locale = "de"
    expect(list[:de]).to eq([:de, :en])
  end

  it "compacts nil default_locale" do
    SiteSetting.default_locale = nil
    expect(list[:de]).to eq([:de, :en])
  end
end
```

:red_circle: [testing] ensure_loaded! idempotency logic has no test coverage in lib/freedom_patches/translate_accelerator.rb:62 (confidence: 95)
The memoization contract — load_locale called exactly once per locale across multiple ensure_loaded! calls — is entirely untested. Any regression in the check-then-act guard (e.g., a rebase drops the @loaded_locales guard) will be invisible until production behavior changes.
```suggestion
it "does not re-load an already-loaded locale" do
  allow(I18n).to receive(:load_locale)
  I18n.ensure_loaded!(:fr)
  I18n.ensure_loaded!(:fr)
  expect(I18n).to have_received(:load_locale).with(:fr).once
end
```

:red_circle: [correctness] NoMethodError if SiteSetting.default_locale returns nil — .to_sym called before .compact in config/initializers/i18n.rb:13 (confidence: 85)
In FallbackLocaleList#[], SiteSetting.default_locale.to_sym is evaluated before the array is constructed. If SiteSetting.default_locale returns nil, Ruby raises NoMethodError: undefined method 'to_sym' for nil:NilClass. The .compact at the end cannot protect against this because .to_sym is called on nil first, before the array is ever built.
```suggestion
def [](locale)
  [locale, SiteSetting.default_locale&.to_sym, :en].uniq.compact
end
```

:red_circle: [security] Custom fallback bypasses I18n locale normalization; attacker-controlled locale flows to file loads in config/initializers/i18n.rb:13 (confidence: 85)
FallbackLocaleList#[] returns [locale, SiteSetting.default_locale.to_sym, :en] without validating locale. The locale value originates from user input (Accept-Language header, cookie, or ?locale= param). The new Hash subclass bypasses normalization that I18n::Locale::Fallbacks would normally perform. The chain is forwarded to I18n.ensure_loaded! which calls load_locale and reads YAML files using the locale symbol. Discourse's set_locale validates against available_locales upstream, so direct exploitation depends on that guard being tight — this is primarily a defense-in-depth regression: removing normalization that previously existed for free. This fix also subsumes the nil-safety issue on the same lines.
```suggestion
def [](locale)
  allowed = I18n.available_locales.map(&:to_sym)
  [locale, SiteSetting.default_locale&.to_sym, :en].uniq.compact.select { |l| allowed.include?(l) }
end
```
[References: Discourse set_locale validation in app/controllers/application_controller.rb]

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: application_controller (base class), translate_accelerator (global I18n monkey patch), i18n initializer (global) | Sensitive Paths: none
AI-Authored Likelihood: LOW

(4 additional findings below confidence threshold)
