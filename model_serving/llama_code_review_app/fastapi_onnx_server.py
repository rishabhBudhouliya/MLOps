from fastapi import FastAPI
from pydantic import BaseModel
from transformers import AutoTokenizer
import onnxruntime as ort
import numpy as np
import time

app = FastAPI(title="Code Review Comments Generator")

# Define paths
model_path = "/mnt/object/llama_onnx/codellama_7b_gpu.onnx"
tokenizer_path = "/mnt/object/llama_onnx"

# Globals
ort_session = None
tokenizer = None

# Input schema
class CodeDiffRequest(BaseModel):
    code_diff: str

@app.on_event("startup")
def load_model():
    global ort_session, tokenizer
    ort_session = ort.InferenceSession(model_path, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

@app.get("/")
def root():
    return {"message": "ONNX Review Comment Generator is running"}

@app.post("/predict")
async def predict(request: CodeDiffRequest):
    try:
        code_diff = request.code_diff
        prompt = (
            "You are a helpful code reviewer. "
            "Given a code diff, generate all relevant review comments. "
            "Each comment must be in the format: "
            "<COMMENT side=\"RIGHT\" offset=\"X\">Your comment here.\n"
            "Only output comments, nothing else.\n\n"
            "### Code Diff:\n"
            f"{code_diff}"
        )

        inputs = tokenizer(prompt, return_tensors="np", padding="max_length", truncation=True, max_length=512)
        input_ids = inputs["input_ids"]
        attention_mask = inputs["attention_mask"]

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

