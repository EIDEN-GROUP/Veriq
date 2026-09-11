# Gateway-only image: Slack approval callbacks + 👾 chat (/commands, DMs, mentions).
FROM python:3.12-slim

WORKDIR /veriq
COPY requirements-gateway.txt .
RUN pip install --no-cache-dir -r requirements-gateway.txt
# gateway/chat imports the shared redaction layer (+ stdlib llm client); no CI deps.
COPY agent/redact.py agent/__init__.py ./agent/
COPY gateway/ gateway/

EXPOSE 8080
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
