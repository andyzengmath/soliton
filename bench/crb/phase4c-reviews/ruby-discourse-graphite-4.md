## Summary
28 files changed, 653 lines added, 13 lines deleted. 16 findings (9 critical, 7 improvements, 0 nitpicks).
Destructive `force: true` on an already-executed migration will DROP the production `top_topics` table, plus stored XSS, SSRF, and a template SyntaxError.

## Critical
:red_circle: [correctness] force: true on already-executed migration will DROP production top_topics table in db/migrate/20131223171005_create_top_topics.rb:3 (confidence: 99)
The migration 20131223171005_create_top_topics.rb already exists in the repository and has been run in production. Adding force: true to create_table instructs ActiveRecord to execute DROP TABLE IF EXISTS top_topics before recreating it. Any future invocation (db:migrate:redo, db:schema:load, test suite db:reset, or rollback + re-apply) will silently destroy all existing top_topics rows. Changing an already-deployed migration is the core defect — schema changes should always use a new migration file.
```suggestion
    create_table :top_topics do |t|
```
[References: https://guides.rubyonrails.org/active_record_migrations.html]

:red_circle: [security] Stored XSS via raw rendering of RSS-imported post content in app/views/embed/best.html.erb:17 (confidence: 95)
`<div class='cooked'><%= raw post.cooked %></div>` renders `post.cooked` without escaping. For posts imported via the embed/RSS path, `Post#cook` returns `self.raw` unchanged when `cook_method == Post.cook_methods[:raw_html]`. The raw HTML comes directly from the external RSS feed content through `TopicEmbed.import`, which only runs `Nokogiri::HTML.fragment` to absolutize URLs — it does NOT sanitize script tags, event handlers, or other dangerous HTML. An attacker who controls the RSS feed (or the remote URL fetched via `import_remote`) can inject `<script>`, `<iframe>`, or event-handler payloads that execute in the Discourse origin whenever the embed iframe is loaded. Additionally, line 6 contains the invalid ERB syntax `<%- end if %>` (Ruby does not permit a bare `end if`), which raises SyntaxError at template compile time causing every request to embed/best to return 500 Internal Server Error before the XSS vector is reachable; once the syntax is fixed the XSS is immediately exploitable.
```suggestion
<%- if @topic_view.posts.present? %>
  <%= link_to(I18n.t('embed.title'), @topic_view.topic.url, class: 'button', target: '_blank') %>
<%- else %>
  <%= link_to(I18n.t('embed.start_discussion'), @topic_view.topic.url, class: 'button', target: '_blank') %>
<%- end %>
...
<div class='cooked'><%= sanitize(post.cooked, tags: %w[p a img h1 h2 h3 b em i strong code pre], attributes: %w[href src]) %></div>
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [security] SSRF and local file read via Kernel#open in import_remote in app/models/topic_embed.rb:45 (confidence: 95)
`Readability::Document.new(open(url).read, ...)` uses Ruby's `Kernel#open` which interprets the argument based on its prefix. While `import_remote` is called from `TopicRetriever#fetch_http` after a host check, the URL is ultimately user-controlled via the `embed_url` param. `open-uri` accepts non-HTTP schemes and follows HTTP redirects by default to arbitrary hosts including internal IPs (e.g., 169.254.169.254 cloud metadata). There is no timeout or response size limit, enabling DoS. Combined with downstream raw-HTML rendering, the impact is high.
```suggestion
require 'net/http'
require 'resolv'
def self.safe_get(url)
  uri = URI(url)
  raise 'invalid scheme' unless %w[http https].include?(uri.scheme)
  ip = IPAddr.new(Resolv.getaddress(uri.host))
  raise 'blocked' if ip.private? || ip.loopback? || ip.link_local?
  Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https',
                  open_timeout: 5, read_timeout: 10) do |http|
    resp = http.get(uri.request_uri)
    raise 'too large' if resp.body.bytesize > 5_000_000
    resp.body
  end
end
```
[References: https://owasp.org/Top10/A10_2021-Server-Side_Request_Forgery_%28SSRF%29/, https://cwe.mitre.org/data/definitions/918.html]

:red_circle: [security] XSS via unescaped Referer header interpolated into JavaScript string literal in app/views/layouts/embed.html.erb:9 (confidence: 92)
`parent.postMessage({...}, '<%= request.referer %>');` interpolates the Referer HTTP header directly into a single-quoted JavaScript string literal inside a `<script>` block. ERB's default HTML escaping does not escape single quotes or backslashes, and HTML entity encoding is not applied to `<script>` content by browsers. An attacker can craft a Referer value (e.g., `http://embeddable.host/path?x=');evilcode;//`) that passes the host check in `ensure_embeddable` but breaks out of the JS string and executes arbitrary code in the Discourse origin.
```suggestion
<% origin = (begin; u = URI(request.referer||''); "#{u.scheme}://#{u.host}#{u.port ? ":#{u.port}" : ''}"; rescue; ''; end) %>
parent.postMessage({type: 'discourse-resize', height: document.body.offsetHeight}, <%= origin.to_json.html_safe %>);
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/79.html, https://cwe.mitre.org/data/definitions/116.html]

:red_circle: [security] SSRF via Kernel#open on feed_polling_url, plus nil crash on missing content field in app/jobs/scheduled/poll_feed.rb:28 (confidence: 90)
Two defects converge here. (1) `SimpleRSS.parse open(SiteSetting.feed_polling_url)` uses `Kernel#open` with no scheme validation, size cap, or timeout. `open-uri` follows redirects to arbitrary hosts including internal IPs and cloud metadata endpoints. While `feed_polling_url` is admin-set, a compromised admin account pivots to internal services or local files. (2) On line 33, `i.content.scrub` crashes with `NoMethodError` when a feed item has no `<content>` element — many valid RSS 2.0 feeds use `<description>` instead and `simple-rss` returns nil for missing fields. The crash aborts processing of all remaining items, and with `sidekiq_options retry: false` no retry occurs.
```suggestion
uri = URI(SiteSetting.feed_polling_url)
raise unless %w[http https].include?(uri.scheme)
body = Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https',
                       open_timeout: 5, read_timeout: 10) { |h| h.get(uri.request_uri).body }
raise 'too large' if body.bytesize > 10_000_000
rss = SimpleRSS.parse(body)

rss.items.each do |i|
  url = i.link
  url = i.id if url.blank? || url !~ /^https?\:\/\//
  raw_content = i.content || i.description || ""
  content = CGI.unescapeHTML(raw_content.scrub)
  TopicEmbed.import(user, url, i.title, content)
end
```
[References: https://owasp.org/Top10/A10_2021-Server-Side_Request_Forgery_%28SSRF%29/, https://cwe.mitre.org/data/definitions/918.html, https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [correctness] Non-atomic setnx + expire creates permanent throttle key on crash in lib/topic_retriever.rb:22 (confidence: 95)
`retrieved_recently?` performs two separate Redis commands: `$redis.setnx(retrieved_key, "1")` followed by `$redis.expire(retrieved_key, 60)`. These are not atomic. If the Ruby process is killed, crashes, or the connection drops between these two commands, the key is written by `setnx` but `expire` is never called. The key persists indefinitely with no TTL, causing `retrieved_recently?` to always return true for that URL and permanently blocking all future retrieval until the key is manually cleared from Redis.
```suggestion
def retrieved_recently?
  return false if @opts[:no_throttle]
  retrieved_key = "retrieved:#{@embed_url}"
  result = $redis.set(retrieved_key, "1", nx: true, ex: 60)
  !result
end
```
[References: https://redis.io/docs/manual/patterns/distributed-locks/]

:red_circle: [testing] Post#cook raw_html branch has no unit test in app/models/post.rb:131 (confidence: 97)
`cook` gained a new early-return branch: when `cook_method == Post.cook_methods[:raw_html]` it returns `raw` unprocessed, bypassing the entire rendering pipeline including `Plugin::Filter`. `topic_embed_spec` exercises it indirectly through full integration, but there is no unit test verifying `cook` short-circuits for `raw_html` vs. invokes the plugin filter for regular posts. A regression (e.g., Enum value shifting) would go undetected.
```suggestion
describe Post, '#cook' do
  it 'returns raw without invoking the plugin filter for raw_html posts' do
    post = Fabricate.build(:post, raw: '<b>hello</b>', cook_method: Post.cook_methods[:raw_html])
    Plugin::Filter.expects(:apply).never
    expect(post.cook).to eq('<b>hello</b>')
  end

  it 'passes through the plugin filter for regular posts' do
    post = Fabricate.build(:post, raw: 'hello')
    Plugin::Filter.expects(:apply).with(:after_post_cook, post, anything).returns('cooked')
    expect(post.cook).to eq('cooked')
  end
end
```

:red_circle: [testing] RetrieveTopic job has zero test coverage in app/jobs/regular/retrieve_topic.rb:1 (confidence: 95)
`RetrieveTopic` is entirely new production code with no spec file. It handles the `embed_url` validation guard, user lookup by `user_id`, and `no_throttle` based on staff status. These are distinct behaviors that can fail independently — a missing or unprivileged user silently skips throttle bypass, and a blank `embed_url` raises `InvalidParameters` with no test confirming the error path.
```suggestion
describe Jobs::RetrieveTopic do
  it 'raises InvalidParameters without embed_url' do
    expect { described_class.new.execute({}) }.to raise_error(Discourse::InvalidParameters)
  end

  it 'passes no_throttle: true for staff users' do
    staff = Fabricate(:admin)
    retriever = mock
    TopicRetriever.expects(:new).with('http://x.com', no_throttle: true).returns(retriever)
    retriever.expects(:retrieve)
    described_class.new.execute(embed_url: 'http://x.com', user_id: staff.id)
  end

  it 'passes no_throttle: false for regular users' do
    user = Fabricate(:user)
    retriever = mock
    TopicRetriever.expects(:new).with('http://x.com', no_throttle: false).returns(retriever)
    retriever.expects(:retrieve)
    described_class.new.execute(embed_url: 'http://x.com', user_id: user.id)
  end
end
```

:red_circle: [testing] PostRevisor skip_validations path has no test coverage in lib/post_revisor.rb:85 (confidence: 92)
PR changes `@post.save` to `@post.save(validate: !@opts[:skip_validations])`. This new code path is relied on by `TopicEmbed.import` when updating an embed. There is no test passing `skip_validations: true` to `PostRevisor#revise!` that verifies `save` is called without validation. `topic_embed_spec`'s update test exercises the update path but does not assert that an otherwise-invalid post is still saved.
```suggestion
it 'saves without validation when skip_validations: true' do
  post = Fabricate(:post)
  revisor = PostRevisor.new(post)
  result = revisor.revise!(post.user, 'x', skip_validations: true, bypass_rate_limiter: true)
  expect(result).to be_truthy
  expect(post.reload.raw).to eq('x')
end
```

## Improvements
:yellow_circle: [correctness] open(SiteSetting.feed_polling_url) has no rescue for network/HTTP errors in app/jobs/scheduled/poll_feed.rb:28 (confidence: 92)
`open(url)` from `open-uri` raises exceptions for network failures (`SocketError`, `Errno::ECONNREFUSED`), HTTP error responses (`OpenURI::HTTPError`), and redirects-to-non-http. None are rescued in `poll_feed`. An unreachable or error-returning feed URL causes the scheduled job to raise. With `sidekiq_options retry: false` the feed is silently skipped until the next hourly invocation with no log entry.
```suggestion
begin
  rss = SimpleRSS.parse open(SiteSetting.feed_polling_url)
rescue SocketError, OpenURI::HTTPError, RuntimeError => e
  Rails.logger.error("PollFeed failed to fetch #{SiteSetting.feed_polling_url}: #{e.message}")
  return
end
```

:yellow_circle: [testing] poll_feed method itself is never exercised — RSS parsing and import go untested in spec/jobs/poll_feed_spec.rb:1 (confidence: 90)
All four tests only stub `execute` guard conditions and mock `poll_feed` with `expects(:poll_feed).never/.once`. The actual `poll_feed` method — which calls `open(SiteSetting.feed_polling_url)`, parses RSS, iterates items, extracts link/id/content, and delegates to `TopicEmbed.import` — is never invoked. Edge cases like blank link falling back to `item.id`, non-http URLs being skipped, nil content, and malformed RSS are unverified.
```suggestion
context 'poll_feed' do
  let(:user) { Fabricate(:user, username: 'eviltrout') }
  let(:rss) { StringIO.new(File.read(Rails.root.join('spec/fixtures/sample.rss'))) }

  before do
    SiteSetting.stubs(:feed_polling_enabled?).returns(true)
    SiteSetting.stubs(:feed_polling_url).returns('http://eviltrout.com/feed')
    SiteSetting.stubs(:embed_by_username).returns('eviltrout')
    poller.stubs(:open).returns(rss)
  end

  it 'imports each feed item via TopicEmbed.import' do
    TopicEmbed.expects(:import).at_least_once
    poller.poll_feed
  end

  it 'does not crash on items with nil content' do
    # fixture with <description> only, no <content:encoded>
    TopicEmbed.expects(:import)
    expect { poller.poll_feed }.not_to raise_error
  end
end
```

:yellow_circle: [correctness] TopicEmbed.create! inside import lacks duplicate-key rescue — concurrent imports orphan posts in app/models/topic_embed.rb:19 (confidence: 88)
`import` reads `TopicEmbed.where(embed_url: url).first`; if nil it proceeds to `PostCreator` and `TopicEmbed.create!` inside a transaction. A unique index on `embed_url` exists. Two concurrent processes (e.g., `PollFeed` and `RetrieveTopic` for the same URL) both observing `embed.blank?` will both attempt `create!`; the second raises `ActiveRecord::RecordNotUnique` (unrescued). The transaction unwinds, the newly created post is orphaned (topic exists but no `TopicEmbed` row), and subsequent runs still see `blank?`, creating an infinite loop of orphaned posts.
```suggestion
begin
  TopicEmbed.create!(topic_id: post.topic_id, embed_url: url,
                     content_sha1: content_sha1, post_id: post.id)
rescue ActiveRecord::RecordNotUnique
  return TopicEmbed.where(embed_url: url).first&.post
end
```

:yellow_circle: [testing] SSRF-relevant URL schemes and perform_retrieve path untested in spec/components/topic_retriever_spec.rb:1 (confidence: 88)
`invalid_host?` is tested for one mismatch and one invalid string, but not for non-http schemes (`file://`, `ftp://`, `javascript:`) that could bypass the embeddable host check. `perform_retrieve` is never exercised, so `fetch_http` and the RSS-first logic have zero coverage.
```suggestion
it 'does not retrieve file:// URLs' do
  r = TopicRetriever.new('file:///etc/passwd')
  SiteSetting.stubs(:embeddable_host).returns('eviltrout.com')
  r.expects(:perform_retrieve).never
  r.retrieve
end

it 'does not retrieve javascript: URLs' do
  r = TopicRetriever.new('javascript:alert(1)')
  r.expects(:perform_retrieve).never
  r.retrieve
end

context 'perform_retrieve' do
  before { SiteSetting.stubs(:embeddable_host).returns('eviltrout.com') }
  it 'skips fetch if embed already exists' do
    TopicEmbed.stubs(:where).returns(stub(exists?: true))
    topic_retriever.expects(:fetch_http).never
    topic_retriever.send(:perform_retrieve)
  end
end
```

:yellow_circle: [security] Weak postMessage origin validation using indexOf allows spoofed origins in app/assets/javascripts/embed.js:15 (confidence: 85)
`if (discourseUrl.indexOf(e.origin) === -1) { return; }` validates that the message origin is a substring of `discourseUrl`. Any origin whose string happens to appear in `discourseUrl` passes the check, including partial matches. Current impact is limited (only iframe height is set) but any future handler expansion is immediately vulnerable.
```suggestion
var expectedOrigin = (function() {
  var a = document.createElement('a'); a.href = discourseUrl;
  return a.protocol + '//' + a.host;
})();
function postMessageReceived(e) {
  if (!e || e.origin !== expectedOrigin) return;
  if (e.data && e.data.type === 'discourse-resize' && e.data.height) {
    iframe.height = e.data.height + "px";
  }
}
```
[References: https://cwe.mitre.org/data/definitions/346.html, https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage#security_concerns]

:yellow_circle: [correctness] import_remote calls open(url) with no error handling for network failures in app/models/topic_embed.rb:45 (confidence: 85)
`open(url).read` via `open-uri` can raise `OpenURI::HTTPError`, `SocketError`, `Errno::ECONNREFUSED`, or `URI::InvalidURIError`. None are rescued. From `fetch_http`, the exception propagates to `RetrieveTopic`. For URLs that consistently fail, every iframe load requeues a job that always raises, adding queue pressure.
```suggestion
begin
  body = open(url).read
rescue OpenURI::HTTPError, SocketError, RuntimeError => e
  Rails.logger.error("TopicEmbed.import_remote failed to fetch #{url}: #{e.message}")
  return nil
end
doc = Readability::Document.new(body,
                                tags: %w[div p code pre h1 h2 h3 b em i strong a img],
                                attributes: %w[href src])
TopicEmbed.import(user, url, opts[:title] || doc.title, doc.content)
```

:yellow_circle: [testing] TopicEmbed.import missing negative and edge-case tests in spec/models/topic_embed_spec.rb:25 (confidence: 85)
Covers happy path and one update. Does not cover: (1) non-http scheme URLs (`ftp://`, `javascript:`) which should be rejected by the `^https?://` guard, (2) XSS in `contents` — since `raw_html` skips sanitization, `<script>` content would be stored as-is, (3) `absolutize_urls` with non-80/443 ports and protocol-relative URLs.
```suggestion
it 'returns nil for ftp:// URLs' do
  expect(TopicEmbed.import(user, 'ftp://example.com/post', title, contents)).to be_nil
end

it 'preserves port in absolutized URLs for non-standard ports' do
  result = TopicEmbed.absolutize_urls('http://example.com:8080/post', '<a href="/page">link</a>')
  expect(result).to include('http://example.com:8080/page')
end

it 'does not mangle protocol-relative URLs' do
  result = TopicEmbed.absolutize_urls('http://example.com/post', '<a href="//other.com/page">link</a>')
  expect(result).to include('//other.com/page')
end
```

## Risk Metadata
Risk Score: 43/100 (MEDIUM) | Blast Radius: 0 (shim repo, no grep-visible importers) | Sensitive Paths: 4 migration files touched (one retroactively adds `force: true` to a deployed migration)
AI-Authored Likelihood: LOW
