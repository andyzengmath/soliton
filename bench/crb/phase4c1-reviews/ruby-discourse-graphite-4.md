## Summary
28 files changed, ~612 lines added, ~13 lines deleted. 23 findings (16 critical, 7 improvements, 0 nitpicks).
New embed/RSS feature ships with multiple critical SSRF and XSS vectors, a broken ERb template (`<%- end if %>`) that prevents rendering, a destructive `force: true` added to an already-shipped migration, and a Redis throttle race that can permanently block URLs.

## Critical

:red_circle: [correctness] Invalid ERb syntax `<%- end if %>` causes template parse failure in app/views/embed/best.html.erb:414 (confidence: 99)
Line 414 uses `<%- end if %>` which is not valid Ruby. `end` closes a block; an `if` keyword appearing after `end` parses as the start of a new, unterminated if-statement, producing a SyntaxError (`unexpected keyword_if`). Rails raises ActionView::Template::Error at render time — the entire embed feature is non-functional as written.
```suggestion
  <% end %>
```

:red_circle: [correctness] Migration default:1 maps to :raw_html — all pre-existing posts silently switch to raw HTML rendering in db/migrate/20131219203905_add_cook_method_to_posts.rb:4 (confidence: 95)
`Post.cook_methods` is `Enum.new(:regular, :raw_html)`. In Discourse's zero-indexed Enum, this gives `:regular = 0` and `:raw_html = 1`. The migration sets `default: 1, null: false`, so every pre-existing post retroactively receives `cook_method = 1 = :raw_html`. Post#cook now returns the raw unprocessed markdown source for all existing posts, so callers across topic views, serializers, search indexers, and UI surfaces silently receive raw markdown instead of sanitized HTML — a catastrophic, silent, database-wide regression.
```suggestion
add_column :posts, :cook_method, :integer, default: 0, null: false
```

:red_circle: [security] XSS via unescaped request.referer injected into JavaScript string and insecure postMessage targetOrigin in app/views/layouts/embed.html.erb:7 (confidence: 98)
The layout injects `<%= request.referer %>` directly inside a single-quoted JavaScript string literal: `parent.postMessage({...}, '<%= request.referer %>')`. ERb's `<%= %>` HTML-escapes but does not JavaScript-escape — a Referer header containing a single quote, backslash, newline, or `</script>` breaks out of the JS string context and enables arbitrary script injection. Additionally, using the attacker-controlled Referer as the postMessage targetOrigin defeats the security model of postMessage; the iframe broadcasts payloads to whatever site happens to frame it.
```suggestion
parent.postMessage({type: 'discourse-resize', height: document.body.offsetHeight}, <%= request.referer.to_json.html_safe %>);
```
[References: https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [security] SSRF via open-uri on SiteSetting.feed_polling_url with no scheme or host allowlist in app/jobs/scheduled/poll_feed.rb:29 (confidence: 95)
`SimpleRSS.parse open(SiteSetting.feed_polling_url)` uses Ruby's Kernel#open via open-uri. With no scheme allowlist, no DNS-rebinding protection, and no SSRF guard, an admin-configured (or compromised) URL set to `file:///etc/passwd`, `http://169.254.169.254/latest/meta-data/`, or an internal service is fetched and ingested verbatim into posts, enabling exfiltration via stored content.
```suggestion
uri = URI.parse(SiteSetting.feed_polling_url)
raise 'bad scheme' unless %w[http https].include?(uri.scheme)
ip = Resolv.getaddress(uri.host)
raise 'blocked host' if IPAddr.new(ip).private? || IPAddr.new(ip).loopback? || IPAddr.new(ip).link_local?
body = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https', open_timeout: 5, read_timeout: 10) { |http| http.get(uri.request_uri).body }
rss = SimpleRSS.parse(body)
```
[References: https://cwe.mitre.org/data/definitions/918.html]

:red_circle: [security] SSRF in TopicEmbed.import_remote via open(url) on attacker-influenced URL in app/models/topic_embed.rb:44 (confidence: 95)
`import_remote` calls `open(url).read` where `url` originates from `params[:embed_url]`. `TopicRetriever#invalid_host?` compares only the host string, not the scheme — `file://` URLs with no host bypass it — and open-uri follows HTTP redirects by default to arbitrary destinations including 127.0.0.1 and cloud metadata endpoints. DNS rebinding between the check and the fetch can also redirect to internal addresses.
```suggestion
body = SafeHttp.fetch(url, allowed_hosts: [SiteSetting.embeddable_host])
doc = Readability::Document.new(body, tags: %w[div p code pre h1 h2 h3 b em i strong a img], attributes: %w[href src])
```
[References: https://cwe.mitre.org/data/definitions/918.html]

:red_circle: [security] Stored XSS via `raw post.cooked` for network-fetched HTML (cook_method raw_html) in app/views/embed/best.html.erb:19 (confidence: 95)
The view renders `<%= raw post.cooked %>`. Embed posts have `cook_method = raw_html`, so `Post#cook` returns the raw field verbatim. That raw content is fetched over the network (RSS items or Readability output) and processed only by `absolutize_urls`, which runs a Nokogiri fragment rewrite with no sanitization. Readability's tag/attribute allowlist permits `href`/`src` with `javascript:` URIs and `onerror` handlers; RSS polling bypasses Readability entirely. Rendered inside an iframe served with `X-Frame-Options: ALLOWALL`, attacker HTML/JS executes in the Discourse origin.
```suggestion
<div class='cooked'><%= sanitize post.cooked, tags: %w[p b i em strong a img h1 h2 h3 code pre div], attributes: %w[href src] %></div>
```
[References: https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [security] Referer-based origin check is spoofable and X-Frame-Options ALLOWALL disables clickjacking protection in app/controllers/embed_controller.rb:22 (confidence: 90)
`ensure_embeddable` authenticates the embedding site by comparing `URI(request.referer).host` to `SiteSetting.embeddable_host`. The Referer header is client-controlled and is not a security boundary (non-browser clients forge it trivially; browser privacy settings strip it). The controller then sets `X-Frame-Options: ALLOWALL`, a non-standard value most browsers treat as no restriction — any site can frame the response. Combined with the stored-XSS vector this is a clickjacking-plus-XSS delivery pipeline.
```suggestion
def ensure_embeddable
  host = SiteSetting.embeddable_host
  raise Discourse::InvalidAccess.new('embeddable host not set') if host.blank?
  response.headers['Content-Security-Policy'] = "frame-ancestors https://#{host}"
  response.headers.delete('X-Frame-Options')
end
```
[References: https://cwe.mitre.org/data/definitions/1021.html, https://cwe.mitre.org/data/definitions/346.html]

:red_circle: [correctness] Non-atomic SETNX + EXPIRE leaves throttle key without TTL on process crash in lib/topic_retriever.rb:17 (confidence: 97)
`retrieved_recently?` calls `$redis.setnx` and then `$redis.expire` as two separate commands. A crash, SIGKILL, or Redis connection loss between them leaves the key written with no expiry. Every subsequent call then sees `setnx` return false, `retrieved_recently?` returns true forever, and the URL is permanently throttled with no operator recovery short of manual Redis key deletion.
```suggestion
def retrieved_recently?
  return false if @opts[:no_throttle]
  retrieved_key = "retrieved:#{@embed_url}"
  return false if $redis.set(retrieved_key, "1", nx: true, ex: 60)
  true
end
```

:red_circle: [correctness] Modifying an already-executed migration to add `force: true` silently DROPS top_topics on re-run in db/migrate/20131223171005_create_top_topics.rb:3 (confidence: 92)
This migration's timestamp predates this PR — it has already been executed in production-equivalent environments. Adding `force: true` means that on any re-run (CI `db:schema:load`, `db:migrate:reset`, fresh developer DB, or any environment that replays migrations), Rails executes `DROP TABLE IF EXISTS top_topics` before recreating it, destroying existing data without warning. Already-shipped migration files must never be modified.
```suggestion
    create_table :top_topics do |t|
```

:red_circle: [correctness] `force: true` on new topic_embeds migration silently drops any pre-existing table in db/migrate/20131217174004_create_topic_embeds.rb:3 (confidence: 88)
If the `topic_embeds` table already exists (from a failed previous migration run, manual setup, or a prior iteration of this branch), `force: true` drops it without warning, destroying any `TopicEmbed` records that link posts to their source URLs.
```suggestion
    create_table :topic_embeds do |t|
```

:red_circle: [cross-file-impact] Disqus import now calls TopicEmbed.import_remote making live HTTP requests per thread with no rate limiting in lib/tasks/disqus.thor:140 (confidence: 88)
The previous implementation created a permalink stub locally via `PostCreator`. The new implementation calls `TopicEmbed.import_remote(user, t[:link], ...)` which invokes `open(url).read` for every Disqus thread. On a large archive this fires hundreds or thousands of outbound HTTP requests in a tight loop with no rate limiting, no error handling for unreachable URLs, and no timeout. Stored content silently changes from a permalink stub to a full parsed article body — a breaking semantic change with no documentation.
```suggestion
creator = PostCreator.new(user, title: t[:title], raw: "\\[[Permalink](#{t[:link]})\\]", created_at: Date.parse(t[:created_at]))
post = creator.create
```

:red_circle: [cross-file-impact] --category/-c option silently removed; existing callers receive no error in lib/tasks/disqus.thor:113 (confidence: 88)
The `method_option :category, aliases: '-c'` declaration was deleted. Existing scripts, cron jobs, or documented workflows invoking `thor disqus:import -c CategoryName` will have `-c` silently discarded by Thor (unknown options are ignored by default). Posts are imported without category assignment and no error is surfaced.
```suggestion
method_option :category, aliases: '-c', desc: "The category to post in"
```

:red_circle: [testing] embed_controller_spec mocks TopicRetriever.new but the controller enqueues a Sidekiq job — mock never fires in app/controllers/embed_controller.rb:184 (confidence: 95)
The spec stubs `TopicRetriever.expects(:new).returns(retriever); retriever.expects(:retrieve)`, but the controller calls `Jobs.enqueue(:retrieve_topic, ...)` — the job, not the controller, later instantiates `TopicRetriever`. Unless Sidekiq is in inline mode (it is not in this spec), the mock expectations are never satisfied. The actual behavior (`Jobs.enqueue` called with the correct arguments) is entirely untested.
```suggestion
it "enqueues a retrieve_topic job when no previous embed is found" do
  TopicEmbed.expects(:topic_id_for_embed).with(embed_url).returns(nil)
  Jobs.expects(:enqueue).with(:retrieve_topic, has_entries(embed_url: embed_url))
  get :best, embed_url: embed_url
end
```

:red_circle: [testing] No unit test for Post#cook bypass when cook_method is raw_html in app/models/post.rb:127 (confidence: 92)
The new conditional branch (`return raw if cook_method == Post.cook_methods[:raw_html]`) in a core model method is untested directly. `topic_embed_spec` observes a side effect but goes through PostCreator, PostRevisor, and the database — a regression that flipped the guard or changed the enum value would not be caught.
```suggestion
describe '#cook' do
  it "returns raw without rendering when cook_method is raw_html" do
    post = Fabricate.build(:post, raw: "<b>hello</b>", cook_method: Post.cook_methods[:raw_html])
    post.cook.should == "<b>hello</b>"
  end
end
```

:red_circle: [testing] RetrieveTopic job has zero test coverage in app/jobs/regular/retrieve_topic.rb:222 (confidence: 90)
`Jobs::RetrieveTopic` is a new file with branching logic — raises on missing `embed_url`, looks up an optional user, passes `no_throttle` based on `staff?` status — and none of these paths are tested.
```suggestion
describe Jobs::RetrieveTopic do
  it "raises when embed_url is missing" do
    expect { described_class.new.execute({}) }.to raise_error(Discourse::InvalidParameters)
  end

  it "calls TopicRetriever with no_throttle true for staff" do
    staff = Fabricate(:admin)
    TopicRetriever.expects(:new).with('http://x', no_throttle: true).returns(stub(retrieve: nil))
    described_class.new.execute(embed_url: 'http://x', user_id: staff.id)
  end
end
```

:red_circle: [testing] import_remote has zero test coverage in app/models/topic_embed.rb:364 (confidence: 88)
`TopicEmbed.import_remote` is new and non-trivial: it calls `open()` (the primary SSRF surface), invokes `Readability::Document` for extraction, and delegates to `import`. It is called from both `TopicRetriever#fetch_http` and `disqus.thor`. No spec exercises any of these paths.
```suggestion
context '.import_remote' do
  before { TopicEmbed.stubs(:open).returns(stub(read: '<html><body><p>x</p></body></html>')) }

  it "delegates to import with extracted title when no override is given" do
    TopicEmbed.expects(:import).with(user, url, anything, anything).returns(stub)
    TopicEmbed.import_remote(user, url)
  end
end
```

## Improvements

:yellow_circle: [correctness] `import` mutates caller's `contents` string via `<<` — crashes on frozen strings in app/models/topic_embed.rb:333 (confidence: 88)
`TopicEmbed.import` does `contents << "\n<hr>\n..."`, an in-place mutation. Under `# frozen_string_literal: true` or Ruby 3.x defaults, passing a string literal raises `FrozenError`. The existing topic_embed_spec passes string literals as `contents`, so this path is fragile under modern frozen-string semantics.
```suggestion
contents = contents + "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: "<a href='#{url}'>#{url}</a>")}</small>\n"
```

:yellow_circle: [correctness] discourse_expires_in called after `render 'loading'` — caches the loading spinner for 1 minute in app/controllers/embed_controller.rb:12 (confidence: 85)
In the else branch `render 'loading'` fires, then execution falls through to `discourse_expires_in 1.minute`. The intent is to cache the successfully-found-topic response, but the current structure caches the loading placeholder — clients reuse the stale loading page for 60 seconds even after the topic becomes available.
```suggestion
if topic_id
  @topic_view = TopicView.new(topic_id, current_user, {best: 5})
  discourse_expires_in 1.minute
else
  Jobs.enqueue(:retrieve_topic, user_id: current_user.try(:id), embed_url: embed_url)
  render 'loading'
end
```

:yellow_circle: [correctness] require_dependency used for Nokogiri gem — semantically incorrect in app/models/topic_embed.rb:321 (confidence: 90)
`require_dependency` is Rails' autoloader hook for autoloadable constants in the application's load paths. Nokogiri is a Bundler-managed gem and must be loaded with `require 'nokogiri'` (or simply omitted, since it is transitively required elsewhere).
```suggestion
require 'nokogiri'
```

:yellow_circle: [correctness] Unused `require_dependency 'email/sender'` — leftover import in app/jobs/regular/retrieve_topic.rb:217 (confidence: 95)
The file begins with `require_dependency 'email/sender'` but the body never references `Email::Sender`. This is a dead import that adds load cost and creates a misleading dependency signal.
```suggestion
require_dependency 'topic_retriever'
```

:yellow_circle: [correctness] Deprecated Rails 4 API: skip_before_filter / before_filter in app/controllers/embed_controller.rb:178 (confidence: 85)
Rails 4.0 renamed `before_filter` and `skip_before_filter` to `before_action` and `skip_before_action`. The old names were deprecated in Rails 4.0 and removed in Rails 5.1 — using them emits deprecation warnings and creates a forward-compatibility hazard.
```suggestion
skip_before_action :check_xhr
skip_before_action :preload_json
before_action :ensure_embeddable
```

:yellow_circle: [testing] Redis throttle logic in retrieved_recently? is bypassed by mocking — actual logic untested in lib/topic_retriever.rb:12 (confidence: 92)
Tests stub `retrieved_recently?` directly on the instance, so the `setnx`/`expire` calls, the `no_throttle` bypass, and the Redis key format are never executed. If the Redis code broke — for example the SETNX+EXPIRE race flagged above — the suite would still pass.
```suggestion
context "throttle behavior" do
  it "is not throttled when setnx succeeds" do
    $redis.stubs(:setnx).returns(true)
    $redis.expects(:expire).once
    topic_retriever.expects(:perform_retrieve).once
    topic_retriever.retrieve
  end

  it "skips the redis call when no_throttle is true" do
    r = TopicRetriever.new(embed_url, no_throttle: true)
    $redis.expects(:setnx).never
    r.expects(:perform_retrieve).once
    r.retrieve
  end
end
```

:yellow_circle: [testing] `import` does not test content_sha1-unchanged path — re-import idempotency unverified in app/models/topic_embed.rb:349 (confidence: 88)
The spec covers the update path (changed content) but never the skip-revision path when `content_sha1` matches. `PollFeed` runs hourly — a bug here spams `PostRevisor` revisions on every poll cycle for every tracked URL.
```suggestion
it "does not revise when content is unchanged" do
  first = TopicEmbed.import(user, url, title, contents)
  PostRevisor.any_instance.expects(:revise!).never
  second = TopicEmbed.import(user, url, title, contents)
  second.id.should == first.id
end
```

## Risk Metadata
Risk Score: 73/100 (HIGH) | Blast Radius: post.rb / post_creator.rb / post_revisor.rb touch widely-imported core models (estimated 45+ downstream files) | Sensitive Paths: 4 migration files (1 modifies an already-run migration; 2 use `force: true`)
AI-Authored Likelihood: HIGH

(7 additional findings below confidence threshold: untrusted RSS content stored as raw HTML, unauthenticated embed trigger / SSRF amplification, synchronous PollFeed invocation from Sidekiq worker, nil guard on `i.content` in poll_feed, cook_method not validated against enum in post_creator, poll_feed body entirely untested, absolutize_urls branch coverage gaps. Consistency nits — trailing blank lines, missing EOF newlines, whitespace-only churn in migrate_word_counts — omitted per Phase 3.5.)
