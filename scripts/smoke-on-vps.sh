#!/usr/bin/env bash
# VPS smoke test: run the exact caller audit command with the ghcr image.
set -euo pipefail
FIXTURE="$HOME/veriq-smoke/target"
IMAGE=ghcr.io/eiden-group/veriq:latest
docker pull -q "$IMAGE"
docker run --rm \
  -v "$FIXTURE:/workspace" -w /workspace \
  -e PYTHONPATH=/veriq \
  -e GITHUB_REPOSITORY=EIDEN-GROUP/smoketest \
  -e GITHUB_REF_NAME=main \
  -e GITHUB_SHA=smoke001 \
  -e GITHUB_ACTOR=anynonenom \
  -e AI_AGENT_ENABLE_REPAIR=false \
  --entrypoint bash "$IMAGE" -lc \
  "python -m agent.orchestrator --target . --artifacts artifacts; echo RC=\$?; ls -la artifacts; echo '--- audit.json head ---'; head -c 700 artifacts/audit.json"
