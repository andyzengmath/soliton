## Summary
7 files changed, 212 lines added, 15 lines deleted. 8 findings (6 critical, 2 improvements, 0 nitpicks).
7 files changed. 8 findings (6 critical, 2 improvements). Most critical: `sync_assignee_outbound()` abstract signature not widened — TypeError on every assignee sync webhook.

## Critical

:red_circle: [correctness] Class-level default `queued = timezone.now()` evaluated once at import time, not per-instance in src/sentry/integrations/services/assignment_source.py:16 (confidence: 98)
In Python dataclasses a bare default (not wrapped in `field(default_factory=...)`) is evaluated exactly once when the class body executes — at module import time. Every `AssignmentSource` instance that omits `queued` will receive the same stale timestamp from when the module was first loaded. This makes `queued` unreliable for any audit, expiry, or replay-window use. Confirmed independently by both the correctness and hallucination agents.
```suggestion
from dataclasses import asdict, dataclass, field
from datetime import datetime
from django.utils import timezone

@dataclass(frozen=True)
class AssignmentSource:
    source_name: str
    integration_id: int
    queued: datetime = field(default_factory=timezone.now)
```

:red_circle: [correctness] `to_dict()` / `from_dict()` round-trip breaks over Celery JSON serialization for `queued: datetime` in src/sentry/integrations/services/assignment_source.py:18 (confidence: 97)
`asdict()` emits `queued` as a raw Python `datetime` object. Celery's default JSON serializer cannot encode it, raising `TypeError` at `apply_async` time and dropping the entire outbound assignee task. If a non-default serializer ISO-coerces it, `from_dict` then reconstructs the dataclass with `queued` as a `str` (no runtime type coercion) — meaning the field is silently corrupt. The cycle-break logic (consulting only `integration_id`) still works in that narrow case, but the task drop on the default serializer path is a hard failure. Confirmed independently by both the correctness and hallucination agents.
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

:red_circle: [cross-file-impact] `sync_assignee_outbound` called with `assignment_source=` kwarg not declared on the abstract method in src/sentry/integrations/tasks/sync_assignee_outbound.py:60 (confidence: 95)
The diff widens `sync_status_outbound`'s abstract signature to accept `assignment_source`, but does NOT widen `sync_assignee_outbound`. The new task call site at line 60-62 passes `assignment_source=parsed_assignment_source` to `installation.sync_assignee_outbound(...)`. Concrete subclasses (Jira, GitHub, GitLab, AzureDevOps, JiraServer, etc.) whose override does not accept `**kwargs` or an explicit `assignment_source` parameter will raise `TypeError: sync_assignee_outbound() got an unexpected keyword argument 'assignment_source'` on every assignee sync — the hot path triggered by every inbound Jira/GitHub assignment webhook. Confirmed independently by correctness, hallucination, and cross-file-impact agents.
```suggestion
# In src/sentry/integrations/mixins/issues.py — widen the abstract signature
@abstractmethod
def sync_assignee_outbound(
    self,
    external_issue,
    user,
    assign: bool,
    assignment_source: AssignmentSource | None = None,
    **kwargs,
) -> None:
    raise NotImplementedError

# Then update every concrete override (JiraIntegration, GithubIntegration,
# GitlabIntegration, AzureDevOpsIntegration, JiraServerIntegration, …) to
# accept assignment_source and either honor or ignore it.
```

:red_circle: [cross-file-impact] `should_sync` called with a new positional argument — concrete overrides keeping the 1-arg signature will TypeError in src/sentry/integrations/tasks/sync_assignee_outbound.py:55 (confidence: 90)
The task invokes `installation.should_sync("outbound_assignee", parsed_assignment_source)` — passing `AssignmentSource` as the second positional argument. Any concrete subclass that overrides `should_sync(self, attribute)` with the original 1-arg signature (and no `**kwargs`) will raise `TypeError: should_sync() takes 2 positional arguments but 3 were given`. Overrides that accept the arg but ignore it will not enforce the cycle-break for that integration, which is a correctness regression.
```suggestion
- Audit every override of should_sync in IssueBasicIntegration / IssueSyncIntegration
  subclasses (ExampleIntegration and any plugin-side overrides are candidates).
- Add the new parameter to each override and call super(), OR pass it as a keyword
  argument at the call site (`installation.should_sync("outbound_assignee",
  sync_source=parsed_assignment_source)`) so old 1-arg overrides do not TypeError
  on the positional.
```

:red_circle: [cross-file-impact] Celery task signature change is backwards-incompatible during a rolling deploy in src/sentry/integrations/tasks/sync_assignee_outbound.py:30 (confidence: 88)
The Celery task `sync_assignee_outbound` gains a new kwarg `assignment_source_dict`. New producer code (`sync_group_assignee_outbound`) passes it via `apply_async(kwargs={...})`. Old worker processes (pre-PR signature `sync_assignee_outbound(external_issue_id, user_id, assign)`) consuming those queued tasks during a rolling deploy will raise `TypeError: sync_assignee_outbound() got an unexpected keyword argument 'assignment_source_dict'`, causing task failures and retry loops until all workers are upgraded.
```suggestion
Two-phase deploy:
(1) Ship the new worker-side code first so all workers accept the new kwarg.
(2) Then ship the producer-side code that starts passing it.
Document this ordering in the PR description and release notes. As a short-term
safety net, temporarily accept **kwargs in the task signature for one release cycle
to absorb the new kwarg even on old worker code.
```

:red_circle: [correctness] `from_dict` swallows deserialization failures silently — re-introduces the cycle bug with zero observability in src/sentry/integrations/services/assignment_source.py:30 (confidence: 90)
`from_dict` returns `None` on `ValueError | TypeError` without logging. The caller in `tasks/sync_assignee_outbound.py` passes that `None` directly to `should_sync`, where `if sync_source and ...` short-circuits the cycle-break check. Any future schema drift (field rename, type change, in-flight Celery payloads carrying an old schema after a code change) silently disables the entire fix this PR is implementing — with no log line, no metric, and no Sentry event. The silent-None contract is confirmed by the test suite, which asserts only on `result is None` without asserting on any observability signal.
```suggestion
import logging
logger = logging.getLogger(__name__)

@classmethod
def from_dict(cls, input_dict: dict[str, Any]) -> AssignmentSource | None:
    try:
        return cls(**input_dict)
    except (ValueError, TypeError) as e:
        logger.warning(
            "assignment_source.from_dict.parse_failure",
            extra={"input_keys": list(input_dict.keys()), "error": str(e)},
        )
        return None
```

## Improvements

:yellow_circle: [cross-file-impact] `sync_status_outbound` concrete overrides will diverge once callers pass `assignment_source` in src/sentry/integrations/mixins/issues.py:411 (confidence: 85)
The PR widens the abstract `sync_status_outbound` to include `assignment_source: AssignmentSource | None = None, **kwargs`, but no caller in this PR passes that kwarg yet — it is preparatory. Concrete overrides with a fixed signature `(self, external_issue, is_resolved, project_id)` (no `**kwargs`) are now non-conformant and will raise `TypeError` as soon as a follow-up PR begins passing the kwarg. This should be addressed in this PR or tracked explicitly.
```suggestion
Update each concrete override of `sync_status_outbound` (Jira, GitHub, GitLab, AzureDevOps, JiraServer) to add `assignment_source: AssignmentSource | None = None` before `**kwargs`, consistent with the updated abstract signature.
```

:yellow_circle: [correctness] Error-path tests assert only on the silent-None return, not on observability in tests/sentry/integrations/services/test_assignment_source.py:9 (confidence: 90)
`test_from_dict_empty_array` and `test_from_dict_inalid_data` assert only `result is None`. If the recommended `logger.warning` is added to `from_dict` and later accidentally removed, these tests will not catch the regression. Adding an `assertLogs` assertion (or an explicit comment that silent-None is intentional) ties the test to the observable contract.
```suggestion
def test_from_dict_invalid_data_logs_warning(self):
    with self.assertLogs("sentry.integrations.services.assignment_source", level="WARNING") as cm:
        assert AssignmentSource.from_dict({"foo": "bar"}) is None
    assert "parse_failure" in cm.output[0]
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: HIGH (mixins/issues.py and groupassignee.py are central; ~8+ downstream subclasses estimated) | Sensitive Paths: none matched
AI-Authored Likelihood: MEDIUM

(4 additional findings below confidence threshold)
