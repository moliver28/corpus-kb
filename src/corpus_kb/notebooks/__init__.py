"""The marimo notebooks console (UX program Wave 6).

Two modules:

- ``settings``: the typed ``notebooks.*`` config reads (the house
  three-ways pattern: this module, ``config.get_default_config``, and
  both shipped YAML files stay in lockstep).
- ``console``: the lazy ``[notebook]`` extra seam and the ``/notebooks``
  ASGI mount on the running HTTP server. marimo is never imported at
  module scope, so the extras-less runtime (and CI) never touches it.
"""

from __future__ import annotations
