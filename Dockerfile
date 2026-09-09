# Veriq Docker image: deterministic tools + Playwright + agent code.
# Human-in-the-loop boundary: this image never deploys, never touches prod.
FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /veriq

# System deps for scanners (gitleaks, trivy installed at runtime in workflow for version pinning)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git jq curl nodejs npm \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir -r requirements.txt \
 && curl -sSfL https://github.com/gitleaks/gitleaks/releases/download/v8.18.4/gitleaks_8.18.4_linux_x64.tar.gz \
    | tar -xz -C /usr/local/bin gitleaks \
 && python -m playwright install --with-deps chromium

COPY . /veriq

ENTRYPOINT ["python", "-m", "agent.orchestrator"]
