"""Assembled default config (W5-1 split).

``DEFAULT_CONFIG`` merges the two defaults halves: ``_CORE_CONFIG`` and
``_FEATURE_CONFIG`` (the ``master`` block). ``get_default_config`` in
``config.py`` deep-copies this tree and then applies the typed module
defaults. The lockstep gate: ``tests/test_config_lockstep.py`` pins the
packaged ``config.yaml`` to these leaves one for one.
"""

from __future__ import annotations

from corpus_kb.config_defaults_core import _CORE_CONFIG
from corpus_kb.config_defaults_features import _FEATURE_CONFIG

DEFAULT_CONFIG: dict[str, object] = {**_CORE_CONFIG, **_FEATURE_CONFIG}
