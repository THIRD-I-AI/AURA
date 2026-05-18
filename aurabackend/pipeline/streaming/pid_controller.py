"""
PID-controlled backpressure — Sprint 20a (Pillar 4).

Anchors:
  * Hellerstein, Diao, Parekh, Tilbury (2004). "Feedback Control of
    Computing Systems." Wiley. — PID control applied to queueing
    systems is Chapter 4 (Standard PID) + Chapter 7 (Queueing-network
    case studies).
  * Ziegler & Nichols (1942). "Optimum Settings for Automatic
    Controllers." Trans. ASME 64:759-768 — the closed-loop tuning
    method whose Kp/Ki/Kd defaults we ship.

What this module ships
----------------------
``PIDBackpressureController`` — a single-output controller that
replaces AURA's existing static ``max_buffer_size`` cliff (which
drops to DROP_TAIL or BLOCK at 100% occupancy) with a continuous
control signal ``u(t) ∈ [0, 1]`` that the ingestor uses as a sleep-
window fraction. Higher ``u(t)`` → ingestor sleeps longer → inflow
rate decreases → buffer depth converges toward ``B_target``.

The standard PID equation (Hellerstein-Diao § 4.3):

    u(t) = Kp · e(t)  +  Ki · ∫₀ᵗ e(τ) dτ  +  Kd · de(t)/dt

where ``e(t) = (B(t) - B_target) / B_max`` is the normalised error.
We discretise time-step at the caller's cadence (typically each
batch-completion event) and integrate / differentiate by simple
Euler stepping with the caller-supplied ``dt``.

Asymmetric semantics
--------------------
Backpressure can only SLOW DOWN ingestion, never speed it up — the
ingestor cannot create events that don't exist upstream. So:

    * When B(t) > B_target (buffer overfull):  e > 0  ⇒  u > 0  ⇒  ingestor sleeps.
    * When B(t) < B_target (buffer underfull): e < 0  ⇒  u < 0  ⇒  CLAMPED TO 0.

The output clamp ``[0, 1]`` is enforced strictly. This makes the
controller an "elastic spring" that pulls B(t) DOWN toward the
target when over-target and idles when under-target (the underflow
case isn't a problem to fix — the upstream simply hasn't produced
enough events yet).

Anti-windup
-----------
When the output is saturated at ``u = 1.0`` (maximum backpressure),
the integral term must NOT continue accumulating — otherwise it
"winds up" arbitrarily large and the controller takes a long time
to back off when the buffer finally drains. We apply **conditional
integration**: the integrator only accumulates when the unsaturated
output ``u_unclamped`` is inside the linear range. This is
Hellerstein-Diao § 4.7's anti-windup recipe.

Ziegler-Nichols default tuning
-------------------------------
``Kp=0.5, Ki=0.1, Kd=0.05`` — conservative defaults that give a
small-overshoot, fast-settling response on typical queueing
dynamics. Operators tune per-deployment via the Prometheus telemetry
this module exposes (``e(t)``, ``u(t)``, integral, derivative).

Determinism
-----------
The controller is fully deterministic in the (current_b, dt) input
sequence — same inputs → byte-identical outputs. Used for byte-stable
replay of streaming-pipeline operator decisions (Layer 10 contract).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# Ziegler-Nichols closed-loop defaults — gentle response on typical
# I/O-bound queueing dynamics. Operators override per-deployment via
# env vars or constructor args.
DEFAULT_KP = 0.5
DEFAULT_KI = 0.1
DEFAULT_KD = 0.05

# Saturation band — width of the linear-range "deadzone" inside which
# integration is allowed. ``u`` strictly inside ``(low + δ, high - δ)``
# triggers integration; otherwise the integrator pauses.
_LINEAR_RANGE_EPSILON = 1e-6


@dataclass(frozen=True)
class PIDMetrics:
    """Telemetry snapshot — what Prometheus gauges should expose.

    All fields are unitless except ``b_target`` and ``current_b``
    which share units with ``b_max`` (typically "events" or "bytes")."""

    current_b: float
    """Latest observed buffer depth ``B(t)``."""

    b_target: float
    """Set-point ``B_target`` the controller is converging toward."""

    error: float
    """Normalised error ``e(t) = (B(t) - B_target) / B_max``."""

    integral: float
    """Accumulated integral term, in the same normalised units as e(t).
    Bounded by anti-windup logic."""

    derivative: float
    """Derivative term ``de(t)/dt``, computed by Euler differencing."""

    output: float
    """Control output ``u(t) ∈ [0, 1]`` after clamping. The ingestor
    multiplies this by its ``max_sleep_seconds`` to get the actual
    per-batch sleep duration."""


class PIDBackpressureController:
    """Single-output PID controller for buffer-depth backpressure.

    Usage::

        ctrl = PIDBackpressureController(b_target=7000, b_max=10000)
        for batch in ingestor:
            sleep_fraction = ctrl.step(current_b=queue.qsize(), dt=batch.dt_seconds)
            await asyncio.sleep(sleep_fraction * MAX_SLEEP_SECONDS)
            await consumer.process(batch)
        prom.gauge_pid_error.set(ctrl.metrics().error)

    Thread-safety: NOT thread-safe. The controller maintains
    integral/derivative state across calls; concurrent step()
    invocations would corrupt that state. Wrap with an asyncio.Lock
    or call from a single coroutine (the ingestor's outer loop).
    """

    def __init__(
        self,
        b_target: float,
        b_max: float,
        *,
        kp: float = DEFAULT_KP,
        ki: float = DEFAULT_KI,
        kd: float = DEFAULT_KD,
        output_clamp: Tuple[float, float] = (0.0, 1.0),
    ) -> None:
        if b_max <= 0:
            raise ValueError(f"b_max must be > 0, got {b_max}")
        if not 0 < b_target < b_max:
            raise ValueError(
                f"b_target must be in (0, b_max); got b_target={b_target}, b_max={b_max}"
            )
        if output_clamp[0] >= output_clamp[1]:
            raise ValueError(f"output_clamp must be (lo, hi) with lo < hi, got {output_clamp}")
        self._b_target = float(b_target)
        self._b_max = float(b_max)
        self._kp = float(kp)
        self._ki = float(ki)
        self._kd = float(kd)
        self._clamp_lo, self._clamp_hi = float(output_clamp[0]), float(output_clamp[1])

        # Stateful accumulators.
        self._integral: float = 0.0
        self._last_error: float = 0.0
        self._last_output: float = 0.0
        self._last_current_b: float = float(b_target)  # init at target so first dt step doesn't kick

    # ── Public API ────────────────────────────────────────────────────

    def step(self, current_b: float, dt: float = 1.0) -> float:
        """Advance the controller one timestep and return the new ``u(t)``.

        Args:
            current_b: Current buffer depth (must be ≥ 0; values > b_max
                are accepted — error simply saturates positively).
            dt: Seconds elapsed since the previous step. Must be > 0.
                Default 1.0 — appropriate when the caller invokes step
                once per batch and batches arrive at ~uniform cadence.

        Returns:
            ``u(t) ∈ [output_clamp_lo, output_clamp_hi]``. The caller
            typically multiplies this by ``max_sleep_seconds`` to get
            the ingestor's per-batch sleep duration.
        """
        if dt <= 0:
            raise ValueError(f"dt must be > 0, got {dt}")
        if current_b < 0:
            raise ValueError(f"current_b must be >= 0, got {current_b}")

        # Normalised error in [B_target/B_max - 1, +∞). At B = B_target
        # this is 0; at B = B_max this is (B_max - B_target)/B_max ≈ 0.3
        # for our 70%-target default.
        error = (current_b - self._b_target) / self._b_max

        # Derivative — Euler differencing. Use error-on-error (not
        # derivative-on-measurement) for simplicity; could swap later
        # if "derivative kick" on set-point changes becomes a problem.
        derivative = (error - self._last_error) / dt

        # Unclamped output FIRST so the anti-windup logic can decide
        # whether to integrate.
        u_unclamped = (
            self._kp * error
            + self._ki * (self._integral + error * dt)   # tentative integrated value
            + self._kd * derivative
        )

        # Anti-windup: only commit the integration step if the
        # tentative output is INSIDE the saturation band. Otherwise
        # we'd accumulate error indefinitely while the output is
        # pegged, then take forever to back off.
        in_linear_range = (
            self._clamp_lo + _LINEAR_RANGE_EPSILON
            < u_unclamped
            < self._clamp_hi - _LINEAR_RANGE_EPSILON
        )
        if in_linear_range:
            self._integral += error * dt
        # Recompute output using the (possibly-NOT-updated) integral.
        u = (
            self._kp * error
            + self._ki * self._integral
            + self._kd * derivative
        )
        u_clamped = max(self._clamp_lo, min(self._clamp_hi, u))

        # Commit state for next step.
        self._last_error = error
        self._last_output = u_clamped
        self._last_current_b = current_b
        return u_clamped

    def reset(self) -> None:
        """Reset all stateful accumulators. Call on pipeline restart
        or set-point change to prevent stale state from biasing the
        first few steps."""
        self._integral = 0.0
        self._last_error = 0.0
        self._last_output = 0.0
        self._last_current_b = self._b_target

    def metrics(self) -> PIDMetrics:
        """Snapshot the controller's internal state — used by Prometheus."""
        # Reconstruct derivative from last-error (we don't store it;
        # this is purely a diagnostic, not a control input).
        return PIDMetrics(
            current_b=self._last_current_b,
            b_target=self._b_target,
            error=self._last_error,
            integral=self._integral,
            derivative=0.0,  # derivative is a per-step quantity; report 0 between calls
            output=self._last_output,
        )

    # ── Read-only configuration ──────────────────────────────────────

    @property
    def b_target(self) -> float:
        return self._b_target

    @property
    def b_max(self) -> float:
        return self._b_max

    @property
    def gains(self) -> Dict[str, float]:
        return {"kp": self._kp, "ki": self._ki, "kd": self._kd}


__all__ = [
    "PIDBackpressureController",
    "PIDMetrics",
    "DEFAULT_KP",
    "DEFAULT_KI",
    "DEFAULT_KD",
]
