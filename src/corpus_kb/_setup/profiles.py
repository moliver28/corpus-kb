"""Hardware profile detection for the installer (zero-drift split).

``install.py`` keeps a ``detect_profile()`` wrapper that passes THIS
module's patchable GPU probe through, so tests that patch
``install_module._detect_gpu_vram_gb`` keep working after the split.
"""

from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from typing import Any

import psutil


def _detect_gpu_vram_gb() -> tuple[float, bool]:
    """Return (total GPU VRAM in GB, detected); (0.0, False) if pynvml is unavailable.

    The bool distinguishes "genuinely zero VRAM" from "no NVIDIA detection",
    so the doctor report can say VRAM: unknown instead of the false "0.0 GB"
    on GPU hosts without pynvml (review F-5).
    """
    try:
        import pynvml  # type: ignore[import-not-found]

        pynvml.nvmlInit()
        device_count = pynvml.nvmlDeviceGetCount()
        total_bytes = 0
        for i in range(device_count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            total_bytes += int(mem_info.total)
        pynvml.nvmlShutdown()
        return total_bytes / (1024**3), True
    except Exception:
        return 0.0, False


def detect_profile(gpu_detector: Callable[[], tuple[float, bool]]) -> dict[str, Any]:
    """Detect hardware profile and return installer profile name + details.

    ``gpu_detector`` is injected by the caller (install.py passes its own
    module global so test patches stay effective).
    """
    ram_gb = psutil.virtual_memory().total / (1024**3)
    vram_gb, vram_detected = gpu_detector()
    cpu_cores = psutil.cpu_count(logical=True) or 1

    if ram_gb > 16 or vram_gb >= 8:
        profile = "performance"
    elif ram_gb >= 8 or vram_gb >= 4:
        profile = "balanced"
    else:
        profile = "minimal"

    return {
        "profile": profile,
        "ram_gb": round(ram_gb, 1),
        "vram_gb": round(vram_gb, 1),
        "vram_detected": vram_detected,
        "cpu_cores": cpu_cores,
        "os": platform.system(),
        "python": sys.version.split()[0],
    }


def load_installer_profiles(config: dict[str, Any]) -> dict[str, Any]:
    """Load installer profiles from config, with safe defaults."""
    installer = config.get("installer", {})
    profiles = installer.get("profiles", {})
    if not isinstance(profiles, dict) or not profiles:
        profiles = {
            # Schema-coherent: all three profiles embed at 2560 dims (the
            # halfvec(2560) schema width); only the LLM varies. A different
            # embedding width needs a schema migration first.
            "minimal": {
                "ram_gb_max": 8,
                "vram_gb_max": 0,
                "model": "qwen3-embedding:4b",
                "llm": "qwen3:0.6b",
            },
            "balanced": {
                "ram_gb_max": 16,
                "vram_gb_max": 4,
                "model": "qwen3-embedding:4b",
                "llm": "qwen3:4b",
            },
            "performance": {
                "ram_gb_max": 999,
                "vram_gb_max": 8,
                "model": "qwen3-embedding:4b",
                "llm": "qwen3:14b",
            },
        }
    return profiles
