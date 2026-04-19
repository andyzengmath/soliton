## Summary
10 files changed, 155 lines added, 23 lines deleted. 9 findings (2 critical, 7 improvements).
Race condition in blocked_email.rb — non-atomic `match_count` increment loses updates under concurrency, and a nil email can reach `BlockedEmail.should_block?` before the presence validator halts the chain.

## Critical

:red_circle: [correctness] Race condition in `should_block?`: non-atomic read-modify-write on `match_count` in app/models/blocked_email.rb:9 (confidence: 95)
`should_block?` reads the record, increments `match_count` in Ruby memory, then calls `save`. Under concurrent requests two threads can both read `match_count = N`, both write `N+1`, and one increment is silently lost. Additionally, `record.save` can fail (validation or DB error) and the failure is silently swallowed — leaving `match_count`/`last_match_at` stale in the DB while the method still returns a block decision. For a security-relevant path, silent write failure is dangerous.
```suggestion
def self.should_block?(email)
  record = BlockedEmail.where(email: email).first
  if record
    BlockedEmail.where(id: record.id).update_all(
      "match_count = match_count + 1, last_match_at = NOW()"
    )
  end
  record && record.action_type == actions[:block]
end
```

:red_circle: [correctness] Nil email reaches `BlockedEmail.should_block?` before presence validation runs in lib/validators/email_validator.rb:10 (confidence: 92)
`EmailValidator` is registered as a separate `EachValidator` entry from `validates :email, presence: true`, so `value` can be `nil` when it reaches `validate_each`. `BlockedEmail.where(email: nil).first` still issues a DB query; if any row matches `nil` the method increments `match_count` and may falsely return a block decision. A defensive early return also protects future callers from `NoMethodError` on nil.
```suggestion
def validate_each(record, attribute, value)
  return if value.blank?
  # ...existing logic
end
```

## Improvements

:yellow_circle: [correctness] `and` vs `&&` operator precedence hazard in lib/validators/email_validator.rb:13 (confidence: 97)
Line 13 uses the `and` keyword: `if record.errors[attribute].blank? and BlockedEmail.should_block?(value)`. The `and` keyword has extremely low precedence — lower than `=`. Semantically correct in isolation today, but any future refactor that embeds this expression in an assignment or a larger conditional will silently produce the wrong result. Discourse convention is `&&` throughout the codebase, and mixing the two is a well-known style hazard.
```suggestion
if record.errors[attribute].blank? && BlockedEmail.should_block?(value)
```

:yellow_circle: [consistency] Use `find_by` instead of `where().first` in app/models/blocked_email.rb:13 (confidence: 95)
Rails/Discourse idiom prefers `find_by` for single-record lookups. `where(...).first` is more verbose and can mislead readers into thinking ordering matters; if you later adopt the atomic-update fix above you can still keep this read-side cleanup.
```suggestion
record = BlockedEmail.find_by(email: email)
```

:yellow_circle: [testing] `EmailValidator` spec only covers the blocked-email branch; whitelist/blacklist paths untested in lib/validators/email_validator.rb:1 (confidence: 92)
The spec stubs `BlockedEmail.should_block?` and exercises two cases. The whitelist setting branch, the blacklist setting branch, and the both-blank fallthrough are not covered. Any regex or branching bug in those paths will go undetected — the regex construction logic in particular is the same code the correctness finding above flags as unanchored.
```suggestion
context "site whitelist is set" do
  before { SiteSetting.stubs(:email_domains_whitelist).returns("example.com") }
  it "adds an error when email domain is not on the whitelist" do
    validator.validate_each(record, :email, "user@other.com")
    expect(record.errors[:email]).to be_present
  end
  it "does not add an error when email domain is on the whitelist" do
    validator.validate_each(record, :email, "user@example.com")
    expect(record.errors[:email]).not_to be_present
  end
end

context "site blacklist is set" do
  before { SiteSetting.stubs(:email_domains_blacklist).returns("spamclub.com") }
  it "adds an error when email domain is on the blacklist" do
    validator.validate_each(record, :email, "bad@spamclub.com")
    expect(record.errors[:email]).to be_present
  end
end
```

:yellow_circle: [testing] Validation restructuring in `user.rb` has no `user_spec` update in app/models/user.rb:45 (confidence: 90)
`user.rb` swapped from `validate :email_validator` (inline method) to `validates :email, email: true` (custom validator class). No `user_spec.rb` test was added to assert the email validator actually fires during `User#valid?`. A regression in validator wiring (e.g. a typo in the validator class name) would not be caught by existing tests.
```suggestion
describe "email validation" do
  it "is invalid when email is on the blocked list" do
    BlockedEmail.create!(email: "blocked@spam.org", action_type: BlockedEmail.actions[:block])
    user = Fabricate.build(:user, email: "blocked@spam.org")
    expect(user).not_to be_valid
    expect(user.errors[:email]).to be_present
  end

  it "is valid when email is not on the blocked list" do
    user = Fabricate.build(:user, email: "good@example.com")
    expect(user).to be_valid
  end
end
```

:yellow_circle: [testing] New `errors`+`values` response shape has no controller spec coverage in app/controllers/users_controller.rb:194 (confidence: 88)
The `create` action now returns `errors: user.errors.to_hash` and `values: user.attributes.slice("name","username","email")` on the failure branch. No controller/request spec asserts this shape. The JS client depends on both keys; a silent rename or drop would break the client without any failing test.
```suggestion
context "when registration fails due to blocked email" do
  before do
    BlockedEmail.create!(email: "bad@spam.org", action_type: BlockedEmail.actions[:block])
    post :create, email: "bad@spam.org", username: "spammer", password: "password123"
  end

  it "includes an errors hash in the response" do
    json = JSON.parse(response.body)
    expect(json["errors"]).to be_present
  end

  it "echoes name, username, and email (and nothing else) in values" do
    json = JSON.parse(response.body)
    expect(json["values"].keys).to match_array(%w[name username email])
    expect(json["values"]["email"]).to eq("bad@spam.org")
  end
end
```

:yellow_circle: [correctness] Domain regex is not end-anchored — partial-match bypass in lib/validators/email_validator.rb:17 (confidence: 85)
`email_in_restriction_setting?` builds the regex as `@(#{domains})` with no `\z` anchor. An email like `user@bar.org.evil.com` will match a whitelisted `bar.org` as a substring, bypassing the whitelist check — or conversely trip a blacklist match for unrelated domains that happen to contain a blacklisted substring. This is pre-existing behavior moved from `user.rb`, but the relocation is a reasonable checkpoint to fix it.
```suggestion
def email_in_restriction_setting?(setting, value)
  domains = setting.gsub('.', '\.')
  regexp = Regexp.new("@(#{domains})\\z", true)
  value =~ regexp
end
```

:yellow_circle: [testing] New `rejectedEmails` caching has no JS test coverage in app/assets/javascripts/discourse/controllers/create_account_controller.js:17 (confidence: 85)
The new client-side caching logic (`rejectedEmails` array, `contains()` short-circuit, `pushObject` on server rejection) has no QUnit coverage. Regressions in initialization, duplicate pushes, and the cache-consultation short-circuit would go undetected. Worth noting: the cache is also never cleared on email-field changes, so a typo that was briefly rejected server-side stays "rejected" on the client even after the user corrects it — a test will naturally surface this UX gap too.
```suggestion
test('rejectedEmails defaults to an empty array', function(assert) {
  const controller = this.subject();
  assert.deepEqual(controller.get('rejectedEmails'), []);
});

test('emailValidation fails when email is already in rejectedEmails', function(assert) {
  const controller = this.subject();
  controller.set('accountEmail', 'bad@spam.org');
  controller.get('rejectedEmails').pushObject('bad@spam.org');
  assert.ok(controller.get('emailValidation.failed'),
    'validation should fail for a cached rejected email');
});

test('pushObject fires when server returns errors.email + values.email', function(assert) {
  // simulate server error response handling and assert the push side-effect
});
```

## Risk Metadata
Risk Score: 39/100 (MEDIUM) | Blast Radius: core Rails model + registration controller (blast factor 20 in shim; higher in full Discourse tree) | Sensitive Paths: db/migrate/20130724201552_create_blocked_emails.rb
AI-Authored Likelihood: LOW

(4 additional findings below confidence threshold or nitpick-only — suppressed from markdown body per Phase 3.5)
