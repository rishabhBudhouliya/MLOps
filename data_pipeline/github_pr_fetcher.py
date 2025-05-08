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

# --- Configuration and Constants ---
CHECKPOINT_FILENAME = ".fetch_checkpoint.log"

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
        # Basic validation
        if not config:
            raise ValueError("Config file is empty.")
        # Ensure data_paths itself exists before trying to access keys within it
        if 'data_paths' not in config:
            raise ValueError("Missing 'data_paths' section in config.")
        if 'raw' not in config['data_paths']:
             raise ValueError("Missing 'data_paths.raw' in config.")
        if 'rclone_remote_name' not in config or not config['rclone_remote_name']:
            raise ValueError("Missing or empty 'rclone_remote_name' in config.")
        # Corrected validation: Check for remote_raw_data_base within data_paths
        if 'remote_raw_data_base' not in config['data_paths'] or \
           not config['data_paths']['remote_raw_data_base']:
            raise ValueError("Missing or empty 'data_paths.remote_raw_data_base' in config for remote uploads.")
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
    Returns (None, None, error_message) on non-rate-limit failure.
    Raises RateLimitExceededException if that specific error occurs.
    """
    try:
        owner, repo_name, pr_number = parse_github_pr_url(pr_url)
        print(f"Fetching data for {owner}/{repo_name}/pull/{pr_number}")
        
        repo = g.get_repo(f"{owner}/{repo_name}")
        pr = repo.get_pull(pr_number)

        # --- Fetch Unified Diff --- 
        token = get_github_token() 
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3.diff'
        }
        # Add a timeout to requests.get as well
        diff_response = requests.get(pr.diff_url, headers=headers, timeout=60) # Increased timeout slightly
        
        # Check for 429 specifically from requests, as PyGithub won't catch this
        if diff_response.status_code == 429:
            print("DEBUG: Headers from diff_response (status 429):", diff_response.headers, file=sys.stderr)
            retry_after_seconds = diff_response.headers.get('Retry-After')
            if retry_after_seconds:
                try:
                    wait_time = int(retry_after_seconds)
                    print(f"Diff download for {pr_url} got 429 with Retry-After: {wait_time}s. Raising RateLimitExceededException to honor.", file=sys.stderr)
                    # Pass the original headers from the diff response, which might contain the specific Retry-After
                    raise RateLimitExceededException(status=429, data={}, headers=diff_response.headers)
                except ValueError:
                    print(f"Diff download for {pr_url} got 429 with unparsable Retry-After: {retry_after_seconds}. Signaling for incremental backoff.", file=sys.stderr)
                    return None, None, "DIFF_DOWNLOAD_RATE_LIMIT_NO_RETRY_AFTER" 
            else:
                print(f"Rate limit hit (429) fetching diff for {pr_url} (no Retry-After header). Signaling for incremental backoff.", file=sys.stderr)
                return None, None, "DIFF_DOWNLOAD_RATE_LIMIT_NO_RETRY_AFTER"

        diff_response.raise_for_status() 
        diff_text = diff_response.text
        if not diff_text:
             print(f"Warning: Diff content for {pr_url} is empty.")

        # --- Fetch Review Comments --- 
        print("Fetching review comments...")
        review_comments_paginated = pr.get_review_comments()
        comments_list = list(review_comments_paginated)
        print(f"Found {len(comments_list)} review comments.")

        return diff_text, comments_list, None

    except RateLimitExceededException: # Explicitly catch and re-raise
        print(f"Rate limit exceeded during API call within fetch_pr_data for {pr_url}. Re-raising.", file=sys.stderr)
        raise # Re-raise the original RateLimitExceededException
    except GithubException as ge:
        # All other GithubExceptions (Not Found, Server Error, etc.)
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
        error_msg = f"Unexpected error fetching data for {pr_url}: {type(e).__name__} - {e}"
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

# --- New Helper Functions for Checkpointing and Batching ---

def load_checkpoint(checkpoint_path: Path) -> dict:
    """Loads the checkpoint file (JSON) into a dictionary."""
    if checkpoint_path.exists():
        try:
            with open(checkpoint_path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Warning: Checkpoint file {checkpoint_path} is corrupted. Starting fresh.", file=sys.stderr)
            return {}
        except Exception as e:
            print(f"Warning: Could not read checkpoint file {checkpoint_path}: {e}. Starting fresh.", file=sys.stderr)
            return {}
    return {}

def save_checkpoint(checkpoint_path: Path, processed_data: dict):
    """Saves the processed data dictionary to the checkpoint file (JSON)."""
    try:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True) # Ensure directory exists
        with open(checkpoint_path, 'w') as f:
            json.dump(processed_data, f, indent=2)
        print(f"Checkpoint saved to {checkpoint_path}")
    except Exception as e:
        print(f"Error saving checkpoint to {checkpoint_path}: {e}", file=sys.stderr)

def is_pr_processed(owner: str, repo_name: str, pr_number: int, processed_prs_by_repo: dict) -> bool:
    """Checks if a PR is marked as processed in the checkpoint data."""
    repo_key = f"{owner}/{repo_name}"
    return repo_key in processed_prs_by_repo and pr_number in processed_prs_by_repo[repo_key]

def group_prs_by_repository(pr_urls: list[str]) -> dict[str, list[dict]]:
    """Groups PR URLs by repository, storing parsed details."""
    grouped = {}
    for url in pr_urls:
        try:
            owner, repo, pr_number = parse_github_pr_url(url)
            repo_key = f"{owner}/{repo}"
            if repo_key not in grouped:
                grouped[repo_key] = []
            grouped[repo_key].append({'url': url, 'owner': owner, 'repo': repo, 'pr_number': pr_number})
        except ValueError:
            print(f"Skipping invalid PR URL during grouping: {url}", file=sys.stderr)
    return grouped

def upload_repository_batch_to_s3(config: dict, local_repo_data_path: Path, owner: str, repo_name: str) -> bool:
    """
    Uploads all files in the local_repo_data_path for a specific repository to S3 using rclone.
    The S3 path will be <rclone_remote_name>:<s3_target_path>/<owner>/<repo_name>/
    """
    rclone_remote = config['rclone_remote_name']
    s3_base_path = config['data_paths']['remote_raw_data_base'].strip('/') # Ensure no leading/trailing slashes for joining
    
    # Corrected remote_path construction
    remote_path = f"{rclone_remote}:{s3_base_path}/{owner}/{repo_name}"
    
    # local_repo_data_path already points to .../owner/repo_name, so we just append /
    # to copy its contents.
    source_path_for_rclone = str(local_repo_data_path) + ("" if str(local_repo_data_path).endswith('/') else "/")


    if not local_repo_data_path.exists() or not any(local_repo_data_path.iterdir()):
        print(f"No files found in {local_repo_data_path} to upload for {owner}/{repo_name}. Skipping S3 upload.", file=sys.stdout)
        return True # Nothing to upload, so "success"

    cmd = [
        "rclone", "copy", "--retries", "3", "--retries-sleep", "10s",
        str(source_path_for_rclone), # Source: local directory for the repo
        remote_path # Destination: S3 path for the repo
    ]
    print(f"Attempting to upload batch for {owner}/{repo_name} to {remote_path} using command: {' '.join(cmd)}")
    try:
        # Add timeout to rclone command
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=300) # 5 min timeout
        if result.returncode == 0:
            print(f"Successfully uploaded batch for {owner}/{repo_name} to {remote_path}")
            # Potentially list files:
            # list_cmd = ["rclone", "ls", remote_path]
            # list_result = subprocess.run(list_cmd, capture_output=True, text=True, check=False)
            # print(f"Uploaded files:\n{list_result.stdout}")
            return True
        else:
            print(f"Error uploading batch for {owner}/{repo_name} to {remote_path}.", file=sys.stderr)
            print(f"Rclone stdout:\n{result.stdout}", file=sys.stderr)
            print(f"Rclone stderr:\n{result.stderr}", file=sys.stderr)
            return False
    except subprocess.TimeoutExpired:
        print(f"Rclone command timed out for {owner}/{repo_name}.", file=sys.stderr)
        return False
    except FileNotFoundError:
        print("Error: rclone command not found. Please ensure rclone is installed and in your PATH.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"An unexpected error occurred during rclone execution for {owner}/{repo_name}: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch GitHub PR diff and comments, save raw data, and upload to S3.")
    parser.add_argument("--config", required=True, help="Path to the YAML configuration file.")
    parser.add_argument("--input-pr-list", required=True, help="Path to a text file containing PR URLs to process, one URL per line.")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode with more verbose output")
    parser.add_argument("--local-output-dir", required=True, help="Directory to save the raw diff and comment files locally.")
    parser.add_argument("--skip-remote-upload", action="store_true", help="Skip uploading files to S3 remote.")
    args = parser.parse_args()

    # --- Load Config ---
    config = load_config(args.config)

    # --- Setup local output directory ---
    local_output_path = Path(args.local_output_dir)
    local_output_path.mkdir(parents=True, exist_ok=True)
    print(f"Local output directory: {local_output_path}")

    # --- Checkpoint Setup ---
    checkpoint_file_path = local_output_path / CHECKPOINT_FILENAME
    processed_prs_by_repo_checkpoint = load_checkpoint(checkpoint_file_path)
    # This will be updated during processing and saved after each repo batch or S3 skip.

    # --- Read PR List ---
    all_input_pr_urls = []
    try:
        with open(args.input_pr_list, 'r') as f:
            for line in f:
                url = line.strip()
                if url:
                    all_input_pr_urls.append(url)
        print(f"Read {len(all_input_pr_urls)} PR URLs from {args.input_pr_list}")
    except FileNotFoundError:
        print(f"Error: Input PR list file not found at {args.input_pr_list}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reading input PR list {args.input_pr_list}: {e}", file=sys.stderr)
        sys.exit(1)

    if not all_input_pr_urls:
        print("No PR URLs found in the input file. Exiting.")
        sys.exit(0)
        
    # --- Group PRs by Repository ---
    prs_grouped_by_repository = group_prs_by_repository(all_input_pr_urls)
    if not prs_grouped_by_repository:
        print("No valid PR URLs to process after grouping. Exiting.")
        sys.exit(0)

    # --- GitHub API Setup --- 
    try:
        token = get_github_token()
        auth = Auth.Token(token)
        g = Github(auth=auth, retry=5, timeout=60) # Increased timeout for Github client
        print("GitHub client initialized.")
        print(f"Authenticated as user: {g.get_user().login}")
    except Exception as e:
        print(f"Error initializing GitHub client: {e}", file=sys.stderr)
        sys.exit(1)

    # --- Processing Loop ---
    overall_success_count = 0
    overall_failure_count = 0
    
    # Store all successfully processed PRs (owner, repo, number) across all batches in this run
    # to later compare with all_input_pr_urls for checkpoint deletion.
    all_prs_fully_processed_in_this_run_or_before = set()
    # Populate from existing checkpoint
    for repo_key_chk, pr_nums_chk in processed_prs_by_repo_checkpoint.items():
        owner_chk, repo_name_chk = repo_key_chk.split('/', 1)
        for pr_num_chk in pr_nums_chk:
            all_prs_fully_processed_in_this_run_or_before.add((owner_chk, repo_name_chk, pr_num_chk))


    for repo_key, pr_details_list in prs_grouped_by_repository.items():
        owner, repo_name = repo_key.split('/', 1)
        print(f"\n--- Processing Repository: {owner}/{repo_name} ---")

        repo_batch_successfully_fetched_and_saved = [] # List of (owner, repo, pr_number, diff_path, comments_path)
        repo_batch_had_errors = False

        for pr_info in pr_details_list:
            pr_url = pr_info['url']
            pr_number = pr_info['pr_number']
            
            print("-"*40)
            print(f"Processing PR: {pr_url}")

            if is_pr_processed(owner, repo_name, pr_number, processed_prs_by_repo_checkpoint):
                print(f"PR {pr_url} already processed according to checkpoint. Skipping.")
                all_prs_fully_processed_in_this_run_or_before.add((owner, repo_name, pr_number)) # Ensure it's counted
                # No need to increment overall_success_count here as it's from a previous run.
                # We only count successes for PRs processed *in this current run*.
                continue

            pr_processed_successfully_this_iteration = False
            local_diff_path = None
            local_comments_path = None
            
            try:
                # file_basename for this PR
                file_basename = f"{owner}_{repo_name}_{pr_number}"
                
                # Define local paths within the base output directory, organized by owner/repo
                pr_specific_output_dir = local_output_path / owner / repo_name
                pr_specific_output_dir.mkdir(parents=True, exist_ok=True)
                local_diff_path = pr_specific_output_dir / f"{file_basename}.diff"
                local_comments_path = pr_specific_output_dir / f"{file_basename}_comments.jsonl"

                # Fetch data (with rate limit retry loop)
                # --- Inner retry loop for fetching data for a single PR ---
                max_diff_fetch_retries = 5 # Max attempts for diff download with incremental backoff
                current_diff_fetch_retry = 0
                base_diff_backoff_seconds = 30 # Initial wait for diff download retry
                
                diff_text, comments_list, error_msg = None, None, None # Ensure these are defined before the loop

                while True: # This loop handles retries for a single PR
                    try:
                        # Reset error_msg at the start of each attempt if it's from a previous failed attempt
                        # This is important if fetch_pr_data returns an error_msg that's not an exception
                        if error_msg == "DIFF_DOWNLOAD_RATE_LIMIT_NO_RETRY_AFTER":
                            error_msg = None 

                        diff_text, comments_list, error_msg = fetch_pr_data(g, pr_url)

                        if error_msg == "DIFF_DOWNLOAD_RATE_LIMIT_NO_RETRY_AFTER":
                            if current_diff_fetch_retry < max_diff_fetch_retries:
                                current_diff_fetch_retry += 1
                                # Exponential backoff with a cap, and some jitter could be added too
                                wait_time = min(base_diff_backoff_seconds * (2 ** (current_diff_fetch_retry - 1)), 300) 
                                print(f"Diff download for {pr_url} was rate limited (no Retry-After). "
                                      f"Retrying ({current_diff_fetch_retry}/{max_diff_fetch_retries}) in {wait_time}s...", file=sys.stderr)
                                time.sleep(wait_time)
                                continue # Retry fetching this PR's data (re-call fetch_pr_data)
                            else:
                                print(f"Max retries ({max_diff_fetch_retries}) reached for diff download of {pr_url} after rate limiting. "
                                      "Skipping this PR.", file=sys.stderr)
                                # This will be caught by the outer PR processing exception handler
                                raise Exception(f"Failed to download diff for {pr_url} after {max_diff_fetch_retries} retries due to secondary rate limit.")
                        
                        elif error_msg: # Any other error message from fetch_pr_data that indicates failure
                             # This will be caught by the outer PR processing exception handler
                             raise Exception(f"fetch_pr_data for {pr_url} returned an error: {error_msg}")
                        
                        # If no error_msg and no exception, it means success
                        break # Success from fetch_pr_data, exit retry loop for this PR

                    except RateLimitExceededException as rle_inner: 
                        # This is for PRIMARY GitHub API rate limits or if fetch_pr_data raised it due to Retry-After on diff
                        print(f"RateLimitExceededException caught for {pr_url}. Determining wait time...", file=sys.stderr)
                        
                        # Check if the exception's headers (potentially from diff_response) have Retry-After
                        specific_retry_after = None
                        if rle_inner.headers and 'Retry-After' in rle_inner.headers:
                            try:
                                specific_retry_after = int(rle_inner.headers['Retry-After'])
                                print(f"RateLimitExceededException for {pr_url} included Retry-After: {specific_retry_after}s.", file=sys.stderr)
                            except ValueError:
                                print(f"RateLimitExceededException for {pr_url} had unparsable Retry-After: {rle_inner.headers['Retry-After']}.", file=sys.stderr)
                        
                        if specific_retry_after is not None and specific_retry_after > 0:
                            wait_seconds = specific_retry_after + 5 # Add a small buffer
                            reset_time_for_log = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=wait_seconds)
                            print(f"Waiting {wait_seconds:.0f}s based on specific Retry-After header from exception for {pr_url} (until ~{reset_time_for_log})...")
                        else:
                            # Fallback to general GitHub API rate limit reset time
                            print(f"No specific Retry-After in RLE for {pr_url} or it was invalid. Using general GitHub API reset time.", file=sys.stderr)
                            try:
                                rate_limit_info = g.get_rate_limit().core # core, search, graphql, etc.
                                reset_time = rate_limit_info.reset
                            except Exception as e_rl:
                                print(f"Could not get primary rate limit info: {e_rl}. Waiting default 120s.", file=sys.stderr)
                                reset_time = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=120)
                            wait_seconds = max((reset_time - datetime.datetime.now(datetime.timezone.utc)).total_seconds() + 15, 30) # Add buffer, min wait
                        
                        print(f"Overall rate limit policy for {pr_url}: Waiting for {wait_seconds:.0f} seconds...")
                        time.sleep(wait_seconds)
                        # Loop will continue to retry fetching this PR's data

                    # Other GithubException or general exceptions from fetch_pr_data will be caught by the outer try-except for the PR
                    # (the one that sets repo_batch_had_errors = True)

                # --- End of inner retry loop ---
                # If we exited the loop, it means fetch_pr_data was successful (no error_msg and no unhandled exception)
                
                # Save locally (ensure diff_text is not None if we got here)
                if diff_text is None: # Should not happen if loop logic is correct and fetch_pr_data succeeded
                    print(f"Error: diff_text is None for {pr_url} after fetch attempts. Skipping save.", file=sys.stderr)
                    raise Exception(f"diff_text was None for {pr_url} unexpectedly.")

                print(f"Saving diff locally to {local_diff_path}")
                with open(local_diff_path, 'w', encoding='utf-8') as f_diff:
                    f_diff.write(diff_text)

                print(f"Saving comments locally to {local_comments_path}")
                if not save_comments_to_jsonl(comments_list, local_comments_path):
                     raise Exception(f"Failed to save comments locally for {pr_url}")

                print(f"Successfully fetched and saved PR: {pr_url}")
                pr_processed_successfully_this_iteration = True
                repo_batch_successfully_fetched_and_saved.append({
                    "owner": owner, "repo": repo_name, "pr_number": pr_number,
                    "diff_path": local_diff_path, "comments_path": local_comments_path
                })

            except ValueError as ve: # From parse_github_pr_url if it was somehow missed in grouping
                print(f"Skipping invalid URL: {pr_url} - {ve}", file=sys.stderr)
                repo_batch_had_errors = True
            except GithubException as ge:
                print(f"GitHub API error processing PR {pr_url}: {ge}. Will not be added to current batch.", file=sys.stderr)
                repo_batch_had_errors = True
            except Exception as pr_e:
                print(f"Error processing PR {pr_url}: {pr_e}. Will not be added to current batch.", file=sys.stderr)
                repo_batch_had_errors = True
            
            # Tally individual PR success/failure FOR THIS RUN
            # This is different from overall_success_count which tracks PRs added to checkpoint.
            # If pr_processed_successfully_this_iteration is false, it counts towards overall_failure_count for the run summary.
            if not pr_processed_successfully_this_iteration and not is_pr_processed(owner, repo_name, pr_number, processed_prs_by_repo_checkpoint) :
                 overall_failure_count +=1


        # --- After processing all PRs for the current repository ---
        if repo_batch_successfully_fetched_and_saved:
            batch_upload_successful_or_skipped = False
            if args.skip_remote_upload:
                print(f"Skipping remote S3 upload for repository {owner}/{repo_name} as per --skip-remote-upload flag.")
                batch_upload_successful_or_skipped = True
            else:
                print(f"Attempting to upload batch for repository {owner}/{repo_name} to S3.")
                # The path for rclone should be the parent directory containing all PR files for this repo
                repo_data_path_for_upload = local_output_path / owner / repo_name
                if upload_repository_batch_to_s3(config, repo_data_path_for_upload, owner, repo_name):
                    print(f"Successfully uploaded batch for {owner}/{repo_name}.")
                    batch_upload_successful_or_skipped = True
                else:
                    print(f"Failed to upload batch for repository {owner}/{repo_name} to S3. These PRs will not be checkpointed in this run.", file=sys.stderr)
                    repo_batch_had_errors = True # Mark that this repo batch had errors at upload stage
                     # PRs that were locally saved but failed to upload contribute to failure_count
                    overall_failure_count += len(repo_batch_successfully_fetched_and_saved)


            if batch_upload_successful_or_skipped:
                print(f"Updating checkpoint for repository {owner}/{repo_name}...")
                repo_key_for_checkpoint = f"{owner}/{repo_name}"
                if repo_key_for_checkpoint not in processed_prs_by_repo_checkpoint:
                    processed_prs_by_repo_checkpoint[repo_key_for_checkpoint] = []
                
                newly_checkpointed_count_for_repo = 0
                for pr_data in repo_batch_successfully_fetched_and_saved:
                    # Add to checkpoint only if not already there (though skip logic should prevent this)
                    if pr_data["pr_number"] not in processed_prs_by_repo_checkpoint[repo_key_for_checkpoint]:
                        processed_prs_by_repo_checkpoint[repo_key_for_checkpoint].append(pr_data["pr_number"])
                        all_prs_fully_processed_in_this_run_or_before.add((owner, repo_name, pr_data["pr_number"]))
                        overall_success_count += 1 # This PR is now fully processed and checkpointed.
                        newly_checkpointed_count_for_repo +=1
                
                if newly_checkpointed_count_for_repo > 0:
                     # Sort PR numbers for consistent checkpoint file
                    processed_prs_by_repo_checkpoint[repo_key_for_checkpoint].sort()
                    save_checkpoint(checkpoint_file_path, processed_prs_by_repo_checkpoint)
                else:
                    print(f"No new PRs to checkpoint for {owner}/{repo_name} in this batch.")
            else: # Batch upload failed
                print(f"Skipping checkpoint update for {owner}/{repo_name} due to S3 upload failure or because all PRs in batch failed before upload stage.")
        
        elif not repo_batch_successfully_fetched_and_saved and not repo_batch_had_errors:
             print(f"No new PRs processed for repository {owner}/{repo_name} in this run (all might have been skipped or input list for repo was empty).")


    # --- Final Checkpoint Cleanup ---
    all_input_prs_parsed_details = []
    for url in all_input_pr_urls:
        try:
            owner_in, repo_in, pr_num_in = parse_github_pr_url(url)
            all_input_prs_parsed_details.append((owner_in, repo_in, pr_num_in))
        except ValueError:
            pass # Already logged during grouping

    # Check if every PR in the original input list is now considered processed
    # (either from this run or a previous one via checkpoint)
    # This means that all_prs_fully_processed_in_this_run_or_before must contain every item from all_input_prs_parsed_details
    
    # Corrected logic for checkpoint deletion:
    # Only delete if there were NO failures in *this current run* AND all PRs from input list are in the checkpoint
    
    num_total_input_prs = len(all_input_prs_parsed_details)
    num_successfully_processed_ever = len(all_prs_fully_processed_in_this_run_or_before)

    can_delete_checkpoint = True
    if overall_failure_count > 0: # If any PR failed in *this specific run*
        print(f"Checkpoint file {checkpoint_file_path} will be kept due to {overall_failure_count} failures in this run.")
        can_delete_checkpoint = False
    else: # No failures in this run, now check if all input PRs are in the checkpoint
        if num_successfully_processed_ever >= num_total_input_prs:
            # Double check: every single PR from input must be in the 'all_prs_fully_processed_in_this_run_or_before' set
            all_required_prs_are_processed = True
            for req_owner, req_repo, req_pr_num in all_input_prs_parsed_details:
                if (req_owner, req_repo, req_pr_num) not in all_prs_fully_processed_in_this_run_or_before:
                    all_required_prs_are_processed = False
                    print(f"Debug: Required PR {req_owner}/{req_repo}#{req_pr_num} not found in fully processed set for checkpoint deletion.")
                    break
            
            if all_required_prs_are_processed:
                print(f"All {num_total_input_prs} PRs from input list are processed and no failures in this run. Deleting checkpoint file.")
                try:
                    checkpoint_file_path.unlink(missing_ok=True)
                except Exception as e:
                    print(f"Error deleting checkpoint file {checkpoint_file_path}: {e}", file=sys.stderr)
            else:
                print(f"Checkpoint file {checkpoint_file_path} will be kept as not all PRs from the input list are fully processed yet (processed: {num_successfully_processed_ever}/{num_total_input_prs}).")
                can_delete_checkpoint = False # Redundant given the flow but good for clarity
        else:
            print(f"Checkpoint file {checkpoint_file_path} will be kept. Not all PRs from input list are processed (processed: {num_successfully_processed_ever}/{num_total_input_prs}).")
            can_delete_checkpoint = False


    # --- Summary ---
    print("="*40)
    print("Processing Summary:")
    print(f"  Total PRs listed in input file: {len(all_input_pr_urls)}")
    print(f"  Number of unique repositories processed: {len(prs_grouped_by_repository)}")
    # overall_success_count is PRs NEWLY checkpointed in THIS RUN
    print(f"  Successfully processed & checkpointed in this run: {overall_success_count}")
    print(f"  Failed in this run (fetch, save, or upload): {overall_failure_count}")
    print(f"  Total PRs in checkpoint (including previous runs): {sum(len(prs) for prs in processed_prs_by_repo_checkpoint.values())}")
    print("="*40)

    if overall_failure_count > 0:
        print("Completed with errors.")
        sys.exit(1)
    else:
        # Check if all input PRs are in the checkpoint, even if no *new* ones were processed this run
        # This means a "successful" completion even if all were skipped due to prior processing.
        all_input_covered_by_checkpoint = True
        for req_owner, req_repo, req_pr_num in all_input_prs_parsed_details:
            if not is_pr_processed(req_owner, req_repo, req_pr_num, processed_prs_by_repo_checkpoint):
                all_input_covered_by_checkpoint = False
                break
        if all_input_covered_by_checkpoint:
             print("Completed successfully. All input PRs are accounted for in the checkpoint.")
             sys.exit(0)
        else:
             print("Completed (no new errors in this run), but not all input PRs are in the checkpoint yet.")
             sys.exit(1) # Technically not an error IN THIS RUN, but the overall task isn't complete.
