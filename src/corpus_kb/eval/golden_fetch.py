"""Fetch the pinned golden textbooks for the bench (program W0).

The manifest, ``textbook_golden.json`` beside this module, pins three
OpenStax books under CC-BY-4.0: title, file name, download URL, sha256,
and license. ``python -m corpus_kb.eval.golden_fetch`` downloads each
book into ``~/.corpus-kb/textbook-golden`` (or the directory named by
``CORPUS_KB_TEXTBOOK_GOLDEN_DIR``) and checks the sha256. The PDFs never
enter the repository; the bench reads them from this directory, and
extra local books reach the bench through its ``--books-dir`` flag.

Each download is timeboxed. A failed or hash-mismatched book is
reported and makes the exit code nonzero; the bench still runs on any
books already on disk. Note for ``corpus-kb doctor``: this variable is
an eval-harness path, not a config override, so it is not in
``KNOWN_CORPUS_KB_ENV_VARS`` and doctor lists it as unmapped.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from corpus_kb.ingestion.strategies.textbook_cache import file_sha256

__all__ = ["GoldenBook", "default_dir", "fetch_book", "load_manifest", "main"]

MANIFEST_PATH = Path(__file__).with_name("textbook_golden.json")
DEFAULT_DIR = "~/.corpus-kb/textbook-golden"
DEFAULT_TIMEOUT_S = 900.0
ENV_DIR_VAR = "CORPUS_KB_TEXTBOOK_GOLDEN_DIR"


@dataclass(frozen=True)
class GoldenBook:
    """One pinned book: name on disk, download URL, and its sha256."""

    title: str
    filename: str
    source_url: str
    sha256: str
    license: str


#: Hard byte ceiling per download. The manifest pins exact hashes, so a
#: stream that grows past this bound is wrong (a redirect loop, a mirror
#: serving the wrong object); the partial file is deleted.
MAX_DOWNLOAD_BYTES = 1 << 30


def default_dir() -> Path:
    """The golden book directory: the env var, else the home default."""
    raw = os.environ.get(ENV_DIR_VAR) or DEFAULT_DIR
    return Path(raw).expanduser()


def load_manifest(path: Path = MANIFEST_PATH) -> list[GoldenBook]:
    """The pinned books from the manifest; a bad row is an honest error."""
    books = json.loads(path.read_text(encoding="utf-8")).get("books", [])
    rows: list[GoldenBook] = []
    keys = ("title", "filename", "source_url", "sha256", "license")
    for row in books:
        missing = [key for key in keys if not row.get(key)]
        if missing:
            raise ValueError(f"manifest row lacks keys: {', '.join(missing)}")
        rows.append(
            GoldenBook(
                title=row["title"],
                filename=row["filename"],
                source_url=row["source_url"],
                sha256=row["sha256"],
                license=row["license"],
            )
        )
    return rows


def fetch_book(book: GoldenBook, dest_dir: Path, timeout_s: float = DEFAULT_TIMEOUT_S) -> str:
    """One book to ``dest_dir``; the status string says what happened.

    ``downloaded`` means fresh bytes landed and the hash matches,
    ``cached`` means the file was already there with the pinned hash,
    ``mismatch`` means the served bytes broke the pin (the partial file
    is deleted), ``oversized`` means the stream passed the 1 GiB byte
    ceiling mid-download (the partial file is deleted), and ``failed``
    means the network step went wrong.
    """
    target = dest_dir / book.filename
    if target.exists() and file_sha256(target) == book.sha256:
        return "cached"
    part = target.with_name(f"{target.name}.{os.getpid()}.part")
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        oversized = False
        with (
            httpx.Client(follow_redirects=True, timeout=timeout_s) as client,
            client.stream("GET", book.source_url) as reply,
        ):
            reply.raise_for_status()
            with part.open("wb") as handle:
                received = 0
                for chunk in reply.iter_bytes():
                    received += len(chunk)
                    if received > MAX_DOWNLOAD_BYTES:
                        oversized = True
                        break
                    handle.write(chunk)
        if oversized:
            # Unlink only after the write handle closed; Windows cannot
            # delete an open file.
            part.unlink(missing_ok=True)
            return "oversized"
        if file_sha256(part) != book.sha256:
            part.unlink(missing_ok=True)
            return "mismatch"
    except (httpx.HTTPError, OSError):
        part.unlink(missing_ok=True)
        return "failed"
    part.replace(target)
    return "downloaded"


def main(argv: list[str] | None = None) -> int:
    """Download every pinned book; exit 0 only when all of them verify."""
    parser = argparse.ArgumentParser(description="Download the pinned golden textbooks.")
    parser.add_argument("--dir", dest="dir", default=None, help="Target directory for the books.")
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S, help="Seconds per download."
    )
    args = parser.parse_args(argv)
    dest = Path(args.dir).expanduser() if args.dir else default_dir()
    books = load_manifest()
    ok = True
    for book in books:
        status = fetch_book(book, dest, args.timeout)
        print(f"{book.title}: {status}")
        ok = ok and status in ("downloaded", "cached")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
