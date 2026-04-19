## Summary
16 files changed, 389 lines added, 283 lines deleted. 11 findings (5 critical, 6 improvements, 0 nitpicks).
Group-membership refactor has correctness and security regressions in the new admin add/remove endpoints and untested behavior changes on the public members API.

## Critical
:red_circle: [correctness] findMembers early-exit returns undefined instead of a Promise in app/assets/javascripts/discourse/models/group.js:255 (confidence: 92)
The bare `return;` when name is empty replaces `return Ember.RSVP.resolve([])`, so any caller writing `model.findMembers().then(...)` throws `TypeError: Cannot read property 'then' of undefined`. The function's return type is now inconsistently `Promise | undefined`, a latent runtime crash for any current or future `.then()` caller.
```suggestion
  findMembers: function() {
    if (Em.isEmpty(this.get('name'))) { return Ember.RSVP.resolve(); }
```

:red_circle: [correctness] remove_member passes integer user_id to group.users.delete in app/controllers/admin/groups_controller.rb:649 (confidence: 88)
`group.users.delete(user_id)` passes a raw integer from `params.require(:user_id).to_i` to ActiveRecord's CollectionProxy, which expects a model instance — in Rails 3/4 this silently fails to remove the join-table row, so the endpoint returns 200 while the member is still in the group. Worse, `.to_i` coerces garbage/missing input to `0`, so malformed requests also succeed silently.
```suggestion
    user = User.find_by(id: user_id)
    return render_json_error(I18n.t("user.not_found")) unless user
    group.remove(user)
    render json: success_json
```
<details><summary>More context</summary>

The old `update_patch` code used `User.find_by_username(username); group.remove(user)` — the new endpoint abandons that pattern. Note also that the subsequent `if group.save` is unnecessary because collection `delete` already writes to the join table; it just costs a spurious UPDATE. Consider `group.group_users.where(user_id: user_id).destroy_all` if you want a direct, auditable DELETE.
</details>

:red_circle: [security] add_members performs unbounded N-query iteration on comma-separated input in app/controllers/admin/groups_controller.rb:622 (confidence: 85)
The new action reads `params.require(:usernames)` as a string and does `usernames.split(",").each { User.find_by_username; group.add }` with no length cap, dedup, or type check. A single request with thousands of usernames causes thousands of synchronous DB queries (worker DoS, join-table lock contention) and enables bulk enrollment into permission-granting groups in one call; a JSON client sending an Array instead of a String raises a 500 on `.split`.
```suggestion
  MAX_USERNAMES_PER_REQUEST = 50

  def add_members
    group = Group.find(params.require(:group_id).to_i)
    return can_not_modify_automatic if group.automatic

    raw = params.require(:usernames)
    names = (raw.is_a?(String) ? raw.split(",") : Array(raw))
              .map { |n| n.to_s.strip }.reject(&:blank?).uniq
    return render_json_error("Too many usernames") if names.size > MAX_USERNAMES_PER_REQUEST

    User.where(username_lower: names.map(&:downcase)).find_each { |u| group.add(u) }
    group.save ? render(json: success_json) : render_json_error(group)
  end
```
[References: https://cwe.mitre.org/data/definitions/770.html, https://owasp.org/Top10/A04_2021-Insecure_Design/]

:red_circle: [testing] Public GroupsController#members rewrite has zero test coverage in app/controllers/groups_controller.rb:19 (confidence: 97)
The action now always paginates (previously only automatic groups did), default limit changed from 200 to 50, and the response shape changed from a plain serialized array to `{ members, meta: {total, limit, offset} }` — but no spec in this PR exercises any of it. Any external consumer expecting the old array response silently breaks and the new boundary behavior is unverified.
```suggestion
# spec/controllers/groups_controller_spec.rb (new file)
describe GroupsController do
  describe "GET members" do
    it "returns members with meta" do
      group = Fabricate(:group, visible: true)
      group.add(Fabricate(:user)); group.save
      xhr :get, :members, group_id: group.name
      response.status.should == 200
      json = JSON.parse(response.body)
      json["meta"].should == { "total" => 1, "limit" => 50, "offset" => 0 }
      json["members"].length.should == 1
    end

    it "paginates with explicit limit and offset" do
      # ...
    end
  end
end
```

:red_circle: [testing] remove_member guard spec sends PUT instead of DELETE — routes to add_members in spec/controllers/admin/groups_controller_spec.rb:952 (confidence: 95)
`remove_member` is routed as `DELETE members`, so `xhr :put, :remove_member, group_id: 1, user_id: 42` actually hits `add_members` — the 422 observed in that test comes from `add_members`' automatic-group guard, so the `remove_member` guard is never exercised. The happy-path test uses `:delete` correctly; only the guard test has the wrong verb.
```suggestion
    it "cannot remove members from automatic groups" do
      xhr :delete, :remove_member, group_id: 1, user_id: 42
      response.status.should == 422
    end
```

## Improvements
:yellow_circle: [correctness] totalPages overcounts by 1 when user_count is an exact multiple of limit in app/assets/javascripts/admin/controllers/admin-group.js.es6:17 (confidence: 95)
`Math.floor(user_count / limit) + 1` yields 2 for 50 users with limit 50, so the UI shows "1/2" with a live "next" arrow; clicking it loads `offset=50` and blanks the list. The formula should be ceiling division.
```suggestion
  totalPages: function() {
    if (this.get("user_count") == 0) { return 0; }
    return Math.ceil(this.get("user_count") / this.get("limit"));
  }.property("limit", "user_count"),
```

:yellow_circle: [testing] add_members spec covers only the happy path in spec/controllers/admin/groups_controller_spec.rb:879 (confidence: 92)
The deleted incremental-API tests explicitly covered "succeeds silently when adding non-existent users" and "succeeds silently when removing non-members" — equivalent behavior is still present in the new action but untested, along with duplicate usernames, blank entries, and mixed valid/invalid lists. Regressions that flip silent-skip to a 500 or double-add would not be caught.
```suggestion
    it "silently skips non-existent usernames" do
      user = Fabricate(:user); group = Fabricate(:group)
      xhr :put, :add_members, group_id: group.id, usernames: "#{user.username},nosuchperson"
      response.should be_success
      group.reload; group.users.count.should == 1
    end

    it "does not double-add an existing member" do
      user = Fabricate(:user); group = Fabricate(:group)
      group.add(user); group.save
      xhr :put, :add_members, group_id: group.id, usernames: user.username
      response.should be_success
      group.reload; group.users.count.should == 1
    end
```

:yellow_circle: [security] Public members endpoint accepts unbounded limit/offset in app/controllers/groups_controller.rb:22 (confidence: 85)
`limit = (params[:limit] || 50).to_i` is not capped, so `?limit=1000000` forces the server to serialize a million rows in one request. Combined with only `guardian.ensure_can_see!`, this also eases full-membership enumeration of any visible group (usernames for credential-stuffing / phishing) far more than the previous 200-row implicit cap.
```suggestion
    MAX_MEMBERS_PER_PAGE = 200
    limit  = [[(params[:limit] || 50).to_i, 1].max, MAX_MEMBERS_PER_PAGE].min
    offset = [params[:offset].to_i, 0].max
```
[References: https://cwe.mitre.org/data/definitions/770.html, https://cwe.mitre.org/data/definitions/799.html]

:yellow_circle: [correctness] findMembers() called fire-and-forget in setupController silently swallows AJAX errors in app/assets/javascripts/admin/routes/admin_group_route.js:126 (confidence: 85)
The previous `afterModel` returned the promise so Ember surfaced rejections via the error route; the new `setupController` discards the promise, so a 403/500/network-error leaves `members` empty with no user-visible feedback. Same discard happens in `discourse/routes/group-members.js.es6`.
```suggestion
  setupController: function(controller, model) {
    controller.set("model", model);
    model.findMembers().catch(function(err) {
      Ember.Logger.error("Failed to load group members", err);
    });
  }
```

:yellow_circle: [correctness] findMembers() called fire-and-forget in setupController silently swallows AJAX errors in app/assets/javascripts/discourse/routes/group-members.js.es6:361 (confidence: 85)
Same pattern as the admin route: `controller.set("model", model); model.findMembers();` discards the promise, so AJAX failures silently leave the members list blank. The template now iterates `members` on the model, so a failure yields an empty page rather than an error state.
```suggestion
  setupController: function(controller, model) {
    this.controllerFor('group').set('showing', 'members');
    controller.set("model", model);
    model.findMembers().catch(function(err) {
      Ember.Logger.error("Failed to load group members", err);
    });
  }
```

:yellow_circle: [consistency] protected method body is double-indented in app/controllers/admin/groups_controller.rb:663 (confidence: 85)
`can_not_modify_automatic` is indented 4 spaces under `protected`, not 2, diverging from the rest of the controller and from Discourse's prevailing Ruby style. Trivial to fix but creates diff noise for future edits.
```suggestion
  protected

  def can_not_modify_automatic
    render json: {errors: I18n.t('groups.errors.can_not_modify_automatic')}, status: 422
  end
```

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: core Group model + admin groups controller, ~18-20 importing files across JS and Rails | Sensitive Paths: none matched (admin auth-relevant but not in glob list)
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold of 85 suppressed: indent of private method in groups_controller.rb, missing strong-parameters in create/update, admin before_filter coverage gap, deletion of PATCH API tests without route-is-dead assertion, new Group model methods have no JS unit tests)
