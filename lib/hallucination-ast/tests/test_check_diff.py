"""Tests for check.check_diff — the diff-level analog of check_source.

Wraps extract_from_diff + per-file import-context + local-name awareness
+ rule execution + per-file merging, so CLI callers get the same
precision discipline check_source has for standalone snippets.
"""
from __future__ import annotations

import pytest


def test_check_diff_clean_file_no_findings(tmp_path):
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    # Post-image exists on disk (simulating repo_root) and is clean.
    foo = tmp_path / "foo.py"
    foo.write_text(
        "import requests\n"
        "def fetch(url):\n"
        "    return requests.get(url).json()\n"
    )
    diff = (
        "--- /dev/null\n+++ b/foo.py\n@@ -0,0 +1,3 @@\n"
        "+import requests\n"
        "+def fetch(url):\n"
        "+    return requests.get(url).json()\n"
    )
    report = check_diff(diff, tmp_path, SitePackagesKB())
    assert report.findings == [], report.findings


def test_check_diff_respects_local_variable_names(tmp_path):
    """A local variable `numbers = set()` followed by `numbers.add(x)` must
    NOT be flagged as a missing-import on the stdlib `numbers` module.

    This is the false positive that surfaced on lib/hallucination-ast's
    own dogfood run: real `numbers` stdlib module is importable, so
    resolve succeeds, but `add` isn't in dir(numbers) — identifier_not_found.
    The fix is to detect `numbers` as a local name and skip the check.
    """
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    foo = tmp_path / "foo.py"
    foo.write_text(
        "def collect():\n"
        "    numbers: set[int] = set()\n"
        "    numbers.add(1)\n"
        "    return numbers\n"
    )
    diff = (
        "--- /dev/null\n+++ b/foo.py\n@@ -0,0 +1,4 @@\n"
        "+def collect():\n"
        "+    numbers: set[int] = set()\n"
        "+    numbers.add(1)\n"
        "+    return numbers\n"
    )
    report = check_diff(diff, tmp_path, SitePackagesKB())
    assert report.findings == [], report.findings


def test_check_diff_flags_typo_method_only_on_added_lines(tmp_path):
    """Context-line references must not be flagged; added-line only."""
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    foo = tmp_path / "foo.py"
    foo.write_text(
        "import requests\n"
        "def fetch(url):\n"
        "    return requests.get(url)\n"
        "def broken(url):\n"
        "    return requests.gett(url)\n"
    )
    # Only lines 4-5 are added (the broken function). Lines 1-3 are context.
    diff = (
        "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,5 @@\n"
        " import requests\n"
        " def fetch(url):\n"
        "     return requests.get(url)\n"
        "+def broken(url):\n"
        "+    return requests.gett(url)\n"
    )
    report = check_diff(diff, tmp_path, SitePackagesKB())
    assert len(report.findings) == 1
    assert report.findings[0].symbol == "requests.gett"
    assert report.findings[0].line == 5


def test_check_diff_missing_import_only_flags_added_line(tmp_path):
    """If the added line references `pd.something` but there's NO
    `import pandas as pd` in the file, flag it."""
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    foo = tmp_path / "foo.py"
    foo.write_text("def bad():\n    return pd.DataFrame()\n")
    diff = (
        "--- /dev/null\n+++ b/foo.py\n@@ -0,0 +1,2 @@\n"
        "+def bad():\n"
        "+    return pd.DataFrame()\n"
    )
    report = check_diff(diff, tmp_path, SitePackagesKB())
    assert any(f.rule == "identifier_not_found" and "pd" in f.symbol for f in report.findings)


def test_check_diff_non_python_file_skipped(tmp_path):
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    diff = "--- /dev/null\n+++ b/README.md\n@@ -0,0 +1,1 @@\n+import requests\n"
    report = check_diff(diff, tmp_path, SitePackagesKB())
    assert report.findings == []
    assert report.unresolved == []


def test_check_diff_merges_reports_across_multiple_files(tmp_path):
    """A diff touching two .py files yields findings from each, merged."""
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    (tmp_path / "a.py").write_text("import requests\ndef f(): return requests.gett('u')\n")
    (tmp_path / "b.py").write_text("import os\ndef g(p): os.makedirs(p, recursive=True)\n")
    diff = (
        "diff --git a/a.py b/a.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n+++ b/a.py\n@@ -0,0 +1,2 @@\n"
        "+import requests\n"
        "+def f(): return requests.gett('u')\n"
        "diff --git a/b.py b/b.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n+++ b/b.py\n@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+def g(p): os.makedirs(p, recursive=True)\n"
    )
    report = check_diff(diff, tmp_path, SitePackagesKB())
    symbols = sorted(f.symbol for f in report.findings)
    assert "requests.gett" in symbols
    assert "os.makedirs" in symbols


def test_check_diff_rejects_path_traversal(tmp_path):
    """An attacker diff targeting `b/../leak.py` must NOT read a file
    outside repo_root. Regression for PR #26 security review."""
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    # Plant a file outside the repo with an import that WOULD produce a
    # ref if read — so if the read escapes containment, leaked-content
    # refs would show up in the report.
    outside = tmp_path / "leak.py"
    outside.write_text("import secrets\nsecrets.token_hex(16)\n")

    repo = tmp_path / "repo"
    repo.mkdir()

    diff = (
        "--- /dev/null\n+++ b/../leak.py\n@@ -0,0 +1,2 @@\n"
        "+import os\n"
        "+os.makedirs('x', exist_ok=True)\n"
    )
    report = check_diff(diff, repo, SitePackagesKB())
    # Crucial: no 'secrets' symbol from the escaping file.
    leaked_symbols = [
        (f.symbol, r.symbol)
        for f in report.findings
        for r in report.unresolved
        if "secrets" in f.symbol or "secrets" in r.symbol
    ]
    assert all(
        "secrets" not in (r.symbol or "") for r in report.unresolved
    ), report.unresolved
    assert all(
        "secrets" not in (f.symbol or "") for f in report.findings
    ), report.findings


def test_check_diff_rejects_absolute_target(tmp_path):
    """Absolute path targets must not bypass repo_root containment."""
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    repo = tmp_path / "repo"
    repo.mkdir()
    diff = (
        "--- /dev/null\n+++ b//etc/passwd.py\n@@ -0,0 +1,1 @@\n+x = 1\n"
    )
    # Must not raise and must not read anything.
    report = check_diff(diff, repo, SitePackagesKB())
    assert report.findings == []


def test_check_diff_stats_populated(tmp_path):
    from hallucination_ast.check import check_diff
    from hallucination_ast.resolve import SitePackagesKB

    (tmp_path / "a.py").write_text("import requests\ndef f(u): return requests.get(u)\n")
    diff = (
        "--- /dev/null\n+++ b/a.py\n@@ -0,0 +1,2 @@\n"
        "+import requests\n"
        "+def f(u): return requests.get(u)\n"
    )
    report = check_diff(diff, tmp_path, SitePackagesKB())
    assert report.stats.total_references >= 2
    assert report.stats.wall_ms >= 0
