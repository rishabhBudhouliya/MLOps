import os
import re
import argparse
import requests
import json
import yaml
import subprocess
import time
import sys
from pathlib import Path
import tempfile
from github import Github, Auth, RateLimitExceededException, GithubException
from unidiff import PatchSet
from io import StringIO
import datetime

def get_github_token():
    """Retrieves the GitHub token from the environment variable."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub token not found. Set the GITHUB_TOKEN environment variable.")
    return token

def load_config(config_path):
    """Loads the YAML configuration file."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        # Basic validation (add specific checks needed by fetcher)
        if not config:
            raise ValueError("Config file is empty.")
        if 'data_paths' not in config or 'raw' not in config['data_paths']:
             raise ValueError("Missing 'data_paths.raw' in config.")
        if 'rclone_remote_name' not in config or not config['rclone_remote_name']:
            raise ValueError("Missing or empty 'rclone_remote_name' in config.")
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

def parse_github_pr_url(url):
    """Parses a GitHub PR URL to extract owner, repo, and PR number."""
    match = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not match:
        raise ValueError(f"Invalid GitHub PR URL format: {url}")
    owner, repo, pr_number = match.groups()
    return owner, repo, int(pr_number)

def fetch_pr_data(g: Github, pr_url: str):
    """
    Fetches the unified diff and review comments for a given GitHub PR URL.
    Returns tuple (diff_text, comments_list, error_message) 
    comments_list contains comment objects directly from PyGithub.
    Returns (None, None, error_message) on failure.
    """
    try:
        owner, repo_name, pr_number = parse_github_pr_url(pr_url)
        print(f"Fetching data for {owner}/{repo_name}/pull/{pr_number}")
        
        repo = g.get_repo(f"{owner}/{repo_name}")
        pr = repo.get_pull(pr_number)

        # --- Fetch Unified Diff --- 
        # Use requests directly as PyGithub's diff handling can be tricky
        token = get_github_token() # Re-get token in case it expires? Or pass from main?
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3.diff'
        }
        diff_response = requests.get(pr.diff_url, headers=headers, timeout=30)
        diff_response.raise_for_status() # Raise exception for bad status codes
        diff_text = diff_response.text
        if not diff_text:
             print(f"Warning: Diff content for {pr_url} is empty.")

        # --- Fetch Review Comments --- 
        print("Fetching review comments...")
        review_comments_paginated = pr.get_review_comments()
        comments_list = list(review_comments_paginated) # Convert PaginatedList to list
        print(f"Found {len(comments_list)} review comments.")

        return diff_text, comments_list, None

    except GithubException as ge:
        # Handle rate limits specifically
        if isinstance(ge, RateLimitExceededException):
             print(f"Rate limit exceeded while fetching data for {pr_url}. Need to wait.", file=sys.stderr)
             # Consider adding retry/wait logic here if needed within a single PR fetch
        error_msg = f"GitHub API error fetching {pr_url}: {ge}"
        print(error_msg, file=sys.stderr)
        return None, None, error_msg
    except requests.exceptions.RequestException as req_e:
        error_msg = f"Network error fetching diff for {pr_url}: {req_e}"
        print(error_msg, file=sys.stderr)
        return None, None, error_msg
    except ValueError as ve:
        error_msg = f"Error parsing URL {pr_url}: {ve}"
        print(error_msg, file=sys.stderr)
        return None, None, error_msg
    except Exception as e:
        error_msg = f"Unexpected error fetching data for {pr_url}: {e}"
        print(error_msg, file=sys.stderr)
        return None, None, error_msg

def save_comments_to_jsonl(comments, filename):
    """Saves a list of PyGithub comment objects to a JSON Lines file."""
    try:
        with open(filename, 'w') as f:
            for comment in comments:
                # Convert comment object to a dictionary suitable for JSON
                # Select relevant fields to avoid circular references or complex objects
                comment_dict = {
                    'id': comment.id,
                    'user_login': comment.user.login if comment.user else None,
                    'body': comment.body,
                    'path': comment.path,
                    'position': comment.position, # Might be None for outdated comments
                    'original_position': comment.original_position,
                    'commit_id': comment.commit_id,
                    'original_commit_id': comment.original_commit_id,
                    'diff_hunk': comment.diff_hunk,
                    'created_at': comment.created_at.isoformat() if comment.created_at else None,
                    'updated_at': comment.updated_at.isoformat() if comment.updated_at else None,
                    'html_url': comment.html_url,
                    # Add other fields if needed
                }
                json.dump(comment_dict, f)
                f.write('\n')
        print(f"Saved {len(comments)} comments to {filename}")
        return True
    except Exception as e:
        print(f"Error saving comments to {filename}: {e}", file=sys.stderr)
        return False

def find_hunk_and_line_for_comment(parsed_diff, comment_path, comment_pos):
    """
    Finds the specific hunk and line object in the parsed diff corresponding
    to a comment path and position.
    Returns a tuple (hunk, line) or (None, None) if not found.
    """
    if not comment_path or comment_pos is None or comment_pos <= 0:
        return None, None

    target_file = None
    # Find target_file logic (same as before)...
    for file_diff in parsed_diff:
        if file_diff.path == comment_path:
            target_file = file_diff
            break
    if not target_file: # Add fallback if needed
        # ... fallback logic ...
        if not target_file:
             return None, None


    current_pos_count = 0
    for hunk in target_file: # Iterate through hunks
        for line in hunk: # Iterate through lines within the hunk
            if line.is_context or line.is_added:
                current_pos_count += 1
                if current_pos_count == comment_pos:
                    # Found the line, return it AND the current hunk
                    return hunk, line

    # If loop completes without finding the position
    return None, None

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch GitHub PR diff and comments and save raw data locally.")
    parser.add_argument("--config", required=True, help="Path to the YAML configuration file.")
    parser.add_argument("--input-pr-list", required=True, help="Path to a text file containing PR URLs to process, one URL per line.")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode with more verbose output")
    parser.add_argument("--local-output-dir", required=True, help="Directory to save the raw diff and comment files locally.")
    args = parser.parse_args()

    # --- Load Config ---
    config = load_config(args.config) # Keep load_config for basic validation

    # --- Setup local output directory ---
    local_output_path = Path(args.local_output_dir)
    local_output_path.mkdir(parents=True, exist_ok=True)
    print(f"Ensured local output directory exists: {local_output_path}")
    # save_locally flag is removed, it's always true now.
    # Remote upload mode print removed.

    # --- Read PR List ---
    pr_urls_to_process = []
    try:
        with open(args.input_pr_list, 'r') as f:
            for line in f:
                url = line.strip()
                if url:
                    pr_urls_to_process.append(url)
        print(f"Read {len(pr_urls_to_process)} PR URLs from {args.input_pr_list}")
    except FileNotFoundError:
        print(f"Error: Input PR list file not found at {args.input_pr_list}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading input PR list {args.input_pr_list}: {e}", file=sys.stderr)
        sys.exit(1)

    if not pr_urls_to_process:
        print("No PR URLs found in the input file. Exiting.")
        sys.exit(0)
        
    # --- GitHub API Setup --- 
    try:
        token = get_github_token()
        auth = Auth.Token(token)
        # Add sensible timeouts and retries
        g = Github(auth=auth, retry=5, timeout=30)
        print("GitHub client initialized.")
        # Verify connection/token early?
        print(f"Authenticated as user: {g.get_user().login}")
    except Exception as e:
        print(f"Error initializing GitHub client: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Processing Loop ---
    success_count = 0
    failure_count = 0

    # --- EDIT: Removed temp directory logic ---
    # We write directly to the specified local_output_path

    try:
        # --- EDIT: Use local_output_path directly ---
        base_output_dir = local_output_path

        for pr_url in pr_urls_to_process:
            print("-"*40)
            pr_processed_successfully = False # Flag for this specific PR
            local_diff_path = None # Ensure paths are defined for potential cleanup
            local_comments_path = None
            try:
                owner, repo_name, pr_number = parse_github_pr_url(pr_url)
                file_basename = f"{owner}_{repo_name}_{pr_number}"

                # Define local paths within the base output directory
                # Create owner/repo subdirs for better organization
                pr_output_dir = base_output_dir / owner / repo_name
                pr_output_dir.mkdir(parents=True, exist_ok=True)
                local_diff_path = pr_output_dir / f"{file_basename}.diff"
                local_comments_path = pr_output_dir / f"{file_basename}_comments.jsonl"

                # Fetch data
                # Add rate limit handling around the fetch call
                while True:
                    try:
                        diff_text, comments_list, error_msg = fetch_pr_data(g, pr_url)
                        if error_msg:
                             # If fetch_pr_data handled rate limit internally and suggests retry, it might return a specific error
                             # For now, assume any error_msg means failure for this PR here.
                             raise Exception(error_msg)
                        break # Success
                    except RateLimitExceededException as rlee:
                        reset_time = g.get_rate_limit().core.reset
                        wait_seconds = max((reset_time - datetime.datetime.now(datetime.timezone.utc)).total_seconds() + 10, 15) # Wait until reset + buffer
                        print(f"Rate limit hit. Waiting for {wait_seconds:.0f} seconds until {reset_time}...")
                        time.sleep(wait_seconds)
                        # Retry the fetch
                    except GithubException as ge:
                         # Handle other GitHub errors (e.g., 404 Not Found, 50x Server Error)
                         print(f"GitHub API error for {pr_url}: {ge}. Skipping PR.", file=sys.stderr)
                         raise # Re-raise to be caught by the outer loop's exception handler

                # --- EDIT: Simplified saving, removed upload ---
                # Save locally (mandatory now)
                print(f"Saving diff locally to {local_diff_path}")
                with open(local_diff_path, 'w', encoding='utf-8') as f_diff:
                    f_diff.write(diff_text)

                print(f"Saving comments locally to {local_comments_path}")
                if not save_comments_to_jsonl(comments_list, local_comments_path):
                     raise Exception(f"Failed to save comments locally for {pr_url}")

                # --- EDIT: Removed rclone upload block ---

                print(f"Successfully processed PR: {pr_url}")
                pr_processed_successfully = True

            except ValueError as ve:
                print(f"Skipping invalid URL from input list: {pr_url} - {ve}", file=sys.stderr)
            except Exception as pr_e:
                print(f"Error processing PR {pr_url}: {pr_e}", file=sys.stderr)
                # Keep partially created local files for debugging when an error occurs

            finally:
                 if pr_processed_successfully:
                     success_count += 1
                 else:
                     failure_count += 1
                 # --- EDIT: Removed individual file cleanup ---
                 # We are not using a temp dir, keep files in the output dir

    finally:
        # --- EDIT: Removed temp_dir_context exit ---
        pass # No temp dir context manager anymore

    # --- Summary ---
    print("="*40)
    print("Processing Summary:")
    print(f"  Total PRs attempted: {len(pr_urls_to_process)}")
    print(f"  Successfully processed & saved/uploaded: {success_count}")
    print(f"  Failed: {failure_count}")
    print("="*40)

    # Exit with non-zero code if there were failures
    if failure_count > 0:
        print("Completed with errors.")
        sys.exit(1)
    else:
        print("Completed successfully.")
        sys.exit(0)