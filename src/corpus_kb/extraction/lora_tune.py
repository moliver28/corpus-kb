"""GLiNER LoRA adapter tuning (Living Corpus W5).

The real training path for ``extraction_tune(method="lora")``: a
GLiNER base model wrapped with a peft LoRA adapter (rank-r low-rank
side weights — the base stays frozen) trained on token-level ``ner``
spans mined from confirmed corpus evidence, saved as an adapter-only
artifact (``adapter_model.pt`` + ``adapter_config.json``).

All heavy imports are lazy and gated: without the ``tune`` extra
(peft + gliner[training]) the op raises DependencyMissingError — a
typed taxonomy error, never a silent fallback. The trained artifact is
a CANDIDATE: it reaches live extraction only through the W7 eval
promotion gate (micro F1 at or above 0.80 and at least baseline+0.02),
so a bad adapter can never silently degrade extraction.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..errors import DependencyMissingError, ValidationError

DEFAULT_TUNE_MODEL = "knowledgator/gliner-multitask-large-v0.5"
TUNE_DEFAULTS: dict[str, Any] = {
    "model": DEFAULT_TUNE_MODEL,
    "steps": 200,
    "learning_rate": 5e-5,
    "batch_size": 2,
    "lora_r": 8,
    "lora_alpha": 16,
    "lora_dropout": 0.05,
}
MIN_TRAIN_EXAMPLES = 8


def tune_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Read ``extraction.tune`` with the shipped defaults merged in."""
    extraction = config.get("extraction")
    typed = extraction if isinstance(extraction, dict) else {}
    raw = typed.get("tune")
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(TUNE_DEFAULTS)
    for key in TUNE_DEFAULTS:
        if isinstance(raw.get(key), (str, int, float)) and not isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    settings["steps"] = int(settings["steps"])
    settings["batch_size"] = int(settings["batch_size"])
    settings["lora_r"] = int(settings["lora_r"])
    settings["lora_alpha"] = int(settings["lora_alpha"])
    settings["lora_dropout"] = float(settings["lora_dropout"])
    settings["learning_rate"] = float(settings["learning_rate"])
    return settings


def _imports() -> tuple[Any, Any, Any, Any]:
    """Lazy torch/peft/gliner import (the ``tune`` extra).

    String imports via importlib keep the module importable (and pyright
    clean) on hosts without the extra — the same pattern as
    ``_gliner_types.import_gliner``.
    """
    import importlib

    try:
        torch = importlib.import_module("torch")
        gliner = importlib.import_module("gliner")
        peft = importlib.import_module("peft")
    except ImportError as exc:
        raise DependencyMissingError(
            f"LoRA tuning needs the tune extra: pip install 'corpus-kb[tune]' ({exc}).",
            remediation="Install the tune extra, or use method=exemplar for profile seeds.",
        ) from exc
    return (
        torch,
        peft.LoraConfig,
        peft.inject_adapter_in_model,
        gliner.GLiNER,
    )


def _check_examples(examples: list[dict[str, Any]]) -> None:
    if len(examples) < MIN_TRAIN_EXAMPLES:
        raise ValidationError(
            f"LoRA tuning needs at least {MIN_TRAIN_EXAMPLES} examples, got {len(examples)}.",
            remediation="Ingest more confirmed content or raise the limit"
            " (min_confidence filters statement confidence).",
        )
    for example in examples:
        if (
            not isinstance(example, dict)
            or not isinstance(example.get("tokenized_text"), list)
            or not isinstance(example.get("ner"), list)
        ):
            raise ValidationError(
                "Training examples must hold tokenized_text and ner lists.",
                remediation="Re-run the mine step; tune_data.to_gliner_example builds the rows.",
            )


def run_lora_tune(
    examples: list[dict[str, Any]],
    config: dict[str, Any],
    output_dir: str,
) -> dict[str, Any]:
    """Train a LoRA adapter on GLiNER-format examples; returns the manifest."""
    _check_examples(examples)
    torch, peft_lora, inject_adapter_in_model, gliner_cls = _imports()
    settings = tune_settings(config)
    adapter_dir = Path(output_dir)
    adapter_dir.mkdir(parents=True, exist_ok=True)

    model = gliner_cls.from_pretrained(str(settings["model"]))
    lora = peft_lora(
        r=settings["lora_r"],
        lora_alpha=settings["lora_alpha"],
        lora_dropout=settings["lora_dropout"],
        target_modules="all-linear",
        bias="none",
    )
    model = inject_adapter_in_model(lora, model)

    started = time.monotonic()
    model.train_model(
        examples,
        output_dir=str(adapter_dir / "trainer"),
        max_steps=settings["steps"],
        learning_rate=settings["learning_rate"],
        per_device_train_batch_size=settings["batch_size"],
    )
    elapsed = round(time.monotonic() - started, 1)

    # Adapter-only artifact: base weights stay frozen, so the lora_ params
    # are the whole delta. The trainer checkpoints stay local; the adapter
    # is what ships (and what the W7 eval gate scores).
    lora_state = {
        name: value.detach().cpu() for name, value in model.state_dict().items() if "lora_" in name
    }
    torch.save(lora_state, adapter_dir / "adapter_model.pt")
    manifest = {
        "kind": "gliner_lora_adapter",
        "base_model": settings["model"],
        "lora_r": settings["lora_r"],
        "lora_alpha": settings["lora_alpha"],
        "lora_dropout": settings["lora_dropout"],
        "target_modules": "all-linear",
        "steps": settings["steps"],
        "examples": len(examples),
        "train_seconds": elapsed,
        "adapter_params": len(lora_state),
    }
    (adapter_dir / "adapter_config.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def apply_lora_adapter(model: Any, adapter_dir: str) -> Any:
    """Re-inject a saved adapter onto its base model and load the weights.

    Used by the eval gate (W7) to score the candidate adapter against
    the untouched baseline — never by live extraction before promotion.
    """
    torch, peft_lora, inject_adapter_in_model, _ = _imports()
    manifest = json.loads((Path(adapter_dir) / "adapter_config.json").read_text())
    lora = peft_lora(
        r=int(manifest["lora_r"]),
        lora_alpha=int(manifest["lora_alpha"]),
        lora_dropout=float(manifest["lora_dropout"]),
        target_modules=manifest.get("target_modules", "all-linear"),
        bias="none",
    )
    adapted = inject_adapter_in_model(lora, model)
    state = torch.load(
        Path(adapter_dir) / "adapter_model.pt", map_location="cpu", weights_only=True
    )
    adapted.load_state_dict(state, strict=False)
    return adapted
