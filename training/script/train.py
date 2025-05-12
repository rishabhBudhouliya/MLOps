import os
import time

print("Training model...")
time.sleep(3)

# Simulate model saving
os.makedirs("/models", exist_ok=True)
with open("/models/version.txt", "w") as f:
    f.write("1")

print("Training complete. Model version 1 saved.")
