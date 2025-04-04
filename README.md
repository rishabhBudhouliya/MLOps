## Title: Github Bot for Code Review Assistance

### Goal
To develop a bot that is able to create context-aware review comments on Github Pull Requests.

### Value Proposition
Jenkins, valued as a leading open-source automation server, thrives with the support of a large open source community. Naturally, the maintainers deal with a large number of Pull Requests and in their years of maintaining the large, old codebase, they have defined a structured process to review PRs. In this extensive [document](https://wiki.jenkins.io/display/JENKINS/Beginners+Guide+to+Contributing#BeginnersGuidetoContributing-Areyouinterestedinwritingcode%3F), they mention some of the things they check each PR with: 
- Checkstyle: analyzes Java code for coding standard violations
- Code Coverage: enforces users to improve unit test coverage by writing unit test for their contribution
- ESLint: static code analysis for Javascript code
- SpotBugs: Uses static analysis to look for bugs in Java code

Currently, the standard code review process on GitHub (without our bot) involves:
* Human reviewers (peers, team leads, maintainers) are assigned or volunteer to review.
* Reviewers manually read through the code changes (the diff).
* They rely on their own knowledge, experience, memory of the codebase, and established team guidelines to identify issues ranging from simple typos and style violations to complex logical errors, security vulnerabilities, and architectural concerns.
* Reviewers manually write comments on specific lines or provide overall feedback.
* This process is often asynchronous, potentially leading to significant delays depending on reviewer availability.
* The quality and thoroughness of the review depend heavily on the individual reviewer's diligence, current workload, and familiarity with the specific code area. Repetitive identification of common issues consumes significant reviewer time.

With our bot, we aim to:
1. Reduce reviewer load: automate the detection of common issues and potential pitfalls, freeing reviewers to focus on higher level architectural design concerns.
2. Faster feedback loop for developers: Developers would receive an initial feedback on their PR almost instantly, thus removing the wait that can vary from a couple of hours to days depending on the reviewer’s availability
3. Consistency: This bot aims to introduce a consistent reviewing pattern to the entire codebase, and that is often an ignored factor while developing source code with multiple contributors. Style and procedure of reviewing varies with each reviewer and that can lead to increased time to merge.

Metric to be judged on:
1. Location awareness: 
   1. Precision: Of all the locations the bot commented on, what fraction were actual human comment locations in the test set?
   2. Recall: Of all the actual human comment locations in the test set, what fraction did the bot find?
2. Semantic Similarity:  **BERTScore:** Computes similarity using contextual embeddings from BERT, focusing on semantic meaning rather than just exact word matches.



### Contributors

| Name               | Responsible for                                              | Link to their commits in this repo |
|--------------------|--------------------------------------------------------------|------------------------------------|
| All team members   | DevOps, Overall design of ML System, Value Proposition, Decisions around Content Extraction at Inference, Custom dataset, Model selection, UI/UX of the bot |                                    |
| Nidhi Donuru       | Model Serving  |     https://github.com/BugBeak/MLOps/commits/main/?author=nidhiid                                         |                                    |
| Riya Patil         | Model Training and Experimentation    | https://github.com/BugBeak/MLOps/commits/main/?author=Riyap30                    |                                    |
| Khushi Sharma      | Evaluation and Monitoring  | https://github.com/BugBeak/MLOps/commits/main/?author=BugBeak                               |                                    |
| Rishabh Budhouliya | Data Pipeline   | https://github.com/BugBeak/MLOps/commits/main/?author=rishabhBudhouliya   

---
### System Diagram

![](https://github.com/BugBeak/MLOps/blob/main/system_diagram.png?raw=true)



### Summary of outside materials

|            | How it was created                                           | Conditions of use                                            |
|------------|--------------------------------------------------------------|--------------------------------------------------------------|
| Code LLaMA | Code LLaMA                                                   | 1. **No Direct Model Hosting**: You can’t redistribute the raw model weights as-is; instead, you’re encouraged to use them in applications or fine-tune them, adhering to the license terms.<br>2. **Disclosure**: If deploying Code LLaMA in a product, inform users it’s AI-generated content where appropriate (e.g., watermarking via tools like SynthID, if applicable).<br>3. **Governance**: Establish accountability mechanisms (e.g., logging usage, monitoring outputs) to address misuse or errors.<br>4. **Safety Measures**: Implement safeguards like human oversight or filtering to prevent unintended outcomes, especially since Code LLaMA can generate executable code. |
| StarCoder  | [StarCoder: A State-of-the-Art LLM for Code](https://huggingface.co/blog/starcoder) | This an **Apache-2.0 License** model<br>**Use**: We can freely use StarCoder for any purpose—personal, academic, or commercial—without royalties or fees. This includes running it as-is, integrating it into applications, or deploying it in production systems.<br>**Modification**: We’re allowed to modify the source code or model to suit our needs.<br>**Distribution**: We can distribute the original or modified versions of StarCoder, either as source code, binaries, or model weights, to others. |

### Summary of infrastructure requirements

| Stage                            | Requirement                  | Quantity | Floating IPs | Justification                                                |
|----------------------------------|------------------------------|----------|--------------|--------------------------------------------------------------|
| **Data Collection & Processing** | m1.large / m1.xlarge         | 1 - 2    | 1            | ETL Processing: High RAM needed for data loading/processing (Pandas/Polars, context extraction). Moderate CPU for parallel fetching/processing. |
|                                  | Persistent Volume            | 1        | -            | Initial Data Storage: Store raw & processed datasets (Est. 100GB, expandable). Needs good I/O for ETL. |
| **Model Development & Training** | gpu_a100 / gpu_v100          | 1-2      | -            | Model Training/Fine-tuning: Critical for training 7B-15B models. High VRAM/compute. Multiple needed for HPO (Ray Tune) |
|                                  | m1.medium                    | 1        | 1            | MLflow Server: Host MLflow UI/backend                        |
|                                  | m1.large or gpu_t4           | 1        | -            | Evaluation Runner: Run offline evaluation                    |
| **System Infra & Deployment**    | m1.medium                    | 1        | 1            | CI/CD Setup: ArgoCD                                          |
| **Evaluation**                   | gpu_a100 / gpu_v100 / gpu_t4 | 1        | 1            | Staging/Canary API Endpoint: Run the full inference service for load testing and A/B tests.  |
|                                  | m1.medium                    | 1        | 1            | Webhook Handler (Staging/Prod): Handle incoming GitHub events. Needs reliability. FIP for GitHub communication. |
| **Serving**                      | gpu_a100 / gpu_v100          | 1/2      | 1            | Production API Endpoint: Serve inference requests. Scale quantity based on load |
|                                  | m1.medium                    | 1        | 1            | Webhook Handler: Handle incoming GitHub events reliably      |
|                                  | gpu_a100 / gpu_v100          | 1        | -            | Retraining Pipeline: Run scheduled retraining jobs           |

---
### Detailed design plan

Core Assumptions & Ground Truth:
* Dataset Size: 10k - 50k PR diff/comment pairs. We'll aim for the higher end (50k) for planning purposes, acknowledging scraping effort.
* Models: Code Llama focus (7B, 13B initially, maybe 34B exploration). Plus a classifier and ranker model.
* Serving Latency: Async acceptable, target < 1 hour, ideally minutes (e.g., < 5-10 minutes end-to-end).
* Concurrency: Design for 10-20 concurrent PR reviews.
* Team Size: 4 people.
* Platform: Chameleon Cloud.

Design Plan

1. Data Pipeline
   1. Strategy: Build an ETL Pipeline to perform the following steps:
      1. Fetch new PRs/reviews from GitHub API (handle rate limiting).
      2. Extract context: Clone repo temporarily, extract relevant code snippets or file contents based on diff.
      3. Clean/Process: Filter comments, normalize text, potentially anonymize user data if needed.
      4. Align comments to specific code lines using diff parsing.
      5. Output to the versioned offline data store
   2. Justification
      1. We need a scalable mechanism to extract data from Github REST APIs and transform that into useable dataset for model training and evaluation.
      2. The pipeline should ideally also handle online data, which would be incoming Github Webhook push payloads that can be transformed into useable data point upon which the model can perform inference.
   3. Guesstimates
      1. Persistent Storage Volume: 100GB
2. Cloud Computing
   1. Strategy: We can divide this into two parts:
      1. Infrastructure: We will provision Virtual Machines (VMs)  on ChameleonCloud for instances of CPU, GPU and additionally, we’ll require Persistence Storage (Block storage)
      2. Cloud native design: Using dockerized micro-services wherever required
   2. Justification: Already provided in the Hardware Requirements table
3. Model Training and Experimentation
   1. Strategy: 
      1. Initial Fine-tuning: Perform initial QLoRA fine-tuning of Code Llama 7B/13B on the static, processed 50k dataset.
      2. Retraining Pipeline: Integrate data collection (from feedback loop/new sources) and fine-tuning into the CI/CD pipeline, allowing scheduled or triggered retraining runs. Version model artifacts.
      3. Documentation: Rigorously document base model choice rationale (Code Llama's strengths), PEFT justification, prompt engineering attempts, and context injection methods (how code context, file context, PR description are fed to the model).
      4. Training Strategies: Implement mixed-precision training (BF16/FP16 via libraries like accelerate or transformers) and gradient accumulation to effectively increase batch size and stabilize training, especially with limited VRAM. We shall document experiments showing efficiency gains (throughput, memory usage).
   2. Justification: We require initial fine tuning to establish a baseline model. Furthermore, we would to apply multiple training strategies to leverage the existing resources optimally and potentially reduce the time to conduct an experiment.
   3. Guesstimates:
      1. Initial Fine-tuning Time (7B/13B on 50k samples): ~6-48 hours depending heavily on GPU type (e.g., single T4 vs multiple A100s) and using QLoRA.
      2. GPU VRAM Requirement (QLoRA Fine-tuning): 7B might fit on ~16-24GB VRAM. 13B likely needs >=24GB, potentially > 40GB without aggressive optimization/quantization during training. 34B would likely require multi-GPU.
      3. Retraining Frequency: Start with weekly or bi-weekly, adjust based on data influx and performance drift.
4. Model Training Infrastructure and Platform
   1. Strategy: 
      1. Experiment Tracking: Set up an MLFlow server (on a dedicated VM or container) on Chameleon. Log all fine-tuning runs, hyperparameters, evaluation metrics (BERTScore, Precision/Recall on lines), and model artifacts.
      2. Hyperparameter Tuning (Difficulty Point 3)**:** Utilize Ray Tune integrated with MLFlow. Define a search space for: Learning Rate, LoRA r (rank) and alpha, context length/formatting strategy, potentially prompt structure variations.
   2. Justification:
      1. MLFlow: Standardizes tracking, makes experiments comparable, aids debugging, and stores model versions.
      2. Ray: Simplifies the transition from single-node to multi-node training/tuning, essential for larger models. Handles cluster management.
      3. Ray Tune: Automates the tedious and compute-intensive process of finding optimal hyperparameters, leading to better model performance.
5. Model Serving
   1. Strategy:
      1. API Endpoint: Create a RESTful API (e.g., using FastAPI) for the combined inference pipeline (Classifier -> LLM -> Ranker). It will accept PR diff URL/content and project context/guidelines. Return structured JSON with comments, line numbers, severity, etc.
      2. Model Optimization:
         1. *Quantization:* Apply INT8 or potentially INT4 (if quality permits) quantization to the fine-tuned LLM for faster inference and lower memory footprint (using libraries like bitsandbytes, AutoGPTQ)
         2. Attention: Use optimized attention mechanisms if needed (e.g., Flash Attention, if supported by hardware/libraries)
         3. Prompt Optimization: Experiment with prompt structure to minimize token count while maintaining effectiveness.
      3. System Optimization:
         1. *Request Batching:* Implement dynamic batching at the inference server level (e.g., using vLLM, TGI, or Triton Inference Server) to process multiple requests concurrently on the GPU.
   2. Justification:
      1. API: Standard, flexible interface for integration (initially with the bot logic, potentially other tools later).
      2. Model Optimization: Crucial for meeting latency/throughput goals and reducing serving costs/resource needs. Quantization is often the highest impact optimization.
      3. System Optimization: Batching maximizes GPU utilization. Caching can significantly reduce load and latency for common patterns.
   3. Guesstimates:
      1. Target End-to-End Latency (p95)**:** ~1-5 minutes (allowing time for queuing, context fetching).
      2. Target Throughput: Handle 10-20 concurrent requests resulting in inference calls.
      3. Inference Latency (7B QLoRA, Quantized INT8, per PR): ~5-20 seconds on a modern GPU (e.g., A10G/A100) with batching, depending on diff length and context size. Classifier/Ranker add maybe ~0.5-2 seconds total.
      4. Serving GPUs: 1-2 GPUs (e.g., T4, A10G, A100) depending on final model size, quantization, and required throughput. A T4 might struggle with 13B even quantized under concurrent load.
      5. Batch Size: Target 4-16 depending on GPU memory and latency tolerance.
6. Evaluation and Monitoring
   1. Strategy:
      1. Offline Evaluation: Create a diverse held-out test set (~1k-5k PRs). Evaluate using BERTScore for semantic similarity to human comments, Precision/Recall on commented lines, and implement unit tests for known edge cases (e.g., large diffs, specific languages, empty files). Analyze performance differences across major programming languages in the dataset.
      2. Load Testing:** Use a tool (e.g., locust, k6) to simulate 20-50 concurrent webhook events hitting the staging environment. Measure API response times (p50, p95, p99), error rates, and resource utilization (CPU, RAM, GPU) under load.
      3. Online Evaluation:** Implement A/B testing during canary deployments. Compare metrics like comment acceptance rate (via feedback buttons), thumbs up/down reactions, and potentially developer survey feedback between the challenger (new) and control (current production) model versions.
      4. Business Evaluation:** Define metric for "developer time saved" (e.g., estimated time saved per accepted comment category * acceptance rate). Track % accepted vs. rejected/ignored comments.
   2. Justification:
      1. Offline: Ensures model correctness and quality before deployment.
      2. Load Testing: Validates performance and stability under expected load.
      3. Online: Measures real-world impact and effectiveness. A/B testing provides statistically sound comparisons.
      4. Business Eval: Connects technical metrics to business value.
   3. Guesstimates:
      1. Held-out Test Set Size: 1-2 pairs.
      2. Load Test Simulation: 30-60 minutes runs simulating 20-50 users/webhooks.

---


### Model training and training platforms

##### **1. Model Training at Scale (Unit 4)**

##### **1.1 Train and Re-train the Model**
- Fine-tune an open-source, code-specialized **LLM** (StarCoder2 7B or Code Llama 13B (Instruct)**).
- Implement **Parameter-Efficient Fine-Tuning (PEFT)** techniques like **LoRA or QLoRA**.
- Train the model using **instruction-based fine-tuning** to generate review comments based on code diffs.
- The model will be **re-trained periodically** using newly collected GitHub PR data.

##### **1.2. Context Enhancement (Chosen Method: Simple Context Injection)**
- Before sending the PR diff to the LLM, **extract relevant rules and guidelines** from project documentation (e.g., `CONTRIBUTING.md`, `STYLE.md`, `.eslintrc`, `.prettierrc`).
- **Inject this extracted context** into the LLM prompt alongside the diff.
- **Rationale:**
  - Chosen over full code-retrieval RAG or code lineage tracking due to **lower complexity** and better feasibility within a **1-month timeframe**.
  - Provides **project-specific guidance** to the LLM without significant overhead.

##### **1.3 Modeling**
- **Model Choice:** Fine-tune **StarCoder or LLaMA** using **LoRA/qLoRA** to reduce memory requirements.
- **Loss Function:** Cross-entropy loss optimized for sequence generation tasks.
- **Evaluation Metrics:**
  - Semantic similarity (e.g., BERTScore) to check if the meaning aligns.
  - Overlap in commented lines/regions (Precision/Recall/F1).
Qualitative human evaluation on a subset for relevance, correctness, and actionability.


##### **1.4 Training Strategies for Large Models (Optional Difficulty)**
- Strategies:
  - **FSDP (Fully Sharded Data Parallelism):** Efficient distribution of training across multiple GPUs.
  - **ZeRO (Zero Redundancy Optimizer):** Reduces memory footprint.
  - **Mixed Precision Training:** Lowers memory usage while maintaining performance.
- **Experiments and Measurements:**
  - **Training time comparison:** Single GPU vs. Multiple GPUs.
  - **Memory consumption analysis:** LoRA/qLoRA vs. Full fine-tuning.
  - **Effect of batch size and learning rate tuning** on model convergence.
---

##### **2. Model Training Infrastructure and Platform (Unit 5)**

##### **2.1 Experiment Tracking**
- **Tool:** **MLflow hosted on ChameleonCloud**.
- **Logging Key Details:**
  - Hyperparameters: Learning rate, batch size, optimizer type.
  - Training loss, validation loss, and evaluation metrics.
  - Model versioning and performance comparisons.

##### **2.2 Scheduling Training Jobs**
- **Tool:** **Ray Cluster** for job scheduling.
- **Steps:**
  1. Deploy a **Ray cluster** on ChameleonCloud.
  2. Submit training jobs to Ray, allowing for **distributed execution**.
  3. Automate retraining in the **CI/CD pipeline** to ensure periodic model updates.

##### **2.3 Ray Train for Fault-Tolerant Execution (Optional Difficulty)**
- **Implementation:**
  - Use **Ray Train** for checkpointing to remote storage.
  - Enable **automatic recovery** from failures.
  - Implement **adaptive batch sizing** based on available GPU memory.
- **Expected Outcomes:**
  - Reduced downtime in case of hardware failures.
  - More efficient GPU utilization through dynamic workload distribution.

---

### Model Serving and Monitoring Platforms
##### 3. Model Serving (Unit 6)
##### 3.1 Serving from an API Endpoint
- **Component**: Webhook Listener API (FastAPI)  
  - Wraps the model pipeline, receives a GitHub PR event, and routes it through the inference flow  
  - Dockerized and runs continuously to handle live GitHub PRs  

##### 3.2 Specific Inference Requirements (MVP Targets)
- **Use Case**: Automatic review commenting on GitHub PRs  
- **Model Type**: Fine-tuned LLM using QLoRA  
- **Model Size**: ~4.5 GB, enabling fast loading and inference on P100  
- **Latency**: <= few minutes per PR  
- **Throughput**: ~3–5 PRs per minute (with batching/dynamic batching)  
- **Concurrency**: 10-15 PRs  
- **Deployment**: FastAPI + Docker + P100 GPU  
- **Backend Serving**: Triton or Ray Serve for autoscaling  

##### 3.3 Optimizations

- **Model-level**
  - QLoRA – Quantization for efficient serving  
  - Instruction tuning with domain-specific review comments  

- **System-level**
  - Async request handling in FastAPI  
  - Dynamic batching and concurrent model execution  
    - Enabled via multiple GPU-backed model instances and dynamic batching in NVIDIA Triton to support high throughput and low latency inference  
  - Dockerized deployment for portability and reproducibility  

##### 3.4 Optional Difficulty Points

- **Serving Strategy Evaluation**  
  - Compare model serving on server-grade GPU vs server-grade CPU using the same deployment setup. 
  - Evaluate trade-offs in latency, throughput, and cost of deployment using commercial cloud infrastructure (e.g., Chameleon Cloud).

---

### Monitoring & Evaluation
##### 4.1 Offline Evaluation
After model training, we conduct automated offline evaluations:

- **General Metrics**: Overall accuracy (e.g., BERTScore/F1 for text) – logged via MLFlow  
- **Bias & Fairness**: Performance on PR slices (e.g., small vs large PRs, bugfix vs feature PRs)  
- **Known Failure Modes**: PRs with outdated diffs, high token count, or structurally similar examples that previously failed  
- **Template-Based Tests**: Checking LLM predictions for identical PRs with different comment instructions  
- **Test Suite**: Implemented using `pytest`, and integrated into the training pipeline  
  - Based on results, models are automatically registered or discarded  
- **Automated Model Registration**: Pass/fail logic in test suite decides model promotion  

##### 4.2 Load Testing
- Conducted in a staging environment using simulated GitHub PR events  
- Operational metrics measured via Prometheus:  
  - Latency, throughput, and error rates 
- Observed under varied load patterns to validate FastAPI + Triton under concurrent requests  
- Results visualized via Grafana dashboards  

##### 4.3 Online Evaluation via Canary
- Online evaluation with artificial users (team members)  
- Real-time observation of model responses on live PRs  
- Monitoring model-specific metrics such as prediction confidence using Prometheus and Grafana  

##### 4.4 Close the Loop
- No user feedback is collected  
- Monitoring drift using:  
  - Drift events (`drift_events_total`)  
  - Test statistic (`drift_test_stat`)  
- Infrastructure and application-level health tracked through Prometheus + Grafana  


##### 4.5 Define a Business-Specific Evaluation

- **Location Awareness**  
  - *Precision*: Of all the locations where the model generated comments, what fraction matched actual human comment locations in the test set?  
  - *Recall*: Of all the human-generated comment locations in the test set, what fraction were identified by the model?  

- **Semantic Similarity**  
  - *BERTScore*: Measures the similarity between model-generated comments and human comments using contextual embeddings from BERT. This captures semantic alignment beyond surface-level token overlap.  

These metrics serve as proxies for usefulness and alignment with human reviewers and will be computed on a held-out labeled test set.

---
### Continuous X
##### 5. DevOps (Unit 3)
##### 5.1 Infrastructure-as-Code (IaC)
To provision and manage infrastructure declaratively:

- **Terraform**: Define cloud infrastructure (compute, storage, networking).
- **Ansible**: Automate service configuration and deployment.
- **Kubernetes (K8s) with ArgoCD**: GitOps-based deployment.
- **Version Control**: Store all Terraform, Ansible, and Kubernetes configurations in a Git repository.

IaC Implementation Steps:
- Define Terraform modules for:
  - **Compute**: GPU/CPU nodes for model training & inference.
  - **Storage**: Persistent storage for datasets and models.
  - **Networking**: API Gateway, Load Balancer, VPC.
- Automate provisioning using Terraform (`terraform apply` from GitHub Actions).
- Use Ansible to install dependencies (e.g., MLFlow, FastAPI).
- ArgoCD watches Git repositories and auto-deploys services on Kubernetes.

---
##### 5.2 Cloud-Native Architecture

To follow cloud-native principles of:
- (a) Microservices
- (b) Containers as the Smallest Compute Unit
- (c) Immutable Infrastructure

**Cloud-Native Components**
**GitHub API Scraper (Data Pipeline)**
- Runs as a **Dockerized job** within Apache Airflow.
- Stores scraped data in persistent storage (e.g., **MinIO**).

**Model Training and Experimentation**
- Uses **MLFlow** for hyperparameter tuning and tracking.
- Runs **PEFT-based fine-tuning** on GPUs.
- Saves models to object storage.

**Inference & Model Serving**
- Uses **FastAPI** as an inference endpoint.
- Runs the fine-tuned **LLM (TGI)** in a containerized GPU pod.

**Automated ML Pipeline**
- Triggers training based on **new data** or **schedule**.
- Automates **hyperparameter tuning and evaluation**.

---
##### 5.3 CI/CD & Continuous Training

To automate model updates and deployments:

- **GitHub Actions**
- **Argo Workflows**
- **Kubernetes Jobs**

##### **Trigger Points**
- **New PR in GitHub** → Triggers inference pipeline.
- **New dataset** → Triggers re-training.
- **Scheduled Training** → Runs model updates periodically.

##### **CI/CD Pipeline**
- Lints Python code, Dockerfiles, Terraform.
- Runs unit tests (**pytest**) on ML components.
- Builds Docker images (`docker build`).
- Detects data changes.
- Runs training and evaluation in **Argo Workflows**.
- Stores the model in **MLFlow**.
- Packages model inside a **TGI container**.
- Deploys to **staging** using ArgoCD.

---
##### 5.4 Staged Deployment (Staging, Canary, Production)

To safely release model updates:

##### **Deployment Strategy**
- **Staging**: Runs in a separate namespace.
- **Canary Deployment**: Gradually increases traffic to the new model.
- **Production Rollout**: Fully replaces the old model if canary tests pass.

##### **Staged Deployment Steps**
- ArgoCD auto-deploys new model versions.
- Check **API responses, latency, accuracy**.
- Split traffic **90/10 (old/new)**.
- Use **Prometheus & Grafana** for observability.
- Roll out to 100% if no issues are detected.


### Data Pipeline

**1. Persistent Storage:**

* Provision persistent block and object storage on Chameleon for non-Git artifacts (datasets, models, logs, checkpoints, MLFlow data). Volumes attach to VMs as needed.
* **Justification:** Ensures data durability and separates large artifacts from Git. Meets the Chameleon requirement.
* **Guesstimate:** 100 GB initial capacity.

**2. Offline Data:**

* Curate 10k-50k GitHub PR diff/comment pairs (permissive licenses) for training/evaluation. Store processed data (JSONL/Parquet) in Chameleon Object Storage, versioned using DVC integrated with Git.
* **Justification:** Provides quality training data; versioning ensures reproducibility.

**3. Data Pipelines (ETL):**

* Automated Python pipeline using GitHub API to:
  1. **Extract:** Fetch PRs, diffs, comments, file context from target repos.
  2. **Transform:** Align comments to code lines, extract context, clean/filter data, structure into JSONL/Parquet.
  3. **Load:** Store processed data in Object Storage, track versions with DVC.
* **Implementation:** Scheduled scripts (e.g., daily/weekly cron).
* **Justification:** Automates data acquisition and preparation, ensuring consistency for training.

**4. Online Data & Simulation:**

* **Online Pipeline:** GitHub webhooks -> Webhook Listener service -> Queue (Redis/RabbitMQ) -> Worker service (fetches context, preprocesses) -> Inference services.
* **Simulation:** Python script reads offline data, generates realistic GitHub webhook JSON payloads, and sends them (at configurable rates/bursts simulating 10-20 concurrent PRs) to the staging environment's Webhook Listener endpoint.
* **Justification:** Asynchronous pipeline handles live requests efficiently. Simulation enables robust development, testing (including load testing), and debugging without live traffic dependency.

##### **Staged Deployment Steps**
   - ArgoCD auto-deploys new model versions.
   - Check **API responses, latency, accuracy**.
   - Split traffic **90/10 (old/new)**.
   - Use **Prometheus & Grafana** for observability.
   - Roll out to 100% if no issues are detected.


