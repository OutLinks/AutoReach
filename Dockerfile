FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY . .
RUN python -c "from orchestrator.adapters.live import _load_agent; [_load_agent(dirname, alias) for dirname, alias in [('agent1-lead-finder', 'agent1_lead_finder'), ('agent2-research-analyst', 'agent2_research_analyst'), ('agent3-email-writer', 'agent3_email_writer'), ('agent4-sender', 'agent4_sender'), ('agent5-reply-handler', 'agent5_reply_handler')]]"
RUN mkdir -p /data

EXPOSE 8000

CMD ["sh", "/app/scripts/start-api.sh"]
