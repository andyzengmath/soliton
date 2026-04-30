## Summary
10 files changed, 155 lines added, 23 lines deleted. 2 findings (2 critical, 0 improvements, 0 nitpicks).
Unconditional DB writes and enumeration oracle in `BlockedEmail.should_block?` require immediate remediation; insufficient regex escaping in `email_in_restriction_setting?` enables ReDoS and silent allow/blocklist bypass.

## Critical

:red_circle: [correctness, security] BlockedEmail.should_block? unconditionally writes to DB on every call including :do_nothing rows, with a race condition and unauthenticated enumeration oracle in app/models/blocked_email.rb:10 (confidence: 95)

Two overlapping problems converge on this method:

CORRECTNESS: `should_block?` increments `match_count`, updates `last_match_at`, and calls `record.save` whenever any `BlockedEmail` row is found, regardless of whether `action_type` is `:block` or `:do_nothing`. This means:
- `:do_nothing` rows have their counters incremented despite the pass-through intent.
- `record.save` executes outside the caller's transaction, so counter increments survive even when the parent transaction is rolled back.
- Save failures are silently swallowed.
- `match_count += 1; save` is a read-modify-write race under concurrent requests.

SECURITY: `should_block?` is called from `EmailValidator` during the unauthenticated signup endpoint with no rate limiting. Every blocked-email hit triggers a DB write, creating write-amplification DoS. The structured `errors: user.errors.to_hash` response and the measurable latency delta (DB write present vs. absent) expose a timing and error-message oracle that allows unauthenticated callers to enumerate which email addresses are on the blocklist.

```suggestion
def self.should_block?(email)
  record = BlockedEmail.find_by(email: email)
  return false unless record
  if record.action_type == actions[:block]
    BlockedEmail.where(id: record.id).update_all(
      'match_count = COALESCE(match_count, 0) + 1, last_match_at = CURRENT_TIMESTAMP'
    )
    true
  else
    false
  end
end
```
[References: https://owasp.org/Top10/A04_2021-Insecure_Design/, https://cwe.mitre.org/data/definitions/204.html, https://cwe.mitre.org/data/definitions/362.html]

:red_circle: [correctness, security] email_in_restriction_setting? performs insufficient regex escaping enabling ReDoS, regex injection, and silent allow/blocklist bypass for common separator characters in lib/validators/email_validator.rb:18 (confidence: 90)

Two overlapping problems converge on this method:

SECURITY (ReDoS / regex injection): The method builds a regex directly from `SiteSetting.email_domains_whitelist` / `email_domains_blacklist`, escaping only `.` characters with `gsub('.', '\.')`. All other regex metacharacters — including `(`, `)`, `+`, `*`, `?`, `[`, `]`, and `{` — pass through verbatim. An admin, or any attacker who can influence the setting (e.g., via a compromised admin account or a settings-write vulnerability), can craft patterns that cause catastrophic backtracking (e.g., `(a+)+$`), pinning a worker thread for seconds to minutes per signup request. Unescaped `|` in a domain entry also silently splits the pattern and can enable allow/blocklist bypass.

CORRECTNESS: The method only handles `|` as a separator. If an admin uses commas, whitespace, or newlines (all common delimiter choices), the regex wraps the entire raw setting string as a single domain literal and silently fails to match any valid email. A malformed setting string can also raise `RegexpError` during signup, causing an unhandled 500.

```suggestion
def email_in_restriction_setting?(setting, value)
  return false if value.to_s.length > 254
  domains = setting.split(/[\s,|]+/).map { |d| Regexp.escape(d.strip) }.reject(&:empty?)
  return false if domains.empty?
  regexp = /@(?:#{domains.join('|')})\z/i
  !!(value =~ regexp)
end
```
[References: https://owasp.org/www-community/attacks/Regular_expression_Denial_of_Service_-_ReDoS, https://cwe.mitre.org/data/definitions/1333.html]

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: user.rb / users_controller.rb / create_account_controller.js are architecturally central registration paths | Sensitive Paths: lib/validators/email_validator.rb, app/models/blocked_email.rb, app/controllers/users_controller.rb (auth-adjacent)
AI-Authored Likelihood: N/A

(2 additional findings below confidence threshold)
