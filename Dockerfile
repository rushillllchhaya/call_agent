FROM python:3.12-slim

WORKDIR /app

# System deps: curl for healthcheck, sqlite3
RUN apt-get update && apt-get install -y \
    build-essential \
    sqlite3 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Create DB files if they don't exist (avoids volume mount errors)
RUN touch conversations.db conversations_graph.db

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
