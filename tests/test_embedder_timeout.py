"""Embed read-timeout contract tests (issue #468).

Two bounds hold on the Ollama embed call path:

1. A stalled server (accepts TCP, never answers) must return zero vectors
   inside the timeout budget, and the wall-clock deadline must stop new
   sub-batches once the budget is spent. The httpx read timeout alone is
   not enough: it is a per-chunk inactivity bound, so a server that drips
   keep-alive bytes can starve it and block a worker for many minutes.
2. An oversized ``input`` batch must split into sub-batches no larger than
   the batch cap (module constant ``EMBED_MAX_BATCH_TEXTS``, default of the
   ``embedding.max_batch_texts`` config key).

No live Ollama: test 1 uses a local threaded TCP stub, test 2 an
``httpx.MockTransport`` transport injected through the ollama client.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from typing import Any

import httpx
import pytest
from ollama import Client as RealOllamaClient

from corpus_kb.rag import embedder as embedder_module
from corpus_kb.rag.embedder import OllamaEmbedder

_STUB_TIMEOUT = 1.0
_DIMENSIONS = 8


def _config(base_url: str, **extra: Any) -> dict[str, Any]:
    embedding: dict[str, Any] = {
        "provider": "ollama",
        "model": "test-model",
        "base_url": base_url,
        "batch_size": 2,
        "dimensions": _DIMENSIONS,
        **extra,
    }
    return {"embedding": embedding}


class _StalledServer:
    """TCP stub that accepts requests and never sends a response byte.

    Each accepted connection is counted, drained (one recv consumes the
    request so the client's write succeeds), and then held open in silence.
    The hold releases when the server stops or after 30 seconds, whichever
    comes first.
    """

    def __init__(self) -> None:
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.5)
        host, port = self._listener.getsockname()[:2]
        self.base_url = f"http://{host}:{port}"
        self.requests = 0
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stopping.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            self.requests += 1
            threading.Thread(target=self._hold, args=(conn,), daemon=True).start()

    def _hold(self, conn: socket.socket) -> None:
        try:
            conn.recv(65536)
            self._stopping.wait(30.0)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=2.0)
        self._listener.close()


def _mock_client(seen: list[int], dimensions: int) -> Any:
    """Build an ollama Client factory backed by httpx.MockTransport.

    The handler records the ``input`` length of every request and answers
    with that many vectors of ``dimensions`` width, so the embedder's
    returned-width validation passes.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        seen.append(len(payload["input"]))
        return httpx.Response(
            200,
            json={
                "embeddings": [[0.5] * dimensions for _ in payload["input"]],
                "prompt_eval_count": 3,
            },
        )

    def factory(**kwargs: Any) -> RealOllamaClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return RealOllamaClient(**kwargs)

    return factory


class TestStalledReadDegrades:
    def test_stalled_read_returns_zero_vectors_within_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A silent server degrades inside the budget and the deadline stops
        new sub-batches: zero vectors, a recorded note, one API request."""
        server = _StalledServer()
        try:
            monkeypatch.setattr(embedder_module, "EMBED_TIMEOUT_SECONDS", _STUB_TIMEOUT)
            embedder = OllamaEmbedder(_config(server.base_url))
            started = time.monotonic()
            vectors = embedder.embed_batch(["a", "b", "c", "d"])
            elapsed = time.monotonic() - started
        finally:
            server.close()

        assert elapsed < _STUB_TIMEOUT + 5.0
        assert vectors == [[0.0] * _DIMENSIONS for _ in range(4)]
        assert embedder.last_note is not None
        assert server.requests == 1


class TestOversizedBatchSplits:
    def test_oversized_batch_splits_into_bounded_sub_batches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A 70-text input under an oversized ``batch_size`` goes out as
        capped sub-batches; the ``max_batch_texts`` config override lowers
        the cap."""
        cap = embedder_module.EMBED_MAX_BATCH_TEXTS

        seen_default: list[int] = []
        monkeypatch.setattr(embedder_module, "Client", _mock_client(seen_default, _DIMENSIONS))
        embedder = OllamaEmbedder(_config("http://localhost:11434", batch_size=64))
        vectors = embedder.embed_batch([f"text-{i}" for i in range(70)])

        assert seen_default == [cap, cap, 70 - 2 * cap]
        assert all(size <= cap for size in seen_default)
        assert len(vectors) == 70
        assert all(len(vector) == _DIMENSIONS for vector in vectors)

        seen_override: list[int] = []
        monkeypatch.setattr(embedder_module, "Client", _mock_client(seen_override, _DIMENSIONS))
        overridden = OllamaEmbedder(
            _config("http://localhost:11434", batch_size=64, max_batch_texts=8)
        )
        overridden.embed_batch([f"text-{i}" for i in range(20)])

        assert seen_override == [8, 8, 4]
