## Summary
7 files changed, ~220 lines added, ~15 lines deleted. 11 findings (4 critical, 7 improvements).
Introduces `AssignmentSource` to break integration sync-cycles, but the dataclass has a frozen-at-import-time `queued` default and a datetime value that cannot JSON-round-trip through Celery — together these silently defeat cycle-prevention. Also a rolling-deploy hazard on the Celery task signature and a positional-arg call into a method many subclasses override.

## Critical

:red_circle: [correctness] Dataclass default `queued: datetime = timezone.now()` evaluated once at module import in src/sentry/integrations/services/assignment_source.py:18 (confidence: 98)
Dataclass field defaults are evaluated once at class-definition time (module import), not per instance. Every `AssignmentSource` constructed without an explicit `queued` argument therefore shares the identical timestamp — the time the module was first imported, not the time the assignment event was triggered. The `frozen=True` attribute does not help; it only prevents mutation after construction. Any code that uses `queued` for ordering, auditing, deduplication, or staleness will silently receive wrong data.
```suggestion
from dataclasses import asdict, dataclass, field

@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```
[References: https://docs.python.org/3/library/dataclasses.html#mutable-default-values]

:red_circle: [correctness] `to_dict()` emits a `datetime` that cannot JSON-round-trip through Celery in src/sentry/integrations/services/assignment_source.py:31 (confidence: 95)
`to_dict` uses `dataclasses.asdict`, which preserves `datetime` objects as Python objects rather than ISO strings. When `sync_group_assignee_outbound` passes `assignment_source.to_dict()` as Celery task kwargs via `apply_async`, Sentry's default JSON serializer raises `TypeError: Object of type datetime is not JSON serializable` — or, under a custom serializer, the worker's `from_dict` receives a string where a `datetime` is annotated, and dataclasses perform no runtime type enforcement, so construction silently succeeds with the wrong type. Either path means cycle-prevention is silently broken for every non-None `assignment_source` call: `from_dict` returns `None`, `should_sync` never hits the `sync_source.integration_id == self.org_integration.integration_id` guard, and ping-pong loops between Sentry and Jira/GitHub/GitLab become possible. This is CWE-636 (fail-open under error).
```suggestion
def to_dict(self) -> dict[str, Any]:
    d = asdict(self)
    d["queued"] = self.queued.isoformat()
    return d

@classmethod
def from_dict(cls, input_dict: dict[str, Any]) -> AssignmentSource | None:
    try:
        data = dict(input_dict)
        if isinstance(data.get("queued"), str):
            data["queued"] = datetime.fromisoformat(data["queued"])
        return cls(**data)
    except (ValueError, TypeError, KeyError):
        return None
```
[References: https://cwe.mitre.org/data/definitions/636.html]

:red_circle: [cross-file-impact] Celery task adds new kwarg without `**kwargs` guard — old workers reject tasks queued by new code during rolling deploy in src/sentry/integrations/tasks/sync_assignee_outbound.py:30 (confidence: 95)
The previous `sync_assignee_outbound` signature was `(external_issue_id, user_id, assign)` — no `**kwargs`. The new signature adds `assignment_source_dict: dict[str, Any] | None = None`. During a rolling deploy, upgraded application servers will enqueue tasks with `assignment_source_dict` in kwargs while old workers still run the previous code. Python raises `TypeError: sync_assignee_outbound() got an unexpected keyword argument 'assignment_source_dict'`, the task fails, Celery's retry policy keeps replaying, and the deploy window either drops assignment sync events or fills the dead-letter queue until all workers are updated.
```suggestion
# Two-phase migration: in a prior deploy, add **kwargs to the old signature
# so old workers silently ignore the new field.
def sync_assignee_outbound(
    external_issue_id: int,
    user_id: int | None,
    assign: bool,
    **kwargs,  # prior deploy
) -> None: ...

# Only after all workers are upgraded, add the typed parameter in a second deploy.
```

:red_circle: [cross-file-impact] `should_sync` called with positional second arg — subclass overrides with a strict 1-param signature raise TypeError in src/sentry/integrations/tasks/sync_assignee_outbound.py:53 (confidence: 90)
`installation.should_sync("outbound_assignee", parsed_assignment_source)` passes `sync_source` positionally. Any integration subclass override written as `def should_sync(self, attribute: str) -> bool:` (no second param, no `**kwargs`) will raise `TypeError: should_sync() takes 2 positional arguments but 3 were given` at runtime. The diff updates only the two base classes in `mixins/issues.py`; subclass overrides in Jira/GitHub/GitLab/Azure DevOps/etc. are not touched. Keyword-passing doesn't fully fix this — the callee's signature must also accept the new parameter — so every override must be audited.
```suggestion
# Audit every should_sync override across integration providers
# and add `sync_source: AssignmentSource | None = None`.
# At the call site, pass by keyword to make the contract explicit:
if installation.should_sync("outbound_assignee", sync_source=parsed_assignment_source):
    ...
```

## Improvements

:yellow_circle: [correctness] `sync_status_outbound` gained `assignment_source` param but no caller in the PR passes it — dead wiring in src/sentry/integrations/mixins/issues.py:411 (confidence: 85)
The abstract method signature now includes `assignment_source: AssignmentSource | None = None`, but no caller in this diff threads the value through, and `should_sync` is never invoked with a non-None source for the status-sync path. The cycle-prevention guard is therefore unreachable for status sync despite the signature change. Either mirror the assignee-sync plumbing through the status-sync task (`AssignmentSource.from_dict` → `should_sync("outbound_status", parsed_source)` → `sync_status_outbound(..., assignment_source=parsed_source)`), or drop the parameter until the plumbing lands to avoid a misleading interface contract for subclass implementors.
```suggestion
# In the sync_status_outbound Celery task (not in this diff), mirror the pattern:
parsed_assignment_source = (
    AssignmentSource.from_dict(assignment_source_dict) if assignment_source_dict else None
)
if installation.should_sync("outbound_status", parsed_assignment_source):
    installation.sync_status_outbound(
        external_issue, is_resolved, project_id,
        assignment_source=parsed_assignment_source,
    )
```

:yellow_circle: [security] Silent `from_dict` failure converts cycle-prevention into a fail-open control in src/sentry/integrations/services/assignment_source.py:31 (confidence: 85)
When `from_dict` catches `ValueError`/`TypeError` and returns `None`, `sync_assignee_outbound` cannot distinguish "no source provided" from "source payload corrupted". The security-relevant cycle-prevention invariant silently disappears under any payload corruption or version skew — exactly the failure class this PR is trying to prevent. Add observability (metric + warning log of key-names only, not values) so operators learn when cycle-prevention was bypassed, and consider fail-closed semantics (drop the sync if a non-None `assignment_source_dict` fails to parse) since one dropped assignment is cheaper than a sync storm.
```suggestion
# In sync_assignee_outbound task:
parsed_assignment_source = None
if assignment_source_dict is not None:
    parsed_assignment_source = AssignmentSource.from_dict(assignment_source_dict)
    if parsed_assignment_source is None:
        logger.warning(
            "sync_assignee_outbound.invalid_assignment_source",
            extra={"external_issue_id": external_issue_id, "keys": sorted(assignment_source_dict.keys())},
        )
        return  # fail closed — better than a sync loop
```
[References: https://owasp.org/Top10/A09_2021-Security_Logging_and_Monitoring_Failures/]

:yellow_circle: [consistency] Two names for one concept: `sync_source` vs `assignment_source` in src/sentry/integrations/mixins/issues.py:18 (confidence: 85)
`IssueBasicIntegration.should_sync` and `IssueSyncIntegration.should_sync` name the new parameter `sync_source`, while every other method and caller in the PR (`sync_status_outbound`, `GroupAssignee.assign`, `GroupAssignee.deassign`, `sync_group_assignee_outbound`, the Celery task kwarg `assignment_source_dict`, and the local `parsed_assignment_source`) uses `assignment_source`. The same concept with two names forces readers to context-switch and makes keyword-passing error-prone.
```suggestion
def should_sync(self, attribute: str, assignment_source: AssignmentSource | None = None) -> bool:
    ...
    if assignment_source and assignment_source.integration_id == self.org_integration.integration_id:
        return False
```

:yellow_circle: [testing] No round-trip serialization test — the JSON/Celery boundary (where the bug hides) is untested in tests/sentry/integrations/services/test_assignment_source.py:38 (confidence: 95)
Cycle-prevention depends on `AssignmentSource` surviving: `to_dict()` → Celery JSON encode → Celery JSON decode → `from_dict()`. No test exercises that chain. `test_to_dict` and `test_from_dict_valid_data` each cover half, but the failure mode lives in the seam between them (datetime cannot round-trip through JSON).
```suggestion
import json
from datetime import datetime

def test_from_dict_round_trip_after_json_serialization(self):
    source = AssignmentSource(source_name="gh-integration", integration_id=42)
    serialized = json.loads(json.dumps(source.to_dict(), default=str))
    result = AssignmentSource.from_dict(serialized)
    assert result is not None, "from_dict must survive JSON round-trip"
    assert result.source_name == "gh-integration"
    assert result.integration_id == 42
    assert isinstance(result.queued, datetime), "queued must deserialize to datetime, not str"
```

:yellow_circle: [testing] `from_integration` — the primary factory used in production — has zero test coverage in tests/sentry/integrations/services/test_assignment_source.py:38 (confidence: 90)
`from_integration` is the sole construction path used by `sync_group_assignee_inbound` via `utils/sync.py`. If the attribute mapping (`integration.name → source_name`, `integration.id → integration_id`) ever regresses, nothing will catch it. Add a test using the normal integration fixture so both the ORM `Integration` and the `RpcIntegration` variants are exercised.
```suggestion
def test_from_integration_populates_fields(self):
    integration = self.create_integration(
        organization=self.organization,
        external_id="ext-999",
        provider="example",
        name="My GitHub Integration",
    )
    source = AssignmentSource.from_integration(integration)
    assert source.source_name == integration.name
    assert source.integration_id == integration.id
    assert isinstance(source.queued, datetime)
```

:yellow_circle: [testing] No inverse-case test — a different integration_id should still sync in tests/sentry/models/test_groupassignee.py:390 (confidence: 88)
`test_assignee_sync_outbound_assign_with_matching_source_integration` verifies the positive cycle-break path (matching id suppresses the call). There is no complementary test that a *different* integration's `AssignmentSource` still triggers an outbound sync. A regression that degrades the guard to "any truthy sync_source short-circuits" would silently pass the existing tests.
```suggestion
@mock.patch.object(ExampleIntegration, "sync_assignee_outbound")
def test_assignee_sync_outbound_assign_different_source_integration_still_syncs(
    self, mock_sync_assignee_outbound,
):
    source_integration = self.create_integration(
        organization=self.group.organization,
        external_id="source-111", provider="example", name="Source",
        oi_params={"config": {"sync_assignee_outbound": True}},
    )
    target_integration = self.create_integration(
        organization=self.group.organization,
        external_id="target-222", provider="example", name="Target",
        oi_params={"config": {"sync_assignee_outbound": True}},
    )
    ExternalIssue.objects.create(
        organization_id=self.group.organization.id,
        integration_id=target_integration.id, key="T-1",
    )
    with self.feature({"organizations:integrations-issue-sync": True}), self.tasks():
        GroupAssignee.objects.assign(
            self.group, self.user,
            assignment_source=AssignmentSource.from_integration(source_integration),
        )
        mock_sync_assignee_outbound.assert_called_once()
```

:yellow_circle: [testing] `test_to_dict` asserts `queued is not None` — tautology that cannot catch the bug it purports to verify in tests/sentry/integrations/services/test_assignment_source.py:37 (confidence: 88)
Because `queued` has a class-level default of `timezone.now()` evaluated at import time, it is *always* non-None on a freshly-constructed instance regardless of whether serialization is correct. The test would pass even if `queued` came back as an empty string or an epoch integer. Replace the presence-check with a type and value check tied to a recent time window.
```suggestion
def test_to_dict(self):
    before = timezone.now()
    source = AssignmentSource(source_name="foo-source", integration_id=123)
    result = source.to_dict()

    assert result.get("source_name") == "foo-source"
    assert result.get("integration_id") == 123

    queued = result.get("queued")
    assert isinstance(queued, datetime), f"Expected datetime, got {type(queued).__name__}"
    assert before <= queued <= timezone.now(), "queued should be a recent timestamp"
```

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: touches every integration provider's sync path (Jira, GitHub, GitLab, Azure DevOps, Linear, etc.) via abstract-method signature changes and a cross-service Celery task | Sensitive Paths: `src/sentry/integrations/**` (cross-system state sync), `src/sentry/models/groupassignee.py` (core assignment model)
AI-Authored Likelihood: MEDIUM — mutable-default-factory anti-pattern on a frozen dataclass, lossy `asdict`/JSON round-trip design, tautological unit-test assertion, and dead-wired abstract-method param are characteristic of assistant-generated boilerplate that compiles cleanly but misses runtime invariants; a human reviewer familiar with Python dataclasses + Celery JSON serialization would likely have caught at least the `queued` default.

(3 additional nitpick findings below threshold suppressed: test-name typo `test_from_dict_inalid_data`, over-broad `cls(**input_dict)` unpack, and `TYPE_CHECKING` import path spot-check.)
