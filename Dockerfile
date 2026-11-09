FROM python:3.10-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
# The worker is the main process; deploy a separate web service for independent scaling.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} app:app & exec python3 main.py"]
