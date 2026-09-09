# Veriq Docker image: deterministic tools + Playwright + agent code.
# Also published as ghcr.io/eiden-group/veriq:latest (docker-publish.yml);
# the reusable workflow runs the audit from this image.
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /veriq

RUN apt-get update && apt-get install -y --no-install-recommends \
    git jq curl ca-certificates \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz \
    | tar -xz -C /usr/local/bin gitleaks \
 && python -m playwright install --with-deps chromium

COPY . /veriq

ENTRYPOINT ["python", "-m", "agent.orchestrator"]
