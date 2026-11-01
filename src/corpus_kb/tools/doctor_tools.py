"""Doctor snapshot handler (UI SPA program Task 5a, B4).

ONE read-only handler behind ``GET /api/doctor``. It reuses the EXACT
probe functions the ``corpus-kb doctor`` CLI command runs (``_setup``
doctor modules) and assembles them into one JSON envelope — the CLI keeps
its printed report, the SPA gets the same truth as data. No second probe
set, no copy of the check logic.

The envelope is honest by construction: every probe reports its own
``status`` (``ok``/``warn``/``fail``/``info``/``skipped``); a probe that
cannot run is reported, never raised. The route never mutates anything.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any, cast

from corpus_kb._setup.doctor_env import env_var_check
from corpus_kb._setup.doctor_research import ResearchCheck, embedder_check, research_checks
from corpus_kb._setup.doctor_storage import storage_checks
from corpus_kb.rag.rerank_settings import load_rerank_settings
from corpus_kb.tools.ingest_common import load_config_or_pass

__all__ = ["doctor_snapshot"]


def _check_dicts(checks: list[ResearchCheck]) -> list[dict[str, object]]:
    """One flat dict per check, in the probe's own stable order."""
    return [dict(asdict(check)) for check in checks]


def _connection_string(cfg: dict[str, Any]) -> str:
    """The DSN the research commands use: env override wins (review F-1)."""
    db_cfg = cfg.get("database") or {}
    return os.environ.get("CORPUS_KB_DATABASE_URL", "") or str(db_cfg.get("connection_string", ""))


async def doctor_snapshot(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run every doctor probe and return the read-only envelope.

    Probes are the CLI doctor's own functions, so the SPA's System-ready
    strip can never disagree with ``corpus-kb doctor`` on the same host.
    """
    from corpus_kb._setup.install import (
        DEFAULT_OLLAMA_URL,
        detect_profile,
        load_installer_profiles,
    )
    from corpus_kb._setup.setup_steps import (
        check_extensions,
        check_ollama,
        check_postgres,
    )

    cfg = load_config_or_pass(config)
    info = detect_profile()
    profiles = load_installer_profiles(cfg)
    profile = profiles.get(info["profile"], {})
    recommended_model = str(profile.get("model", "nomic-embed-text"))

    conn_str = _connection_string(cfg)
    postgres_ok, postgres_msg = (
        await check_postgres(conn_str) if conn_str else (False, "no connection string")
    )
    extensions = await check_extensions(conn_str) if postgres_ok else None

    emb_cfg = cfg.get("embedding") or {}
    ollama_url = str(emb_cfg.get("base_url", DEFAULT_OLLAMA_URL))
    ollama_ok, ollama_msg = await check_ollama(ollama_url)

    research: list[ResearchCheck] | None = None
    if postgres_ok:
        research = await research_checks(conn_str, cfg, extensions, ollama_ok=ollama_ok)
    storage = await storage_checks(cfg, conn_str, postgres_ok)
    env = env_var_check()

    # Structured embedding facts (issue #183) + the reranker's active
    # state, straight from config — the SPA's System-ready strip reads
    # these fields, so they must not hide inside check detail strings.
    # ``degraded`` is the provider-health flag: true only when the active
    # provider cannot emit usable vectors right now (Ollama down, or the
    # pgml extension known-absent). Unknown states stay honest: an
    # unprobed pgml is not a degraded pgml.
    emb_cfg = cfg.get("embedding") or {}
    provider = str(emb_cfg.get("provider", "ollama"))
    pgml_installed: bool | None = None
    if provider == "pgml" and extensions is not None:
        pgml_installed = bool(extensions.get("pgml", (False, ""))[0])
    embedding_degraded = (not ollama_ok) if provider != "pgml" else pgml_installed is False
    embedder = embedder_check(cfg, pgml_installed=pgml_installed)
    rerank = load_rerank_settings(cast("dict[str, object]", cfg))

    return {
        "status": "success",
        "result": {
            "profile": {
                "os": info["os"],
                "python": info["python"],
                "cpu_cores": info["cpu_cores"],
                "ram_gb": info["ram_gb"],
                "vram_detected": bool(info.get("vram_detected")),
                "vram_gb": info.get("vram_gb"),
                "profile": info["profile"],
                "recommended_model": recommended_model,
            },
            "postgres": {
                "reachable": bool(postgres_ok),
                "message": str(postgres_msg),
                "extensions": None
                if extensions is None
                else {
                    name: {"installed": bool(installed), "version": str(version)}
                    for name, (installed, version) in extensions.items()
                },
            },
            "ollama": {"reachable": bool(ollama_ok), "message": str(ollama_msg)},
            "embedding": {
                "model": str(emb_cfg.get("model", "")),
                "dimensions": embedder.embedding_dimensions,
                "degraded": embedding_degraded,
            },
            "reranker": {
                "enabled": bool(rerank.enabled),
                "backend": rerank.backend,
                "model": rerank.model,
            },
            "env": dict(asdict(env)),
            "research_checks": [] if research is None else _check_dicts(research),
            "storage_checks": _check_dicts(storage),
        },
    }
