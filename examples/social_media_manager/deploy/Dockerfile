# 24/7 social media manager — container image.
#
# Build from the OpenJarvis repo root so the package can be installed:
#   docker build -f examples/social_media_manager/deploy/Dockerfile -t social-agent .
#
# The image installs OpenJarvis (for content generation) plus the
# example's light deps. Use a CLOUD engine at runtime so no GPU is
# needed (set AGENT_ARGS="--engine cloud" and the relevant API key).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps kept minimal; httpx/click are pure-Python wheels.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install the package (base extras only — cloud engine needs no GPU libs).
RUN pip install --upgrade pip && pip install .

# The example code.
COPY examples/social_media_manager ./agent

# Persisted state (SQLite DB) lives on a mounted volume.
ENV SMM_DB=/data/social_media_manager.db
VOLUME ["/data"]

WORKDIR /app/agent

# Default: run the always-on scheduler. Override CMD to run one-off
# commands, e.g.  docker run ... python social_media_manager.py status
CMD ["python", "deploy/scheduler_loop.py"]
