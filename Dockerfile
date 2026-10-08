FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_FILE=/data/config.json

# Install runtime dependencies.
COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy the agent runtime as a single immutable image layer.
COPY bot.py agent_client.py chat_relay.py wings_console.py i18n.py ./
COPY adapters ./adapters

# Keep legacy-mode state on a persistent volume.
RUN mkdir -p /data && chmod 777 /data

HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "from pathlib import Path; import sys,time; p=Path('/tmp/ptero-bot-health'); sys.exit(0 if p.exists() and time.time()-p.stat().st_mtime < 90 else 1)"

STOPSIGNAL SIGTERM

CMD ["python", "bot.py"]
