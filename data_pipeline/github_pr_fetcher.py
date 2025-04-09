import os
import re
import argparse
import requests
from github import Github
from github import Auth
from unidiff import PatchSet
from io import StringIO

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
        raise ValueError("Invalid GitHub PR URL format.")
    owner, repo, pr_number = match.groups()
    return owner, repo, int(pr_number)

def fetch_pr_data(pr_url):
    """Fetches the unified diff and review comments for a given GitHub PR URL."""
    diff_content = None
    comments = None
    try:
        token = get_github_token()
        owner, repo_name, pr_number = parse_github_pr_url(pr_url)

        # Authenticate with GitHub
        auth = Auth.Token(token)
        g = Github(auth=auth)

        # Get the repository
        repo = g.get_user(owner).get_repo(repo_name)

        # Get the pull request
        pr = repo.get_pull(pr_number)

        # --- Fetch Unified Diff --- 
        print(f"Fetching diff from: {pr.diff_url}")
        headers = {
            'Authorization': f'token {token}',
            'Accept': 'application/vnd.github.v3.diff'
        }
        response = requests.get(pr.diff_url, headers=headers)
        response.raise_for_status() 
        diff_content = response.text

        # --- Fetch Review Comments ---
        print("Fetching review comments...")
        review_comments_paginated = pr.get_review_comments()
        comments = list(review_comments_paginated)
        print(f"Found {len(comments)} review comments.")

        return diff_content, comments

    except requests.exceptions.RequestException as req_e:
        print(f"An error occurred during diff request: {req_e}")
        return None, comments 
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return None, None

def find_diff_line_for_comment(parsed_diff, comment):
    """Finds the specific line in the parsed diff corresponding to a comment."""
    if not comment.path or comment.position is None: # Check position explicitly for None
        return None # Comment is not associated with a specific line/position

    target_file = None
    for file_diff in parsed_diff:
        # Compare source_file and target_file as filename might change (rename)
        if file_diff.source_file == comment.path or file_diff.target_file == comment.path:
            target_file = file_diff
            break

    if not target_file:
        # print(f"Warning: Could not find file '{comment.path}' in diff for comment {comment.id}")
        return f"[File '{comment.path}' not found in diff]"

    current_pos = 0
    for hunk in target_file:
        for line in hunk:
            # Only count context lines and added lines for position matching
            # as per GitHub's diff comment positioning
            if line.is_context or line.is_added:
                current_pos += 1
                if current_pos == comment.position:
                    # Strip trailing newline for cleaner printing
                    return line.value.rstrip('\n')

    # print(f"Warning: Could not find position {comment.position} in file '{comment.path}' for comment {comment.id}")
    return f"[Position {comment.position} not found in hunk for file '{comment.path}']"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch GitHub PR diff and review comments.")
    parser.add_argument("pr_url", help="The URL of the GitHub Pull Request")
    args = parser.parse_args()

    print(f"Fetching data for PR: {args.pr_url}")
    diff, comments = fetch_pr_data(args.pr_url)

    parsed_diff = None
    if diff is not None:
        print("\n" + "="*20 + " UNIFIED DIFF " + "="*20)
        print(diff)
        try:
            # Use StringIO to treat the diff string like a file for PatchSet
            diff_file_like = StringIO(diff)
            parsed_diff = PatchSet(diff_file_like)
            print("\nDiff parsed successfully.")
        except Exception as parse_e:
            print(f"\nError parsing diff: {parse_e}")
            # Proceed without linking comments if parsing fails
            parsed_diff = None 

    if comments is not None:
        print("\n" + "="*20 + " REVIEW COMMENTS " + "="*20)
        if comments:
            for comment in comments:
                print(f"--- Comment ID: {comment.id} ---")
                print(f"User: {comment.user.login}")
                
                linked_line_content = "[Diff not available or not parsed]"
                if parsed_diff:
                     linked_line_content = find_diff_line_for_comment(parsed_diff, comment)
                
                if comment.path and comment.position is not None:
                    print(f"File Path: {comment.path}")
                    # Using original_position might be more robust if available and different
                    # print(f"Position: {comment.position} (Original Pos: {comment.original_position})") 
                    print(f"Position: {comment.position}")
                    if linked_line_content:
                         print(f"Relevant Diff Line ({'context/added' if linked_line_content and not linked_line_content.startswith('[') else ''}): {linked_line_content}")
                    else:
                        print("Relevant Diff Line: [Comment not linked to a specific line]")
                else:
                    # General PR comment, not tied to a specific file/line diff
                    print("File Path: N/A (General PR Comment)")
                    print("Position: N/A")

                print(f"Body:\n{comment.body}")
                print("-" * 10)
        else:
            print("No review comments found.")

    print("\nDone.") 