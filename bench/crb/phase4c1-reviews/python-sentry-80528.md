## Summary
4 files changed, 289 lines added, 264 lines deleted. 1 findings (0 critical, 1 improvements, 0 nitpicks).
Pure refactor that splits incident-creation and issue-occurrence logic out of `monitors/logic/mark_failed.py` into two new modules (`incidents.py`, `incident_occurrence.py`) and hoists the `SimpleCheckIn` TypedDict into `monitors/types.py`. Line-for-line the moved function bodies are identical; the only call-site update is `create_issue_platform_occurrence` -> `create_incident_occurrence` and `mark_failed_threshold` -> `try_incident_threshold`. Codecov reports 91.95% patch coverage with all tests passing, and one maintainer has already approved. Main residual risk is that multiple public-looking symbols (`mark_failed_threshold`, `create_issue_platform_occurrence`, `get_failure_reason`, `HUMAN_FAILURE_STATUS_MAP`, `SINGULAR_HUMAN_FAILURE_MAP`, `get_monitor_environment_context`, `SimpleCheckIn`) disappear from `mark_failed.py` without a re-export shim, so any out-of-diff importer of the old paths would break at import time.

## Improvements
:yellow_circle: [cross-file-impact] Removed symbols from `mark_failed.py` lack back-compat re-exports in src/sentry/monitors/logic/mark_failed.py:1 (confidence: 86)
This refactor deletes seven names that previously lived in `sentry.monitors.logic.mark_failed` — `mark_failed_threshold`, `create_issue_platform_occurrence` (also renamed to `create_incident_occurrence`), `get_failure_reason`, `get_monitor_environment_context`, `HUMAN_FAILURE_STATUS_MAP`, `SINGULAR_HUMAN_FAILURE_MAP`, and the `SimpleCheckIn` TypedDict (moved to `sentry.monitors.types`). The only caller updated inside the diff is `mark_failed.py` itself. Anything outside this diff — tests that patched `sentry.monitors.logic.mark_failed.create_issue_platform_occurrence`, internal tooling importing `SimpleCheckIn` from the old path, or vendored code referencing `mark_failed_threshold` — will now raise `ImportError` or `AttributeError` at the first import or `mock.patch` call. Codecov reporting all tests green is reassuring, but `mock.patch("sentry.monitors.logic.mark_failed.<symbol>", ...)` is a common pattern in this codebase and `grep`-based static discovery would catch the ones that are exercised. Please `rg "from sentry\.monitors\.logic\.mark_failed import (mark_failed_threshold|create_issue_platform_occurrence|get_failure_reason|get_monitor_environment_context|HUMAN_FAILURE_STATUS_MAP|SINGULAR_HUMAN_FAILURE_MAP|SimpleCheckIn)"` and `rg "sentry\.monitors\.logic\.mark_failed\.(create_issue_platform_occurrence|mark_failed_threshold|SimpleCheckIn)"` across the repo (including `tests/`) to confirm no callers remain. If any are found, either update them in this PR or add thin re-export shims in `mark_failed.py`.
```suggestion
# At the bottom of src/sentry/monitors/logic/mark_failed.py, add back-compat re-exports
# if any external callers (tests, tools) still import the old names:
from sentry.monitors.logic.incident_occurrence import (  # noqa: F401  (back-compat re-export)
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
Risk Score: 22/100 (LOW) | Blast Radius: narrow — changes confined to `sentry.monitors.logic` package; no cross-service, migration, or API-surface changes | Sensitive Paths: none (no auth, payment, migration, secret, or credential files touched)
AI-Authored Likelihood: LOW
