"""Tool registry: the ONLY surface the NIM agent may call. Every call passes
permissions + redaction. run_command is sandboxed to non-privileged commands."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agent.permissions import Policy
from agent.redact import redact_text, should_exclude_path

_EXTRA_DROP = ("GITHUB_TOKEN", "GH_TOKEN", "NPM_TOKEN", "AWS_", "AZURE_",
               "GOOGLE_APPLICATION", "DATABASE_URL", "REDIS_URL", "UPSTASH_",
               "SLACK_", "NIM_", "AI_AGENT_API", "VERIQ_TOKEN")


@dataclass
class ToolResult:
    ok: bool
    output: str
    truncated: bool = False


@dataclass
class ToolRegistry:
    root: Path
    policy: Policy
    log: list[dict] = field(default_factory=list)

    @staticmethod
    def _child_env() -> dict:
        """Scrubbed environment for LLM-runnable commands: tools work, secrets do not leak.

        Keeps PATH/HOME/etc; drops anything credential-like plus explicit denylist.
        """
        import os
        drop = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTH",
                "PRIVATE", "SESSION", "ACCESS")
        out = {}
        for k, v in os.environ.items():
            ku = k.upper()
            if any(d in ku for d in drop) or ku.startswith(_EXTRA_DROP):
                continue
            out[k] = v
        return out

    def _record(self, tool: str, args: dict, result: ToolResult) -> ToolResult:
        self.log.append({"tool": tool, "args": args, "ok": result.ok})
        return result

    def dispatch(self, tool: str, args: dict) -> ToolResult:
        if not self.policy.is_tool_allowed(tool):
            return self._record(tool, args, ToolResult(False, f"Tool denied by policy: {tool}"))
        handler = getattr(self, f"_t_{tool}", None)
        if handler is None:
            return self._record(tool, args, ToolResult(False, f"Unknown tool: {tool}"))
        try:
            return self._record(tool, args, handler(args))
        except Exception as exc:  # fail safely, never leak internals raw
            return self._record(tool, args, ToolResult(False, f"Tool error: {type(exc).__name__}"))

    # ---- read-only tools ----
    def _t_list_files(self, a: dict) -> ToolResult:
        rel = a.get("path", ".")
        target = (self.root / rel).resolve()
        if self.root.resolve() not in target.parents and target != self.root.resolve():
            return ToolResult(False, "Path escapes workspace")
        files = [str(p.relative_to(self.root)) for p in target.rglob("*")
                 if p.is_file() and not should_exclude_path(str(p))][:500]
        return ToolResult(True, "\n".join(files))

    def _t_read_file(self, a: dict) -> ToolResult:
        rel = str(a.get("path", ""))
        if should_exclude_path(rel) or not self.policy.is_path_allowed(rel):
            return ToolResult(False, "File blocked by policy")
        target = (self.root / rel).resolve()
        if self.root.resolve() not in target.parents:
            return ToolResult(False, "Path escapes workspace")
        if not target.exists():
            return ToolResult(False, "File not found")
        text = target.read_text(errors="replace")[:20000]
        return ToolResult(True, redact_text(text))

    def _t_search_code(self, a: dict) -> ToolResult:
        import re
        pattern, glob = str(a.get("pattern", "")), str(a.get("glob", "*"))
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return ToolResult(False, f"Bad regex: {e}")
        hits: list[str] = []
        for p in self.root.rglob(glob if glob != "*" else "*"):
            if not p.is_file() or should_exclude_path(str(p)) or p.stat().st_size > 300_000:
                continue
            try:
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{p.relative_to(self.root)}:{i}:{redact_text(line.strip()[:300])}")
                        if len(hits) >= 100:
                            return ToolResult(True, "\n".join(hits), truncated=True)
            except OSError:
                continue
        return ToolResult(True, "\n".join(hits))

    def _t_git_diff(self, a: dict) -> ToolResult:
        return self._git(["diff", "--", "."])

    def _t_git_diff_after_patch(self, a: dict) -> ToolResult:
        return self._git(["diff", "--stat", "--", "."])

    # ---- execution tools (guarded) ----
    def _t_run_command(self, a: dict) -> ToolResult:
        cmd = str(a.get("command", ""))
        if not self.policy.is_command_allowed(cmd):
            return ToolResult(False, "Command denied by policy (privileged/destructive)")
        timeout = int(a.get("timeout_s", 120))
        try:
            proc = subprocess.run(cmd, shell=True, cwd=self.root, capture_output=True,
                                  text=True, timeout=timeout, env=self._child_env())
            out = redact_text((proc.stdout + "\n" + proc.stderr)[-8000:])
            return ToolResult(proc.returncode == 0, f"exit={proc.returncode}\n{out}")
        except subprocess.TimeoutExpired:
            return ToolResult(False, f"Command timed out after {timeout}s")

    def _git(self, args: list[str]) -> ToolResult:
        try:
            proc = subprocess.run(["git"] + args, cwd=self.root, capture_output=True,
                                  text=True, timeout=30)
            return ToolResult(proc.returncode == 0, redact_text((proc.stdout + proc.stderr)[-8000:]))
        except Exception as exc:
            return ToolResult(False, f"git error: {exc}")

    # Aliases routed through run_command by orchestrator; kept explicit for schema clarity.
    def _t_run_tests(self, a: dict) -> ToolResult:
        return self._t_run_command({"command": a.get("command", "echo no-test-command"), **a})

    def _t_run_linter(self, a: dict) -> ToolResult:
        return self._t_run_command({"command": a.get("command", "echo no-lint-command"), **a})

    def _t_run_typecheck(self, a: dict) -> ToolResult:
        return self._t_run_command({"command": a.get("command", "echo no-typecheck"), **a})

    def _t_run_security_scan(self, a: dict) -> ToolResult:
        return self._t_run_command({"command": a.get("command", "echo no-scan"), **a})

    # Browser/app tools are fulfilled by browser/ modules via orchestrator; registry acknowledges routing.
    def _t_start_application(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:start_application")

    def _t_open_browser(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:open_browser")

    def _t_take_screenshot(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:take_screenshot")

    def _t_inspect_dom(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:inspect_dom")

    def _t_inspect_console(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:inspect_console")

    def _t_inspect_network(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:inspect_network")

    def _t_apply_patch(self, a: dict) -> ToolResult:
        return ToolResult(True, "ROUTED:apply_patch")
