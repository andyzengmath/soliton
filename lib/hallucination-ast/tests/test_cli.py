"""Tests for the `python -m hallucination_ast` CLI.

Uses click.testing.CliRunner to drive the entry point in-process. The real
SitePackagesKB is used so the tests double as end-to-end integration for
extract → resolve → check → JSON serialization. Deps (requests, numpy) are
guaranteed installed in the dev env.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner


pytest.importorskip("requests")


CLEAN_DIFF = """\
--- /dev/null
+++ b/foo.py
@@ -0,0 +1,3 @@
+import requests
+
+response = requests.get("https://example.com")
"""


HALLUCINATED_DIFF = """\
--- /dev/null
+++ b/foo.py
@@ -0,0 +1,3 @@
+import requests
+
+response = requests.gett("https://example.com")
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_cli_clean_diff_exits_zero_and_emits_empty_findings(runner, tmp_path):
    from hallucination_ast.cli import main

    diff_path = tmp_path / "clean.patch"
    diff_path.write_text(CLEAN_DIFF)

    result = runner.invoke(main, ["--diff", str(diff_path)])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    # Critical findings: zero. Non-critical (e.g. deprecated) also zero on
    # the clean sample.
    assert payload["findings"] == []


def test_cli_hallucinated_diff_emits_finding_with_exit_one(runner, tmp_path):
    from hallucination_ast.cli import main

    diff_path = tmp_path / "hallucinated.patch"
    diff_path.write_text(HALLUCINATED_DIFF)

    result = runner.invoke(main, ["--diff", str(diff_path)])
    # Non-zero on any critical finding — Soliton agent uses this to gate.
    assert result.exit_code == 1, result.output

    payload = json.loads(result.output)
    assert len(payload["findings"]) >= 1
    f = payload["findings"][0]
    assert f["rule"] == "identifier_not_found"
    assert f["symbol"] == "requests.gett"
    assert f["severity"] == "critical"
    assert f["confidence"] == 100
    assert f["suggestedFix"] == "get"


def test_cli_reads_diff_from_stdin_with_dash(runner):
    from hallucination_ast.cli import main

    result = runner.invoke(main, ["--diff", "-"], input=HALLUCINATED_DIFF)
    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert any(f["rule"] == "identifier_not_found" for f in payload["findings"])


def test_cli_emits_camelcase_stats(runner, tmp_path):
    from hallucination_ast.cli import main

    diff_path = tmp_path / "d.patch"
    diff_path.write_text(CLEAN_DIFF)
    result = runner.invoke(main, ["--diff", str(diff_path)])
    payload = json.loads(result.output)

    stats = payload["stats"]
    # Spec's TS interface keys must be present as camelCase.
    for key in ("totalReferences", "resolvedOk", "resolvedBad", "unresolved", "wallMs"):
        assert key in stats, stats
    assert stats["totalReferences"] >= 1  # at least the import.


def test_cli_missing_diff_file_exits_two(runner, tmp_path):
    from hallucination_ast.cli import main

    missing = tmp_path / "does_not_exist.patch"
    result = runner.invoke(main, ["--diff", str(missing)])
    assert result.exit_code == 2


def test_cli_empty_diff_is_valid_zero_report(runner):
    from hallucination_ast.cli import main

    result = runner.invoke(main, ["--diff", "-"], input="")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["findings"] == []
    assert payload["stats"]["totalReferences"] == 0


def test_cli_unresolved_forwards_not_flagged(runner):
    """Imports of non-installed modules are unresolved (known=False), not
    hallucinations — must NOT raise the exit code."""
    from hallucination_ast.cli import main

    diff = (
        "--- /dev/null\n+++ b/foo.py\n@@ -0,0 +1,2 @@\n"
        "+import definitely_not_installed_pkg\n"
        "+definitely_not_installed_pkg.do_thing()\n"
    )
    result = runner.invoke(main, ["--diff", "-"], input=diff)
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["findings"] == []
    assert payload["stats"]["unresolved"] >= 1


def test_cli_module_entry_point_exists(runner):
    """`python -m hallucination_ast` needs a __main__.py in the package."""
    import importlib
    import importlib.util

    spec = importlib.util.find_spec("hallucination_ast.__main__")
    assert spec is not None, (
        "hallucination_ast.__main__ missing — `python -m hallucination_ast` "
        "will fail"
    )
