## Summary
36 files changed, ~570 lines added, ~127 lines deleted. 11 findings (6 critical, 5 improvements).
SQL injection via string interpolation in create_embeddable_hosts migration; new admin controllers ship with placeholder-only tests; fabricator file contents are inverted relative to their filenames.

## Critical

:red_circle: [security] SQL injection in migration via string interpolation of embeddable_hosts site setting in db/migrate/20150818190757_create_embeddable_hosts.rb:18 (confidence: 90)
The migration reads `embeddable_hosts` from `site_settings`, splits on newlines, and interpolates each entry directly into a raw SQL `INSERT`: `execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES ('#{h}', #{category_id}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"`. Values stored in `site_settings.embeddable_hosts` were populated by admin UI input and were never constrained by the new model's `validates_format_of` regex. A crafted entry containing a single quote can break out of the quoted literal and execute arbitrary SQL at migration time with database-owner privileges. The payload can be planted ahead of upgrade.
```suggestion
records.each do |h|
  quoted_host = ActiveRecord::Base.connection.quote(h.strip)
  execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) " \
          "VALUES (#{quoted_host}, #{category_id.to_i}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
end
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/89.html]

:red_circle: [correctness] obj[k].map() throws TypeError when server sends null or omitted plural _ids field in app/assets/javascripts/discourse/models/store.js.es6:228 (confidence: 97)
In the new plural-ids branch of `_hydrateEmbedded`, `.map()` is called directly on `obj[k]` without any null guard. If the server serializes a field such as `color_ids: null` or omits it entirely (yielding `undefined`), `obj[k].map(...)` throws a TypeError at runtime. The fallback `hydrated || []` is positioned after `.map()` already executes — it guards the assignment variable, not the input array, and never fires when `obj[k]` is null/undefined.
```suggestion
const hydrated = (obj[k] || []).map(function(id) {
  return self._lookupSubType(subType, type, id, root);
});
obj[self.pluralize(subType)] = hydrated;
delete obj[k];
```

:red_circle: [testing] Controller spec is a placeholder stub — no behavioral coverage for create, update, or destroy actions in spec/controllers/admin/embeddable_hosts_controller_spec.rb:1 (confidence: 97)
The spec for `Admin::EmbeddableHostsController` contains exactly one test that asserts class inheritance. The controller has three actions with non-trivial logic (`save_host`'s category_id defaulting, validation failure branching, destroy). None of these behaviors are tested. This is a new controller introduced by this PR.
```suggestion
describe '#create' do
  before { log_in_user(Fabricate(:admin)) }
  it "creates a host and defaults category_id to uncategorized when blank" do
    post :create, embeddable_host: { host: 'example.com', category_id: '' }
    expect(response).to be_success
    expect(EmbeddableHost.last.category_id).to eq(SiteSetting.uncategorized_category_id)
  end
  it "returns errors for invalid host" do
    post :create, embeddable_host: { host: 'not a valid host' }
    expect(response).not_to be_success
  end
end
describe '#destroy' do
  let!(:host) { Fabricate(:embeddable_host) }
  before { log_in_user(Fabricate(:admin)) }
  it "destroys the host" do
    delete :destroy, id: host.id
    expect(EmbeddableHost.find_by(id: host.id)).to be_nil
  end
end
```

:red_circle: [testing] New 63-line Ember component with four user-facing actions has zero JavaScript test coverage in app/assets/javascripts/admin/components/embeddable-host.js.es6:1 (confidence: 96)
The embeddable-host component implements four user-facing actions (edit, save, delete, cancel) and a computed property `cantSave`. The save action has non-trivial logic including category_id wiring and `Discourse.Category.findById`; cancel has branching for new vs existing records. No QUnit tests exist for this component introduced by this PR.
```suggestion
// test/javascripts/admin/components/embeddable-host-test.js.es6
moduleForComponent('embeddable-host', 'Component: embeddable-host', { integration: true });
test('cancel on a new host fires deleteHost', function(assert) {
  const host = Ember.Object.create({ isNew: true, isSaving: false, host: 'example.com' });
  this.set('host', host);
  this.on('deleteHost', (h) => assert.equal(h, host));
  this.render(hbs`{{embeddable-host host=host deleteHost="deleteHost"}}`);
  this.$('button.btn-danger').click();
});
test('cancel on an existing host rolls back buffer and clears edit', function(assert) { /* ... */ });
```

:red_circle: [testing] Controller spec is a placeholder stub — show and update actions have zero behavioral coverage in spec/controllers/admin/embedding_controller_spec.rb:1 (confidence: 95)
The spec for `Admin::EmbeddingController` contains only a class inheritance assertion. The `show` action serializes an OpenStruct containing all `EmbeddableHost` records ordered by host, and `update` re-renders the same structure. Neither action nor the `fetch_embedding` before_filter is tested. This is a new controller introduced by this PR.
```suggestion
describe '#show' do
  before { log_in_user(Fabricate(:admin)) }
  it "returns hosts ordered by host name" do
    Fabricate(:embeddable_host, host: 'zebra.com')
    Fabricate(:embeddable_host, host: 'apple.com')
    get :show
    json = JSON.parse(response.body)
    expect(json['embeddable_hosts'].map { |h| h['host'] }).to eq(['apple.com', 'zebra.com'])
  end
  it "requires staff access" do
    log_in_user(Fabricate(:user))
    get :show
    expect(response).not_to be_success
  end
end
```

:red_circle: [correctness] Fabricator file contents inverted — category_fabricator.rb defines :embeddable_host, embeddable_host_fabricator.rb defines :category in spec/fabricators/category_fabricator.rb:1 (confidence: 92)
The content of `spec/fabricators/category_fabricator.rb` and `spec/fabricators/embeddable_host_fabricator.rb` is inverted relative to their filenames. While auto-loading all fabricators may mask this at runtime in many test suite configurations, the layout is fragile: if `category_fabricator.rb` is explicitly required before `embeddable_host_fabricator.rb` by any spec helper, `:category` will be undefined and tests will fail with `Fabricator::UnknownFabricatorError`. The mismatch also makes the codebase actively misleading to anyone navigating by filename. Both the correctness and cross-file-impact agents confirmed the inversion independently.
```suggestion
# spec/fabricators/category_fabricator.rb
Fabricator(:category) do
  name { sequence(:name) { |i| "Amazing Category #{i}" } }
  user
end
# ...other :diff_category, :happy_category, :private_category definitions here...

# spec/fabricators/embeddable_host_fabricator.rb
Fabricator(:embeddable_host) do
  host "eviltrout.com"
  category
end
```

## Improvements

:yellow_circle: [correctness] type.replace('_', '-') only replaces the first underscore — multi-underscore type names will not match ADMIN_MODELS in app/assets/javascripts/discourse/adapters/rest.js.es6:207 (confidence: 95)
`String.prototype.replace` with a plain string literal only replaces the first occurrence. If a type name contains more than one underscore, only the first `_` is converted to `-` and the result will not match any entry in `ADMIN_MODELS`. All existing type names in `ADMIN_MODELS` happen to be single-underscore, but the logic is silently wrong for any future type with multiple underscores.
```suggestion
if (ADMIN_MODELS.indexOf(type.replace(/_/g, '-')) !== -1) { return "/admin/"; }
```

:yellow_circle: [correctness] update and destroy actions dereference nil when record is not found — 500 instead of 404 in app/controllers/admin/embeddable_hosts_controller.rb:259 (confidence: 90)
Both `update` and `destroy` use `EmbeddableHost.where(id: params[:id]).first` and then immediately call methods on the result without checking for nil. When the record does not exist, `.first` returns `nil`, and `nil.host=` / `nil.destroy` raise `NoMethodError`, producing an unhandled 500 instead of a proper 404.
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

:yellow_circle: [correctness] .map() over plural _ids does not filter null results from _lookupSubType, producing sparse arrays in app/assets/javascripts/discourse/models/store.js.es6:228 (confidence: 88)
`_lookupSubType` can return `null` or `undefined` when a referenced sub-type record is not found in the root payload. The singular-id branch guards with `if (hydrated)` and skips assignment on a miss. The plural-ids branch does no such filtering — nulls are left in the resulting array, which can cause downstream null-dereference errors when callers iterate the collection.
```suggestion
const hydrated = (obj[k] || []).map(function(id) {
  return self._lookupSubType(subType, type, id, root);
}).filter(Boolean);
obj[self.pluralize(subType)] = hydrated;
delete obj[k];
```

:yellow_circle: [correctness] destroyRecord() promise has no .catch() handler — unhandled rejection on network or server error in app/assets/javascripts/admin/components/embeddable-host.js.es6:62 (confidence: 85)
The `delete` action calls `this.get('host').destroyRecord().then(...)` without a `.catch()`. If the DELETE request fails (network error or server error), the promise rejection is silently swallowed. By contrast, the `save()` action in the same component correctly chains `.catch(popupAjaxError)`, making the omission inconsistent.
```suggestion
this.get('host').destroyRecord().then(() => {
  this.sendAction('deleteHost', this.get('host'));
}).catch(popupAjaxError);
```

:yellow_circle: [cross-file-impact] TopicEmbed.import passes nil category when no EmbeddableHost matches, silently creating uncategorized topics in app/models/topic_embed.rb:401 (confidence: 85)
`TopicEmbed.import` previously used `category: SiteSetting.embed_category` as an explicit global fallback. The PR changes this to `category: eh.try(:category_id)`. If `record_for_host` returns nil/false (no matching host configured), `category` is nil and `PostCreator` silently creates an uncategorized topic. This is called from RSS background jobs (`TopicRetriever`) where no controller fallback applies, making the regression invisible until content is miscategorized in production.
```suggestion
eh = EmbeddableHost.record_for_host(url)
category_id = eh.try(:category_id) || SiteSetting.uncategorized_category_id
# pass category_id into PostCreator; log a warning when eh is nil to aid debugging
```

## Risk Metadata
Risk Score: 61/100 (HIGH) | Blast Radius: core Ember store + rest adapter + routes + site_setting/topic models (~8 dependents) | Sensitive Paths: db/migrate/20150818190757_create_embeddable_hosts.rb
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold)
