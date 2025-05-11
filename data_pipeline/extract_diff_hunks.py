import argparse
import json
import sys
from pathlib import Path
from unidiff import PatchSet
from io import StringIO

def extract_hunks_to_jsonl(diff_file_path, output_jsonl_path, pr_identifier):
    """
    Parses a .diff file, extracts all hunks, and writes them to a JSONL file.
    Each line in the output is a JSON object representing one hunk.
    """
    try:
        with open(diff_file_path, 'r', encoding='utf-8') as f_diff:
            diff_text = f_diff.read()
        
        if not diff_text.strip():
            print(f"Warning: Diff file {diff_file_path} is empty. No hunks to extract.", file=sys.stderr)
            # Create an empty output file to signify processing attempt
            Path(output_jsonl_path).touch()
            return True

        parsed_diff = PatchSet(StringIO(diff_text))
        
        hunks_extracted = 0
        with open(output_jsonl_path, 'w', encoding='utf-8') as f_out:
            for patched_file in parsed_diff:
                for hunk in patched_file:
                    hunk_record = {
                        "pr_identifier": pr_identifier,
                        "source_file_path": patched_file.source_file,
                        "target_file_path": patched_file.target_file,
                        "diff_hunk": str(hunk) # Get the string representation of the hunk
                    }
                    json.dump(hunk_record, f_out)
                    f_out.write('\n')
                    hunks_extracted += 1
        
        print(f"Successfully extracted {hunks_extracted} hunks from '{diff_file_path}' to '{output_jsonl_path}'.")
        return True

    except FileNotFoundError:
        print(f"Error: Input diff file not found: {diff_file_path}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error processing diff file {diff_file_path}: {e}", file=sys.stderr)
        # Clean up potentially partial output file
        if Path(output_jsonl_path).exists():
            try:
                Path(output_jsonl_path).unlink()
            except OSError:
                pass
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract diff hunks from a PR's .diff file to JSONL format.")
    parser.add_argument("--input-pr-diff-file", required=True, type=Path,
                        help="Path to the input .diff file for the Pull Request.")
    parser.add_argument("--output-jsonl-file", required=True, type=Path,
                        help="Path to save the output JSONL file containing extracted hunks.")
    parser.add_argument("--pr-identifier", required=True, 
                        help="String identifier for the PR (e.g., 'owner/repo/number' or URL) to embed in output records.")
    # --config is not used by this script but can be accepted for consistency if called by an orchestrator
    parser.add_argument("--config", help="Optional path to a YAML configuration file (not used by this script directly).")

    args = parser.parse_args()

    args.output_jsonl_file.parent.mkdir(parents=True, exist_ok=True)

    if extract_hunks_to_jsonl(args.input_pr_diff_file, args.output_jsonl_file, args.pr_identifier):
        sys.exit(0)
    else:
        sys.exit(1) 