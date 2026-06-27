# Single image for the whole autoreach pipeline. The same image runs the
# orchestrator and every agent — the container command (see run.py / compose)
# selects what actually executes.
FROM python:3.14-slim

# - PYTHONUNBUFFERED: logs stream out immediately (don't sit in a buffer)
# - PYTHONDONTWRITEBYTECODE: no .pyc clutter in the image/volumes
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app

WORKDIR /app

# Install deps first so this layer is cached across code-only changes.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# Then the application code.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app \
    && chown -R app:app /app
USER app

# Default to the orchestrator; compose overrides this per service.
ENTRYPOINT ["python", "run.py"]
CMD ["orchestrator"]
