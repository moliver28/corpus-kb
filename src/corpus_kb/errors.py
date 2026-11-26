"""The Corpus-KB error taxonomy (zero-drift Wave 2A).

One failure class maps to ONE HTTP status, ONE JSON-RPC socket code, and
ONE CLI exit code (program standard 4); every surface adapter reads it.

Socket codes follow JSON-RPC 2.0: -32602 invalid params, -32603 internal,
plus server-defined -32001 not found, -32002 conflict, -32003 database
unavailable, -32004 migration missing, -32005 dependency missing, -32006
unknown name. CLI exits keep the scripted codes: 1 bad input, 3
environment problem. Do not renumber: scripts depend on these values.

``ERROR_MAP`` also carries the plain exception class names the legacy
HTTP envelopes used as ``error_type`` strings, so old payloads get
honest statuses. All user-facing strings follow ASD-STE100.
"""

from __future__ import annotations

# error_type string -> (http_status, socket_code, cli_exit)
ERROR_MAP: dict[str, tuple[int, int, int]] = {
    # Taxonomy names.
    "config_error": (400, -32602, 3),
    "validation_error": (400, -32602, 1),
    "question_too_short": (400, -32602, 1),
    "no_method_matches": (400, -32602, 1),
    "write_refused": (400, -32602, 1),
    "payload_too_large": (413, -32602, 1),
    "unsupported_media_type": (415, -32602, 1),
    "not_found": (404, -32001, 1),
    "conflict": (409, -32002, 3),
    "database_unavailable": (503, -32003, 3),
    "migration_missing": (503, -32004, 3),
    "dependency_missing": (503, -32005, 3),
    # W5-a: store_newer = conflict triple; package_not_published = dependency triple.
    "store_newer": (409, -32002, 3),
    "package_not_published": (503, -32005, 3),
    "project_not_found": (404, -32001, 1),
    "source_type_unknown": (400, -32006, 1),
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
    # unstructured raises this when no parser matches the input file.
    "UnsupportedFileFormatError": (415, -32602, 1),
    # asyncpg class names surfaced through legacy envelopes.
    "CannotConnectNowError": (503, -32003, 3),
    "ConnectionDoesNotExistError": (503, -32003, 3),
    "InvalidPasswordError": (503, -32003, 3),
    "TooManyConnectionsError": (503, -32003, 3),
    "InterfaceError": (503, -32003, 3),
    "UndefinedTableError": (503, -32004, 3),
    "UndefinedFunctionError": (503, -32004, 3),
    # duckdb class names surfaced through legacy envelopes. The DuckDB
    # graph store translates its failures at the store boundary (lock and
    # catalog errors become typed taxonomy errors); these per-class rows
    # cover the raw names that can still escape (file locks, write-write
    # conflicts, malformed values or SQL, constraint collisions).
    "IOException": (503, -32003, 3),
    "ConnectionException": (503, -32003, 3),
    "TransactionException": (503, -32003, 3),
    "PermissionException": (503, -32003, 3),
    "CatalogException": (503, -32004, 3),
    "NotImplementedException": (503, -32005, 3),
    "InvalidInputException": (400, -32602, 1),
    "ConversionException": (400, -32602, 1),
    "ParserException": (400, -32602, 1),
    "SyntaxException": (400, -32602, 1),
    "ConstraintException": (409, -32002, 3),
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


class QuestionTooShortError(CorpusError):
    """The question is too short to route to a method."""

    error_type = "question_too_short"
    http_status, socket_code, cli_exit = ERROR_MAP["question_too_short"]
    remediation = "Type the full question. Then send it again."


class NoMethodMatchesError(CorpusError):
    """No register method matches the question."""

    error_type = "no_method_matches"
    http_status, socket_code, cli_exit = ERROR_MAP["no_method_matches"]
    remediation = "Try other words, or pick a method with analysis-select-method."


class WriteRefusedError(CorpusError):
    """The statement is not a read, or the call carries more than one.

    Raised by the SQL query route's statement gate (issue #349) before any
    connection opens, so a refused statement never changes a row.
    """

    error_type = "write_refused"
    http_status, socket_code, cli_exit = ERROR_MAP["write_refused"]
    remediation = "Send one SELECT or WITH statement. The route cannot change data."


class PayloadTooLargeError(CorpusError):
    """The uploaded file is over the configured size cap."""

    error_type = "payload_too_large"
    http_status, socket_code, cli_exit = ERROR_MAP["payload_too_large"]
    remediation = "Upload a smaller file, or raise ui.spa.max_upload_mb in config.yaml."


class UnsupportedMediaTypeError(CorpusError):
    """The request body is not a multipart form upload."""

    error_type = "unsupported_media_type"
    http_status, socket_code, cli_exit = ERROR_MAP["unsupported_media_type"]
    remediation = "Send the file as a multipart form field named file."


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


class StoreNewerError(CorpusError):
    """The store schema is newer than this code knows (W5-a boot guard)."""

    error_type = "store_newer"
    http_status, socket_code, cli_exit = ERROR_MAP["store_newer"]
    remediation = (
        "Update this app to the version that owns those migrations. Or restore the store backup."
    )


class PackageNotPublishedError(CorpusError):
    """The package is not on PyPI yet, so the upgrade step cannot run."""

    error_type = "package_not_published"
    http_status, socket_code, cli_exit = ERROR_MAP["package_not_published"]
    remediation = "Install from the git checkout instead. Or wait for the PyPI release."


class ProjectNotFoundError(CorpusError):
    """No project matches the given slug or id."""

    error_type = "project_not_found"
    http_status, socket_code, cli_exit = ERROR_MAP["project_not_found"]
    remediation = "Check the slug. List the projects to find a valid one."


class SourceTypeUnknownError(CorpusError):
    """No source type matches the given name."""

    error_type = "source_type_unknown"
    http_status, socket_code, cli_exit = ERROR_MAP["source_type_unknown"]
    remediation = (
        "Use a registered source type. See the typed source registry table in docs/INGESTION.md."
    )


class InternalError(CorpusError):
    """An unexpected server-side failure."""

    error_type = "internal_error"
    http_status, socket_code, cli_exit = ERROR_MAP["internal_error"]
    remediation = "Report this error to the maintainers. Include the message."
