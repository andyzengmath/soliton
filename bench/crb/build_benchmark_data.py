#!/usr/bin/env python3
"""Build a CRB-compatible benchmark_data.json from Soliton Phase 3 review markdown files.

CRB's step1_download_prs.py normally aggregates reviews by listing repos in a GitHub
org and fetching comments per fork. The Soliton Phase 3 path skips forking entirely
and instead runs Soliton locally, so we synthesize the same JSON structure from the
markdown files under bench/crb/phase3-reviews/<slug>.md.

Output schema (matches step2/step3 expectations):

{
  "<golden_url>": {
    "pr_title": "...",
    "original_url": "...",
    "source_repo": "sentry",
    "golden_comments": [{"comment": "...", "severity": "High"}, ...],
    "reviews": [
      {
        "tool": "soliton",
        "repo_name": "soliton-local-<slug>",
        "pr_url": "<slug>-local",
        "review_comments": [
          {"path": null, "line": null, "body": "<full markdown>", "created_at": null}
        ]
      }
    ]
  }
}

Usage:
  python3 bench/crb/build_benchmark_data.py \\
      --reviews-dir bench/crb/phase3-reviews \\
      --golden-dir ../code-review-benchmark/offline/golden_comments \\
      --output ../code-review-benchmark/offline/results/benchmark_data.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# slug → upstream URL. Derived from benchmark-prs.json via the same convention
# used in phase3-dispatch-list.txt. Rebuild this map by reading the JSON rather
# than hardcoding if you edit that file.
URL_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)")
LANG_PRE = {"Java": "java", "Python": "python", "Go": "go", "TypeScript": "ts", "Ruby": "ruby"}


def slug_for(pr: dict) -> str | None:
    m = URL_RE.match(pr["url"])
    if not m:
        return None
    _, repo, num = m.groups()
    pre = LANG_PRE.get(pr["language"], "x")
    # Match the slug convention used in phase3-dispatch-list.txt:
    #   cal.com → calcom (dots stripped), keep hyphens for discourse-graphite etc.
    short = repo.replace(".git", "").replace(".", "")
    return f"{pre}-{short}-{num}"


def load_goldens(golden_dir: Path) -> dict[str, dict]:
    golden = {}
    for path in sorted(golden_dir.glob("*.json")):
        with path.open() as f:
            for entry in json.load(f):
                url = entry["url"]
                golden[url] = {
                    "pr_title": entry.get("pr_title"),
                    "original_url": entry.get("original_url"),
                    "az_comment": entry.get("az_comment"),
                    "comments": entry.get("comments", []),
                    "source_file": path.name,
                }
    return golden


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reviews-dir", type=Path, required=True)
    parser.add_argument("--golden-dir", type=Path, required=True)
    parser.add_argument("--benchmark-prs", type=Path,
                        default=Path("bench/crb/benchmark-prs.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tool", default="soliton")
    args = parser.parse_args()

    with args.benchmark_prs.open() as f:
        benchmark_prs = json.load(f)

    golden = load_goldens(args.golden_dir)
    print(f"Loaded {len(golden)} golden comment entries")

    output: dict[str, dict] = {}
    n_ok = 0
    n_missing = 0
    n_skip = 0

    for pr in benchmark_prs:
        url = pr["url"]
        if not URL_RE.match(url):
            n_skip += 1
            continue
        slug = slug_for(pr)
        if not slug:
            n_skip += 1
            continue
        review_path = args.reviews_dir / f"{slug}.md"
        if not review_path.exists() or review_path.stat().st_size == 0:
            print(f"  [missing] {slug}")
            n_missing += 1
            continue

        body = review_path.read_text(encoding="utf-8")
        golden_data = golden.get(url)
        if not golden_data:
            print(f"  [no-golden] {url} — skipping")
            n_missing += 1
            continue

        output[url] = {
            "pr_title": golden_data["pr_title"],
            "original_url": golden_data.get("original_url"),
            "source_repo": URL_RE.match(url).group(2),
            "golden_comments": golden_data["comments"],
            "golden_source_file": golden_data["source_file"],
            "az_comment": golden_data.get("az_comment"),
            "reviews": [
                {
                    "tool": args.tool,
                    "repo_name": f"soliton-local-{slug}",
                    "pr_url": f"{url}#soliton-local",
                    "review_comments": [
                        {
                            "path": None,
                            "line": None,
                            "body": body,
                            "created_at": None,
                        }
                    ],
                }
            ],
        }
        n_ok += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nWrote {args.output}:")
    print(f"  reviewed   : {n_ok}")
    print(f"  missing    : {n_missing}")
    print(f"  skipped    : {n_skip}")


if __name__ == "__main__":
    main()
