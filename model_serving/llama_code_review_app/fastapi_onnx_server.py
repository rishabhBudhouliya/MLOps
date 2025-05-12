from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter
from typing import Optional
import onnxruntime as ort
import numpy as np
import time
from transformers import AutoTokenizer

app = FastAPI(title="Code Review Comments Generator")

# Metrics setup
feedback_counter = Counter(
    'feedback_events_total',
    'Count of feedback events by case type',
    ['case_type']
)

# Model setup
model_path = "/mnt/object/llama_onnx/codellama_7b_gpu.onnx"
tokenizer_path = "/mnt/object/llama_onnx"

ort_session = ort.InferenceSession(model_path, providers=["CUDAExecutionProvider"])
tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
if tokenizer.pad_token is None:
    tokenizer.pad_token = tokenizer.eos_token

# Request Models
class CodeDiffRequest(BaseModel):
    code_diff: str

class FeedbackRequest(BaseModel):
    original_prompt: str
    model_output: str
    user_feedback: Optional[str] = None  # If modified or written from scratch
    case_type: int  # 1: accepted, 2: modified, 3: written from scratch

@app.get("/")
def root():
    return {"message": "ONNX Review Comment Generator is running"}

@app.post("/predict")
async def predict(request: CodeDiffRequest):
    try:
        prompt = (
            "You are a helpful code reviewer. "
            "Given a code diff, generate all relevant review comments. "
            "Each comment must be in the format: "
            "<COMMENT side=\"RIGHT\" offset=\"X\">Your comment here.\n"
            "Only output comments, nothing else.\n\n"
            "### Code Diff:\n"
            f"{request.code_diff}"
        )

        encoded = tokenizer(prompt, return_tensors="np", padding="max_length", truncation=True, max_length=512)
        input_ids = encoded["input_ids"]
        attention_mask = encoded["attention_mask"]

        start_time = time.time()
        outputs = ort_session.run(["logits"], {"input_ids": input_ids, "attention_mask": attention_mask})[0]
        latency = time.time() - start_time

        pred_ids = np.argmax(outputs, axis=-1)
        decoded_output = tokenizer.decode(pred_ids[0], skip_special_tokens=True)

        return {
            "output": decoded_output.strip(),
            "latency_ms": round(latency * 1000, 2)
        }

    except Exception as e:
        return {"error": str(e)}

@app.post("/feedback")
async def log_feedback(request: FeedbackRequest):
    try:
        feedback_counter.labels(case_type=str(request.case_type)).inc()
        return {"message": "Feedback logged"}
    except Exception as e:
        return {"error": str(e)}

# Prometheus metrics instrumentation
Instrumentator().instrument(app).expose(app)
