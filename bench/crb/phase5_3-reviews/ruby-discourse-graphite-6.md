## Summary
5 files changed, 44 lines added, 12 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
5 files changed. 3 findings (1 critical, 2 improvements). Inline rescue swallows all StandardError in user_serializer.rb:137.

## Critical
:red_circle: [correctness] Inline rescue swallows all StandardError, masking real bugs in app/serializers/user_serializer.rb:137 (confidence: 92)
The expression `URI(website.to_s).host rescue nil` uses Ruby's inline rescue modifier, which catches all StandardError subclasses — not just URI::InvalidURIError. ArgumentError, NoMethodError, and TypeError are silently absorbed. When this happens, `website_host` becomes nil, the method returns nil early, and the serializer emits no website_name field. A real bug — a broken `website` method or a bad object stored in user_profile — is invisible in production.
```suggestion
begin
  website_host = URI(website.to_s).host
rescue URI::InvalidURIError
  return nil
end
```
[References: https://rubystyle.guide/#no-rescue-modifiers]

## Improvements
:yellow_circle: [correctness] URI(website.to_s) parsed multiple times with inconsistent error guarding in app/serializers/user_serializer.rb:134 (confidence: 90)
The method calls `URI(website.to_s).host` once on line 137 with an inline rescue, but then calls `URI(website.to_s).path` up to three more times inside the conditional branches (lines 140-148) without any error guard. This is both redundant and inconsistent: if the URI is valid on the first parse, re-parsing it is wasteful; if the URI somehow raises on the second parse (e.g., due to mutation or a different code path), the exception propagates uncaught. The URI object should be parsed once into a local variable and reused throughout the method.
```suggestion
uri = URI(website.to_s) rescue nil
return nil unless uri
website_host = uri.host
return nil unless website_host
# ... use uri.path in subsequent branches
```

:yellow_circle: [testing] Test suite covers only happy-path URLs — error and edge cases untested in spec/serializers/user_serializer_spec.rb:1 (confidence: 90)
The spec for website_name tests only three valid URL combinations. It does not assert behavior for nil website, empty string website, malformed URIs ("not a url", "javascript:alert(1)"), URIs with no host component (relative URLs), or IP-address hosts. The `rescue nil` safety net is completely unverified. The IP-address case is also relevant because the dot-count comparison logic could misclassify an IPv4 address like "1.2.3.4" (4 segments) against a discourse host.
```suggestion
context "when website is nil" do
  before { user.user_profile.update!(website: nil) }
  it { expect(json[:website_name]).to be_nil }
end

context "when website is malformed" do
  before { user.user_profile.update!(website: "not a url") }
  it { expect(json[:website_name]).to be_nil }
end

context "when website is an IP address" do
  before { user.user_profile.update!(website: "http://1.2.3.4/path") }
  it { expect(json[:website_name]).to eq("1.2.3.4") }
end
```

## Risk Metadata
Risk Score: 14/100 (LOW) | Blast Radius: 0 detected importers (sparse repo) | Sensitive Paths: none hit
AI-Authored Likelihood: N/A

(3 additional findings below confidence threshold)
