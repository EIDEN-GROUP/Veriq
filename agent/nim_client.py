"""NVIDIA NIM client: OpenAI-compatible chat completions with structured-JSON enforcement,
configurable model + fallback chain, retries with exponential backoff, timeouts."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

import requests
from tenacity import retry, stop_after_attempt, wait_exponential


def _load_prompt(name: str) -> str:
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "prompts" / name
    return p.read_text() if p.exists() else ""


@dataclass
class NimClient:
    base_url: str = field(default_factory=lambda: os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1"))
    api_key: str = field(default_factory=lambda: os.environ.get("NIM_API_KEY", ""))
    model: str = field(default_factory=lambda: os.environ.get("NIM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"))
    fallback_models: list[str] = field(default_factory=list)
    timeout_s: int = 120

    def __post_init__(self) -> None:
        if not self.fallback_models:
            raw = os.environ.get("NIM_FALLBACK_MODELS",
                                 "nvidia/nemotron-3-super-120b-a12b,mistralai/mistral-nemotron")
            self.fallback_models = [m.strip() for m in raw.split(",") if m.strip()]
        self._system = _load_prompt("system.md")

    @property
    def model_chain(self) -> list[str]:
        return [self.model] + [m for m in self.fallback_models if m != self.model]

    def complete_json(self, task_prompt: str, evidence: dict, reasoning: bool = True) -> dict:
        """Call NIM requesting a JSON object; retry malformed responses, then try fallback models."""
        if not self.api_key:
            raise RuntimeError("NIM_API_KEY not configured")
        from agent.redact import redact_evidence_blob
        if not self.api_key:
            raise RuntimeError("NIM unavailable on all models: NIM_API_KEY not configured")
        safe_evidence = redact_evidence_blob(evidence)
        user_block = f"{task_prompt}\n\n<EVIDENCE>\n{json.dumps(safe_evidence)[:60000]}\n</EVIDENCE>\nReturn ONLY JSON."
        last_err: Exception | None = None
        for model in self.model_chain:
            try:
                raw = self._chat(model, self._system, user_block, reasoning)
                return self._parse_json(raw)
            except Exception as exc:
                last_err = exc
                continue
        raise RuntimeError(f"NIM unavailable or malformed on all models: {last_err}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=2, min=2, max=30))
    def _chat(self, model: str, system: str, user: str, reasoning: bool) -> str:
        if not self.api_key:
            raise RuntimeError("NIM_API_KEY not configured")
        # Nemotron reasoning toggle is system-prompt driven; be explicit.
        sys = system + ("\nReasoning: detailed thinking ON." if reasoning else "\nReasoning: detailed thinking OFF, concise JSON only.")
        resp = requests.post(
            f"{self.base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [
                {"role": "system", "content": sys},
                {"role": "user", "content": user}],
                "temperature": 0.1, "max_tokens": 4000,
                "response_format": {"type": "json_object"}},
            timeout=self.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _parse_json(raw: str) -> dict:
        raw = raw.strip()
        # tolerate fences if a model adds them despite instructions
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{"):] if "{" in raw else raw
        try:
            parsed = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Malformed NIM JSON: {exc}; raw prefix: {raw[:300]}")
        if not isinstance(parsed, dict):
            raise ValueError("NIM JSON root must be an object")
        return parsed
