import os
import sys
import subprocess
import yaml
import argparse
from pathlib import Path
import shutil

# --- Helper Functions (copied/adapted from discover_new_prs.py) --- 

def load_config(config_path):
    """Loads the YAML configuration file."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        # Basic validation (add checks needed by orchestrator)
        if not config:
            raise ValueError("Config file is empty.")
        if 'data_paths' not in config or 'metadata' not in config['data_paths']:
             raise ValueError("Missing 'data_paths.metadata' in config.")
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

def run_rclone_command(args, suppress_output=False, max_retries=3, retry_delay=5):
    """Runs an rclone command with retry logic for network issues."""
    command = ['rclone'] + args
    print(f"Running command: {' '.join(command)}")
    # Simplified for orchestrator - assumes rclone is installed
    # Add retry logic if needed, but focus is on running the command
    try:
        process = subprocess.run(command, capture_output=True, text=True, check=False) # Don't check=True initially
        if process.returncode != 0:
            print(f"Error running rclone command: {' '.join(command)}", file=sys.stderr)
            print(f"Return Code: {process.returncode}", file=sys.stderr)
            print(f"Stderr:\n{process.stderr}", file=sys.stderr)
            return False, process.stderr
        else:
            if not suppress_output and process.stdout:
                 print(f"Rclone stdout:\n{process.stdout}")
            if process.stderr:
                 print(f"Rclone stderr:\n{process.stderr}", file=sys.stderr)
            return True, process.stderr
    except FileNotFoundError:
         print("Error: 'rclone' command not found.", file=sys.stderr)
         return False, "rclone not found"
    except Exception as e:
        print(f"An unexpected error occurred while running rclone: {e}", file=sys.stderr)
        return False, str(e)

def run_script(script_name, args_list):
    """Runs a python script using subprocess and checks the return code."""
    command = [sys.executable, script_name] + args_list
    print("-"*20 + f" Running {script_name} " + "-"*20)
    print(f"Command: {' '.join(command)}")
    try:
        # Use check=True to automatically raise CalledProcessError on non-zero exit
        process = subprocess.run(command, check=True, text=True, capture_output=True)
        print(f"--- {script_name} Output ---")
        if process.stdout:
            print(process.stdout)
        if process.stderr:
            print(process.stderr, file=sys.stderr) # Print script's stderr to orchestrator's stderr
        print(f"--- End {script_name} Output ---")
        print(f"{script_name} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {script_name} failed with exit code {e.returncode}", file=sys.stderr)
        print(f"--- {script_name} Output (Error) ---", file=sys.stderr)
        if e.stdout:
             print(e.stdout, file=sys.stderr)
        if e.stderr:
             print(e.stderr, file=sys.stderr)
        print(f"--- End {script_name} Output (Error) ---", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Failed to execute {script_name}: {e}", file=sys.stderr)
        return False

# --- Main Orchestration --- 

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate the GitHub PR data pipeline.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML configuration file.")
    parser.add_argument("--debug", action="store_true", help="Enable debug flags for sub-scripts.")
    parser.add_argument("--local", action="store_true", help="Run sub-scripts in local mode (skip remote operations where applicable and keep raw data locally)")
    # Define intermediate file paths
    parser.add_argument("--intermediate-dir", default="./pipeline_intermediate", help="Directory for intermediate files.")
    parser.add_argument("--local-raw-output-dir", default="./pipeline_output_raw", help="Directory to save raw data when running with --local flag.")

    args = parser.parse_args()

    # --- Setup --- 
    config_path = args.config
    config = load_config(config_path)
    intermediate_dir = Path(args.intermediate_dir)
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    print(f"Using intermediate directory: {intermediate_dir}")

    # Define intermediate filenames
    new_prs_file = intermediate_dir / "new_prs_to_process.txt"
    updated_log_file = intermediate_dir / "updated_processed_prs.log"
    # Define path for local raw output if needed
    local_raw_output_path = Path(args.local_raw_output_dir) if args.local else None
    if args.local:
         print(f"Local mode: Raw data will be saved to: {local_raw_output_path}")
         local_raw_output_path.mkdir(parents=True, exist_ok=True) # Ensure it exists

    # --- Step 1: Discover New PRs --- 
    discover_args = [
        config_path,
        "--output-file", str(new_prs_file),
        "--no-upload",
        "--log-output-path", str(updated_log_file)
    ]
    if args.debug:
        discover_args.append("--debug")
    if args.local:
        discover_args.append("--local")
        
    step1_success = run_script("discover_new_prs.py", discover_args)

    if not step1_success:
        print("Pipeline failed at Step 1: Discover New PRs. Exiting.")
        # Consider cleanup of intermediate_dir? For now, leave it for debugging.
        sys.exit(1)

    # --- Step 2: Fetch Raw Data --- 
    step2_success = True # Assume success if no new PRs
    try:
        # Check if new PRs were actually found
        if not new_prs_file.exists() or new_prs_file.stat().st_size == 0:
            print("No new PRs found to process in Step 2.")
        else:
            fetcher_args = [
                "--config", config_path,
                "--input-pr-list", str(new_prs_file)
            ]
            if args.debug:
                fetcher_args.append("--debug")
            # Pass --local-output-dir if orchestrator is in local mode
            if args.local:
                 fetcher_args.extend(["--local-output-dir", str(local_raw_output_path)])
            
            step2_success = run_script("github_pr_fetcher.py", fetcher_args)

            if not step2_success:
                print("Pipeline failed at Step 2: Fetch Raw Data. Skipping final log upload.")
                sys.exit(1)
                
    except Exception as e:
        print(f"Error during Step 2 preparation/execution: {e}", file=sys.stderr)
        step2_success = False
        sys.exit(1)

    # --- Step 3: Upload Updated Log File --- 
    step3_success = False
    if step1_success and step2_success:
        if args.local:
             print("Running in local mode. Skipping final upload of processed log.")
             print(f"The final updated log is available at: {updated_log_file}")
             step3_success = True # Consider local mode a success here
        elif not updated_log_file.exists():
            print(f"Error: Updated log file {updated_log_file} not found. Cannot upload.", file=sys.stderr)
        else:
            print("\n" + "-"*20 + " Uploading Final Processed Log " + "-"*20)
            metadata_path = Path(config['data_paths']['metadata']).as_posix().strip('/')
            rclone_remote = config['rclone_remote_name']
            remote_log_path = f"{rclone_remote}:{metadata_path}/processed_prs.log"
            
            upload_success, _ = run_rclone_command(['copyto', str(updated_log_file), remote_log_path], suppress_output=not args.debug)
            
            if upload_success:
                print("Successfully uploaded final processed PR log.")
                step3_success = True
            else:
                print("Failed to upload final processed PR log.", file=sys.stderr)
    else:
         print("Skipping final log upload due to failures in previous steps.")

    # --- Cleanup --- 
    print("\n" + "-"*20 + " Cleaning Up Intermediate Files " + "-"*20)
    try:
        # Only remove intermediate files, not the whole directory if local raw output exists
        if new_prs_file.exists():
             new_prs_file.unlink()
             print(f"Removed intermediate file: {new_prs_file}")
        if updated_log_file.exists():
             # If not in local mode OR if local_raw_output_path is different from intermediate_dir parent
             # we can potentially remove the updated log file too. But let's keep it simple:
             # Always remove the intermediate log file copy. The user got the raw data if needed.
             updated_log_file.unlink()
             print(f"Removed intermediate file: {updated_log_file}")
        
        # Try removing the intermediate directory if it's empty
        if not any(intermediate_dir.iterdir()):
             intermediate_dir.rmdir()
             print(f"Removed empty intermediate directory: {intermediate_dir}")
        else:
             print(f"Intermediate directory {intermediate_dir} not empty, leaving it.")
             
    except Exception as e:
        print(f"Warning: Failed during cleanup of intermediate directory {intermediate_dir}: {e}", file=sys.stderr)

    # --- Final Status --- 
    print("="*40)
    if step1_success and step2_success and step3_success:
        print("Pipeline finished successfully.")
        if args.local:
            print(f"Raw data saved locally in: {local_raw_output_path}")
        sys.exit(0)
    else:
        print("Pipeline finished with errors.")
        sys.exit(1) 