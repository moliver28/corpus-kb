"""The preflight soft checks: Ollama and the embedding model (issue #353).

Soft checks warn and never stop setup. Both read the engine's
``corpus-kb doctor --json`` (the resolved-config block) and one GET to
the Ollama tags API for the model list. Stdlib only.
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request

OLLAMA_BASE = "http://127.0.0.1:11434"
PROBE_TIMEOUT_S = 30


def doctor_report(cli: str) -> dict:
    """The engine ``doctor --json`` report, or an empty dict."""
    try:
        result = subprocess.run(
            [cli, "doctor", "--json"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    try:
        report = json.loads(result.stdout or "{}")
    except ValueError:
        return {}
    return report if isinstance(report, dict) else {}


def ollama_models() -> list[str]:
    """Model names Ollama serves, or an empty list when it is down."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names = []
    for row in models:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            names.append(row["name"])
    return names


def soft_checks(cli: str | None, soft: list[dict]) -> None:
    """Append the Ollama and embedding-model rows (warnings, never stops)."""
    report = doctor_report(cli) if cli else {}
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    reachable = None
    for check in checks:
        if isinstance(check, dict) and check.get("name") == "Ollama":
            reachable = check.get("level") == "OK"
    if reachable is None:
        reachable = bool(ollama_models())
    soft.append(
        row(
            "Ollama",
            reachable,
            "Start Ollama: `ollama serve`. Search falls back to keywords only.",
            "reachable" if reachable else "down",
        )
    )
    config = report.get("config") if isinstance(report.get("config"), dict) else {}
    model = str(config.get("embedding_model") or "")
    models = ollama_models()
    pulled = False
    if model:
        pulled = any(name == model or name.startswith(f"{model}:") for name in models)
    soft.append(
        row(
            "embedding model",
            pulled if model else bool(models),
            f"Pull the model: `ollama pull {model or 'nomic-embed-text'}`.",
            model or "no model configured",
        )
    )


def row(name: str, ok: bool, fix: str, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "fix": fix, "detail": detail}
