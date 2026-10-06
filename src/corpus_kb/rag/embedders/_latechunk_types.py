"""Typed Protocol wrappers for the optional late-chunking dependency stack.

House pattern (``extraction/_langextract_types.py``): NO bare
``import sentence_transformers`` / ``import transformers`` / ``import torch``
anywhere in the package — a bare import, even guarded by try/except, trips
pyright ``reportMissingImports=error`` in the extras-less CI type-check job.
Every access goes through ``importlib.import_module`` + ``cast`` onto the
Protocols below, which declare exactly the members we use.

Importing this module is SAFE without the extra: nothing heavy loads until
one of the ``import_*`` functions runs (and those raise
:class:`LateChunkDependencyError` with an install hint when absent).
"""

from __future__ import annotations

import importlib
from types import TracebackType
from typing import Protocol, cast, runtime_checkable


class LateChunkDependencyError(RuntimeError):
    """Raised when the ``latechunk`` optional extra is not installed."""


_INSTALL_HINT = (
    "the latechunk extra is not installed — run "
    '`pip install -e ".[latechunk]"` (sentence-transformers + transformers + torch)'
)


def import_or_raise(module: str) -> object:
    """importlib.import_module with the house install hint on failure."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        raise LateChunkDependencyError(_INSTALL_HINT) from exc


@runtime_checkable
class Tensor1D(Protocol):
    """1-D tensor view: the pooled-span result."""

    def tolist(self) -> list[float]: ...


@runtime_checkable
class Tensor2D(Protocol):
    """2-D tensor view (rows x hidden): ST encode output / span windows."""

    def mean(self, dim: int, keepdim: bool = ...) -> Tensor1D: ...

    def tolist(self) -> list[list[float]]: ...


@runtime_checkable
class TokenStates(Protocol):
    """(batch=1, seq_len, hidden) late-chunking token hidden states."""

    def __getitem__(self, key: tuple[int, slice, slice]) -> Tensor2D: ...


@runtime_checkable
class AutoModelOutput(Protocol):
    """HF base-model forward output: token-level hidden states."""

    last_hidden_state: TokenStates


@runtime_checkable
class IntTensor2D(Protocol):
    """(batch, seq_len) integer ids/mask tensor."""

    def tolist(self) -> list[list[int]]: ...


@runtime_checkable
class BatchEncoding(Protocol):
    """HF tokenizer output: dict-like of int tensors."""

    def __getitem__(self, key: str) -> IntTensor2D: ...


@runtime_checkable
class TokenizerLike(Protocol):
    """HF tokenizer surface used by late chunking."""

    def __call__(
        self,
        texts: list[str],
        *,
        return_tensors: str,
        padding: bool,
        truncation: bool,
        max_length: int | None = ...,
        add_special_tokens: bool = ...,
    ) -> BatchEncoding: ...

    def convert_ids_to_tokens(
        self, ids: list[int], skip_special_tokens: bool = ...
    ) -> list[str]: ...


@runtime_checkable
class AutoTokenizerType(Protocol):
    def from_pretrained(self, name: str, **kwargs: object) -> TokenizerLike: ...


@runtime_checkable
class AutoModelLike(Protocol):
    """HF base model: eval mode + forward over ids/mask tensors."""

    def eval(self) -> AutoModelLike: ...

    def __call__(
        self, *, input_ids: IntTensor2D, attention_mask: IntTensor2D
    ) -> AutoModelOutput: ...


@runtime_checkable
class AutoModelType(Protocol):
    def from_pretrained(self, name: str, **kwargs: object) -> AutoModelLike: ...


@runtime_checkable
class STModelLike(Protocol):
    """sentence-transformers pooled-embedding surface (naive arm)."""

    def encode(
        self,
        sentences: str | list[str],
        *,
        batch_size: int = ...,
        normalize_embeddings: bool = ...,
        show_progress_bar: bool = ...,
    ) -> Tensor2D: ...

    @property
    def max_seq_length(self) -> int: ...

    def get_sentence_embedding_dimension(self) -> int | None: ...


class TransformersModule(Protocol):
    """``transformers`` module surface used by late chunking."""

    AutoTokenizer: AutoTokenizerType
    AutoModel: AutoModelType


class SentenceTransformersModule(Protocol):
    """``sentence_transformers`` module surface used by the naive arm."""

    def SentenceTransformer(self, name: str, **kwargs: object) -> STModelLike: ...  # noqa: N802


class TorchModule(Protocol):
    """``torch`` module surface used by late chunking."""

    def no_grad(self) -> AbstractNoGrad: ...


class AbstractNoGrad(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None: ...


def import_transformers() -> TransformersModule:
    """Lazily import ``transformers`` and cast to the typed Protocol."""
    return cast(TransformersModule, import_or_raise("transformers"))


def import_sentence_transformers() -> SentenceTransformersModule:
    """Lazily import ``sentence_transformers`` and cast to the typed Protocol."""
    return cast(SentenceTransformersModule, import_or_raise("sentence_transformers"))


def import_torch() -> TorchModule:
    """Lazily import ``torch`` and cast to the typed Protocol."""
    return cast(TorchModule, import_or_raise("torch"))
