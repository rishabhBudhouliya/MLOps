import os
import sys
import subprocess
import yaml
import argparse
import tempfile
from github import Github, Auth, GithubException

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
        if 'github_repositories' not in config or not config['github_repositories']:
            raise ValueError("Missing or empty 'github_repositories' in config.")
        if 'data_paths' not in config or 'metadata' not in config['data_paths']:
             raise ValueError("Missing 'data_paths.metadata' in config.")
        if 'rclone_remote_name' not in config or not config['rclone_remote_name']:
            raise ValueError("Missing or empty 'rclone_remote_name' in config.")
        if 'filters' not in config:
             print("Warning: 'filters' section not found in config. Using defaults.")
             config['filters'] = {} # Ensure filters key exists
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


def run_rclone_command(args, suppress_output=False):
    """Runs an rclone command."""
    command = ['rclone'] + args
    print(f"Running command: {' '.join(command)}")
    try:
        # Use Popen for better control over output and errors
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        stdout, stderr = process.communicate()

        if process.returncode != 0:
            # Ignore "doesn't exist" errors specifically for copyto when downloading
            # Rclone exit code 3 means "Directory not found" which can happen on copyto if source doesn't exist
            if not (args[0] == 'copyto' and process.returncode == 3 and "doesn't exist" in stderr):
                 print(f"Error running rclone command: {' '.join(command)}", file=sys.stderr)
                 print(f"Return Code: {process.returncode}", file=sys.stderr)
                 print(f"Stderr: {stderr}", file=sys.stderr)
                 # Don't exit here, allow calling function to decide (e.g., log file might not exist initially)
            else:
                 print(f"Note: Remote log file not found (this might be expected on first run).")
            return False, stderr # Indicate failure but return stderr
        else:
            if not suppress_output and stdout:
                 print(f"Rclone stdout: {stdout}")
            if stderr: # Still print stderr even on success as rclone might output info there
                 print(f"Rclone stderr: {stderr}")
            return True, stderr # Indicate success

    except FileNotFoundError:
        print("Error: 'rclone' command not found. Make sure rclone is installed and in your PATH.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred while running rclone: {e}", file=sys.stderr)
        return False, str(e) # Indicate failure

def load_processed_prs(log_path):
    """Loads the set of processed PR URLs from the local log file."""
    processed_urls = set()
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f:
                for line in f:
                    url = line.strip()
                    if url: # Avoid adding empty lines
                        processed_urls.add(url)
            print(f"Loaded {len(processed_urls)} processed PR URLs from {log_path}")
        except Exception as e:
            print(f"Warning: Could not read processed PRs log {log_path}: {e}. Assuming no PRs processed yet.", file=sys.stderr)
            # If we can't read it, better to proceed assuming none are processed than fail.
            # The log might be corrupted. Subsequent runs might overwrite it.
    else:
        print(f"Processed PRs log file not found at {log_path}. Assuming no PRs processed yet.")
    return processed_urls

def fetch_github_prs(g, repo_full_name, filters):
    """Fetches PRs for a given repository based on filters."""
    print(f"Fetching PRs for {repo_full_name}...")
    all_prs = []
    try:
        repo = g.get_repo(repo_full_name)

        # Prepare filter arguments for get_pulls
        pulls_kwargs = {
            'state': filters.get('state', 'merged'), # Default to merged
            'sort': 'updated',
            'direction': 'desc'
        }
        # Note: PyGithub's get_pulls doesn't directly support a 'since' date filter like the REST API search.
        # We fetch recent PRs and filter by date locally if needed, or use the Search API for complex date queries.
        # For simplicity here, we'll rely on 'updated' sort and potentially filter later if 'since_date' is present.
        # For comment filtering, we fetch PRs first and then check comment counts.

        pulls = repo.get_pulls(**pulls_kwargs)
        count = 0
        limit = 1000 # Safety limit to avoid fetching too many if filters are broad

        min_comments = filters.get('min_comments', 0)
        since_date_str = filters.get('since_date') # Keep as string for now

        # TODO: Implement proper pagination if expecting > limit PRs
        for pr in pulls:
            if count >= limit:
                print(f"Reached fetch limit ({limit}) for {repo_full_name}. Consider more specific filters.")
                break

            # 1. Filter by Date (if 'since_date' is provided)
            #    Requires converting since_date_str to datetime and comparing with pr.created_at or pr.updated_at
            #    This basic implementation doesn't include date filtering yet. Add if needed.
            #    Example:
            #    if since_date_str:
            #        try:
            #            since_date = datetime.fromisoformat(since_date_str.replace('Z', '+00:00'))
            #            if pr.updated_at < since_date:
            #                 continue # Skip PRs updated before the 'since' date
            #        except ValueError:
            #             print(f"Warning: Invalid 'since_date' format: {since_date_str}. Skipping date filter.", file=sys.stderr)


            # 2. Filter by Minimum Comments
            if min_comments > 0:
                # This requires an extra API call per PR to get review comments count accurately.
                # pr.comments is issue comments, pr.review_comments is code review comments.
                # Fetching all comments just for the count can be slow and hit rate limits.
                # A potentially better approach for large repos is to use the GitHub Search API
                # with qualifiers like `comments:>=N`, but that's more complex.
                # Let's use the direct count for now, acknowledging the performance implication.
                try:
                    # Count both issue comments and review comments for a broader filter
                    # total_comments = pr.comments + pr.review_comments # pr.review_comments seems to be a count attr
                    # Let's try fetching actual review comments to be sure
                    review_comments_count = pr.get_review_comments().totalCount # Efficient way if available
                    if review_comments_count < min_comments:
                         # print(f"Skipping PR {pr.number} (comments: {review_comments_count} < {min_comments})")
                         continue # Skip PR if it doesn't meet min comment criteria
                except GithubException as ge:
                     print(f"Warning: Could not fetch comment count for PR {pr.number} in {repo_full_name} due to API error: {ge}. Skipping comment check for this PR.", file=sys.stderr)
                except Exception as e:
                     print(f"Warning: Error checking comments for PR {pr.number} in {repo_full_name}: {e}. Skipping comment check.", file=sys.stderr)


            all_prs.append(pr.html_url) # Store the URL
            count += 1
            if count % 100 == 0:
                 print(f"Fetched {count} PRs for {repo_full_name}...")


        print(f"Finished fetching for {repo_full_name}. Found {len(all_prs)} PRs matching state '{pulls_kwargs['state']}' (before other filters).")
        return all_prs

    except GithubException as e:
        print(f"Error fetching PRs for {repo_full_name}: {e}", file=sys.stderr)
        if e.status == 404:
            print(f"Repository '{repo_full_name}' not found or access denied.", file=sys.stderr)
        elif e.status == 401:
            print("Authentication error. Check your GITHUB_TOKEN.", file=sys.stderr)
        # Consider specific handling for rate limits if needed
        return [] # Return empty list on error for this repo
    except Exception as e:
        print(f"An unexpected error occurred fetching PRs for {repo_full_name}: {e}", file=sys.stderr)
        return []


def main():
    parser = argparse.ArgumentParser(description="Discover new GitHub PRs based on config and a processed log file.")
    parser.add_argument("config_file", help="Path to the YAML configuration file (e.g., config.yaml)")
    args = parser.parse_args()

    # --- Load Config ---
    config = load_config(args.config_file)
    rclone_remote = config['rclone_remote_name']
    # Construct remote path carefully, avoiding leading/trailing slashes issues
    metadata_path = config['data_paths']['metadata'].strip('/')
    remote_log_path = f"{rclone_remote}:{metadata_path}/processed_prs.log"

    # --- Download Log File ---
    # Use a temporary file for the local log to avoid conflicts
    with tempfile.NamedTemporaryFile(mode='w', delete=False, prefix="processed_prs_", suffix=".log") as temp_log_file:
        local_log_path = temp_log_file.name
    print(f"Attempting to download processed PR log to temporary file: {local_log_path}")
    # Download using rclone copyto
    # Suppress output unless there's an error, rclone can be verbose
    success, rclone_stderr = run_rclone_command(['copyto', remote_log_path, local_log_path], suppress_output=True)
    # No need to check 'success' explicitly here, load_processed_prs handles file not existing

    # --- Load Processed PRs ---
    processed_pr_urls = load_processed_prs(local_log_path)

    # --- GitHub API Setup ---
    try:
        token = get_github_token()
        auth = Auth.Token(token)
        g = Github(auth=auth, retry=3, timeout=20) # Add retry/timeout
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        # Clean up temp file before exiting
        if os.path.exists(local_log_path):
            os.remove(local_log_path)
        sys.exit(1)


    # --- Fetch PRs from Configured Repos ---
    all_new_pr_urls = []
    all_fetched_pr_urls = set() # Track all fetched URLs to avoid duplicates if repo listed twice

    for repo_name in config.get('github_repositories', []):
        fetched_urls = fetch_github_prs(g, repo_name, config.get('filters', {}))
        for url in fetched_urls:
            if url not in processed_pr_urls and url not in all_fetched_pr_urls:
                 all_new_pr_urls.append(url)
                 all_fetched_pr_urls.add(url) # Add to set to track across repos

    # --- Output New PR URLs ---
    print("\n" + "="*20 + " NEW PRs TO PROCESS " + "="*20)
    if all_new_pr_urls:
        print(f"Found {len(all_new_pr_urls)} new PRs:")
        for url in all_new_pr_urls:
            print(url)
    else:
        print("No new PRs found matching the criteria.")

    # --- Cleanup ---
    print(f"Cleaning up temporary log file: {local_log_path}")
    try:
        if os.path.exists(local_log_path):
            os.remove(local_log_path)
    except Exception as e:
        print(f"Warning: Could not delete temporary file {local_log_path}: {e}", file=sys.stderr)

    print("\nDiscovery script finished.")

if __name__ == "__main__":
    main() 