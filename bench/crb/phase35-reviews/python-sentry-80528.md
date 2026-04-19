## Summary
4 files changed, 289 lines added, 264 lines deleted. 1 finding (0 critical, 1 improvement, 0 nitpicks).
Pure refactor splitting incident / issue-occurrence logic out of `mark_failed.py` into dedicated modules; behavior preserved, rename-only, but public symbols previously importable from `sentry.monitors.logic.mark_failed` are no longer re-exported.

## Improvements
:yellow_circle: [cross-file-impact] Removed public symbols from `mark_failed` are not re-exported — external importers will break in src/sentry/monitors/logic/mark_failed.py:76 (confidence: 88)
Several top-level names previously defined in `sentry.monitors.logic.mark_failed` were relocated without leaving compatibility aliases: `mark_failed_threshold` → moved+renamed to `incidents.try_incident_threshold`, `create_issue_platform_occurrence` → moved+renamed to `incident_occurrence.create_incident_occurrence`, `SimpleCheckIn` → moved to `sentry.monitors.types`, and `get_failure_reason`, `get_monitor_environment_context`, `HUMAN_FAILURE_STATUS_MAP`, `SINGULAR_HUMAN_FAILURE_MAP` — all removed from `mark_failed.py`. Any in-tree test module or downstream consumer that did `from sentry.monitors.logic.mark_failed import SimpleCheckIn` / `mark_failed_threshold` / `create_issue_platform_occurrence` will fail at import time. This is especially likely because `mark_failed_threshold` was the natural unit-test entrypoint for incident-threshold behavior and because `SimpleCheckIn` was the only public TypedDict in this area before the move. Before merging, grep the tree for the old names (`mark_failed_threshold`, `create_issue_platform_occurrence`, and `from sentry.monitors.logic.mark_failed import SimpleCheckIn`) and either update call sites in the same PR or add shim re-exports in `mark_failed.py`.
```suggestion
# At the bottom of src/sentry/monitors/logic/mark_failed.py, add backwards-compat re-exports:
from sentry.monitors.logic.incident_occurrence import (  # noqa: F401  # re-exported for backwards compat
    create_incident_occurrence as create_issue_platform_occurrence,
    get_failure_reason,
    get_monitor_environment_context,
    HUMAN_FAILURE_STATUS_MAP,
    SINGULAR_HUMAN_FAILURE_MAP,
)
from sentry.monitors.logic.incidents import try_incident_threshold as mark_failed_threshold  # noqa: F401
from sentry.monitors.types import SimpleCheckIn  # noqa: F401
```

## Risk Metadata
Risk Score: 18/100 (LOW) | Blast Radius: 4 files, 3 modules in `src/sentry/monitors/logic/**`, one public-ish entry (`mark_failed`) preserved | Sensitive Paths: none hit
AI-Authored Likelihood: LOW

Notes for the maintainer:
- The deploy-observed `Environment.DoesNotExist` suspect-issue flagged by the Sentry bot originates from `monitor_env.get_environment().name` in `create_incident_occurrence` (formerly `create_issue_platform_occurrence`). That call path is byte-identical before and after this PR, so the exception is pre-existing, not introduced here.
- Patch coverage reported by Codecov (91.95 %) is fine for a move-only change; the 7 missing lines are the muted-branch early-returns and the `monitor_env is None` guards — all preserved verbatim from the original.
- Pre-existing typo ("humam readible") in the `get_failure_reason` docstring carried over unchanged; not worth fixing in this PR but worth noting.
