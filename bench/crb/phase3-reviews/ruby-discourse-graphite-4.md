## Summary
28 files changed, 651 lines added, 13 lines deleted. 22 findings (13 critical, 8 improvements, 1 nitpick).
PR introduces Discourse's embed/RSS-feed import pipeline with multiple critical security and data-integrity defects: SSRF via `Kernel#open`, stored XSS via a new `raw_html` cook path that bypasses sanitization, a migration default that makes every existing post render as raw HTML, an ERB syntax error in the new view, a Topic-orphaning race in `TopicEmbed.import`, and a `force: true` added to an already-shipped migration.

## Critical

:red_circle: [security] SSRF via `Kernel#open` in `TopicEmbed.import_remote` in app/models/topic_embed.rb:40 (confidence: 95)
`open(url).read` uses Ruby's `Kernel#open` (from `open-uri`) on a URL that flows from the `embed_url` request parameter, from `i.link`/`i.id` RSS items in `PollFeed`, and from `t[:link]` in `lib/tasks/disqus.thor`. `Kernel#open` supports arbitrary schemes — `file:///etc/passwd`, `ftp://…`, cloud-metadata endpoints — and, on Ruby versions where `open-uri` has not monkey-patched away the behavior, treats a leading `|` as a shell command. The only guard is `TopicRetriever#invalid_host?`, which is bypassed entirely by `disqus.thor` and by the `poll_feed` path (which trusts feed-supplied links).
```suggestion
require 'net/http'
uri = URI.parse(url)
raise Discourse::InvalidParameters.new(:url) unless %w[http https].include?(uri.scheme)
raise Discourse::InvalidParameters.new(:url) if PrivateAddressCheck.resolves_to_private_address?(uri.host)
response = Net::HTTP.get_response(uri)
doc = Readability::Document.new(response.body,
                                tags: %w[div p code pre h1 h2 h3 b em i strong a img],
                                attributes: %w[href src])
```
[References: https://cwe.mitre.org/data/definitions/918.html, https://owasp.org/Top10/A10_2021-Server-Side_Request_Forgery_%28SSRF%29/]

:red_circle: [security] SSRF / command injection in scheduled feed poller in app/jobs/scheduled/poll_feed.rb:22 (confidence: 90)
`SimpleRSS.parse open(SiteSetting.feed_polling_url)` passes a site-setting-controlled URL straight to `Kernel#open`. Any attacker who can write site settings (or who compromises the admin UI) can point it at `file://`, internal addresses, or a leading-pipe command. There is also no scheme validation on per-item `i.link` values before they are fed into `TopicEmbed.import`.
```suggestion
uri = URI.parse(SiteSetting.feed_polling_url)
raise "Invalid feed URL" unless %w[http https].include?(uri.scheme)
rss = SimpleRSS.parse(Net::HTTP.get_response(uri).body)
rss.items.each do |i|
  url = i.link
  url = i.id if url.blank? || url !~ /\Ahttps?:\/\//i
  next unless url =~ /\Ahttps?:\/\//i
  content = CGI.unescapeHTML((i.content || i.summary || '').to_s.scrub)
  TopicEmbed.import(user, url, i.title, content)
end
```
[References: https://cwe.mitre.org/data/definitions/78.html, https://cwe.mitre.org/data/definitions/918.html]

:red_circle: [security] Stored XSS through `raw_html` cook path rendered with `raw post.cooked` in app/views/embed/best.html.erb:22 (confidence: 95)
`TopicEmbed.import` creates posts with `cook_method: Post.cook_methods[:raw_html]`. `Post#cook` now short-circuits to `return raw` for that method, so the HTML pulled from an RSS feed or scraped via Readability is persisted verbatim. `best.html.erb` then does `<%= raw post.cooked %>`. Readability only strips tags outside its allowlist but does not strip `onerror=`, `onclick=`, `javascript:` URIs, or `<script>` children inside allowed tags like `<a>`/`<img>`. Any attacker-controlled feed or scraped page yields stored XSS in the Discourse-origin iframe (and, because `X-Frame-Options: ALLOWALL` is set, on arbitrary embedder pages).
```suggestion
# In TopicEmbed.import, before creating/revising the post:
contents = Sanitize.fragment(contents, Sanitize::Config::RELAXED)
# Or drop cook_method: :raw_html entirely and let PrettyText sanitize.
```
[References: https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [security] JavaScript-context injection via `request.referer` in postMessage targetOrigin in app/views/layouts/embed.html.erb:10 (confidence: 90)
`parent.postMessage({...}, '<%= request.referer %>');` interpolates the Referer directly into a single-quoted JavaScript string. ERB's default escaping is HTML-context, not JS-context — a referer of `x'); alert(document.domain);//` breaks out of the string literal and runs arbitrary JS in the Discourse origin. Using Referer as a postMessage targetOrigin is also fundamentally wrong: it is spoofable, may be stripped by referrer policy, and becomes `""` (a pattern match of "no origin" → broadcast to any origin in some browsers) on many requests.
```suggestion
<%
  target_origin = begin
    host = URI(request.referer || '').host
    host == SiteSetting.embeddable_host ? URI(request.referer).origin : ''
  rescue URI::InvalidURIError
    ''
  end
%>
<script>
  (function () {
    window.onload = function () {
      if (parent && <%= target_origin.to_json.html_safe %>) {
        parent.postMessage({type: 'discourse-resize', height: document.body.offsetHeight},
                           <%= target_origin.to_json.html_safe %>);
      }
    };
  })();
</script>
```
[References: https://cwe.mitre.org/data/definitions/79.html, https://cwe.mitre.org/data/definitions/95.html]

:red_circle: [security] postMessage origin check is a substring match in app/assets/javascripts/embed.js:14 (confidence: 90)
`discourseUrl.indexOf(e.origin) === -1` treats any `e.origin` that is a substring of `discourseUrl` as trusted. With `discourseUrl = "https://forum.example.com"`, both `"https://forum.example.co"` (dropped `m`) and the empty string `""` pass, and future handler extensions will inherit the weak check.
```suggestion
var expectedOrigin = new URL(discourseUrl).origin;
function postMessageReceived(e) {
  if (!e || e.origin !== expectedOrigin) { return; }
  if (e.data && e.data.type === 'discourse-resize' && typeof e.data.height === 'number') {
    iframe.height = e.data.height + 'px';
  }
}
```
[References: https://cwe.mitre.org/data/definitions/346.html]

:red_circle: [security] Clickjacking / embed auth based on spoofable Referer + `X-Frame-Options: ALLOWALL` in app/controllers/embed_controller.rb:22 (confidence: 85)
`ensure_embeddable` sets `X-Frame-Options: ALLOWALL` (a non-standard value that most browsers treat as "no framing restriction") and then tries to enforce origin via `URI(request.referer || '').host != SiteSetting.embeddable_host`. The host compare ignores scheme and userinfo (`http://evil@embeddable-host/...`) and the Referer is client-controlled. Combined with the GET-based `embed/best` endpoint that enqueues a background job, this is the primary auth gate for the whole embed surface and it cannot be trusted.
```suggestion
def ensure_embeddable
  raise Discourse::InvalidAccess.new('embeddable host not set') if SiteSetting.embeddable_host.blank?
  host = URI(request.referer || '').host
  raise Discourse::InvalidAccess.new('invalid referer host') if host.to_s.downcase != SiteSetting.embeddable_host.to_s.downcase
  response.headers['Content-Security-Policy'] = "frame-ancestors https://#{SiteSetting.embeddable_host}"
  response.headers.delete('X-Frame-Options')
rescue URI::InvalidURIError
  raise Discourse::InvalidAccess.new('invalid referer host')
end
```
[References: https://cwe.mitre.org/data/definitions/1021.html, https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Content-Security-Policy/frame-ancestors]

:red_circle: [correctness] Migration `default: 1` maps to `:raw_html`, turning every existing post into raw HTML in db/migrate/20131219203905_add_cook_method_to_posts.rb:4 (confidence: 98)
`Post.cook_methods = Enum.new(:regular, :raw_html)` assigns `:regular=0, :raw_html=1` (identical to the existing `Post.types` enum in the same file). The new column's default is `1`, so every row backfilled from the migration receives `cook_method = 1 = :raw_html`. The `return raw if cook_method == Post.cook_methods[:raw_html]` short-circuit in `Post#cook` then causes every existing post's `cooked` output to collapse to the unprocessed raw Markdown — no sanitization, no Markdown rendering, no Discourse filters. Combined with the new XSS path this turns every pre-existing post into a potential XSS sink.
```suggestion
add_column :posts, :cook_method, :integer, default: 0, null: false
```

:red_circle: [correctness] `<%- end if %>` is a Ruby syntax error — embed view will 500 in app/views/embed/best.html.erb:7 (confidence: 97)
The `if @topic_view.posts.present? / else / end if` block closes with `<%- end if %>`. `end if` is not valid Ruby (`if` as a modifier requires a trailing expression). ERB compiles this to `end if; …`, which raises `SyntaxError` at template compile time. The `embed_controller_spec` stubs this view out in most paths and the "success" context never asserts the rendered body, so the bug is not caught by the new specs.
```suggestion
  <%- end %>
```

:red_circle: [correctness] `force: true` added to an already-shipped migration in db/migrate/20131223171005_create_top_topics.rb:3 (confidence: 92)
Modifying a migration file that has already been committed and run in production is unusual enough; doing so to add `force: true` is actively destructive. Any environment that replays migrations from scratch (CI, new developer setup, `rake db:schema:load`, staging rebuilds from snapshots, `db:migrate:redo VERSION=…`) will `DROP TABLE top_topics` silently and recreate it empty. The correct pattern is a brand-new migration that alters the existing table.
```suggestion
# revert force: true here; put any schema change in a new migration
create_table :top_topics do |t|
```

:red_circle: [correctness] Race in `TopicEmbed.import` orphans Topics on duplicate-URL contention in app/models/topic_embed.rb:17-25 (confidence: 90)
Two concurrent imports of the same `url` can both pass the `embed.blank?` check before either commits. Both enter `Topic.transaction`, both call `PostCreator#create` (which commits the Topic/Post via its own transaction/savepoint), and both reach `TopicEmbed.create!`. The second call raises `ActiveRecord::RecordNotUnique` on the unique index; the outer transaction rolls back the `TopicEmbed` row, but the Topic/Post that `PostCreator` committed remains. The next import for the same URL re-enters with `embed.blank?` still true (if the winning row was also rolled back on its own failure path) and creates yet another orphan, indefinitely.
```suggestion
Topic.transaction do
  embed = TopicEmbed.lock.find_by(embed_url: url)
  # ... or rescue RecordNotUnique and refetch:
  begin
    TopicEmbed.create!(topic_id: post.topic_id, embed_url: url,
                       content_sha1: content_sha1, post_id: post.id)
  rescue ActiveRecord::RecordNotUnique
    # another writer won the race; treat as update path
    raise ActiveRecord::Rollback
  end
end
```

:red_circle: [cross-file-impact] `PostRevisor#update_post` now honors `skip_validations` for all callers in lib/post_revisor.rb:85 (confidence: 90)
Before this PR, `@post.save` always ran validations regardless of the `skip_validations` option. After the change, `@post.save(validate: !@opts[:skip_validations])` means every existing caller that already passed `skip_validations: true` (there are admin tools and migration jobs in Discourse that do) silently stops validating. The common case (option absent → `!nil == true` → `validate: true`) is preserved, but the semantic change is not mentioned in the PR description. Audit existing call-sites before merging.
[References: existing `skip_validations` usages in admin/migration paths]

:red_circle: [cross-file-impact] Disqus importer now fetches live URLs with no error handling in lib/tasks/disqus.thor:142 (confidence: 88)
The old path constructed a permalink stub locally (no network). The new path calls `TopicEmbed.import_remote(user, t[:link], title: t[:title])`, which calls `open(t[:link]).read` with no rescue. For archived Disqus threads whose original article URL is dead (404 / DNS failure / connection refused), the first failing thread aborts the entire import loop. Threads already imported stay; everything after the failure is silently skipped.
```suggestion
parser.threads.each do |id, t|
  begin
    post = TopicEmbed.import_remote(user, t[:link], title: t[:title])
  rescue => e
    puts "Skipping #{t[:link]}: #{e.class}: #{e.message}"
    next
  end
  # ...
end
```

:red_circle: [correctness] `i.content.scrub` NPEs on RSS 2.0 feeds that use `<description>` instead of `<content:encoded>` in app/jobs/scheduled/poll_feed.rb:31 (confidence: 85)
Many RSS 2.0 feeds do not emit a `content:encoded` element; SimpleRSS then returns `nil` for `i.content`. `nil.scrub` raises `NoMethodError`, and because `sidekiq_options retry: false` is set, the entire feed poll aborts and no retry happens — every item after the first `nil`-content item is silently lost until the feed is reshaped.
```suggestion
content = CGI.unescapeHTML((i.content || i.summary || i.description || '').to_s.scrub)
next if content.blank?
TopicEmbed.import(user, url, i.title, content)
```

## Improvements

:yellow_circle: [security] `TopicRetriever#invalid_host?` ignores scheme, userinfo, and case in lib/topic_retriever.rb:13 (confidence: 75)
The check is `SiteSetting.embeddable_host != URI(@embed_url).host`. `file://embeddable-host/etc/passwd`, `ftp://embeddable-host/…`, and `http://evil@embeddable-host/` all pass. DNS is case-insensitive; the compare is case-sensitive.
```suggestion
def invalid_host?
  uri = URI(@embed_url)
  return true unless %w[http https].include?(uri.scheme)
  return true if uri.userinfo.present?
  uri.host.to_s.downcase != SiteSetting.embeddable_host.to_s.downcase
rescue URI::InvalidURIError
  true
end
```

:yellow_circle: [security] HTML injection in the `imported_from` footer in app/models/topic_embed.rb:8 (confidence: 70)
`I18n.t('embed.imported_from', link: "<a href='#{url}'>#{url}</a>")` interpolates `url` into a single-quoted HTML attribute. A feed-supplied URL of `https://x/'><img src=x onerror=…>` escapes the attribute, and because the post is stored as `raw_html` it renders verbatim.
```suggestion
safe_url = CGI.escapeHTML(url)
contents = contents + "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: "<a href='#{safe_url}'>#{safe_url}</a>")}</small>\n"
```

:yellow_circle: [security] `embed_controller#best` enqueues jobs with `no_throttle: staff?` and no rate limit in app/controllers/embed_controller.rb:13 (confidence: 65)
The GET endpoint has no CSRF token (rightly, for cross-site embed use) and no rate limiter. A staff member tricked into visiting a crafted embedder page triggers `TopicRetriever.new(embed_url, no_throttle: true).retrieve`, which calls `TopicEmbed.import_remote` with no throttle — an SSRF amplification channel.
```suggestion
RateLimiter.new(current_user, "embed-retrieve", 10, 1.minute).performed!
Jobs.enqueue(:retrieve_topic, embed_url: embed_url)  # drop user_id so staff bypass is unreachable
```

:yellow_circle: [correctness] Redis SETNX + EXPIRE is not atomic in lib/topic_retriever.rb:19 (confidence: 88)
If the process is killed between `setnx` and `expire`, the key persists without a TTL, throttling that URL forever.
```suggestion
if $redis.set(retrieved_key, "1", nx: true, ex: 60)
  return false
end
true
```

:yellow_circle: [cross-file-impact] Disqus importer silently loses historical `created_at` in lib/tasks/disqus.thor:142 (confidence: 95)
The old `PostCreator` call passed `created_at: Date.parse(t[:created_at])` to preserve the original thread date. `TopicEmbed.import_remote` has no `created_at` channel — every migrated thread is stamped with the import time, destroying the historical ordering.
```suggestion
# Extend TopicEmbed.import/import_remote to accept created_at in opts
# and thread it through to PostCreator.
TopicEmbed.import_remote(user, t[:link], title: t[:title], created_at: Date.parse(t[:created_at]))
```

:yellow_circle: [cross-file-impact] Disqus importer drops `--category` / `-c` option in lib/tasks/disqus.thor:117 (confidence: 95)
The `method_option :category` declaration and all `category_id` plumbing are removed. Existing automation that runs `disqus.thor import -c <name>` either errors out (Thor rejects unknown options by default) or silently loses the category. The replacement path offers no category handling at all.

:yellow_circle: [correctness] `contents << "\n<hr>…"` mutates the caller's string in app/models/topic_embed.rb:8 (confidence: 80)
`<<` is in-place append. In the current callers the string is freshly allocated each time so no bleed, but it is a footgun — any future caller that reuses the string object, or any spec that calls `import` twice with the same `contents` variable, accumulates the footer repeatedly.
```suggestion
contents = contents + "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: "<a href='#{url}'>#{url}</a>")}</small>\n"
```

:yellow_circle: [cross-file-impact] Unauthenticated embed visits can trigger an infinite reload loop in app/controllers/embed_controller.rb:13 (confidence: 80)
When `current_user` is nil, `user_id: nil` is passed to `Jobs.enqueue(:retrieve_topic)`; `TopicRetriever#fetch_http` returns early if `SiteSetting.embed_by_username` is blank, so no topic is ever created. The `loading.html.erb` view unconditionally `setTimeout(reload, 30000)`, so an anonymous visitor on a misconfigured site gets a silent, forever-reloading iframe with no error signal.

## Nitpicks

:white_circle: [correctness] Case-sensitive scheme regex silently drops uppercase-`HTTP://` feed URLs in app/jobs/scheduled/poll_feed.rb:29 and app/models/topic_embed.rb:7 (confidence: 72)
`/^https?\:\/\//` has no `i` flag. RFC 3986 §3.1 makes scheme case-insensitive; real-world feeds occasionally emit `HTTP://`. Such items fall through to `i.id`, which then fails the same case-sensitive guard in `TopicEmbed.import` and are silently dropped.

## Conflicts
(none — findings across agents are additive, with migration default (critical) appearing in both correctness and cross-file-impact agents and merged as a single finding above)

## Risk Metadata
Risk Score: 71/100 (HIGH) | Blast Radius: 100 (touches core `post.rb`, `post_creator.rb`, `post_revisor.rb` — cross-cutting for every post in the app) | Sensitive Paths: 4 matches on `*migration*` (incl. destructive `force: true` addition) | File Size/Scope: 80 (~750 diff lines across 28 files, 6 new production source files) | Test Coverage Gap: 69 (9 of 13 production files lack new specs — views, `embed.js`, `post.rb` cook change, `post_creator.rb`, `post_revisor.rb`, `disqus.thor`, `retrieve_topic.rb`)
AI-Authored Likelihood: LOW (idiomatic Ruby style, no uniform boilerplate blocks, no attribution artifacts; commit history not available in this shim repo so some uncertainty remains)

Recommendation: request-changes — the migration default, the ERB syntax error, and the SSRF/XSS pair are all release-blocking.
