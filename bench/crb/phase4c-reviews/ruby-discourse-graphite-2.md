Warning: consistency agent failed (5/6 agents completed)

## Summary
18 files changed, 156 lines added, 68 lines deleted. 11 findings (10 critical, 1 improvements, 0 nitpicks).
Per-topic email unsubscribe feature ships with multiple critical defects: a CSRF-vulnerable GET-mutating-state endpoint, several nil-dereference crashes in the new controller action, a relative-URL bug that breaks every unsubscribe link in emails, XSS sinks in the dropdown and unsubscribe template, a breaking change to `Email::MessageBuilder`'s caller contract, and effectively zero test coverage for the new behavior.

## Critical

:red_circle: [security] CSRF: state-mutating unsubscribe action exposed via GET in config/routes.rb:437 (confidence: 95)
The new `unsubscribe` action is routed as HTTP GET (`get "t/:slug/:topic_id/unsubscribe"` and `get "t/:topic_id/unsubscribe"`), but the controller toggles `TopicUser.notification_level` between `:regular` and `:muted` and calls `tu.save!`. Rails `protect_from_forgery` only runs on non-GET verbs, so this endpoint has no CSRF protection; `skip_before_filter :check_xhr, only: [:show, :unsubscribe, :feed]` compounds the problem by allowing non-XHR navigations. Any attacker page, onebox preview, link prefetcher, or `<img src="https://discourse.example/t/123/unsubscribe">` embedded elsewhere will silently mute / unmute an authenticated victim's topic subscriptions; the toggle semantics mean repeated hits flip state back and forth. See CWE-352 / OWASP A01.
```suggestion
# config/routes.rb — render confirmation on GET, mutate only on POST (with CSRF token)
get  "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe",         constraints: {topic_id: /\d+/}
get  "t/:topic_id/unsubscribe"       => "topics#unsubscribe",         constraints: {topic_id: /\d+/}
post "t/:slug/:topic_id/unsubscribe" => "topics#perform_unsubscribe", constraints: {topic_id: /\d+/}
post "t/:topic_id/unsubscribe"       => "topics#perform_unsubscribe", constraints: {topic_id: /\d+/}

# For clicks arriving from emails (no session CSRF token), use a signed token:
#   Rails.application.message_verifier(:unsubscribe).generate([user_id, topic_id], expires_in: 7.days)
# and verify it in the controller instead of relying on the session cookie.
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/352.html]

:red_circle: [correctness] Nil dereference on `tu` when TopicUser row does not exist in app/controllers/topics_controller.rb:108 (confidence: 98)
`TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])` returns `nil` whenever the current user has no explicit TopicUser row for that topic — exactly the common case of a user who received the notification email but never opened the topic. The next line unconditionally calls `tu.notification_level`, raising `NoMethodError: undefined method 'notification_level' for nil:NilClass`. The unsubscribe link will 500 for the population most likely to click it.
```suggestion
tu = TopicUser.find_or_initialize_by(user_id: current_user.id, topic_id: params[:topic_id])

if tu.notification_level.to_i > TopicUser.notification_levels[:regular]
  tu.notification_level = TopicUser.notification_levels[:regular]
else
  tu.notification_level = TopicUser.notification_levels[:muted]
end

tu.save!
```

:red_circle: [correctness, cross-file-impact] `Topic#unsubscribe_url` returns a relative path, breaking every email unsubscribe link in app/models/topic.rb:719 (confidence: 95)
`unsubscribe_url` is defined as `"#{url}/unsubscribe"` where `url` delegates to `relative_url` and returns `/t/slug/123`. In `app/mailers/user_notifications.rb` this relative path is passed as `unsubscribe_url: post.topic.unsubscribe_url` into the email builder and interpolated into the `unsubscribe_link` locale string as `[click here](%{unsubscribe_url})`. Email clients do not resolve relative URLs — the link is dead for every recipient. A `List-Unsubscribe` header built from the same value would also be invalid per RFC 2369. Compare `app/views/email/notification.html.erb`, which correctly uses `<%= Discourse.base_url %><%= post.url %>` for absolute URLs.
```suggestion
def unsubscribe_url
  "#{Discourse.base_url}#{url}/unsubscribe"
end
```

:red_circle: [cross-file-impact] `Email::MessageBuilder` caller contract changed — `unsubscribe_url` now required when `add_unsubscribe_link: true` in lib/email/message_builder.rb:60 (confidence: 95)
`config/locales/server.en.yml` expanded the `unsubscribe_link` string to interpolate `%{unsubscribe_url}`, and `MessageBuilder#html_part` renders it via `I18n.t('unsubscribe_link', template_args)` where `template_args` is built from `@opts`. Any caller that passes `add_unsubscribe_link: true` without also supplying `unsubscribe_url:` will trigger `I18n::MissingInterpolationArgument` (or silently leave a literal `%{unsubscribe_url}` in the outgoing email, depending on Rails i18n config). The spec fixture was updated to add `unsubscribe_url: "/t/1234/unsubscribe"`, which is the tell that the public contract changed. Audit every other mailer / call site in the codebase.
```suggestion
# lib/email/message_builder.rb — provide a safe default so old callers do not hard-break
@template_args = {
  site_name: SiteSetting.email_prefix.presence || SiteSetting.title,
  base_url: Discourse.base_url,
  user_preferences_url: "#{Discourse.base_url}/my/preferences",
  unsubscribe_url: "#{Discourse.base_url}/my/preferences",
}.merge!(@opts)
```

:red_circle: [correctness] Nil dereference on `@topic_view.topic` when `topic_id` is invalid in app/controllers/topics_controller.rb:100 (confidence: 92)
`TopicView.new(params[:topic_id], current_user)` either raises (`Discourse::InvalidAccess` / `ActiveRecord::RecordNotFound`) for an unknown/inaccessible topic, in which case the exception bubbles up as a 500, or (depending on the constructor path) returns a view with a nil topic, in which case the slug-mismatch branch `redirect_to @topic_view.topic.unsubscribe_url` immediately raises `NoMethodError`. Unsubscribe links travel via email and may be followed long after the topic is deleted or restricted; both failure modes produce a stack trace instead of a clean 404.
```suggestion
def unsubscribe
  begin
    @topic_view = TopicView.new(params[:topic_id], current_user)
  rescue ActiveRecord::RecordNotFound, Discourse::InvalidAccess
    return render_404
  end

  topic = @topic_view.topic
  return render_404 if topic.nil?

  if slugs_do_not_match || (!request.format.json? && params[:slug].blank?)
    return redirect_to topic.unsubscribe_url, status: 301
  end
  # ... rest of action
end
```

:red_circle: [security] Stored XSS: unescaped title concatenated into HTML buffer in app/assets/javascripts/discourse/components/dropdown-button.js.es6:27 (confidence: 90)
`renderString` now guards on presence but still does `buffer.push("<h4 class='title'>" + title + "</h4>")` with no escaping. `title` is an Ember component attribute that in the new unsubscribe page flows from `{{topic-notifications-button topic=model}}`, whose title is derived from topic/user-influenced text (topic fancy title, i18n strings interpolating a topic title). Any `<`, `>`, or `"` in the value is interpreted as HTML — `<img src=x onerror=...>` is sufficient. CWE-79 / OWASP A03.
```suggestion
renderString(buffer) {
  const title = this.get('title');
  if (title) {
    const escaped = Handlebars.Utils.escapeExpression(title);
    buffer.push("<h4 class='title'>" + escaped + "</h4>");
  }
  buffer.push("<button class='btn standard dropdown-toggle' data-toggle='dropdown'>");
  // ...
}
```
[References: https://cwe.mitre.org/data/definitions/79.html, https://owasp.org/Top10/A03_2021-Injection/]

:red_circle: [correctness, cross-file-impact] `perform_show_response` hard-codes `render :show`; unsubscribe page renders the wrong template and `show` risks double-render in app/controllers/topics_controller.rb:500 (confidence: 88)
Adding `render :show` inside the shared `perform_show_response` helper affects every action that calls it. For the existing `show` action, any `before_filter` or prior code path that already rendered or redirected will now raise `AbstractController::DoubleRenderError`. For the new `unsubscribe` action, HTML requests will render `app/views/topics/show.html.erb` instead of a dedicated unsubscribe confirmation view — users arriving from an email link get the full topic view, not the stop-notifications confirmation the JS route and `unsubscribe.hbs` template were built to show.
```suggestion
# Revert the render :show from perform_show_response and keep it action-local:

def show
  # ... existing logic ...
  perform_show_response
end

def unsubscribe
  # ... nil guards and tu mutation ...
  respond_to do |format|
    format.html { render :unsubscribe }   # or :show if the SPA takes over
    format.json { render json: { success: true } }
  end
end
```

:red_circle: [security] Stored XSS via triple-stache rendering in app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3 (confidence: 85)
The template uses `{{{stopNotificiationsText}}}` (triple-stache = raw HTML, no escaping). The value comes from the new controller's computed property, which calls `I18n.t("topic.unsubscribe.stop_notifications", { title: this.get("model.fancyTitle") })`. Discourse's `I18n.t` performs plain string substitution and does not HTML-escape interpolation values. While `fancyTitle` is server-escaped for normal rendering, any future change that routes `model.title` (or a plugin-provided field) through this controller becomes immediate stored XSS. Attackers who can set a topic title containing `<img src=x onerror=alert(1)>` execute script on every victim who visits `/t/<slug>/<id>/unsubscribe`.
```suggestion
// app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6
import { escapeExpression } from "discourse/lib/utilities";

export default ObjectController.extend({
  stopNotificationsText: function() {
    const title = escapeExpression(this.get("model.fancyTitle"));
    return I18n.t("topic.unsubscribe.stop_notifications", { title });
  }.property("model.fancyTitle"),
});
```
Or split static markup from dynamic text and use the auto-escaping double-stache (`{{model.fancyTitle}}`) in the template directly.
[References: https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [testing] No tests for `TopicsController#unsubscribe` action in app/controllers/topics_controller.rb:97 (confidence: 97)
The new `unsubscribe` action has three distinct paths (slug-mismatch 301 redirect, watching→regular downgrade, regular→muted downgrade) plus a nil-dereference branch when no `TopicUser` row exists. None of these are covered by a controller or request spec. The critical correctness bugs elsewhere in this review would all have been caught by a minimally thorough spec.
```suggestion
# spec/controllers/topics_controller_spec.rb
describe "GET #unsubscribe" do
  let(:user) { Fabricate(:user) }
  let(:topic) { Fabricate(:topic) }
  before { sign_in(user) }

  it "does not crash when the user has no TopicUser row" do
    get :unsubscribe, slug: topic.slug, topic_id: topic.id
    expect(response).not_to be_server_error
  end

  it "downgrades from watching to regular" do
    TopicUser.change(user.id, topic.id, notification_level: TopicUser.notification_levels[:watching])
    get :unsubscribe, slug: topic.slug, topic_id: topic.id
    expect(TopicUser.find_by(user_id: user.id, topic_id: topic.id).notification_level)
      .to eq(TopicUser.notification_levels[:regular])
  end

  it "mutes when currently regular" do
    TopicUser.change(user.id, topic.id, notification_level: TopicUser.notification_levels[:regular])
    get :unsubscribe, slug: topic.slug, topic_id: topic.id
    expect(TopicUser.find_by(user_id: user.id, topic_id: topic.id).notification_level)
      .to eq(TopicUser.notification_levels[:muted])
  end

  it "301-redirects when slug does not match" do
    get :unsubscribe, slug: "wrong-slug", topic_id: topic.id
    expect(response).to redirect_to(topic.unsubscribe_url)
    expect(response.status).to eq(301)
  end
end
```

:red_circle: [testing] No tests for `Topic#unsubscribe_url` in app/models/topic.rb:719 (confidence: 92)
`unsubscribe_url` is the canonical URL embedded into every notification email this PR touches. If `url` returns an unexpected value (trailing slash, private-topic behavior, base-url changes), every email link silently breaks. There are no model specs.
```suggestion
# spec/models/topic_spec.rb
describe "#unsubscribe_url" do
  let(:topic) { Fabricate(:topic, slug: "my-topic") }

  it "returns an absolute URL ending in /unsubscribe" do
    expect(topic.unsubscribe_url).to start_with(Discourse.base_url)
    expect(topic.unsubscribe_url).to end_with("/unsubscribe")
  end

  it "includes the topic id" do
    expect(topic.unsubscribe_url).to include(topic.id.to_s)
  end
end
```

## Improvements

:yellow_circle: [testing] `message_builder_spec` fixture updated but no assertion verifies unsubscribe URL rendering in spec/components/email/message_builder_spec.rb:510 (confidence: 88)
The only spec change is adding `unsubscribe_url: "/t/1234/unsubscribe"` to the `message_with_unsubscribe` let block — a fixture fix required because the new `%{unsubscribe_url}` interpolation would otherwise raise. No new `it` block asserts that the per-topic URL actually appears in the rendered HTML or plain-text body. The core new behavior is completely unverified.
```suggestion
it "includes the per-topic unsubscribe URL in the html body" do
  html = message_with_unsubscribe.html_part.body.to_s
  expect(html).to include("/t/1234/unsubscribe")
end

it "includes the per-topic unsubscribe URL in the text body" do
  text = message_with_unsubscribe.body.to_s
  expect(text).to include("/t/1234/unsubscribe")
end
```

## Risk Metadata
Risk Score: 44/100 (MEDIUM) | Blast Radius: 100 (high-centrality core models — topic.rb, topic_user.rb, topics_controller.rb — imported by dozens of files) | Sensitive Paths: none matched default globs (but the unsubscribe controller mutates user state via GET with `check_xhr` skipped — security-relevant pattern not captured by path glob)
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold: missing authorization/guardian check (75), mailer integration spec missing (82), QUnit spec missing for stopNotificiationsText (75), relative-URL used in in-app redirect (75), misspelled `stopNotificiationsText` identifier (65), consistency-agent scope-creep findings on unrelated var→const / hash-syntax refactors in `topic-from-params.js.es6`, `topic_user.rb`, `message_builder.rb` (agent unavailable — files not on disk in shim))
