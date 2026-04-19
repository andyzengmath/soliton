## Summary
5 files changed, 44 lines added, 12 lines deleted. 9 findings (3 critical, 6 improvements).
New `website_name` serializer shows the full URL path when the profile website matches the Discourse instance domain, but the same-domain heuristic is spoof-friendly, the template binding is undeclared on the Ember model, and a mutating `<<` on a string literal will raise under `frozen_string_literal: true`.

## Critical

:red_circle: [correctness] `"." << website_host` mutates a string literal — will raise FrozenError under frozen_string_literal in app/serializers/user_serializer.rb:144 (confidence: 92)
The expression `"." << website_host` uses the destructive shovel operator on the string literal `"."`. Under `# frozen_string_literal: true` (Discourse enables this broadly) the literal is frozen and `<<` raises `FrozenError: can't modify frozen String` at runtime, breaking every profile page where the branch is taken. Even without the pragma, the mutation aliases `website_host` through the subsequent `website_host + URI(website.to_s).path` (the mutated receiver can reappear) and the pattern is fragile. Use non-mutating concatenation or interpolation.
```suggestion
discourse_host.ends_with?(".#{website_host}") ? website_host + URI(website.to_s).path : website_host
```

:red_circle: [security] Sibling-subdomain / suffix-match logic lets attackers craft convincing phishing link text in app/serializers/user_serializer.rb:134 (confidence: 85)
`website_name` concatenates the user-controlled URL path onto a host that the user can trivially cause to pass the "same organization" checks. If Discourse runs at `forum.company.com`, an attacker sets their profile website to `https://evil.company.com/admin/login?token=…`; the elsif branch evaluates `company.com == company.com` and the rendered link text becomes `evil.company.com/admin/login?token=…` while the href points wherever the attacker chose. The `ends_with?` branch has the same shape: attacker registers `example.com`, picks path `/forum/session/sso_login`, and the visible text looks like an internal Discourse URL. Because the companion Handlebars change swaps the client-side computed `websiteName` for the serialized `model.website_name`, this spoofed text is now authoritative display for every user-profile link.
```suggestion
def website_name
  uri = URI.parse(website.to_s) rescue nil
  return nil unless uri && uri.host && %w[http https].include?(uri.scheme)
  # Display only the host — never concatenate user-controlled path/query.
  uri.host.downcase
end
```
[References: https://owasp.org/www-community/attacks/Phishing, https://cwe.mitre.org/data/definitions/601.html, https://cwe.mitre.org/data/definitions/1021.html]

:red_circle: [cross-file-impact] `model.website_name` is undeclared on the Ember User model — template binding will always be undefined in app/assets/javascripts/discourse/templates/user/user.hbs:66 (confidence: 85)
The template now reads `model.website_name` in place of the removed controller-level `websiteName` computed property, but `app/assets/javascripts/discourse/models/user.js.es6` is not updated to declare the attribute. Discourse's `RestModel` does not auto-expose arbitrary server JSON keys as reactive Ember properties, so `{{#if model.website_name}}` will evaluate falsy and the entire website block (anchor + fallback span) silently disappears from every profile — a runtime regression with no error. The JSDoc edit in the same file (`@property websiteName` → `@property profileBackground`) does not add the attr; it's an unrelated doc fix.
```suggestion
// In app/assets/javascripts/discourse/models/user.js.es6, add:
websiteName: Discourse.computed.fmt('website_name', '%@'),
// or declare the field so it round-trips from the serializer:
website_name: null,
```

## Improvements

:yellow_circle: [correctness] `URI(website.to_s)` parsed up to three times — rescue guards only the first call in app/serializers/user_serializer.rb:134 (confidence: 95)
`website` is parsed once into `website_host` with `rescue nil`, but every branch re-parses via `URI(website.to_s).path` without a rescue. The later calls will not raise when the first succeeded (same input), so the defect is latent, but the duplicated parsing obscures intent, makes the ternaries fragile, and mixes `rescue nil` with bare calls. Parse once and reuse `.host`/`.path`.
```suggestion
def website_name
  uri = URI(website.to_s) rescue nil
  return if uri.nil? || uri.host.nil?
  website_host = uri.host
  website_path = uri.path
  discourse_host = Discourse.current_hostname
  if website_host == discourse_host
    website_host + website_path
  elsif website_host.split('.').length == discourse_host.split('.').length && discourse_host.split('.').length > 2
    website_host.split('.')[1..-1].join('.') == discourse_host.split('.')[1..-1].join('.') ? website_host + website_path : website_host
  else
    discourse_host.ends_with?(".#{website_host}") ? website_host + website_path : website_host
  end
end
```

:yellow_circle: [correctness] Tail-match logic misclassifies ccTLDs such as `co.uk`, `com.au` as shared domain in app/serializers/user_serializer.rb:142 (confidence: 88)
The elsif treats any two hosts with the same (>2) number of segments and matching tails as the same organisation. For ccTLDs with an effective second-level suffix (e.g. `foo.co.uk` vs `bar.co.uk`), both hosts split into three segments and the tail comparison yields `co.uk == co.uk`, so the code happily appends the user-controlled path. The comment `# www.example.com == forum.example.com` misleads about the branch's scope. Use the Public Suffix list (the `public_suffix` gem) to compute the registrable domain rather than naïve segment counting.
```suggestion
# Use PublicSuffix.domain(host) for the eTLD+1 comparison so
# registrable domains are correctly identified across ccTLDs.
```

:yellow_circle: [security] `target="_blank"` without `rel="noopener noreferrer"` on user-controlled href enables reverse tabnabbing in app/assets/javascripts/discourse/templates/user/user.hbs:68 (confidence: 90)
The profile website link opens a user-supplied URL in a new tab and conditionally sets `rel` to `nofollow` or nothing — never `noopener`/`noreferrer`. The opened page receives `window.opener` and can redirect the parent tab to a phishing page indistinguishable from Discourse. Modern browsers imply `noopener` for `target="_blank"`, but embedded views and older clients do not. The PR already touches this exact line, so fix it now.
```suggestion
<a href={{model.website}}
   rel={{if removeNoFollow "noopener noreferrer" "nofollow noopener noreferrer"}}
   target="_blank">{{model.website_name}}</a>
```
[References: https://owasp.org/www-community/attacks/Reverse_Tabnabbing, https://cwe.mitre.org/data/definitions/1022.html]

:yellow_circle: [testing] Branch B (sibling-subdomain elsif) is completely untested in spec/serializers/user_serializer_spec.rb:66 (confidence: 95)
The elsif fires when both hosts have the same segment count (>2). It has two sub-paths — shared parent matches (return host+path) vs shared parent differs (return host only) — and neither is covered. The three added specs only exercise Branch A (identical hosts), the negative of Branch C (unrelated hosts), and the positive of Branch C (Discourse is a subdomain of the website). A regression in the elsif body would not be caught.
```suggestion
it "returns host + path when discourse is a sibling subdomain sharing the parent" do
  Discourse.stubs(:current_hostname).returns('www.example.com')
  user.user_profile.website = 'http://forum.example.com/user'
  expect(json[:website_name]).to eq 'forum.example.com/user'
end

it "returns host only when subdomains share segment count but different parents" do
  Discourse.stubs(:current_hostname).returns('www.example.com')
  user.user_profile.website = 'http://forum.other.com/user'
  expect(json[:website_name]).to eq 'forum.other.com'
end
```

:yellow_circle: [testing] Malformed website URL path (the `rescue nil` branch) is untested in spec/serializers/user_serializer_spec.rb:66 (confidence: 92)
`URI(website.to_s).host rescue nil` is the central safety net, but no test exercises a malformed value such as `'not a url'` or `'http://:80'`. If the rescue is removed or the guard changes, the serializer will raise from the controller layer and users with slightly-malformed profile URLs will 500 their own pages.
```suggestion
context "when website is malformed" do
  before { user.user_profile.website = 'not a valid url' }

  it "returns nil website_name" do
    expect(json[:website_name]).to be_nil
  end
end
```

:yellow_circle: [testing] Nil / blank website inputs are untested in spec/serializers/user_serializer_spec.rb:66 (confidence: 88)
When `website` is nil or an empty string, `URI('').host` is nil and the early guard returns. This is the default state for most users — a fundamental boundary that should be locked in.
```suggestion
context "when website is blank" do
  before { user.user_profile.website = nil }

  it "omits website_name from the payload" do
    expect(json).not_to have_key(:website_name)
  end
end
```

## Risk Metadata
Risk Score: 15/100 (LOW) | Blast Radius: shim repo — 0 importers observable | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold)
