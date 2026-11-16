"""Doctor report printers (zero-drift split of _setup/install.py).

The human-readable doctor report and the machine-readable ``--json``
report. ``install.py`` re-exports both, so the historical
``install.print_doctor_report`` imports (CLI wiring, tests that call it
through the install module) keep working.

The section printers for research, storage, and journey checks stay
function-level imports inside ``print_doctor_report``, exactly as they
were before the split.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

from corpus_kb._setup.doctor_engine import resolved_backend
from corpus_kb._setup.migrate import DUCKDB_DSN_PREFIX
from corpus_kb._setup.setup_steps import EXTENSION_REMEDIATION, EXTENSIONS


def _print_doctor_system_lines(info: dict[str, Any]) -> str:
    """The header through the Ollama line, backend-honest on DuckDB setups."""
    backend = str(info.get("backend", "postgres"))
    print("\n=== Corpus-KB Doctor ===")
    print(f"OS:           {info['os']}")
    print(f"Python:       {info['python']}")
    print(f"CPU cores:    {info['cpu_cores']}")
    print(f"RAM:          {info['ram_gb']} GB")
    if info.get("vram_detected"):
        print(f"GPU VRAM:     {info['vram_gb']} GB")
    else:
        print("GPU VRAM:     unknown (no NVIDIA VRAM detection available)")
    print(f"Profile:      {info['profile']}")
    if backend == "duckdb":
        print("Postgres:     not used (database.backend: duckdb)")
    else:
        print(
            f"Postgres:     {'OK' if info['postgres_ok'] else 'UNREACHABLE'}"
            f" ({info['postgres_msg']})"
        )
    print(f"Ollama:       {'OK' if info['ollama_ok'] else 'UNREACHABLE'} ({info['ollama_msg']})")
    if backend != "duckdb":
        print("Extensions:")
        extensions = info.get("extensions")
        for name, label in EXTENSIONS:
            if extensions is None:
                print(f"  {label} ({name}): UNKNOWN (Postgres unreachable; cannot verify)")
            else:
                installed, version = extensions[name]
                if installed:
                    print(f"  {label} ({name}): OK v{version}")
                else:
                    print(f"  {label} ({name}): MISSING - {EXTENSION_REMEDIATION}")
    return backend


def _print_doctor_config_block(env_check: Any, info: dict[str, Any]) -> None:
    """The configuration block: env check, config file, and overlay paths."""
    if env_check is None:
        return
    print("\nConfiguration:")
    print(f"  {env_check.name}: {env_check.status.upper()} - {env_check.detail}")
    if env_check.fix:
        print(f"    fix: {env_check.fix}")
    if env_check.doc:
        print(f"    docs: {env_check.doc}")
    # Issue #348: report the config file and the overlay paths, so a
    # client never has to parse config.yaml to know what is in force.
    print(f"  Config file: {info.get('config_file', 'packaged default')}")
    overlays = info.get("overlay_paths") or []
    print(f"  Overlays: {', '.join(overlays) if overlays else 'none'}")


def print_doctor_report(
    info: dict[str, Any],
    research: list[Any] | None = None,
    env_check: Any | None = None,
    storage: list[Any] | None = None,
    journey: list[Any] | None = None,
) -> None:
    """Print read-only diagnostic report."""
    backend = _print_doctor_system_lines(info)
    _print_doctor_config_block(env_check, info)
    if research is None:
        if backend == "duckdb":
            print("\nResearch subsystem: SKIPPED (needs the Postgres backend)")
        else:
            print("\nResearch subsystem: SKIPPED (Postgres unreachable; run corpus-kb setup)")
    else:
        from corpus_kb._setup.doctor_research import print_research_checks

        print_research_checks(research)
    if storage:
        from corpus_kb._setup.doctor_storage import print_storage_checks

        print_storage_checks(storage)
    if journey:
        from corpus_kb._setup.doctor_journey import print_journey_checks

        print_journey_checks(journey)
    print("\nRecommended commands (drop --dry-run to execute setup):")
    print("  1. pip install -e .[dev]")
    print("  2. corpus-kb setup")
    print(f"  3. ollama pull {info['recommended_model']}")
    print("========================\n")


# ResearchCheck statuses (lowercase) to the doctor --json levels (issue #348).
_LEVEL_BY_STATUS = {"ok": "OK", "warn": "WARN", "fail": "FAIL", "info": "INFO", "skipped": "INFO"}


def _check_entry(check: Any) -> dict[str, str]:
    """One JSON check entry: name, level, detail, plus fix and doc when set."""
    status = str(getattr(check, "status", ""))
    entry = {
        "name": str(getattr(check, "name", "")),
        "level": _LEVEL_BY_STATUS.get(status.lower(), status.upper()),
        "detail": str(getattr(check, "detail", "")),
    }
    fix = getattr(check, "fix", "")
    if fix:
        entry["fix"] = str(fix)
    doc = getattr(check, "doc", "")
    if doc:
        entry["doc"] = str(doc)
    return entry


def _database_display_name(backend: str, conn_str: str) -> str:
    """The database name (Postgres) or file path (DuckDB) for the JSON report."""
    if backend == "duckdb":
        if conn_str.startswith(DUCKDB_DSN_PREFIX):
            return conn_str[len(DUCKDB_DSN_PREFIX) :]
        return conn_str
    try:
        path = urlsplit(conn_str).path
    except ValueError:
        return ""
    return unquote(path.lstrip("/"))


def _resolved_config_summary(config: dict[str, Any], conn_str: str) -> dict[str, Any]:
    """The resolved config values a client needs (issue #348): backend,
    database name, embedding model, chat model, overlay paths."""
    from corpus_kb.config import config_overlay_paths

    backend = resolved_backend(config)
    emb_cfg = config.get("embedding") or {}
    llm_cfg = config.get("llm") or {}
    return {
        "backend": backend,
        "database": _database_display_name(backend, conn_str),
        "embedding_model": str(emb_cfg.get("model", "")) if isinstance(emb_cfg, dict) else "",
        "chat_model": str(llm_cfg.get("model", "")) if isinstance(llm_cfg, dict) else "",
        "config_overlays": [str(overlay) for overlay in config_overlay_paths()],
    }


def doctor_json_report(
    info: dict[str, Any],
    env_check: Any | None,
    research: list[Any] | None,
    storage: list[Any] | None,
    config: dict[str, Any],
    conn_str: str,
    journey: list[Any] | None = None,
) -> dict[str, Any]:
    """The machine-readable doctor report (issue #348).

    Every check carries its name, level (OK, INFO, WARN, FAIL) and fix
    text; the config block carries the resolved values a client needs, so
    clients stop parsing config.yaml or the text output. On a DuckDB
    backend the Postgres check is INFO ("not used"), never a FAIL.
    """
    backend = resolved_backend(config)
    if backend == "duckdb":
        checks: list[dict[str, str]] = [
            {
                "name": "Postgres",
                "level": "INFO",
                "detail": "not used: database.backend is duckdb",
            }
        ]
    else:
        checks = [
            {
                "name": "Postgres",
                "level": "OK" if info["postgres_ok"] else "FAIL",
                "detail": str(info["postgres_msg"]),
            }
        ]
        if not info["postgres_ok"]:
            checks[0]["fix"] = "run: corpus-kb setup"
    checks.append(
        {
            "name": "Ollama",
            "level": "OK" if info["ollama_ok"] else "WARN",
            "detail": str(info["ollama_msg"]),
        }
    )
    if not info["ollama_ok"]:
        checks[-1]["fix"] = "install Ollama, then run: ollama serve"
    if env_check is not None:
        checks.append(_check_entry(env_check))
    for group in (research, storage, journey):
        for check in group or []:
            checks.append(_check_entry(check))
    return {
        "checks": checks,
        "config": _resolved_config_summary(config, conn_str),
    }
