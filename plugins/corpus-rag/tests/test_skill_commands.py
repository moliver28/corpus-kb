"""Every ``corpus-rag ...`` string in every SKILL.md must parse (issue #354).

A skill that names a command the parser does not know, or misspells a
flag, sends the agent into a dead end. This test extracts each command
string from every skill and runs it through the real parser.

The contract for skill authors (what this test accepts):

- One command per line, starting at the word ``corpus-rag``.
- A closing backtick, a ``#`` comment, or a new sentence ends the string.
- ``[optional parts]`` may drop the brackets; ``...`` means "more pairs".
- ``<placeholders>`` are swapped for dummy values before parsing.

Frontmatter names must match their folder, so the loader finds each skill.
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))

import cli_spec  # noqa: E402

SKILL_FILES = sorted((PLUGIN / "skills").glob("*/SKILL.md"))

_INT_NAMES = {"n", "num", "k", "count", "limit", "min", "seconds"}
_SLUG_NAMES = {"slug", "study", "study-slug", "name", "file-name"}


def _dummy_for(placeholder: str) -> str:
    name = placeholder.strip().lower()
    if name in _INT_NAMES:
        return "5"
    if name in _SLUG_NAMES:
        return "alpha"
    if name in {"url", "web"}:
        return "https://example.com/page"
    if name in {"file", "path", "cells", "labels", "codebook", "json"}:
        return "notes.txt"
    return "words"


def _extract_command(line: str) -> str | None:
    """The ``corpus-rag ...`` string on this line, or None."""
    marker = line.find("corpus-rag")
    if marker < 0:
        return None
    text = line[marker + len("corpus-rag"):]
    text = text.split("`")[0]           # an inline-code span ends the command
    text = re.sub(r"\s+#.*$", "", text)  # an inline comment ends the command
    text = re.sub(r"\.\s.*$", "", text)  # a new sentence ends the command
    text = re.sub(r"\.$", "", text)
    text = text.strip()
    return text or None


def _normalise(command: str) -> str:
    text = command.replace("[", " ").replace("]", " ")
    text = re.sub(r"(?<!\S)\.\.\.(?!\S)", " ", text)  # a bare ... means "and so on"
    text = re.sub(r"\bN\b", "5", text)                # a bare N fills an int flag
    text = re.sub(r"<([^<>]+)>", lambda m: _dummy_for(m.group(1)), text)
    return text.strip()


def _tokens(command: str) -> list[str]:
    return shlex.split(_normalise(command))


class _ParseError(Exception):
    """Captures the parser's own error message instead of exiting."""


def _hook_errors(parser: argparse.ArgumentParser, restore: list) -> None:
    """Route every parser in the tree (subparsers included) to _ParseError."""
    original = parser.error

    def fail(message: str) -> None:
        raise _ParseError(message)

    parser.error = fail  # type: ignore[method-assign]
    restore.append((parser, original))
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice in action.choices.values():
                _hook_errors(choice, restore)


def _parse(parser: argparse.ArgumentParser, tokens: list[str]) -> None:
    """Parse, or raise _ParseError with the parser's error message."""
    restore: list = []
    _hook_errors(parser, restore)
    try:
        parser.parse_args(tokens)
    finally:
        for node, original in restore:
            node.error = original  # type: ignore[method-assign]


def _subcommand_exists(parser: argparse.ArgumentParser, tokens: list[str]) -> bool:
    """True when the token path names a real subcommand chain."""
    node = parser
    for token in tokens:
        choices = None
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                choices = action.choices
                break
        if not choices or token not in choices:
            return False
        node = choices[token]
    return True


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda p: p.parent.name)
def test_every_command_string_parses(skill_file: Path) -> None:
    failures: list[str] = []
    for line in skill_file.read_text(encoding="utf-8").splitlines():
        command = _extract_command(line)
        if command is None:
            continue
        tokens = _tokens(command)
        parser = cli_spec.build_parser()
        normalized = cli_spec._op_normalized(tokens)
        try:
            _parse(parser, tokens if normalized is None else normalized)
            continue
        except SystemExit:
            failures.append(f"{command!r} (line: {line.strip()!r})")
            continue
        except _ParseError as exc:
            message = str(exc)
        # A bare mention (no arguments) is fine when the command exists
        # and the only complaint is missing required arguments.
        missing_only = "arguments are required" in message or "required:" in message
        if missing_only and _subcommand_exists(parser, tokens):
            continue
        failures.append(f"{command!r} (line: {line.strip()!r})")
    assert not failures, (
        f"{skill_file.parent.name}/SKILL.md names commands the parser "
        f"rejects: {failures}"
    )


def test_op_pairs_may_follow_the_study_flag() -> None:
    """`op <name> -p <slug> key=value` is the documented form and must parse."""
    args = cli_spec.parse_argv(["op", "ingest_url", "-p", "alpha", "url=https://example.com"])
    assert args.name == "ingest_url"
    assert args.study_slug == "alpha"
    assert args.pairs == ["url=https://example.com"]
    reordered = cli_spec.parse_argv(["op", "ingest_url", "url=https://example.com", "-p", "alpha"])
    assert reordered.pairs == ["url=https://example.com"]
    assert reordered.study_slug == "alpha"


@pytest.mark.parametrize("skill_file", SKILL_FILES, ids=lambda p: p.parent.name)
def test_frontmatter_name_matches_folder(skill_file: Path) -> None:
    head = skill_file.read_text(encoding="utf-8").split("---", 2)[1]
    match = re.search(r"^name:\s*(\S+)\s*$", head, re.MULTILINE)
    assert match, f"{skill_file} frontmatter has no name"
    assert match.group(1) == skill_file.parent.name


def test_the_four_guided_skills_exist() -> None:
    names = {path.parent.name for path in SKILL_FILES}
    expected = {
        "corpus-research",
        "corpus-codify-study",
        "corpus-codify-code",
        "corpus-codify-export",
    }
    assert expected <= names


CAUTION_SKILLS = (
    "corpus-research",
    "corpus-codify-study",
    "corpus-codify-code",
    "corpus-codify-export",
)


def test_epistemological_caution_is_verbatim_everywhere() -> None:
    """The caution blockquote is one text, shown beside every agreement number."""
    reference = PLUGIN / "references" / "epistemological-caution.md"
    match = re.search(
        r"^> \*\*Caution:\*\* .*$",
        reference.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, "the caution reference must hold the canonical blockquote"
    canonical = match.group(0)
    for skill in CAUTION_SKILLS:
        text = (PLUGIN / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.count(canonical) == 1, (
            f"{skill}/SKILL.md must show the caution blockquote verbatim, once"
        )
    import codify_commands

    assert canonical.lstrip("> ") == codify_commands.CAUTION, (
        "the reliability command prints the same caution, without the mark"
    )
