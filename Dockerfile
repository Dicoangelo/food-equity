FROM us-central1-docker.pkg.dev/database-toolbox/toolbox/toolbox:1.14.0 AS toolbox
FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    GOOGLE_CLOUD_LOCATION=us-east1 GOOGLE_GENAI_USE_VERTEXAI=true \
    TOOLBOX_URL=http://127.0.0.1:5000
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=toolbox /toolbox /usr/local/bin/toolbox
COPY agent ./agent
COPY scripts/serve.py ./scripts/serve.py
RUN useradd --system --uid 10001 appuser
USER appuser
EXPOSE 8080
CMD ["python", "scripts/serve.py"]
