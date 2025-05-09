import streamlit as st
import pandas as pd
import re
from collections import Counter
import json
import os
import glob
import gzip
import matplotlib.pyplot as plt # Added for explicit figure creation

# --- Determine script directory for robust path construction ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Configuration (Paths are now absolute) ---
PROCESSED_PRS_FILE_PATH = os.path.join(SCRIPT_DIR, "../new_prs_to_process.txt")
SFT_DATASET_FILE_PATH = os.path.join(SCRIPT_DIR, "../dataset/v1/train.jsonl.gz")
DATASET_CARD_PATH = os.path.join(SCRIPT_DIR, "../dataset/v1/dataset_card.md")
BRONZE_DATA_PATH = os.path.join(SCRIPT_DIR, "../bronze/")
DEFAULT_SAMPLE_SIZE = 5 # Number of comments to sample from bronze data

# --- Helper Functions ---

def extract_repo_from_url(url):
    """Extracts 'owner/repo' from a GitHub PR URL."""
    match = re.search(r"github\.com/([^/]+/[^/]+)/pull/\d+", url)
    if match:
        return match.group(1)
    return None

@st.cache_data # Cache data loading for performance
def load_processed_prs(file_path):
    """Loads the list of processed PR URLs and counts them per repository."""
    prs_df = pd.DataFrame(columns=['url', 'repository'])
    processed_pr_urls = []
    if not os.path.exists(file_path):
        st.warning(f"Warning: Processed PRs file not found at {file_path}")
        return pd.DataFrame(columns=['url']), 0, Counter()

    try:
        with open(file_path, 'r') as f:
            processed_pr_urls = [line.strip() for line in f if line.strip()]
        
        if not processed_pr_urls:
            # st.info(f"No PRs found in {file_path}") # Reduced verbosity
            return pd.DataFrame(columns=['url']), 0, Counter()

        repos = [extract_repo_from_url(url) for url in processed_pr_urls]
        prs_df['url'] = processed_pr_urls
        prs_df['repository'] = [repo if repo else "Unknown" for repo in repos]
        
        repo_counts = Counter(prs_df['repository'])
        return prs_df, len(processed_pr_urls), repo_counts
    except Exception as e:
        st.error(f"Error loading processed PRs from {file_path}: {e}")
        return pd.DataFrame(columns=['url']), 0, Counter()

@st.cache_data
def load_sft_dataset(file_path):
    """Loads the SFT dataset from a JSONL file (can be gzipped)."""
    data = []
    if not os.path.exists(file_path):
        st.warning(f"Warning: SFT dataset file not found at {file_path}")
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
        st.warning(f"Markdown file not found: {file_path}")
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
def discover_bronze_repositories(bronze_dir_path):
    """Discovers repository data files in the bronze directory."""
    if not os.path.isdir(bronze_dir_path):
        st.warning(f"Bronze data directory not found: {bronze_dir_path}")
        return []
    
    repo_files = glob.glob(os.path.join(bronze_dir_path, "*.jsonl.gz"))
    repo_names = sorted([os.path.basename(f).replace(".jsonl.gz", "") for f in repo_files])
    return repo_names

@st.cache_data
def load_repository_comments(bronze_dir_path, repository_name, sample_size=DEFAULT_SAMPLE_SIZE):
    """Loads a sample of comments from a specific repository's gzipped JSONL file."""
    file_path = os.path.join(bronze_dir_path, f"{repository_name}.jsonl.gz")
    if not os.path.exists(file_path):
        st.warning(f"Data file for repository {repository_name} not found: {file_path}")
        return []

    comments_sample = []
    try:
        with gzip.open(file_path, 'rt', encoding='utf-8') as gz_file: # rt for text mode
            for i, line in enumerate(gz_file):
                if i >= sample_size:
                    break
                try:
                    comments_sample.append(json.loads(line))
                except json.JSONDecodeError as json_err:
                    st.warning(f"Skipping malformed JSON line in {file_path}: {json_err}")
                    continue # Skip malformed lines
        return comments_sample
    except Exception as e:
        st.error(f"Error loading comments for {repository_name} from {file_path}: {e}")
        return []

# --- Main Dashboard ---
st.set_page_config(layout="wide")
st.title("⚙️ GitHub PRs to SFT Dataset - Insights Dashboard")

# --- Load Data ---
with st.spinner("Loading data..."):
    processed_prs_df, total_prs_processed, repo_counts = load_processed_prs(PROCESSED_PRS_FILE_PATH)
    sft_df = load_sft_dataset(SFT_DATASET_FILE_PATH)
    dataset_card_content = load_markdown_file(DATASET_CARD_PATH)
    bronze_repos = discover_bronze_repositories(BRONZE_DATA_PATH)

# --- Display Metrics ---
st.header("📊 Overall Summary")
col1, col2, col3 = st.columns(3)
col1.metric("Total PRs Processed/Identified", total_prs_processed)
col2.metric("Total SFT Records (from train.jsonl.gz)", len(sft_df) if not sft_df.empty else 0)
col3.metric("Unique Repositories Involved (from PR log)", len(repo_counts) if repo_counts else 0)

st.markdown("---")

# --- Dataset Card Display ---
st.header("📝 Dataset Card")
if dataset_card_content:
    st.markdown(dataset_card_content, unsafe_allow_html=True) 
else:
    st.info(f"Dataset card ({os.path.basename(DATASET_CARD_PATH)}) not found, empty, or error during loading. Check path: {DATASET_CARD_PATH}")

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

# --- Browse Raw Comments by Repository (Bronze Layer) ---
st.header("🔍 Browse Raw Comments by Repository (Bronze Layer)")
if bronze_repos:
    # Add a unique key to selectbox
    selected_repo = st.selectbox(
        "Select a Repository to View Sample Comments:",
        options=[""] + bronze_repos, # Add an empty option for default state
        format_func=lambda x: "Select..." if x == "" else x,
        key="bronze_repo_select"
    )
    
    if selected_repo: # Only proceed if a repository is actually selected
        st.subheader(f"Sample Comments for: {selected_repo}")
        # Use a unique key for number_input to avoid issues when selected_repo changes
        sample_size_input = st.number_input(
            "Number of samples to display:",
            min_value=1,
            max_value=50, # Allow up to 50 samples
            value=DEFAULT_SAMPLE_SIZE,
            key=f"sample_size_{selected_repo}"
        )
        
        comments = load_repository_comments(BRONZE_DATA_PATH, selected_repo, sample_size=sample_size_input)
        if comments:
            for i, comment_data in enumerate(comments):
                # Ensure comment_data is a dict, provide default if not
                if not isinstance(comment_data, dict):
                    st.warning(f"Skipping malformed comment entry {i+1} for {selected_repo} (expected a dictionary).")
                    continue

                # Try to get a unique ID, default if not available or not suitable for key
                comment_id_raw = comment_data.get('comment_id')
                comment_id_for_key = str(comment_id_raw) if comment_id_raw is not None else f"comment_{i}"

                with st.expander(f"Comment {i+1} (ID: {comment_data.get('comment_id', 'N/A')})", expanded=False):
                    user_login = comment_data.get('comment_user_login', 'N/A')
                    
                    st.markdown(f"**User:** {user_login}")
                    st.markdown(f"**File Path:** `{comment_data.get('comment_path', 'N/A')}`")
                    st.markdown(f"**Created At:** {comment_data.get('comment_created_at', 'N/A')}")
                    
                    st.markdown("**Comment Body:**")
                    st.text_area(
                        f"Body_{selected_repo}_{comment_id_for_key}",
                        comment_data.get('comment_body', ''),
                        height=100,
                        key=f"body_ta_{selected_repo}_{comment_id_for_key}_{i}",
                        disabled=True
                    )

                    st.markdown("**Diff Hunk:**")
                    st.text_area(
                        f"Diff Hunk_{selected_repo}_{comment_id_for_key}",
                        comment_data.get('diff', 'No diff hunk available'),
                        height=200,
                        key=f"dh_ta_{selected_repo}_{comment_id_for_key}_{i}",
                        disabled=True
                    )
        elif selected_repo: # if selected_repo is not an empty string but comments list is empty
             st.info(f"No comments found or able to be loaded for {selected_repo}. Make sure the .jsonl.gz file is correctly formatted, not empty, and accessible.")
else:
    st.info("No repository data files found in the bronze layer directory. Check `BRONZE_DATA_PATH` and ensure files like `owner_repo.jsonl.gz` exist.")

# --- Sidebar ---
st.sidebar.header("About")
st.sidebar.info(
    "This dashboard provides insights into the GitHub PR processing pipeline, "
    "the generated SFT dataset, and allows browsing raw comment data from the bronze layer."
)
st.sidebar.markdown("---")
st.sidebar.header("Data File Paths")
st.sidebar.markdown(f"**Processed PRs Log:** `{PROCESSED_PRS_FILE_PATH}`")
st.sidebar.markdown(f"**SFT Train Dataset:** `{SFT_DATASET_FILE_PATH}`")
st.sidebar.markdown(f"**Dataset Card:** `{DATASET_CARD_PATH}`")
st.sidebar.markdown(f"**Bronze Layer Data:** `{BRONZE_DATA_PATH}`")

if st.sidebar.button("Reload All Data & Clear Cache"):
    st.cache_data.clear()
    st.rerun() 