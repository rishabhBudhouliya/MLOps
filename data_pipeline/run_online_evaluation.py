import os
import sys
import subprocess
import yaml
import argparse
from pathlib import Path
import shutil
import tempfile

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
           'handoff_storage_path' not in config['online_evaluation']:
            raise ValueError("Missing 'online_evaluation.handoff_storage_path' in config.")
        if 'data_paths' not in config or 'rclone_remote_name' not in config: # Still needed if sub-scripts use them
             print("Warning: 'data_paths' or 'rclone_remote_name' might be expected by sub-scripts like fetcher/transformer if they use generic config loading.", file=sys.stderr)
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

        handoff_path_str = config['online_evaluation']['handoff_storage_path']
        handoff_dir = Path(handoff_path_str)
        if not handoff_dir.exists() or not handoff_dir.is_dir():
            print(f"Error: Handoff storage path '{handoff_path_str}' does not exist or is not a directory.", file=sys.stderr)
            print("Please ensure it's a mounted block storage path or a valid directory accessible by the inference system.")
            sys.exit(1)


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

        # --- Step 3: Stage Transformed Data for Inference ---
        print("\n" + "="*10 + " STEP 3: Stage Transformed Data for PR " + args.pr_identifier + "="*10)
        
        staged_files_count = 0
        # Now we expect a specific file: {pr_identifier_slug}_hunks.jsonl
        if output_hunks_jsonl.exists() and output_hunks_jsonl.is_file():
            target_handoff_file_name = f"pr_{pr_identifier_slug}_hunks_for_inference.jsonl" # Make distinct for handoff
            target_handoff_file_path = handoff_dir / target_handoff_file_name
            try:
                shutil.copy(output_hunks_jsonl, target_handoff_file_path)
                print(f"Successfully staged '{output_hunks_jsonl.name}' to '{target_handoff_file_path}'")
                staged_files_count += 1
            except Exception as e:
                print(f"Error staging file '{output_hunks_jsonl.name}' to '{target_handoff_file_path}': {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"Error: Expected output file {output_hunks_jsonl} not found after hunk extraction.", file=sys.stderr)
            sys.exit(1) # Failure if the expected output isn't there

        if staged_files_count == 0:
            print(f"Error: No transformed files were staged for PR {args.pr_identifier}. This indicates an issue.", file=sys.stderr)
            sys.exit(1)

        print(f"Successfully staged {staged_files_count} transformed hunk file(s) to {handoff_dir}.")

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