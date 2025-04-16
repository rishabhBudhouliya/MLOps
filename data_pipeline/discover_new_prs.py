import os
import sys
import subprocess
import yaml
import argparse
import tempfile
import platform
import socket
import time
from pathlib import Path
from github import Github, Auth, GithubException
from datetime import datetime

def is_network_available():
    """Check if network connectivity is available."""
    try:
        # Try to connect to GitHub's API
        socket.create_connection(("api.github.com", 443), timeout=5)
        return True
    except (socket.timeout, socket.gaierror, ConnectionRefusedError):
        return False

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

def check_rclone_installation():
    """Check if rclone is installed and accessible."""
    try:
        subprocess.run(['rclone', 'version'], capture_output=True, check=True)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False

def run_rclone_command(args, suppress_output=False, max_retries=3, retry_delay=5):
    """Runs an rclone command with retry logic for network issues."""
    command = ['rclone'] + args
    print(f"Running command: {' '.join(command)}")
    
    for attempt in range(max_retries):
        try:
            # Use Popen for better control over output and errors
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                # Ignore "doesn't exist" errors specifically for copyto when downloading
                # Rclone exit code 3 means "Directory not found" which can happen on copyto if source doesn't exist
                if not (args[0] == 'copyto' and process.returncode == 3 and "doesn't exist" in stderr):
                    if attempt < max_retries - 1:
                        print(f"Attempt {attempt + 1} failed. Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        continue
                    print(f"Error running rclone command: {' '.join(command)}", file=sys.stderr)
                    print(f"Return Code: {process.returncode}", file=sys.stderr)
                    print(f"Stderr: {stderr}", file=sys.stderr)
                else:
                    print(f"Note: Remote log file not found (this might be expected on first run).")
                return False, stderr
            else:
                if not suppress_output and stdout:
                    print(f"Rclone stdout: {stdout}")
                if stderr:
                    print(f"Rclone stderr: {stderr}")
                return True, stderr

        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Attempt {attempt + 1} failed with error: {e}. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
            print(f"An unexpected error occurred while running rclone: {e}", file=sys.stderr)
            return False, str(e)

    return False, "Max retries exceeded"

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
    else:
        print(f"Processed PRs log file not found at {log_path}. Assuming no PRs processed yet.")
    return processed_urls

def fetch_github_prs(g, repo_full_name, filters):
    """Fetches PRs for a given repository using the Search API based on filters."""
    print(f"Fetching PRs for {repo_full_name} using Search API...")
    all_pr_urls = []
    try:
        # Print rate limit information (Search API has a separate limit)
        rate_limit = g.get_rate_limit()
        print(f"GitHub Core API Rate Limit: {rate_limit.core.remaining}/{rate_limit.core.limit} (resets at {rate_limit.core.reset})")
        print(f"GitHub Search API Rate Limit: {rate_limit.search.remaining}/{rate_limit.search.limit} (resets at {rate_limit.search.reset})")

        # Construct search query
        query_parts = [
            f"repo:{repo_full_name}",
            "is:pr",
        ]
        
        state = filters.get('state', 'merged')
        if state != 'all':
            query_parts.append(f"is:{state}")
        
        min_comments = filters.get('min_comments', 0)
        if min_comments > 0:
            # Note: GitHub search 'comments:' includes both issue comments and review comments
            query_parts.append(f"comments:>{min_comments}")
            
        search_query = " ".join(query_parts)
        print(f"Constructed Search Query: {search_query}")

        # Use search_issues API
        # Sorting directly in search for PRs is limited, often defaults to 'best match'.
        # We can sort later if needed, but fetching efficiency is key here.
        search_results = g.search_issues(search_query)
        
        # Handle pagination for search results
        page = 0 # Search API pagination is often 0-indexed in practice or handled internally by PyGithub
        count = 0
        limit = 1000  # Safety limit
        processed_count = 0
        
        print(f"Total potential PRs found by search: {search_results.totalCount}")

        while True:
            try:
                current_page_results = list(search_results.get_page(page))
                if not current_page_results:
                    print(f"No more results found on page {page}.")
                    break
                    
                print(f"Processing page {page} with {len(current_page_results)} potential PRs...")
                processed_count += len(current_page_results)
                
                for issue in current_page_results:
                    # search_issues returns Issue objects, we need the html_url which points to the PR
                    if count >= limit:
                        print(f"Reached fetch limit ({limit}).")
                        break
                        
                    # We already filtered by comments in the search query, so no need to check again
                    all_pr_urls.append(issue.html_url)
                    count += 1
                    if count % 50 == 0:
                        print(f"Collected {count} PR URLs...")
                
                if count >= limit:
                    break
                    
                page += 1
                # Add a small delay to be kind to the Search API rate limit (30 reqs/min)
                time.sleep(2.1) # Slightly over 2 seconds to stay under 30/min
                
            except GithubException as e:
                if e.status == 403 and ("rate limit exceeded" in str(e) or "secondary rate limit" in str(e)):
                    print(f"Search API rate limit likely exceeded. Waiting...")
                    # Search API reset times can be less predictable, wait a standard time
                    wait_time = 60 # Wait a minute
                    print(f"Waiting {wait_time} seconds...")
                    time.sleep(wait_time)
                    continue # Retry the same page
                else:
                    print(f"GitHub Search API error on page {page}: {e}")
                    break # Stop processing on other errors
            except Exception as e:
                print(f"Error processing search results page {page}: {e}")
                break

        print(f"Finished fetching from Search API for {repo_full_name}. Found {len(all_pr_urls)} PR URLs matching criteria.")
        return all_pr_urls

    except GithubException as e:
        print(f"Error during initial search setup for {repo_full_name}: {e}", file=sys.stderr)
        # Handle specific common errors
        if e.status == 422: # Unprocessable Entity - often means invalid search query
             print(f"Invalid search query likely: {search_query}", file=sys.stderr)
        elif e.status == 401:
             print("Authentication error. Check your GITHUB_TOKEN.", file=sys.stderr)
        elif e.status == 403 and "rate limit exceeded" in str(e):
             print("GitHub Search API rate limit exceeded during setup.", file=sys.stderr)
        return [] # Return empty list on error
    except Exception as e:
        print(f"An unexpected error occurred fetching PRs via Search API for {repo_full_name}: {e}", file=sys.stderr)
        return []

def save_urls_to_file(urls, filename):
    """Saves a list of URLs to a text file, one URL per line."""
    try:
        with open(filename, 'w') as f:
            for url in urls:
                f.write(f"{url}\n")
        print(f"Saved {len(urls)} URLs to {filename}")
    except Exception as e:
        print(f"Error saving URLs to {filename}: {e}", file=sys.stderr)

def save_processed_urls(urls, log_path):
    """Saves the set of processed PR URLs to the local log file."""
    try:
        with open(log_path, 'w') as f:
            # Sort URLs for consistency, although set order isn't guaranteed anyway
            sorted_urls = sorted(list(urls))
            for url in sorted_urls:
                f.write(f"{url}\n")
        print(f"Saved {len(urls)} total processed PR URLs to {log_path}")
        return True
    except Exception as e:
        print(f"Error writing updated processed PRs log {log_path}: {e}.", file=sys.stderr)
        return False

def main():
    parser = argparse.ArgumentParser(description="Discover new GitHub PRs based on config and a processed log file.")
    parser.add_argument("config_file", help="Path to the YAML configuration file (e.g., config.yaml)")
    parser.add_argument("--local", action="store_true", help="Run in local mode (skip rclone operations)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode with more verbose output")
    parser.add_argument("--output-file", default="new_prs_to_process.txt", help="File to save the list of new PR URLs")
    # Add arguments for orchestration
    parser.add_argument("--no-upload", action="store_true", help="Do not upload the processed log file back to remote storage.")
    parser.add_argument("--log-output-path", default=None, help="Path to save the final updated local log file (used with --no-upload).")
    args = parser.parse_args()

    if args.no_upload and not args.log_output_path:
        print("Error: --log-output-path must be specified when using --no-upload.", file=sys.stderr)
        sys.exit(1)

    # Check network connectivity
    if not is_network_available():
        print("Error: No network connectivity available. Please check your internet connection.", file=sys.stderr)
        sys.exit(1)

    # --- Load Config ---
    config = load_config(args.config_file)
    
    # Print config for debugging
    if args.debug:
        print("Loaded configuration:")
        print(f"Repositories: {config.get('github_repositories', [])}")
        print(f"Filters: {config.get('filters', {})}")
    
    # --- Setup Paths ---
    metadata_path = Path(config['data_paths']['metadata']).as_posix().strip('/')
    rclone_remote = config['rclone_remote_name']
    remote_log_path = f"{rclone_remote}:{metadata_path}/processed_prs.log"
    local_log_for_upload = None # Path to the final log file to potentially upload or keep

    # --- Check Rclone Installation ---
    if not args.local and not check_rclone_installation():
        print("Error: rclone is not installed or not accessible. Please install rclone or use --local mode.", file=sys.stderr)
        sys.exit(1)

    # --- Download Log File ---    
    # Use a temporary file for download, then potentially copy to final log output path
    temp_download_log_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', delete=False, prefix="processed_dl_", suffix=".log") as temp_log_file:
            temp_download_log_path = temp_log_file.name
        
        if not args.local:
            print(f"Attempting to download processed PR log to temporary file: {temp_download_log_path}")
            success, rclone_stderr = run_rclone_command(['copy', remote_log_path, temp_download_log_path], suppress_output=True)
            if success and "Source file not found" in rclone_stderr:
                 print("Note: Remote log file not found (this might be expected on first run).")
                 open(temp_download_log_path, 'w').close() 
            elif not success:
                 print(f"Warning: Failed to download {remote_log_path}. Proceeding as if no PRs processed.", file=sys.stderr)
                 open(temp_download_log_path, 'w').close()
        else:
            print("Running in local mode, skipping rclone download.")
            if not os.path.exists(temp_download_log_path):
                 open(temp_download_log_path, 'w').close()
                 print(f"Created empty local log file: {temp_download_log_path}")

        # --- Load Processed PRs --- Load from the temp download path
        processed_pr_urls = load_processed_prs(temp_download_log_path)
        if args.debug:
            print(f"Loaded {len(processed_pr_urls)} processed PR URLs from {temp_download_log_path}")

        # --- GitHub API Setup ---
        try:
            token = get_github_token()
            auth = Auth.Token(token)
            g = Github(auth=auth, retry=3, timeout=20)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1) # Exit before cleanup block

        # --- Fetch PRs from Configured Repos ---
        all_new_pr_urls = []
        all_fetched_pr_urls = set() # Use a set to store all unique fetched URLs this run

        for repo_name in config.get('github_repositories', []):
            # Fetch URLs using the search API
            fetched_urls_list = fetch_github_prs(g, repo_name, config.get('filters', {}))
            
            if args.debug:
                print(f"Fetched {len(fetched_urls_list)} URLs for {repo_name}")
            
            # Add fetched URLs to the set for this run
            all_fetched_pr_urls.update(fetched_urls_list)
            
            # Identify which of these are actually new
            for url in fetched_urls_list:
                if url not in processed_pr_urls:
                    all_new_pr_urls.append(url)
                    # Add to processed immediately to avoid duplicates if listed twice
                    processed_pr_urls.add(url) 
                    if args.debug:
                        print(f"Identified new PR: {url}")

        # --- Output & Save New PR URLs ---
        print("\n" + "="*20 + " NEW PRs TO PROCESS " + "="*20)
        if all_new_pr_urls:
            print(f"Found {len(all_new_pr_urls)} new PRs:")
            # Sort for consistent output order
            all_new_pr_urls.sort()
            for url in all_new_pr_urls:
                print(url)
            # Save to output file
            save_urls_to_file(all_new_pr_urls, args.output_file)
        else:
            print("No new PRs found matching the criteria.")
            # Clear the output file if no new PRs are found
            save_urls_to_file([], args.output_file) 
            if args.debug:
                print("Debug information:")
                print(f"- Total processed PRs before this run: {len(processed_pr_urls) - len(all_new_pr_urls)}") # Approximate
                print(f"- Total unique fetched PRs this run: {len(all_fetched_pr_urls)}")
                print(f"- Repositories checked: {config.get('github_repositories', [])}")
                print(f"- Filters applied: {config.get('filters', {})}")

        # --- Update Processed Log (Locally) ---
        print("\n" + "="*20 + " UPDATING PROCESSED LOG LOCALLY " + "="*20)
        final_processed_urls = processed_pr_urls.union(all_fetched_pr_urls)
        print(f"Total processed URLs: {len(final_processed_urls)}")
        
        # Determine the final local path for the updated log
        local_log_for_upload = args.log_output_path if args.no_upload else temp_download_log_path
        print(f"Saving updated log to: {local_log_for_upload}")
        
        # Save updated URLs to the final local log path
        if save_processed_urls(final_processed_urls, local_log_for_upload):
             # Upload the updated log file only if not --no-upload
             if not args.no_upload and not args.local:
                 print(f"Uploading updated processed PR log from {local_log_for_upload} to {remote_log_path}")
                 success, _ = run_rclone_command(['copyto', local_log_for_upload, remote_log_path], suppress_output=True)
                 if success:
                     print("Successfully uploaded updated log file.")
                 else:
                     print(f"Warning: Failed to upload updated log file to {remote_log_path}", file=sys.stderr)
             elif args.no_upload:
                 print("Skipping upload because --no-upload was specified.")
                 print(f"Updated log saved locally at: {local_log_for_upload}")
             else: # Local mode
                 print("Running in local mode, skipping rclone upload.")
                 # If local mode, the log file is still the temp one unless --log-output-path was also given
                 print(f"Updated processed log available at: {local_log_for_upload}") 
        else:
            print("Skipping log upload due to error saving locally.")

    finally:
        # --- Cleanup ---
        # Clean up the initial download temp file if it wasn't the final output path
        if temp_download_log_path and os.path.exists(temp_download_log_path):
             if args.no_upload and args.log_output_path and temp_download_log_path != args.log_output_path:
                  # We copied the contents to log_output_path, so delete the temp download file
                  print(f"Cleaning up temporary download log file: {temp_download_log_path}")
                  try: os.remove(temp_download_log_path)
                  except Exception as e: print(f"Warning: Could not delete temporary download file {temp_download_log_path}: {e}", file=sys.stderr)
             elif not args.no_upload:
                  # If we uploaded (or tried to), we used the temp file directly, so delete it
                  print(f"Cleaning up temporary log file: {temp_download_log_path}")
                  try: os.remove(temp_download_log_path)
                  except Exception as e: print(f"Warning: Could not delete temporary file {temp_download_log_path}: {e}", file=sys.stderr)
             # If --no-upload but no --log-output-path, local_log_for_upload is the temp path, keep it? 
             # The orchestrator should handle cleanup of the file specified by --log-output-path if needed.
             # Let's stick to deleting the *original* temp download file if it's no longer needed.

    print("\nDiscovery script finished.")

if __name__ == "__main__":
    main() 