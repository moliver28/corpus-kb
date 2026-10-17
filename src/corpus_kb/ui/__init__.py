"""The first-party browser UI (UX program Wave 3).

Four NiceGUI pages — Overview, Add, Ask, Library — mounted into the
running HTTP server by :func:`corpus_kb.ui.app.build_ui` (``ui.run_with``;
no new daemon, no new port). NiceGUI itself is the ``[ui]`` optional
extra and is imported lazily in ``app.py`` only; every other module in
this package stays stdlib-plus-core so extras-less CI never touches it.
"""

from __future__ import annotations
