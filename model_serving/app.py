from fastapi import FastAPI, UploadFile, File, Request
from fastapi.responses import PlainTextResponse
from prometheus_client import Counter, generate_latest, start_http_server
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch
import threading

# Prometheus metrics server (on 8001 for Prometheus scraper)
threading.Thread(target=start_http_server, args=(8001,), daemon=True).start()

# Prometheus counters
inference_counter = Counter("inference_requests_total", "Total inference requests")
inference_errors = Counter("inference_errors_total", "Total number of failed inference requests")

# FastAPI app
app = FastAPI()

# Load model and tokenizer
tokenizer = AutoTokenizer.from_pretrained("deepseek-ai/deepseek-coder-6.7b-base")
model = AutoModelForCausalLM.from_pretrained("deepseek-ai/deepseek-coder-6.7b-base")
model.eval()
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)

@app.get("/")
def root():
    return {"message": "Inference API running"}

@app.get("/metrics")
def metrics():
    return PlainTextResponse(generate_latest(), media_type="text/plain")

@app.post("/predict")
@inference_request_latency_seconds.time()
async def predict(code_file: UploadFile = File(...), guideline_file: UploadFile = File(...)):
    try:
        inference_counter.inc()
        code = (await code_file.read()).decode("utf-8")
        guideline = (await guideline_file.read()).decode("utf-8")

        prompt = f"Code:\n{code}\n\nGuidelines:\n{guideline}\n\n# Add comments to the code following the above guidelines."
        inputs = tokenizer(prompt, return_tensors="pt").to(device)

        with torch.no_grad():
            outputs = model.generate(
                **inputs,
                max_new_tokens=256,
                do_sample=False,
                temperature=0.7
            )
        response = tokenizer.decode(outputs[0], skip_special_tokens=True)
        return {"output": response}
    except Exception as e:
        inference_errors.inc()
        return {"error": str(e)}
