#!/usr/bin/env python3
"""
Build Bronze Layer: for each repository, merge all PR JSONL files into a single gzipped JSONL file.
"""

import gzip
import json
import argparse
import sys
from pathlib import Path
from collections import defaultdict


def build_bronze(input_dir: Path, output_dir: Path):
    if not input_dir.exists() or not input_dir.is_dir():
        print(f"Error: Input directory '{input_dir}' not found or is not a directory.", file=sys.stderr)
        sys.exit(1)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Group aligned JSONL files by owner/repo and merge into bronze files
    groups = defaultdict(list)
    for pr_file in input_dir.rglob("*.jsonl"):
        rel = pr_file.relative_to(input_dir)
        parts = rel.parts
        if len(parts) < 3:
            continue
        owner, repo = parts[0], parts[1]
        groups[(owner, repo)].append(pr_file)
    for (owner, repo), pr_files in sorted(groups.items()):
        repo_key = f"{owner}/{repo}"
        safe_name = f"{owner}_{repo}"
        out_file = output_dir / f"{safe_name}.jsonl.gz"
        if out_file.exists():
            print(f"Skipping existing bronze file: {out_file}")
            continue
        print(f"Building bronze for repository: {repo_key}")
        with gzip.open(out_file, "wt", encoding="utf-8") as writer:
            for pr_file in sorted(pr_files):
                for line in pr_file.open("r", encoding="utf-8"):
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError as e:
                        print(f"Warning: Skipping invalid JSON in file {pr_file}: {e}", file=sys.stderr)
                        continue
                    rec["repo"] = repo_key
                    writer.write(json.dumps(rec) + "\n")
        print(f"Created bronze file: {out_file}")


def main():
    parser = argparse.ArgumentParser(description="Build bronze layer: per-repo gzipped JSONL from per-PR JSONL files.")
    parser.add_argument("--input-dir", type=str, default="processed", help="Directory containing per-PR JSONL directories by repo.")
    parser.add_argument("--output-dir", type=str, default="bronze", help="Directory to output per-repo JSONL.gz files.")
    args = parser.parse_args()
    build_bronze(Path(args.input_dir), Path(args.output_dir))


if __name__ == "__main__":
    main() 