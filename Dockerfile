FROM python:3.11-slim

# Install system utilities needed for the database
RUN apt-get update && apt-get install -y sqlite3 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Stop Python from buffering log outputs to ensure real time terminal views
ENV PYTHONUNBUFFERED=1

# Copy the requirements file and install dependencies
COPY app/requirements.txt ./app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt

# Copy the remaining execution assets
COPY app/ ./app/

# No CMD line any more!
# The command is now managed entirely by docker-compose.yml.
