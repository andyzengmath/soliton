"""CLI entry point for hallucination_ast.

Invocation (both forms work):

    python -m hallucination_ast --diff path/to/change.patch
    hallucination-ast --diff -

Reads a unified diff, runs extract → resolve → check against the interpreter's
site-packages KB, and writes a JSON Report to stdout. Exit code:

    0  — no critical findings (clean, or only improvement / nitpick findings)
    1  — at least one critical finding (identifier_not_found, signature_mismatch_arity)
    2  — input error (missing diff file, malformed diff)

Designed to be shelled out to by agents/hallucination.md Step 2.5.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from .check import check_all
from .extract import extract_from_diff
from .resolve import SitePackagesKB
from .types import report_to_json_dict


@click.command(name="hallucination-ast")
@click.option(
    "--diff",
    "diff_arg",
    type=click.File("r", encoding="utf-8"),
    required=True,
    help="Path to a unified diff, or '-' to read from stdin.",
)
@click.option(
    "--repo-root",
    type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path),
    required=False,
    default=None,
    help=(
        "Repository root. When provided, post-image file content is loaded "
        "from disk (correct for modified files). When omitted, the diff's "
        "added + context lines are used (correct for new files)."
    ),
)
def main(diff_arg, repo_root: Path | None) -> None:
    """Run the AST hallucination pre-check on a unified diff."""
    try:
        diff_text = diff_arg.read()
    except OSError as e:
        click.echo(f"error: failed to read diff: {e}", err=True)
        sys.exit(2)

    refs = extract_from_diff(diff_text, repo_root=repo_root)
    kb = SitePackagesKB()
    report = check_all(refs, kb)

    click.echo(json.dumps(report_to_json_dict(report), indent=2))

    has_critical = any(f.severity == "critical" for f in report.findings)
    sys.exit(1 if has_critical else 0)


if __name__ == "__main__":
    main()
