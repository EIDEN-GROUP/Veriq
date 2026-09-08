# Gateway-only image (slim): Slack approval callbacks + Action polling. No scanners.
FROM python:3.12-slim

WORKDIR /veriq
COPY requirements-gateway.txt .
RUN pip install --no-cache-dir -r requirements-gateway.txt
COPY gateway/ gateway/

EXPOSE 8080
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8080"]
