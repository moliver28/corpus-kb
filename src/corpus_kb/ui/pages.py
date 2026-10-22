"""The four browser UI pages (UX program Wave 3).

Thin view layer: every decision lives in ``view_models``; these
functions only arrange elements and render the strings the view models
shape. The NiceGUI namespace arrives as a ``UiNamespace`` Protocol
parameter, so this module never imports nicegui — the lazy import stays
in ``app.py`` (the only module that touches the extra).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import cast

from corpus_kb.ui.advisor_vm import advisor_markdown, method_advisor, split_csv
from corpus_kb.ui.demo_kb_vm import demo_kb_view
from corpus_kb.ui.health import check_line, overview_checks
from corpus_kb.ui.render import (
    demo_kb_markdown,
    library_markdown,
    receipt_line,
    sources_markdown,
    url_receipt_line,
)
from corpus_kb.ui.types import UiContext, UiNamespace, UploadEvent, get_ui_context
from corpus_kb.ui.view_models import (
    add_via_path,
    add_via_url,
    ask_view,
    demo_receipt,
    library_rows,
)

# Long demo narrations stay readable: the page shows the first N chars.
NARRATION_PREVIEW_CHARS = 4000


def register_pages(ui: UiNamespace) -> None:
    """Attach the four pages. Registration order is the nav order."""

    async def page_overview() -> None:
        """The health panel: the doctor's checks in plain language."""
        ctx = get_ui_context()
        ui.label("System health")
        for check in await overview_checks(ctx.config):
            ui.markdown(check_line(check))
        ui.markdown("Add files on the **Add** page. Ask questions on the **Ask** page.")

    async def page_add() -> None:
        """Drag-drop ingest with per-file progress, plus the narrated demo."""
        ctx = get_ui_context()
        ui.label("Add documents")
        url_box = ui.input("Page address (URL)")
        progress = ui.linear_progress(value=0.0, show_value=False)
        results = ui.column()

        async def handle_batch(events: object) -> None:
            uploads = cast(list[UploadEvent], events)
            progress.value = 0.0
            for index, upload in enumerate(uploads, start=1):
                receipt = await ingest_upload(upload, ctx)
                progress.value = index / max(len(uploads), 1)
                with results:
                    ui.markdown(receipt_line(upload.name, receipt))

        ui.upload(on_multi_upload=handle_batch, multiple=True, auto_upload=True)

        async def add_url() -> None:
            address = url_box.value.strip()
            if not address:
                ui.notify("Paste a page address first.")
                return
            receipt = await add_via_url(address, ctx.config, pool=ctx.pool)
            with results:
                ui.markdown(url_receipt_line(address, receipt))

        ui.button("Add page", on_click=add_url)

        async def run_demo() -> None:
            ui.notify("The demo runs the full pipeline. This can take minutes.")
            demo = await demo_receipt(ctx.config)
            with results:
                ui.markdown("Demo finished." if demo["ok"] else str(demo["message"]))
                narration = str(demo["narration"])
                if narration:
                    preview = narration[:NARRATION_PREVIEW_CHARS]
                    ui.markdown(f"```\n{preview}\n```")

        ui.button("Run the demo", on_click=run_demo)

        async def load_sample() -> None:
            """The general-RAG sample corpus, loaded through the CLI's loader."""
            ui.notify("Loading the sample knowledge corpus.")
            view = await demo_kb_view(ctx.config, ctx.pool)
            with results:
                ui.markdown(demo_kb_markdown(view))

        ui.button("Load sample knowledge", on_click=load_sample)

    async def page_ask() -> None:
        """Cited answers with the honest verification badge."""
        ctx = get_ui_context()
        ui.label("Ask your documents")
        box = ui.input("Your question")
        results = ui.column()

        async def run_ask() -> None:
            question = box.value.strip()
            if not question:
                ui.notify("Type a question first.")
                return
            ui.notify("Looking for evidence.")
            view = await ask_view(question, ctx.config, pg_pool=ctx.pool)
            results.clear()
            with results:
                badge = cast(dict[str, object], view["verify_badge"])
                ui.label(f"Verification: {badge['label']} ({badge['groundedness']:.2f})")
                if view["note"]:
                    ui.markdown(str(view["note"]))
                for sentence in cast(list[dict[str, object]], view["sentences"]):
                    ui.chat_message(str(sentence.get("text", "")), sent=False)
                sources = cast(list[dict[str, object]], view["sources"])
                if sources:
                    ui.markdown(sources_markdown(sources))

        ui.button("Ask", on_click=run_ask)

        # The method-advisor panel (the AMR program): one rank call,
        # strings shaped in advisor_vm, claims rendered verbatim.
        ui.label("Method advisor")
        ui.markdown("Rank the analysis methods for one task. Blank boxes are skipped.")
        task_box = ui.input("Analysis task")
        family_box = ui.input("Output family")
        inputs_box = ui.input("Inputs you have")
        tier_box = ui.input("Hardest tier")
        advisor_results = ui.column()

        async def run_advisor() -> None:
            view = await method_advisor(
                task_box.value,
                ctx.config,
                output_family=_optional(family_box.value),
                inputs_available=split_csv(inputs_box.value),
                max_complexity_tier=_optional(tier_box.value),
            )
            advisor_results.clear()
            with advisor_results:
                ui.markdown(advisor_markdown(view))

        ui.button("Rank methods", on_click=run_advisor)

    async def page_library() -> None:
        """Documents and discovered topics, read through the standard tools."""
        ctx = get_ui_context()
        ui.label("Your library")
        if ctx.pool is None:
            ui.markdown("The database is not connected. Start the server with `corpus-kb start`.")
            return
        try:
            data = await library_rows(ctx.config, ctx.pool)
        except Exception as exc:  # a failed read must not kill the page
            ui.markdown(f"The library could not load: {exc}")
            return
        ui.markdown(
            library_markdown(
                cast(list[dict[str, object]], data["documents"]),
                cast(list[dict[str, object]], data["topics"]),
            )
        )

    ui.page("/")(page_overview)
    ui.page("/add")(page_add)
    ui.page("/ask")(page_ask)
    ui.page("/library")(page_library)


def _optional(raw: str) -> str | None:
    """A box value that means "no filter" when blank."""
    value = raw.strip()
    return value or None


async def ingest_upload(upload: UploadEvent, ctx: UiContext) -> dict[str, object]:
    """Write one upload to a temp file (extension kept) and ingest it.

    The extension survives the temp-file hop so file-type detection still
    picks the right ingestion strategy.
    """
    suffix = Path(upload.name).suffix or ".bin"
    with tempfile.NamedTemporaryFile(
        prefix="corpus-kb-upload-", suffix=suffix, delete=False
    ) as tmp:
        tmp.write(upload.content.read())
        tmp_path = Path(tmp.name)
    try:
        return await add_via_path(str(tmp_path), ctx.config, pool=ctx.pool)
    finally:
        tmp_path.unlink(missing_ok=True)
