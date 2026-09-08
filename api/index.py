"""Vercel serverless entrypoint: `vercel.json` routes every path here; FastAPI serves it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.app import app  # noqa: F401  (Vercel looks for `app`)
