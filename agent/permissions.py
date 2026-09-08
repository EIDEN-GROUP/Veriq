"""Permission enforcement for agent tools. The LLM can only invoke allowlisted tools;
privileged ops (deploy, prod creds, destructive git) are rejected unconditionally."""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

ALLOWLISTED_TOOLS = frozenset({
    "read_file", "search_code", "list_files", "git_diff",
    "run_command", "run_tests", "run_linter", "run_typecheck",
    "run_security_scan", "start_application", "open_browser",
    "take_screenshot", "inspect_dom", "inspect_console",
    "inspect_network", "apply_patch", "git_diff_after_patch",
})

DENIED_COMMAND_SUBSTRINGS = (
    "terraform apply", "kubectl ", "helm upgrade", "aws ", "az ",
    "gcloud ", "git reset --hard", "git push --force", "rm -rf /",
    "shutdown", "reboot", "docker push",
)

DENIED_CATEGORIES = frozenset({"auth", "payments", "iam", "infrastructure"})


@dataclass
class Policy:
    deny_paths: list[str] = field(default_factory=list)
    require_admin_for: list[str] = field(default_factory=list)
    repair_enabled: bool = True

    def is_tool_allowed(self, tool: str) -> bool:
        return tool in ALLOWLISTED_TOOLS

    def is_path_allowed(self, path: str) -> bool:
        p = path.replace("\\", "/")
        return not any(fnmatch.fnmatch(p, pat) or fnmatch.fnmatch(p, pat.lstrip("/"))
                       for pat in self.deny_paths)

    def is_command_allowed(self, cmd: str) -> bool:
        c = cmd.lower()
        return not any(d in c for d in DENIED_COMMAND_SUBSTRINGS)

    def needs_admin(self, category: str) -> bool:
        return category in set(self.require_admin_for) | DENIED_CATEGORIES


def check_patch_allowed(paths: list[str], policy: Policy) -> tuple[bool, str]:
    blocked = [p for p in paths if not policy.is_path_allowed(p)]
    if blocked:
        return False, f"Denied paths in patch: {blocked}"
    return True, "ok"
