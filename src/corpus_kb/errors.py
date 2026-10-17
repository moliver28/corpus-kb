"""The Corpus-KB error taxonomy (zero-drift Wave 2A).

One failure class maps to ONE HTTP status, ONE JSON-RPC socket code, and
ONE CLI exit code (program standard 4). Raise a ``CorpusError`` subclass at
the failure site; every surface adapter reads the same triple — HTTP via
``api._body.error_response``, socket via ``socket_code``, CLI via
``cli_exit``.

Socket codes follow JSON-RPC 2.0:

  -32602  invalid params — the caller's input is wrong (validation, config)
  -32603  internal error — the server failed
  -32001..-32005  server-defined errors (JSON-RPC reserves -32000..-32099):
          -32001 not found, -32002 conflict, -32003 database unavailable,
          -32004 migration missing, -32005 dependency missing.

CLI exit codes KEEP the codes this repo already scripts against:

  1  bad input or crash — ``cli._cli_error_exit`` maps ValueError (unknown
     gate, malformed UUID) to 1; an uncaught traceback also exits 1.
  3  environment problem — ``cli_ingest`` documents EXIT_CONFIG=3 for "bad
     path, unknown --source-type, unreachable database, migration missing";
     promote exits 3 on ``duplicate_blocked`` (the conflict case).

The suggested 2/4 split would collide with live codes (2 = EXIT_FAILED
total ingest failure and cycle EXIT_UNAVAILABLE), so the existing
convention stands. Do not renumber: scripts depend on these values.

``ERROR_MAP`` also carries the plain exception class names the legacy HTTP
envelopes used as ``error_type`` strings, so old string payloads get honest
statuses with no change at the raise site.

All user-facing strings follow ASD-STE100: short active sentences, 20 words
or fewer.
"""

from __future__ import annotations

# error_type string -> (http_status, socket_code, cli_exit)
ERROR_MAP: dict[str, tuple[int, int, int]] = {
    # Taxonomy names.
    "config_error": (400, -32602, 3),
    "validation_error": (400, -32602, 1),
    "not_found": (404, -32001, 1),
    "conflict": (409, -32002, 3),
    "database_unavailable": (503, -32003, 3),
    "migration_missing": (503, -32004, 3),
    "dependency_missing": (503, -32005, 3),
    "internal_error": (500, -32603, 1),
    # Legacy ``type(exc).__name__`` strings seen in HTTP envelopes.
    "ValueError": (400, -32602, 1),
    "KeyError": (400, -32602, 1),
    "TypeError": (400, -32602, 1),
    "JSONDecodeError": (400, -32602, 1),
    "FileNotFoundError": (404, -32001, 1),
    "NotADirectoryError": (400, -32602, 3),
    "IsADirectoryError": (400, -32602, 3),
    "ConnectionError": (503, -32003, 3),
    "OSError": (503, -32003, 3),
    "TimeoutError": (503, -32003, 3),
    # asyncpg class names surfaced through legacy envelopes.
    "CannotConnectNowError": (503, -32003, 3),
    "ConnectionDoesNotExistError": (503, -32003, 3),
    "InvalidPasswordError": (503, -32003, 3),
    "TooManyConnectionsError": (503, -32003, 3),
    "InterfaceError": (503, -32003, 3),
    "UndefinedTableError": (503, -32004, 3),
    "UndefinedFunctionError": (503, -32004, 3),
}

DEFAULT_ERROR_TYPE = "internal_error"
DEFAULT_HTTP_STATUS = 500


def error_status(error_type: str) -> int:
    """The HTTP status for an ``error_type`` string; unknown names get 500."""
    entry = ERROR_MAP.get(error_type)
    return entry[0] if entry else DEFAULT_HTTP_STATUS


class CorpusError(Exception):
    """Base class for every typed Corpus-KB failure.

    Class attributes carry the surface contract; ``str(exc)`` returns the
    plain message. ``remediation`` names one fix the user can apply.
    """

    error_type: str = DEFAULT_ERROR_TYPE
    http_status: int = DEFAULT_HTTP_STATUS
    socket_code: int = -32603
    cli_exit: int = 1
    remediation: str = "Read the error message. Fix the input. Try again."

    def __init__(self, message: str, *, remediation: str | None = None) -> None:
        super().__init__(message)
        if remediation is not None:
            self.remediation = remediation


class ConfigError(CorpusError):
    """A required setting is missing or invalid."""

    error_type = "config_error"
    http_status, socket_code, cli_exit = ERROR_MAP["config_error"]
    remediation = "Set the missing setting in config.yaml. Then restart the server."


class ValidationError(CorpusError):
    """The request fields are malformed or out of range."""

    error_type = "validation_error"
    http_status, socket_code, cli_exit = ERROR_MAP["validation_error"]
    remediation = "Fix the request fields. Then send the request again."


class NotFoundError(CorpusError):
    """No object matches the given identifier."""

    error_type = "not_found"
    http_status, socket_code, cli_exit = ERROR_MAP["not_found"]
    remediation = "Check the identifier. List the documents to find a valid one."


class ConflictError(CorpusError):
    """The operation collides with existing state."""

    error_type = "conflict"
    http_status, socket_code, cli_exit = ERROR_MAP["conflict"]
    remediation = "Resolve the duplicate first. Then repeat the operation."


class DatabaseUnavailableError(CorpusError):
    """The database is not reachable."""

    error_type = "database_unavailable"
    http_status, socket_code, cli_exit = ERROR_MAP["database_unavailable"]
    remediation = "Run corpus-kb quickstart or corpus-kb setup. Then try again."


class MigrationMissingError(CorpusError):
    """The database schema misses a required migration."""

    error_type = "migration_missing"
    http_status, socket_code, cli_exit = ERROR_MAP["migration_missing"]
    remediation = "Apply the named migration. Run corpus-kb quickstart, then try again."


class DependencyMissingError(CorpusError):
    """An optional extra or system library is not installed."""

    error_type = "dependency_missing"
    http_status, socket_code, cli_exit = ERROR_MAP["dependency_missing"]
    remediation = "Install the named extra. Example: pip install '.[extra]'. Then try again."


class InternalError(CorpusError):
    """An unexpected server-side failure."""

    error_type = "internal_error"
    http_status, socket_code, cli_exit = ERROR_MAP["internal_error"]
    remediation = "Report this error to the maintainers. Include the message."
