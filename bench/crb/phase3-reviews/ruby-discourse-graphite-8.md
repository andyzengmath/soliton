## Summary
16 files changed, ~389 lines added, ~283 lines deleted. 9 findings (1 critical, 5 improvements, 3 nitpicks).
Group-membership refactor introduces dedicated add/remove endpoints and pagination, but a route/controller parameter mismatch breaks the new endpoints in production, and the pagination math is off by one.

## Critical

:red_circle: [correctness] Route param is `:id`, controller requires `:group_id` — `add_members`/`remove_member` raise `ParameterMissing` in production in `config/routes.rb:49-50` (confidence: 88)

In `config/routes.rb`, the new member routes are declared inside `resources :groups` but **outside** a `collection do` / `member do` block:

```ruby
resources :groups, except: [:new, :edit] do
  collection do
    post "refresh_automatic_groups" => "groups#refresh_automatic_groups"
  end
  delete "members" => "groups#remove_member"
  put    "members" => "groups#add_members"
end
```

Rails treats bare verb routes inside a `resources` block as **member** routes, generating `DELETE /admin/groups/:id/members` / `PUT /admin/groups/:id/members` where the URL parameter is `:id` (the resource's own id), not `:group_id` (which is the name Rails uses only for nested `resources`).

However `app/controllers/admin/groups_controller.rb` does:

```ruby
def add_members
  group = Group.find(params.require(:group_id).to_i)
  ...
end

def remove_member
  group = Group.find(params.require(:group_id).to_i)
  ...
end
```

Hitting either endpoint from the new JS client (`app/assets/javascripts/discourse/models/group.js` calls `/admin/groups/' + this.get('id') + '/members.json'`) will raise `ActionController::ParameterMissing: param is missing or the value is empty: group_id` before the controller body runs. The feature is end-to-end broken.

The existing `spec/controllers/admin/groups_controller_spec.rb` does **not** catch this because `xhr :put, :add_members, group_id: group.id, ...` injects params directly and bypasses the router.

```suggestion
resources :groups, except: [:new, :edit] do
  collection do
    post "refresh_automatic_groups" => "groups#refresh_automatic_groups"
  end
  member do
    delete "members" => "groups#remove_member"
    put    "members" => "groups#add_members"
  end
end
```

…and in the controller change `params.require(:group_id)` to `params.require(:id)` (or accept either). Add a request-spec (not just a controller spec) that exercises the real URL.

## Improvements

:yellow_circle: [correctness] `totalPages` is off-by-one in `app/assets/javascripts/admin/controllers/admin-group.js.es6:17-20` (confidence: 92)

```js
totalPages: function() {
  if (this.get("user_count") == 0) { return 0; }
  return Math.floor(this.get("user_count") / this.get("limit")) + 1;
}.property("limit", "user_count"),
```

When `user_count == limit` (e.g. 50 users, limit 50) this returns `floor(50/50) + 1 == 2`, but there is only one page. The user will see an enabled "next" arrow and an empty page 2, and `showingLast` becomes incorrect (`1 !== 2`). Use ceiling division instead:

```suggestion
totalPages: function() {
  var count = this.get("user_count"), limit = this.get("limit");
  if (count == 0 || !limit) { return 0; }
  return Math.ceil(count / limit);
}.property("limit", "user_count"),
```

:yellow_circle: [correctness] `findMembers` breaks its promise contract for empty names in `app/assets/javascripts/discourse/models/group.js:16` (confidence: 85)

```js
findMembers: function() {
  if (Em.isEmpty(this.get('name'))) { return ; }
  ...
}
```

The pre-change implementation returned `Ember.RSVP.resolve([])` for this branch. Callers treat the return value as a promise — e.g. the `next`/`previous` actions in `admin-group.js.es6` do `return group.findMembers();`, and the action pipeline / any future `.then` chaining on the result now throws `Cannot read property 'then' of undefined` when `name` happens to be empty (e.g. during model setup race conditions on a brand-new group). Also, the bare `return ;` with no value is lint-ugly.

```suggestion
findMembers: function() {
  if (Em.isEmpty(this.get('name'))) { return Ember.RSVP.resolve(); }
  ...
}
```

:yellow_circle: [correctness] `group.users.delete(user_id)` + `group.save` combination is both redundant and fragile in `app/controllers/admin/groups_controller.rb:93-98` (confidence: 78)

```ruby
def remove_member
  group = Group.find(params.require(:group_id).to_i)
  user_id = params.require(:user_id).to_i

  return can_not_modify_automatic if group.automatic

  group.users.delete(user_id)

  if group.save
    render json: success_json
  else
    render_json_error(group)
  end
end
```

Two issues:
1. `group.users.delete(user_id)` already persists the removal (deletes the join row) and does not require a subsequent `group.save` to commit. The `if group.save` guard therefore cannot undo a failed delete — removal always happens regardless of the branch.
2. Passing a raw integer to `CollectionProxy#delete` works in modern Rails, but is inconsistent with the rest of the codebase which uses `group.remove(user)` (see the old `update_patch` path that this PR deletes). Prefer the symmetric method:

```suggestion
  user = User.find(user_id)
  group.remove(user)
  render json: success_json
```

:yellow_circle: [correctness] Silently ignoring unknown usernames in `add_members` in `app/controllers/admin/groups_controller.rb:79-91` (confidence: 70)

```ruby
usernames.split(",").each do |username|
  if user = User.find_by_username(username)
    group.add(user)
  end
end
```

When an admin pastes five usernames and three are typos, the request returns `success_json` with no indication of which users were skipped. The old code had the same behaviour (`succeeds silently when adding non-existent users` is an explicit spec), but the new UI doesn't surface the count of actual adds either. Consider returning `{ added: [...], skipped: [...] }` so the client can notify the admin. If keeping silent is deliberate, at minimum preserve a test asserting it.

:yellow_circle: [testing] New behaviours are covered only by controller specs that bypass routing in `spec/controllers/admin/groups_controller_spec.rb:148-176` (confidence: 80)

The `.add_members` and `.remove_member` contexts never assert that the route exists or that the real URL reaches the action. Because the critical routing bug above is invisible to `xhr :put, :add_members, group_id: ...`, there is no regression barrier. Add a `spec/requests/admin/groups_spec.rb` (or equivalent integration spec) that hits `put "/admin/groups/#{group.id}/members"` and asserts a 2xx, and a `spec/routing/admin/groups_routing_spec.rb` that pins down the generated param name.

## Nitpicks

:white_circle: [consistency] `{{each member in members itemView="group-member"}}` uses the non-block, implicitly-self-closing form in `app/assets/javascripts/admin/templates/group.hbs:170` (confidence: 55)

This form is valid in the Ember/Handlebars vintage this codebase targets, but the rest of the templates in this diff consistently use `{{#each ... }} ... {{/each}}` with explicit blocks. Aligning styles will avoid future confusion and help a later Ember upgrade.

:white_circle: [consistency] Missing/stray semicolons and trailing blank lines in `app/assets/javascripts/discourse/models/group.js:32`, `app/assets/javascripts/admin/routes/admin_group_route.js:14` (confidence: 60)

- `addMembers` ends with `})` — no terminating semicolon.
- `destroy` early-return reads `if (!this.get('id')) { return };` (semicolon outside the brace).
- `admin_group_route.js` gains a gratuitous trailing blank line inside `extend({ ... })`.

Trivial, but the file-wide style elsewhere is ASI-conscious.

:white_circle: [consistency] `asJSON` no longer wraps under `group:` in `app/assets/javascripts/discourse/models/group.js:44-50` (confidence: 65)

Controller and client were updated in lockstep (`params[:name]` instead of `params[:group][:name]`), but any third-party/admin plugin posting to `/admin/groups` with the old `{ group: { ... } }` shape will now silently create a group with a blank `name`. If public plugin compatibility matters here, log a deprecation warning on receiving the legacy shape for one release.

## Risk Metadata
Risk Score: 62/100 (MEDIUM) | Blast Radius: admin group membership CRUD (16 files, JS + Ruby + routes + tests, cross-layer) | Sensitive Paths: `app/controllers/admin/*`, `config/routes.rb`
AI-Authored Likelihood: LOW (idiomatic 2014-era Discourse/Ember patterns; hand-rolled diff style)
