## Summary
18 files changed, 156 lines added, 70 lines deleted. 6 findings (3 critical, 3 improvements).
Adds per-topic email unsubscribe flow; core controller action has a nil-deref crash path, an unconditional `render :show` that mutates shared code, and a GET-based unsubscribe susceptible to email-client link prefetching.

## Critical

:red_circle: [correctness] `TopicUser.find_by` result is dereferenced without a nil guard in `app/controllers/topics_controller.rb:105` (confidence: 95)
In the new `unsubscribe` action, `tu = TopicUser.find_by(user_id: current_user.id, topic_id: params[:topic_id])` can return `nil` when the current user has never interacted with the topic (no TopicUser row yet — common for users who received a one-off email notification through a group/category watch). The immediately following `tu.notification_level > ...` then raises `NoMethodError: undefined method 'notification_level' for nil:NilClass`, returning a 500 to a user who clicked an unsubscribe link from their inbox. Because the first-time-click scenario is exactly the expected usage pattern, this is a likely crash in production.
```suggestion
    tu = TopicUser.find_or_initialize_by(user_id: current_user.id, topic_id: params[:topic_id])

    if tu.notification_level && tu.notification_level > TopicUser.notification_levels[:regular]
      tu.notification_level = TopicUser.notification_levels[:regular]
    else
      tu.notification_level = TopicUser.notification_levels[:muted]
    end

    tu.save!
```

:red_circle: [cross-file-impact] Unconditional `render :show` added to shared `perform_show_response` in `app/controllers/topics_controller.rb:500` (confidence: 90)
`perform_show_response` is the shared HTML/JSON responder used by `show`, and now by `unsubscribe`. Adding an unconditional `render :show` inside the `format.html` branch means every existing caller of this method now hard-codes the `show` template, overriding Rails' implicit template lookup. The `unsubscribe` route is served by the Ember client via `topicUnsubscribe` (which points to `app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs`), but the server-side render here will now always send `topics/show.html.*` regardless of which controller action invoked it. This couples unrelated actions to the `show` view and can break any action that previously relied on implicit rendering of its own view (e.g. if another action is added later and calls `perform_show_response`). At minimum it is a latent footgun; more likely it silently changes the HTML payload shipped for the new `unsubscribe` response.
```suggestion
      format.html do
        @description_meta = @topic_view.topic.excerpt
        store_preloaded("topic_#{@topic_view.topic.id}", MultiJson.dump(topic_view_serializer))
        render :show unless action_name == 'show' # default lookup already handles show
      end
```

:red_circle: [security] One-click GET unsubscribe is auto-triggered by email-client link prefetching (`config/routes.rb:440`, `app/controllers/topics_controller.rb:98`) (confidence: 85)
The unsubscribe endpoint is a GET that mutates `TopicUser.notification_level` on the first request. Modern mail clients (Gmail's link-scanning proxy, Outlook SafeLinks, corporate AV mail filters) routinely pre-fetch every URL in an inbound email for malware scanning, which will silently trigger unsubscribes the moment the notification email is delivered — before the user has even opened it. This violates the HTTP idempotency contract for GET and makes the feature effectively unreliable for the user flows it is supposed to support. The fix is either (a) render an interstitial confirmation page on GET and perform the state change on a subsequent POST from that page, or (b) require a signed one-time token in the URL and only apply the state change when the form is submitted.
```suggestion
  # In routes.rb — keep GET for the landing page, add POST for the actual mutation:
  get  "t/:slug/:topic_id/unsubscribe" => "topics#unsubscribe",        constraints: {topic_id: /\d+/}
  post "t/:slug/:topic_id/unsubscribe" => "topics#perform_unsubscribe", constraints: {topic_id: /\d+/}
```
References: RFC 7231 §4.2.1 (GET is safe/idempotent); Gmail link-scanning prefetch behavior.

## Improvements

:yellow_circle: [consistency] Misspelled identifier `stopNotificiationsText` (three files) (confidence: 95)
The new controller property `stopNotificiationsText` at `app/assets/javascripts/discourse/controllers/topic-unsubscribe.js.es6:5` is misspelled ("Notificiations" vs "Notifications"). The same typo propagates to `app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3` (`{{{stopNotificiationsText}}}`). Because the property name is part of the public binding contract between controller and template, the typo will be fossilized by any future plugin or theme that references it. Rename both occurrences while this is still a fresh add.
```suggestion
  stopNotificationsText: function() {
    return I18n.t("topic.unsubscribe.stop_notifications", { title: this.get("model.fancyTitle") });
  }.property("model.fancyTitle"),
```

:yellow_circle: [testing] New `TopicsController#unsubscribe` action has no spec coverage in `spec/controllers/topics_controller_spec.rb` (confidence: 90)
The PR adds a controller action with non-trivial branching (slug mismatch redirect, notification-level toggle, XHR/HTML dual response) but only updates `spec/components/email/message_builder_spec.rb` to pass `unsubscribe_url`. No request or controller spec exercises the new endpoint, so the nil-deref and shared-render regressions above would not have been caught by CI. Add a `describe "#unsubscribe"` block covering (1) the no-existing-TopicUser case, (2) the tracking→regular transition, (3) the regular→muted transition, and (4) the slug-mismatch 301 redirect.

:yellow_circle: [security] Template renders unescaped `{{{stopNotificiationsText}}}` interpolating `model.fancyTitle` (`app/assets/javascripts/discourse/templates/topic/unsubscribe.hbs:3`) (confidence: 70)
The template uses Handlebars' triple-brace (raw HTML) form. The underlying I18n string `You will stop receiving notifications for <strong>{{title}}</strong>.` relies on that to render the `<strong>` tag, but it is supplied with `model.fancyTitle` via the MessageFormat double-brace `{{title}}` placeholder at `config/locales/client.en.yml:984`. `fancyTitle` contains HTML-escaped title text plus emoji markup in Discourse, but because the translation's `{{title}}` interpolation is not itself HTML-escaped (it is embedded inside the triple-brace sink), any future change upstream that makes `fancyTitle` include less-trusted HTML (e.g. admin-controlled title decorations) will become a stored XSS sink on this page. Prefer double-brace interpolation and a `{{#html-safe ...}}` or a dedicated helper that escapes `title` before concatenating it with the surrounding markup.

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: 18 files across controllers, models, mailer, JS routes/controllers/views, templates, locales, and routing | Sensitive Paths: `app/mailers/`, `app/controllers/`, `config/routes.rb`
AI-Authored Likelihood: LOW
