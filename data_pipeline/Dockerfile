# Use an official Python runtime as a parent image
FROM python:3.10-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Install system dependencies: curl and unzip are needed
# Add any other system dependencies your scripts might need
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    unzip \
    # Add 'bash' if somehow missing from python:slim (unlikely)
    && rm -rf /var/lib/apt/lists/*

# Install rclone using the official install script
# Note: Running as root within the Dockerfile, so sudo is not needed/available
RUN curl https://rclone.org/install.sh | bash && \
    # Verify installation
    rclone --version

# Set the working directory in the container
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
# Use .dockerignore to exclude unnecessary files/dirs
COPY . /app/

# Note: config.yaml and rclone.conf will be mounted via docker-compose.
# The entrypoint/command will be specified in docker-compose.yml
# We assume rclone is available in the execution environment (e.g., host mounted, or base image) 