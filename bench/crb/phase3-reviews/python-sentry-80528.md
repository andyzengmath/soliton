# PR Review — getsentry/sentry #80528

**Title:** ref(crons): Reorganize incident creation / issue occurrence logic
**Head → Base:** `evanpurkhiser/ref-crons-reorganize-incident-creation-issue-occurrence-logic` → `master`
**Changed files:** 4 · **+289 / −264**

## Summary

4 files changed, 289 lines added, 264 lines deleted. 6 findings (0 critical, 3 improvements, 3 nitpicks).

Pure extraction refactor: `mark_failed.py` is split into `logic/incidents.py` (incident-threshold flow) and `logic/incident_occurrence.py` (Kafka occurrence payload), and the `SimpleCheckIn` TypedDict is hoisted to `monitors/types.py`. Two functions are renamed in the process: `mark_failed_threshold → try_incident_threshold` and `create_issue_platform_occurrence → create_incident_occurrence`. The extracted code is byte-for-byte faithful to the original modulo those renames and import relocations — no behavioral change is visible in the diff.

## Improvements

:yellow_circle: [testing] Refactor ships with zero test-file updates in `src/sentry/monitors/logic/mark_failed.py`:288 (confidence: 85)
The PR renames two public functions (`mark_failed_threshold` and `create_issue_platform_occurrence`) and moves them across module boundaries, but no tests are touched in the diff. `tests/sentry/monitors/logic/test_mark_failed.py` exists and exercises this code path; any `patch("sentry.monitors.logic.mark_failed.create_issue_platform_occurrence", ...)` or `from sentry.monitors.logic.mark_failed import mark_failed_threshold` left over there will silently become a no-op patch (patching a name that no longer exists on the module raises `AttributeError` at test collection time, which is the safe failure mode — but patches of the *old path* against a now-missing symbol can mask coverage). Confirm with:

```bash
rg -n "mark_failed_threshold|create_issue_platform_occurrence|SimpleCheckIn" tests/
```

and update any references to the new module paths. If call sites elsewhere (`check_timeout.py`, `check_missed.py`, `monitor_consumer.py`) were touched as part of an adjacent PR, note that in the description so reviewers can correlate.

:yellow_circle: [correctness] Dead `logger` declarations in both new modules in `src/sentry/monitors/logic/incidents.py`:5 and `src/sentry/monitors/logic/incident_occurrence.py`:15 (confidence: 95)
Both new files do `logger = logging.getLogger(__name__)` but neither file actually calls `logger.*` anywhere. The `import logging` + `logger = ...` lines were carried over from `mark_failed.py` mechanically. Either remove them, or — preferably — instrument the moved logic (e.g., log on `get_or_create` hitting the existing-incident branch, or on the "muted monitor skipped" path) so that post-refactor log attribution under the new module names is useful. Right now the only visible side effect of the refactor on logging is that future `logger.*` calls added here will emit under `sentry.monitors.logic.incidents` / `…incident_occurrence` rather than `…mark_failed`, which may affect log-based alerts keyed on the old logger name.

```suggestion
# if you don't plan to log from this module, drop:
import logging
logger = logging.getLogger(__name__)
```

:yellow_circle: [consistency] Module surface area widened without visibility markers in `src/sentry/monitors/logic/incident_occurrence.py`:122 (confidence: 70)
`HUMAN_FAILURE_STATUS_MAP`, `SINGULAR_HUMAN_FAILURE_MAP`, `get_failure_reason`, `get_monitor_environment_context`, and `create_incident_occurrence` are all public at the module level of the new file. Inside `mark_failed.py` they were effectively internal (callers only used `mark_failed`). If they're only consumed by `incidents.py` and by nothing outside `sentry.monitors.logic`, prefix the helpers with `_` or keep the existing names but add an `__all__` that lists only `create_incident_occurrence`. Otherwise the refactor has turned implementation details into a re-exportable API surface that downstream code can now couple to.

## Nitpicks

:white_circle: [consistency] `try_incident_threshold` is a less descriptive name than `mark_failed_threshold` in `src/sentry/monitors/logic/incidents.py`:12 (confidence: 60)
The function does more than "try": it mutates `monitor_env.status`, persists via `save(update_fields=…)`, creates a `MonitorIncident` row, emits occurrences to Kafka through `create_incident_occurrence`, and fires the `monitor_environment_failed` signal. `try_` conventionally signals a cheap best-effort predicate. Consider `evaluate_incident_threshold` or `process_incident_threshold`. Since this is a rename-only change already, the additional cost of a better name is minimal.

:white_circle: [consistency] Pre-existing docstring typo preserved while the function is being relocated in `src/sentry/monitors/logic/incident_occurrence.py`:138 (confidence: 95)
`"""Builds a humam readible string from a list of failed check-ins.` — two typos ("humam" → "human", "readible" → "readable"). Easy fix to slip in while the block is already being moved.

:white_circle: [consistency] `TYPE_CHECKING` import of `_StrPromise` is duplicated rather than centralized in `src/sentry/monitors/logic/incident_occurrence.py`:10 (confidence: 40)
Not wrong, just a note: the `_StrPromise` annotation for `HUMAN_FAILURE_STATUS_MAP` / `SINGULAR_HUMAN_FAILURE_MAP` is now module-local here. If a future extraction pulls those maps into their own i18n module, the import guard moves again. Low-priority.

## Risk Metadata

**Risk Score:** 40/100 (MEDIUM)

| Factor | Score | Details |
|---|---|---|
| Blast radius | 45 | 5 other modules import `sentry.monitors.logic.mark_failed` (`clock_tasks/check_timeout`, `clock_tasks/check_missed`, `consumers/monitor_consumer`, plus 2 test modules). `mark_failed.mark_failed()` remains the entry point with the same signature, so call sites are unaffected; the risk is entirely in symbols-by-name references (mocks, direct imports of the renamed helpers). |
| Sensitive paths hit | 40 | `src/sentry/monitors/` — incident creation / alert routing path. A regression here produces silent alert-loss or alert-storms, both high-impact. |
| Test coverage delta | 60 | No test changes in diff. Existing `tests/sentry/monitors/logic/test_mark_failed.py` presumably still covers the end-to-end `mark_failed` → incident creation flow via the unchanged entry point, which mitigates this, but the renamed internal symbols are not re-pinned. |
| Code-move fidelity | 10 | Extracted blocks match the removed blocks line-for-line apart from the two renames. |
| Cyclic import risk | 10 | Import graph: `mark_failed → incidents → incident_occurrence → types`; no cycles. |
| AI-authored likelihood | 20 | LOW — the change is a typical human refactor (kept pre-existing typos, kept unused `logger` carryover, used existing style). |

**Focus areas:**
- `src/sentry/monitors/logic/mark_failed.py` — verify the single surviving call-through to `try_incident_threshold` returns the same bool semantics the rest of the pipeline expects.
- `tests/sentry/monitors/logic/` — grep for old symbol names before merge.

**Recommendation:** `needs-discussion` — not because the code move is risky (it isn't; it's very clean), but because a rename-only refactor touching alerting code should land with its tests re-pointed in the same PR, or at minimum with an explicit note in the description confirming the test suite was re-run against the renamed symbols. The underlying logic change is safe to approve once test linkage is verified.

## Metadata
- Agents: orchestrator direct-analysis (risk-scorer + correctness + consistency + cross-file-impact inlined due to narrow, contained diff and local benchmark budget)
- Source: `gh pr view/diff 80528 --repo getsentry/sentry`
- Cross-repo symbol search via GitHub Code Search API confirmed 0 live references to the old names on `master` at review time.
