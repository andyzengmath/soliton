## Summary
10 files changed, 155 lines added, 24 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
Non-atomic read-modify-write on `match_count` causes lost increments under concurrency in `blocked_email.rb`.

## Critical
:red_circle: [correctness] Non-atomic read-modify-write on match_count causes lost increments under concurrency in app/models/blocked_email.rb:10 (confidence: 92)
`should_block?` reads `match_count`, increments it in memory, then saves. Under concurrent requests for the same email, two threads can both read the same value, each add 1, and both write back — resulting in only one increment recorded instead of two. Additionally, if `record.save` fails (DB error or concurrent constraint violation), the failure is silently swallowed: no exception is raised and no error is logged, so the audit count becomes silently wrong with no observable signal.
```suggestion
def self.should_block?(email)
  record = BlockedEmail.where(email: email).first
  if record
    BlockedEmail.where(id: record.id).update_all("match_count = match_count + 1, last_match_at = NOW()")
  end
  record && record.action_type == actions[:block]
end
```

## Improvements
:yellow_circle: [correctness] BlockedEmail.should_block? called during validation triggers side effects on non-save paths in lib/validators/email_validator.rb:13 (confidence: 88)
`validate_each` is invoked any time the email attribute is validated, including `user.valid?` calls that do not result in a save. Each such call currently increments `match_count`, inflating the audit counter with non-registration attempts. A single form submission may trigger 2-3 validation passes internally, multiplying the counter per actual registration attempt. This also compounds the race-condition risk in `blocked_email.rb` if the atomic fix is not applied first.
```suggestion
# Split the concern: have should_block? return the blocking decision without persisting,
# and introduce a separate BlockedEmail.record_match(email) method called only from the
# controller at the point of a confirmed save, not inside the validator.
```

:yellow_circle: [consistency] Use && instead of low-precedence `and` operator in conditional in lib/validators/email_validator.rb:13 (confidence: 85)
The condition uses the low-precedence `and` keyword. Ruby/Rails convention strongly favors `&&` in conditionals because `and` has lower precedence than assignment, which can produce subtle evaluation-order bugs. The current code works here, but the style is inconsistent with Rails conventions and introduces a maintenance hazard.
```suggestion
if record.errors[attribute].blank? && BlockedEmail.should_block?(value)
```

## Risk Metadata
Risk Score: 12/100 (LOW) | Blast Radius: 0 importers (shim repo) | Sensitive Paths: none matched (auth-adjacent semantically but no glob hit)
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold 85)
