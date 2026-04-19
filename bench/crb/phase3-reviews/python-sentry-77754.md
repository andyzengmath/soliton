# PR Review: getsentry/sentry#77754 — fix(ecosystem): Breaks issue sync cycles

**Author:** GabeVillalobos
**State:** MERGED
**Base:** master ← **Head:** gv/fix-jira-cyclic-sync
**Files:** 7 | **+212 / -15**

## Summary
7 files changed, 212 lines added, 15 lines deleted. 11 findings (2 critical, 6 improvements, 3 nitpicks).
The cycle-break design is sound, but the new `AssignmentSource` dataclass ships two latent serialization/default-value bugs that can silently re-enable the exact cycle this PR was written to fix.

## Critical

:red_circle: [correctness] `queued` default evaluated once at class-definition time in `src/sentry/integrations/services/assignment_source.py:16` (confidence: 98)
`queued: datetime = timezone.now()` is evaluated when the class body is imported, not when instances are constructed. Every `AssignmentSource` created via `from_integration` or the bare constructor shares the same timestamp — effectively process-start time. This is the classic Python evaluated-once default gotcha, aggravated by `@dataclass(frozen=True)` which prevents later correction. If `queued` is ever used for ordering, deduplication, or TTL on the cycle-break logic, it will be silently wrong across the entire process lifetime. The new `test_to_dict` asserts `queued is not None`, which passes trivially and masks the bug.
```suggestion
from dataclasses import asdict, dataclass, field
...
@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```

:red_circle: [correctness] `to_dict` / `from_dict` roundtrip broken by Celery JSON serialization in `src/sentry/integrations/services/assignment_source.py:25` (confidence: 95)
`to_dict()` uses `asdict(self)`, which emits `queued` as a `datetime` object. That dict is then passed through `sync_assignee_outbound.apply_async(kwargs=...)`. Celery's default JSON serializer will either refuse the datetime outright or (with a custom encoder) coerce it to an ISO string. On the worker side, `from_dict` does `cls(**input_dict)`, constructing `AssignmentSource(queued="2026-04-19T...")` — a string bound to a field annotated `datetime`. Dataclasses do not runtime-enforce annotations, so either (a) `apply_async` fails to enqueue the task (cycle fix never runs — cycle returns), or (b) the task enqueues but `queued` silently holds a `str`, poisoning any future code that treats it as a datetime. Either path regresses the feature this PR is adding.
```suggestion
def to_dict(self) -> dict[str, Any]:
    d = asdict(self)
    d["queued"] = self.queued.isoformat()
    return d

@classmethod
def from_dict(cls, input_dict: dict[str, Any]) -> AssignmentSource | None:
    try:
        queued_raw = input_dict.get("queued")
        queued = datetime.fromisoformat(queued_raw) if isinstance(queued_raw, str) else timezone.now()
        return cls(
            source_name=str(input_dict["source_name"]),
            integration_id=int(input_dict["integration_id"]),
            queued=queued,
        )
    except (KeyError, ValueError, TypeError):
        return None
```

## Improvements

:yellow_circle: [cross-file-impact] `sync_status_outbound` gained `assignment_source` kwarg but no caller passes it in `src/sentry/integrations/mixins/issues.py:381` (confidence: 90)
The abstract signature of `sync_status_outbound` was widened to accept `assignment_source: AssignmentSource | None = None`, but nothing in this PR threads a real source through the status-sync call chain — only the assignee path is wired end-to-end (`sync_group_assignee_inbound → GroupAssignee.assign/deassign → sync_group_assignee_outbound → sync_assignee_outbound task → installation.sync_assignee_outbound`). The analogous status path (`sync_status_inbound → group status change → sync_status_outbound` task → `installation.sync_status_outbound`) still omits the source. Net effect: *status-sync cycles* (open/resolve ping-pong between Sentry and Jira) are **not actually fixed** by this PR, despite the signature suggesting they are. Either wire it through or drop the parameter until it is plumbed — dead API surface here is actively misleading because the parameter name promises a property the code does not deliver.

:yellow_circle: [cross-file-impact] Existing callers of `GroupAssignee.assign`/`deassign` still omit `assignment_source` in `src/sentry/models/groupassignee.py:137` (confidence: 92)
`assign` and `deassign` gained an optional `assignment_source`, and `sync_group_assignee_inbound` was updated to pass it. Good. But `GroupAssignee.assign/deassign` is called from many non-inbound-integration paths (UI, REST API, auto-assign rules, ownership rules, notification-triggered assignment, plugin-based issue sync) — those keep calling without a source (correct), but any path that represents an **inbound integration event** not routed through `sync_group_assignee_inbound` will still cause cycles. A quick caller audit (`grep -rn "GroupAssignee.objects.assign\|\.deassign(" src/`) would confirm no integration-originated path bypasses the new plumbing.

:yellow_circle: [correctness] `from_dict` silently returns `None` on malformed input, disabling cycle-detection in `src/sentry/integrations/services/assignment_source.py:30` (confidence: 85)
`from_dict` swallows `ValueError`/`TypeError` and returns `None`. The caller in `sync_assignee_outbound` then treats `None` as "no source known" and proceeds with the sync as if the event were user-originated. Any schema drift, typo, or serialization regression will silently re-enable the exact cycle this PR was written to fix, with zero telemetry. Log a warning (keys only, not values) so regressions are observable in metrics/Sentry itself.
```suggestion
except (ValueError, TypeError, KeyError):
    logger.warning(
        "assignment_source.from_dict.invalid",
        extra={"keys": sorted(input_dict.keys())},
    )
    return None
```

:yellow_circle: [testing] Missing positive test: different source integration still propagates in `tests/sentry/models/test_groupassignee.py:173` (confidence: 95)
The new test suite verifies `mock_sync_assignee_outbound.assert_not_called()` when the assignment source matches the target integration (the cycle-break case), but there is no symmetric test asserting that when the source is a **different** integration, outbound sync IS still called. Without it, a regression that over-eagerly skips sync (wrong id compared, `should_sync` always returning `False`, `if sync_source` inverted, etc.) would pass CI — and this is the higher-risk failure mode because it silently breaks cross-integration sync entirely rather than reintroducing a loop.
```suggestion
@mock.patch.object(ExampleIntegration, "sync_assignee_outbound")
def test_assignee_sync_outbound_assign_with_different_source_integration(self, mock_sync_assignee_outbound):
    # Target integration
    target = self.create_integration(organization=self.group.organization, external_id="target", provider="example", oi_params={...})
    # Source integration (different id)
    source = self.create_integration(organization=self.group.organization, external_id="source", provider="example2", oi_params={...})
    # ... create ExternalIssue + GroupLink against `target` ...
    with self.feature({"organizations:integrations-issue-sync": True}), self.tasks():
        GroupAssignee.objects.assign(
            self.group, self.user,
            assignment_source=AssignmentSource.from_integration(source),
        )
        mock_sync_assignee_outbound.assert_called_once()
```

:yellow_circle: [testing] `test_to_dict` asserts on `queued` which is the class-level evaluated-once default in `tests/sentry/integrations/services/test_assignment_source.py:30` (confidence: 88)
`assert result.get("queued") is not None` passes trivially because the default was resolved at import time. This both masks the critical `default_factory` bug and provides no coverage that each instance gets a fresh timestamp. Use `freezegun` (or `mock.patch("django.utils.timezone.now")`) to construct two instances at different simulated times and assert `queued` differs.

:yellow_circle: [correctness] Cycle check compares `integration_id` only — identity boundary may be wrong in `src/sentry/integrations/mixins/issues.py:387` (confidence: 78)
`should_sync` skips when `sync_source.integration_id == self.org_integration.integration_id`. `from_integration` sets this to `integration.id` (the global `Integration` row), which is shared across all orgs that install the same integration app. If the intended cycle boundary is "don't echo back to the specific org-installation that sent this", the comparison should be against `org_integration.id` (the `OrganizationIntegration` row). If the intended boundary is "any installation of the same integration provider anywhere", then `integration_id` is correct — but worth confirming, because a Jira integration installed in orgs A and B will have the same `integration_id` for both, and a cross-org propagation (rare but possible in Sentry's model) would be suppressed.

## Nitpicks

:white_circle: [testing] Typo in test name: `test_from_dict_inalid_data` in `tests/sentry/integrations/services/test_assignment_source.py:12` (confidence: 99)
Missing the `v`: `inalid` → `invalid`. Shows up in CI output, grep, and future test selection.

:white_circle: [consistency] Parameter name inconsistency: `sync_source` vs `assignment_source` vs `assignment_source_dict` in `src/sentry/integrations/mixins/issues.py:65` (confidence: 80)
Three related names exist for the same concept: `should_sync(..., sync_source=...)`, `sync_assignee_outbound(..., assignment_source=...)`, and the celery kwarg `assignment_source_dict`. Readers tracing the flow rename at each boundary. Unify on `assignment_source`, reserving `assignment_source_dict` only for the on-wire serialized form.

:white_circle: [security] `cls(**input_dict)` on broker-originated payload in `src/sentry/integrations/services/assignment_source.py:28` (confidence: 70)
`from_dict` splats the dict into the dataclass constructor. Threat model is low (internal Celery broker), but (a) any future field added to `AssignmentSource` immediately becomes injectable from any producer, and (b) if the broker is ever shared with less-trusted producers (cross-tenant workers, replay tooling) this becomes a constructor-smuggling primitive. Whitelist keys explicitly, with type coercion — this also defends against the JSON-roundtrip type drift called out in the second critical finding.

## Risk Metadata
Risk Score: **55/100 (MEDIUM)** | Blast Radius: 7 files, 2 core integration modules (`integrations/mixins`, `models/groupassignee`), touches a Celery task signature (cross-worker deploy ordering matters) | Sensitive Paths: `integrations/` (3rd-party sync, prod-incident territory), `models/groupassignee.py` (hot write path)
AI-Authored Likelihood: LOW

## Recommendation
**request-changes** — the two critical findings (class-level `timezone.now()` default and the Celery/JSON datetime roundtrip) can silently defeat this PR's own goal in production. Both are one-line fixes. The missing "different-source" test is also cheap insurance against the opposite regression. Everything else is follow-up-ticket material.

---
*Review metadata: 7 files, 212 / 15 lines, 11 findings surfaced (confidence threshold 70+), Python/Django backend, issue-sync subsystem.*
