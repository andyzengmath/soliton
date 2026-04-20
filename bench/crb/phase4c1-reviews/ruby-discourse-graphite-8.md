## Summary
16 files changed, 389 lines added, 283 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
Group-membership refactor is solid overall, but a `totalPages` off-by-one makes the paginator render a phantom empty last page and keeps the "next" button enabled at the real end of the list.

## Critical

:red_circle: [correctness] `totalPages` off-by-one produces an extra empty page in app/assets/javascripts/admin/controllers/admin-group.js.es6:17 (confidence: 90)
`totalPages` is computed as `Math.floor(user_count / limit) + 1`. When `user_count` is an exact multiple of `limit` this returns one page more than actually exists (e.g. 50 users at `limit: 50` → `totalPages == 2`). Because `showingLast` is `Discourse.computed.propertyEqual("currentPage", "totalPages")`, the user can still click "next" after reaching the real last page; `findMembers` then issues a request with `offset == user_count`, which the server clamps and returns an empty `members` array, emptying the UI. The correct formula is `Math.ceil(user_count / limit)`.
```suggestion
  totalPages: function() {
    if (this.get("user_count") == 0) { return 0; }
    return Math.ceil(this.get("user_count") / this.get("limit"));
  }.property("limit", "user_count"),
```

## Improvements

:yellow_circle: [correctness] `findMembers` silently returns `undefined` when name is empty, breaking its promise contract in app/assets/javascripts/discourse/models/group.js:15 (confidence: 85)
Previously `findMembers` returned `Ember.RSVP.resolve([])` for an unnamed group, so every caller could safely chain `.then(...)`. The new body is `if (Em.isEmpty(this.get('name'))) { return ; }`, returning `undefined`. The in-tree callers (`admin_group_route.js`, `group-members.js.es6`, `addMembers`, `removeMember`) now invoke it without `.then`, which hides the problem, but any future or external consumer chaining `.then` on the returned value will crash with `Cannot read property 'then' of undefined`. Keep the promise contract.
```suggestion
  findMembers: function() {
    if (Em.isEmpty(this.get('name'))) { return Ember.RSVP.resolve([]); }
```

:yellow_circle: [correctness] `addMembers` and `removeMember` have no failure handling, leaving the UI out of sync with the server in app/assets/javascripts/discourse/models/group.js:40 (confidence: 85)
Both methods call `self.findMembers()` only inside the success continuation and have no `.catch`/error handler. If the server returns a non-2xx (422 for automatic groups, 403 for permission, 500 on a stray error) the promise rejects, the bubbling `bootbox.confirm`/button action swallows it, and the local `members` array is never refreshed. For `removeMember` this is particularly bad: the admin clicks the remove icon, gets a silent failure, and the member still appears in the list — reinforcing the belief they have been removed. Surface the error (bootbox/popup/flash) and either rollback optimistic state or force a `findMembers()` refresh in a rejection handler.
```suggestion
  removeMember: function(member) {
    var self = this;
    return Discourse.ajax('/admin/groups/' + this.get('id') + '/members.json', {
      type: "DELETE",
      data: { user_id: member.get("id") }
    }).then(function() {
      self.findMembers();
    }, function() {
      bootbox.alert(I18n.t('generic_error'));
    });
  },
```

:yellow_circle: [testing] automatic-group membership specs assume a persisted group with `id: 1` exists and is automatic in spec/controllers/admin/groups_controller_spec.rb:118 (confidence: 85)
The two "cannot (add|remove) members to automatic groups" examples call `xhr :put, :add_members, group_id: 1, usernames: "l77t"` and `xhr :put, :remove_member, group_id: 1, user_id: 42` and assert `response.status.should == 422`. The controller does `Group.find(params.require(:group_id).to_i)` *before* the `automatic` check, so if no record with `id == 1` exists (or it exists but is not automatic) the example fails with `ActiveRecord::RecordNotFound` (404/500) or a misleading 200, not 422 for the reason the test name claims. This only works today because Discourse seeds trust-level automatic groups with low ids; any change to seed order or DatabaseCleaner strategy silently breaks the guarantee. Fabricate an explicit automatic group inside the example and use its `id`, matching the pattern already used in the `.destroy` specs.
```suggestion
    it "cannot add members to automatic groups" do
      group = Fabricate(:group, automatic: true)
      xhr :put, :add_members, group_id: group.id, usernames: "l77t"
      response.status.should == 422
    end
```

## Risk Metadata
Risk Score: 42/100 (MEDIUM) | Blast Radius: MEDIUM — 16 files across admin controllers, JS model, templates, routes, i18n; touches group-membership write paths with new REST endpoints | Sensitive Paths: app/controllers/admin/groups_controller.rb (admin authorization), config/routes.rb (new PUT/DELETE endpoints)
AI-Authored Likelihood: LOW
