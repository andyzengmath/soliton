## Summary
10 files changed, 155 lines added, 23 lines deleted. 11 findings (5 critical, 6 improvements, 0 nitpicks).
Non-atomic read-modify-write race condition on `match_count` in `blocked_email.rb`; email-domain regex is under-escaped (subdomain bypass + ReDoS); new JS cache, controller JSON shape, and user model refactor all lack test coverage.

## Critical

:red_circle: [correctness] Non-atomic read-modify-write race condition on match_count in app/models/blocked_email.rb:9 (confidence: 95)
`should_block?` performs `record.match_count += 1` followed by `record.save`. The read-modify-write happens in Ruby memory, not atomically at the database level. Under concurrent requests both threads read the same value, both write N+1, and one increment is silently lost. Additionally, `save` can silently swallow failures without raising.
```suggestion
def self.should_block?(email)
  record = BlockedEmail.where(email: email).first
  if record
    BlockedEmail.where(email: email).update_all(
      "match_count = match_count + 1, last_match_at = #{connection.quote(Time.zone.now)}"
    )
  end
  record && record.action_type == actions[:block]
end
```

:red_circle: [correctness] match_count inflated by repeated validation calls per registration attempt in app/models/blocked_email.rb:9 (confidence: 90)
`should_block?` is called from `EmailValidator#validate_each`. Rails invokes validations multiple times per registration lifecycle (`valid?`, `save`, sometimes `before_save` callbacks), so for a single signup attempt the counter is inflated by 2-3x. The method also fires for `do_nothing` records, polluting the metric with false matches.
```suggestion
# Pure check — no side-effects, safe to call from validators:
def self.blocked?(email)
  record = find_by(email: email)
  record && record.action_type == actions[:block]
end

# Separate mutation called once from the controller after a confirmed block decision:
def self.record_match!(email)
  where(email: email).update_all(
    "match_count = match_count + 1, last_match_at = #{connection.quote(Time.zone.now)}"
  )
end
```

:red_circle: [testing] No tests for rejectedEmails caching behavior in create_account_controller in app/assets/javascripts/discourse/controllers/create_account_controller.js:1 (confidence: 95)
The new `rejectedEmails` client-side cache has zero JS test coverage (no QUnit/Ember spec). Cache-key bugs or missing invalidation logic would silently degrade the account-creation UX, which is a critical user-facing flow. The unbounded-growth issue (see improvement finding on the same file) is also entirely untested.
```suggestion
// test/javascripts/controllers/create-account-test.js
test("rejectedEmails: suppresses duplicate server call for the same rejected email", async function (assert) { ... });
test("rejectedEmails: re-validates on a different email value", async function (assert) { ... });
test("rejectedEmails: clears/ignores cache when the email field changes", async function (assert) { ... });
```

:red_circle: [testing] No tests for the updated JSON error response shape in users_controller in app/controllers/users_controller.rb:1 (confidence: 92)
The controller JSON response now includes `errors` and `values` hashes. The JavaScript client depends on this exact shape. No controller or request spec asserts the presence of these keys. A key typo (e.g., `"error"` vs `"errors"`) would silently drop client-side feedback with no test failure.
```suggestion
# spec/requests/users_controller_spec.rb
describe "POST /users (blocked email)" do
  before { Fabricate(:blocked_email, email: "bad@spamclub.com", action_type: BlockedEmail.actions[:block]) }

  it "returns an errors hash with the email key" do
    post "/users", params: { username: "alice", email: "bad@spamclub.com", password: "supersecret" }
    expect(response.parsed_body["errors"]["email"]).to be_present
  end

  it "returns a values hash so the form can repopulate" do
    post "/users", params: { username: "alice", email: "bad@spamclub.com", password: "supersecret" }
    expect(response.parsed_body["values"]["email"]).to eq("bad@spamclub.com")
  end

  it "responds with HTTP 422" do
    post "/users", params: { username: "alice", email: "bad@spamclub.com", password: "supersecret" }
    expect(response).to have_http_status(:unprocessable_entity)
  end
end
```

:red_circle: [testing] user_spec not updated to cover the email validation refactor in app/models/user.rb:1 (confidence: 88)
Email validation was moved from an inline `email_validator` method to an `ActiveModel::EachValidator`. The existing `user_spec` has not been updated to reflect this change. Regressions in the validation flow — blocked emails slipping through, valid emails being rejected, or error-message drift — would go undetected at the model level.
```suggestion
describe "email validations (post-refactor)" do
  it "is invalid when the email is on the block list" do
    BlockedEmail.stubs(:should_block?).returns(true)
    user = Fabricate.build(:user, email: "bad@spamclub.com")
    expect(user).not_to be_valid
    expect(user.errors[:email]).to be_present
  end

  it "is valid when the email is not on the block list" do
    BlockedEmail.stubs(:should_block?).returns(false)
    expect(Fabricate.build(:user, email: "good@example.com")).to be_valid
  end

  it "preserves uniqueness validation" do
    existing = Fabricate(:user)
    expect(Fabricate.build(:user, email: existing.email)).not_to be_valid
  end
end
```

## Improvements

:yellow_circle: [correctness] Incomplete regex escaping enables subdomain bypass, ReDoS, and pattern injection in domain validation in lib/validators/email_validator.rb:17 (confidence: 85)
`setting.gsub('.', '\.')` only escapes dots. All other regex metacharacters — `(`, `)`, `[`, `]`, `*`, `+`, `?`, `|`, `\`, `^`, `$` — pass through unescaped into `Regexp.new`. Two classes of vulnerability share this root cause: (1) **Correctness** — the pattern `@(gmail\.com)` matches `user@evil-gmail.com` because there is no start-of-domain anchor, enabling whitelist bypass or false blacklist positives. (2) **Security** — admin-controlled SiteSetting values containing regex metacharacters can inject ReDoS patterns (catastrophic backtracking), `.*` bypass patterns, or malformed expressions that raise `RegexpError` and crash the signup flow.
```suggestion
def email_in_restriction_setting?(setting, value)
  domains = setting.to_s.split(/[\s,|]+/).map { |d| Regexp.escape(d.strip) }.reject(&:empty?)
  return false if domains.empty?
  regexp = Regexp.new("@(?:[^@]*\\.)?(?:#{domains.join('|')})\\z", Regexp::IGNORECASE)
  value =~ regexp
end
```
[References: https://cwe.mitre.org/data/definitions/1333.html, https://owasp.org/www-community/attacks/Regular_expression_Denial_of_Service_-_ReDoS]

:yellow_circle: [consistency] Use of `.where().first` instead of idiomatic `.find_by` in app/models/blocked_email.rb:12 (confidence: 85)
`BlockedEmail.where(email: email).first` should be replaced with `BlockedEmail.find_by(email: email)`. The `find_by` form is clearer, idiomatic Rails, and generates equivalent SQL (`SELECT ... LIMIT 1`).
```suggestion
record = BlockedEmail.find_by(email: email)
```

:yellow_circle: [testing] email_validator_spec does not test whitelist/blacklist domain restriction paths in spec/components/validators/email_validator_spec.rb:1 (confidence: 85)
`EmailValidator` handles three distinct branches: whitelist, blacklist, and blocked-email. The spec only covers the blocked-email branch via stubs. The whitelist/blacklist regex logic — which has documented bypass and ReDoS vulnerabilities (see the merged finding on `email_validator.rb:17`) — has zero spec coverage, meaning those bugs would not be caught by the test suite.
```suggestion
context "whitelisted domains" do
  before { SiteSetting.stubs(:email_domains_whitelist).returns("example.com|corp.org") }

  it "accepts a whitelisted address" do
    record = Fabricate.build(:user, email: "alice@example.com")
    validator.validate_each(record, :email, record.email)
    expect(record.errors[:email]).not_to be_present
  end

  it "rejects a non-whitelisted address" do
    record = Fabricate.build(:user, email: "alice@outsider.com")
    validator.validate_each(record, :email, record.email)
    expect(record.errors[:email]).to be_present
  end
end

context "blacklisted domains" do
  before { SiteSetting.stubs(:email_domains_blacklist).returns("spam.net|junk.io") }

  it "rejects a blacklisted address" do
    record = Fabricate.build(:user, email: "alice@spam.net")
    validator.validate_each(record, :email, record.email)
    expect(record.errors[:email]).to be_present
  end
end
```

:yellow_circle: [correctness] rejectedEmails array grows unbounded and permanently blacklists emails client-side in app/assets/javascripts/discourse/controllers/create_account_controller.js:15 (confidence: 80)
Once an email is pushed into `rejectedEmails` it is never removed. If a server-side block is lifted, a transient server error occurs, or the user re-enters a corrected value, the UI permanently marks that email as invalid for the entire session. This creates a UX trap where a legitimately valid email cannot be submitted after a single rejection, and conflates "server said no" with "email is permanently invalid".
```suggestion
// Track only the most-recently server-rejected email and clear on field change:
accountEmailChanged: function () {
  this.set('serverRejectedEmail', null);
}.observes('accountEmail'),

// ...in emailValidation:
if (this.get('serverRejectedEmail') === email) {
  return Discourse.InputValidation.create({ failed: true, reason: I18n.t('user.email.invalid') });
}

// ...in the error handler:
createAccountController.set('serverRejectedEmail', result.values.email);
```

:yellow_circle: [consistency] Use of low-precedence `and` operator instead of `&&` in lib/validators/email_validator.rb:13 (confidence: 80)
The condition `if record.errors[attribute].blank? and BlockedEmail.should_block?(value)` uses the `and` keyword, which has very low operator precedence in Ruby and can cause subtle, hard-to-spot evaluation-order bugs. RuboCop and the Rails style guide both prefer `&&` for boolean logic in conditions.
```suggestion
if record.errors[attribute].blank? && BlockedEmail.should_block?(value)
```

:yellow_circle: [testing] No test for concurrent increment / race condition in should_block? in spec/models/blocked_email_spec.rb:1 (confidence: 80)
The existing "updates statistics" shared example does not verify atomicity of the `match_count` increment. The race condition identified in the critical finding (`blocked_email.rb:9`) is not exercised by any test. A test can subscribe to ActiveRecord SQL notifications to assert a single atomic `UPDATE` is issued rather than a `SELECT` followed by a separate `UPDATE`.
```suggestion
it "increments match_count with a single atomic SQL UPDATE (no preceding SELECT of match_count)" do
  Fabricate(:blocked_email, email: email, action_type: BlockedEmail.actions[:block])
  queries = []
  listener = ->(*, _started, _finished, _id, payload) { queries << payload[:sql] if payload[:sql] =~ /blocked_emails/ }
  ActiveSupport::Notifications.subscribed(listener, "sql.active_record") do
    BlockedEmail.should_block?(email)
  end
  expect(queries.any? { |q| q =~ /UPDATE.*match_count\s*=\s*match_count\s*\+/i }).to be_true
  expect(queries.any? { |q| q =~ /SELECT.*match_count/i }).to be_false
end
```

## Risk Metadata
Risk Score: 33/100 (MEDIUM) | Blast Radius: 0 importers in shim repository (repo has no source files checked in for downstream impact analysis) | Sensitive Paths: `db/migrate/20130724201552_create_blocked_emails.rb` (matches `*migration*`)
AI-Authored Likelihood: N/A (empty shim — no git history or source to analyze for AI signatures)

(1 additional finding below confidence threshold: registration error response enables account/email enumeration via `errors: user.errors.to_hash` — confidence 70)
