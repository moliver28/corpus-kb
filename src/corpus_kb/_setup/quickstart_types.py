"""Shared types and config readers for the quickstart step machine.

The dataclasses every collaborator speaks (StepResult, the options,
the injected dependencies, the outcome), the honest step states, the
config-block readers, and the reranker copy. The runner lives in
``quickstart.py``; the CLI wiring in ``cli_quickstart.py``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from corpus_kb._setup.db_provision import DsnParts, PgProbe
from corpus_kb._setup.schema_apply import SchemaApplyResult

STATE_OK = "OK"
STATE_SKIP = "SKIP"
STATE_FIX = "FIX"
STATE_FAIL = "FAIL"

RERANKER_HEADER = "Ollama has no rerank endpoint, so qwen3-reranker models cannot run there."
RERANKER_SERVE = "Serve the GGUF with llama-server, then point reranking.base_url at it:"
LLAMA_SERVER_HINT = "  llama-server -hf Mungert/Qwen3-Reranker-4B-GGUF --port 8011"


@dataclass
class StepResult:
    """One step's honest outcome; ``extra`` lines print under ``detail``.

    ``gates_exit`` is False for the preflight report lines: they state
    what the probes saw, while the postgres/models steps decide the run.
    """

    name: str
    state: str
    detail: str
    extra: tuple[str, ...] = ()
    gates_exit: bool = True


@dataclass
class QuickstartOptions:
    """The CLI flags of one quickstart run."""

    dsn: str | None = None
    superuser_dsn: str | None = None
    no_write: bool = False
    pull_models: bool = True
    json_output: bool = False


OllamaGet = Callable[[str, str], Awaitable[tuple[int, dict[str, object]] | None]]
PgProbeFn = Callable[[str], Awaitable[PgProbe]]
ProvisionFn = Callable[[str, DsnParts], Awaitable[str]]
ApplySchemaFn = Callable[[str], Awaitable[SchemaApplyResult]]
PullFn = Callable[[str], int]
DoctorFn = Callable[[dict[str, object]], Awaitable[int]]


@dataclass
class QuickstartDeps:
    """Collaborators behind callables; real implementations in cli_quickstart."""

    config: dict[str, object]
    ollama_get: OllamaGet
    pg_probe: PgProbeFn
    docker_available: Callable[[], bool]
    provision: ProvisionFn
    apply_schema: ApplySchemaFn
    write_dsn: Callable[[str], str]
    pull_model: PullFn
    run_doctor: DoctorFn
    set_env_dsn: Callable[[str], None]


@dataclass
class QuickstartOutcome:
    """Every step result plus the adopted DSN and the exit code."""

    steps: list[StepResult] = field(default_factory=list)
    adopted_dsn: str | None = None
    exit_code: int = 0


def cfg_str(value: object, key: str, default: str = "") -> str:
    """Read a string key from a config block (or return the default)."""
    if not isinstance(value, dict):
        return default
    found = value.get(key, default)
    return found if isinstance(found, str) else default


def cfg_dict(value: object, key: str) -> dict[str, object]:
    """Read a nested config block (or an empty dict)."""
    if not isinstance(value, dict):
        return {}
    found = value.get(key)
    return found if isinstance(found, dict) else {}


def runtime_dsn(config: dict[str, object]) -> str:
    """The configured DSN (load_config already merged the env override)."""
    dsn = cfg_str(cfg_dict(config, "database"), "connection_string")
    if not dsn:
        raise ValueError("No database.connection_string in the config (or CORPUS_KB_DATABASE_URL)")
    return dsn


def model_present(present: set[str], model: str) -> bool:
    """Whether Ollama lists ``model`` (a tagless name matches any tag)."""
    if model in present:
        return True
    if ":" not in model:
        return any(name.split(":", 1)[0] == model for name in present)
    return False


def reranker_note(model: str) -> tuple[str, ...]:
    """The honest reranker lines: Ollama cannot serve it; llama-server can."""
    return (f"{RERANKER_HEADER} ({model})", RERANKER_SERVE, LLAMA_SERVER_HINT)
