## Summary
36 files changed, 449 lines added, 127 lines deleted. 6 findings (3 critical, 3 improvements).
PR migrates `embeddable_hosts` / `embed_category` site settings to a new `embeddable_hosts` table with admin UI; solid feature but ships with swapped fabricator files, a SQL-injection-shaped migration, and unguarded `.first` lookups in the new controller.

## Critical

:red_circle: [correctness] Fabricator file contents are swapped in `spec/fabricators/category_fabricator.rb`:1 (confidence: 98)
`spec/fabricators/category_fabricator.rb` now defines only `Fabricator(:embeddable_host)`, and the new file `spec/fabricators/embeddable_host_fabricator.rb` contains all the category fabricators (`:category`, `:diff_category`, `:happy_category`, `:private_category`). The file names no longer match their contents. Fabrication auto-loads everything under `spec/fabricators/` so tests may still pass by accident, but this is a clear refactor error that will confuse every future maintainer searching for a fabricator by filename and is almost certainly not what was intended — the diff looks like the rename was half-applied (content moved, but the "remove old content" hunk on the destination file is missing).
```suggestion
# spec/fabricators/category_fabricator.rb — keep the category fabricators here:
Fabricator(:category) do
  name { sequence(:name) { |n| "Amazing Category #{n}" } }
  user
end

Fabricator(:diff_category, from: :category) do
  name "Different Category"
  user
end

Fabricator(:happy_category, from: :category) do
  name 'Happy Category'
  slug 'happy'
  user
end

Fabricator(:private_category, from: :category) do
  transient :group

  name 'Private Category'
  slug 'private'
  user
  after_build do |cat, transients|
    cat.update!(read_restricted: true)
    cat.category_groups.build(group_id: transients[:group].id, permission_type: CategoryGroup.permission_types[:full])
  end
end

# spec/fabricators/embeddable_host_fabricator.rb — move only the embeddable_host fabricator here:
Fabricator(:embeddable_host) do
  host "eviltrout.com"
  category
end
```

:red_circle: [security] SQL injection via string interpolation of site-setting value in `db/migrate/20150818190757_create_embeddable_hosts.rb`:25 (confidence: 92)
The migration interpolates the `embeddable_hosts` site-setting value directly into raw SQL: `execute "INSERT INTO embeddable_hosts (host, category_id, ...) VALUES ('#{h}', #{category_id}, ...)"`. Although `embeddable_hosts` is admin-configured, an existing value containing a single quote (e.g. the admin previously typed a malformed entry, or one was set via API), or an apostrophe in a future locale, will either break the migration at deploy time or permit arbitrary SQL execution as the database user running migrations. Use `exec_params` / `ActiveRecord::Base.sanitize_sql_array` instead.
```suggestion
records.each do |h|
  execute ActiveRecord::Base.send(
    :sanitize_sql_array,
    ["INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)", h, category_id]
  )
end
```

:red_circle: [correctness] `NoMethodError` on nil when PUT/DELETE targets a non-existent id in `app/controllers/admin/embeddable_hosts_controller.rb`:8 (confidence: 95)
Both `update` and `destroy` use `EmbeddableHost.where(id: params[:id]).first`, which returns `nil` for unknown ids. The next line then calls `host.destroy` / `save_host(host)` (which does `host.host = ...`), raising `NoMethodError: undefined method 'destroy' for nil` and surfacing a 500 to the admin UI instead of a 404. `find` raises `ActiveRecord::RecordNotFound`, which Discourse renders as a proper 404.
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

## Improvements

:yellow_circle: [correctness] `TopicEmbed.import` silently categorizes as `nil` when no matching `EmbeddableHost` exists in `app/models/topic_embed.rb`:36 (confidence: 80)
`eh = EmbeddableHost.record_for_host(url)` returns `false` when the URL is unparseable and `nil` when no row matches. The following line `category: eh.try(:category_id)` therefore quietly falls through to `category: nil` (uncategorized) rather than rejecting the embed. Previously `SiteSetting.embed_category` provided a deterministic default. Upstream callers (`TopicRetriever`) already gate on `EmbeddableHost.host_allowed?`, so in the normal path `eh` is non-nil — but `TopicEmbed.import` is also callable directly (and is, in tests), so the nil-silent branch can create topics under unexpected categories if a caller forgets the guard.
```suggestion
eh = EmbeddableHost.record_for_host(url)
raise Discourse::InvalidAccess.new('embeddable host not registered') if eh.blank?

creator = PostCreator.new(user,
                          title: title,
                          raw: absolutize_urls(url, contents),
                          skip_validations: true,
                          cook_method: Post.cook_methods[:raw_html],
                          category: eh.category_id)
```

:yellow_circle: [correctness] `Admin::EmbeddingController#update` is a no-op that returns the current state in `app/controllers/admin/embedding_controller.rb`:9 (confidence: 75)
`update` simply calls `render_serialized(@embedding, EmbeddingSerializer, ...)` without applying any changes from the request. The frontend `saveChanges` action (`admin-embedding.js.es6`) triggers `this.get('embedding').update({})`, which hits this endpoint expecting to persist something. Because all persistence actually happens via the per-host `Admin::EmbeddableHostsController`, this endpoint is effectively dead weight and misleads future readers. Either remove the `PUT /customize/embedding` route + action, or make it update something meaningful (e.g., global embedding settings) and document the no-op.
```suggestion
# app/controllers/admin/embedding_controller.rb — drop `update` until it has real work:
class Admin::EmbeddingController < Admin::AdminController
  before_filter :ensure_logged_in, :ensure_staff, :fetch_embedding

  def show
    render_serialized(@embedding, EmbeddingSerializer, root: 'embedding', rest_serializer: true)
  end

  protected

    def fetch_embedding
      @embedding = OpenStruct.new({
        id: 'default',
        embeddable_hosts: EmbeddableHost.all.order(:host)
      })
    end
end
# …and remove `put "customize/embedding" => "embedding#update"` from config/routes.rb.
```

:yellow_circle: [correctness] Host regex rejects valid TLDs and normalized hosts in `app/models/embeddable_host.rb`:2 (confidence: 72)
`validates_format_of :host, :with => /\A[a-z0-9]+([\-\.]{1}[a-z0-9]+)*\.[a-z]{2,5}(:[0-9]{1,5})?(\/.*)?\Z/i` caps the TLD at 5 chars, rejecting legitimate TLDs like `.museum`, `.engineering`, `.community`, and most IDN punycode TLDs (`.xn--...`). It also still permits a trailing path `(\/.*)?` even though `before_validation` strips it — meaning the regex accepts values that the hook has already stripped, but the expected user input is bare host only. Loosen the TLD (`{2,}`) and drop the path branch so the accepted set matches the normalized shape.
```suggestion
validates_format_of :host, :with => /\A[a-z0-9]+([\-\.]{1}[a-z0-9]+)*\.[a-z]{2,}(:[0-9]{1,5})?\Z/i
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 36 files across app controllers, models, migrations, Ember admin UI, and REST adapter — touches auth-adjacent embeddable-host allow-listing and introduces a new admin CRUD surface | Sensitive Paths: `db/migrate/*`, `app/controllers/admin/*`, `app/controllers/embed_controller.rb`
AI-Authored Likelihood: LOW
