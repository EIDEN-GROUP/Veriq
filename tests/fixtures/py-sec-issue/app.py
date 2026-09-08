"""PySec fixture: contains deterministic findings the scanner must flag (no real creds)."""
import os
import pickle
import subprocess

API_KEY = "sk-1234567890abcdef-fixture-NOT-REAL"  # noqa: S105 - intentional fixture


def run_user_cmd(user_input: str) -> None:
    os.system("ls " + user_input)  # noqa: S605 - intentional command-injection fixture
    subprocess.run(f"echo {user_input}", shell=True)  # noqa: S602 - intentional fixture


def load_blob(blob: bytes):
    return pickle.loads(blob)  # noqa: S301 - intentional insecure-deser fixture
