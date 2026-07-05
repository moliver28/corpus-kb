"""Typed Protocol wrapper around the lancedb package.

LanceDB ships without complete pyright-compatible stubs. This module exposes
the tiny surface we need via hand-typed Protocols so the rest of the codebase
stays strict-mode clean.
"""

from __future__ import annotations

import importlib
from typing import Protocol, cast

import pyarrow as pa


class LanceDBQueryBuilder(Protocol):
    """LanceDB query builder for vector search."""

    def limit(self, k: int) -> "LanceDBQueryBuilder": ...
    def to_arrow(self) -> pa.Table: ...


class LanceDBTable(Protocol):
    """A LanceDB table exposing the operations we use."""

    schema: pa.Schema

    def add(self, data: list[dict[str, object]]) -> object: ...
    def search(self, query: list[float]) -> LanceDBQueryBuilder: ...
    def count_rows(self) -> int: ...


class _TableListPage(Protocol):
    """Page object returned by LanceDBConnection.list_tables."""

    tables: list[str]


class LanceDBConnection(Protocol):
    """A LanceDB database connection."""

    def list_tables(self) -> _TableListPage: ...
    def open_table(self, name: str) -> LanceDBTable: ...
    def create_table(self, name: str, *, schema: pa.Schema) -> LanceDBTable: ...
    def drop_table(self, name: str) -> None: ...


class LanceDBConnectionFactory(Protocol):
    """The top-level lancedb module as a typed factory."""

    def connect(self, uri: str) -> LanceDBConnection: ...


def connect(uri: str) -> LanceDBConnection:
    """Open a typed LanceDB connection.

    Args:
        uri: LanceDB URI (directory path).

    Returns:
        A connection object typed to our minimal Protocol.
    """
    module = importlib.import_module("lancedb")
    factory = cast(LanceDBConnectionFactory, module)
    return factory.connect(uri)
