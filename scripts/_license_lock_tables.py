"""Data tables for the runtime-dependency license lock.

Exclusion set, license-string normalization, license-text URLs, and the
platform-pin overrides. Kept apart from the resolver logic in
generate_license_lock.py so the tables are readable in one screen.

Canonical SPDX-like short names: the key is matched case-insensitively
after lowercasing and collapsing whitespace, so "MIT License", "mit",
"MIT license" all map to the same canonical "MIT" form. This keeps the
lock deterministic across wheels/installs that emit slightly different
strings for the same license.
"""

from __future__ import annotations


def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


EXCLUDED_PACKAGES = {
    _normalize(name)
    for name in {
        "corpus-kb",
        "python-magic-bin",
        "pywin32",
        # `colorama` is only pulled in on Windows (typer/tqdm/wasabi gate it
        # behind `platform_system == "Windows"` markers). Excluding it keeps
        # the lock identical whether it is generated on Windows or Linux.
        "colorama",
        # `tzdata` is only pulled in on Windows (psycopg gates it behind a
        # `platform_system == "Windows"` marker). Same cross-platform rule
        # as colorama: exclude so the lock is platform-independent.
        "tzdata",
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "syrupy",
        "coverage",
        # `pip` and `wheel` ship with the interpreter and aren't runtime
        # dependencies of the project. `setuptools` is intentionally kept
        # here because `llama-index-core>=0.11` declares a runtime
        # `setuptools>=80.9.0` requirement.
        "pip",
        "wheel",
    }
}

LICENSE_NORMALIZATION = {
    "mit": "MIT",
    "mit license": "MIT",
    "apache software license": "Apache-2.0",
    "apache license 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "apache-2.0 and mit": "Apache-2.0 AND MIT",
    "mit and psf-2.0": "MIT AND PSF-2.0",
    "apache-2.0 and cnri-python": "Apache-2.0 AND CNRI-Python",
    "apache-2.0 or bsd-2-clause": "Apache-2.0 OR BSD-2-Clause",
    "bsd license": "BSD-3-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "isc license (iscl)": "ISC",
    "isc": "ISC",
    "mozilla public license 2.0 (mpl 2.0)": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
    "mpl-2.0 and mit": "MPL-2.0 AND MIT",
    "mit-cmu": "MIT-CMU",
    "python software foundation license": "PSF-2.0",
    "psf-2.0": "PSF-2.0",
    "academic free license (afl)": "AFL-3.0",
    "historical permission notice and disclaimer (hpnd)": "HPND",
    "gnu library or lesser general public license (lgpl)": "LGPL",
}

LICENSE_TEXT_URLS = {
    "MIT": "https://opensource.org/licenses/MIT",
    "Apache-2.0": "https://opensource.org/licenses/Apache-2.0",
    "BSD-2-Clause": "https://opensource.org/licenses/BSD-2-Clause",
    "BSD-3-Clause": "https://opensource.org/licenses/BSD-3-Clause",
    "ISC": "https://opensource.org/licenses/ISC",
    "MPL-2.0": "https://opensource.org/licenses/MPL-2.0",
    "LGPL": "https://opensource.org/licenses/LGPL-3.0",
    "PSF-2.0": "https://docs.python.org/3/license.html#psf-license",
    "AFL-3.0": "https://opensource.org/licenses/AFL-3.0",
    "HPND": "https://opensource.org/licenses/HPND",
    "MIT-CMU": "https://github.com/python-pillow/Pillow/blob/main/LICENSE",
}

# Some distributions ship platform-specific wheels whose license metadata
# differs: llvmlite's Linux wheel declares a PEP 639 License-Expression
# ("BSD-2-Clause AND Apache-2.0 WITH LLVM-exception") while its Windows wheel
# only ships License-File entries, so the generic extractor reports UNKNOWN.
# Pin those to a canonical value so the lock is identical on every platform.
# tiktoken's only License metadata is the FULL MIT license TEXT (no
# expression, no classifier), which defeats string normalization; the text
# itself was hand-verified as MIT ("Copyright (c) 2022 OpenAI, Shantanu Jain").
LICENSE_OVERRIDES = {
    "llvmlite": "BSD-2-Clause AND Apache-2.0 WITH LLVM-exception",
    "tiktoken": "MIT",
}
