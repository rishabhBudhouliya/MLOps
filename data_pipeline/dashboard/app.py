import streamlit as st
import pandas as pd
import re
from collections import Counter
import json
import os
import glob
import gzip
import matplotlib.pyplot as plt # Added for explicit figure creation

# --- Determine script directory for robust path construction for files COPIED into the image ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Configuration ---
# Path for the comprehensive PR log, ACCESSED VIA A MOUNTED VOLUME
PROCESSED_PRS_FILE_PATH = "/mnt/object/data/metadata/processed_prs.log"

# Paths for SFT data and dataset card, also ACCESSED VIA A MOUNTED VOLUME
SFT_DATASET_FILE_PATH = "/mnt/object/data/processed/train.jsonl.gz"
DATASET_CARD_PATH = "/mnt/object/data/processed/dataset_card.md"

DEFAULT_SAMPLE_SIZE = 5 # Number of comments to sample from bronze data

# --- Helper Functions ---

def extract_repo_from_url(url):
    """Extracts 'owner/repo' from a GitHub PR URL."""
    match = re.search(r"github\.com/([^/]+/[^/]+)/pull/\d+", url)
    if match:
        return match.group(1)
    # Handle cases where URL might start with @, e.g., from user input
    match_at = re.search(r"@https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
    if match_at:
        return match_at.group(1)
    return None

@st.cache_data # Cache data loading for performance
def load_processed_prs(file_path):
    """Loads the list of processed PR URLs and counts them per repository."""
    prs_df = pd.DataFrame(columns=['url', 'repository'])
    processed_pr_urls = []
    if not os.path.exists(file_path):
        st.warning(f"Warning: Processed PRs log file not found at {file_path}. Ensure the volume is mounted correctly and the path is accessible within the container.")
        return pd.DataFrame(columns=['url']), 0, Counter()

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            # Strip leading/trailing whitespace and potential leading '@' characters
            processed_pr_urls = [line.strip().lstrip('@') for line in f if line.strip()]
        
        if not processed_pr_urls:
            # st.info(f"No PRs found in {file_path}") # Reduced verbosity
            return pd.DataFrame(columns=['url']), 0, Counter()

        repos = [extract_repo_from_url(url) for url in processed_pr_urls]
        # Filter out None repos that might result from non-matching lines if any
        valid_repos = [repo for repo in repos if repo is not None]
        
        # Create DataFrame with only valid PR URLs that had a repo extracted
        valid_pr_urls = [url for url, repo in zip(processed_pr_urls, repos) if repo is not None]
        
        if not valid_pr_urls:
             return pd.DataFrame(columns=['url', 'repository']), 0, Counter()

        prs_df['url'] = valid_pr_urls
        prs_df['repository'] = valid_repos
        
        repo_counts = Counter(valid_repos)
        return prs_df, len(valid_pr_urls), repo_counts # Count only valid PRs
    except Exception as e:
        st.error(f"Error loading processed PRs from {file_path}: {e}")
        return pd.DataFrame(columns=['url']), 0, Counter()

@st.cache_data
def load_sft_dataset(file_path):
    """Loads the SFT dataset from a JSONL file (can be gzipped)."""
    data = []
    if not os.path.exists(file_path):
        st.warning(f"Warning: SFT dataset file not found at {file_path}. Ensure the volume is mounted correctly and the path is accessible within the container.")
        return pd.DataFrame()
    
    try:
        open_func = gzip.open if file_path.endswith('.gz') else open
        mode = 'rt' if file_path.endswith('.gz') else 'r'
        
        with open_func(file_path, mode, encoding='utf-8') as f:
            for line in f:
                data.append(json.loads(line))
        if not data:
            # st.info(f"No data found in {file_path}") # Reduced verbosity
            return pd.DataFrame()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"Error loading SFT dataset from {file_path}: {e}")
        return pd.DataFrame()

@st.cache_data
def load_markdown_file(file_path):
    """Loads a markdown file and returns its content as a string."""
    if not os.path.exists(file_path):
        st.warning(f"Markdown file not found: {file_path}. Ensure the volume is mounted correctly and the path is accessible within the container.")
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
            if not content.strip(): # Check if content is empty or just whitespace
                st.info(f"Markdown file is empty or contains only whitespace: {file_path}")
                return None # Treat as if not found for display purposes
            return content
    except Exception as e:
        st.error(f"Error reading markdown file {file_path}: {e}")
        return None

@st.cache_data
def load_sample_sft_data(sft_file_path, sample_size=DEFAULT_SAMPLE_SIZE):
    """Loads a sample of records from the SFT training data file (train.jsonl.gz)."""
    if not os.path.exists(sft_file_path):
        st.warning(f"SFT training data file for sampling not found: {sft_file_path}. Ensure the volume is mounted correctly.")
        return []

    records_sample = []
    try:
        open_func = gzip.open if sft_file_path.endswith('.gz') else open
        mode = 'rt' if sft_file_path.endswith('.gz') else 'r'
        with open_func(sft_file_path, mode, encoding='utf-8') as f_sft:
            for i, line in enumerate(f_sft):
                if i >= sample_size:
                    break
                try:
                    records_sample.append(json.loads(line))
                except json.JSONDecodeError as json_err:
                    st.warning(f"Skipping malformed JSON line in {sft_file_path}: {json_err}")
                    continue 
        return records_sample
    except Exception as e:
        st.error(f"Error loading sample SFT data from {sft_file_path}: {e}")
        return []

# --- Main Dashboard ---
st.set_page_config(layout="wide")
st.title("⚙️ GitHub PRs to SFT Dataset - Insights Dashboard")

# --- Load Data ---
with st.spinner("Loading data..."):
    processed_prs_df, total_prs_processed, repo_counts = load_processed_prs(PROCESSED_PRS_FILE_PATH)
    sft_df = load_sft_dataset(SFT_DATASET_FILE_PATH)
    dataset_card_content = load_markdown_file(DATASET_CARD_PATH)

# --- Display Metrics ---
st.header("📊 Overall Summary")
col1, col2, col3 = st.columns(3)
col1.metric("Total PRs Processed (from log)", total_prs_processed)
col2.metric("Total SFT Records (from train.jsonl.gz)", len(sft_df) if not sft_df.empty else 0)
col3.metric("Unique Repositories Involved (from PR log)", len(repo_counts) if repo_counts else 0)

st.markdown("---")

# --- Dataset Card Display ---
st.header("📝 Dataset Card")
if dataset_card_content:
    st.markdown(dataset_card_content, unsafe_allow_html=True) 
else:
    st.info(f"Dataset card ({os.path.basename(DATASET_CARD_PATH)}) not found, empty, or error during loading. Check mount and path: {DATASET_CARD_PATH}")

st.markdown("---")

# --- Visualizations ---
st.header("📈 Data Distribution")

if repo_counts:
    st.subheader("PRs Processed per Repository")
    # Convert Counter to DataFrame for Streamlit charts
    repo_counts_df = pd.DataFrame(repo_counts.items(), columns=['Repository', 'Number of PRs']).sort_values(by='Number of PRs', ascending=False)
    st.bar_chart(repo_counts_df.set_index('Repository'))
else:
    st.info("No repository data to display.")

if not sft_df.empty:
    st.subheader("SFT Dataset Preview")
    st.dataframe(sft_df.head())

    # Example: Distribution of instruction lengths
    if 'instruction' in sft_df.columns:
        sft_df['instruction_length'] = sft_df['instruction'].astype(str).apply(len)
        st.subheader("Distribution of Instruction Lengths")
        fig, ax = st.pyplot() # Create a matplotlib figure explicitly
        if fig and ax:
             sft_df['instruction_length'].plot(kind='hist', bins=50, ax=ax, title="Instruction Lengths")
             ax.set_xlabel("Length of Instruction")
             ax.set_ylabel("Frequency")
             st.pyplot(fig) # Pass the figure to st.pyplot
        else:
            st.warning("Could not generate instruction length plot.")


    if 'response' in sft_df.columns:
        sft_df['response_length'] = sft_df['response'].astype(str).apply(len)
        st.subheader("Distribution of Response Lengths")
        fig_resp, ax_resp = st.pyplot() # Create a matplotlib figure explicitly for response
        if fig_resp and ax_resp:
            sft_df['response_length'].plot(kind='hist', bins=50, ax=ax_resp, title="Response Lengths")
            ax_resp.set_xlabel("Length of Response")
            ax_resp.set_ylabel("Frequency")
            st.pyplot(fig_resp) # Pass the figure to st.pyplot
        else:
            st.warning("Could not generate response length plot.")

else:
    st.info("SFT dataset is empty or not loaded. No SFT-specific visualizations to display.")

st.markdown("---")

# --- Sample Records from SFT Training Data ---
st.header(f"🔍 Sample Records from SFT Training Data (`{os.path.basename(SFT_DATASET_FILE_PATH)}`)")

sample_size_sft_input = st.number_input(
    "Number of samples to display from SFT training data:",
    min_value=1,
    max_value=50, 
    value=DEFAULT_SAMPLE_SIZE,
    key="sample_size_sft_train"
)

sampled_sft_records = load_sample_sft_data(SFT_DATASET_FILE_PATH, sample_size=sample_size_sft_input)

if sampled_sft_records:
    for i, record_data in enumerate(sampled_sft_records):
        if not isinstance(record_data, dict):
            st.warning(f"Skipping malformed record entry {i+1} from SFT data (expected a dictionary).")
            continue
        
        # Using keys confirmed from user's previous debug output for bronze data, assuming train data has similar structure
        record_id_raw = record_data.get('comment_id') 
        record_id_for_key = str(record_id_raw) if record_id_raw is not None else f"record_{i}"

        with st.expander(f"Sample Record {i+1} (ID: {record_data.get('comment_id', 'N/A')})", expanded=False):
            user_login = record_data.get('comment_user_login', 'N/A') 
            st.markdown(f"**User:** {user_login}")
            st.markdown(f"**File Path (from comment):** `{record_data.get('comment_path', 'N/A')}`") 
            st.markdown(f"**Created At (comment):** {record_data.get('comment_created_at', 'N/A')}") 
            
            # Display other fields relevant to SFT if they exist (e.g., 'instruction', 'response')
            st.markdown("**Instruction:**")
            st.text_area(
                f"Instruction_{record_id_for_key}", 
                str(record_data.get('instruction', '')), 
                height=100,
                key=f"instr_ta_{record_id_for_key}_{i}", 
                disabled=True
            )
            st.markdown("**Response:**")
            st.text_area(
                f"Response_{record_id_for_key}", 
                str(record_data.get('response', '')), 
                height=150,
                key=f"resp_ta_{record_id_for_key}_{i}", 
                disabled=True
            )

            # Keep original comment body and diff if they are still relevant and present in train.jsonl.gz
            st.markdown("**Original Comment Body (if available):**")
            st.text_area(
                f"OrigBody_{record_id_for_key}", 
                record_data.get('comment_body', ''), 
                height=100,
                key=f"origbody_ta_{record_id_for_key}_{i}", 
                disabled=True
            )
            st.markdown("**Original Diff Hunk (if available):**")
            st.text_area(
                f"OrigDiff_{record_id_for_key}", 
                record_data.get('diff', 'No diff hunk available'), 
                height=200,
                key=f"origdiff_ta_{record_id_for_key}_{i}", 
                disabled=True
            )
else:
    st.info(f"No sample records to display from {os.path.basename(SFT_DATASET_FILE_PATH)}. File might be empty, not found at {SFT_DATASET_FILE_PATH}, or error during loading. Check mount.")

# --- Sidebar ---
st.sidebar.header("About")
st.sidebar.info(
    "This dashboard provides insights into the GitHub PR processing pipeline, "
    "the generated SFT dataset (from train.jsonl.gz), and allows browsing sample records from the SFT training data."
)
st.sidebar.markdown("---")
st.sidebar.header("Data File Paths (as configured in app)")
st.sidebar.markdown(f"**Processed PRs Log (from mount):** `{PROCESSED_PRS_FILE_PATH}`")
st.sidebar.markdown(f"**SFT Train Dataset (from mount):** `{SFT_DATASET_FILE_PATH}`")
st.sidebar.markdown(f"**Dataset Card (from mount):** `{DATASET_CARD_PATH}`")

if st.sidebar.button("Reload All Data & Clear Cache"):
    st.cache_data.clear()
    st.rerun() 