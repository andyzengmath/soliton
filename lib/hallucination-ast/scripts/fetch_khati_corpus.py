"""Fetch the Khati 2026 replication-package corpus for the shipping gate.

Clones https://github.com/WM-SEMERU/Hallucinations-in-Code at --depth 1,
copies `hallucination_pipeline/data/generated_dataset.csv` into
lib/hallucination-ast/tests/fixtures/khati-2026/dataset.csv, and drops a
NOTICE.md with attribution. The upstream repo has no LICENSE file; the
dataset is referenced for research reproduction under fair-use
attribution and is not redistributed in the Soliton tree.

Run once before `pytest tests/test_khati_corpus.py`:

    python lib/hallucination-ast/scripts/fetch_khati_corpus.py
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_URL = "https://github.com/WM-SEMERU/Hallucinations-in-Code.git"
CORPUS_RELPATH = "hallucination_pipeline/data/generated_dataset.csv"


NOTICE = """\
# Khati 2026 replication corpus

This directory is populated by running
`scripts/fetch_khati_corpus.py`. The dataset it downloads is drawn
verbatim from the authors' public replication package:

    https://github.com/WM-SEMERU/Hallucinations-in-Code
    hallucination_pipeline/data/generated_dataset.csv

Cite:

    Khati, Dipin; Rodriguez-Cardenas, Daniel; Pantzer, Paul; Poshyvanyk, Denys.
    "Detecting and Correcting Hallucinations in LLM-Generated Code via
    Deterministic AST Analysis." arXiv:2601.19106 (FORGE 2026).

Use: research validation of hallucination_ast's precision / recall
against an external baseline. Do NOT ship this file inside any
Soliton artifact — it is fetched on-demand, not vendored.

The upstream repository carries no LICENSE file at the time of fetch;
downstream redistribution is limited to local validation runs.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "khati-2026",
        help="Destination directory for dataset.csv (default: tests/fixtures/khati-2026).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite dataset.csv if it already exists.",
    )
    args = parser.parse_args()

    dest: Path = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    csv_path = dest / "dataset.csv"

    if csv_path.exists() and not args.force:
        print(f"already present: {csv_path}  (pass --force to refresh)")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = Path(tmp) / "repo"
        print(f"cloning {REPO_URL} ...")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", REPO_URL, str(repo_dir)],
                check=True,
                stderr=subprocess.STDOUT,
            )
        except subprocess.CalledProcessError as e:
            print(f"ERROR: git clone failed: {e}", file=sys.stderr)
            return 1

        src = repo_dir / CORPUS_RELPATH
        if not src.is_file():
            print(f"ERROR: expected corpus at {src}", file=sys.stderr)
            return 1

        shutil.copy2(src, csv_path)
        (dest / "NOTICE.md").write_text(NOTICE, encoding="utf-8")
        print(f"wrote {csv_path}")
        print(f"wrote {dest / 'NOTICE.md'}")

    print("done. next: pytest lib/hallucination-ast/tests/test_khati_corpus.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
