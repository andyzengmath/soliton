## Summary
36 files changed, 449 lines added, 127 lines deleted. 9 findings (4 critical, 5 improvements).
Migrates `embeddable_hosts` from a newline-delimited site setting to a new `EmbeddableHost` ActiveRecord model with admin CRUD UI; the migration and new controllers have several unguarded failure modes.

## Critical

:red_circle: [security] SQL injection in data-backfill migration in `db/migrate/20150818190757_create_embeddable_hosts.rb`:25 (confidence: 95)
The backfill loop interpolates host strings from the old `embeddable_hosts` site setting directly into a raw `INSERT`: `execute "INSERT INTO embeddable_hosts (host, category_id, ...) VALUES ('#{h}', #{category_id}, ...)"`. An admin-entered host containing a single quote (or a deliberately crafted payload) breaks the statement and executes arbitrary SQL under migration privileges. Use parameterized SQL / `ActiveRecord::Base.connection.quote` or build records via the model.
```suggestion
records.each do |h|
  quoted_host = ActiveRecord::Base.connection.quote(h)
  execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES (#{quoted_host}, #{category_id.to_i}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
end
```

:red_circle: [correctness] `before_validation` block crashes when `host` is nil in `app/models/embeddable_host.rb`:5 (confidence: 92)
`self.host.sub!(...)` and `self.host.sub!(...)` are called before the format validator runs. Creating an `EmbeddableHost` with `host: nil` (or without setting `host`) raises `NoMethodError: undefined method 'sub!' for nil:NilClass` instead of producing a clean validation error. This fires on every controller `create` path where the client omits `embeddable_host[host]`.
```suggestion
before_validation do
  next if host.blank?
  self.host = host.sub(/^https?:\/\//, '').sub(/\/.*$/, '')
end
```

:red_circle: [correctness] `update` / `destroy` dereference nil when id is invalid in `app/controllers/admin/embeddable_hosts_controller.rb`:9 (confidence: 90)
Both actions do `EmbeddableHost.where(id: params[:id]).first` and then call methods on the result without a nil check. An unknown or already-deleted id makes `update` call `save_host(nil)` (which then calls `nil.host = ...`) and `destroy` call `nil.destroy`, both raising `NoMethodError` and returning a 500 instead of a 404.
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

:red_circle: [correctness] `create_table ... force: true` drops and rewrites the table on any re-run in `db/migrate/20150818190757_create_embeddable_hosts.rb`:3 (confidence: 88)
`force: true` issues `DROP TABLE IF EXISTS embeddable_hosts` before creating the table. If this migration is ever re-run (e.g. `rake db:migrate:redo`, a CI rollback, or a schema reset script), every configured embeddable host is silently destroyed. Drop `force: true`; migrations should be additive and idempotent via the migrations table.
```suggestion
create_table :embeddable_hosts do |t|
  t.string :host, null: false
  t.integer :category_id, null: false
  t.timestamps
end
```

## Improvements

:yellow_circle: [security] Missing strong parameters in `app/controllers/admin/embeddable_hosts_controller.rb`:20 (confidence: 80)
The controller reads `params[:embeddable_host][:host]` and `params[:embeddable_host][:category_id]` directly. The current code only sets two explicit fields so there is no immediate mass-assignment exposure, but the pattern bypasses the project's Rails 4 strong-parameter convention; any future `host.attributes = params[:embeddable_host]` refactor would become a vulnerability. Add a `permit`ted params helper.
```suggestion
def save_host(host)
  attrs = params.require(:embeddable_host).permit(:host, :category_id)
  host.host = attrs[:host]
  host.category_id = attrs[:category_id].presence || SiteSetting.uncategorized_category_id
  # ...
end
```

:yellow_circle: [testing] Controller specs only assert class inheritance in `spec/controllers/admin/embeddable_hosts_controller_spec.rb`:5 (confidence: 90)
Both `embeddable_hosts_controller_spec.rb` and `embedding_controller_spec.rb` contain a single `it "is a subclass of AdminController"` assertion, which is a tautology that exercises none of the new `create`/`update`/`destroy` behavior, serializer output, staff-only authorization, or error paths. The new controllers ship effectively untested. Add request/controller specs that exercise authz, happy path JSON shape, and invalid-id handling.
```suggestion
describe 'POST create' do
  it 'requires staff' do
    post :create, params: { embeddable_host: { host: 'eviltrout.com' } }
    expect(response).not_to be_success
  end

  context 'as staff' do
    before { sign_in(Fabricate(:admin)) }
    it 'creates a host' do
      expect {
        post :create, params: { embeddable_host: { host: 'eviltrout.com' } }
      }.to change(EmbeddableHost, :count).by(1)
    end
  end
end
```

:yellow_circle: [correctness] `type.replace('_', '-')` only rewrites the first underscore in `app/assets/javascripts/discourse/adapters/rest.js.es6`:22 (confidence: 85)
String `replace` with a string pattern (not a regex) substitutes only the first match, so a future admin model named e.g. `user_custom_field` would be looked up as `user-custom_field`, silently miss the `ADMIN_MODELS` list, and hit the public base path `/`. Use a global regex so all underscores are normalized.
```suggestion
if (ADMIN_MODELS.indexOf(type.replace(/_/g, '-')) !== -1) { return "/admin/"; }
```

:yellow_circle: [correctness] Host regex rejects modern/long TLDs in `app/models/embeddable_host.rb`:2 (confidence: 78)
The validator `/\A[a-z0-9]+([\-\.]{1}[a-z0-9]+)*\.[a-z]{2,5}(:[0-9]{1,5})?(\/.*)?\Z/i` caps the final TLD at 5 characters, so valid hosts like `example.museum`, `example.travel`, `example.photography`, or `discuss.example.london` cannot be added. Widen the range or drop the explicit TLD length cap (the public suffix list is the only real source of truth).
```suggestion
validates_format_of :host, with: /\A[a-z0-9]+([\-\.]{1}[a-z0-9]+)*\.[a-z]{2,}(:[0-9]{1,5})?(\/.*)?\Z/i
```

:yellow_circle: [correctness] `eh.try(:category_id)` can create topic with nil category in `app/models/topic_embed.rb`:36 (confidence: 72)
`TopicEmbed.import` now passes `category: eh.try(:category_id)`. The surrounding code path is guarded by `EmbeddableHost.host_allowed?` at controller/retriever level, but `record_for_host` returns `false` when `URI(host)` parses an unexpected shape (e.g. hosts with `_` underscores), and this method is `public`/reachable directly. A nil category bypasses the previous `SiteSetting.embed_category` default and makes the `PostCreator` fall through to uncategorized — desirable to make explicit rather than implicit. Also note the old setting `embed_category` held a category **name** while `eh.category_id` is the primary key; confirm `PostCreator` accepts an integer here (looks correct in current Discourse but worth an assertion in tests).
```suggestion
eh = EmbeddableHost.record_for_host(url)
category_id = eh&.category_id || SiteSetting.uncategorized_category_id
creator = PostCreator.new(user, title: title, raw: absolutize_urls(url, contents),
                          skip_validations: true, cook_method: Post.cook_methods[:raw_html],
                          category: category_id)
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: touches topic creation, embed auth path, admin routes, and a destructive DB migration — ~36 files across controllers/models/js/specs | Sensitive Paths: `db/migrate/`, `app/controllers/admin/`, `app/controllers/embed_controller.rb`
AI-Authored Likelihood: LOW
