## Summary
10 files changed, 155 lines added, 23 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
Unanchored domain regex allows whitelist/blacklist bypass in `lib/validators/email_validator.rb:18`.

## Critical
:red_circle: [correctness] Unanchored domain regex allows suffix-match bypass of whitelist and blacklist in lib/validators/email_validator.rb:18 (confidence: 95)
`email_in_restriction_setting?` builds the regex `@(example\.com)` (dots escaped, no left boundary or end anchor). For whitelist entry `example.com`, the email `user@evil-example.com` matches because `example.com` appears as a right-aligned substring of `evil-example.com`. This bypass works for both whitelist (attacker gains access) and blacklist (blocked domain circumvented), defeating the security boundary of the feature. The original method in `app/models/user.rb` had the same flaw; the refactor into `EmailValidator` preserved it unchanged.
```suggestion
def email_in_restriction_setting?(setting, value)
  domains = setting.split(/[\s,|]+/).map { |d| Regexp.escape(d) }
  regexp = Regexp.new("@(.*\\.)?(#{domains.join('|')})\\z", true)
  value =~ regexp
end
```

## Improvements
:yellow_circle: [correctness] Non-atomic read-modify-write on match_count causes lost updates under concurrency in app/models/blocked_email.rb:12 (confidence: 90)
`should_block?` reads the record into Ruby memory, increments `match_count` in Ruby, then calls `record.save`. Under concurrent requests for the same blocked email, two processes can both read `match_count = N`, both compute `N+1`, and both write `N+1`, losing one increment. Additionally, if `record.save` returns `false` (e.g., uniqueness race or callback failure), the failure is silently ignored. Because this runs inside `User` validation wrapped in a transaction, a later `User.save` rollback will also roll back the counter update even though the method already returned its blocking decision.
```suggestion
def self.should_block?(email)
  record = BlockedEmail.find_by(email: email)
  if record
    BlockedEmail.where(id: record.id)
                .update_all("match_count = match_count + 1, last_match_at = NOW()")
  end
  record && record.action_type == actions[:block]
end
```

:yellow_circle: [consistency] Use `find_by` instead of `.where(...).first` in app/models/blocked_email.rb:13 (confidence: 90)
The code uses `BlockedEmail.where(email: email).first` but Active Record convention (and Discourse convention) prefers `find_by(email: email)` when retrieving a single record by attribute. `find_by` is more idiomatic and clearer in intent.
```suggestion
record = BlockedEmail.find_by(email: email)
```

:yellow_circle: [consistency] Use `&&` instead of `and` — low operator precedence is an idiomatic hazard in lib/validators/email_validator.rb:13 (confidence: 85)
The code uses `and` in `if record.errors[attribute].blank? and BlockedEmail.should_block?(value)`. Ruby idiom (and Discourse convention) prefers `&&` for boolean conjunction because `and` has lower precedence than `=` and can cause subtle bugs in future edits. While this line works correctly today, the pattern is inconsistent with the rest of the codebase.
```suggestion
if record.errors[attribute].blank? && BlockedEmail.should_block?(value)
```

## Risk Metadata
Risk Score: 13/100 (LOW) | Blast Radius: 0 (shim repo, no importers resolvable) | Sensitive Paths: none matched (but user-registration/email-validation are functionally security-relevant)
AI-Authored Likelihood: LOW

(1 additional finding below confidence threshold)
