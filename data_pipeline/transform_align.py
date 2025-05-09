import os
import re
import argparse
import json
import yaml
import sys
import time
from pathlib import Path
from unidiff import PatchSet
from io import StringIO

# Basic configuration loading (adapt error messages if needed)
def load_config(config_path):
    """Loads the YAML configuration file."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        if not config:
            raise ValueError("Config file is empty.")
        # We don't strictly need paths from config here, but could validate
        return config
    except FileNotFoundError:
        print(f"Error: Config file not found at {config_path}", file=sys.stderr)
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"Error parsing config file {config_path}: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error in config file structure: {e}", file=sys.stderr)
        sys.exit(1)

def parse_owner_repo_pr_from_filename(filename):
    """Extracts owner, repo, and PR number from filenames like owner_repo_prnumber.diff"""
    # Handle potential suffixes like .diff or _comments.jsonl
    base_name = filename.split('.')[0]
    if base_name.endswith('_comments'):
        base_name = base_name[:-len('_comments')]

    match = re.match(r"([^_]+)_([^_]+)_(\d+)", base_name)
    if match:
        return match.groups()
    return None, None, None

def find_hunk_and_line_for_comment(parsed_diff, comment_path, comment_pos):
    """
    Finds the specific hunk and line object in the parsed diff corresponding
    to a comment path and position (1-based index within the file's diff).
    Returns a tuple (patched_file, hunk, line) or (None, None, None) if not found.
    """
    if not comment_path or comment_pos is None or comment_pos <= 0:
        print(f"Debug: Invalid comment_path ('{comment_path}') or comment_pos ({comment_pos})")
        return None, None, None

    patched_file_obj = None # This will store the PatchedFile object
    # Find the file in the parsed diff that matches the comment's path
    for file_diff in parsed_diff: # file_diff is a PatchedFile
        # unidiff paths might start with 'a/' or 'b/' - try to match flexibly
        if file_diff.source_file == comment_path or \
           file_diff.target_file == comment_path or \
           file_diff.path == comment_path: # path attribute combines source/target heuristically
            patched_file_obj = file_diff
            break
        # Fallback: Check if the comment path is a suffix of the unidiff path
        # (e.g., comment path 'src/main.py', unidiff path 'b/src/main.py')
        elif comment_path and (file_diff.source_file.endswith('/' + comment_path) or \
                              file_diff.target_file.endswith('/' + comment_path)):
             print(f"Debug: Matched comment path '{comment_path}' as suffix of diff path '{file_diff.path}'")
             patched_file_obj = file_diff
             break

    if not patched_file_obj:
        print(f"Debug: Could not find file matching path '{comment_path}' in the diff.")
        return None, None, None

    # Position in comments refers to the line number within the *diff view* of that file,
    # counting only added and context lines. It's a 1-based index.
    current_pos_count = 0
    for hunk in patched_file_obj: # Iterate through hunks of the found PatchedFile
        for line in hunk: # Iterate through lines within the hunk
            # Only count lines that appear in the final diff view that positions usually refer to
            if line.is_context or line.is_added:
                current_pos_count += 1
                if current_pos_count == comment_pos:
                    # Found the line, return the PatchedFile, Hunk, and Line
                    return patched_file_obj, hunk, line

    # If loop completes without finding the position
    print(f"Debug: Comment position {comment_pos} not found in file '{comment_path}' (max pos checked: {current_pos_count}). This might indicate an outdated comment or position mismatch.")
    return None, None, None

def get_line_type(line):
    """Determines the type of a diff line."""
    if line.is_added: return "added"
    if line.is_removed: return "removed"
    if line.is_context: return "context"
    return "unknown" # Should not happen with standard diffs

def process_pr_files(diff_path, comments_path, output_path, debug=False):
    """Processes a single PR's diff and comments to create aligned data."""
    print(f"Processing PR:\n  Diff: {diff_path}\n  Comments: {comments_path}")
    aligned_data = []
    skipped_comments = 0

    try:
        # Read and parse the diff file
        with open(diff_path, 'r', encoding='utf-8') as f_diff:
            diff_text = f_diff.read()
        # Use StringIO because PatchSet expects a file-like object or string iterator
        # diff_text is already a string, so no encoding needed for PatchSet here.
        parsed_diff = PatchSet(StringIO(diff_text))

        # Read the comments JSONL file
        with open(comments_path, 'r', encoding='utf-8') as f_comments:
            for line_num, line_text in enumerate(f_comments):
                if not line_text.strip():
                    continue
                try:
                    comment = json.loads(line_text)
                except json.JSONDecodeError as json_e:
                    print(f"Warning: Skipping invalid JSON in {comments_path}, line {line_num + 1}: {json_e}", file=sys.stderr)
                    skipped_comments += 1
                    continue

                # Get the position to use for matching. Use 'position' if available,
                # fallback to 'original_position' (comments might become outdated).
                comment_pos = comment.get('position')
                comment_path = comment.get('path')

                if comment_pos is None:
                    # Try original_position if position is null (might be an outdated comment)
                    comment_pos = comment.get('original_position')
                    if comment_pos is not None and debug:
                         print(f"Debug: Using 'original_position' ({comment_pos}) for comment ID {comment.get('id')} on path '{comment_path}'")

                if not comment_path or comment_pos is None:
                    if debug:
                        print(f"Debug: Skipping comment ID {comment.get('id')} due to missing path ('{comment_path}') or position ({comment_pos}).")
                    skipped_comments += 1
                    continue

                # Find the corresponding line in the parsed diff
                # Now expecting patched_file, hunk, and diff_line
                matched_patched_file, hunk, diff_line = find_hunk_and_line_for_comment(parsed_diff, comment_path, comment_pos)

                if matched_patched_file and hunk and diff_line: # Check all three
                    line_type = get_line_type(diff_line)
                    # Content remove leading '+' '-' ' '
                    line_content = diff_line.value

                    aligned_record = {
                        "comment_id": comment.get('id'),
                        "comment_user_login": comment.get('user_login'),
                        "comment_body": comment.get('body'),
                        "comment_created_at": comment.get('created_at'),
                        "comment_html_url": comment.get('html_url'),
                        "comment_path": comment_path,
                        "comment_position": comment_pos, # The position used for matching
                        "comment_original_position": comment.get('original_position'), # Keep original too
                        "comment_commit_id": comment.get('commit_id'),
                        "comment_original_commit_id": comment.get('original_commit_id'),
                        "diff_line_content": line_content,
                        "diff_line_type": line_type,
                        # Line numbers are 0 if not applicable (e.g., target for removed line)
                        "diff_line_source_no": diff_line.source_line_no,
                        "diff_line_target_no": diff_line.target_line_no,
                        "diff_hunk_header": hunk.section_header.strip(),
                        "diff": comment.get('diff_hunk'),        # full diff hunk header
                        "side": comment.get('side'),            # LEFT or RIGHT from comment
                        # Compute 0-based offset within this hunk based on side
                        "line_offset": (
                            (diff_line.target_line_no - hunk.target_start)
                            if (diff_line.is_context or diff_line.is_added)
                            else (diff_line.source_line_no - hunk.source_start)
                        ),
                        "diff_file_source": matched_patched_file.source_file, # Use PatchedFile attribute
                        "diff_file_target": matched_patched_file.target_file, # Use PatchedFile attribute
                    }
                    aligned_data.append(aligned_record)
                else:
                    if debug:
                        print(f"Debug: Could not align comment ID {comment.get('id')} (path: '{comment_path}', pos: {comment_pos}) to a diff line.")
                    skipped_comments += 1

        # Write the aligned data to the output JSONL file
        if aligned_data:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, 'w', encoding='utf-8') as f_out:
                for record in aligned_data:
                    json.dump(record, f_out)
                    f_out.write('\n')
            print(f"Successfully aligned {len(aligned_data)} comments. Skipped {skipped_comments}.")
            print(f"Saved aligned data to: {output_path}")
            return True
        else:
            print(f"No comments were successfully aligned for this PR. Skipped {skipped_comments}.")
            # Create an empty file to indicate processing occurred but yielded no results? Or just skip? Let's skip creating empty files.
            # If output_path exists from a previous run, maybe delete it?
            if output_path.exists():
                 output_path.unlink()
            return True # Still considered success as the process ran

    except FileNotFoundError as fnf_e:
        print(f"Error: Input file not found during processing - {fnf_e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error processing PR {diff_path.name}: {e}", file=sys.stderr)
        # Attempt to clean up partial output file if error occurred during writing
        if output_path and output_path.exists():
            try:
                output_path.unlink()
            except OSError:
                pass
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transform raw PR diffs and comments into an aligned JSONL format.")
    parser.add_argument("--config", required=True, help="Path to the YAML configuration file (used for consistency, not paths).")
    parser.add_argument("--input-dir", required=True, help="Directory containing the raw PR data (subdirs like owner/repo/*.diff and *.jsonl).")
    parser.add_argument("--output-dir", required=True, help="Directory to save the transformed JSONL files.")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    start_time = time.time()
    print("Starting transformation process...")
    config = load_config(args.config) # Load config for consistency/future use
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not input_dir.is_dir():
        print(f"Error: Input directory '{input_dir}' not found or is not a directory.", file=sys.stderr)
        sys.exit(1)

    total_prs = 0
    successful_prs = 0
    failed_prs = 0

    # Iterate through the input directory structure (owner/repo/...)
    for diff_file in input_dir.rglob("*.diff"):
        total_prs += 1
        owner, repo, pr_num = parse_owner_repo_pr_from_filename(diff_file.name)
        if not owner:
            print(f"Warning: Skipping file with unexpected name format: {diff_file}", file=sys.stderr)
            continue

        comments_file = diff_file.with_name(f"{owner}_{repo}_{pr_num}_comments.jsonl")
        # Define output path preserving structure
        relative_path = diff_file.parent.relative_to(input_dir)
        output_pr_dir = output_dir / relative_path
        output_file = output_pr_dir / f"{owner}_{repo}_{pr_num}_aligned.jsonl"

        if not comments_file.exists():
            print(f"Warning: Comments file {comments_file.name} not found for diff {diff_file.name}. Skipping PR.", file=sys.stderr)
            failed_prs += 1
            continue

        # Process this PR's files
        print("-" * 20)
        success = process_pr_files(diff_file, comments_file, output_file, args.debug)
        if success:
            successful_prs += 1
        else:
            failed_prs += 1

    end_time = time.time()
    print("=" * 40)
    print("Transformation Summary:")
    print(f"  Total PRs found: {total_prs}")
    print(f"  Successfully processed: {successful_prs}")
    print(f"  Failed/Skipped: {failed_prs}")
    print(f"  Total time: {end_time - start_time:.2f} seconds")
    print(f"  Transformed data saved in: {output_dir}")
    print("=" * 40)

    if failed_prs > 0:
        print("Transformation completed with errors.")
        sys.exit(1)
    elif total_prs == 0:
        print("No diff files found in the input directory.")
        sys.exit(0) # No work to do is not an error
    else:
        print("Transformation completed successfully.")
        sys.exit(0) 