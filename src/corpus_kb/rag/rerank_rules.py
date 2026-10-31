"""Shared rerank config rules: the ONE home for the ``reranking.*`` rules.

``rag/rerank_settings.py`` (runtime reads) and ``_setup/validate_configs.py``
(the CI config gate) both enforce the same rules. This module holds each
rule once, so the two callers cannot drift. Constants and pure functions
only — no I/O and no imports beyond the standard library. It must stay
import-light: the validate-configs CI job installs only PyYAML.

The validate-configs gate runs by file path without the package installed,
so it imports this module through an ``ImportError`` guard that falls back
to a direct file load (skipping the ``rag`` package init, which imports
third-party modules). The runtime caller imports it normally.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# reranking.return_top_k range: a positive int in 1..1000. It IS the
# default return k, so the catalogue and the settings loader pin the same
# bounds (tests/test_config_blocks.py).
RETURN_TOP_K_MIN = 1
RETURN_TOP_K_MAX = 1000

# reranking.backend values (null also valid — the qwen3 judgment scorer).
# "generative" is the explicit alias of null; "fake" is the test double.
SUPPORTED_RERANK_BACKENDS = frozenset({"llamacpp", "generative", "fake"})

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


def return_top_k_error(value: object) -> str | None:
    """Range rule for ``reranking.return_top_k``: a positive int in 1..1000."""
    if isinstance(value, bool) or not isinstance(value, int):
        return f"reranking.return_top_k must be an int; got {value!r}"
    if not RETURN_TOP_K_MIN <= value <= RETURN_TOP_K_MAX:
        return (
            f"reranking.return_top_k must be between {RETURN_TOP_K_MIN} and "
            f"{RETURN_TOP_K_MAX}; got {value}"
        )
    return None


def backend_error(backend: object) -> str | None:
    """Enum rule for ``reranking.backend`` (None is the shipped default)."""
    if backend is not None and backend not in SUPPORTED_RERANK_BACKENDS:
        backends = ", ".join(sorted(SUPPORTED_RERANK_BACKENDS))
        return f"reranking.backend: must be null or one of ({backends}), got {backend!r}"
    return None


def is_local_host(hostname: str) -> bool:
    """True for loopback, private, link-local, or reserved addresses."""
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        return False
    return bool(addr.is_loopback or addr.is_private or addr.is_link_local or addr.is_reserved)


def service_url_error(value: str, key: str, allow_remote: bool) -> str | None:
    """Scheme, credential, and local-host rules for one rerank endpoint."""
    parsed = urlparse(value)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.hostname:
        return f"{key} must be an http or https URL with a host; got {value!r}"
    if parsed.username is not None or parsed.password is not None:
        return f"{key} must not carry credentials; got {value!r}"
    if not allow_remote and not is_local_host(parsed.hostname):
        return (
            f"{key} must target localhost or a private address; set "
            f"reranking.allow_remote_urls: true for a remote server; got {value!r}"
        )
    return None
