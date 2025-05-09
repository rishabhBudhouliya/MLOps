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
        if 'data_paths' not in config or \
           'metadata' not in config['data_paths'] or \
           'raw' not in config['data_paths'] or \
           'processed' not in config['data_paths']:
             raise ValueError("Missing one or more required keys in 'data_paths' (metadata, raw, processed).")
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
        # Run the script and let its stdout/stderr stream directly to console
        subprocess.run(command, check=True, text=True)
        print(f"{script_name} completed successfully.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {script_name} failed with exit code {e.returncode}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Failed to execute {script_name}: {e}", file=sys.stderr)
        return False

# --- Main Orchestration --- 

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orchestrate the GitHub PR data pipeline.")
    parser.add_argument("--config", default="config.yaml", help="Path to the YAML configuration file.")
    parser.add_argument("--debug", action="store_true", help="Enable debug flags for sub-scripts.")
    parser.add_argument("--local", action="store_true", help="Run sub-scripts in local mode (skip remote operations, keep intermediate data locally).")
    # Define intermediate file paths
    parser.add_argument("--intermediate-dir", default="./pipeline_intermediate", help="Directory for intermediate files.")
    # parser.add_argument("--local-raw-output-dir", default="./pipeline_output_raw", help="Directory to save raw data when running with --local flag.") # Replaced by intermediate dir concept

    args = parser.parse_args()

    # --- Setup --- 
    config_path = args.config
    config = load_config(config_path)
    intermediate_dir = Path(args.intermediate_dir)
    intermediate_dir.mkdir(parents=True, exist_ok=True)
    print(f"Using intermediate directory: {intermediate_dir}")

    # Define intermediate artifact paths
    new_prs_file = intermediate_dir / "new_prs_to_process.txt"
    updated_log_file = intermediate_dir / "updated_processed_prs.log"
    raw_data_dir = intermediate_dir / "raw_data" # Fetcher saves here
    transformed_data_dir = intermediate_dir / "transformed_data" # Transformer output

    # --- Flags ---
    # Keep track of step success
    step1_success = False
    step2_success = False
    step3_success = False # Transform step
    step4_success = False # Load step (upload transformed data)
    step5_success = False # Log upload step (renamed from step 3)

    # --- Step 1: Discover New PRs --- 
    print("\n" + "="*10 + " STEP 1: Discover New PRs " + "="*10)
    discover_args = [
        config_path,
        "--output-file", str(new_prs_file),
        "--no-upload", # Always handle log upload at the end
        "--log-output-path", str(updated_log_file)
    ]
    if args.debug:
        discover_args.append("--debug")
    if args.local:
        discover_args.append("--local") # discover_new_prs handles its own --local logic

    step1_success = run_script("discover_new_prs.py", discover_args)

    if not step1_success:
        print("Pipeline failed at Step 1: Discover New PRs. Exiting.")
        # Cleanup? For now, leave intermediate files for debugging.
        sys.exit(1)

    # Check if new PRs were found before proceeding to fetch/transform/load
    if not new_prs_file.exists() or new_prs_file.stat().st_size == 0:
        print("\nNo new PRs found to process. Skipping Fetch, Transform, and Load steps.")
        # Mark subsequent steps as "skipped" (or trivially successful)
        step2_success = True
        step3_success = True
        step4_success = True
        # We still need to potentially upload the log file if discover ran ok.
    else:
        # --- Step 2: Fetch Raw Data --- 
        print("\n" + "="*10 + " STEP 2: Fetch Raw Data " + "="*10)
        # Always fetch locally to the intermediate directory first
        raw_data_dir.mkdir(parents=True, exist_ok=True)
        fetcher_args = [
            "--config", config_path,
            "--input-pr-list", str(new_prs_file),
            "--local-output-dir", str(raw_data_dir) # Use intermediate dir for local output
        ]
        if args.debug:
            fetcher_args.append("--debug")
        if args.local: # If the main pipeline is running in local mode
            fetcher_args.append("--skip-remote-upload") # Tell fetcher to also skip its S3 uploads

        step2_success = run_script("github_pr_fetcher.py", fetcher_args)

        if not step2_success:
            print("Pipeline failed at Step 2: Fetch Raw Data. Skipping subsequent steps.")
            # Don't upload log if fetch failed
            sys.exit(1)

        # --- Step 3: Transform and Align Data ---
        print("\n" + "="*10 + " STEP 3: Transform and Align Data " + "="*10)
        transformed_data_dir.mkdir(parents=True, exist_ok=True)
        transformer_args = [
            "--config", config_path,
            "--input-dir", str(raw_data_dir),
            "--output-dir", str(transformed_data_dir),
        ]
        if args.debug:
            transformer_args.append("--debug")

        step3_success = run_script("transform_align.py", transformer_args)

        if not step3_success:
            print("Pipeline failed at Step 3: Transform and Align Data. Skipping load step.")
            # Don't upload log if transform failed
            sys.exit(1)

        # --- Step 3.5: Build Bronze Layer ---
        print("\n" + "="*10 + " STEP 3.5: Build Bronze Layer " + "="*10)
        bronze_dir = Path("bronze")
        step3_5_success = run_script("build_bronze.py", ["--input-dir", str(transformed_data_dir), "--output-dir", str(bronze_dir)])
        if not step3_5_success:
            print("Pipeline failed at Step 3.5: Build Bronze Layer. Skipping subsequent steps.")
            sys.exit(1)

        # --- Step 3.6: Build Silver Layer ---
        print("\n" + "="*10 + " STEP 3.6: Build Silver Layer " + "="*10)
        silver_dir = Path("dataset/v1")
        step3_6_success = run_script("build_silver.py", ["--bronze-dir", str(bronze_dir), "--output-dir", str(silver_dir)])
        if not step3_6_success:
            print("Pipeline failed at Step 3.6: Build Silver Layer. Skipping subsequent steps.")
            sys.exit(1)

        # --- Step 4: Load Silver Data ---
        print("\n" + "="*10 + " STEP 4: Load Silver Data " + "="*10)
        if args.local:
            print("Running in local mode. Skipping remote upload of silver data.")
            print(f"Silver data is available locally at: {silver_dir}")
            step4_success = True # Consider local mode a success here
        else:
            # Check if there's actually silver data to upload
            if not any(silver_dir.iterdir()):
                print(f"Silver directory '{silver_dir}' is empty. Skipping upload.")
                step4_success = True
            else:
                loader_args = [
                    "--config", config_path,
                    "--input-dir", str(silver_dir),
                ]
                if args.debug:
                    loader_args.append("--debug")

                step4_success = run_script("load_data.py", loader_args)

                if not step4_success:
                     print("Pipeline failed at Step 4: Load Silver Data.")
                     sys.exit(1)

    # --- Step 5: Upload Updated Log File ---
    # This runs if Step 1 was successful, AND subsequent steps that ran also succeeded.
    # If no new PRs were found, steps 2-4 were skipped (success=True), so log still uploads.
    # If new PRs were found, steps 2-4 must have succeeded.
    print("\n" + "="*10 + " STEP 5: Upload Final Processed Log " + "="*10)
    if step1_success and step2_success and step3_success and step4_success:
        if args.local:
            print("Running in local mode. Skipping final upload of processed log.")
            print(f"The final updated log is available at: {updated_log_file}")
            step5_success = True # Consider local mode a success here
        elif not updated_log_file.exists():
            print(f"Error: Updated log file {updated_log_file} not found. Cannot upload.", file=sys.stderr)
            step5_success = False # Mark failure
        else:
            metadata_path = Path(config['data_paths']['metadata']).as_posix().strip('/')
            rclone_remote = config['rclone_remote_name']
            remote_log_path = f"{rclone_remote}:{metadata_path}/processed_prs.log"

            upload_success, _ = run_rclone_command(['copyto', str(updated_log_file), remote_log_path], suppress_output=not args.debug)

            if upload_success:
                print("Successfully uploaded final processed PR log.")
                step5_success = True
            else:
                print("Failed to upload final processed PR log.", file=sys.stderr)
                step5_success = False # Mark failure
    else:
        print("Skipping final log upload due to failures or skips in previous steps.")
        # If prior steps failed, step5_success remains False

    # --- Cleanup --- 
    print("\n" + "="*10 + " STEP 6: Cleaning Up Intermediate Files " + "="*10)
    try:
        # Decide whether to keep intermediate data based on --local or errors
        final_status_success = step1_success and step2_success and step3_success and step4_success and step5_success
        keep_intermediate = args.local or not final_status_success

        if keep_intermediate:
            print(f"Keeping intermediate directory due to --local flag or pipeline errors: {intermediate_dir}")
            # Optionally, still remove specific temp files like the PR list?
            if new_prs_file.exists():
                 new_prs_file.unlink()
                 print(f"Removed intermediate file: {new_prs_file}")
            # Keep raw_data_dir, transformed_data_dir, and updated_log_file
        else:
            print(f"Pipeline finished successfully. Removing intermediate directory: {intermediate_dir}")
            shutil.rmtree(intermediate_dir)

    except Exception as e:
        print(f"Warning: Failed during cleanup: {e}", file=sys.stderr)

    # --- Final Status --- 
    print("="*40)
    if final_status_success:
        print("Pipeline finished successfully.")
        if args.local:
             print(f"Intermediate data retained locally in: {intermediate_dir}")
             print(f"(Raw data: {raw_data_dir})")
             print(f"(Transformed data: {transformed_data_dir})")
             print(f"(Updated log: {updated_log_file})")

        sys.exit(0)
    else:
        print("Pipeline finished with errors.")
        if args.local:
             print(f"Intermediate data retained locally for debugging in: {intermediate_dir}")
        sys.exit(1) 