# Content Agents (blog + YouTube writers). The LLM runs in the separate `ollama` service (see docker-compose.yml).
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data \
    TZ=Asia/Kolkata

# graphviz: local diagrams; fonts: diagram labels; tzdata: schedules run in local time (TZ).
RUN apt-get update \
 && apt-get install -y --no-install-recommends graphviz fonts-dejavu-core tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY content_agents ./content_agents

RUN useradd --create-home --uid 1000 agent \
 && mkdir -p /app/data \
 && chown -R agent:agent /app
USER agent

VOLUME ["/app/data"]
ENTRYPOINT ["python", "-m", "content_agents"]
CMD ["serve"]
