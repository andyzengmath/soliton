## Summary
36 files changed, 447 lines added, 127 lines deleted. 10 findings (7 critical, 3 improvements, 0 nitpicks).
TypeError when plural `_ids` field is null — `.map()` called on null in `store.js.es6:228`.

## Critical

:red_circle: [correctness] TypeError when plural _ids field is null — .map() called on null in app/assets/javascripts/discourse/models/store.js.es6:228 (confidence: 95)
In the new plural hydration branch, `obj[k].map(...)` is called unconditionally. If the server sends `"embeddable_host_ids": null`, this throws `TypeError: Cannot read property 'map' of null`. The singular branch guards with `if (hydrated)`; the plural branch has no null guard. The `hydrated || []` fallback is dead code because `Array.map` always returns an array.
```suggestion
if (m[2]) {
  const ids = obj[k];
  if (!ids) {
    obj[self.pluralize(subType)] = [];
  } else {
    const hydrated = ids.map(function(id) {
      return self._lookupSubType(subType, type, id, root);
    });
    obj[self.pluralize(subType)] = hydrated;
  }
  delete obj[k];
}
```

:red_circle: [correctness] Migration crashes with NoMethodError when embed_category setting does not exist in db/migrate/20150818190757_create_embeddable_hosts.rb:17 (confidence: 92)
`execute(...)[0]['id']` is called unconditionally. If the `embed_category` site setting was never set (common on fresh installs), the result set is empty and `result[0]` is `nil`. `nil['id']` raises `NoMethodError` before the `if category_id == 0` fallback is reached. The `embeddable_hosts` query a few lines below correctly guards with `.cmd_tuples > 0` — the same pattern must be applied here.
```suggestion
cat_result = execute("SELECT c.id FROM categories AS c
                    INNER JOIN site_settings AS s ON s.value = c.name
                    WHERE s.name = 'embed_category'")
category_id = cat_result.cmd_tuples > 0 ? cat_result[0]['id'].to_i : 0
if category_id == 0
  category_id = execute("SELECT value FROM site_settings WHERE name = 'uncategorized_category_id'")[0]['value'].to_i
end
```

:red_circle: [testing] EmbeddableHostsController CRUD (create/update/destroy) has zero functional test coverage in spec/controllers/admin/embeddable_hosts_controller_spec.rb:1 (confidence: 98)
The spec only asserts class inheritance. None of `create`, `update`, `destroy` are exercised. The `save_host` helper's fallback to `uncategorized_category_id` when `category_id` is blank, the success-serialization path, the error-rendering path, and the `ensure_staff` guard are all untested.
```suggestion
describe Admin::EmbeddableHostsController do
  let(:admin) { Fabricate(:admin) }
  before { log_in_user(admin) }

  describe '#create' do
    it 'creates an embeddable host' do
      expect {
        post :create, embeddable_host: { host: 'example.com' }
      }.to change(EmbeddableHost, :count).by(1)
    end

    it 'falls back to uncategorized when category_id is blank' do
      post :create, embeddable_host: { host: 'example.com', category_id: '' }
      expect(EmbeddableHost.last.category_id).to eq(SiteSetting.uncategorized_category_id)
    end

    it 'returns errors for an invalid host' do
      post :create, embeddable_host: { host: 'not a host!!' }
      expect(response).not_to be_success
    end
  end

  describe '#destroy' do
    let!(:eh) { Fabricate(:embeddable_host) }
    it 'destroys the host' do
      expect { delete :destroy, id: eh.id }.to change(EmbeddableHost, :count).by(-1)
    end
  end

  it 'requires staff' do
    log_in_user(Fabricate(:user))
    post :create, embeddable_host: { host: 'example.com' }
    expect(response).not_to be_success
  end
end
```

:red_circle: [testing] EmbeddingController show/update actions have zero functional test coverage in spec/controllers/admin/embedding_controller_spec.rb:1 (confidence: 97)
The spec only asserts inheritance. The `show` action serializes all `EmbeddableHost` records via `EmbeddingSerializer`; none of these behaviors are verified. The staff-only guard is untested.
```suggestion
describe Admin::EmbeddingController do
  let(:admin) { Fabricate(:admin) }
  before { log_in_user(admin) }

  describe '#show' do
    let!(:eh) { Fabricate(:embeddable_host) }
    it 'returns embedding data with embeddable hosts' do
      get :show
      expect(response).to be_success
      json = JSON.parse(response.body)
      expect(json['embedding']['embeddable_hosts'].length).to eq(1)
    end
  end

  it 'requires staff' do
    log_in_user(Fabricate(:user))
    get :show
    expect(response).not_to be_success
  end
end
```

:red_circle: [testing] Ember component save/delete/cancel actions have zero JavaScript test coverage in app/assets/javascripts/admin/components/embeddable-host.js.es6:1 (confidence: 95)
The `embeddable-host` component contains non-trivial action logic: `save` reads buffered properties and a separate `categoryId` state; `delete` shows `bootbox.confirm` before `destroyRecord`; `cancel` branches on `host.isNew`; `cantSave` is a computed gate. None is covered by any JavaScript test in the diff.
```suggestion
moduleForComponent('embeddable-host', 'EmbeddableHost component', {
  needs: ['component:d-button']
});

test('cantSave is true when host is empty', function(assert) {
  const component = this.subject();
  component.set('buffered', Ember.Object.create({ host: '' }));
  assert.equal(component.get('cantSave'), true);
});

test('cancel on new host sends deleteHost action', function(assert) {
  assert.expect(1);
  const component = this.subject();
  const mockHost = Ember.Object.create({ isNew: true });
  component.set('host', mockHost);
  component.set('deleteHost', (host) => { assert.equal(host, mockHost); });
  component.send('cancel');
});

test('cancel on existing host rolls back buffer', function(assert) {
  const component = this.subject();
  component.set('host', Ember.Object.create({ isNew: false }));
  component.set('editToggled', true);
  component.send('cancel');
  assert.equal(component.get('editToggled'), false);
});
```

:red_circle: [cross-file-impact] category_fabricator.rb and embeddable_host_fabricator.rb content was swapped in spec/fabricators/category_fabricator.rb:1 (confidence: 90)
The PR replaced all content of `spec/fabricators/category_fabricator.rb` with a single `Fabricate(:embeddable_host)` definition. The four previously-defined category fabricators (`:category`, `:diff_category`, `:happy_category`, `:private_category`) were moved to the newly-created `spec/fabricators/embeddable_host_fabricator.rb`. Fabricator auto-loads all files in `spec/fabricators/`, so this works at runtime — but the naming is inverted. Any test using explicit `require_fabricator 'category'` will fail, and future maintainers will be confused looking for `:category` in `category_fabricator.rb`.
```suggestion
# Reverse the swap:
# spec/fabricators/category_fabricator.rb should contain Fabricator(:category), Fabricator(:diff_category),
# Fabricator(:happy_category), Fabricator(:private_category).
# spec/fabricators/embeddable_host_fabricator.rb should contain Fabricator(:embeddable_host).
```

:red_circle: [security] SQL injection via string interpolation in migration INSERT in db/migrate/20150818190757_create_embeddable_hosts.rb:25 (confidence: 85)
The migration builds an INSERT using raw Ruby string interpolation: `execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES ('#{h}', #{category_id}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"`. The value `h` is read from the `site_settings` table. While site settings are admin-controlled, any value containing a single quote causes a SQL syntax error or, if crafted, SQL injection during migration. Migrations run with elevated DB privileges. The raw INSERT also bypasses the model's host regex validation.
```suggestion
records.each do |h|
  next unless h.present?
  quoted_host = ActiveRecord::Base.connection.quote(h)
  execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES (#{quoted_host}, #{category_id}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
end
```
[References: https://owasp.org/Top10/A03_2021-Injection/, https://cwe.mitre.org/data/definitions/89.html]

## Improvements

:yellow_circle: [cross-file-impact] EmbeddableHost.record_for_host returns false on invalid URL — eh.try(:category_id) silently drops category in app/models/topic_embed.rb:35 (confidence: 85)
`EmbeddableHost.record_for_host` returns `false` (not `nil`) on invalid URL. In `topic_embed.rb`, `false.try(:category_id)` returns `nil` (Rails `Object#try` returns nil if method is undefined on the object). So unmatched embed URLs silently receive `category: nil`, whereas previously `SiteSetting.embed_category` was the explicit fallback. Depending on PostCreator's handling, posts may be created in uncategorized or fail validation.
```suggestion
eh = EmbeddableHost.record_for_host(url)
category_id = eh ? eh.category_id : SiteSetting.uncategorized_category_id
# Also consider standardizing record_for_host to return nil rather than false so eh&.category_id works idiomatically.
```

:yellow_circle: [testing] EmbeddableHost model spec missing validation and nil-input edge cases in spec/models/embeddable_host_spec.rb:1 (confidence: 88)
The spec tests protocol/path trimming for valid inputs and `host_allowed?` for known good/bad hosts. Missing: `record_for_host` with a completely invalid URI string (the `rescue` branch), `record_for_host` with `nil` input, `host_allowed?` with `nil`, validation with blank/invalid host format, and port-number host values (`example.com:8080`).
```suggestion
describe 'validations' do
  it 'is invalid with a blank host' do
    expect(EmbeddableHost.new(host: '')).not_to be_valid
  end
  it 'is invalid with a non-hostname string' do
    expect(EmbeddableHost.new(host: 'not a host!!')).not_to be_valid
  end
  it 'is valid with a host:port' do
    expect(EmbeddableHost.new(host: 'example.com:8080')).to be_valid
  end
end

describe '.record_for_host' do
  it 'returns falsey for a completely invalid URI' do
    expect(EmbeddableHost.record_for_host('not a url at all $$')).to be_falsey
  end
  it 'returns falsey for nil input' do
    expect(EmbeddableHost.record_for_host(nil)).to be_falsey
  end
end

describe '.host_allowed?' do
  it 'returns false for nil' do
    expect(EmbeddableHost.host_allowed?(nil)).to eq(false)
  end
end
```

:yellow_circle: [consistency] Inconsistent model name format in store.createRecord() call in app/assets/javascripts/admin/controllers/admin-embedding.js.es6:13 (confidence: 85)
The controller uses `this.store.createRecord('embeddable-host')` (hyphenated), but the Rails serializer root is `embeddable_host` (snake_case). Discourse's store typically dasherizes types, so this likely works by convention — but the format should be explicitly verified or documented so a future store refactor doesn't silently break it.
```suggestion
// If 'embeddable-host' is the expected Discourse store convention, add a brief comment linking to
// the dasherize normalization in discourse/adapters/rest.js.es6 (basePath).
// If the store expects snake_case to match the serializer root, change to:
//   this.store.createRecord('embeddable_host')
const host = this.store.createRecord('embeddable-host');
```

## Risk Metadata
Risk Score: 64/100 (HIGH) | Blast Radius: store.js.es6 + rest.js.es6 are foundational frontend files consumed widely; core Rails models (topic, topic_embed, site_setting) touched | Sensitive Paths: db/migrate/20150818190757_create_embeddable_hosts.rb (matches *migration*), embed_controller.rb (access-control gate changed)
AI-Authored Likelihood: LOW

(6 additional findings below confidence threshold)
