import os
import re
import argparse
import requests
from github import Github
from github import Auth
# Handle potential rate limit errors
from github import RateLimitExceededException, GithubException
from unidiff import PatchSet
from io import StringIO
import time # For potential rate limit handling

def get_github_token():
    """Retrieves the GitHub token from the environment variable."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GitHub token not found. Set the GITHUB_TOKEN environment variable.")
    return token

def parse_github_pr_url(url):
    """Parses a GitHub PR URL to extract owner, repo, and PR number."""
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if not match:
        # Try matching format without www. or with different protocols if needed
        match = re.match(r"https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
        if not match:
            raise ValueError(f"Invalid GitHub PR URL format: {url}")
    owner, repo, pr_number = match.groups()
    return owner, repo, int(pr_number)

def fetch_pr_data(pr_url, max_retries=3, retry_delay=5):
    """
    Fetches the unified diff and review comments for a given GitHub PR URL.
    Includes basic rate limit handling.
    """
    diff_content = None
    comments = [] # Initialize as empty list
    retries = 0

    while retries < max_retries:
        try:
            token = get_github_token()
            owner, repo_name, pr_number = parse_github_pr_url(pr_url)

            # Authenticate with GitHub
            auth = Auth.Token(token)
            g = Github(auth=auth, retry=5, timeout=15) # Add retry/timeout to Github object

            # Get the repository
            repo = g.get_repo(f"{owner}/{repo_name}") # More direct way

            # Get the pull request
            pr = repo.get_pull(pr_number)

            # --- Fetch Unified Diff ---
            print(f"Fetching diff from: {pr.diff_url}")
            headers = {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github.v3.diff'
            }
            response = requests.get(pr.diff_url, headers=headers)
            # --- DEBUG --- 
            print(f"DEBUG: Diff request status code: {response.status_code}")
            # --- END DEBUG ---
            response.raise_for_status()
            print(f"DEBUG: Diff content: {response.text}")
            diff_content = response.text
            # --- DEBUG --- 
            print(f"DEBUG: Diff content fetched successfully (length: {len(diff_content) if diff_content else 0}).")
            # --- END DEBUG ---

            # --- Fetch Review Comments ---
            print("Fetching review comments...")
            review_comments_paginated = pr.get_review_comments()
            comments = list(review_comments_paginated)
            print(f"Found {len(comments)} review comments.")

            return diff_content, comments

        except requests.exceptions.RequestException as req_e:
            # --- DEBUG --- 
            print(f"DEBUG: Caught requests.exceptions.RequestException: {req_e}")
            # --- END DEBUG ---
            print(f"An error occurred during diff request: {req_e}")
            return None, comments 
        except Exception as e:
            # --- DEBUG --- 
            print(f"DEBUG: Caught generic Exception: {e}")
            # --- END DEBUG ---
            print(f"An unexpected error occurred: {e}")
            return None, None


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
    parser = argparse.ArgumentParser(description="Fetch GitHub PR diff and review comments.")
    parser.add_argument("pr_url", help="The URL of the GitHub Pull Request")
    args = parser.parse_args()

    print(f"Fetching data for PR: {args.pr_url}")
    diff_text, comments_data = fetch_pr_data(args.pr_url)

    parsed_diff = None
    if diff_text is not None:
        print("\n" + "="*20 + " UNIFIED DIFF " + "="*20)
        # print(diff_text) # Keep this commented unless debugging diff text itself
        try:
            diff_file_like = StringIO(diff_text)
            parsed_diff = PatchSet(diff_file_like)
            print("\nDiff parsed successfully.")

            # --- Print filenames in the parsed diff ---
            print("\n" + "="*10 + " Files in Parsed Diff " + "="*10)
            if parsed_diff:
                 for file_diff in parsed_diff:
                     # file_diff.path usually gives the target path without the b/ prefix
                     # source_file and target_file include the a/ and b/ prefixes
                     print(f"- Path: {file_diff.path} (Source: {file_diff.source_file}, Target: {file_diff.target_file})")
            else:
                 print("Parsed diff object is empty or None.")
            print("="*32)
            # --- End print filenames ---

        except Exception as parse_e:
            print(f"\nError parsing diff: {parse_e}")
            parsed_diff = None
    else:
        print("\nDiff content not available.")

    if comments_data:
        print("\n" + "="*20 + " REVIEW COMMENTS " + "="*20)
        linked_count = 0
        outdated_count = 0
        general_count = 0

        for comment in comments_data:
            print(f"--- Comment ID: {comment.id} ---")
            print(f"User: {comment.user.login}")
            print(f"Body:\n{comment.body}")

            # --- Alignment Logic based on V1 Strategy ---
            if comment.path and comment.position is not None:
                # CASE 1: Linkable comment (has path and non-null position)
                print(f"Status: Attempting link")
                print(f"File Path: {comment.path}")
                print(f"Position (current): {comment.position}")

                target_line_obj = None
                if parsed_diff:
                    target_hunk_obj, target_line_obj = find_hunk_and_line_for_comment(parsed_diff, comment.path, comment.position)
                
                if target_line_obj and target_hunk_obj:
                    linked_line_content = target_line_obj.value.rstrip('\n')
                    hunk_text = str(target_hunk_obj)
                    line_type = ('added' if target_line_obj.is_added else
                                 'context' if target_line_obj.is_context else
                                 'removed' if target_line_obj.is_removed else 'unknown')
                    print(f"Linked Line Type: {line_type}")
                    print(f"Linked Line Content: {linked_line_content}")
                    print(f"Hunk Text: {hunk_text}")
                    linked_count += 1
                elif not parsed_diff:
                    print("Linked Line Content: [Diff not parsed or unavailable]")
                else:
                    print(f"Linked Line Content: [Failed to find line for position {comment.position} in parsed diff for {comment.path}]")

            elif comment.path and comment.position is None:
                # CASE 2: Outdated comment (has path but null position)
                print(f"Status: Outdated (position is null)")
                print(f"File Path: {comment.path}")
                print(f"Position (current): None")
                print(f"Original Position: {comment.original_position} (at commit {comment.original_commit_id})")
                print("Linked Line Content: [Skipped - Position is null, comment outdated relative to current diff]")
                outdated_count += 1
            else:
                # CASE 3: General PR comment (no path)
                print(f"Status: General PR comment")
                print(f"File Path: N/A")
                print(f"Position: N/A")
                print("Linked Line Content: [N/A - General comment]")
                general_count += 1

            print("-" * 10)

        print("\n" + "="*20 + " SUMMARY " + "="*20)
        print(f"Total Comments Processed: {len(comments_data)}")
        print(f"Successfully Linked (using position): {linked_count}")
        print(f"Skipped as Outdated (position was null): {outdated_count}")
        print(f"Skipped as General Comment (no path): {general_count}")

    elif comments_data is None:
         print("\n" + "="*20 + " REVIEW COMMENTS " + "="*20)
         print("Could not fetch comments (or operation failed).")
    else: # comments_data is an empty list
         print("\n" + "="*20 + " REVIEW COMMENTS " + "="*20)
         print("No review comments found for this PR.")


    print("\nDone.")