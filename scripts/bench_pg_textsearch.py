"""U23: pg_textsearch benchmark stub — gated, benchmark-only, never adopted by default.

pg_textsearch is a PostgreSQL-licensed BM25 index extension (license
re-verified from the upstream repo before any run; native Postgres FTS
STAYS the default regardless — adoption requires the benchmark to show a
configured recall or latency gain AND the image to build with it, per spec
v6 U23). ParadeDB pg_search is AGPL and is BANNED — never benchmark it.

This stub is deliberately NOT in the shipping image and NOT wired into any
default path: it runs only when invoked with the explicit
``--enable-pg-textsearch`` flag against a database that has the extension
installed. Its documented limitation to record in every report: Boolean
filtering combined with BM25 scoring is not yet a single index scan.

Without the flag the script emits a honest ``not_enforced`` report and
exits 0 (nothing failed; the benchmark is simply not enabled).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

LICENSE = "PostgreSQL License (verify against the upstream repo before any run)"
KNOWN_LIMITATION = "Boolean filtering + BM25 scoring is not yet a single index scan"
STATUS_NOT_ENFORCED = "not_enforced"
STATUS_INSUFFICIENT_DATA = "insufficient_data"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dsn",
        default="postgresql://corpus:corpus@localhost:5432/corpus",
    )
    parser.add_argument(
        "--enable-pg-textsearch",
        action="store_true",
        help="opt-in gate: without it the benchmark does NOT run",
    )
    parser.add_argument("--k", type=int, default=10)
    return parser.parse_args(argv)


def gated_report(enabled: bool) -> dict[str, object]:
    """The report for a NOT-ENABLED invocation (the only offline path)."""
    return {
        "status": STATUS_NOT_ENFORCED if not enabled else "enabled",
        "reason": (
            "benchmark is opt-in (run with --enable-pg-textsearch against a DB "
            "with the pg_textsearch extension installed)"
        )
        if not enabled
        else "gate open; requires a live database",
        "native_fts_default": True,
        "license": LICENSE,
        "known_limitation": KNOWN_LIMITATION,
        "pg_search": "banned (AGPL) — never benchmarked, never adopted",
    }


async def check_extension(dsn: str) -> str:
    """Probe whether pg_textsearch is installed; raises when unreachable."""
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT extversion FROM pg_extension WHERE extname = 'pg_textsearch'"
        )
        return str(row["extversion"]) if row is not None else ""
    finally:
        await conn.close()


async def run_benchmark(dsn: str, k: int) -> dict[str, object]:
    """Real run requires the extension; without it the data is insufficient."""
    version = await check_extension(dsn)
    if not version:
        return {
            "status": STATUS_INSUFFICIENT_DATA,
            "reason": (
                "pg_textsearch extension is not installed on the target database; "
                "native Postgres FTS remains the default and the comparison cannot run"
            ),
            "native_fts_default": True,
            "license": LICENSE,
            "known_limitation": KNOWN_LIMITATION,
        }
    # The extension IS installed: the BM25-vs-native-FTS fusion comparison
    # belongs to the U45 harness labels. This run reports the runtime facts
    # it can verify and marks the metric leg insufficient until wired.
    return {
        "status": STATUS_INSUFFICIENT_DATA,
        "reason": (
            "extension present but the BM25-vs-native-FTS comparison harness leg "
            "is not wired yet; no numbers are fabricated"
        ),
        "extension_version": version,
        "native_fts_default": True,
        "license": LICENSE,
        "known_limitation": KNOWN_LIMITATION,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.enable_pg_textsearch:
        print(json.dumps(gated_report(enabled=False), indent=2))
        return 0
    try:
        report = asyncio.run(run_benchmark(args.dsn, args.k))
    except Exception as exc:  # unreachable DB = real failure, honest exit
        print(
            json.dumps(
                {
                    "status": "error",
                    "reason": f"benchmark requested but the database is unreachable: {exc}",
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
