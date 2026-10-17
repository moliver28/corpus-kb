"""The Overview page's health panel (UX program Wave 3).

``overview_checks`` reuses the doctor's own probes (env vars, Postgres,
Ollama) so the page and ``corpus-kb doctor`` never drift; ``check_line``
renders one check as one plain-language markdown line. Both are pure
view-model code: no NiceGUI import, and the probe functions are imported
inside the call so tests patch them at their owning modules.
"""

from __future__ import annotations

import os

__all__ = ["check_line", "overview_checks"]


async def overview_checks(config: dict[str, object] | None = None) -> list[dict[str, object]]:
    """The Overview page's plain-language health panel.

    Each row is ``{name, ok, detail, fix}``; ``fix`` carries the doctor's
    remediation so a failed row always names the next step.
    """
    from corpus_kb._setup.doctor_env import env_var_check
    from corpus_kb._setup.doctor_research import STATUS_OK
    from corpus_kb._setup.setup_steps import check_ollama, check_postgres

    cfg = config or {}
    checks: list[dict[str, object]] = []

    env = env_var_check()
    checks.append(
        {
            "name": "Configuration",
            "ok": env.status == STATUS_OK,
            "detail": env.detail,
            "fix": env.fix,
        }
    )

    db_cfg = cfg.get("database", {})
    db = db_cfg if isinstance(db_cfg, dict) else {}
    conn_str = os.environ.get("CORPUS_KB_DATABASE_URL", "") or str(db.get("connection_string", ""))
    if conn_str:
        pg_ok, pg_msg = await check_postgres(conn_str)
    else:
        pg_ok, pg_msg = False, "no connection string configured"
    checks.append(
        {
            "name": "Database",
            "ok": pg_ok,
            "detail": pg_msg,
            "fix": "" if pg_ok else "Run: corpus-kb setup",
        }
    )

    emb_cfg = cfg.get("embedding", {})
    emb = emb_cfg if isinstance(emb_cfg, dict) else {}
    ollama_url = str(emb.get("base_url", "http://localhost:11434"))
    ollama_ok, ollama_msg = await check_ollama(ollama_url)
    checks.append(
        {
            "name": "Models",
            "ok": ollama_ok,
            "detail": ollama_msg,
            "fix": "" if ollama_ok else "Run: corpus-kb setup (installs the models)",
        }
    )
    return checks


def check_line(check: dict[str, object]) -> str:
    """One Overview check as one markdown line (plain language)."""
    mark = "OK" if bool(check.get("ok")) else "Needs attention"
    line = f"**{check.get('name', 'Check')} — {mark}**: {check.get('detail', '')}"
    fix = str(check.get("fix", "") or "")
    if fix and not bool(check.get("ok")):
        line += f". Fix: {fix}"
    return line
