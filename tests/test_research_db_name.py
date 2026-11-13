"""Pin the throwaway research test-database default name (issue #327).

Two live pytest sessions used to share ONE fixed name; a starting session
force-dropped the other's database mid-run, and a finishing session's
teardown drop broke the survivor. The default name is now per-process, so
concurrent sessions never share. The explicit CORPUS_KB_TEST_DB override
still pins one shared name on purpose.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROBE = (
    f"import sys; sys.path.insert(0, r'{TESTS_DIR}'); "
    "import research_db; print(research_db.DB_NAME)"
)


def _db_name(env_extra: dict[str, str] | None = None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "CORPUS_KB_TEST_DB"}
    env.update(env_extra or {})
    result = subprocess.run(
        [sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def test_default_db_name_is_process_unique() -> None:
    first = _db_name()
    second = _db_name()
    assert first != second, "two fresh processes must not share the throwaway name"
    for name in (first, second):
        assert name.startswith("corpus_kb_t11_"), name
        assert name.endswith("_test"), name


def test_explicit_override_is_honored() -> None:
    assert _db_name({"CORPUS_KB_TEST_DB": "corpus_kb_shared_pin"}) == "corpus_kb_shared_pin"
