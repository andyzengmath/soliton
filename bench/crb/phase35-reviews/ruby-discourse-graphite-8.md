## Summary
16 files changed, 389 lines added, 283 lines deleted. 5 findings (0 critical, 5 improvements, 0 nitpicks).
Refactor of group-membership management: splits the old PATCH-based `update` into dedicated `add_members` / `remove_member` endpoints, paginates `/groups/:group_id/members`, and rewrites the admin UI to page / add / remove members inline. The refactor is a meaningful improvement over the `update_patch`/`update_put` branching, but introduces an off-by-one in the client pagination math, several breaking wire-format changes, an unbounded `limit` query parameter, and a render-before-data race in the route setup.

## Improvements

:yellow_circle: [correctness] `totalPages` off-by-one when `user_count` is an exact multiple of `limit` in app/assets/javascripts/admin/controllers/admin-group.js.es6:10 (confidence: 95)
`totalPages` is computed as `Math.floor(user_count / limit) + 1`. For any `user_count` that is an exact multiple of `limit`, this over-counts by one page. Example: with `user_count=50, limit=50`, the UI will show `1/2` and render a non-disabled "next" button that, when clicked, requests `offset=50` and receives an empty page. It also prevents `showingLast` from ever becoming `true` for such groups, because `currentPage` (1) will never equal the inflated `totalPages` (2). The fix is `Math.ceil`, with the `user_count == 0` guard preserved:
```suggestion
  totalPages: function() {
    if (this.get("user_count") == 0) { return 0; }
    return Math.ceil(this.get("user_count") / this.get("limit"));
  }.property("limit", "user_count"),
```

:yellow_circle: [cross-file-impact] `GET /groups/:group_id/members` response shape changed from array to `{members, meta}` — breaks external API consumers in app/controllers/groups_controller.rb:22 (confidence: 90)
The endpoint previously returned a bare JSON array produced by `render_serialized(members.to_a, GroupUserSerializer)`. It now returns `{ members: [...], meta: { total, limit, offset } }`. The frontend in `discourse/models/group.js` is updated to read `result.members` / `result.meta.*`, but any out-of-tree consumers — third-party Discourse plugins, mobile clients, or scripts hitting `/groups/:name/members.json` — will receive a hash where they expect an array and break silently (typical JS clients will iterate no entries; Ruby clients will raise `NoMethodError: undefined method 'each' for Hash`). This is not documented as a breaking change in the PR body ("Test 8"). At minimum, the changelog / API docs need to reflect this, and ideally the old array shape would remain available under a versioned path or `Accept` header.
```suggestion
# Option: preserve backward compatibility by branching on a param or Accept header,
# or document the breaking change explicitly in the PR / release notes.
render json: {
  members: serialize_data(members, GroupUserSerializer),
  meta: { total: total, limit: limit, offset: offset }
}
```

:yellow_circle: [cross-file-impact] `POST /admin/groups` silently drops `group[usernames]` — breaking change for external API callers in app/controllers/admin/groups_controller.rb:22 (confidence: 88)
The old `create` action unwrapped the nested `params[:group]` hash and honored `params[:group][:usernames]`, letting a caller create a group and populate members in one request (covered by the deleted spec `"is able to create a group"` which asserted `groups[0].usernames.should == usernames`). The new implementation reads flat params (`params[:name]`, `params[:visible]`) and ignores any `usernames` input entirely — a caller sending the old payload gets a 200 with an empty group and no error. Two concerns: (1) the param key also changed from `params[:group][:name]` to `params[:name]`, so old clients lose the `name` too; (2) there is no test asserting the new shape rejects or ignores the legacy payload. Either accept both shapes during a deprecation window, or explicitly 400 on the legacy payload so clients fail loudly rather than creating empty groups.
```suggestion
def create
  # Accept both the legacy nested payload and the new flat payload, or
  # explicitly 400 on the legacy shape to surface client breakage.
  name    = (params[:name] || params.dig(:group, :name) || "").strip
  visible = (params[:visible] || params.dig(:group, :visible)) == "true"

  group = Group.new(name: name, visible: visible)
  if group.save
    render_serialized(group, BasicGroupSerializer)
  else
    render_json_error group
  end
end
```

:yellow_circle: [security] `params[:limit]` is unbounded in app/controllers/groups_controller.rb:23 (confidence: 88)
`limit = (params[:limit] || 50).to_i` accepts any integer from the client with no upper bound. An unauthenticated caller (this endpoint lives under `GroupsController`, not `Admin::GroupsController`, and has no visible rate limit) can request `?limit=10000000` and force the server to build, serialize, and ship a large member list for any visible group. Combined with the new `total = group.users.count` (extra `SELECT COUNT(*)`), this is a cheap way to generate load. Cap the limit at a sane value (e.g., 200, matching the legacy automatic-group cap) and clamp `offset` to be non-negative:
```suggestion
limit  = [(params[:limit] || 50).to_i, 200].min
offset = [params[:offset].to_i, 0].max

total   = group.users.count
members = group.users.order(:username_lower).limit(limit).offset(offset)
```

:yellow_circle: [correctness] Render-before-data race: `setupController` now fires `findMembers` without awaiting it in app/assets/javascripts/admin/routes/admin_group_route.js:12 (confidence: 85)
Previously `afterModel` did `return model.findMembers().then(...)`, which gated the route transition on the members load; the template only rendered once members were available. The new `setupController` calls `model.findMembers()` without `return`ing the promise and without an `afterModel` gate, so the template renders immediately with `members` unset. The user sees an empty member list (and `currentPage`/`totalPages` computed from the default `user_count: 0` → `0/0` pager) flicker before the AJAX resolves. The same pattern is repeated in `discourse/routes/group-members.js.es6:9`. Either restore `afterModel` (`return model.findMembers();`) or return the promise from `setupController` so Ember waits on it before rendering.
```suggestion
afterModel: function(model) {
  return model.findMembers();
},

setupController: function(controller, model) {
  controller.set("model", model);
}
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: admin + public group endpoints, public JSON response shape change, 16 files across controllers/models/routes/templates | Sensitive Paths: `app/controllers/admin/*`, `config/routes.rb`
AI-Authored Likelihood: LOW

(3 additional findings below confidence threshold: `Discourse.Group` instance method named `create` shadows Ember's class-level `Ember.Object.create` idiom; `group.users.delete(user_id)` passes an integer to a `CollectionProxy#delete` that may require AR objects depending on Rails version; removed specs for "succeeds silently when adding non-existent users" / "succeeds silently when removing non-members" reduce regression coverage of the silent-noop contract.)
