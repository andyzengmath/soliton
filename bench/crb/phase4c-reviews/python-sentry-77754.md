## Summary
7 files changed, 212 lines added, 15 lines deleted. 7 findings (3 critical, 4 improvements).
The cycle-break design is sound, but three implementation bugs silently defeat it in production: a dataclass default evaluated once at import, a Celery/JSON round-trip that cannot preserve `AssignmentSource`, and a newly-required kwarg that concrete `sync_assignee_outbound` implementations do not accept.

## Critical

:red_circle: [correctness] `queued` dataclass default is evaluated once at class-definition time in src/sentry/integrations/services/assignment_source.py:17 (confidence: 95)
`queued: datetime = timezone.now()` is evaluated exactly once when the class body runs (at module import), not each time an `AssignmentSource` is constructed. Every instance that does not supply `queued` explicitly will share the same import-time timestamp, making the field semantically a startup timestamp rather than a "when was this queued" marker. `frozen=True` does not fix this — it only forbids post-construction mutation; the default is still captured once. The existing `test_to_dict` asserts `queued is not None`, so the bug is invisible to the test suite.
```suggestion
from dataclasses import asdict, dataclass, field

@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```

:red_circle: [cross-file-impact] Concrete `sync_assignee_outbound` implementations will TypeError on the new `assignment_source` kwarg in src/sentry/integrations/tasks/sync_assignee_outbound.py:61 (confidence: 92)
The task now calls `installation.sync_assignee_outbound(external_issue, user, assign=assign, assignment_source=parsed_assignment_source)`. The abstract `sync_assignee_outbound` on `IssueSyncIntegration` is not modified by this diff (only `should_sync` and `sync_status_outbound` are), and no concrete implementation (Jira, Jira Server, GitLab, Asana, Azure DevOps, Linear, `example/integration.py`) is touched. Any concrete override whose signature does not already accept `assignment_source` (or absorb it via `**kwargs`) will raise `TypeError: sync_assignee_outbound() got an unexpected keyword argument 'assignment_source'` for every outbound assignee sync — including the majority case where `parsed_assignment_source is None` and no cycle exists. This regresses all issue-sync integrations, not just Jira.
```suggestion
# 1) Extend the abstract in src/sentry/integrations/mixins/issues.py:
@abstractmethod
def sync_assignee_outbound(
    self,
    external_issue: ExternalIssue,
    user: RpcUser | None,
    assign: bool = True,
    assignment_source: AssignmentSource | None = None,
) -> None:
    raise NotImplementedError

# 2) Update every concrete override (Jira, Jira Server, GitLab, Asana,
# Azure DevOps, Linear, example) to accept and ignore (or act on) the kwarg:
def sync_assignee_outbound(self, external_issue, user, assign=True, assignment_source=None):
    ...
```

:red_circle: [correctness] `AssignmentSource` does not round-trip through Celery's JSON boundary, silently defeating the cycle-breaker in src/sentry/integrations/services/assignment_source.py:20 (confidence: 88)
`to_dict()` calls `dataclasses.asdict()`, which preserves `queued` as a `datetime` object. Celery serializes `apply_async` kwargs as JSON by default, and Python `datetime` is not natively JSON-serializable — depending on Sentry's Celery configuration this either raises an encode error at enqueue time (crashing the inbound webhook handler) or, more likely, kombu's JSON encoder coerces `datetime` to an ISO string on the wire. In the latter case, `from_dict` is called on the worker side with `{"queued": "<iso-string>", ...}` and `cls(**input_dict)` succeeds (Python does not enforce type hints), but the resulting instance holds a `str` in a field typed `datetime`. The current guard only reads `integration_id` so cycle detection still fires in the happy path — but any downstream code that later treats `queued` as a `datetime` will crash, and the dataclass's own invariants are violated. The narrow `except (ValueError, TypeError)` also silently swallows `KeyError` scenarios that indicate real programming errors.
```suggestion
def to_dict(self) -> dict[str, Any]:
    d = asdict(self)
    if isinstance(d.get("queued"), datetime):
        d["queued"] = d["queued"].isoformat()
    return d

@classmethod
def from_dict(cls, input_dict: dict[str, Any]) -> AssignmentSource | None:
    try:
        data = dict(input_dict)
        queued = data.get("queued")
        if isinstance(queued, str):
            data["queued"] = datetime.fromisoformat(queued)
        return cls(**data)
    except (KeyError, ValueError, TypeError):
        return None
```

## Improvements

:yellow_circle: [consistency] Parameter name diverges between `should_sync` (`sync_source`) and everywhere else (`assignment_source`) in src/sentry/integrations/mixins/issues.py:65 (confidence: 95)
`IssueBasicIntegration.should_sync(self, attribute, sync_source: AssignmentSource | None = None)` and its override in `IssueSyncIntegration` use `sync_source`, but every other new parameter introduced in this PR — `sync_status_outbound`, `GroupAssignee.assign/deassign`, `sync_group_assignee_outbound`, the Celery task's `assignment_source_dict`, the model-layer invocations — uses `assignment_source`. Any caller threading the value via kwargs has to rename on the way through `should_sync`, and a future `**kwargs` forwarder would silently drop the value. Pick one canonical name (`assignment_source` aligns with the dataclass and is dominant) and apply it uniformly.
```suggestion
class IssueBasicIntegration(IntegrationInstallation, ABC):
    def should_sync(self, attribute: str, assignment_source: AssignmentSource | None = None):
        return False

class IssueSyncIntegration(IssueBasicIntegration, ABC):
    def should_sync(self, attribute: str, assignment_source: AssignmentSource | None = None) -> bool:
        key = getattr(self, f"{attribute}_key", None)
        if key is None or self.org_integration is None:
            return False
        if assignment_source and assignment_source.integration_id == self.org_integration.integration_id:
            return False
        value: bool = self.org_integration.config.get(key, False)
        return value
```

:yellow_circle: [testing] `test_to_dict` cannot catch the stale-default-timestamp bug in tests/sentry/integrations/services/test_assignment_source.py:33 (confidence: 90)
The only assertion on `queued` is `assert result.get("queued") is not None`. Because the buggy `timezone.now()` default still produces a non-None datetime (just the wrong one — process-start time), this test happily passes even when every `AssignmentSource` in production shares the same timestamp. Tightening this assertion would have caught the mutable-default bug during development.
```suggestion
def test_to_dict(self):
    before = timezone.now()
    source = AssignmentSource(source_name="foo-source", integration_id=123)
    after = timezone.now()

    result = source.to_dict()
    assert result.get("source_name") == "foo-source"
    assert result.get("integration_id") == 123
    queued = result.get("queued")
    assert queued is not None
    # Must reflect construction time, not module-import time
    assert before <= queued <= after
```

:yellow_circle: [testing] No end-to-end test that `assignment_source_dict` round-trips through Celery and still breaks the cycle in tests/sentry/models/test_groupassignee.py:150 (confidence: 88)
The new `test_assignee_sync_outbound_assign_with_matching_source_integration` exercises the model-layer guard (in-process call), but never drives `AssignmentSource` through the serialize → enqueue → deserialize → `should_sync` path that is the actual runtime shape of this bug class. Given the datetime round-trip hazard flagged above, this is the single missing test that would have surfaced the Celery-boundary problem. Invoke the task function directly with `assignment_source.to_dict()` and assert the installation's `sync_assignee_outbound` is not called.
```suggestion
@mock.patch.object(ExampleIntegration, "sync_assignee_outbound")
def test_task_level_cycle_break_roundtrips_assignment_source(self, mock_sync):
    integration = self.create_integration(
        organization=self.group.organization,
        external_id="123456",
        provider="example",
        oi_params={"config": {"sync_assignee_outbound": True}},
    )
    external_issue = ExternalIssue.objects.create(
        organization_id=self.group.organization.id,
        integration_id=integration.id,
        key="APP-123",
    )
    source_dict = AssignmentSource.from_integration(integration).to_dict()

    sync_assignee_outbound(
        external_issue_id=external_issue.id,
        user_id=self.user.id,
        assign=True,
        assignment_source_dict=source_dict,
    )
    mock_sync.assert_not_called()
```

:yellow_circle: [consistency] `sync_status_outbound` gains an `assignment_source` kwarg that no caller in this PR passes, leaving it dead until a follow-up in src/sentry/integrations/mixins/issues.py:382 (confidence: 82)
The PR advertises breaking assignee sync cycles, but also extends the `sync_status_outbound` abstract signature to accept `assignment_source: AssignmentSource | None = None`. No caller in this diff forwards an `AssignmentSource` into status outbound, so (a) the parameter is currently dead code, and (b) every concrete `sync_status_outbound` override across integrations must now either accept the new kwarg or already absorb it via `**kwargs` — the abstract change alone can force a silent signature mismatch at call sites outside this PR. Either split the status-sync change into a separate PR that also updates the callers, or keep this PR scoped strictly to the assignee path.
```suggestion
# Drop the signature change from src/sentry/integrations/mixins/issues.py
# until a follow-up PR wires actual callers; keep only the should_sync +
# sync_assignee_outbound + GroupAssignee + task changes required for the
# advertised fix.
@abstractmethod
def sync_status_outbound(self, external_issue, is_resolved, project_id, **kwargs):
    """Propagate a sentry issue's status to a linked issue's status."""
```

## Risk Metadata
Risk Score: 35/100 (MEDIUM) | Blast Radius: `groupassignee.py` (core model, ~15+ importers) + `mixins/issues.py` (base for every issue-sync integration); estimated ~25 downstream files | Sensitive Paths: none matched
AI-Authored Likelihood: LOW-MEDIUM — no co-author signature, but the frozen dataclass with `from_dict`/`to_dict`/`from_integration` triad, the `except (ValueError, TypeError): return None` pattern, and the mutable-default footgun are all typical AI generation artifacts.

(1 additional finding below confidence threshold: test typo `test_from_dict_inalid_data` → `test_from_dict_invalid_data`.)
