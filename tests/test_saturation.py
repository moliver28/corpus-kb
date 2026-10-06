"""Test saturation calculation: ISR and stopping rules."""

from __future__ import annotations

from corpus_kb.coding.saturation import isr, run_stop


def test_isr_basic() -> None:
    """Test Incremental Sampling Rate calculation."""
    # No applications: ISR is 0
    assert isr(0, 0) == 0.0
    assert isr(5, 0) == 0.0

    # Equal unique and total: ISR is 1.0
    assert isr(10, 10) == 1.0

    # Standard case: 20 unique codes in 100 applications
    assert isr(20, 100) == 0.2

    # ISR capped at 1.0
    assert isr(100, 50) == 1.0


def test_isr_monotonic_decreasing() -> None:
    """ISR should decrease as the ratio of unique to total decreases."""
    isr1 = isr(10, 50)
    isr2 = isr(10, 100)
    isr3 = isr(10, 200)

    assert isr1 > isr2 > isr3
    assert isr1 == 0.2
    assert isr2 == 0.1
    assert isr3 == 0.05


def test_run_stop_below_min_samples() -> None:
    """Saturation check should require minimum samples before stopping."""
    # Below min_samples: should never stop
    assert run_stop(new_codes=0, base_unique=10, threshold=0.05, min_samples=50) is False
    assert run_stop(new_codes=2, base_unique=49, threshold=0.05, min_samples=50) is False

    # At min_samples boundary: should allow stop
    assert run_stop(new_codes=0, base_unique=50, threshold=0.05, min_samples=50) is True


def test_run_stop_zero_base_unique() -> None:
    """Edge case: zero unique codes should not allow stop."""
    assert run_stop(new_codes=0, base_unique=0, threshold=0.05, min_samples=1) is False
    assert run_stop(new_codes=5, base_unique=0, threshold=0.05, min_samples=1) is False


def test_run_stop_below_threshold() -> None:
    """New code discovery rate below threshold allows stop."""
    # 1 new code out of 100 base: discovery rate 1/100 = 0.01, below 0.05
    assert run_stop(new_codes=1, base_unique=100, threshold=0.05, min_samples=50) is True

    # 5 new codes out of 100 base: discovery rate 5/100 = 0.05, at threshold
    assert run_stop(new_codes=5, base_unique=100, threshold=0.05, min_samples=50) is True

    # 6 new codes out of 100 base: discovery rate 6/100 = 0.06, above threshold
    assert run_stop(new_codes=6, base_unique=100, threshold=0.05, min_samples=50) is False


def test_run_stop_threshold_variants() -> None:
    """Test different threshold settings."""
    # Base: 100 codes, 3 new (discovery rate 0.03)
    assert run_stop(new_codes=3, base_unique=100, threshold=0.05, min_samples=50) is True
    assert run_stop(new_codes=3, base_unique=100, threshold=0.02, min_samples=50) is False
    assert run_stop(new_codes=3, base_unique=100, threshold=0.04, min_samples=50) is True


def test_run_stop_realistic_trajectory() -> None:
    """Simulate a realistic coding trajectory."""
    # Early stage: lots of new codes, should not stop
    assert run_stop(new_codes=15, base_unique=30, threshold=0.05, min_samples=50) is False

    # Middle stage: fewer new codes
    assert run_stop(new_codes=8, base_unique=80, threshold=0.05, min_samples=50) is False

    # Late stage: saturation reached
    assert run_stop(new_codes=2, base_unique=150, threshold=0.05, min_samples=50) is True

    # Very late stage: minimal new discovery
    assert run_stop(new_codes=0, base_unique=200, threshold=0.05, min_samples=50) is True
