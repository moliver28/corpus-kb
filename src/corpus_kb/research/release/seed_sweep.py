"""U44 deterministic clustering evidence — content-hashed matrix + seed sweep.

The inductive run's RELEASED clustering comes from the PERSISTED embedding
matrix only: this module content-hashes that matrix (float32 bytes behind a
shape/dtype header), clusters from it with fixed seeds, and records seed,
params, and library versions for the manifest. The >=10-seed sweep is
EVIDENCE, not the released artifact: the single deterministic run is what
ships; the sweep proves the pipeline is not seed-sensitive. ``not_converged``
when cluster-count variance exceeds the admin-supplied threshold.

NOTE (documented pin): fixing UMAP ``random_state`` DISABLES UMAP's
parallelism — a seeded fit is single-threaded and slower by design. Do not
"fix" the slowness by unsetting the seed; that is the exact nondeterminism
this module exists to prevent (unseeded UMAP gave 4 topics ten times and 24
on the eleventh in a public report).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from corpus_kb.research.cluster_stability import ari

CLUSTER_LIBRARIES: tuple[str, ...] = ("numpy", "umap-learn", "hdbscan", "scikit-learn")


def content_hash_matrix(matrix: Sequence[Sequence[float]]) -> str:
    """SHA-256 over the matrix's float32 bytes (shape/dtype header first).

    Row order is SIGNIFICANT (labels align by index), so no sorting happens
    here: two matrices hash identically iff their bytes are identical.
    """
    arr = np.ascontiguousarray(np.asarray(matrix, dtype=np.float32))
    header = f"float32|{arr.shape[0]}x{arr.shape[1]}|".encode("ascii")
    return hashlib.sha256(header + arr.tobytes()).hexdigest()


def library_versions(names: Sequence[str] = CLUSTER_LIBRARIES) -> dict[str, str | None]:
    """Installed versions by distribution name; ``None`` when absent."""
    from importlib.metadata import PackageNotFoundError, version

    versions: dict[str, str | None] = {}
    for name in names:
        try:
            versions[name] = version(name)
        except PackageNotFoundError:
            versions[name] = None
    return versions


def clustering_provenance(
    seed: int,
    params: dict[str, object],
    *,
    transform_seed: int | None = None,
    libraries: dict[str, str | None] | None = None,
) -> dict[str, object]:
    """The manifest's clustering_determinism block (without matrix/sweep)."""
    return {
        "seed": int(seed),
        "transform_seed": transform_seed if transform_seed is not None else int(seed),
        "params": dict(params),
        "library_versions": libraries if libraries is not None else library_versions(),
        "umap_parallelism": "disabled by fixed random_state (documented)",
    }


def nmi(labels_a: Sequence[object], labels_b: Sequence[object]) -> float:
    """Normalized Mutual Information (arithmetic-mean normalization).

    Contingency form, numpy only; matches :func:`cluster_stability.ari`'s
    noise convention (label -1 is its own group). 1.0 for identical
    partitions, ~0.0 for independent ones.
    """
    if len(labels_a) != len(labels_b):
        raise ValueError("label vectors must align")
    n = len(labels_a)
    if n < 2:
        return 1.0
    a = np.asarray([str(x) for x in labels_a])
    b = np.asarray([str(x) for x in labels_b])
    ua, ub = np.unique(a), np.unique(b)
    cont = np.zeros((len(ua), len(ub)), dtype=np.float64)
    for i, ca in enumerate(ua):
        for j, cb in enumerate(ub):
            cont[i, j] = float(np.sum((a == ca) & (b == cb)))
    pxy = cont / n
    px = pxy.sum(axis=1, keepdims=True)
    py = pxy.sum(axis=0, keepdims=True)
    mask = pxy > 0
    mi = float(np.sum(pxy[mask] * np.log(pxy[mask] / (px @ py)[mask])))
    hx = float(-(px[px > 0] * np.log(px[px > 0])).sum())
    hy = float(-(py[py > 0] * np.log(py[py > 0])).sum())
    denom = (hx + hy) / 2.0
    return 1.0 if denom == 0 else max(0.0, min(1.0, mi / denom))


@dataclass(frozen=True)
class SeedSweepResult:
    """Distribution evidence over seeds; convergence vs an admin threshold."""

    n_seeds: int
    seeds: tuple[int, ...]
    cluster_counts: dict[int, int]
    pairwise_ari_mean: float
    pairwise_ari_min: float
    pairwise_nmi_mean: float
    pairwise_nmi_min: float
    count_variance: float
    max_count_variance: float | None
    converged: bool | None
    note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "n_seeds": self.n_seeds,
            "seeds": list(self.seeds),
            "cluster_counts": {str(k): v for k, v in self.cluster_counts.items()},
            "pairwise_ari_mean": self.pairwise_ari_mean,
            "pairwise_ari_min": self.pairwise_ari_min,
            "pairwise_nmi_mean": self.pairwise_nmi_mean,
            "pairwise_nmi_min": self.pairwise_nmi_min,
            "count_variance": self.count_variance,
            "max_count_variance": self.max_count_variance,
            "converged": self.converged,
            "note": self.note,
        }


MIN_SWEEP_SEEDS = 10


def seed_sweep(
    matrix: Sequence[Sequence[float]],
    cluster_fn: Callable[[Sequence[Sequence[float]], int], Sequence[object]],
    seeds: Sequence[int],
    *,
    max_count_variance: float | None = None,
) -> SeedSweepResult:
    """Re-cluster the SAME hashed matrix under each seed; pairwise ARI/NMI.

    ``cluster_fn(matrix, seed) -> labels`` is the engine pipeline (Wave 2
    wires UMAP+HDBSCAN with the seed plumbed through). ``max_count_variance``
    is ADMIN-CONFIGURED (no paper default): with ``None`` the sweep reports
    variance but ``converged=None`` (not_evaluable) instead of guessing.
    """
    if len(seeds) < MIN_SWEEP_SEEDS:
        raise ValueError(f"seed sweep requires >= {MIN_SWEEP_SEEDS} seeds, got {len(seeds)}")
    labels_by_seed: dict[int, list[object]] = {}
    for seed in seeds:
        labels_by_seed[int(seed)] = list(cluster_fn(matrix, int(seed)))
    counts = {s: len({lab for lab in labs if lab != -1}) for s, labs in labels_by_seed.items()}
    aris: list[float] = []
    nmis: list[float] = []
    seed_list = sorted(labels_by_seed)
    for i, seed_a in enumerate(seed_list):
        for seed_b in seed_list[i + 1 :]:
            la, lb = labels_by_seed[seed_a], labels_by_seed[seed_b]
            aris.append(ari(la, lb))
            nmis.append(nmi(la, lb))
    variance = float(np.var([float(c) for c in counts.values()]))
    converged: bool | None = None
    note = ""
    if max_count_variance is None:
        note = "max_count_variance not configured; convergence not_evaluable"
    else:
        converged = variance <= float(max_count_variance)
        if not converged:
            note = "not_converged: cluster-count variance exceeds the admin threshold"
    return SeedSweepResult(
        n_seeds=len(seed_list),
        seeds=tuple(seed_list),
        cluster_counts=counts,
        pairwise_ari_mean=float(np.mean(aris)) if aris else 1.0,
        pairwise_ari_min=float(np.min(aris)) if aris else 1.0,
        pairwise_nmi_mean=float(np.mean(nmis)) if nmis else 1.0,
        pairwise_nmi_min=float(np.min(nmis)) if nmis else 1.0,
        count_variance=variance,
        max_count_variance=max_count_variance,
        converged=converged,
        note=note,
    )


def determinism_proof(
    matrix: Sequence[Sequence[float]],
    seed: int,
    params: dict[str, object],
    sweep: SeedSweepResult | None = None,
    *,
    transform_seed: int | None = None,
    libraries: dict[str, str | None] | None = None,
) -> dict[str, object]:
    """Assemble the manifest's clustering_determinism block (U44 gate input)."""
    proof = clustering_provenance(seed, params, transform_seed=transform_seed, libraries=libraries)
    proof["embedding_matrix_sha256"] = content_hash_matrix(matrix)
    if sweep is not None:
        proof["sweep"] = sweep.to_dict()
    return proof
