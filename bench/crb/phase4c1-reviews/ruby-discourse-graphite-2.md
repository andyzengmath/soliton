Warning: consistency agent aborted (could not access diff in sandbox); 4/5 agents completed.

## Summary
18 files changed, 156 lines added, 83 lines deleted. 9 findings (7 critical, 2 improvements, 0 nitpicks).
Per-topic email-unsubscribe feature ships a GET-mutating controller action with CSRF and nil-guard bugs, and a locale change that breaks every other `add_unsubscribe_link: true` caller in the codebase.

## Critical

:red_circle: [correctness] NoMethodError crash when TopicUser row does not exist for the current user in app/controllers/topics_controller.rb:204 (confidence: 97)
`TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])` returns `nil` when the user has never visited the topic — the realistic path for an unsubscribe link, since the email is delivered before the user has opened the topic page (which is what creates the TopicUser row). On the next line `tu.notification_level` raises `NoMethodError: undefined method 'notification_level' for nil:NilClass`, producing a 500 for every user clicking the link in that (common) state. `tu.save!` at line 212 has the same problem.
```suggestion
tu = TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])

if tu.nil?
  TopicUser.change(current_user.id, params[:topic_id],
                   notification_level: TopicUser.notification_levels[:muted])
else
  new_level = if tu.notification_level.to_i > TopicUser.notification_levels[:regular]
                TopicUser.notification_levels[:regular]
              else
                TopicUser.notification_levels[:muted]
              end
  tu.update!(notification_level: new_level)
end
```

:red_circle: [correctness] ArgumentError when TopicUser#notification_level is NULL in app/controllers/topics_controller.rb:206 (confidence: 90)
The `topic_users.notification_level` column is nullable — the existing `tracking` scope in `app/models/topic_user.rb` wraps reads in `COALESCE(topic_users.notification_level, :regular)` specifically because of this. When the row exists but the column is NULL, `tu.notification_level` returns `nil` and the comparison `nil > TopicUser.notification_levels[:regular]` raises `ArgumentError: comparison of NilClass with Integer failed`. This is a distinct crash path from the nil-`tu` case.
```suggestion
current_level = tu.notification_level.to_i
if current_level > TopicUser.notification_levels[:regular]
  tu.notification_level = TopicUser.notification_levels[:regular]
else
  tu.notification_level = TopicUser.notification_levels[:muted]
end
tu.save!
```

:red_circle: [security] CSRF via GET request mutates TopicUser notification state in app/controllers/topics_controller.rb:98 (confidence: 95)
The new `unsubscribe` action is wired to `GET /t/:slug/:topic_id/unsubscribe` yet mutates server state (toggles `TopicUser.notification_level` and calls `tu.save!`). Rails' `protect_from_forgery` does not apply to GET verbs, and `skip_before_filter :check_xhr` confirms plain-browser navigation is intentional. Any attacker who gets a logged-in Discourse user to load the URL — `<img src>`, `<link rel=prefetch>`, hidden iframe, email with remote image loading, a link in another forum post — silently toggles that user's subscription state. Because the toggle flips between muted/regular, repeated requests oscillate state, and iterating `topic_id` lets an attacker mute a victim from every topic they watch. `ensure_logged_in` does not mitigate CSRF — CSRF relies on the victim's existing authenticated session. Other Discourse email actions (email-change, global unsubscribe) use signed `EmailToken`/`UnsubscribeKey` precisely to avoid this class of bug.
```suggestion
# routes.rb — state change moves to POST
get  "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe_confirm", constraints: {topic_id: /\d+/}
post "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe",         constraints: {topic_id: /\d+/}

# controller
def unsubscribe_confirm
  @topic_view = TopicView.new(params[:topic_id], current_user)
  # render interstitial with POST form + CSRF token
end

def unsubscribe
  # POST-only, CSRF token required
  tu = TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])
  # ... toggle + save ...
end
```
References: https://owasp.org/www-community/attacks/csrf, https://cwe.mitre.org/data/definitions/352.html

:red_circle: [cross-file-impact] All callers passing add_unsubscribe_link: true without unsubscribe_url will raise I18n::MissingInterpolationArgument in lib/email/message_builder.rb:68 (confidence: 92)
The `unsubscribe_link` key in `config/locales/server.en.yml` now requires a second interpolation argument `%{unsubscribe_url}` alongside the existing `%{user_preferences_url}`. In `message_builder.rb`, `I18n.t('unsubscribe_link', template_args)` builds `template_args` from `@opts`, so `unsubscribe_url` is only present when the caller explicitly passes it. This PR patches only `user_notifications.rb#send_notification_email`. Every other `MessageBuilder.new(..., add_unsubscribe_link: true)` caller — digest mailer, mailing-list-mode summaries, account creation emails, system PMs — will hit `I18n::MissingInterpolationArgument` (or silently render a broken string depending on the i18n exception handler) when `html_part` runs. The fact that the spec fixture itself had to be patched with `unsubscribe_url: "/t/1234/unsubscribe"` is direct evidence of the breaking interface change.
```suggestion
# lib/email/message_builder.rb — supply a fallback before interpolation
if @opts[:add_unsubscribe_link]
  template_args[:unsubscribe_url] ||= "#{Discourse.base_url}/my/preferences"

  if response_instructions = @template_args[:respond_instructions]
    respond_instructions = PrettyText.cook(response_instructions).html_safe
    html_override.gsub!("%{respond_instructions}", respond_instructions)
  end

  unsubscribe_link = PrettyText.cook(I18n.t('unsubscribe_link', template_args)).html_safe
  html_override.gsub!("%{unsubscribe_link}", unsubscribe_link)
end
```
Alternative: split into two keys (`unsubscribe_link` and `unsubscribe_link_with_topic`) so callers without a topic context keep the single-argument key.

:red_circle: [cross-file-impact] wordpress action HTML path now silently renders :show template instead of wordpress template in app/controllers/topics_controller.rb:498 (confidence: 90)
`perform_show_response` is shared by `#show`, `#wordpress`, and the new `#unsubscribe`. The PR adds `render :show` unconditionally inside its `format.html` block. Before this change the block was empty, so Rails fell through to convention-based rendering for each caller (`topics/wordpress` for the wordpress action). After this change, an HTML request to `/t/:slug/:topic_id/wordpress` renders `topics/show` silently — any WordPress plugin or automation consuming that endpoint as HTML gets a different view than before.
```suggestion
# Render :show only where it is actually intended — in the two actions that want it
def show
  # ... existing body ...
  perform_show_response
end

def unsubscribe
  # ... existing body ...
  perform_show_response
end

def perform_show_response
  respond_to do |format|
    format.html do
      @description_meta = @topic_view.topic.excerpt
      store_preloaded("topic_#{@topic_view.topic.id}", MultiJson.dump(topic_view_serializer))
      render :show if [:show, :unsubscribe].include?(action_name.to_sym)
    end
    format.json { ... }
  end
end
```

:red_circle: [cross-file-impact] post.topic.unsubscribe_url raises NoMethodError if post.topic is nil at email-send time in app/mailers/user_notifications.rb:295 (confidence: 88)
Emails are sent asynchronously via background jobs. By the time the job runs, the topic may have been soft-deleted or the association may not be loaded, so `post.topic` can be `nil`. `post.topic.unsubscribe_url` then raises `NoMethodError`, crashing the mailer job and potentially dropping the email silently depending on the job error handler. Before this PR, `post.topic` was not accessed in this code path, so the nil-dereference risk is entirely new.
```suggestion
unsubscribe_url: post.topic&.unsubscribe_url,
```
Combine with the `template_args[:unsubscribe_url] ||= ...` fallback in `MessageBuilder` so the missing-topic case still produces a valid email body.

:red_circle: [security] Unescaped title concatenation into HTML enables XSS in app/assets/javascripts/discourse/components/dropdown-button.js.es6:27 (confidence: 88)
`buffer.push("<h4 class='title'>" + title + "</h4>")` concatenates the `title` property directly into an HTML string with no escaping. If any current or future caller of `dropdown-button` passes a value derived from user input — topic title, category name, tag name, username, staff-editable site setting — `<script>` or event handlers can be injected. Handlebars/Ember escape by default; hand-built HTML in `renderString` bypasses that protection. This is a footgun for future callers: adding `title=topic.title` at a call site introduces stored XSS with no warning at the widget.
```suggestion
import { escapeExpression } from "discourse/lib/utilities";

renderString(buffer) {
  const title = this.get('title');
  if (title) {
    buffer.push("<h4 class='title'>" + escapeExpression(title) + "</h4>");
  }
  // ...
}
```
References: https://owasp.org/www-community/attacks/xss/, https://cwe.mitre.org/data/definitions/79.html

## Improvements

:yellow_circle: [security] Unsubscribe uses session auth instead of signed email token like other mailers in app/controllers/topics_controller.rb:98 (confidence: 90)
The action identifies the target user via `current_user` + `ensure_logged_in`. Other Discourse mailer-driven actions (email-change confirmation, global unsubscribe, digest unsubscribe) use `UnsubscribeKey` — an unguessable signed key in the URL that authenticates the specific recipient without requiring an active session. Session-based auth here produces three defects: (a) recipients reading email on a device with no Discourse session must log in first, defeating one-click unsubscribe; (b) on shared devices where the recipient is logged in as a different account, the wrong account is silently unsubscribed; (c) session auth is the root cause of the CSRF issue above — a signed-token model is safe on GET because the token is the authenticator, not the cookie.
```suggestion
# When building the email:
key = UnsubscribeKey.create_key_for(user, "topic", topic: topic)
unsubscribe_url = "#{Discourse.base_url}/t/#{topic.slug}/#{topic.id}/unsubscribe?key=#{key}"

# Controller:
skip_before_filter :ensure_logged_in, only: [:unsubscribe, :unsubscribe_confirm]

def unsubscribe
  key_record = UnsubscribeKey.lookup_by_key(params[:key])
  raise Discourse::NotFound unless key_record && key_record.topic_id == params[:topic_id].to_i
  # ... POST-only mutation keyed off key_record.user_id ...
end
```

:yellow_circle: [security] Triple-stash renders I18n string with interpolated topic title as raw HTML in app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3 (confidence: 85)
`{{{stopNotificiationsText}}}` is a Handlebars triple-stash — its value is rendered as raw HTML. The value is `I18n.t("topic.unsubscribe.stop_notifications", { title: model.fancyTitle })`. Discourse's `fancyTitle` is normally HTML-escaped by the server, so in the common case this is safe. The pattern is fragile, though: (a) safety depends on an invariant (`fancyTitle` always escaped) not enforced at this call site and that has regressed historically; (b) I18n `%{...}` substitution does not re-escape, so a community translation that adds extra `%{...}` placeholders bound to unescaped fields, or an English-string change from `model.fancyTitle` to `model.title`, becomes stored XSS; (c) the triple-stash exists solely to render the `<strong>` tags in the translation — structuring the template safely is cheap.
```suggestion
<!-- server.en.yml -->
topic:
  unsubscribe:
    stop_notifications_prefix: "You will stop receiving notifications for"

<!-- topic/unsubscribe.hbs -->
<p>
  {{i18n "topic.unsubscribe.stop_notifications_prefix"}} <strong>{{model.fancyTitle}}</strong>.
</p>
```

## Risk Metadata
Risk Score: 37/100 (MEDIUM) | Blast Radius: ~70 (core models topic.rb, topic_user.rb, message_builder.rb, topics_controller.rb hit many importers) | Sensitive Paths: none matched configured patterns, but controller introduces a new authenticated, state-mutating public endpoint
AI-Authored Likelihood: LOW
