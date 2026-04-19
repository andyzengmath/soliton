## Summary
22 files changed, 340 lines added, 157 lines deleted. 9 findings (4 critical, 4 improvements, 1 nitpick).
Replaces the `embeddable_hosts` / `embed_category` site settings with a new `EmbeddableHost` ActiveRecord model and admin UI; the migration path and a couple of controller actions have nil-handling and SQL-injection hazards, and the topic-import path silently loses its category fallback.

## Critical

:red_circle: [correctness] Migration crashes when `embed_category` site_setting row is missing in `db/migrate/20150818190757_create_embeddable_hosts.rb`:9 (confidence: 95)
`execute("SELECT c.id ... WHERE s.name = 'embed_category'")[0]['id'].to_i` raises `NoMethodError: undefined method '[]' for nil:NilClass` on any database where the `embed_category` row was never inserted (fresh installs, installs that never configured embedding). The migration aborts before the later fallback branch can run, so `db:migrate` fails for a large class of existing deployments.
```suggestion
    rows = execute("SELECT c.id FROM categories AS c
                    INNER JOIN site_settings AS s ON s.value = c.name
                    WHERE s.name = 'embed_category'")
    category_id = rows.ntuples > 0 ? rows[0]['id'].to_i : 0

    if category_id == 0
      fallback = execute("SELECT value FROM site_settings WHERE name = 'uncategorized_category_id'")
      category_id = fallback.ntuples > 0 ? fallback[0]['value'].to_i : SiteSetting.uncategorized_category_id
    end
```

:red_circle: [security] SQL injection via unescaped interpolation of user-controlled site setting in `db/migrate/20150818190757_create_embeddable_hosts.rb`:22 (confidence: 90)
`execute "INSERT INTO embeddable_hosts (host, category_id, ...) VALUES ('#{h}', #{category_id}, ...)"` interpolates each host string straight from the `embeddable_hosts` site_settings value. That value was historically editable by any admin via the settings UI, so a host entry containing a single quote (accidental or malicious — e.g. `evil.com','1'); DROP TABLE ...--`) breaks out of the literal and runs arbitrary SQL inside the schema migration.
```suggestion
            quoted_host = ActiveRecord::Base.connection.quote(h)
            execute "INSERT INTO embeddable_hosts (host, category_id, created_at, updated_at) VALUES (#{quoted_host}, #{category_id.to_i}, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
```

:red_circle: [correctness] `update` and `destroy` dereference a nil record when the id is unknown in `app/controllers/admin/embeddable_hosts_controller.rb`:9 (confidence: 95)
Both actions do `host = EmbeddableHost.where(id: params[:id]).first` and then call `host.host = ...` / `host.destroy` unconditionally. A stale client id, a concurrent delete, or any crafted request with a non-existent id raises `NoMethodError` and returns a 500 instead of a clean 404.
```suggestion
  def update
    host = EmbeddableHost.find_by(id: params[:id])
    return render json: failed_json, status: 404 unless host
    save_host(host)
  end

  def destroy
    host = EmbeddableHost.find_by(id: params[:id])
    return render json: failed_json, status: 404 unless host
    host.destroy
    render json: success_json
  end
```

:red_circle: [correctness] Topic embed silently loses its category fallback in `app/models/topic_embed.rb`:36 (confidence: 90)
The previous code always assigned `category: SiteSetting.embed_category`, which was validated to be a real category. The new code passes `category: eh.try(:category_id)`, so when `record_for_host(url)` returns nil (race with host deletion, case/www mismatch, URL that arrives without a scheme so `URI(host).host` is nil) the post is created with `category: nil`, landing in the default/uncategorized bucket with no audit trail and no site-wide way to redirect those imports.
```suggestion
        eh = EmbeddableHost.record_for_host(url)
        category_id = eh&.category_id || SiteSetting.uncategorized_category_id

        creator = PostCreator.new(user,
                                  title: title,
                                  raw: absolutize_urls(url, contents),
                                  skip_validations: true,
                                  cook_method: Post.cook_methods[:raw_html],
                                  category: category_id)
```

## Improvements

:yellow_circle: [correctness] `type.replace('_', '-')` only replaces the first underscore in `app/assets/javascripts/discourse/adapters/rest.js.es6`:4 (confidence: 90)
`String.prototype.replace` with a string pattern replaces only the first match, so a type such as `site_text_type` becomes `site-text_type` and fails the `ADMIN_MODELS.indexOf` check. Any future admin model whose name has more than one underscore silently bypasses the `/admin/` base path.
```suggestion
    if (ADMIN_MODELS.indexOf(type.replace(/_/g, '-')) !== -1) { return "/admin/"; }
```

:yellow_circle: [correctness] `_hydrateEmbedded` plural branch dereferences `obj[k]` without a type guard in `app/assets/javascripts/discourse/models/store.js.es6`:193 (confidence: 80)
When the regex matches `foo_ids` the code unconditionally calls `obj[k].map(...)`. If the server ever omits the key, sends `null`, or sends a scalar (single-id shorthand), the client throws `TypeError: Cannot read property 'map' of null` during hydration and the whole payload fails. Serializers elsewhere in Discourse emit `_ids` keys that can be null or missing, so this is reachable without changing the server.
```suggestion
        if (m[2]) {
          const ids = obj[k];
          const hydrated = Array.isArray(ids)
            ? ids.map(id => self._lookupSubType(subType, type, id, root))
            : [];
          obj[self.pluralize(subType)] = hydrated;
          delete obj[k];
        } else {
```

:yellow_circle: [correctness] Host validation regex rejects valid TLDs in `app/models/embeddable_host.rb`:2 (confidence: 85)
The `[a-z]{2,5}` segment rejects common TLDs like `.museum`, `.agency`, `.technology`, and virtually every new gTLD, so sites with those domains cannot add themselves as embeddable hosts and the admin UI reports a generic validation error. IPv4/IPv6 literals and hostnames without a dot (internal staging hosts) are also rejected.
```suggestion
  validates_format_of :host, :with => /\A[a-z0-9]+([\-\.]{1}[a-z0-9]+)*\.[a-z]{2,}(:[0-9]{1,5})?(\/.*)?\Z/i
```

:yellow_circle: [correctness] `embedding#update` is a no-op that the UI already calls in `app/controllers/admin/embedding_controller.rb`:9 (confidence: 75)
The action re-serialises `@embedding` without persisting anything, but the admin controller exposes `saveChanges()` which calls `this.get('embedding').update({})`. Nothing on the `embedding` resource is actually editable (hosts have their own REST resource), so the button does nothing and silently returns success — either wire it to real settings or remove the action and the button so operators don't think their change was saved.
```suggestion
  def update
    # If there are no top-level embedding settings to persist, drop this action
    # and the `saveChanges` UI affordance. Otherwise, assign and save here.
    render json: failed_json, status: 404
  end
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 22 files, 340 add / 157 del, touches auth-adjacent embed path + schema migration | Sensitive Paths: db/migrate/20150818190757_create_embeddable_hosts.rb, app/controllers/admin/embeddable_hosts_controller.rb, app/controllers/admin/embedding_controller.rb
AI-Authored Likelihood: LOW

(1 additional finding below confidence threshold: `spec/fabricators/category_fabricator.rb` and `spec/fabricators/embeddable_host_fabricator.rb` appear to have had their contents swapped — file names no longer match the fabricators they declare.)
