"""Write-back of the adopted DSN into the LOCAL runtime config.yaml.

The repo-root ``config.yaml`` is the local runtime contract by design
(``load_config`` discovers it in the current working directory). This
module edits exactly one key — ``database.connection_string`` — with a
surgical line replacement that keeps every comment and key in place.
The packaged ``src/corpus_kb/config.yaml`` is NEVER touched.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_TOP_LEVEL_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:")
_CONNECTION_RX = re.compile(r"^(\s*)connection_string\s*:")


def config_write_target() -> Path:
    """The config file quickstart may write: env-pinned, else ./config.yaml.

    ``CORPUS_KB_CONFIG`` wins when set (the same file ``load_config``
    reads); otherwise the repo-root/working-directory ``config.yaml``.
    """
    env_path = os.environ.get("CORPUS_KB_CONFIG")
    if env_path:
        return Path(env_path)
    return Path("config.yaml")


def write_connection_string(path: Path, dsn: str) -> str:
    """Set ``database.connection_string`` in one YAML file.

    In-place line replacement when the database block holds exactly one
    ``connection_string`` key (comments survive); a missing key gets the
    block appended; an existing-but-ambiguous file is refused. Returns
    the one-line notice for the quickstart report.
    """
    if not path.exists():
        path.write_text(f"database:\n  connection_string: {dsn}\n", encoding="utf-8")
        return f"Created {path} (database.connection_string)"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out, hits = _replace_connection_lines(lines, dsn)
    if hits == 1:
        path.write_text("".join(out), encoding="utf-8")
        return f"Wrote {path} (database.connection_string)"
    if hits == 0:
        merged = _append_database_block(text, dsn)
        path.write_text(merged, encoding="utf-8")
        return f"Wrote {path} (database.connection_string appended)"
    raise ValueError(
        f"{path} has {hits} connection_string keys inside database blocks; edit it by hand"
    )


def _replace_connection_lines(lines: list[str], dsn: str) -> tuple[list[str], int]:
    """Replace the single connection_string line under ``database:``."""
    in_database_block = False
    hits = 0
    out: list[str] = []
    for line in lines:
        if _TOP_LEVEL_RX.match(line):
            in_database_block = line.startswith("database:")
        match = _CONNECTION_RX.match(line) if in_database_block else None
        if match is None:
            out.append(line)
            continue
        hits += 1
        indent = match.group(1)
        newline = "\n" if line.endswith("\n") else ""
        out.append(f"{indent}connection_string: {dsn}{newline}")
    return out, hits


def _append_database_block(text: str, dsn: str) -> str:
    """Append a minimal database block to a file without one."""
    block = f"database:\n  connection_string: {dsn}\n"
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" + block if text.strip() else block)
