from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.post("/predict")
def predict():
    return {"result": "This is a dummy prediction"}
