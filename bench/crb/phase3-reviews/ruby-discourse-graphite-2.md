## Summary
18 files changed, 156 lines added, 69 lines deleted. 11 findings (4 critical, 7 improvements, 0 nitpicks).
Good feature direction, but the new unsubscribe action has a CSRF-exploitable GET-mutation pattern, a guaranteed nil-TopicUser crash path, and zero controller test coverage — all critical blockers.

## Critical

:red_circle: [correctness/security/testing] Nil dereference on `TopicUser.find_by` — guaranteed NoMethodError/500 for first-time visitors (zero test coverage) in app/controllers/topics_controller.rb:104 (confidence: 97)
`TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])` returns `nil` when no row exists for the (user, topic) pair. The action immediately dereferences `tu.notification_level` and calls `tu.save!` without any nil guard, causing a NoMethodError/500 for every user who has not previously interacted with the topic — the common case for email-driven unsubscribe flows. The security angle compounds this: the differential 500-vs-200 response can be exploited as an error oracle, allowing an attacker (via the CSRF vector below) to probe arbitrary `topic_id`s and infer whether a victim has `TopicUser` records for specific topics (reading-history leak / IDOR). No spec covers this code path in any scenario.
```suggestion
tu = TopicUser.find_or_initialize_by(user_id: current_user.id, topic_id: params[:topic_id])

guardian.ensure_can_see!(@topic_view.topic)

if tu.notification_level.to_i > TopicUser.notification_levels[:regular]
  tu.notification_level = TopicUser.notification_levels[:regular]
else
  tu.notification_level = TopicUser.notification_levels[:muted]
end

tu.save!
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/639.html]

:red_circle: [security/correctness] CSRF vulnerability: GET endpoint performs state-mutating DB write — exploitable by email prefetchers and link scanners in app/controllers/topics_controller.rb:97 (confidence: 95)
The `unsubscribe` action issues `tu.save!` (state mutation) on a GET request with no CSRF protection. RFC 7231 §4.2.1 requires GET to be safe and idempotent; violating this has two concrete real-world consequences: (1) Email security scanners (Outlook Safe Links, Gmail image proxy, Proofpoint, Mimecast) and browser prefetchers will silently unsubscribe users the moment the email is opened or previewed. (2) An attacker who can embed the URL (e.g. `<img src="...">` in any page the victim visits, or a forwarded email) can repeatedly toggle a victim's notification state because the action flips regular↔muted. `before_filter :ensure_logged_in` does not mitigate CSRF — it guarantees the request runs in the victim's session, which is precisely what CSRF exploits. No signed token, no expiry, no user confirmation step.
```suggestion
# config/routes.rb
get  "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe",         constraints: {topic_id: /\d+/}
post "t/:slug/:topic_id/unsubscribe" => "topics#perform_unsubscribe", constraints: {topic_id: /\d+/}

# app/controllers/topics_controller.rb
def unsubscribe
  # GET: render confirmation page only, no DB write
  @topic_view = TopicView.new(params[:topic_id], current_user)
end

def perform_unsubscribe
  # POST: CSRF token enforced automatically; also verify signed token from email
  verify_unsubscribe_token!(params[:key])
  tu = TopicUser.find_or_initialize_by(user_id: current_user.id, topic_id: params[:topic_id])
  # ...toggle logic...
  tu.save!
  perform_show_response
end
```
Also add an HMAC-signed, per-recipient, expiring token bound to `{user_id, topic_id, nonce, exp}` using `Rails.application.message_verifier(:unsubscribe)` and embed it in the emailed URL so the GET page can safely render a POST form.
[References: https://cwe.mitre.org/data/definitions/352.html, https://datatracker.ietf.org/doc/html/rfc7231#section-4.2.1, https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html]

:red_circle: [correctness] `current_user.id` called without nil guard — crash risk if authentication filter is relaxed in app/controllers/topics_controller.rb:103 (confidence: 88)
`current_user.id` is called unconditionally. The current `before_filter` chain guards this today, but the email-unsubscribe UX specifically targets users clicking links from email clients while potentially logged out. Any future relaxation of the filter, misordered `skip_before_filter`, or refactor that lets a guest reach this action will produce `NoMethodError: undefined method 'id' for nil:NilClass`. Defensive coding requires an explicit guard at the action level regardless of filter chain assumptions.
```suggestion
def unsubscribe
  @topic_view = TopicView.new(params[:topic_id], current_user)
  return render_404 unless current_user

  if slugs_do_not_match || (!request.format.json? && params[:slug].blank?)
    return redirect_to @topic_view.topic.unsubscribe_url, status: 301
  end
  # ...
end
```

:red_circle: [testing] New `unsubscribe` controller action has zero test coverage across all branches in app/controllers/topics_controller.rb:97 (confidence: 99)
Every branch in the new action (slug redirect, nil `TopicUser`, notification level toggling above/at-or-below regular, unauthenticated access) is completely untested. Combined with the nil-dereference crash path above, this guarantees a production incident on first real-world use by a user without an existing `TopicUser` record.
```suggestion
# spec/controllers/topics_controller_spec.rb
describe '#unsubscribe' do
  let(:user)  { Fabricate(:user) }
  let(:topic) { Fabricate(:topic) }

  context 'when not logged in' do
    it 'redirects to login' do
      get :unsubscribe, topic_id: topic.id, slug: topic.slug
      expect(response).to redirect_to('/login')
    end
  end

  context 'when logged in' do
    before { log_in_user(user) }

    it 'handles missing TopicUser record gracefully' do
      expect {
        get :unsubscribe, topic_id: topic.id, slug: topic.slug
      }.not_to raise_error
    end

    it 'redirects 301 on slug mismatch' do
      get :unsubscribe, topic_id: topic.id, slug: 'wrong-slug'
      expect(response.status).to eq(301)
    end

    it 'sets notification_level to regular when above regular' do
      TopicUser.change(user.id, topic.id, notification_level: TopicUser.notification_levels[:watching])
      get :unsubscribe, topic_id: topic.id, slug: topic.slug
      expect(TopicUser.get(topic, user).notification_level).to eq(TopicUser.notification_levels[:regular])
    end

    it 'sets notification_level to muted when at or below regular' do
      TopicUser.change(user.id, topic.id, notification_level: TopicUser.notification_levels[:regular])
      get :unsubscribe, topic_id: topic.id, slug: topic.slug
      expect(TopicUser.get(topic, user).notification_level).to eq(TopicUser.notification_levels[:muted])
    end
  end
end
```

## Improvements

:yellow_circle: [correctness] Destructive DB write on GET — replayable by prefetchers and browser refresh in app/controllers/topics_controller.rb:97 (confidence: 82)
Correctness dimension of the CSRF critical finding above. Even absent a malicious actor, benign infrastructure (browser back/forward cache, RSS readers, Slack unfurlers, F5-to-refresh) will replay the GET and repeatedly mutate state. Fix is identical to the CSRF finding — split into GET (confirmation) + POST (mutation).
```suggestion
# See CSRF critical finding for the two-step flow recommendation.
```

:yellow_circle: [security] Unsubscribe URL embedded in emails lacks expiry and signature — indefinite replay window in app/mailers/user_notifications.rb:295 (confidence: 75)
The URL embedded in outgoing emails is static and tied only to `topic_id`, with no HMAC signature or expiry. Email archives, forwarded copies, corporate mail relays, and Referer logs all expose a permanently valid mutation URL. This is the mailer-side companion to the CSRF finding on the controller: without a signed token, there is no way to safely perform one-click unsubscribe from the emailed link alone.
```suggestion
# app/mailers/user_notifications.rb
unsubscribe_url: post.topic.signed_unsubscribe_url(user, expires_in: 7.days),

# app/models/topic.rb
def signed_unsubscribe_url(user, expires_in:)
  payload = { uid: user.id, tid: id, exp: (Time.now + expires_in).to_i }
  token   = Rails.application.message_verifier(:unsubscribe).generate(payload)
  "#{Discourse.base_url}/t/#{slug}/#{id}/unsubscribe?key=#{token}"
end
```
[References: https://owasp.org/Top10/A02_2021-Cryptographic_Failures/, https://cwe.mitre.org/data/definitions/294.html]

:yellow_circle: [security] Slugless unsubscribe route simplifies CSRF/prefetch enumeration attacks in config/routes.rb:444 (confidence: 75)
The second route `get "t/:topic_id/unsubscribe"` lets attackers construct canonical unsubscribe URLs for any numeric `topic_id` without knowing the slug, amplifying the CSRF/prefetch risk (no slug-discovery step required to mount a targeted attack).
```suggestion
# Keep only the signed, slugged form for mutation; redirect bare form.
get "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe",           constraints: {topic_id: /\d+/}
get "t/:topic_id/unsubscribe"       => "topics#unsubscribe_redirect",  constraints: {topic_id: /\d+/}

# topics_controller.rb
def unsubscribe_redirect
  topic = Topic.find_by(id: params[:topic_id])
  raise Discourse::NotFound unless topic
  redirect_to "#{topic.relative_url}/unsubscribe", status: 301
end
```

:yellow_circle: [testing] New `unsubscribe_url` model method has no test coverage in app/models/topic.rb:719 (confidence: 92)
The new method depends on `topic.url` behavior. A format or trailing-slash change upstream would silently break all emailed unsubscribe links with no test failure to signal it.
```suggestion
# spec/models/topic_spec.rb
describe '#unsubscribe_url' do
  let(:topic) { Fabricate(:topic, slug: 'my-topic') }

  it 'appends /unsubscribe to the topic URL' do
    expect(topic.unsubscribe_url).to eq("#{topic.url}/unsubscribe")
  end

  it 'does not double-slash' do
    expect(topic.unsubscribe_url).not_to include('//')
  end
end
```

:yellow_circle: [testing] Fixture update in message_builder_spec adds no behavioral assertions for per-topic unsubscribe URL in spec/components/email/message_builder_spec.rb:170 (confidence: 88)
The only spec change in this PR is expanding the `message_with_unsubscribe` fixture with `unsubscribe_url: "/t/1234/unsubscribe"`. No `it` block asserts that the rendered body or `List-Unsubscribe` header actually contains the topic-specific URL. The feature's primary email-delivery contract is therefore untested.
```suggestion
it "includes the per-topic unsubscribe URL in the email body" do
  expect(message_with_unsubscribe.body).to include("/t/1234/unsubscribe")
end

it "List-Unsubscribe header references the topic-specific unsubscribe URL" do
  expect(message_with_unsubscribe.header_args['List-Unsubscribe']).to include("/t/1234/unsubscribe")
end
```

:yellow_circle: [testing] `unsubscribe_url` propagation from mailer to email builder is untested in app/mailers/user_notifications.rb:295 (confidence: 82)
`user_notifications.rb` now passes `unsubscribe_url: post.topic.unsubscribe_url` into the email options. No mailer spec verifies that notification emails generated by `send_notification_email` actually include the topic-specific unsubscribe URL in the rendered output. If `post.topic` is nil (orphaned posts), this is a silent failure path.
```suggestion
# spec/mailers/user_notifications_spec.rb
describe 'notification email unsubscribe URL' do
  let(:post) { Fabricate(:post) }
  let(:user) { Fabricate(:user) }

  it 'includes the topic unsubscribe URL in the notification email' do
    mail = UserNotifications.user_mentioned(
      user,
      notification_type: :mentioned,
      notification_data_hash: { original_post_id: post.id }
    )
    expect(mail.body.encoded).to include(post.topic.unsubscribe_url)
  end
end
```

:yellow_circle: [consistency] Typo in property name: "stopNotificiationsText" (double-i) should be "stopNotificationsText" in app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6:5 (confidence: 95)
The computed property is named `stopNotificiationsText` with a doubled "i" in "Notificiations". It is consistently misspelled across the controller and the template reference, so the UI does not break, but the name is invisible to grep/search for the correctly-spelled word, creates confusion during future maintenance, and the corresponding i18n key is correctly spelled `stop_notifications`.
```suggestion
// app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6
stopNotificationsText: function() {
  return I18n.t("topic.unsubscribe.stop_notifications", { title: this.get("model.fancyTitle") });
}.property("model.fancyTitle"),

// app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs
{{{stopNotificationsText}}}
```

:yellow_circle: [consistency] Ember route parameter `:id` does not match Rails route parameter `:topic_id` in app/assets/javascripts/discourse/routes/app-route-map.js.es6:13 (confidence: 90)
The Ember route definition uses `'/t/:slug/:id/unsubscribe'` with parameter `:id`, while the Rails route uses `:topic_id` for the same URL segment. The Ember model hook reads `params.id`; the segment semantically represents a topic ID, not a generic ID. This cross-stack parameter-name inconsistency creates confusion and risks subtle bugs if any code (middleware, analytics, logging) inspects param names.
```suggestion
// app-route-map.js.es6
this.route('topicUnsubscribe', { path: '/t/:slug/:topic_id/unsubscribe' });

// routes/topic-unsubscribe.js.es6
model(params) {
  const topic = this.store.createRecord("topic", { id: params.topic_id });
  return PostStream.loadTopicView(params.topic_id).then(json => {
    topic.updateFromJson(json);
    return topic;
  });
}
```

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: central Discourse files (TopicsController, TopicUser, message_builder, routes.rb, app-route-map) | Sensitive Paths: none match configured globs, but mailer/notification surface is privacy-sensitive
AI-Authored Likelihood: MEDIUM (style-only reformatting in `app/models/topic_user.rb` and `app/views/email/notification.html.erb`; minimal PR body "Test 2")

(3 additional findings below confidence threshold: potential infinite redirect loop in unsubscribe_url redirect — conf 72; route-enumeration nitpick — conf 70 merged above; template-typo nitpick — conf 70 merged above)

---
**Recommendation:** request-changes — two critical security issues (CSRF on mutating GET, missing auth + error-oracle IDOR), a guaranteed nil-dereference crash path, and zero controller test coverage must be resolved before merge.
