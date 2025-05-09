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

# Copy necessary data files and directories for the dashboard
# The paths in app.py are relative to SCRIPT_DIR (which will be /app/dashboard),
# so these target paths inside the container need to align with those relative lookups (e.g., ../dataset/v1).
COPY ./new_prs_to_process.txt /app/new_prs_to_process.txt
COPY ./dataset/v1 /app/dataset/v1
COPY ./bronze /app/bronze

# Expose Streamlit's default port
EXPOSE 8501

# Command to run the Streamlit application
# --server.enableCORS=false can be useful if you face CORS issues, though often not needed.
# Using 0.0.0.0 makes the server accessible externally from the container.
CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"] 