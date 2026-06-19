FROM python:3.11-slim

# Keep Python lean and unbuffered for clean container logs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first so Docker can cache this layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source.
COPY . .

EXPOSE 8000

# Single worker: background collectors keep state in-process and must not run twice.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
