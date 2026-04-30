## Summary
36 files changed, 438 lines added, 124 lines deleted. 16 findings (10 critical, 6 improvements, 0 nitpicks).
SQL injection in migration via unescaped site-setting interpolation; multiple nil-dereference crashes in admin controller and model; broad regex change in JS store auto-hydrates all `_ids` keys with a null/undefined crash and likely-non-existent `pluralize` method.

## Critical

:red_circle: [correctness] `update` and `destroy` dereference nil when record not found — 500 instead of 404 in app/controllers/admin/embeddable_hosts_controller.rb:8 (confidence: 99)
Both `update` (lines 8-11) and `destroy` (lines 13-16) use `EmbeddableHost.where(id: params[:id]).first`, which returns `nil` when no record exists. `destroy` then calls `host.destroy` (NoMethodError on nil) and `update` passes nil into `save_host`, which crashes on `host.host =`. Both paths return an unhandled 500 instead of a 404.
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

:red_circle: [security] SQL injection in data-migration via unescaped interpolation of site-setting value in db/migrate/20150818190757_create_embeddable_hosts.rb:26 (confidence: 95)
The INSERT statement interpolates `h` (a stored site-setting value) directly into raw SQL: `execute "INSERT INTO embeddable_hosts ... VALUES ('#{h}', #{category_id}, ...)"`. A site-setting value containing a single quote or stacked SQL statements executes arbitrary SQL with full DB privileges. Additionally, lines 12-14 call `execute(...)[0]['id']` without checking `cmd_tuples > 0` first — if the `embed_category` setting row is absent the indexing crashes, and combined with `force: true` on `create_table`, leaves the database in a broken state.
```suggestion
conn = ActiveRecord::Base.connection
records.each do |h|
  conn.exec_insert(
    "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES ($1, $2, NOW(), NOW())",
    "SQL",
    [[nil, h], [nil, category_id]]
  )
end
```
[References: https://owasp.org/Top10/A03_2021-Injection/]

:red_circle: [correctness] `before_validation` block calls `sub!` on nil `self.host`, raising NoMethodError in app/models/embeddable_host.rb:6 (confidence: 97)
When `EmbeddableHost.new` is built without a `:host` attribute, `self.host` is nil. The `before_validation` block unconditionally calls `self.host.sub!(...)`, which raises NoMethodError before any validation runs. This affects both controller-driven creates and the migration data path.
```suggestion
before_validation do
  if self.host.present?
    self.host.sub!(/^https?:\/\//, '')
    self.host.sub!(/\/.*$/, '')
  end
end
```

:red_circle: [security] Missing strong parameters — `params[:embeddable_host][:host]` raises NoMethodError on absent key in app/controllers/admin/embeddable_hosts_controller.rb:22 (confidence: 95)
`save_host` accesses `params[:embeddable_host][:host]` and `params[:embeddable_host][:category_id]` without any `params.require/permit`. If the top-level key is absent, this raises NoMethodError. Additionally, `category_id` is accepted without verifying the category exists or that the admin is permitted to embed into it, and there is no uniqueness constraint on `host` allowing duplicate routing entries.
```suggestion
def save_host(host)
  attrs = params.require(:embeddable_host).permit(:host, :category_id)
  category = Category.find_by(id: attrs[:category_id]) || Category.find(SiteSetting.uncategorized_category_id)
  host.assign_attributes(host: attrs[:host], category_id: category.id)
  if host.save
    render_serialized(host, EmbeddableHostSerializer, root: 'embeddable_host', rest_serializer: true)
  else
    render_json_error(host)
  end
end
```

:red_circle: [correctness] `_hydrateEmbedded` regex widened to match all `_ids` fields — global behavioral breakage and null crash in app/assets/javascripts/discourse/models/store.js.es6:220 (confidence: 95)
The regex change from `/(.+)\_id$/` to `/(.+)\_id(s?)$/` introduces two distinct problems. First, `obj[k].map(...)` is called unconditionally when the regex matches; if any `_ids` field is null or undefined in a server response, this throws TypeError and crashes hydration for the entire response object. Second, the regex now matches ALL `_ids` keys across every response — `tag_ids`, `group_ids`, `user_ids`, `post_ids`, `allowed_user_ids`, etc. Raw arrays are deleted and replaced with hydrated objects (which contain undefined entries when sideloaded records are absent). Any code reading these by their original `_ids` key will break. This is an unintended global side-effect of a change scoped to only `embeddable_hosts`.
```suggestion
const PLURAL_HYDRATE_KEYS = ['embeddable_host_ids'];
Object.keys(obj).forEach(function(k) {
  if (PLURAL_HYDRATE_KEYS.indexOf(k) !== -1) {
    const subType = k.replace(/_ids$/, '');
    const ids = obj[k] || [];
    obj[Ember.String.pluralize(subType)] = ids.map(function(id) {
      return self._lookupSubType(subType, type, id, root);
    });
    delete obj[k];
    return;
  }
  const m = /(.+)\_id$/.exec(k);
  if (m) {
    const subType = m[1];
    const hydrated = self._lookupSubType(subType, type, obj[k], root);
    if (hydrated) { obj[subType] = hydrated; delete obj[k]; }
  }
});
```

:red_circle: [correctness] `eh.try(:category_id)` silently masks failed host lookup — posts land in wrong category in app/models/topic_embed.rb:38 (confidence: 92)
When `eh` is `false` (URI parse error in `record_for_host`) or `nil` (no matching host), `.try(:category_id)` returns nil with no log or error. `PostCreator` silently uses the default category. Operators receive mis-categorized posts with no indication of the failure. The PR also removed the `embed_category` SiteSetting fallback, eliminating the previous safety net.
```suggestion
eh = EmbeddableHost.record_for_host(url)
if eh.blank?
  Rails.logger.warn("[TopicEmbed] No embeddable host matched for URL: #{url}")
end
creator = PostCreator.new(user,
                          title: title,
                          raw: absolutize_urls(url, contents),
                          skip_validations: true,
                          cook_method: Post.cook_methods[:raw_html],
                          category: eh && eh.category_id)
```

:red_circle: [correctness] `destroyRecord` Promise rejection silently swallowed — no `.catch` handler in app/assets/javascripts/admin/components/embeddable-host.js.es6:41 (confidence: 90)
The `delete` action calls `this.get('host').destroyRecord().then(...)` with no `.catch` handler. Server-side failures (404, 500, network errors) are silently discarded. The UI shows stale state with no error feedback. This is inconsistent with the `save` action in the same component, which correctly uses `.catch(popupAjaxError)`.
```suggestion
delete() {
  bootbox.confirm(I18n.t('admin.embedding.confirm_delete'), (result) => {
    if (result) {
      this.get('host').destroyRecord().then(() => {
        this.sendAction('deleteHost', this.get('host'));
      }).catch(popupAjaxError);
    }
  });
}
```

:red_circle: [correctness] `saveChanges` fires `update()` with no `.then`/`.catch` — save failures silently lost in app/assets/javascripts/admin/controllers/admin-embedding.js.es6:5 (confidence: 90)
`this.get('embedding').update({})` returns a Promise but no handlers are attached. Save failures are silently discarded and the user receives neither a success confirmation nor an error message.
```suggestion
import { popupAjaxError } from 'discourse/lib/ajax-error';

export default Ember.Controller.extend({
  embedding: null,
  actions: {
    saveChanges() {
      this.get('embedding').update({}).catch(popupAjaxError);
    },
    // ...
  }
});
```

:red_circle: [correctness] Inline `URI(host) rescue nil` swallows all exceptions silently in app/models/embeddable_host.rb:10 (confidence: 95)
`URI(host) rescue nil` converts URI::InvalidURIError, ArgumentError, and any unexpected runtime exception into nil with no log entry. Failure is completely invisible at the `TopicEmbed.import` call site, where the result feeds into `.try(:category_id)`.
```suggestion
def self.record_for_host(host)
  begin
    uri = URI(host)
  rescue URI::InvalidURIError, ArgumentError => e
    Rails.logger.warn("[EmbeddableHost] Invalid URI '#{host}': #{e.message}")
    return false
  end
  return false unless uri.present?
  host = uri.host
  return false unless host.present?
  where("lower(host) = ?", host).first
end
```

:red_circle: [correctness] Possible non-existent method `self.pluralize(subType)` on Store — TypeError at runtime in app/assets/javascripts/discourse/models/store.js.es6:231 (confidence: 75)
The Discourse REST Store is declared as `Ember.Object.extend({})` and does not define a `pluralize` method anywhere visible in the diff. Pluralization in this 2015-era Discourse / Ember codebase is provided by `Ember.String.pluralize` (via the ember-inflector addon) and is not exposed as an instance method on plain `Ember.Object` subclasses. The first time hydration encounters a key matching `_ids$` (e.g., the new `color_ids` test fixture), the call throws "self.pluralize is not a function" and breaks model loading.
```suggestion
obj[Ember.String.pluralize(subType)] = hydrated || [];
```

## Improvements

:yellow_circle: [correctness] Fabricator file contents are inverted — load-order fragility in spec/fabricators/category_fabricator.rb:1 (confidence: 90)
`category_fabricator.rb` now contains only `Fabricator(:embeddable_host)`, and `embeddable_host_fabricator.rb` contains all category fabricators (`:category`, `:diff_category`, `:happy_category`, `:private_category`). Symbol-based lookup works at runtime because Fabrication loads all files, but selective loaders or alphabetical eager-loading could trigger "No fabricator defined for :category" if `:embeddable_host` references the `category` association before its file loads. Maintenance hazard: any developer searching `category_fabricator.rb` for category fabricators will not find them.
```suggestion
# spec/fabricators/category_fabricator.rb
Fabricator(:category) do
  name { sequence(:name) { |n| "Amazing Category #{n}" } }
  user
end
# ... :diff_category, :happy_category, :private_category ...

# spec/fabricators/embeddable_host_fabricator.rb
Fabricator(:embeddable_host) do
  host "eviltrout.com"
  category
end
```

:yellow_circle: [correctness] Migration raw INSERT bypasses model validation and normalization — silent data integrity failure in db/migrate/20150818190757_create_embeddable_hosts.rb:24 (confidence: 88)
The `EmbeddableHost` `before_validation` callback strips `http://`, `https://`, and trailing paths, and the validator enforces a strict regex. The raw SQL INSERT writes values verbatim from the site setting. A stored value like `"http://example.com"` is inserted as-is; subsequent `host_allowed?` lookups (which use `lower(host)`) will never match because they compare against scheme-prefixed strings. This produces silent embedding failures post-migration.
```suggestion
records.each do |h|
  EmbeddableHost.create!(host: h, category_id: category_id)
end
```

:yellow_circle: [correctness] Controller spec asserts only class hierarchy — all actions completely untested in spec/controllers/admin/embeddable_hosts_controller_spec.rb:1 (confidence: 85)
The single test asserts only inheritance (`Admin::EmbeddableHostsController < Admin::AdminController`). There is no coverage of create, update, destroy, 404 paths, validation failures, or the nil-record crash paths identified in the critical findings above. The spec would pass even if all action methods were deleted.
```suggestion
describe Admin::EmbeddableHostsController do
  let(:admin) { Fabricate(:admin) }
  before { sign_in(admin) }

  context '#destroy' do
    it 'returns 404 when host not found' do
      delete :destroy, id: 999999
      expect(response.status).to eq(404)
    end
    it 'destroys an existing host' do
      eh = Fabricate(:embeddable_host)
      delete :destroy, id: eh.id
      expect(response).to be_success
      expect(EmbeddableHost.exists?(eh.id)).to eq(false)
    end
  end

  context '#update' do
    it 'returns 404 when host not found' do
      put :update, id: 999999, embeddable_host: { host: 'example.com', category_id: 1 }
      expect(response.status).to eq(404)
    end
  end
end
```

:yellow_circle: [correctness] `type.replace('_', '-')` only replaces the first underscore in app/assets/javascripts/discourse/adapters/rest.js.es6:7 (confidence: 90)
`String#replace` with a string literal replaces only the first occurrence. A type like `'site_customization'` (one underscore) normalizes correctly, but any future `ADMIN_MODELS` entry with multiple underscores (e.g., `'user_field_option'`) would only have its first underscore replaced and would silently fail the lookup.
```suggestion
if (ADMIN_MODELS.indexOf(type.replace(/_/g, '-')) !== -1) { return "/admin/"; }
```

:yellow_circle: [correctness] `expandable_first_post?` test passes vacuously — false coverage confidence in spec/models/topic_spec.rb:868 (confidence: 85)
The test "is false if embeddable_host is blank" passes only because `embed_truncate` defaults to false, not because of any EmbeddableHost logic (which the model no longer checks). If the `embed_truncate` guard or `has_topic_embed?` check is later removed, this test would still pass while the behavior regresses.
```suggestion
it "is false with default settings" do
  expect(topic.expandable_first_post?).to eq(false)
end
```

:yellow_circle: [correctness] `SiteSetting.uncategorized_category_id` fallback may be nil/0 — NOT NULL constraint violation in app/controllers/admin/embeddable_hosts_controller.rb:24 (confidence: 82)
The migration defines `category_id` as NOT NULL. If `SiteSetting.uncategorized_category_id` is uninitialized (returns nil or 0) at save time, the INSERT fails with a NOT NULL constraint violation. This path is not covered by the current trivial controller spec.
```suggestion
host.category_id = SiteSetting.uncategorized_category_id if host.category_id.blank?
fallback_category = Category.find_by(id: host.category_id)
return render_json_error('invalid category') unless fallback_category
```

## Risk Metadata
Risk Score: 63/100 (HIGH) | Blast Radius: 80 (rest.js.es6 and store.js.es6 are framework-level files used by every REST-serialized model in the Discourse client) | Sensitive Paths: db/migrate/* matched
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
