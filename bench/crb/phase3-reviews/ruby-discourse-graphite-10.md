## Summary
36 files changed, 463 lines added, 159 lines deleted. 14 findings (4 critical, 7 improvements, 3 nitpicks).
Replaces the `embeddable_hosts` / `embed_category` site settings with a first-class `EmbeddableHost` ActiveRecord model, an admin CRUD UI/controller, and a backfill migration; several controller and migration paths can crash on missing rows, and the data-migration interpolation is unsafe.

## Critical

:red_circle: [security] SQL injection / migration breakage from unescaped host interpolation in `db/migrate/20150818190757_create_embeddable_hosts.rb:25` (confidence: 95)
The backfill loop interpolates the raw `embeddable_hosts` site-setting value directly into a SQL `INSERT` string: `execute "INSERT INTO embeddable_hosts (host, category_id, ...) VALUES ('#{h}', ...)"`. Any host containing a single quote, a comment marker, or a statement terminator will either crash the migration or execute attacker-controlled SQL. `embeddable_hosts` was previously a free-form, admin-editable text setting, so a malicious or compromised admin (or a copy/pasted value with an apostrophe) is enough to weaponize this. Use `ActiveRecord::Base.connection.quote(h)` or, better, instantiate the model: `EmbeddableHost.create!(host: h, category_id: category_id)`.
```suggestion
        records.each do |h|
          quoted_host = ActiveRecord::Base.connection.quote(h)
          execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES (#{quoted_host}, #{category_id.to_i}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        end
```
[References: https://rails-sqli.org/, OWASP A03:2021]

:red_circle: [correctness] `NoMethodError` when `update`/`destroy` receive an unknown `id` in `app/controllers/admin/embeddable_hosts_controller.rb:8` (confidence: 95)
Both `update` and `destroy` use `EmbeddableHost.where(id: params[:id]).first`, which returns `nil` for an unknown id. `update` then calls `save_host(nil)` which immediately invokes `nil.host = ...` (`NoMethodError`); `destroy` calls `nil.destroy`. The endpoint is admin-only but is still trivially DoS'able by any staff user passing a stale id, and produces a 500 instead of a 404. Use `find` (raises `ActiveRecord::RecordNotFound` and Rails translates to 404) or a `head :not_found` guard.
```suggestion
  def update
    host = EmbeddableHost.find(params[:id])
    save_host(host)
  end

  def destroy
    host = EmbeddableHost.find(params[:id])
    host.destroy
    render json: success_json
  end
```

:red_circle: [correctness] Migration crashes on any DB without a matching `embed_category` row in `db/migrate/20150818190757_create_embeddable_hosts.rb:8` (confidence: 95)
`execute("SELECT c.id FROM categories AS c INNER JOIN site_settings AS s ON s.value = c.name WHERE s.name = 'embed_category'")[0]['id'].to_i` indexes `[0]` with no nil check. On any installation that never set `embed_category` (or set it to a category name that no longer exists), the inner join returns zero rows and `[0]` raises `NoMethodError: undefined method '[]' for nil`, breaking the deploy. The fallback `if category_id == 0` is unreachable because the crash happens before it runs.
```suggestion
    result = execute("SELECT c.id FROM categories AS c
                      INNER JOIN site_settings AS s ON s.value = c.name
                      WHERE s.name = 'embed_category'")
    category_id = result.first ? result.first['id'].to_i : 0

    if category_id == 0
      uncategorized = execute("SELECT value FROM site_settings WHERE name = 'uncategorized_category_id'")
      category_id = uncategorized.first ? uncategorized.first['value'].to_i : 1
    end
```

:red_circle: [correctness] `before_validation` raises `NoMethodError` when `host` is nil in `app/models/embeddable_host.rb:5` (confidence: 92)
`self.host.sub!(/^https?:\/\//, '')` is called unconditionally. When the controller forwards a missing or null `params[:embeddable_host][:host]`, the model assigns `nil` and the callback explodes with a 500 instead of letting the `validates_format_of` produce a proper validation error. Because the controller already accepts unsanitized input (`host.host = params[:embeddable_host][:host]`), this is reachable from any malformed admin request.
```suggestion
  before_validation do
    if self.host.present?
      self.host = self.host.sub(/^https?:\/\//, '').sub(/\/.*$/, '')
    end
  end
```

## Improvements

:yellow_circle: [correctness] `Admin::EmbeddingController#update` is a no-op in `app/controllers/admin/embedding_controller.rb:8` (confidence: 90)
The action signature implies "save the embedding configuration" (and the JS controller calls `embedding.update({})` from `saveChanges()`), but the body simply re-renders the in-memory `OpenStruct` without persisting anything. Any future settings added to the embedding payload will be silently dropped. Either drop the route until there is real state to persist, or wire the update through to the relevant `SiteSetting`s / `EmbeddableHost` collection. Document the no-op contract if it is intentional.
```suggestion
  def update
    # No persistent embedding-level settings yet; embeddable hosts are managed
    # via the dedicated EmbeddableHostsController. Re-serialize for round-trip.
    render_serialized(@embedding, EmbeddingSerializer, root: 'embedding', rest_serializer: true)
  end
```

:yellow_circle: [correctness] Hydration crashes on null `*_ids` payload in `app/assets/javascripts/discourse/models/store.js.es6:198` (confidence: 80)
The new plural branch unconditionally calls `obj[k].map(...)`. If the server ever serializes a key like `color_ids` as `null` (a perfectly valid JSON state for a `has_many` with no children, and one the existing `EmbeddingSerializer.has_many :embeddable_hosts` could conceivably emit on an empty store), the hydrator throws `TypeError: Cannot read property 'map' of null` and the entire response fails to load. Also `hydrated || []` is dead — `[].map(...)` is always an array.
```suggestion
        if (m[2]) {
          const ids = obj[k] || [];
          obj[self.pluralize(subType)] = ids.map(function(id) {
            return self._lookupSubType(subType, type, id, root);
          }).filter(Boolean);
          delete obj[k];
        } else {
          ...
        }
```

:yellow_circle: [testing] New admin controllers have no behavioral coverage in `spec/controllers/admin/embeddable_hosts_controller_spec.rb:5` and `spec/controllers/admin/embedding_controller_spec.rb:5` (confidence: 95)
Both new specs assert only `Controller < Admin::AdminController`, which already follows from inheritance. None of `create`, `update`, `destroy`, `show` are exercised — meaning the four `NoMethodError` paths above (unknown id, missing params, OpenStruct stub, etc.) have no regression net. At minimum cover: (a) staff-only access, (b) successful create/update/destroy round-trip, (c) update/destroy with an unknown id returns 404, (d) `show` renders the embedding payload with hosts ordered by host.
```suggestion
describe Admin::EmbeddableHostsController do
  let(:admin) { log_in(:admin) }

  it "creates a host" do
    post :create, embeddable_host: { host: 'example.com', category_id: Fabricate(:category).id }
    expect(response).to be_success
    expect(EmbeddableHost.where(host: 'example.com')).to exist
  end

  it "404s on unknown id for update" do
    put :update, id: 0, embeddable_host: { host: 'example.com' }
    expect(response.status).to eq(404)
  end

  it "destroys an existing host" do
    host = Fabricate(:embeddable_host)
    delete :destroy, id: host.id
    expect(response).to be_success
    expect(EmbeddableHost.where(id: host.id)).not_to exist
  end
end
```

:yellow_circle: [security] Mass-assignment surface widened without strong params in `app/controllers/admin/embeddable_hosts_controller.rb:21` (confidence: 70)
The controller pulls fields out of `params[:embeddable_host]` by hand, which works today, but if a future contributor switches to `EmbeddableHost.new(params[:embeddable_host])` they inherit any column added later (e.g. `created_by_id`, `disabled`). Add `params.require(:embeddable_host).permit(:host, :category_id)` and assign in one place to make the contract explicit and future-proof.
```suggestion
    def save_host(host)
      attrs = params.require(:embeddable_host).permit(:host, :category_id)
      attrs[:category_id] = SiteSetting.uncategorized_category_id if attrs[:category_id].blank?
      host.assign_attributes(attrs)
      ...
    end
```

:yellow_circle: [correctness] `expandable_first_post?` no longer requires any embeddable host to be configured in `app/models/topic.rb:868` (confidence: 78)
The previous guard was `SiteSetting.embeddable_hosts.present? && SiteSetting.embed_truncate? && has_topic_embed?`. The new guard drops the host check entirely, so the "expand" affordance can render on a forum that has never configured embedding at all (as long as some legacy `topic_embed` row exists). The `topic_spec.rb` change titled "is false if embeddable_host is blank" no longer actually tests that condition — it just relies on `has_topic_embed?` being false. Reinstate the existence check via the new model.
```suggestion
  def expandable_first_post?
    SiteSetting.embed_truncate? && has_topic_embed? && EmbeddableHost.exists?
  end
```

:yellow_circle: [correctness] `topic_embed` now silently posts to "no category" instead of `embed_category` in `app/models/topic_embed.rb:42` (confidence: 75)
Old behavior: `category: SiteSetting.embed_category` (a global default). New behavior: `category: eh.try(:category_id)` where `eh` is the matching `EmbeddableHost`, or `nil` when no record matches. After the migration this is usually fine, but for any incoming embed request whose URL host does not match a row (typo, new domain rolling out, race during admin edits), the post is created with `category: nil` rather than the legacy default. Worth a fallback to `SiteSetting.uncategorized_category_id`.
```suggestion
        eh = EmbeddableHost.record_for_host(url)
        creator = PostCreator.new(user,
                                  title: title,
                                  raw: absolutize_urls(url, contents),
                                  skip_validations: true,
                                  cook_method: Post.cook_methods[:raw_html],
                                  category: eh.try(:category_id) || SiteSetting.uncategorized_category_id)
```

:yellow_circle: [consistency] Fabricator file names and contents are swapped in `spec/fabricators/category_fabricator.rb:1` and `spec/fabricators/embeddable_host_fabricator.rb:1` (confidence: 98)
`category_fabricator.rb` now defines `Fabricator(:embeddable_host)` and `embeddable_host_fabricator.rb` now defines all the `:category` / `:diff_category` / `:happy_category` / `:private_category` fabricators. Fabrication loads by name so it works at runtime, but anyone grepping for `Fabricator(:category)` will jump to the wrong file forever. Either restore the original filenames-match-symbol convention, or merge into one file with a comment explaining the consolidation.

## Nitpicks

:white_circle: [correctness] `record_for_host` shadows its own parameter in `app/models/embeddable_host.rb:9` — the local `host = uri.host` reassignment makes the method harder to read; rename one of them.

:white_circle: [security] Loose host regex in `app/models/embeddable_host.rb:2` rejects valid TLDs longer than 5 chars (`.museum`, `.engineering`, `.travel`) and any IDN/punycode (`xn--…`); consider a more permissive pattern or `PublicSuffix`.

:white_circle: [consistency] `force: true` on `create_table :embeddable_hosts` in `db/migrate/20150818190757_create_embeddable_hosts.rb:3` will silently drop a pre-existing table on a re-run; not appropriate for an `up`-only change migration. Drop `force: true`.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: 36 files across admin UI, controllers, models, migrations, and JS hydration core; touches embed/auth path (`embed_controller#ensure_embeddable`) and a shared store hydrator used by every REST model | Sensitive Paths: db/migrate/*, admin/* controllers, embed_controller (auth boundary)
AI-Authored Likelihood: LOW
