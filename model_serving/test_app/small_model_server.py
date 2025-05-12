from fastapi import FastAPI
from pydantic import BaseModel
import time
import os
from transformers import pipeline, AutoTokenizer
import torch

app = FastAPI(title="Code Review Comments Generator")

# Define version file path
VERSION_FILE = "/app/version.txt"

# Global model variables
classifier = None

# Input schema
class CodeDiffRequest(BaseModel):
    code_diff: str

@app.on_event("startup")
def load_model():
    global classifier
    print("Loading small pretrained model...")
    
    # Load a very small sentiment model as an example
    # This uses less than 500MB RAM and is quick to load
    model_name = "distilbert-base-uncased-finetuned-sst-2-english"
    classifier = pipeline("sentiment-analysis", model=model_name, 
                          device=-1)  # Force CPU
    
    print(f"Model {model_name} loaded successfully")

@app.get("/")
def root():
    return {"message": "Review Comment Generator is running"}

@app.get("/version")
def version():
    try:
        # Read model version from file
        if os.path.exists(VERSION_FILE):
            with open(VERSION_FILE, 'r') as f:
                model_version = f.read().strip()
            return {"model_version": model_version}
        else:
            return {"model_version": "unknown"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/predict")
async def predict(request: CodeDiffRequest):
    try:
        # Get the code diff
        code_diff = request.code_diff
        start_time = time.time()
        
        # Use our small model to analyze sentiment of the code diff
        # This is a proxy for code quality in our example
        result = classifier(code_diff[:512])  # Truncate to avoid token limit issues
        sentiment = result[0]["label"]
        score = result[0]["score"]
        
        # Generate a review comment based on sentiment
        if sentiment == "POSITIVE":
            output = f"<COMMENT side=\"RIGHT\" offset=\"1\">Code looks good! Clean implementation. (confidence: {score:.2f})</COMMENT>"
        else:
            output = f"<COMMENT side=\"RIGHT\" offset=\"1\">Consider refactoring this code for better readability. (confidence: {score:.2f})</COMMENT>"
            
            # Add more specific comments based on code content
            if "if" in code_diff.lower():
                output += "\n<COMMENT side=\"RIGHT\" offset=\"5\">Check conditional logic here.</COMMENT>"
            if "for" in code_diff.lower() or "while" in code_diff.lower():
                output += "\n<COMMENT side=\"RIGHT\" offset=\"10\">Verify loop termination conditions.</COMMENT>"
        
        latency = time.time() - start_time
        
        return {
            "output": output,
            "latency_ms": round(latency * 1000, 2)
        }
    except Exception as e:
        return {"error": str(e)}