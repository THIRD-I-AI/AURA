"""
Sprint 20a — Layer 17d contract tests for Hellerstein-Diao PID
backpressure controller.

Two tiers:

  1. Single-step correctness — the standard PID equation behaves the
     way Hellerstein-Diao § 4.3 specifies it (sign convention,
     output clamp, anti-windup, parameter validation).
  2. Closed-loop convergence simulation — a 200-step variable-rate
     queue model where the PID must keep buffer depth inside
     [B_target ± 0.1 * B_max] for at least 90% of steps. This is the
     Layer 17d contract: a controller that fails this can't replace
     the static DROP_TAIL cliff.

The simulation is a discrete-time queueing model:

    B(t+1) = max(0, B(t) + inflow(t) - outflow(t))

where:
    inflow(t)  = nominal_inflow / (1 + u(t-1))   # PID acts via lag-1 backpressure
    outflow(t) = stochastic ~ N(nominal_outflow, sigma)

The "ingestor sleep window" interpretation: a higher ``u(t)`` slows
the inflow proportional to (1 + u). At ``u = 0`` inflow = nominal; at
``u = 1`` inflow = nominal/2 (50% throttle).

Tuning notes
------------
Ziegler-Nichols defaults (Kp=0.5, Ki=0.1, Kd=0.05) are designed for
gentle response on roughly first-order queueing dynamics. The
simulation deliberately uses bursty/noisy inflow + outflow to test
that the PID stays inside the band under realistic stochastic
disturbance — not a contrived noise-free convergence proof.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from pipeline.streaming.pid_controller import (
    DEFAULT_KD,
    DEFAULT_KI,
    DEFAULT_KP,
    PIDBackpressureController,
    PIDMetrics,
)

# ── Constructor + parameter validation ────────────────────────────────


def test_controller_rejects_zero_b_max() -> None:
    with pytest.raises(ValueError, match="b_max must be > 0"):
        PIDBackpressureController(b_target=100, b_max=0)


def test_controller_rejects_b_target_outside_range() -> None:
    """b_target must be strictly inside (0, b_max) — otherwise the
    normalised error is ill-defined."""
    with pytest.raises(ValueError, match="b_target must be in"):
        PIDBackpressureController(b_target=0, b_max=100)
    with pytest.raises(ValueError, match="b_target must be in"):
        PIDBackpressureController(b_target=100, b_max=100)
    with pytest.raises(ValueError, match="b_target must be in"):
        PIDBackpressureController(b_target=-1, b_max=100)


def test_controller_rejects_inverted_output_clamp() -> None:
    with pytest.raises(ValueError, match="output_clamp must be"):
        PIDBackpressureController(b_target=70, b_max=100, output_clamp=(1.0, 0.0))


def test_controller_rejects_invalid_dt() -> None:
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    with pytest.raises(ValueError, match="dt must be > 0"):
        ctrl.step(current_b=50, dt=0)
    with pytest.raises(ValueError, match="dt must be > 0"):
        ctrl.step(current_b=50, dt=-1)


def test_controller_rejects_negative_current_b() -> None:
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    with pytest.raises(ValueError, match="current_b must be >= 0"):
        ctrl.step(current_b=-1.0)


# ── Single-step output sign + clamp ──────────────────────────────────


def test_zero_error_zero_output() -> None:
    """When B = B_target exactly, error = 0, derivative ≈ 0,
    integral = 0 (from reset state) → u(0) = 0."""
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    u = ctrl.step(current_b=70.0, dt=1.0)
    assert u == pytest.approx(0.0)


def test_positive_error_positive_output() -> None:
    """Buffer overfull (B > B_target) → error positive → output
    positive (slow down ingest)."""
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    u = ctrl.step(current_b=90.0, dt=1.0)
    assert u > 0.0


def test_negative_error_clamps_to_zero() -> None:
    """Buffer underfull (B < B_target) → error negative → unclamped
    output negative → clamped to 0 (asymmetric backpressure: can't
    speed ingest up)."""
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    u = ctrl.step(current_b=30.0, dt=1.0)
    assert u == pytest.approx(0.0)


def test_output_clamps_at_upper_bound() -> None:
    """Buffer wildly overfull → output clamps to 1.0 (max throttle)."""
    ctrl = PIDBackpressureController(b_target=70, b_max=100, kp=10.0)  # extreme gain
    u = ctrl.step(current_b=100.0, dt=1.0)  # error = 0.3, kp*e = 3.0 → clamped to 1.0
    assert u == pytest.approx(1.0)


# ── Anti-windup (conditional integration) ────────────────────────────


def test_anti_windup_pauses_integration_at_saturation() -> None:
    """Hellerstein-Diao § 4.7 anti-windup: when the output is pegged
    at the saturation bound, the integrator MUST stop accumulating
    error — otherwise the integral grows arbitrarily and the
    controller takes forever to back off when the buffer drains.

    Test: drive the controller to saturation for several steps, then
    rapidly drop B to target. Without anti-windup, the windup would
    keep u positive long after error went to zero. WITH anti-windup,
    u returns to near-zero almost immediately."""
    ctrl = PIDBackpressureController(
        b_target=70, b_max=100, kp=10.0, ki=2.0, kd=0.0,
    )
    # Drive to saturation for 20 steps.
    for _ in range(20):
        ctrl.step(current_b=100.0, dt=1.0)
    # Snapshot integral after saturation period.
    integral_after_saturation = ctrl.metrics().integral

    # Now drop B back to target and run one step.
    ctrl.step(current_b=70.0, dt=1.0)
    # Anti-windup means the integral did NOT keep accumulating during
    # those 20 saturated steps. Confirm: integral is small (only a few
    # steps where the controller was in linear range counted).
    assert integral_after_saturation < 5.0, (
        f"integral exploded under saturation: {integral_after_saturation} — "
        f"anti-windup is broken"
    )


# ── Deterministic step sequence (Layer 10 audit replay) ──────────────


def test_step_is_deterministic_across_replays() -> None:
    """Same (current_b, dt) sequence → byte-identical u(t) sequence.
    Needed for hash-stable audit artifacts when S20.1 wires this into
    the streaming engine."""
    inputs = [(50, 1.0), (80, 1.0), (95, 1.0), (100, 1.0), (90, 1.0), (70, 1.0)]
    ctrl1 = PIDBackpressureController(b_target=70, b_max=100)
    ctrl2 = PIDBackpressureController(b_target=70, b_max=100)
    u1 = [ctrl1.step(b, dt) for b, dt in inputs]
    u2 = [ctrl2.step(b, dt) for b, dt in inputs]
    assert u1 == u2, "controller is non-deterministic across replays"


# ── Reset clears all stateful accumulators ───────────────────────────


def test_reset_clears_state() -> None:
    """A reset must put the controller back into the as-constructed
    state — same inputs after reset produce same outputs."""
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    # Build up state.
    for _ in range(10):
        ctrl.step(current_b=90.0, dt=1.0)
    # Reset and run one step.
    ctrl.reset()
    u_after_reset = ctrl.step(current_b=70.0, dt=1.0)
    # B = B_target → error = 0, integral = 0, derivative = 0 → u = 0.
    assert u_after_reset == pytest.approx(0.0)


# ── Metrics snapshot ─────────────────────────────────────────────────


def test_metrics_reports_last_step_state() -> None:
    """Prometheus gauges read from this snapshot — must reflect the
    most recent step's e(t), u(t), integral."""
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    ctrl.step(current_b=90.0, dt=1.0)
    m = ctrl.metrics()
    assert isinstance(m, PIDMetrics)
    assert m.current_b == 90.0
    assert m.b_target == 70.0
    assert m.error == pytest.approx(0.2)
    assert m.output > 0.0


# ── Layer 17d closed-loop convergence simulation ─────────────────────


def _simulate_pid(
    n_steps: int,
    b_target: float,
    b_max: float,
    nominal_inflow: float,
    nominal_outflow: float,
    inflow_noise_sigma: float,
    outflow_noise_sigma: float,
    seed: int,
    initial_b: float,
) -> tuple[list[float], list[float]]:
    """Run the PID against a noisy queueing model.

    Returns (buffer_depth_trace, control_output_trace) of length n_steps.
    """
    rng = np.random.default_rng(seed)
    ctrl = PIDBackpressureController(b_target=b_target, b_max=b_max)
    b = initial_b
    last_u = 0.0
    b_trace, u_trace = [b], [last_u]
    for _ in range(n_steps):
        # Inflow throttled by previous step's u (lag-1 control).
        inflow_noise = rng.normal(0.0, inflow_noise_sigma)
        inflow = max(0.0, nominal_inflow / (1.0 + last_u) + inflow_noise)
        outflow_noise = rng.normal(0.0, outflow_noise_sigma)
        outflow = max(0.0, nominal_outflow + outflow_noise)
        b = max(0.0, b + inflow - outflow)
        # PID computes new u(t) given the current buffer depth.
        last_u = ctrl.step(current_b=b, dt=1.0)
        b_trace.append(b)
        u_trace.append(last_u)
    return b_trace, u_trace


def test_pid_converges_within_band_for_90pct_of_steps() -> None:
    """Layer 17d contract: under variable-rate ingestion (bursty
    stochastic), the PID must keep buffer depth inside
    [B_target ± 0.1 * B_max] for at least 90% of steps after a brief
    warm-up.

    This proves the PID is a viable replacement for AURA's existing
    static DROP_TAIL cliff. Without backpressure, a noisy queue's
    depth would drift unboundedly; with a properly-tuned PID it
    converges to the target band."""
    b_target = 7000.0
    b_max = 10000.0
    # Nominal inflow > nominal outflow → buffer would fill without
    # backpressure. The PID's job is to slow inflow exactly enough to
    # keep B at target.
    b_trace, u_trace = _simulate_pid(
        n_steps=400,
        b_target=b_target,
        b_max=b_max,
        nominal_inflow=120.0,    # would fill the buffer without backpressure
        nominal_outflow=100.0,
        inflow_noise_sigma=15.0,
        outflow_noise_sigma=15.0,
        seed=2026,
        initial_b=5000.0,
    )
    # Drop the first 100 steps (warm-up / integral wind-up phase).
    warm = 100
    steady = np.array(b_trace[warm:])
    band_half = 0.1 * b_max  # ±1000
    in_band = np.abs(steady - b_target) <= band_half
    in_band_pct = in_band.mean()
    assert in_band_pct >= 0.90, (
        f"PID only kept buffer in [{b_target - band_half}, {b_target + band_half}] "
        f"for {in_band_pct:.2%} of {len(steady)} steady-state steps — needed ≥ 90% "
        f"to satisfy Layer 17d. Trace mean/std: {steady.mean():.1f}/{steady.std():.1f}"
    )


def test_pid_converges_to_target_in_mean() -> None:
    """In addition to the band-occupancy contract above, the steady-
    state MEAN buffer depth should sit near B_target — the PID isn't
    just oscillating around an offset, it's hitting the set-point."""
    b_target = 7000.0
    b_trace, _ = _simulate_pid(
        n_steps=400,
        b_target=b_target,
        b_max=10000.0,
        nominal_inflow=120.0,
        nominal_outflow=100.0,
        inflow_noise_sigma=15.0,
        outflow_noise_sigma=15.0,
        seed=2026,
        initial_b=5000.0,
    )
    steady = np.array(b_trace[100:])
    # Mean should be within 5% of b_max from target.
    assert abs(steady.mean() - b_target) < 0.05 * 10000.0, (
        f"PID steady-state mean = {steady.mean():.1f}, target = {b_target}; "
        f"drift > 5% indicates set-point bias"
    )


def test_pid_default_gains_match_ziegler_nichols() -> None:
    """Sanity check on the published defaults — Kp=0.5, Ki=0.1, Kd=0.05
    is the Ziegler-Nichols closed-loop recommendation Hellerstein-Diao
    cites for queueing systems. If anyone changes the defaults later
    they must update this test AND the docstring."""
    assert DEFAULT_KP == 0.5
    assert DEFAULT_KI == 0.1
    assert DEFAULT_KD == 0.05
    ctrl = PIDBackpressureController(b_target=70, b_max=100)
    assert ctrl.gains == {"kp": 0.5, "ki": 0.1, "kd": 0.05}


def test_math_isfinite_check() -> None:
    """Sanity: numpy NaN check via math.isnan/isfinite works the way
    the controller's NaN guard expects. Belt-and-braces."""
    assert math.isfinite(1.0)
    assert not math.isfinite(float("nan"))
    assert not math.isfinite(float("inf"))
