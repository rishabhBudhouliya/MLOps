from fastapi import FastAPI, UploadFile, File
from transformers import AutoTokenizer, AutoModelForCausalLM
import torch

from utils import generate_prompt

app = FastAPI()
# dummy model
MODEL_NAME = "distilgpt2" 
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForCausalLM.from_pretrained(MODEL_NAME)

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
model.to(device)
model.eval()


@app.post("/generate-review")
async def generate_review(
    diff_file: UploadFile = File(...),
    guidelines_file: UploadFile = File(...),
    max_new_tokens: int = 300
    ):
    diff = (await diff_file.read()).decode("utf-8")
    guidelines = (await guidelines_file.read()).decode("utf-8")

    prompt = generate_prompt(diff, guidelines)
    
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens = max_new_tokens)
    
    full_output = tokenizer.decode(output[0], skip_special_tokens=True)
    comments_only = full_output.replace(prompt, "").strip()

    return {"comments": comments_only}