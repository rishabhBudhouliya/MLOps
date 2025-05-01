import os
import sys
import subprocess
import yaml
import argparse
import time
from pathlib import Path

# Helper functions (copied/adapted from existing scripts)
def load_config(config_path):
    """Loads the YAML configuration file."""
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        # Validation for this script
        if not config:
            raise ValueError("Config file is empty.")
        if 'data_paths' not in config or 'processed' not in config['data_paths']:
             raise ValueError("Missing 'data_paths.processed' in config.")
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
    """Runs an rclone command with retry logic."""
    command = ['rclone'] + args
    print(f"Running command: {' '.join(command)}")

    for attempt in range(max_retries):
        try:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate()
            if process.returncode != 0:
                 if attempt < max_retries - 1:
                     print(f"Attempt {attempt + 1}/{max_retries} failed. Retrying in {retry_delay} seconds...")
                     time.sleep(retry_delay)
                     continue
                 print(f"Error running rclone command: {' '.join(command)}", file=sys.stderr)
                 print(f"Return Code: {process.returncode}", file=sys.stderr)
                 print(f"Stderr: {stderr}", file=sys.stderr)
                 return False, stderr
            else:
                if not suppress_output and stdout:
                    print(f"Rclone stdout: {stdout}")
                # Rclone often outputs progress/stats to stderr, print it unless suppressed
                if stderr and not suppress_output:
                    print(f"Rclone stderr: {stderr}")
                return True, stderr # Return stderr even on success for potential info
        except FileNotFoundError:
             print("Error: 'rclone' command not found.", file=sys.stderr)
             return False, "rclone not found"
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Attempt {attempt + 1}/{max_retries} failed with exception: {e}. Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                continue
            print(f"An unexpected error occurred while running rclone: {e}", file=sys.stderr)
            return False, str(e)
    return False, "Max retries exceeded for rclone command"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload transformed data to remote storage.")
    parser.add_argument("--config", required=True, help="Path to the YAML configuration file.")
    parser.add_argument("--input-dir", required=True, help="Directory containing the transformed data to upload.")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode (more verbose rclone output).")
    args = parser.parse_args()

    print("Starting processed data load step...")
    config = load_config(args.config)
    input_dir = Path(args.input_dir)

    if not input_dir.is_dir():
        print(f"Error: Input directory '{input_dir}' not found or is not a directory.", file=sys.stderr)
        sys.exit(1)

    # Check if input directory is empty
    if not any(input_dir.iterdir()):
         print(f"Input directory '{input_dir}' is empty. Nothing to upload.")
         print("Load step finished (no action taken).")
         sys.exit(0)

    # Prepare rclone arguments
    rclone_remote = config['rclone_remote_name']
    # Ensure the processed data path ends with a slash for rclone directory copy
    processed_base_path = Path(config['data_paths']['processed']).as_posix().strip('/') + '/'
    remote_dest_path = f"{rclone_remote}:{processed_base_path}"

    # Use `rclone copy` - it copies source files to destination, skipping existing identical files.
    # Alternative: `rclone sync` would make the destination match the source exactly (deleting extra files in dest).
    # `copy` is generally safer unless you specifically need the sync behavior.
    rclone_args = [
        'copy', # or 'sync'
        str(input_dir),      # Source directory
        remote_dest_path,    # Destination path
        '--progress',        # Show progress during transfer
        # Add other rclone flags if needed (e.g., --checksum, --transfers, --checkers)
    ]
    if args.debug:
         rclone_args.append('-vv') # Add verbose logging for debug mode

    print(f"Uploading contents of '{input_dir}' to '{remote_dest_path}'...")
    success, rclone_stderr = run_rclone_command(rclone_args, suppress_output=False) # Show rclone output

    print("=" * 40)
    if success:
        print("Load step completed successfully.")
        sys.exit(0)
    else:
        print("Load step failed.", file=sys.stderr)
        # Stderr was already printed by run_rclone_command on failure
        sys.exit(1) 