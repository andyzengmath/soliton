## Summary
28 files changed, 653 lines added, 13 lines deleted. 13 findings (11 critical, 2 improvements, 0 nitpicks).
Embed/RSS import feature introduces severe XSS and SSRF vectors, a template SyntaxError that breaks the main render path, a destructive edit to an already-committed migration, and silent regressions in the Disqus importer.

## Critical
:red_circle: [correctness] `<%- end if %>` is invalid Ruby syntax — template raises SyntaxError at render time in app/views/embed/best.html.erb:14 (confidence: 98)
The ERB template closes the outer `if @topic_view.posts.present?` block with `<%- end if %>`. `end if` is the modifier-form `if`, which requires a trailing boolean expression; without one, Ruby raises `SyntaxError: unexpected end-of-input` at template compile time. Because `best.html.erb` is rendered on the hot path (any time a topic has already been retrieved), the embed widget 500s on every subsequent view.
```suggestion
  <%- end %>
```

:red_circle: [security] XSS via unescaped Referer interpolated into JavaScript string literal in app/views/layouts/embed.html.erb:10 (confidence: 95)
`parent.postMessage({...}, '<%= request.referer %>')` interpolates the raw Referer header into a single-quoted JavaScript string with no escaping. `ensure_embeddable` only validates the host portion of the Referer; the path/query/fragment remain attacker-controlled. A Referer such as `http://embeddable.example.com/x'-alert(document.cookie)-'` breaks out of the string literal and executes script in the Discourse origin. Combined with `X-Frame-Options: ALLOWALL`, this is a cross-origin DOM XSS primitive.
```suggestion
<script>
  (function() {
    window.onload = function() {
      if (parent) {
        var referer = <%= raw request.referer.to_json %>;
        var targetOrigin = referer ? (function(){ var a=document.createElement('a'); a.href=referer; return a.protocol+'//'+a.host; })() : '*';
        parent.postMessage({type: 'discourse-resize', height: document['body'].offsetHeight}, targetOrigin);
      }
    };
  })();
</script>
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/79.html]

:red_circle: [security] Stored XSS via :raw_html cook_method plus unsanitized RSS content and URL interpolation in app/models/topic_embed.rb:12 (confidence: 95)
`TopicEmbed.import` creates posts with `cook_method: :raw_html`, and the new `Post#cook` short-circuits to return the raw input unchanged. That raw HTML is rendered in `best.html.erb` via `<%= raw post.cooked %>`. Two attacker-controlled inputs feed this sink: (1) `poll_feed.rb` pipes `CGI.unescapeHTML(i.content.scrub)` from a remote RSS feed directly to `import` with no sanitizer — a malicious or compromised feed publishes `<script>` and event handlers that execute in the Discourse origin; (2) `import` builds the attribution footer as `"<a href='#{url}'>#{url}</a>"` — the guard `url =~ /^https?\:\/\//` allows quotes and angle brackets later in the URL, so a feed item whose `link` contains `'><script>` injects script directly into the stored post.
```suggestion
# app/models/topic_embed.rb
require 'sanitize'
def self.import(user, url, title, contents)
  return unless url =~ /^https?\:\/\//
  safe_url = ERB::Util.html_escape(url)
  contents = Sanitize.clean(contents, Sanitize::Config::RELAXED)
  contents = contents + "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: "<a href='#{safe_url}'>#{safe_url}</a>")}</small>\n"
  # ...
end
```
Prefer removing the `:raw_html` cook_method entirely and routing imported content through Discourse's standard cook pipeline, which already applies a sanitizer.
[References: https://cwe.mitre.org/data/definitions/79.html, https://cwe.mitre.org/data/definitions/80.html]

:red_circle: [security] SSRF via Kernel#open on user-influenced URL in app/models/topic_embed.rb:38 (confidence: 90)
`TopicEmbed.import_remote` calls `open(url).read`. `url` ultimately originates from `params[:embed_url]` in `EmbedController#best` (via `Jobs.retrieve_topic` → `TopicRetriever#fetch_http`). `Kernel#open` from `open-uri` has no scheme allowlist, no block on private IP ranges, and on older Rubies treats `|command` strings as shell commands. `TopicRetriever#invalid_host?` restricts the hostname but still permits any path/port on that host, and does not prevent DNS rebinding. `Jobs::PollFeed#poll_feed` calls `open(SiteSetting.feed_polling_url)` with no restrictions at all — a misconfigured or attacker-modified setting fetches `http://169.254.169.254/latest/meta-data/...`, `http://127.0.0.1/`, or `file:///etc/passwd`, and the response is persisted as a post.
```suggestion
require 'net/http'
require 'ipaddr'
uri = URI.parse(url)
raise Discourse::InvalidParameters unless %w[http https].include?(uri.scheme)
addr = IPSocket.getaddress(uri.host)
blocked = %w[127.0.0.0/8 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 169.254.0.0/16 ::1/128 fc00::/7].map { |c| IPAddr.new(c) }
raise Discourse::InvalidParameters if blocked.any? { |c| c.include?(addr) }
body = Net::HTTP.get(uri)
doc = Readability::Document.new(body, tags: %w[...], attributes: %w[...])
```
[References: https://cwe.mitre.org/data/definitions/918.html, https://owasp.org/Top10/A10_2021-Server-Side_Request_Forgery_%28SSRF%29/]

:red_circle: [security] Broken postMessage origin check allows substring-based spoofing in app/assets/javascripts/embed.js:16 (confidence: 90)
The listener validates origin with `if (discourseUrl.indexOf(e.origin) === -1) { return; }`. The substring check runs in the wrong direction: any origin that happens to be a prefix or substring of `discourseUrl` is accepted. The empty string `""` is a substring of every string and is returned as `e.origin` in some browser contexts (sandboxed iframes, `data:` URLs), effectively accepting messages from null origins. An attacker-controlled page that hosts the iframe can still forge `discourse-resize` messages to resize the frame arbitrarily and assist clickjacking.
```suggestion
var expectedOrigin = (function() {
  var a = document.createElement('a');
  a.href = discourseUrl;
  return a.protocol + '//' + a.host;
})();
function postMessageReceived(e) {
  if (!e || e.origin !== expectedOrigin) { return; }
  if (e.data && e.data.type === 'discourse-resize' && e.data.height) {
    iframe.height = e.data.height + "px";
  }
}
```
[References: https://cwe.mitre.org/data/definitions/346.html, https://developer.mozilla.org/en-US/docs/Web/API/Window/postMessage#security_concerns]

:red_circle: [correctness] Destructive `force: true` added to already-committed historical migration in db/migrate/20131223171005_create_top_topics.rb:3 (confidence: 97)
This is not a new migration — it is a modification to a migration that has already been applied to production, staging, and every developer's database. Adding `force: true` causes `create_table` to emit `DROP TABLE IF EXISTS top_topics` before recreating the table. On any environment that replays migrations (rollback/redo, `db:schema:load` paths that go through migrations, fresh CI runs that drop past the previous schema version), the entire `top_topics` table is silently destroyed. Historical migrations are immutable by Rails convention; corrective changes must ship as a new forward-only migration.
```suggestion
class CreateTopTopics < ActiveRecord::Migration
  def change
    create_table :top_topics do |t|
      t.belongs_to :topic
      # ...
    end
  end
end
```
If a forced recreate is genuinely required, author a new migration (`RecreateTopTopics`) that does `drop_table :top_topics, if_exists: true` followed by `create_table :top_topics`.

:red_circle: [correctness] `i.content.scrub` raises NoMethodError when an RSS item has no `<content>` element in app/jobs/scheduled/poll_feed.rb:32 (confidence: 95)
`SimpleRSS` returns `nil` for items without a `<content>` / `<content:encoded>` field — both are optional in RSS 2.0 and Atom, and many real-world feeds omit them in favor of `<description>` or `<summary>`. Calling `.scrub` on `nil` raises `NoMethodError`, which aborts the entire job (there is no rescue), skipping every item after the first content-less one. A single malformed item silently halts polling.
```suggestion
rss.items.each do |i|
  url = i.link
  url = i.id if url.blank? || url !~ /^https?\:\/\//
  raw_content = i.content || i.respond_to?(:summary) && i.summary || ''
  content = CGI.unescapeHTML(raw_content.to_s.scrub)
  next if content.blank?
  TopicEmbed.import(user, url, i.title, content)
end
```

:red_circle: [cross-file-impact] Disqus import drops `created_at` — all imported threads are timestamped to now in lib/tasks/disqus.thor:141 (confidence: 95)
The old flow passed `created_at: Date.parse(t[:created_at])` to `PostCreator`, preserving each Disqus thread's original creation date. The replacement `TopicEmbed.import_remote(user, t[:link], title: t[:title])` threads the title but has no `created_at` parameter, so every imported thread receives `Time.now` as its creation timestamp. The historical ordering of the imported archive is silently destroyed on every run — operators will not see an error.
```suggestion
# lib/tasks/disqus.thor
post = TopicEmbed.import_remote(
  user,
  t[:link],
  title: t[:title],
  created_at: Date.parse(t[:created_at])
)
# Then extend TopicEmbed.import_remote and TopicEmbed.import to accept and forward
# opts[:created_at] to PostCreator.new(..., created_at: opts[:created_at]).
```

:red_circle: [cross-file-impact] Removing the `--category` Thor option breaks existing import invocations in lib/tasks/disqus.thor:117 (confidence: 97)
`method_option :category, aliases: '-c'` was removed, but Thor does not accept unknown options by default. Any existing operator runbook, cron job, or script that invokes the importer with `-c <category>` will now abort at option parsing with `Thor::UnknownArgumentError` before processing a single thread. This is a breaking interface change with no deprecation path.
```suggestion
# Retain the option with a deprecation warning until the category flow is re-implemented:
method_option :category, aliases: '-c', desc: "(deprecated — no longer used)"
# In #import:
warn "--category is no longer supported and will be ignored" if options[:category]
```
Or thread `category_id` through `TopicEmbed.import_remote` / `import` → `PostCreator` so existing callers continue to work.

:red_circle: [cross-file-impact] Unrescued fetch failures in disqus import abort the entire loop on the first unreachable URL in lib/tasks/disqus.thor:143 (confidence: 90)
The new path calls `TopicEmbed.import_remote(user, t[:link], ...)` per thread, which issues a live HTTP fetch via `open(url).read` and parses with `ruby-readability`. Disqus archives routinely contain dead URLs, paywalled pages, and hosts that have rotated TLS certificates. Any raised `OpenURI::HTTPError`, `SocketError`, `OpenSSL::SSL::SSLError`, or Readability parse error is unrescued, so the first failure terminates `parser.threads.each` and leaves the import partially applied with no resume support.
```suggestion
parser.threads.each do |id, t|
  puts "Creating #{t[:title]}... (#{t[:posts].size} posts)"
  if options[:dry_run].blank?
    begin
      post = TopicEmbed.import_remote(user, t[:link], title: t[:title])
    rescue => e
      warn "Failed to import #{t[:link]}: #{e.class}: #{e.message}"
      next
    end
    # ...
  end
end
```

:red_circle: [cross-file-impact] Spec mocks `TopicRetriever.new` but controller calls `Jobs.enqueue(:retrieve_topic)` — expectation never fires in spec/controllers/embed_controller_spec.rb:43 (confidence: 92)
`EmbedController#best` enqueues a Sidekiq job (`Jobs.enqueue(:retrieve_topic, ...)`) when no topic is found; it never instantiates `TopicRetriever` directly. The spec sets `TopicRetriever.expects(:new).returns(retriever)` and `retriever.expects(:retrieve)` on a code path that is never executed during the controller action. Under Mocha, unmet `expects(:once)` expectations fail verification, so this spec either fails outright or (if Mocha is permissive here) passes vacuously without exercising real behavior. Either way the controller's enqueue contract is untested.
```suggestion
it "enqueues retrieve_topic when no previous embed is found" do
  TopicEmbed.expects(:topic_id_for_embed).returns(nil)
  Jobs.expects(:enqueue).with(:retrieve_topic, user_id: nil, embed_url: embed_url)
  get :best, embed_url: embed_url
end
```
A separate integration-level spec can then assert `TopicRetriever#retrieve` is invoked when the job runs.

## Improvements
:yellow_circle: [security] `X-Frame-Options: ALLOWALL` is non-standard and enables clickjacking in app/controllers/embed_controller.rb:22 (confidence: 85)
`ALLOWALL` is not a valid `X-Frame-Options` value in the spec — browsers either ignore it (falling back to default) or permit framing from any origin. The controller's intended allowlist is enforced only via `request.referer`, which is client-controlled and routinely stripped by Referrer-Policy or HTTPS→HTTP navigation. The correct primitive for frame-ancestor allowlisting is CSP.
```suggestion
def ensure_embeddable
  raise Discourse::InvalidAccess.new('embeddable host not set') if SiteSetting.embeddable_host.blank?
  raise Discourse::InvalidAccess.new('invalid referer host') if URI(request.referer || '').host != SiteSetting.embeddable_host
  response.headers['Content-Security-Policy'] = "frame-ancestors https://#{SiteSetting.embeddable_host}"
  response.headers.delete('X-Frame-Options')
rescue URI::InvalidURIError
  raise Discourse::InvalidAccess.new('invalid referer host')
end
```

:yellow_circle: [correctness] `contents << ...` mutates the caller's string argument in app/models/topic_embed.rb:12 (confidence: 85)
`TopicEmbed.import` uses `contents << "\n<hr>..."` to append the attribution footer, mutating the caller's string in place. On Ruby with `# frozen_string_literal: true` or when called with a frozen literal, this raises `FrozenError`. Even where it works, re-using the same string across two `import` calls double-appends the footer. Project coding-style rules forbid in-place mutation.
```suggestion
contents = contents + "\n<hr>\n<small>#{I18n.t('embed.imported_from', link: "<a href='#{ERB::Util.html_escape(url)}'>#{ERB::Util.html_escape(url)}</a>")}</small>\n"
```

## Risk Metadata
Risk Score: 85/100 (HIGH) | Blast Radius: 28 files, 653 LOC, touches Post cook pipeline + PostRevisor + a historical migration (cross-cutting) | Sensitive Paths: app/controllers/, db/migrate/ (migration mutation), SSRF sink, stored-XSS sink
AI-Authored Likelihood: N/A (this is a 2013-era feature PR, not AI-generated)

(11 additional findings below confidence threshold 85 — see full JSON output for CSRF/rate-limit gaps, `Kernel#open` deprecation on Ruby 3.0+, `require_dependency 'nokogiri'` misuse, `lib/topic_retriever.rb` autoload placement, `cook_method` not forwarded through `PostRevisor`, `discourse_expires_in` called after `render`, and related nits.)
