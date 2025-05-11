import os
import sys
import subprocess
import yaml
import argparse
from pathlib import Path
import shutil
import tempfile
import time # Added for rclone retry delay

# --- Helper Functions (duplicated from run_pipeline.py for now) ---

def load_config(config_path):
    """Loads the YAML configuration file."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        if not config:
            raise ValueError("Config file is empty.")
        # Validation for online evaluation mode
        if 'online_evaluation' not in config or \
           's3_target_path' not in config['online_evaluation']:
            raise ValueError("Missing 'online_evaluation.s3_target_path' in config.")
        if 'rclone_remote_name' not in config or not config['rclone_remote_name']:
            raise ValueError("Missing or empty 'rclone_remote_name' in config for S3 uploads.")
        # data_paths might still be referenced by sub-scripts if they load config generically
        if 'data_paths' not in config:
             print("Warning: 'data_paths' might be expected by sub-scripts like fetcher if it uses generic config loading.", file=sys.stderr)
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

def run_rclone_command(args_list, suppress_output=False, max_retries=3, retry_delay=5):
    """Runs an rclone command with retry logic. Args_list is the list of arguments for rclone."""
    command = ['rclone'] + args_list
    print(f"Running rclone command: {' '.join(command)}")

    for attempt in range(max_retries):
        try:
            # Using subprocess.run for potentially simpler capture and error checking
            process = subprocess.run(command, capture_output=True, text=True, check=False) 
            if process.returncode != 0:
                 if attempt < max_retries - 1:
                     print(f"Rclone attempt {attempt + 1}/{max_retries} failed. Retrying in {retry_delay} seconds...", file=sys.stderr)
                     print(f"Rclone stderr: {process.stderr}", file=sys.stderr)
                     time.sleep(retry_delay)
                     continue
                 print(f"Error running rclone command: {' '.join(command)}", file=sys.stderr)
                 print(f"Return Code: {process.returncode}", file=sys.stderr)
                 print(f"Rclone stdout: {process.stdout}", file=sys.stderr) # stdout might also have info
                 print(f"Rclone stderr: {process.stderr}", file=sys.stderr)
                 return False, process.stderr
            else:
                if not suppress_output and process.stdout:
                    print(f"Rclone stdout: {process.stdout}")
                if process.stderr and not suppress_output: # Rclone often uses stderr for progress
                    print(f"Rclone stderr: {process.stderr}")
                return True, process.stderr # Return stderr even on success for potential info
        except FileNotFoundError:
             print("Error: 'rclone' command not found. Ensure it is installed and in PATH.", file=sys.stderr)
             return False, "rclone not found"
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Rclone attempt {attempt + 1}/{max_retries} failed with exception: {e}. Retrying in {retry_delay} seconds...", file=sys.stderr)
                time.sleep(retry_delay)
                continue
            print(f"An unexpected error occurred while running rclone: {e}", file=sys.stderr)
            return False, str(e)
    return False, "Max retries exceeded for rclone command"

def run_script(script_name, args_list, working_dir=None):
    """Runs a python script using subprocess and checks the return code."""
    command = [sys.executable, script_name] + args_list
    print("-"*20 + f" Running {script_name} " + "-"*20)
    print(f"Command: {' '.join(command)}")
    try:
        # Run the script and let its stdout/stderr stream directly to console
        subprocess.run(command, check=True, text=True, cwd=working_dir)
        print(f"{script_name} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {script_name} failed with exit code {e.returncode}", file=sys.stderr)
        # Potentially print e.stdout and e.stderr if captured
        return False
    except Exception as e:
        print(f"Failed to execute {script_name}: {e}", file=sys.stderr)
        return False

# --- Main Online Evaluation Orchestration ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate online evaluation for a single GitHub PR.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML configuration file.")
    parser.add_argument("--pr-identifier", required=True, help="Identifier for the PR to process (e.g., 'owner/repo/pr_number' or a direct URL that github_pr_fetcher.py can handle).")
    parser.add_argument("--intermediate-dir", default="./pipeline_intermediate_online", help="Base directory for temporary intermediate files for online processing.")
    parser.add_argument("--debug", action="store_true", help="Enable debug flags for sub-scripts and keep intermediate files.")

    args = parser.parse_args()

    # --- Setup ---
    config = load_config(args.config)
    
    # Create a unique temporary directory for this specific PR run
    # Sanitize pr_identifier to make it a valid directory name component
    pr_identifier_slug = "".join(c if c.isalnum() or c in ('_','-') else '_' for c in args.pr_identifier)
    run_specific_intermediate_dir = Path(args.intermediate_dir) / pr_identifier_slug
    
    try:
        run_specific_intermediate_dir.mkdir(parents=True, exist_ok=True)
        print(f"Using run-specific intermediate directory: {run_specific_intermediate_dir}")

        raw_data_dir = run_specific_intermediate_dir / "raw_data"
        raw_data_dir.mkdir(parents=True, exist_ok=True)
        
        transformed_data_dir = run_specific_intermediate_dir / "transformed_data"
        transformed_data_dir.mkdir(parents=True, exist_ok=True)

        # Handoff path config now points to S3, not a local dir for staging
        s3_target_base_path_str = config['online_evaluation']['s3_target_path']
        rclone_remote_name = config['rclone_remote_name']
        if not s3_target_base_path_str.endswith('/'):
            s3_target_base_path_str += '/'


        # --- Step 1: Fetch Raw Data for the Single PR ---
        print("\n" + "="*10 + " STEP 1: Fetch Raw Data for PR " + args.pr_identifier + "="*10)
        fetcher_args = [
            "--config", args.config,
            # Assuming github_pr_fetcher.py will be modified to accept --pr-identifier
            # and use it directly, bypassing --input-pr-list for single PR mode.
            "--pr-identifier", args.pr_identifier, # This argument needs to be added to github_pr_fetcher.py
            "--local-output-dir", str(raw_data_dir),
            "--skip-remote-upload" # Always skip S3 for online mode's raw data
        ]
        if args.debug:
            fetcher_args.append("--debug")

        fetch_success = run_script("github_pr_fetcher.py", fetcher_args)
        if not fetch_success:
            print(f"Failed to fetch data for PR {args.pr_identifier}. Exiting.")
            sys.exit(1)
        
        # Locate the fetched .diff file. 
        # This assumes github_pr_fetcher.py (when modified) will save the diff file with a predictable name
        # based on pr_identifier or a known pattern within raw_data_dir for a single PR.
        # For now, let's assume it could be named pr_identifier_slug.diff or the first .diff file found.
        # This part might need refinement once github_pr_fetcher.py single PR mode is finalized.
        input_diff_file = None
        diff_files_found = list(raw_data_dir.glob("*.diff"))
        if not diff_files_found:
            print(f"Error: No .diff file found in {raw_data_dir} after fetch step for PR {args.pr_identifier}. Exiting.", file=sys.stderr)
            sys.exit(1)
        if len(diff_files_found) > 1:
            print(f"Warning: Multiple .diff files found in {raw_data_dir}. Using the first one: {diff_files_found[0]}", file=sys.stderr)
        input_diff_file = diff_files_found[0]
        print(f"Using diff file for transformation: {input_diff_file}")

        # --- Step 2: Extract Diff Hunks (Replaces Transform and Align Data) ---
        print("\n" + "="*10 + " STEP 2: Extract Diff Hunks for PR " + args.pr_identifier + "="*10)
        output_hunks_jsonl = transformed_data_dir / f"{pr_identifier_slug}_hunks.jsonl"
        
        hunk_extractor_args = [
            "--input-pr-diff-file", str(input_diff_file),
            "--output-jsonl-file", str(output_hunks_jsonl),
            "--pr-identifier", args.pr_identifier,
            "--config", args.config, # For consistency, though extract_diff_hunks.py doesn't use it
        ]
        # extract_diff_hunks.py doesn't have a --debug flag in its args, but run_script can pass it if it did.

        transform_success = run_script("extract_diff_hunks.py", hunk_extractor_args)
        if not transform_success:
            print(f"Failed to extract hunks for PR {args.pr_identifier}. Exiting.")
            sys.exit(1)

        # --- Step 3: Upload Transformed Data to S3 ---
        print("\n" + "="*10 + " STEP 3: Upload Transformed Data to S3 for PR " + args.pr_identifier + "="*10)
        
        if not output_hunks_jsonl.exists() or not output_hunks_jsonl.is_file():
            print(f"Error: Expected output file {output_hunks_jsonl} not found after hunk extraction. Cannot upload.", file=sys.stderr)
            sys.exit(1)

        # Construct the full remote S3 path for the specific file
        # Example: remote:bucket/online_eval_hunks/pr_owner_repo_123_hunks.jsonl
        remote_s3_file_path = f"{rclone_remote_name}:{s3_target_base_path_str}{output_hunks_jsonl.name}"
        
        print(f"Attempting to upload '{output_hunks_jsonl}' to '{remote_s3_file_path}'...")
        upload_success, rclone_output = run_rclone_command(
            ['copyto', str(output_hunks_jsonl), remote_s3_file_path],
            suppress_output=not args.debug # Show rclone output if in debug mode
        )

        if upload_success:
            print(f"Successfully uploaded transformed hunks to {remote_s3_file_path}")
        else:
            print(f"Failed to upload transformed hunks to S3 for PR {args.pr_identifier}. Rclone output: {rclone_output}", file=sys.stderr)
            sys.exit(1)

    finally:
        # --- Cleanup ---
        if not args.debug and run_specific_intermediate_dir.exists():
            print("\n" + "="*10 + " STEP 4: Cleaning Up Intermediate Files " + "="*10)
            try:
                shutil.rmtree(run_specific_intermediate_dir)
                print(f"Removed intermediate directory: {run_specific_intermediate_dir}")
            except Exception as e:
                print(f"Warning: Failed during cleanup of {run_specific_intermediate_dir}: {e}", file=sys.stderr)
        elif args.debug:
            print(f"Debug mode: Intermediate files kept at {run_specific_intermediate_dir}")

    print("\nOnline evaluation pipeline for PR", args.pr_identifier, "completed.")
    sys.exit(0) 