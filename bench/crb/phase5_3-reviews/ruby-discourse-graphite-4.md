## Summary
28 files changed, ~640 lines added, ~15 lines deleted. 14 findings (10 critical, 4 improvements).
This PR introduces a comments-embedding feature with RSS feed import, but stores remote HTML as `cook_method: :raw_html` and renders it through `<%= raw post.cooked %>` with no sanitisation pipeline — yielding stored XSS, multiple SSRF vectors, broken access control, and one unrunnable ERB template.

## Critical

:red_circle: [security] Stored XSS via raw_html cook_method rendering untrusted remote HTML unescaped in app/models/post.rb:128 (confidence: 95)
The new `:raw_html` cook method causes `Post#cook` to return `raw` verbatim, and `app/views/embed/best.html.erb:27` renders the result with `<%= raw post.cooked %>`. Posts created by `TopicEmbed.import` come from `Readability::Document` parsed from a remote URL or `SimpleRSS.parse` of an attacker-controlled feed. Readability's tag/attribute allowlist (`tags: %w[div p code pre h1 h2 h3 b em i strong a img]`, `attributes: %w[href src]`) does not block `javascript:`/`data:` URI schemes, the trailing `imported_from` HTML is concatenated as raw markup, and `CGI.unescapeHTML` reintroduces active markup. A malicious blog/feed achieves stored XSS in the embed iframe origin (the Discourse forum), which is itself iframed into third-party sites with `X-Frame-Options: ALLOWALL`.
```suggestion
# app/models/post.rb — keep the plugin filter pipeline running:
def cook(*args)
  cooked = if cook_method == Post.cook_methods[:raw_html]
             raw
           else
             post_analyzer.cook(*args)
           end
  Plugin::Filter.apply(:after_post_cook, self, cooked)
end

# app/views/embed/best.html.erb — render through Rails' sanitizer instead of `raw`:
<%= sanitize post.cooked,
     tags: %w[a p div h1 h2 h3 b strong i em code pre img blockquote ul ol li],
     attributes: %w[href src alt title] %>
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [security] absolutize_urls allows javascript:, data:, and other dangerous URI schemes in app/models/topic_embed.rb:55 (confidence: 92)
`absolutize_urls` only rewrites `href`/`src` values that `start_with?('/')`. Anchors with `href="javascript:alert(document.cookie)"`, `href="data:text/html,..."`, or images with `src="javascript:..."` are passed through unchanged. Combined with `cook_method: :raw_html` and `<%= raw post.cooked %>`, these schemes become live XSS vectors. The function is the only attribute filter in the import path — and it sanitises nothing.
```suggestion
SAFE_SCHEMES = %w[http https mailto].freeze
def self.absolutize_urls(url, contents)
  uri = URI(url)
  prefix = "#{uri.scheme}://#{uri.host}"
  prefix << ":#{uri.port}" if uri.port && ![80, 443].include?(uri.port)
  fragment = Nokogiri::HTML.fragment(contents)
  fragment.css('a, img').each do |el|
    attr = el.name == 'a' ? 'href' : 'src'
    val = el[attr]
    next if val.blank?
    if val.start_with?('/')
      el[attr] = "#{prefix}/#{val.sub(/^\/+/, '')}"
      next
    end
    scheme = (URI.parse(val).scheme rescue nil)&.downcase
    el.remove_attribute(attr) unless scheme.nil? || SAFE_SCHEMES.include?(scheme)
  end
  fragment.to_html
end
```

:red_circle: [security] SSRF and local file read via Kernel#open in import_remote in app/models/topic_embed.rb:32 (confidence: 95)
`TopicEmbed.import_remote` calls `open(url).read` where `url` originates from the user-supplied `embed_url` query parameter (`EmbedController#best` -> `Jobs::RetrieveTopic` -> `TopicRetriever#fetch_http` -> `import_remote`). Ruby's `Kernel#open` interprets values starting with `|` as shell commands and accepts `file://` for local file reads; even when restricted to http(s), there is no allow-list of hosts, so an attacker can pivot to internal services such as cloud metadata endpoints (`http://169.254.169.254/`), Redis, or an admin panel. The `TopicRetriever#invalid_host?` host-equality guard is the only mitigation, and that guard is bypassed entirely on the RSS-poll path (`Jobs::PollFeed#poll_feed` calls `TopicEmbed.import` -> `import_remote`-style paths with arbitrary item URLs).
```suggestion
require 'net/http'
require 'resolv'
require 'ipaddr'

ALLOWED_SCHEMES = %w[http https].freeze

def self.safe_fetch(url)
  uri = URI.parse(url)
  raise 'invalid scheme' unless ALLOWED_SCHEMES.include?(uri.scheme)
  ip = IPAddr.new(Resolv.getaddress(uri.host))
  raise 'private address blocked' if ip.private? || ip.loopback? || ip.link_local?
  Net::HTTP.start(uri.host, uri.port, use_ssl: uri.scheme == 'https',
                  open_timeout: 5, read_timeout: 10) do |http|
    http.request(Net::HTTP::Get.new(uri.request_uri)).body
  end
end
```
[References: https://owasp.org/Top10/A10_2021-Server-Side_Request_Forgery_%28SSRF%29/, https://cwe.mitre.org/data/definitions/918.html]

:red_circle: [security] SSRF via Kernel#open on feed_polling_url and unvalidated item URLs in app/jobs/scheduled/poll_feed.rb:29 (confidence: 92)
`SimpleRSS.parse open(SiteSetting.feed_polling_url)` uses `Kernel#open` on a site-setting that, while admin-configurable, has no scheme/host allow-list — so it accepts `|cmd` and `file://` values. More importantly, the per-item `url` resolved from `i.link`/`i.id` is then passed to `TopicEmbed.import` with no allow-list, completely bypassing the `embeddable_host` check that lives only in `TopicRetriever`. A malicious feed item can inject arbitrary URLs.
```suggestion
require 'simple-rss'
require 'uri'
allowed_host = SiteSetting.embeddable_host.to_s.downcase
return if SiteSetting.feed_polling_url !~ /\Ahttps?:\/\//
rss = SimpleRSS.parse(safe_fetch(SiteSetting.feed_polling_url))
rss.items.each do |i|
  url = i.link.presence || i.id
  next unless url =~ /\Ahttps?:\/\//
  next unless URI(url).host.to_s.downcase == allowed_host
  raw_content = i.content || i.description || i.summary || ''
  content = CGI.unescapeHTML(raw_content.scrub)
  TopicEmbed.import(user, url, i.title, content)
end
```

:red_circle: [security] HTML/attribute injection via i18n interpolation of unescaped URL into anchor tag in app/models/topic_embed.rb:11 (confidence: 90)
`contents << "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: \"<a href='#{url}'>#{url}</a>\")}</small>\n"` interpolates the user-controlled `url` directly into a single-quoted `href` attribute and as anchor text with no HTML escaping. A URL containing `'` (single quote) breaks out of the attribute, e.g. `http://evil.example/x' onclick='alert(1)` produces `<a href='http://evil.example/x' onclick='alert(1)'>...`. Because the resulting HTML is stored as the post body and rendered with `raw` via `:raw_html`, this is a stored XSS reachable through the RSS poller (which uses attacker-supplied `i.link`/`i.id` as `url`).
```suggestion
link_html = ActionController::Base.helpers.link_to(url, url)
contents = contents + "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: link_html)}</small>\n"
```

:red_circle: [security] postMessage targetOrigin built from unescaped request.referer in app/views/layouts/embed.html.erb:8 (confidence: 88)
`parent.postMessage({...}, '<%= request.referer %>')` interpolates the raw `Referer` header into an inline `<script>` block. `<%= %>` HTML-escapes — but inside `<script>` the JS parser does not decode HTML entities, so a referer containing a single quote like `https://x/'); evilCode(); //` produces broken JS at minimum, and using `request.referer` as `targetOrigin` is wrong on its own: it ships the resize message to whatever origin the embedder claims as referrer. Combined with `X-Frame-Options: ALLOWALL`, any third-party site can frame this page and harvest the message.
```suggestion
<% allowed_origin = "https://#{SiteSetting.embeddable_host}" %>
<script>
  (function() {
    window.onload = function() {
      if (parent) {
        parent.postMessage(
          {type: 'discourse-resize', height: document.body.offsetHeight},
          <%= raw allowed_origin.to_json %>
        );
      }
    }
  })();
</script>
```
[References: https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage#security_concerns]

:red_circle: [security] Broken access control: Referer-based authorization is spoofable and X-Frame-Options ALLOWALL is not a valid value in app/controllers/embed_controller.rb:22 (confidence: 92)
`ensure_embeddable` is the only access control on the embed endpoint and relies on `URI(request.referer || '').host == SiteSetting.embeddable_host`. The `Referer` header is client-controlled — trivially spoofed by non-browser clients and frequently absent in browsers with strict referrer-policy. Additionally `response.headers['X-Frame-Options'] = "ALLOWALL"` is not a standardised value: browsers ignore it, leaving framing protection effectively absent. Combined with the embed page rendering raw post HTML, the endpoint is trivially callable cross-origin.
```suggestion
def ensure_embeddable
  raise Discourse::InvalidAccess.new('embeddable host not set') if SiteSetting.embeddable_host.blank?
  referer = request.referer
  raise Discourse::InvalidAccess.new('invalid referer host') if referer.blank?
  raise Discourse::InvalidAccess.new('invalid referer host') if URI(referer).host != SiteSetting.embeddable_host
  response.headers.delete('X-Frame-Options')
  response.headers['Content-Security-Policy'] =
    "frame-ancestors https://#{SiteSetting.embeddable_host}"
rescue URI::InvalidURIError
  raise Discourse::InvalidAccess.new('invalid referer host')
end
```
[References: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/X-Frame-Options]

:red_circle: [correctness] ERB syntax error: `<%- end if %>` closes the if/else with a dangling `if` modifier in app/views/embed/best.html.erb:5 (confidence: 95)
The `if/else` block is closed with `<%- end if %>`. In Ruby, `end if` is a statement modifier that requires a trailing condition expression; with no condition, this is a `SyntaxError` at template compilation. Either the template fails to compile, or under the rare case where ERB joins fragments differently, the next `<%= %>` tag is consumed as the modifier's condition, producing very surprising runtime behaviour. The other `if/end` pair in the same file (lines 11/29) correctly uses `<%- end %>`.
```suggestion
  <%- if @topic_view.posts.present? %>
    <%= link_to(I18n.t('embed.title'), @topic_view.topic.url, class: 'button', target: '_blank') %>
  <%- else %>
    <%= link_to(I18n.t('embed.start_discussion'), @topic_view.topic.url, class: 'button', target: '_blank') %>
  <%- end %>
```

:red_circle: [correctness] perform_retrieve calls Jobs::PollFeed.new.execute({}) synchronously, blocking the worker for the full RSS download in lib/topic_retriever.rb:33 (confidence: 92)
`TopicRetriever#perform_retrieve` invokes `Jobs::PollFeed.new.execute({})` inline. Inside `PollFeed#poll_feed`, `SimpleRSS.parse open(SiteSetting.feed_polling_url)` opens an external HTTP connection with no timeout. A slow or hung feed endpoint blocks the Sidekiq worker thread for the entire duration (potentially indefinitely), starving other jobs. Calling a `Jobs::Scheduled` job's `execute` directly also bypasses any scheduled-job guard rails, and the loop iterates every item with no early-exit once the requested embed URL is present.
```suggestion
def perform_retrieve
  return if TopicEmbed.where(embed_url: @embed_url).exists?
  if SiteSetting.feed_polling_enabled?
    Jobs.enqueue(:poll_feed)
    return if TopicEmbed.where(embed_url: @embed_url).exists?
  end
  fetch_http
end
```

:red_circle: [correctness] i.content.scrub raises NoMethodError when an RSS item has no <content> element in app/jobs/scheduled/poll_feed.rb:35 (confidence: 92)
`content = CGI.unescapeHTML(i.content.scrub)` calls `.scrub` directly on `i.content`. Many real-world feeds omit `<content>` and provide only `<description>` or `<summary>` — `SimpleRSS` returns `nil` for missing fields, so `nil.scrub` raises `NoMethodError`. The `each` block has no `rescue`, so a single such item aborts the whole feed cycle and silently halts import of all remaining items.
```suggestion
raw_content = i.content || i.description || i.summary || ''
content = CGI.unescapeHTML(raw_content.scrub)
```

## Improvements

:yellow_circle: [security] postMessage origin check uses indexOf — substring confusion allows spoofed origins in app/assets/javascripts/embed.js:14 (confidence: 90)
`if (discourseUrl.indexOf(e.origin) === -1) { return; }` checks whether `e.origin` appears anywhere as a substring of `discourseUrl`. Origins shorter than `discourseUrl` (e.g. `https://d` matching `https://discourse.example.com`) succeed the check, and the test does not anchor on the canonical origin of `discourseUrl`. The correct check is strict equality between `e.origin` and a precomputed expected origin.
```suggestion
var expectedOrigin = (function() {
  var a = document.createElement('a');
  a.href = discourseUrl;
  return a.protocol + '//' + a.host;
})();

function postMessageReceived(e) {
  if (!e || e.origin !== expectedOrigin) return;
  if (e.data && e.data.type === 'discourse-resize' && typeof e.data.height === 'number') {
    iframe.height = e.data.height + 'px';
  }
}
```

:yellow_circle: [correctness] Adding `force: true` to the previously-shipped create_top_topics migration silently drops the table on re-run in db/migrate/20131223171005_create_top_topics.rb:3 (confidence: 88)
The diff modifies an already-shipped migration (timestamp 2013-12-23) by adding `force: true` to `create_table :top_topics`. Any developer or operator running `db:migrate:redo`, `db:reset`, or `db:schema:load` will silently `DROP TABLE IF EXISTS top_topics` and recreate it — destroying production data. The convention is that already-shipped migrations are immutable; cleanup belongs in a new migration.
```suggestion
class CreateTopTopics < ActiveRecord::Migration
  def change
    create_table :top_topics do |t|
      # ... revert force: true; keep the original migration immutable
```

:yellow_circle: [correctness] disqus.thor's import_remote call is unrescued — a single dead permalink aborts the entire batch in lib/tasks/disqus.thor:144 (confidence: 88)
The old code stored a stub `[Permalink]` string with no HTTP traffic. The new code calls `TopicEmbed.import_remote(user, t[:link], title: t[:title])`, which calls `open(url).read` with no `rescue`. A historical Disqus export typically contains many dead links; `OpenURI::HTTPError` / `SocketError` from any one of them propagates up through `parser.threads.each`, killing the rest of the import. The `if post.present?` guard only handles a nil return, not exceptions. The migration also silently drops `category_id` support and `created_at: Date.parse(t[:created_at])`, so imported topics now use current time instead of the original Disqus timestamps — a behavioural regression worth confirming.
```suggestion
parser.threads.each do |id, t|
  puts "Creating #{t[:title]}... (#{t[:posts].size} posts)"
  if options[:dry_run].blank?
    post = begin
             TopicEmbed.import_remote(user, t[:link], title: t[:title])
           rescue OpenURI::HTTPError, SocketError, StandardError => e
             warn "  skip #{t[:link]}: #{e.message}"
             nil
           end
    if post.present?
```

:yellow_circle: [correctness] TopicRetriever#invalid_host? comparison is case-sensitive — admin entries with mixed case silently block all embeds in lib/topic_retriever.rb:13 (confidence: 87)
`SiteSetting.embeddable_host != URI(@embed_url).host` is case-sensitive. Hosts are case-insensitive per RFC 3986, and `URI#host` returns the host as parsed (without normalisation). An admin entering `"EvilTrout.com"` while feeds publish `"eviltrout.com"` causes every legitimate retrieve to fail-closed with no operator-visible error. Normalise both sides and reject userinfo-bearing or non-http(s) URLs while you are there.
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

## Risk Metadata
Risk Score: 66/100 (HIGH) | Blast Radius: post.rb / post_creator.rb / post_revisor.rb modifications fan out across all controllers, jobs, importers, search indexing, emails | Sensitive Paths: db/migrate/* (4 files, including a destructive modification of an already-shipped migration)
AI-Authored Likelihood: LOW

(8 additional findings below confidence threshold)
