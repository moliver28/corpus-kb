#!/usr/bin/env python3
"""Generate the deterministic synthetic demo corpus (UI SPA Task 5a).

Writes ``--count`` small markdown documents built from fixed word lists.
The same ``--seed`` and ``--count`` always produce the same bytes: the
random generator is seeded, per-document, and no wall-clock value ever
enters the output. This is the demo's scale proof — 100 to 300 documents,
not 5 files.

The documents mirror the ``docs/demo-kb`` shapes: notes, FAQ, glossary,
and meeting records. Sentences are short plain English (ASD-STE100), so
the demo corpus also passes the string-audit pass.

Usage:
    python scripts/gen_demo_corpus.py [--count 150] [--seed 20261025]
                                      [--out docs/demo-corpus-synthetic]

The output folder is generated on demand, not committed. Re-run with the
same seed and count to reproduce it byte for byte; a smaller count leaves
the higher-numbered files in place, so delete the folder to start over.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from corpus_kb.utils.deterministic import HashRandom

DEFAULT_COUNT = 150
DEFAULT_SEED = 20261025
DEFAULT_OUT = "docs/demo-corpus-synthetic"
MIN_COUNT = 1
MAX_COUNT = 300

TOPICS = (
    "reciprocal rank fusion",
    "the citation index",
    "topic drift",
    "the embedding cache",
    "chunk overlap",
    "the review queue",
    "keyword synthesis",
    "the codebook",
    "conformal bounds",
    "the transcript pipeline",
    "zero-vector fallback",
    "the ingest ledger",
)

ROLES = ("Ana", "Ben", "Cleo", "Dev", "Esme", "Farid")

NOTE_SECTIONS = (
    "Why it matters",
    "How it works",
    "Where it helps",
    "What to watch",
    "First steps",
)

NOTE_LINES = (
    "The team keeps {topic} simple at first.",
    "A small test beats a long debate.",
    "The notes stay short so search finds them.",
    "{role} wrote the first draft in one afternoon.",
    "The pipeline reads the text and keeps the source.",
    "Every claim points back to a stored chunk.",
    "The chart shows steady gains after the change.",
    "{role} checks the receipt before the next run.",
    "A missed step shows up as an honest badge.",
    "The team reviews the results each Friday.",
)

FAQ_QUESTIONS = (
    "What does {topic} do?",
    "When does the team use {topic}?",
    "Who owns {topic} this quarter?",
    "How is {topic} tested?",
)

FAQ_ANSWERS = (
    "It ranks stored text and cites the winner.",
    "The team turns it on when volume grows.",
    "{role} owns it and reviews the logs weekly.",
    "A fixed sample set guards every release.",
    "It runs offline, so demos never need the internet.",
)

GLOSSARY_TERMS = (
    "Chunk",
    "Embedding",
    "Fusion",
    "Citation",
    "Receipt",
    "Degraded mode",
)

GLOSSARY_LINES = (
    "A small piece of stored text with one clear job.",
    "The system keeps the source next to every chunk.",
    "Search merges both scores and keeps the order stable.",
    "A badge shows honest state when a part is down.",
)

MEETING_DECISIONS = (
    "Ship {topic} behind a config flag.",
    "Keep the demo corpus fixed and reproducible.",
    "Review the degraded badges before each demo.",
    "Cap uploads at the value in the config file.",
    "Warm the embeddings before the room fills.",
)

MEETING_ACTIONS = (
    "{role} drafts the checklist.",
    "{role} times each demo step.",
    "{role} records the backup screen capture.",
    "{role} files one gap per demo run.",
)


def _pick(rng: HashRandom, items: tuple[str, ...]) -> str:
    return rng.choice(items)


def _sentences(rng: HashRandom, templates: tuple[str, ...], count: int) -> list[str]:
    lines: list[str] = []
    for _ in range(count):
        template = _pick(rng, templates)
        line = template.format(topic=_pick(rng, TOPICS), role=_pick(rng, ROLES))
        lines.append(line)
    return lines


def _title(rng: HashRandom, index: int) -> str:
    return f"{_pick(rng, TOPICS)} - note {index}"


def _note_doc(rng: HashRandom, index: int, count: int) -> str:
    parts = [f"# {_title(rng, index)}", ""]
    parts.append(_sentences(rng, NOTE_LINES, 1)[0])
    parts.append("")
    section_count = rng.randint(2, 3)
    for slot in range(section_count):
        parts.append(f"## {_pick(rng, NOTE_SECTIONS)} {slot + 1}")
        parts.append("")
        parts.extend(_sentences(rng, NOTE_LINES, rng.randint(2, 3)))
        parts.append("")
    parts.append(f"Synthetic demo document {index} of {count}.")
    parts.append("")
    return "\n".join(parts)


def _faq_doc(rng: HashRandom, index: int, count: int) -> str:
    parts = [f"# FAQ: {_pick(rng, TOPICS)}", ""]
    for _ in range(rng.randint(3, 4)):
        question = _pick(rng, FAQ_QUESTIONS).format(topic=_pick(rng, TOPICS))
        answer = _pick(rng, FAQ_ANSWERS).format(role=_pick(rng, ROLES))
        parts.append(f"## {question}")
        parts.append("")
        parts.append(answer)
        parts.append("")
    parts.append(f"Synthetic demo document {index} of {count}.")
    parts.append("")
    return "\n".join(parts)


def _glossary_doc(rng: HashRandom, index: int, count: int) -> str:
    parts = [f"# Glossary: {_pick(rng, TOPICS)}", ""]
    for _ in range(rng.randint(3, 5)):
        term = _pick(rng, GLOSSARY_TERMS)
        parts.append(f"## {term}")
        parts.append("")
        parts.append(_pick(rng, GLOSSARY_LINES))
        parts.append("")
    parts.append(f"Synthetic demo document {index} of {count}.")
    parts.append("")
    return "\n".join(parts)


def _meeting_doc(rng: HashRandom, index: int, count: int) -> str:
    parts = [f"# Meeting notes: {_pick(rng, TOPICS)}", ""]
    parts.append("## Decisions")
    parts.append("")
    for decision in _sentences(rng, MEETING_DECISIONS, rng.randint(2, 3)):
        parts.append(f"- {decision}")
    parts.append("")
    parts.append("## Actions")
    parts.append("")
    for action in _sentences(rng, MEETING_ACTIONS, rng.randint(2, 3)):
        parts.append(f"- {action}")
    parts.append("")
    parts.append(f"Synthetic demo document {index} of {count}.")
    parts.append("")
    return "\n".join(parts)


_BUILDERS = (_note_doc, _faq_doc, _glossary_doc, _meeting_doc)


def build_document(index: int, seed: int, count: int) -> tuple[str, str]:
    """One document's slug and markdown; a pure function of the inputs."""
    # str seed: version-2 seeding is stable across runs and Python versions.
    rng = HashRandom(f"{seed}:{index}")
    builder = _BUILDERS[index % len(_BUILDERS)]
    text = builder(rng, index, count)
    heading = text.splitlines()[0].removeprefix("# ")
    kind = builder.__name__.removeprefix("_").removesuffix("_doc")
    chars = []
    for char in f"{kind}-{heading}".lower():
        chars.append(char if char.isalnum() else "-")
    slug = f"synthetic-{index:04d}-{''.join(chars)}.md"
    return slug, text


def generate(out_dir: Path, count: int, seed: int) -> int:
    """Write every document; return the total bytes written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for index in range(MIN_COUNT, count + 1):
        slug, text = build_document(index, seed, count)
        path = out_dir / slug
        data = text.encode("utf-8")
        path.write_bytes(data)
        total += len(data)
    return total


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: parse flags, generate, print one receipt line."""
    parser = argparse.ArgumentParser(description="Generate the synthetic demo corpus.")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=str, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    if not MIN_COUNT <= args.count <= MAX_COUNT:
        parser.error(f"--count must be between {MIN_COUNT} and {MAX_COUNT}")
    out_dir = Path(args.out)
    total = generate(out_dir, args.count, args.seed)
    print(f"wrote {args.count} documents to {out_dir.as_posix()} ({total} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
