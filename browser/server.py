"""Local app server lifecycle: install -> build -> serve on ephemeral port, with timeouts."""
from __future__ import annotations

import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def prepare_frontend(root: Path, package_manager: str = "npm", build_cmd: str = "",
                     timeout_s: int = 600) -> dict:
    """Install deps + build so start_app serves the real production bundle.

    Tries locked install first (npm ci / pnpm --frozen-lockfile), falls back to
    plain install. Never raises; every step reports exit codes for the audit.
    """
    def run(cmd: str) -> dict:
        try:
            p = subprocess.run(cmd, shell=True, cwd=root, capture_output=True,
                               text=True, timeout=timeout_s)
            return {"command": cmd, "exit": p.returncode,
                    "output": (p.stdout + p.stderr)[-2000:]}
        except subprocess.TimeoutExpired:
            return {"command": cmd, "exit": 124, "output": "timeout"}
        except Exception as e:  # npm/pnpm missing etc. -> report, never raise
            return {"command": cmd, "exit": 127, "output": f"unavailable: {e}"}

    installers = {"npm": ["npm ci --no-audit --no-fund", "npm install --no-audit --no-fund"],
                  "pnpm": ["pnpm install --frozen-lockfile", "pnpm install"],
                  "yarn": ["yarn install --frozen-lockfile", "yarn install"],
                  "bun": ["bun install --frozen-lockfile", "bun install"]}
    steps: list[dict] = []
    for cmd in installers.get(package_manager, installers["npm"]):
        step = run(cmd)
        steps.append(step)
        if step["exit"] == 0:
            break
    build = run("npm run build --silent") if build_cmd else {"command": "", "exit": 0, "output": "no build declared"}
    if build_cmd:
        steps.append(build)
    installed = any(s["exit"] == 0 for s in steps[:2])
    return {"installed": installed, "build_exit": build["exit"], "steps": steps}


@dataclass
class AppServer:
    process: subprocess.Popen | None = None
    url: str = ""
    logs: str = ""

    def stop(self) -> None:
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()


def start_app(root: Path, start_cmd: str, port: int = 0, timeout_s: int = 90) -> AppServer:
    """Start `start_cmd --port <free>`; wait for TCP accept. Never raises; reports status."""
    srv = AppServer()
    if not start_cmd:
        return srv
    port = port or _free_port()
    env_port = {"PORT": str(port)}
    import os
    env = {**os.environ, **env_port}
    cmd = f"{start_cmd} -- --port {port} --hostname 127.0.0.1" if start_cmd.startswith("next") else start_cmd
    try:
        proc = subprocess.Popen(cmd, shell=True, cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        srv.process = proc
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if proc.poll() is not None:
                srv.logs = (proc.stdout.read() or "")[-3000:] if proc.stdout else "exited early"
                srv.process = None
                return srv
            with socket.socket() as s:
                s.settimeout(1)
                try:
                    s.connect(("127.0.0.1", port))
                    srv.url = f"http://127.0.0.1:{port}"
                    return srv
                except OSError:
                    time.sleep(1)
        srv.logs = "startup timeout"
        srv.stop()
        srv.url = ""
        return srv
    except Exception as e:
        srv.logs = f"start error: {e}"
        return srv
