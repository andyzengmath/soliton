## Summary
7 files changed, 212 lines added, 15 lines deleted. 9 findings (5 critical, 4 improvements, 0 nitpicks).
Cycle-break fix is sound in intent but leaks through three seams: a mutable `timezone.now()` default frozen at module import, a `datetime` field that will not round-trip through Celery's JSON serializer, and an unconditional new kwarg that breaks old workers during rolling deploys.

## Critical
:red_circle: [correctness] Mutable `timezone.now()` default evaluated once at class definition in src/sentry/integrations/services/assignment_source.py:80 (confidence: 97)
`queued: datetime = timezone.now()` is a bare dataclass default, evaluated a single time when the class body is parsed at module import. Every `AssignmentSource` instance created via `from_integration` therefore carries the same frozen process-startup timestamp, silently feeding incorrect data to any future consumer (logging, audit, staleness).
```suggestion
from dataclasses import asdict, dataclass, field

@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```
<details><summary>More context</summary>

Python dataclass default values that are not wrapped in `dataclasses.field(default_factory=...)` are evaluated exactly once at class-definition time. The `from_integration` factory never passes `queued`, so every production `AssignmentSource` shares the same `datetime` captured when the module was first imported. Although the current cycle-prevention logic only compares `integration_id` and does not use `queued` for its decision, the field is serialized via `to_dict()` and shipped to Celery — any downstream consumer that trusts `queued` as "when the assignment happened" will receive the wrong value. The existing `test_to_dict` asserts only that `queued is not None`, which passes even under this bug, so the test suite cannot detect the regression.
</details>

:red_circle: [correctness] `to_dict()` emits a non-JSON-serializable `datetime` that breaks the Celery round-trip in src/sentry/integrations/services/assignment_source.py:89 (confidence: 95)
`to_dict()` calls `dataclasses.asdict()` which leaves `queued` as a Python `datetime`; Sentry enforces `CELERY_TASK_SERIALIZER = "json"`, so stdlib JSON raises `TypeError: Object of type datetime is not JSON serializable` when `apply_async(kwargs={...})` runs. If a custom encoder silently converts to ISO-8601, the worker's `cls(**input_dict)` produces an `AssignmentSource` where `queued` is a `str` (latent type violation) or `from_dict` returns `None` via the broad except and the cycle guard is silently lost.
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
    except (ValueError, TypeError):
        return None
```
<details><summary>More context</summary>

Two failure modes compound:

1. If no custom JSON encoder handles `datetime`, `apply_async` itself raises `TypeError` at enqueue time, potentially disrupting the caller that invoked `GroupAssignee.objects.assign/deassign`.
2. If a custom encoder serializes `datetime` to an ISO-8601 string, the task message succeeds, but on the worker `cls(**input_dict)` accepts the string without coercion (dataclasses do not type-check at runtime). The resulting `AssignmentSource` has `queued: str`, which is a latent correctness violation for any code that later compares or arithmetic-operates on `queued`.

The existing `test_assignment_source.py` tests never call `from_dict(source.to_dict())`, so neither failure is caught. Pairing this fix with the `default_factory` fix above makes `queued` both fresh and round-trip-safe.
</details>

:red_circle: [cross-file-impact] Rolling-deploy hazard: old workers raise `TypeError` on the new `assignment_source_dict` kwarg in src/sentry/integrations/tasks/sync_assignee_outbound.py:27 (confidence: 90)
`sync_group_assignee_outbound` in `utils/sync.py` unconditionally passes `assignment_source_dict` in `apply_async(kwargs=...)` — even when its value is `None` — so 100% of outbound assignee sync tasks enqueued after deploy carry this key. Old worker processes still running the pre-PR signature `sync_assignee_outbound(external_issue_id, user_id, assign)` lack any `**kwargs` catch-all and raise `TypeError: sync_assignee_outbound() got an unexpected keyword argument 'assignment_source_dict'` until the rolling deploy completes.
```suggestion
kwargs = {"external_issue_id": external_issue_id, "user_id": user_id, "assign": assign}
if assignment_source is not None:
    kwargs["assignment_source_dict"] = assignment_source.to_dict()
sync_assignee_outbound.apply_async(kwargs=kwargs)
```
<details><summary>More context</summary>

The current diff writes `"assignment_source_dict": assignment_source.to_dict() if assignment_source else None` into the kwargs dict unconditionally. This means every enqueued task carries the new key, not just those with a real source — so even assignments from the UI (which never set `assignment_source`) trigger the old-worker TypeError. Gating the key's inclusion on non-None preserves backward compatibility for the common path and narrows the incompatible blast radius to the (rare) sourced tasks. A deployment alternative is to ship a transitional worker version that accepts `**kwargs` ahead of the producer change.
</details>

:red_circle: [testing] No test exercises the Celery task with a non-None `assignment_source_dict` in src/sentry/integrations/tasks/sync_assignee_outbound.py:30 (confidence: 90)
The task's deserialization boundary — receiving `assignment_source_dict` as a plain dict and reconstructing `AssignmentSource` via `from_dict` — has zero coverage; existing integration-level tests mock at `ExampleIntegration.sync_assignee_outbound` and bypass the task entirely. This is exactly where the `datetime` round-trip and JSON encoder risks above would manifest in production.
```suggestion
@mock.patch.object(ExampleIntegration, "sync_assignee_outbound")
@mock.patch.object(ExampleIntegration, "should_sync", return_value=True)
def test_sync_assignee_outbound_task_reconstructs_source_through_json(
    self, mock_should_sync, mock_sync_outbound
):
    source = AssignmentSource(source_name="example", integration_id=self.integration.id)
    # Simulate Celery's JSON round-trip explicitly:
    assignment_source_dict = json.loads(json.dumps(source.to_dict()))
    sync_assignee_outbound(
        external_issue_id=self.external_issue.id,
        user_id=self.user.id,
        assign=True,
        assignment_source_dict=assignment_source_dict,
    )
    _, kwargs = mock_should_sync.call_args
    assert kwargs.get("sync_source") is not None
    assert kwargs["sync_source"].integration_id == self.integration.id
```
<details><summary>More context</summary>

The existing test `test_assignee_sync_outbound_assign_with_matching_source_integration` asserts `mock_sync_assignee_outbound.assert_not_called()` by mocking at the integration boundary. That test only proves that the end-to-end path suppresses the mock — it would still pass if `from_dict` silently returned `None` on the worker (e.g., because of the datetime round-trip bug) and `should_sync` fell through to the unrelated `config.get(key, False)` branch that happens to be `False` in the test fixture. A test at the task boundary that forces JSON serialization makes the reconstruction invariant explicit and catches future regressions in the serialization format.
</details>

:red_circle: [testing] No unit test for `IssueSyncIntegration.should_sync` in src/sentry/integrations/mixins/issues.py:27 (confidence: 87)
The `should_sync` short-circuit that suppresses same-integration propagation is the heart of the fix, yet the test suite exercises it only indirectly through four layers (`GroupAssignee.assign` → `sync_group_assignee_outbound` → Celery task → `installation.should_sync`). If any intermediate layer silently drops `assignment_source`, `should_sync` can still return `False` via the unrelated `config.get(key, False)` path and the integration-level assertion still passes.
```suggestion
def test_should_sync_skips_when_source_matches_integration(self):
    installation = self.integration.get_installation(self.organization.id)
    same = AssignmentSource(
        source_name="example",
        integration_id=installation.org_integration.integration_id,
    )
    other = AssignmentSource(source_name="other", integration_id=99999)
    assert installation.should_sync("outbound_assignee", same) is False
    assert installation.should_sync("outbound_assignee", other) is True
    assert installation.should_sync("outbound_assignee", None) is True
```
<details><summary>More context</summary>

A direct unit test isolates the mixin's cycle-break guard from the full Celery + sync stack, and makes the two separate invariants explicit: the guard must fire only when `integration_id` matches, and it must not fire when the source is absent or belongs to another integration. Combined with the missing positive-case test (see the "other integrations still propagate" finding below), this closes the coverage gap for the bug-fix semantics.
</details>

## Improvements
:yellow_circle: [testing] No positive-case test for cross-integration propagation in src/sentry/integrations/utils/sync.py:93 (confidence: 85)
The PR description promises that "other integrations still propagate changes outward," but the only new test exercises the negative case (same integration → suppressed). A regression that flipped `==` to `!=` or introduced an always-truthy guard would still pass the current suite.
```suggestion
@mock.patch.object(ExampleIntegration, "sync_assignee_outbound")
def test_assignee_sync_outbound_assign_with_different_source_integration(
    self, mock_sync_assignee_outbound
):
    other_integration = self.create_integration(
        organization=self.group.organization, external_id="other-999", provider="example",
        oi_params={"config": {"sync_assignee_outbound": True, "sync_assignee_inbound": True}},
    )
    # ... link self.group to the existing self.integration's external issue ...
    with self.feature({"organizations:integrations-issue-sync": True}), self.tasks():
        GroupAssignee.objects.assign(
            self.group, self.user,
            assignment_source=AssignmentSource.from_integration(other_integration),
        )
        mock_sync_assignee_outbound.assert_called_once()
```

:yellow_circle: [testing] `test_to_dict` only asserts truthiness of `queued` in tests/sentry/integrations/services/test_assignment_source.py:32 (confidence: 88)
`assert result.get("queued") is not None` passes even when every instance shares the same frozen timestamp captured at module import, so the mutable-default bug above is invisible to the suite. Strengthening the assertion to check type and freshness makes the class-load-default regression testable.
```suggestion
def test_to_dict(self):
    before = timezone.now()
    source = AssignmentSource(source_name="foo-source", integration_id=123)
    result = source.to_dict()
    assert isinstance(result["queued"], datetime)
    assert result["queued"] >= before
    assert result["source_name"] == "foo-source"
    assert result["integration_id"] == 123
```

:yellow_circle: [consistency] Parameter naming drift: `sync_source` in `should_sync` vs `assignment_source` elsewhere in src/sentry/integrations/mixins/issues.py:18 (confidence: 95)
`IssueBasicIntegration.should_sync` and `IssueSyncIntegration.should_sync` use `sync_source`, while every other new parameter in this PR (`sync.py`, `sync_assignee_outbound.py`, `groupassignee.py`, `sync_status_outbound`) uses `assignment_source`. Two names for the same concept create friction for anyone overriding the interface and mask grep-based audits of the new contract.
```suggestion
def should_sync(self, attribute: str, assignment_source: AssignmentSource | None = None) -> bool:
    key = getattr(self, f"{attribute}_key", None)
    if key is None or self.org_integration is None:
        return False
    if assignment_source and assignment_source.integration_id == self.org_integration.integration_id:
        return False
    value: bool = self.org_integration.config.get(key, False)
    return value
```

:yellow_circle: [consistency] Abstract `sync_status_outbound` gained an orphan `assignment_source` parameter in src/sentry/integrations/mixins/issues.py:45 (confidence: 85)
The abstract signature now accepts `assignment_source: AssignmentSource | None = None`, but no caller in this diff passes it and `should_sync` is never invoked for the `"outbound_status"` attribute. Concrete overrides that declare `**kwargs` will silently absorb and ignore the value; overrides without `**kwargs` will `TypeError` whenever a future caller finally passes it.
```suggestion
# Option (a): remove until actually used
def sync_status_outbound(self, external_issue, is_resolved, project_id, **kwargs):
    """Propagate a sentry issue's status to a linked issue's status."""

# Option (b): wire the guard through the status-outbound call sites too, exercising
# should_sync("outbound_status", assignment_source) at every status-sync entry point.
```

## Risk Metadata
Risk Score: 36/100 (MEDIUM) | Blast Radius: ~5 production files across the integrations subsystem (`mixins/issues.py` and `models/groupassignee.py` are foundational) | Sensitive Paths: none
AI-Authored Likelihood: LOW
