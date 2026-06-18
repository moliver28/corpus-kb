"""Abstract chunker interface and Chunk data class."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from utils.models import Chunk


class Chunker(ABC):
    """Abstract base for all chunking strategies."""

    @abstractmethod
    def chunk(self, text: str, file_path: Optional[str] = None) -> list[Chunk]:
        """Split text into semantically coherent chunks.

        Args:
            text: The full document text.
            file_path: Optional file path for metadata extraction.

        Returns:
            List of Chunk objects with hierarchy metadata populated.
        """
        ...
