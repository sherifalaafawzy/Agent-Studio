# Agent Studio sandbox: where agent shell commands and linters run (never on the host).
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ripgrep nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir ruff pytest \
    && useradd -m -u 1000 agent
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]
