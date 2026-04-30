## Summary
18 files changed, ~150 lines added, ~70 lines deleted. 9 findings (8 critical, 1 improvement, 0 nitpicks).
State-mutating unsubscribe action exposed over GET enables CSRF and prefetch-triggered unsubscribes; multiple correctness and XSS issues in the new controller, template, and Ember route.

## Critical

:red_circle: [correctness] Nil dereference on `tu` when no TopicUser record exists — opaque 500 with no graceful handling in app/controllers/topics_controller.rb:102 (confidence: 97)
`TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])` returns `nil` when the user has no existing TopicUser row for this topic (i.e., they have never interacted with it and have no notification preference stored). The very next line unconditionally calls `tu.notification_level`, which raises `NoMethodError: undefined method 'notification_level' for nil:NilClass`, returning a 500 to the user. The `tu.save!` call is equally affected. This is the normal case for many users who receive unsubscribe emails but have never explicitly set a notification level. Additionally, even on a found row, `notification_level` may itself be nil, yielding an `ArgumentError` on any comparison. There is no graceful error handling or fallback — the user receives no actionable feedback.
```suggestion
tu = TopicUser.find_or_initialize_by(user_id: current_user.id, topic_id: params[:topic_id])

current_level = tu.notification_level.to_i
if current_level > TopicUser.notification_levels[:regular]
  tu.notification_level = TopicUser.notification_levels[:regular]
else
  tu.notification_level = TopicUser.notification_levels[:muted]
end

tu.save!
```

:red_circle: [security] State-mutating unsubscribe action exposed over GET — CSRF-free and prefetch-triggered in app/controllers/topics_controller.rb:95 (confidence: 95)
The `unsubscribe` action is registered as a GET route and mutates `TopicUser.notification_level` via `tu.save!`. Rails' `protect_from_forgery` only validates CSRF tokens on non-GET verbs, so this endpoint has zero CSRF protection. `skip_before_filter :check_xhr` allows it to be invoked from arbitrary contexts: image tags, link previews, email client prefetchers, browser speculative prefetch, link unfurlers, and antivirus link scanners. A trivial attack vector is `<img src="https://forum.example.com/t/any-slug/12345/unsubscribe">` posted anywhere the victim can view it. More critically, mainstream email security gateways (Defender ATP Safe Links, Mimecast, Proofpoint URL Defense) auto-fetch every link in incoming email — meaning the unsubscribe link sent in the notification email will be triggered by the recipient's security gateway before the user opens the message. This violates RFC 7231 §4.2.1 (GET must be safe and idempotent).
```suggestion
# routes.rb
get  "t/:slug/:topic_id/unsubscribe" => "topics#show_unsubscribe"
post "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe"

# topics_controller.rb — render confirmation page on GET, mutate only on POST
def show_unsubscribe
  @topic_view = TopicView.new(params[:topic_id], current_user)
  perform_show_response
end

def unsubscribe
  tu = TopicUser.find_or_initialize_by(user_id: current_user.id, topic_id: params[:topic_id])
  new_level = tu.notification_level.to_i > TopicUser.notification_levels[:regular] ?
                TopicUser.notification_levels[:regular] :
                TopicUser.notification_levels[:muted]
  tu.update!(notification_level: new_level)
  perform_show_response
end
```
[References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/352.html, https://datatracker.ietf.org/doc/html/rfc7231#section-4.2.1, https://datatracker.ietf.org/doc/html/rfc8058]

:red_circle: [security] Triple-stash Handlebars renders i18n string with user-controlled topic title as raw HTML (stored XSS) in app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3 (confidence: 95)
`{{{stopNotificiationsText}}}` (triple-stash = no HTML escaping) renders the result of `I18n.t("topic.unsubscribe.stop_notifications", { title: model.fancyTitle })`. The i18n string is `"You will stop receiving notifications for <strong>{{title}}</strong>."`. Discourse-bundled I18n.js does NOT HTML-escape interpolated values by default. `fancyTitle` may contain HTML (e.g., emoji `<img>` markup); a crafted topic title such as `</strong><img src=x onerror=alert(1)><strong>` will be rendered verbatim, producing stored XSS on the unsubscribe page for any user who follows the unsubscribe link for that topic.
```suggestion
{{!-- unsubscribe.hbs --}}
<p>
  You will stop receiving notifications for <strong>{{model.fancyTitle}}</strong>.
</p>
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [correctness] Other `add_unsubscribe_link: true` callers will emit broken interpolation — unsubscribe_url now required by translation in app/mailers/user_notifications.rb:292 (confidence: 95)
`config/locales/server.en.yml` changed the `unsubscribe_link` translation from a single-sentence string to a multi-line string that interpolates both `%{user_preferences_url}` AND `%{unsubscribe_url}`. `lib/email/message_builder.rb` calls `I18n.t('unsubscribe_link', template_args)` for every caller that passes `add_unsubscribe_link: true`. Only `send_notification_email` was updated in this PR. Other mailers — digest emails, signup, password reset, system mailers — have not been updated and do not pass `unsubscribe_url:`. At runtime, `I18n.t` will raise `I18n::MissingInterpolationArgument` or emit a literal `[missing %{unsubscribe_url} interpolation]` string, producing broken email footers across all non-notification email types.
```suggestion
# config/locales/server.en.yml — make the per-topic sentence conditional on the variable being present
unsubscribe_link: "To unsubscribe from these emails, visit your [user preferences](%{user_preferences_url})."
unsubscribe_link_with_topic: |
  To unsubscribe from these emails, visit your [user preferences](%{user_preferences_url}).

  To stop receiving notifications about this particular topic, [click here](%{unsubscribe_url}).

# lib/email/message_builder.rb — pick the variant based on whether unsubscribe_url is present
key = @template_args[:unsubscribe_url].present? ? 'unsubscribe_link_with_topic' : 'unsubscribe_link'
unsubscribe_link = PrettyText.cook(I18n.t(key, @template_args)).html_safe
```

:red_circle: [correctness] Explicit `render :show` inside `perform_show_response` causes DoubleRenderError for all HTML topic views in app/controllers/topics_controller.rb:497 (confidence: 90)
Prior to this PR, the HTML format block in `perform_show_response` had no explicit render, relying on Rails' implicit template rendering. The PR adds `render :show` explicitly. The existing `show` action calls `perform_show_response` and Rails' implicit render then attempts a second render, raising `AbstractController::DoubleRenderError` for every HTML GET to `t/:slug/:topic_id`. Both the `show` action and the `unsubscribe` redirect target are affected.
```suggestion
format.html do
  @description_meta = @topic_view.topic.excerpt
  store_preloaded("topic_#{@topic_view.topic.id}", MultiJson.dump(topic_view_serializer))
  # do not call render here; let Rails infer or call explicitly only from actions that need it
end
```

:red_circle: [correctness] Promise rejection from `PostStream.loadTopicView` is unhandled — silent route failure on deleted or private topics in app/assets/javascripts/discourse/routes/topic-unsubscribe.js.es6:4 (confidence: 90)
`model()` returns `PostStream.loadTopicView(params.id).then(...)` with no `.catch`. If `loadTopicView` rejects (404 for a deleted topic, 403 for a topic that became private, or a network error), the rejection propagates unhandled. The user sees no actionable feedback and the route silently fails. This is particularly impactful because the route is reached only via email links — topics may well have been deleted or made private by the time the user clicks.
```suggestion
model(params) {
  const topic = this.store.createRecord("topic", { id: params.id });
  return PostStream.loadTopicView(params.id).then(json => {
    topic.updateFromJson(json);
    return topic;
  }).catch(err => {
    Ember.Logger.error("topic-unsubscribe route failed to load topic", err);
    this.transitionTo("exception-unknown");
  });
},
```

:red_circle: [correctness] `PostStream.loadTopicView` called as static class method — likely does not exist in this form in app/assets/javascripts/discourse/routes/topic-unsubscribe.js.es6:1 (confidence: 90)
The route calls `PostStream.loadTopicView(params.id)` as a static method on the imported PostStream model. In conventional Discourse, `loadTopicView` is an instance method on a PostStream attached to a Topic, not a static loader returning topic JSON. If the static method does not exist, the route will throw `TypeError: PostStream.loadTopicView is not a function` for every unsubscribe navigation.
```suggestion
import Topic from "discourse/models/topic";

export default Discourse.Route.extend({
  model(params) {
    return Topic.find(params.id, {});
  },
  // ...
});
```

:red_circle: [correctness] Deprecated import: `discourse/controllers/object` (ObjectController removed in Ember 2.0) in app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6:1 (confidence: 88)
`import ObjectController from "discourse/controllers/object"` relies on a shim that was deprecated in Ember 1.11 and removed in Ember 2.0. Discourse removed the `controllers/object` shim during its Ember upgrade. This import will fail to resolve in any modern Discourse version, breaking the entire unsubscribe controller.
```suggestion
import Controller from "@ember/controller";
import { computed } from "@ember/object";

export default Controller.extend({
  stopNotificationsText: computed("model.fancyTitle", function() {
    return I18n.t("topic.unsubscribe.stop_notifications", { title: this.get("model.fancyTitle") });
  })
});
```

## Improvements

:yellow_circle: [correctness] Route param name mismatch: Ember uses `:id`, Rails route declares `:topic_id` in app/assets/javascripts/discourse/routes/app-route-map.js.es6:13 (confidence: 88)
The Ember route map declares path `/t/:slug/:id/unsubscribe` while `config/routes.rb` declares `t/:slug/:topic_id/unsubscribe`. The Rails controller reads `params[:topic_id]`. URL matching works because Rails matches path segments positionally, but the naming inconsistency means `params[:id]` and `params[:topic_id]` refer to the same positional segment under different names depending on which layer handles the request. This is a latent maintenance hazard: any future code that references `params[:id]` in the unsubscribe action, or `params.topic_id` on the Ember side, will silently receive `nil` or `undefined`.
```suggestion
this.route('topicUnsubscribe', { path: '/t/:slug/:topic_id/unsubscribe' });
```

## Risk Metadata
Risk Score: 47/100 (MEDIUM) | Blast Radius: high (core models topic.rb, topic_user.rb; shared dropdown-button component; mailer subsystem) | Sensitive Paths: none matched
AI-Authored Likelihood: MEDIUM

(10 additional findings below confidence threshold)
