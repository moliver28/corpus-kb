"""Write-back of the adopted database pair into the LOCAL runtime config.yaml.

The repo-root ``config.yaml`` is the local runtime contract by design
(``load_config`` discovers it in the current working directory). This
module edits keys inside the ``database:`` block —
``database.connection_string`` (quickstart) and, for the DuckDB setup
path, the ``database.backend`` + DSN pair — with surgical line
replacement that keeps every comment and key in place. The packaged
``src/corpus_kb/config.yaml`` is NEVER touched.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_TOP_LEVEL_RX = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*\s*:")


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
    return _write_database_keys(path, {"connection_string": dsn})


def write_database_pair(path: Path, backend: str, dsn: str) -> str:
    """Set ``database.backend`` and ``database.connection_string`` in one file.

    The ``setup --backend duckdb`` write-back: both keys land inside the
    existing ``database:`` block (backend first), comments elsewhere
    survive, and an ambiguous file is refused like the single-key form.
    """
    return _write_database_keys(path, {"backend": backend, "connection_string": dsn})


def _write_database_keys(path: Path, keys: dict[str, str]) -> str:
    """Replace or add each key inside the ``database:`` block of one file."""
    label = ", ".join(f"database.{key}" for key in keys)
    if not path.exists():
        block = "database:\n" + "".join(f"  {key}: {value}\n" for key, value in keys.items())
        path.write_text(block, encoding="utf-8")
        return f"Created {path} ({label})"
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out, hits = _replace_key_lines(lines, keys)
    ambiguous = {key: count for key, count in hits.items() if count > 1}
    if ambiguous:
        detail = ", ".join(f"{count} {key} keys" for key, count in ambiguous.items())
        raise ValueError(f"{path} has {detail} inside database blocks; edit it by hand")
    written = {key for key, count in hits.items() if count == 1}
    if len(written) == len(keys):
        path.write_text("".join(out), encoding="utf-8")
        return f"Wrote {path} ({label})"
    if not written:
        merged = _append_database_block(text, keys)
        path.write_text(merged, encoding="utf-8")
        return f"Wrote {path} ({label} appended)"
    merged = _insert_missing_keys(out, keys, written)
    path.write_text("".join(merged), encoding="utf-8")
    return f"Wrote {path} ({label}; appended missing keys)"


def _replace_key_lines(lines: list[str], keys: dict[str, str]) -> tuple[list[str], dict[str, int]]:
    """Replace the single line per key under ``database:`` (same indent)."""
    in_database_block = False
    hits = dict.fromkeys(keys, 0)
    out: list[str] = []
    for line in lines:
        if _TOP_LEVEL_RX.match(line):
            in_database_block = line.startswith("database:")
        replacement = _replace_line(line, keys) if in_database_block else None
        if replacement is None:
            out.append(line)
            continue
        key, new_line = replacement
        hits[key] += 1
        out.append(new_line)
    return out, hits


def _replace_line(line: str, keys: dict[str, str]) -> tuple[str, str] | None:
    """The (key, replaced line) for a key match at this line, or None."""
    for key, value in keys.items():
        match = re.match(rf"^(\s*){re.escape(key)}\s*:", line)
        if match:
            newline = "\n" if line.endswith("\n") else ""
            return key, f"{match.group(1)}{key}: {value}{newline}"
    return None


def _append_database_block(text: str, keys: dict[str, str]) -> str:
    """Append a minimal database block to a file without one."""
    block = "database:\n" + "".join(f"  {key}: {value}\n" for key, value in keys.items())
    if text and not text.endswith("\n"):
        text += "\n"
    return text + ("\n" + block if text.strip() else block)


def _anchor_key(line: str, keys: dict[str, str], written: set[str]) -> str | None:
    """The first already-written key whose line this is."""
    for key in keys:
        if key in written and re.match(rf"^\s*{re.escape(key)}\s*:", line):
            return key
    return None


def _insert_missing_keys(lines: list[str], keys: dict[str, str], written: set[str]) -> list[str]:
    """Insert keys missing from the block before the first present key line."""
    missing = [(key, value) for key, value in keys.items() if key not in written]
    out: list[str] = []
    inserted = False
    for line in lines:
        if not inserted:
            anchor = _anchor_key(line, keys, written)
            if anchor is not None:
                indent_match = re.match(r"^(\s*)", line)
                indent = indent_match.group(1) if indent_match else ""
                for missing_key, missing_value in missing:
                    out.append(f"{indent}{missing_key}: {missing_value}\n")
                inserted = True
        out.append(line)
    return out
