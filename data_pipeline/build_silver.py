#!/usr/bin/env python3
"""
Build Silver Layer: merge per-repo bronze JSONL.gz files into train, val, and test splits.
"""

import gzip
import yaml
import argparse
import sys
import hashlib
from pathlib import Path


def load_split_map(split_map_file: Path, bronze_repos: list) -> dict:
    if split_map_file and split_map_file.exists():
        with open(split_map_file, "r") as f:
            return yaml.safe_load(f)
    # Generate split map by hashing repo names
    splits = {"train": [], "val": [], "test": []}
    for repo in sorted(bronze_repos):
        h = int(hashlib.md5(repo.encode("utf-8")).hexdigest(), 16) % 100
        if h < 80:
            splits["train"].append(repo)
        elif h < 90:
            splits["val"].append(repo)
        else:
            splits["test"].append(repo)
    return splits


def build_silver(bronze_dir: Path, output_dir: Path, split_map_file: Path = None):
    if not bronze_dir.exists() or not bronze_dir.is_dir():
        print(f"Error: Bronze directory '{bronze_dir}' not found or is not a directory.", file=sys.stderr)
        sys.exit(1)
    # Strip both '.jsonl.gz' suffix to get the raw repo identifier (owner_repo)
    repos = [p.name.replace('.jsonl.gz', '') for p in bronze_dir.glob("*.jsonl.gz")]
    split_map = load_split_map(split_map_file, repos)

    # Prepare output directory
    output_dir.mkdir(parents=True, exist_ok=True)
    split_map_path = output_dir / "split_map.yml"
    with open(split_map_path, "w") as f:
        yaml.dump(split_map, f)

    # Open writers for each split
    writers = {}
    counts = {}
    for split in ["train", "val", "test"]:
        out_file = output_dir / f"{split}.jsonl.gz"
        writers[split] = gzip.open(out_file, "wt", encoding="utf-8")
        counts[split] = 0

    # Copy records from bronze to splits
    for split, repo_list in split_map.items():
        for repo in repo_list:
            bronze_file = bronze_dir / f"{repo}.jsonl.gz"
            if not bronze_file.exists():
                print(f"Warning: Bronze file for repo {repo} not found, skipping.", file=sys.stderr)
                continue
            print(f"Adding repo {repo} to split {split}")
            with gzip.open(bronze_file, "rt", encoding="utf-8") as reader:
                for line in reader:
                    writers[split].write(line)
                    counts[split] += 1

    # Close all writers
    for writer in writers.values():
        writer.close()

    # Write dataset card
    card_path = output_dir / "dataset_card.md"
    with open(card_path, "w") as f:
        f.write(f"# Dataset Card for {output_dir.name}\n\n")
        f.write("## Splits\n\n")
        for split in ["train", "val", "test"]:
            repo_count = len(split_map.get(split, []))
            record_count = counts.get(split, 0)
            f.write(f"- **{split}**: {record_count} records from {repo_count} repositories\n")

    print(f"Silver layer created at {output_dir}")
    print(f"Record counts: {counts}")


def main():
    parser = argparse.ArgumentParser(description="Build silver layer: train/val/test splits from bronze data.")
    parser.add_argument("--bronze-dir", type=str, default="bronze", help="Directory containing per-repo JSONL.gz files.")
    parser.add_argument("--output-dir", type=str, default="dataset/v1", help="Output directory for split files and metadata.")
    parser.add_argument("--split-map", type=str, help="Optional path to a split_map.yml specifying repos per split.")
    args = parser.parse_args()

    split_map_path = Path(args.split_map) if args.split_map else None
    build_silver(Path(args.bronze_dir), Path(args.output_dir), split_map_path)


if __name__ == "__main__":
    main() 