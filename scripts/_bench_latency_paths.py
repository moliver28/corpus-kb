"""The four latency-path drivers behind bench_latency.py (master-db W2).

P1 ``cold_start_samples`` (boot to bind plus first search), P2 ``ingest_samples``
(per-document ingest plus the claim short-circuit proof), P3 ``matrix_sample``
(screens over 10k synthetic vectors), P4 ``route_samples`` (route p50). Ollama
down is the designed degraded path; nothing here fabricates a sample.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from statistics import median
from typing import Any

import httpx
import numpy as np

from corpus_kb._setup.migrate import run_duckdb_migrations
from corpus_kb.storage.connect import open_store
from corpus_kb.storage.duckdb_support import duckdb_path_to_conn_string, vector_literal
from corpus_kb.tools.analysis_route_tools import analysis_route
from corpus_kb.tools.ingest_common import ingest_text
from corpus_kb.tools.vector_screen_tools import duplicates_screen

READY_DEADLINE_S = QUERY_DEADLINE_S = 120.0  # boot and first-search bounds
POLL_INTERVAL_S = 0.05
MATRIX_DOCS, MATRIX_DIMS, MATRIX_SEED, MATRIX_RUNS = 10_000, 2560, 20261124, 3

ROUTE_PHRASES = (
    "how do participants describe onboarding",
    "what blocks adoption",
    "which users churn after week two",
    "summarize sentiment by segment",
    "find conflicting statements",
    "cluster the open ends",
    "which theme grew over time",
    "who mentioned pricing",
    "what did the control group say",
    "rank the biggest pain points",
)


def _pick_base_port(start: int, count: int) -> int:
    """The first base port whose whole range refuses connections."""
    base = max(1024, start)
    for _ in range(20):
        busy = False
        for port in range(base, base + count):
            with socket.socket() as sock:
                sock.settimeout(0.2)
                busy = busy or sock.connect_ex(("127.0.0.1", port)) == 0
        if not busy:
            return base
        base += count + 10
    raise RuntimeError("No free port range for the cold-start bench.")


def _terminate_tree(proc: subprocess.Popen[Any]) -> None:
    """Kill the boot process and its children (the script shim spawns one)."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=15)


async def _poll_boot(port: int, query: bool, deadline_s: float) -> float:
    """Seconds until boot answers: the status route 200, or search 200 JSON."""
    started = time.perf_counter()
    url = f"http://127.0.0.1:{port}{'/api/search' if query else '/api/ingest/status'}"
    async with httpx.AsyncClient(timeout=30.0 if query else 2.0) as client:
        while time.perf_counter() - started < deadline_s:
            try:
                request = client.post(url, json={"query": "smoke"}) if query else client.get(url)
                response = await request
                if response.status_code == 200 and (response.json() if query else True) is not None:
                    return time.perf_counter() - started
            except (httpx.TransportError, ValueError):
                pass
            await asyncio.sleep(POLL_INTERVAL_S)
    raise TimeoutError(f"Port {port} never met the {deadline_s:g} s boot deadline.")


class OfflineSkipError(Exception):
    """The fts pre-install cannot download; the P1 path skips honestly (RVW2-06)."""


async def cold_start_samples(base_port: int, iterations: int) -> list[tuple[float, float]]:
    """P1: boot one server per iteration; return (ready, first-query) seconds."""
    from corpus_kb._setup.setup_duckdb import preinstall_extensions

    # The RAG mirror needs fts; a fresh runner has none, so pre-install like
    # `setup`. The download is a network dependency; an offline machine skips
    # the P1 path with a typed signal instead of a crash (RVW2-06).
    if preinstall_extensions() != 0:
        raise OfflineSkipError(
            "The fts extension cannot download offline."
            " The P1 cold-start path needs it for the first query."
        )
    exe = shutil.which("corpus-kb")
    if exe is None:
        raise RuntimeError(
            "The corpus-kb console script is not on PATH. Install the package first."
        )
    base_port = _pick_base_port(base_port, iterations)
    samples: list[tuple[float, float]] = []
    for offset in range(iterations):
        port = base_port + offset
        with tempfile.TemporaryDirectory(prefix="bench-cold-", ignore_cleanup_errors=True) as tmp:
            dsn = duckdb_path_to_conn_string(Path(tmp) / "bench.duckdb")
            run_duckdb_migrations(dsn)
            # database.backend keys the DuckDB arm; the tracked config ships graph.backend: age.
            env = os.environ | {
                "CORPUS_KB_DATABASE_URL": dsn,
                "CORPUS_KB_DATABASE_BACKEND": "duckdb",
                "CORPUS_KB_GRAPH_BACKEND": "duckdb",
                "CORPUS_KB_HOST": "127.0.0.1",  # CI may otherwise bind ::1 only
                "CORPUS_KB_UI_OPEN": "0",  # never open a browser tab
            }
            log = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
            proc = subprocess.Popen(
                [exe, "start", "--transport", "http", "--port", str(port)],
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                ready = await _poll_boot(port, False, READY_DEADLINE_S)
                first_query = await _poll_boot(port, True, QUERY_DEADLINE_S)
            except TimeoutError as exc:
                log.seek(0)
                raise TimeoutError(f"{exc} Boot log tail: {log.read()[-2000:]}") from None
            finally:
                _terminate_tree(proc)
                log.close()
                time.sleep(1.0)
        samples.append((ready, first_query))
    return samples


def _doc_text(index: int) -> str:
    """One distinct bench document body; content differs per index."""
    return (
        f"Bench document {index}. Participants discuss onboarding, adoption, pricing, and churn in "
        f"week two. Segment {index} ranks pain points and records conflicting statements for the group."
    )


async def ingest_samples(scratch_dsn: str, doc_count: int) -> list[float]:
    """P2: per-document ingest seconds, then the repeat-ingest growth proof."""
    store = await open_store(scratch_dsn)
    samples: list[float] = []
    try:
        for index in range(doc_count):
            started = time.perf_counter()
            envelope = await ingest_text(
                _doc_text(index), store, "text", None, source=f"bench/doc-{index}.txt"
            )
            samples.append(time.perf_counter() - started)
            status = str(envelope.get("status"))
            if status not in {"success", "partial"}:
                raise AssertionError(f"Ingest of doc {index} returned {status}: {envelope}")
        before = await store.fetchval("SELECT COUNT(*) FROM chunks_vectors")
        again = await ingest_text(_doc_text(0), store, "text", None, source="bench/doc-0.txt")
        after = await store.fetchval("SELECT COUNT(*) FROM chunks_vectors")
        # Claim short-circuit: skip envelope, zero vector growth. Under the degraded
        # embed the healing rule re-ingests; either path adds no new rows.
        if str(again.get("status")) not in {"skipped", "success"}:
            raise AssertionError(f"The repeat ingest returned {again.get('status')}: {again}")
        if after != before:
            raise AssertionError(
                f"The repeat ingest wrote rows: {before} vectors before, {after} after."
            )
    finally:
        await store.close()
    return samples


async def matrix_sample(scratch_dsn: str) -> float:
    """P3: seed 10k synthetic vectors, screen ``duplicates_screen`` three times.

    The read-only screen shares one seeded store; the median of three gives
    the p50 a denominator (one float can be a lucky run). The extra CI cost
    is two screen runs, not two reseeds.
    """
    tenant = str(uuid.uuid4())
    vectors = np.random.default_rng(MATRIX_SEED).standard_normal((MATRIX_DOCS, MATRIX_DIMS))
    store = await open_store(scratch_dsn)
    try:
        doc_id = str(uuid.uuid4())
        await store.execute(
            "INSERT INTO documents (doc_id, tenant_id, source, source_type, chunk_count) "
            "VALUES (?, ?, ?, 'text', ?)",
            doc_id,
            tenant,
            f"bench-matrix/{tenant}",
            MATRIX_DOCS,
        )
        chunk_ids = [str(uuid.uuid4()) for _ in range(MATRIX_DOCS)]
        chunk_rows = [
            (chunk_ids[i], tenant, doc_id, i, f"bench chunk {i} of the synthetic matrix store.")
            for i in range(MATRIX_DOCS)
        ]
        await store.executemany(
            "INSERT INTO chunks (chunk_id, tenant_id, doc_id, chunk_index, text) "
            "VALUES (?, ?, ?, ?, ?)",
            chunk_rows,
        )
        vector_rows = [
            (chunk_ids[i], tenant, vector_literal(vectors[i].tolist())) for i in range(MATRIX_DOCS)
        ]
        await store.executemany(
            "INSERT INTO chunks_vectors (chunk_id, tenant_id, embedding) "
            "VALUES (?, ?, CAST(? AS FLOAT[2560]))",
            vector_rows,
        )
        samples: list[float] = []
        for _ in range(MATRIX_RUNS):
            started = time.perf_counter()
            envelope = await duplicates_screen(store, tenant_id=tenant)
            samples.append(time.perf_counter() - started)
            if str(envelope.get("status")) != "success":
                raise AssertionError(f"duplicates_screen returned {envelope.get('status')}")
        return median(samples)
    finally:
        await store.close()


async def route_samples(iterations: int) -> list[float]:
    """P4: ``analysis_route`` milliseconds over the pinned phrase list."""
    config = {"embedding": {"provider": "bench-phrase-only"}}  # never maps to an embedder
    samples: list[float] = []
    for index in range(iterations):
        question = ROUTE_PHRASES[index % len(ROUTE_PHRASES)]
        started = time.perf_counter()
        envelope = await analysis_route(None, config, question=question)
        samples.append((time.perf_counter() - started) * 1000.0)
        status = str(envelope.get("status"))
        if status not in {"resolved", "ambiguous", "degraded"}:
            raise AssertionError(f"Route for {question!r} returned {status}: {envelope}")
    return samples
