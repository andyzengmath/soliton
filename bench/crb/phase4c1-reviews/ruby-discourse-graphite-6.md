## Summary
5 files changed, 44 lines added, 12 lines deleted. 8 findings (3 critical, 5 improvements, 0 nitpicks).
String mutation via `<<` operator in `user_serializer.rb:137` raises FrozenError or corrupts state across calls; case-sensitive host comparison and untested elsif branch compound the correctness risk.

## Critical

:red_circle: [correctness] String mutation via `<<` operator raises FrozenError or causes cumulative corruption in app/serializers/user_serializer.rb:137 (confidence: 95)
The expression `"." << website_host` uses Ruby's `String#<<` which mutates its receiver (the string literal `"."`). This produces two distinct failure modes: (1) Under `# frozen_string_literal: true` — present in many Discourse source files — any user whose website triggers the third branch will produce a `FrozenError`, crashing the serializer and returning a 500. An attacker can deliberately craft a website URL that reliably hits this branch to cause a denial-of-service on profile rendering. (2) Without frozen literals, the `"."` literal may be reused across calls, accumulating characters across invocations (`".example.com"`, `"..example.com"`, etc.), silently corrupting the suffix check for all subsequent users after the first call.
```suggestion
      discourse_host.ends_with?(".#{website_host}") ? website_host + URI(website.to_s).path : website_host
```
[References: https://cwe.mitre.org/data/definitions/400.html]

:red_circle: [correctness] Case-insensitive hostname comparison missing — uppercase hostnames fall through to wrong branch in app/serializers/user_serializer.rb:134 (confidence: 92)
DNS hostnames are case-insensitive per RFC 4343. `URI("http://EXAMPLE.COM/path").host` returns `"EXAMPLE.COM"` preserving the original casing, while `Discourse.current_hostname` typically returns a lowercase hostname. The equality check `website_host == discourse_host` is case-sensitive, so a user with website `"http://EXAMPLE.COM/page"` fails the first branch, falls through to the subdomain check, and produces incorrect output. The same flaw affects the subdomain tail comparison in the elsif branch.
```suggestion
    website_host = URI(website.to_s).host&.downcase rescue nil
    discourse_host = Discourse.current_hostname.downcase
```

:red_circle: [testing] Elsif branch (same-depth sibling subdomains) has zero test coverage in app/serializers/user_serializer.rb:137 (confidence: 95)
The elsif branch — triggered when two hosts share the same segment count and have more than 2 segments (e.g., `www.example.com` vs `forum.example.com`) — is not exercised by any of the three added tests. Both the positive case (sibling subdomains sharing a parent domain, expect path appended) and the negative case (sibling subdomains belonging to unrelated parents, expect host only) are untested. The string-splitting logic in this branch is non-trivial and easy to mis-implement, as evidenced by the weak parent-domain match issue surfaced below.
```suggestion
it "returns complete website path when website is on a sibling subdomain of the instance" do
  user.user_profile.website = 'http://www.example.com/user'
  Discourse.stubs(:current_hostname).returns('forum.example.com')
  expect(json[:website_name]).to eq 'www.example.com/user'
end

it "returns only the host when sibling subdomains belong to different parent domains" do
  user.user_profile.website = 'http://www.other.com/user'
  Discourse.stubs(:current_hostname).returns('forum.example.com')
  expect(json[:website_name]).to eq 'www.other.com'
end
```

## Improvements

:yellow_circle: [correctness] Subdomain length-equality branch has weak parent-domain match logic in app/serializers/user_serializer.rb:141 (confidence: 90)
The condition requires equal segment counts and more than 2 segments, then compares only `[1..-1]` (everything after the first label). The logic only works correctly for exactly 3-segment hosts (e.g., `www.example.com` vs `blog.example.com`). For deeper nesting (`a.b.example.com` vs `c.d.example.com`) or multi-part TLDs such as `co.uk`, the comparison produces wrong results.
```suggestion
    elsif website_host.split('.').last(2).join('.') == discourse_host.split('.').last(2).join('.')
      website_host + URI(website.to_s).path
```

:yellow_circle: [testing] No test for malformed or empty website URL early-return path in spec/serializers/user_serializer_spec.rb:66 (confidence: 90)
No spec confirms the method returns nil (and does not raise) when given a malformed URL that triggers the `rescue nil` clause. Removing the rescue or changing its behavior in a future refactor would go undetected.
```suggestion
it "returns nil when the website URL is malformed" do
  user.user_profile.website = 'not-a-valid-url'
  expect(json[:website_name]).to be_nil
end
```

:yellow_circle: [correctness] URI parsed three times — redundant work and minor atomicity risk in app/serializers/user_serializer.rb:134 (confidence: 88)
Each branch calls `URI(website.to_s).path`, recreating the URI object on every invocation. Parsing once at the top of the method is more efficient, avoids any non-atomic re-reads of the `website` accessor between branches, and is a prerequisite for the downcase fix above.
```suggestion
  def website_name
    uri = URI(website.to_s) rescue nil
    return if uri.nil? || uri.host.nil?
    website_host   = uri.host.downcase
    website_path   = uri.path
    discourse_host = Discourse.current_hostname.downcase
    # ... branches use website_host and website_path, not re-parsed URI
  end
```

:yellow_circle: [testing] No test for website URL with query string, fragment, or port in spec/serializers/user_serializer_spec.rb:66 (confidence: 85)
`URI#path` excludes query string and fragment — likely the intended behavior — but no test documents this. A future refactor switching to `URI#request_uri` or `URI#to_s` could silently change the output and no test would catch the regression.
```suggestion
it "returns only the path portion (no query string) when the website URL has a query string" do
  user.user_profile.website = 'http://example.com/user?tab=1'
  Discourse.stubs(:current_hostname).returns('example.com')
  expect(json[:website_name]).to eq 'example.com/user'
end
```

:yellow_circle: [correctness] Single-segment website host (e.g. `localhost`) can spuriously match via suffix check in app/serializers/user_serializer.rb:146 (confidence: 85)
If `discourse_host` is `"a.localhost"` and `website_host` is `"localhost"` (different segment counts), the third branch fires because `"a.localhost".ends_with?(".localhost")` is true, and the method returns `"localhost" + path`. A user linking to `http://localhost/secret-path` on an instance running at `a.localhost` has their path unexpectedly exposed. This is a logic error primarily affecting dev and staging environments.
```suggestion
    else
      if website_host.include?('.') && discourse_host.ends_with?(".#{website_host}")
        website_host + URI(website.to_s).path
      else
        website_host
      end
    end
```

## Risk Metadata
Risk Score: 20/100 (LOW) | Blast Radius: low (serializer + related Ember views; additive change) | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(7 additional findings below confidence threshold)
