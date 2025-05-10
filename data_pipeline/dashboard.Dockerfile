# Base Python image
FROM python:3.9-slim

# Set working directory inside the container
WORKDIR /app

# Copy the requirements file into the working directory
COPY ./requirements.txt /app/requirements.txt

# Install Python dependencies
# --no-cache-dir reduces image size
# --trusted-host pypi.python.org -U pypi.org can sometimes help with network issues in CIs or restricted environments
RUN pip install --no-cache-dir --trusted-host pypi.python.org -r requirements.txt

# Copy the dashboard application code
# This assumes your dashboard code is in a 'dashboard' subdirectory relative to the Dockerfile
COPY ./dashboard /app/dashboard

# Copy the PRs log file. This is still copied into the image.
# app.py expects this at /app/new_prs_to_process.txt based on SCRIPT_DIR + ../new_prs_to_process.txt
COPY ./new_prs_to_process.txt /app/new_prs_to_process.txt

# Expose the new port for Streamlit
EXPOSE 8888

# Command to run the Streamlit application on the new port
# --server.enableCORS=false can be useful if you face CORS issues, though often not needed.
# Using 0.0.0.0 makes the server accessible externally from the container.
CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8888", "--server.address=0.0.0.0"] 