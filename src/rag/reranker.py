"""Result reranking — optional cross-encoder and score normalization.

Supports two modes:
1. **Identity** (default): Pass-through — already ranked by RRF from hybrid search.
2. **LLM-based**: Uses the LLM via Ollama to rerank top-N results by relevance.
   This is the "lm-format-enumeration" pattern: present candidates to the model
   and ask it to rank them.
"""

from __future__ import annotations

import json
import re
from typing import Optional

from utils.models import SearchResult


class Reranker:
    """Reranks search results for improved relevance.

    Mode "identity": returns results as-is (fast, already fused by RRF).
    Mode "llm": uses Ollama to semantically rerank the top candidates.
    """

    def __init__(self, mode: str = "identity", top_k: int = 5):
        self.mode = mode
        self.top_k = top_k

    def rerank(
        self,
        query: str,
        results: list[SearchResult],
        k: int = 10,
    ) -> list[SearchResult]:
        """Rerank results according to configured mode."""
        if not results:
            return results

        if self.mode == "identity":
            return results[:k]

        if self.mode == "llm":
            return self._llm_rerank(query, results, k)

        # Unknown mode — fall back to identity
        return results[:k]

    def _llm_rerank(
        self,
        query: str,
        results: list[SearchResult],
        k: int,
    ) -> list[SearchResult]:
        """Rerank using Ollama to evaluate relevance of each result.

        Only reranks the top self.top_k results to limit cost/latency.
        """
        candidates = results[:self.top_k]
        if len(candidates) <= 1:
            return results[:k]

        try:
            import ollama

            # Build a numbered list of candidates
            lines = []
            for i, r in enumerate(candidates):
                snippet = r.text[:200].replace("\n", " ")
                lines.append(f"{i + 1}. {snippet}")

            prompt = (
                f"Query: {query}\n\n"
                f"Candidates:\n" + "\n".join(lines) + "\n\n"
                f"Rank these candidates from MOST relevant (1) to LEAST relevant "
                f"({len(candidates)}) to the query. Return ONLY a JSON array of "
                f"the candidate numbers in ranked order, like: [3, 1, 2, ...]"
            )

            resp = ollama.generate(
                model="nomic-embed-text",  # lightweight — swap for llama3.2 if available
                prompt=prompt,
            )

            order = self._parse_ranked_order(resp.response, len(candidates))

            reranked = [candidates[i - 1] for i in order if 1 <= i <= len(candidates)]
            reranked.extend(r for r in candidates if r not in reranked)

            return reranked[:k]

        except Exception:
            # Fall through to identity reranking on error
            return results[:k]

    def _parse_ranked_order(self, text: str, count: int) -> list[int]:
        """Parse a JSON array of ranked indices from LLM output.

        Falls back to original order if parsing fails.
        """
        # Try to extract JSON array
        match = re.search(r"\[[\d,\s]+\]", text)
        if match:
            try:
                order = json.loads(match.group())
                if isinstance(order, list) and all(isinstance(x, int) for x in order):
                    return order
            except (json.JSONDecodeError, ValueError):
                pass

        # Fallback: look for space-separated numbers
        nums = [int(x) for x in text.split() if x.isdigit() and 1 <= int(x) <= count]
        if len(nums) == count:
            return nums

        # Default: original order
        return list(range(1, count + 1))
