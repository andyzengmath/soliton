## Summary
16 files changed, 389 lines added, 283 lines deleted. 5 findings (2 critical, 3 improvements).
Refactor of group-membership management splits a monolithic `update` action into dedicated `add_members` / `remove_member` endpoints, adds paginated member listing, and rewrites the admin group UI; introduces an off-by-one in pagination and a likely route/controller param mismatch.

## Critical

:red_circle: [correctness] Pagination `totalPages` off-by-one when `user_count` is a multiple of `limit` in app/assets/javascripts/admin/controllers/admin-group.js.es6:17 (confidence: 95)
`totalPages` is computed as `Math.floor(user_count / limit) + 1`. When `user_count` is a non-zero exact multiple of `limit` the result is one greater than the real number of pages: `user_count=50, limit=50` ⇒ returns 2 (correct: 1); `user_count=100, limit=50` ⇒ returns 3 (correct: 2). Because `showingLast` is `propertyEqual("currentPage", "totalPages")`, the "Next" button stays enabled past the last real page, advancing `offset` beyond `user_count` and rendering an empty member list. Use `Math.ceil` (and guard the zero-`limit` case).
```suggestion
  totalPages: function() {
    var limit = this.get("limit");
    if (this.get("user_count") == 0 || !limit) { return 0; }
    return Math.ceil(this.get("user_count") / limit);
  }.property("limit", "user_count"),
```

:red_circle: [correctness] `params.require(:group_id)` likely doesn't match the generated route param in app/controllers/admin/groups_controller.rb:62 (confidence: 80)
The new routes are declared at the member level of `resources :groups` (`config/routes.rb:49-50`):
```ruby
delete "members" => "groups#remove_member"
put    "members" => "groups#add_members"
```
Custom verb-form routes placed directly inside a `resources` block default to **member** scope and expose the parent id as `:id`, producing paths `/admin/groups/:id/members`. The controller, however, looks the id up via `params.require(:group_id)` in both `add_members` and `remove_member`, which will raise `ActionController::ParameterMissing` at runtime. The frontend URL `'/admin/groups/' + this.get('id') + '/members.json'` (`app/assets/javascripts/discourse/models/group.js`) is consistent with the member-scoped route, so the URL works — only the controller lookup is broken. The new specs (`spec/controllers/admin/groups_controller_spec.rb`) call the actions directly with `group_id: 1`, which bypasses routing and therefore does not catch this. Verify with `rake routes | grep admin/groups` and either change the controller to `params.require(:id)` or move the routes inside `member do … end` with explicit `:group_id` naming, plus add a request/integration spec that exercises the full URL.
```suggestion
  def add_members
    group = Group.find(params.require(:id).to_i)
    usernames = params.require(:usernames)

    return can_not_modify_automatic if group.automatic
    # ...
  end

  def remove_member
    group = Group.find(params.require(:id).to_i)
    user_id = params.require(:user_id).to_i

    return can_not_modify_automatic if group.automatic
    # ...
  end
```

## Improvements

:yellow_circle: [correctness] `findMembers` no longer returns a Promise on empty name in app/assets/javascripts/discourse/models/group.js:17 (confidence: 90)
Old behaviour returned `Ember.RSVP.resolve([])`; the new code uses a bare `return ;`, so callers that chain `.then()` on the result will throw `TypeError: Cannot read property 'then' of undefined`. Today's in-repo callers happen to ignore the return value, but `removeMember`/`addMembers` themselves chain `.then` on the outer ajax promise, and any new caller (or external plugin) that does `group.findMembers().then(...)` against a not-yet-named group will crash. Preserve the old contract.
```suggestion
  findMembers: function() {
    if (Em.isEmpty(this.get('name'))) { return Ember.RSVP.resolve([]); }

    var self = this, offset = Math.min(this.get("user_count"), Math.max(this.get("offset"), 0));
    // ... unchanged ajax/setProperties block
  },
```

:yellow_circle: [testing] Lost coverage for silent-skip of non-existent usernames in spec/controllers/admin/groups_controller_spec.rb:88 (confidence: 85)
The previous `update_patch` test suite covered "succeeds silently when adding non-existent users" and "succeeds silently when removing non-members". The new `add_members` controller still implements that behaviour (`if user = User.find_by_username(username)` skips misses), but the spec was deleted, leaving the silent-skip path untested. Add an explicit spec, otherwise a future refactor that turns the miss into a 404/422 will go unnoticed.
```suggestion
    it "succeeds silently when adding non-existent users" do
      group = Fabricate(:group)
      xhr :put, :add_members, group_id: group.id, usernames: "nosuchperson"
      response.status.should == 200
      group.reload
      group.users.count.should == 0
    end
```

:yellow_circle: [correctness] Redundant `group.save` after associations are already persisted in app/controllers/admin/groups_controller.rb:74 (confidence: 80)
`group.add(user)` (which is `users << user`) and `group.users.delete(user_id)` both write the join row to the database immediately and update `group`'s in-memory association. The subsequent `if group.save … else render_json_error(group)` therefore never sees a validation failure caused by the membership change — it only catches unrelated validation issues on the `Group` record itself, while still incurring an extra UPDATE. Drop the save (and render `success_json` directly) or replace with an explicit `group.errors.empty?` check that actually corresponds to what was just done.
```suggestion
  def add_members
    group = Group.find(params.require(:id).to_i)
    usernames = params.require(:usernames)
    return can_not_modify_automatic if group.automatic

    usernames.split(",").each do |username|
      if user = User.find_by_username(username)
        group.add(user)
      end
    end

    render json: success_json
  end
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 16 files, 672 LOC across admin controller + REST routes + Ember model/controllers/templates + i18n; touches authenticated admin endpoints | Sensitive Paths: app/controllers/admin/, config/routes.rb
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
