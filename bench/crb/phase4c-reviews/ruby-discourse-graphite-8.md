## Summary
16 files changed, 389 lines added, 283 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
Refactor of Discourse group-membership CRUD: replaces `update_patch`/`update_put` with dedicated `add_members`/`remove_member` admin actions, paginates the public members endpoint, and rewires the Ember UI. One breaking change to a public API response shape, one off-by-one in the new paginator, and one misleading error branch in the Rails controllers.

## Critical
:red_circle: [cross-file-impact] Breaking response-shape change on public `GET /groups/:group_id/members` in app/controllers/groups_controller.rb:20 (confidence: 90)
The public (non-admin) members endpoint previously returned a flat JSON array (`render_serialized(members.to_a, GroupUserSerializer)`). The new code wraps the payload in an object `{ members: [...], meta: { total, limit, offset } }`. Any external API consumer, mobile client, or plugin that parses this endpoint expecting an array — including browsers with cached JS bundles immediately after deploy — will break. The internal Ember callers in this PR are updated (`group.js` reads `result.members` / `result.meta.*`), but the route is public and not versioned, and the in-tree JS template `discourse/templates/group/members.hbs` iterates over the `members` property of the *group model*, which only reflects the new shape because `findMembers` was also updated. Nothing else in the diff bumps a client version, adds a deprecation header, or preserves the old contract.
```suggestion
    # Preserve the public array contract; surface pagination via response headers
    # instead of changing the JSON root, or expose the new shape under
    # /groups/:group_id/members.json?paginated=1 (or a new route) until clients migrate.
    response.headers["X-Total"]  = total.to_s
    response.headers["X-Limit"]  = limit.to_s
    response.headers["X-Offset"] = offset.to_s
    render_serialized(members, GroupUserSerializer)
```
[References: Discourse public JSON API — any change to a top-level response shape on an unversioned, publicly reachable `/groups/*` endpoint is a breaking change for third-party consumers.]

## Improvements
:yellow_circle: [correctness] `totalPages` off-by-one when `user_count` is an exact multiple of `limit` in app/assets/javascripts/admin/controllers/admin-group.js.es6:17 (confidence: 95)
`totalPages` is computed as `Math.floor(user_count / limit) + 1`. For `user_count = 50, limit = 50` this returns `2`, and for `user_count = 100, limit = 50` it returns `3` — one more page than actually exists. The UI will display "1/2" on the only real page of a 50-member group and allow `next` to be pressed (though the `next` action's `Math.min(offset + limit, user_count)` clamp prevents an out-of-range fetch, so the symptom is a phantom empty page rather than a crash). The correct formula is `Math.ceil`.
```suggestion
  totalPages: function() {
    if (this.get("user_count") == 0) { return 0; }
    return Math.ceil(this.get("user_count") / this.get("limit"));
  }.property("limit", "user_count"),
```

:yellow_circle: [correctness] `group.save` after membership mutation renders a misleading error in app/controllers/admin/groups_controller.rb:82 (confidence: 86)
Both `add_members` and `remove_member` mutate the join table directly via `group.add(user)` (which calls `users << user`, persisting the `group_users` row on append) and `group.users.delete(user_id)` (which issues an immediate `DELETE` on the join record). By the time control reaches `if group.save`, no attributes on `group` are dirty, so `save` is a no-op on the happy path. However, if `Group` validations ever regress to fail on an unmodified record (e.g., a newly-added validator on an existing attribute), `save` returns `false` and the endpoint returns `render_json_error(group)` — but the membership change has already been committed. The client then sees a 422 and has no way to know the add/remove actually succeeded. Either drop the `save` (rely on the persisted join) or wrap the mutation and save in a transaction so both can be rolled back together.
```suggestion
  def add_members
    group = Group.find(params.require(:group_id).to_i)
    usernames = params.require(:usernames)

    return can_not_modify_automatic if group.automatic

    Group.transaction do
      usernames.split(",").each do |username|
        if user = User.find_by_username(username)
          group.add(user)
        end
      end
    end

    render json: success_json
  end

  def remove_member
    group = Group.find(params.require(:group_id).to_i)
    user_id = params.require(:user_id).to_i

    return can_not_modify_automatic if group.automatic

    group.users.delete(user_id)
    render json: success_json
  end
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: public `/groups/:group_id/members` API + admin routes + Ember model shared by public and admin UI | Sensitive Paths: app/controllers/admin/, config/routes.rb
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold: `findMembers` early-return now returns `undefined` instead of `Ember.RSVP.resolve([])` — benign for in-tree callers but breaks external `.then()` chains; `add_members` calls `usernames.split(",")` without type-checking, so a client that sends a JSON array instead of a comma-separated string raises `NoMethodError` → 500.)
