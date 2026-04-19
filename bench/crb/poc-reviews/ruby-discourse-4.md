## Summary
28 files changed, 608 lines added, 13 lines deleted. 13 findings (5 critical, 6 improvements, 2 nitpicks).
New embed/RSS-import subsystem introduces several security holes (postMessage target-origin XSS, referer-based auth, SSRF via `open-uri`, stored XSS via `raw post.cooked`) plus a likely Ruby syntax error in `best.html.erb`.

## Critical

:red_circle: [security] postMessage targetOrigin uses unescaped `request.referer` in `app/views/layouts/embed.html.erb`:9 (confidence: 95)
The inline script does `parent.postMessage({...}, '<%= request.referer %>')`. Two problems:
1. `request.referer` is fully attacker-controlled (any page linking in can set it), and it is emitted as a JS string literal **without HTML/JS escaping**. A referer containing a single quote, `</script>`, or a `\u2028` line separator breaks out of the string and yields XSS inside the embed iframe. Because the embed iframe renders `raw post.cooked` content, this is a high-impact surface.
2. Even if escaped, using the referer as targetOrigin is meaningless — an attacker-controlled site simply sends its own URL, so the postMessage lands at whatever origin the attacker wants. The targetOrigin should be `SiteSetting.embeddable_host` (normalized), not the referer.
```suggestion
parent.postMessage({type: 'discourse-resize', height: document['body'].offsetHeight},
                   <%= SiteSetting.embeddable_host.to_json.html_safe %>);
```
References: https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage#security_concerns

:red_circle: [security] Referer-based access control is trivially bypassable in `app/controllers/embed_controller.rb`:26 (confidence: 90)
`ensure_embeddable` treats a matching `request.referer` host as proof the request came from the embedding site. Referers are unauthenticated request headers — any attacker can forge one from `curl -H "Referer: http://eviltrout.com/"`, or strip it entirely to bypass when `request.referer` is blank (then `URI('').host` is `nil`, and the check `nil != host` still raises — OK — but an attacker can always supply the header). Worse, the check is an exact host string match; it ignores scheme and port, and does not lowercase. This cannot be used as an authorization boundary for anything sensitive; it should only be treated as a best-effort clickjacking/embedding hint.
```suggestion
def ensure_embeddable
  raise Discourse::InvalidAccess.new('embeddable host not set') if SiteSetting.embeddable_host.blank?
  referer_uri = URI(request.referer.to_s)
  expected = SiteSetting.embeddable_host.to_s.downcase
  raise Discourse::InvalidAccess.new('invalid referer host') unless referer_uri.host && referer_uri.host.downcase == expected
  response.headers['X-Frame-Options'] = "ALLOW-FROM #{SiteSetting.embeddable_host}"
rescue URI::InvalidURIError
  raise Discourse::InvalidAccess.new('invalid referer host')
end
```

:red_circle: [security] SSRF via `open-uri` in `app/models/topic_embed.rb`:42 (confidence: 90)
`Readability::Document.new(open(url).read, ...)` and `poll_feed`'s `open(SiteSetting.feed_polling_url)` use Ruby's `open-uri`, which accepts arbitrary HTTP(S) URLs and follows redirects. Because `url` reaches this code path from two sources — (a) `embed_url` query parameter routed through `TopicRetriever#fetch_http`, and (b) the admin-configured `feed_polling_url` — a forged referer plus a matching `embed_url` host lets an external caller cause the server to fetch arbitrary internal URLs (e.g., metadata service, internal admin panels). Also, `Kernel#open` on older Ruby/Rails versions can accept pipe syntax (`"|cmd"`) when passed strings that weren't originally URIs; using `URI.open` with a pre-parsed `URI` object is safer.
```suggestion
uri = URI.parse(url)
raise Discourse::InvalidAccess.new('invalid scheme') unless %w[http https].include?(uri.scheme)
raise Discourse::InvalidAccess.new('private host') if PrivateIpChecker.private?(uri.host) # enforce RFC1918/loopback/link-local denylist
body = uri.open(read_timeout: 10, open_timeout: 5).read
doc = Readability::Document.new(body, tags: %w[div p code pre h1 h2 h3 b em i strong a img], attributes: %w[href src])
```
References: https://owasp.org/www-community/attacks/Server_Side_Request_Forgery

:red_circle: [security] Stored XSS via `raw post.cooked` on RSS-imported posts in `app/views/embed/best.html.erb`:13 (confidence: 85)
Imported feed items are stored with `cook_method: raw_html`, and `Post#cook` returns the raw content verbatim for that method. `best.html.erb` then emits `<%= raw post.cooked %>` inside the embed iframe. If the feed (`feed_polling_url`) is ever compromised, serves attacker content, or an operator points at a lightly-curated source, arbitrary JavaScript lands in every embedding page's iframe. The `absolutize_urls` / Nokogiri fragment pass only rewrites `href`/`src` — it does not sanitize `<script>`, inline event handlers, or `javascript:` URIs. Either sanitize the content with Discourse's existing HTML sanitizer before import, or restrict the tag allowlist to what `ruby-readability` produces and strip on-* attributes.
```suggestion
# In TopicEmbed.import, before creating/revising:
sanitized = Sanitize.fragment(contents, Sanitize::Config::BASIC.merge(
  elements: %w[div p code pre h1 h2 h3 b em i strong a img br hr]
))
```

:red_circle: [correctness] Likely Ruby syntax error `<%- end if %>` in `app/views/embed/best.html.erb`:5 (confidence: 80)
The block
```erb
<%- if @topic_view.posts.present? %>
  ...
<%- else %>
  ...
<%- end if %>
```
places an `end if` with no condition trailing it. Ruby parses `end if <expr>` as a postfix conditional on `end`, but there is no `<expr>` on the line, so the ERB compiles to `end if \n  <%= link_to ... %>` and the parser either raises `syntax error, unexpected end-of-input` or binds the trailing link_to call as the `if` condition, giving very surprising behavior. The trailing `if` is almost certainly a typo for just `<%- end %>`.
```suggestion
<%- end %>
```

## Improvements

:yellow_circle: [correctness] `absolutize_urls` breaks protocol-relative URLs in `app/models/topic_embed.rb`:59 (confidence: 85)
`href.start_with?('/')` also matches protocol-relative URLs like `//cdn.example.com/x.jpg`, producing `http://eviltrout.com/cdn.example.com/x.jpg` — a broken link that silently loses the intended CDN reference. Guard against the `//` prefix.
```suggestion
if href.present? && href.start_with?('/') && !href.start_with?('//')
  a['href'] = "#{prefix}/#{href.sub(/^\/+/, '')}"
end
```

:yellow_circle: [correctness] Spec does not match controller implementation in `spec/controllers/embed_controller_spec.rb`:34 (confidence: 80)
The test `"tells the topic retriever to work when no previous embed is found"` stubs `TopicRetriever.expects(:new).returns(retriever)` and expects `retriever.retrieve` to be called. But the controller does not instantiate `TopicRetriever` — it calls `Jobs.enqueue(:retrieve_topic, ...)`. This spec will fail as written, or silently pass only because `mocha` unmet expectations may not bubble up depending on the harness. Either change the spec to assert `Jobs.expects(:enqueue).with(:retrieve_topic, has_entries(embed_url: embed_url))`, or change the controller to call `TopicRetriever` directly.
```suggestion
Jobs.expects(:enqueue).with(:retrieve_topic, has_entries(embed_url: embed_url))
get :best, embed_url: embed_url
```

:yellow_circle: [correctness] `contents << "..."` mutates caller's string in `app/models/topic_embed.rb`:10 (confidence: 75)
`TopicEmbed.import(user, url, title, contents)` appends the "Imported from" footer via `contents << ...`. Because `poll_feed` passes `CGI.unescapeHTML(i.content.scrub)`, this is currently harmless, but `import_remote` passes `doc.content` from `ruby-readability`, whose return value may be frozen (Ruby 3+) or shared; mutation can raise `FrozenError` at runtime. Prefer non-mutating concatenation.
```suggestion
body = contents.dup
body << "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: "<a href='#{url}'>#{url}</a>")}</small>\n"
content_sha1 = Digest::SHA1.hexdigest(body)
# ... use body in PostCreator/PostRevisor
```

:yellow_circle: [security] Admin-configured `feed_polling_url` is not scheme/host validated in `app/jobs/scheduled/poll_feed.rb`:28 (confidence: 70)
`SiteSetting.feed_polling_url` flows directly into `open()`. If an admin (or compromised admin account) supplies `file:///etc/passwd` or an internal URL, Sidekiq reads it and imports its contents as post bodies. Validate scheme ∈ {http, https} and block private/loopback hosts before fetching. Same mitigation applies as for the SSRF finding above.

:yellow_circle: [consistency] `require_dependency 'nokogiri'` is a Rails autoloader call for a gem in `app/models/topic_embed.rb`:1 (confidence: 70)
`require_dependency` is meant for application constants that should be reloaded in development; Nokogiri is a gem. Use `require 'nokogiri'` (or omit — Discourse already loads Nokogiri at boot). Using `require_dependency` here is at best a no-op, at worst confusing to maintainers.
```suggestion
require 'nokogiri'
```

:yellow_circle: [correctness] `feed_key` is dead code and there is no dedup between hourly polls in `app/jobs/scheduled/poll_feed.rb`:17 (confidence: 65)
`feed_key` computes a Redis key tag intended to short-circuit reprocessing of unchanged feeds, but nothing reads it. The hourly job re-fetches the full feed every time and calls `TopicEmbed.import` for every item. Deduplication only happens downstream via `content_sha1` per-URL, which means unchanged posts still incur an import/SHA1/DB round-trip per item per hour. Either wire the `If-Modified-Since` / Redis cache check, or remove `feed_key` so a reader doesn't assume it does something.

## Nitpicks

:white_circle: [consistency] Missing trailing newline at EOF in several new files (confidence: 95)
`app/assets/stylesheets/embed.css.scss`, `app/views/embed/loading.html.erb`, `app/views/layouts/embed.html.erb`, `lib/topic_retriever.rb`, and `db/migrate/20131210181901_migrate_word_counts.rb` all show `\ No newline at end of file`. House style throughout Discourse is POSIX-style trailing newlines.

:white_circle: [consistency] `force: true` on `create_table` in two migrations (confidence: 80)
`db/migrate/20131217174004_create_topic_embeds.rb`:3 and `db/migrate/20131223171005_create_top_topics.rb`:3 pass `force: true`, which issues `DROP TABLE IF EXISTS ...` before create. This is fine on first run but destroys data on re-runs and diverges from the rest of the Discourse migration corpus. Drop `force: true` unless there is a concrete prior-schema cleanup the migration is meant to handle.

## Risk Metadata
Risk Score: 78/100 (HIGH) | Blast Radius: new embed/RSS subsystem + mutation to core `post.rb` cook path + schema changes on `posts` and new `topic_embeds` table | Sensitive Paths: `app/controllers/embed_controller.rb`, `app/models/topic_embed.rb`, `app/views/layouts/embed.html.erb`, `db/migrate/*`
AI-Authored Likelihood: LOW

Recommendation: request-changes — the three security criticals (postMessage target-origin, referer-only auth, SSRF via open-uri) and the apparent `<%- end if %>` syntax error must be addressed before merging; the raw-HTML XSS surface needs at minimum a sanitizer pass even if operators are expected to control their own feeds.
