# AURA — Streaming & Self-Healing Foundations

**Target audience: AURA architecture team, distributed-systems engineers, and external reviewers evaluating the streaming substrate against published research.**

This document captures the mathematical foundations and target architecture for AURA's evolution from a localized micro-batching streaming engine + heuristic self-healing worker into a fully distributed, fault-tolerant, dynamically adaptive stream topology with formal guarantees on state recovery, drift detection, and backpressure stability.

The current implementation (`pipeline/streaming/*.py` + `uasr/mapek_worker.py`) is architecturally mature — it correctly bridges Control Theory (closed-loop MAPE-K) with Structured Stream Processing (watermark tracking, window aggregations, state checkpoints). The transactional atomic handling that stages micro-batches to PyArrow Parquet and executes `BEGIN TRANSACTION` via dynamic DuckDB insertions is an exceptional local mitigation against data corruption and partial-write failures.

This document specifies the next-generation evolution that takes AURA's streaming substrate to hyperscale-enterprise-grade with mathematical guarantees.

---

## Current implementation — what we have

| File | Lines | Concern | Current mechanism |
|---|---|---|---|
| `pipeline/streaming/streaming_engine.py` | 459 | Main runtime loop: source → transform → window → sinks | Async micro-batch loop with configurable interval |
| `pipeline/streaming/state_manager.py` | 181 | Checkpoint / recovery | `should_checkpoint(interval_seconds)` — coarse wall-clock interval |
| `pipeline/streaming/window_processor.py` | 357 | Event-time windowing + watermark | Local watermark, late-data routed to dead-letter sink |
| `pipeline/streaming/backpressure.py` | 194 | Queue management | Static `max_buffer_size=10_000` + `DROP_TAIL` policy |
| `uasr/mapek_worker.py` | (heavy) | MAPE-K self-healing loop | `self.pause()` synchronously halts ingest while shim is generated |

All five files work correctly at the single-node level. The architectural seams that prevent them from operating at multi-node distributed scale are the same five lines that this document evolves.

---

## Three Architectural Evolutions

### Evolution 1 — Asynchronous Barrier Snapshotting (ABS)

#### The constraint in our current code

`pipeline/streaming/state_manager.py:139::should_checkpoint(interval_seconds)` is a wall-clock predicate. The streaming engine calls it once per micro-batch loop iteration. When `True`, the engine synchronously serializes state to disk before processing the next batch. In a distributed multi-node mesh, executing this synchronous or coarse-interval pattern drops active tracking performance and forces massive operational pauses while syncing disk writes across network states. Multi-node deployments cannot meet sub-millisecond latency SLAs with this checkpoint cadence.

#### The target architecture

Implement **Asynchronous Barrier Snapshotting** (ABS) — barrier markers injected directly into the message stream (Kafka topic, NATS subject, or our `streaming_manager` pub/sub bus). Operators checkpoint their local state *only when they encounter a barrier token*, allowing processing pipelines to execute continuously at sub-millisecond latencies.

#### Mathematical formulation

Let `G = (V, E)` represent the streaming execution Directed Acyclic Graph (DAG). For an individual streaming node `v ∈ V` receiving elements from upstream input channels `e_in ∈ E`, when `v` receives barrier marker `b` from channel `c`, it initiates the alignment function:

```
Align(v, c) =
  {
    Block c                          if ∃c' ∈ Inflow(v) that has not emitted b
    Snapshot S_v and forward b       if ∀c' ∈ Inflow(v), b has arrived
  }
```

While channel `c` is blocked, incoming elements are buffered in an in-memory queue. Once alignment completes, operator state `S_v` is written asynchronously to storage, and upstream buffered records are flushed. This guarantees that **global state recovery requires zero message-replay overlap** — the canonical exactly-once-processing contract.

#### Why this works

The Chandy-Lamport (1985) theorem proves that the alignment function above captures a globally consistent cut of the distributed system's state, even though individual operators never stop processing. The cut is "consistent" in the sense that any message sent before the snapshot is either (a) recorded in the snapshot of the sender, or (b) recorded as an in-flight message in the snapshot of the channel — never lost, never double-counted.

Carbone et al. (2015) — the Apache Flink design — extends Chandy-Lamport with the alignment optimisation: instead of recording in-flight messages on each channel (the original "marker algorithm"), the operator simply waits for all input channels to deliver the barrier before snapshotting. This is the standard implementation in production stream processors today.

#### File targets

- `pipeline/streaming/state_manager.py` — replace `should_checkpoint(interval_seconds)` with `align_and_snapshot(barrier_id)`; add `pending_alignment: dict[ChannelID, set[BarrierID]]` tracking per-channel barrier arrival
- `pipeline/streaming/streaming_engine.py` — inject barriers into the outbound stream on a configurable cadence (`AURA_STREAM_BARRIER_INTERVAL_SECONDS`, default 30s)
- `pipeline/streaming/window_processor.py` — register as a barrier-aware operator; flush window state on alignment
- New `pipeline/streaming/barrier.py` — `BarrierMarker(barrier_id, source_ts)` Pydantic model + serde helpers
- New eval-gate Layer (S20): inject barriers on a 3-node simulated topology, kill node mid-stream, verify recovery from latest aligned snapshot produces byte-identical output downstream

#### Adoption sprint

**Sprint 20** (Scheduler v2: Distributed Multi-Region Execution) — see plan file. ABS is one of three deepening evolutions in S20.

---

### Evolution 2 — Dataflow Model Watermark Propagation

#### The constraint in our current code

`pipeline/streaming/window_processor.py` tracks event-time windowing locally, dropping data or routing to dead-letter sink based on a fixed late-data policy. When scaling to deep multi-stage streaming DAGs (Service A → Service B → Service C → Service D), local watermarks lose tracking capability across operator boundaries — Service C cannot know whether Service A has emitted all events for window `[t0, t1)` without an explicit signal. The result is severe pipeline stall states, watermark mis-alignment between operators, and silent data loss when an upstream operator's watermark advances past a downstream operator's pending window.

#### The target architecture

Move toward a unified **Dataflow Model** (Akidau et al. 2015) that propagates monotonic watermarks downstream as structural data metrics across independent computing nodes, separating event-time metrics completely from raw wall-clock arrival records.

#### Key primitives

1. **Watermark `W_t`** — a monotonically non-decreasing event-time threshold below which the operator believes no more events will arrive. Each operator emits its current `W_t` as a control element on the same stream as data elements.
2. **Window assignment** — every data element `e` with event-time `e.ts` is assigned to window(s) via a `WindowFn` (tumbling / sliding / session). Window state accumulates until the watermark passes the window's end-time.
3. **Trigger** — fires window emission on watermark advance OR on a count / processing-time / accumulation trigger. The same window can fire multiple times with `AccumulationMode = ACCUMULATING` (each firing includes all elements seen so far) or `DISCARDING` (each firing includes only new elements since the last fire).
4. **Late data** — elements arriving with `e.ts < W_t` are either dropped (with logging), routed to a side output, or merged into a re-fired window if `allowed_lateness > 0`.
5. **Composite watermark** — a downstream operator's watermark is `min(W_upstream_1, W_upstream_2, ...)` across all input channels. The watermark cannot advance past the slowest upstream source.

#### Mathematical contract

For a window `[t_start, t_end)` and an operator with watermark `W_t`:

```
window.is_complete ⟺ W_t ≥ t_end
window.elements    = { e : e.ts ∈ [t_start, t_end) ∧ e.arrival_ts ≤ now }
late_element(e)    ⟺ e.ts < W_t ∧ e.window ∉ {recently_emitted}
```

Akidau et al. prove that this formalism balances **correctness** (no element is silently lost), **latency** (windows emit as soon as the watermark passes), and **cost** (no perpetually-open windows) — the three competing concerns in unbounded out-of-order stream processing.

#### File targets

- `pipeline/streaming/window_processor.py` — replace local watermark with a `WatermarkTracker` that consumes both data and watermark control elements; emit per-window-fire trigger events to downstream operators
- `pipeline/streaming/streaming_engine.py` — propagate watermark control elements between operators alongside data elements
- New `pipeline/streaming/triggers.py` — `WatermarkTrigger`, `CountTrigger`, `ProcessingTimeTrigger`, `CompositeTrigger`
- New `pipeline/streaming/late_data.py` — `LateDataPolicy = Literal["drop", "side_output", "remerge_within_allowed_lateness"]`
- Update operator interface in `pipeline/streaming/streaming_api.py` to declare upstream channel dependencies for composite-watermark computation
- New eval-gate Layer (S20): 3-stage DAG with synthetic out-of-order ingestion; verify watermark propagation matches the analytical contract above on a 1000-event stream

#### Adoption sprint

**Sprint 20**. Bundled with ABS so the same `pipeline/streaming/` rewrite touches all watermark-aware files once.

---

### Evolution 3 — Dynamic Stream Graph Reconfiguration

#### The constraint in our current code

When data or schema drift is flagged by `uasr.drift_detector.DriftDetector`, the `uasr.mapek_worker.MAPEKWorker` actively calls `self.pause()`, which stops the async Kafka execution loop completely while the LLM generates a recovery script or deploys a schema shim. At high throughputs, halting consumer polling creates catastrophic data queues upstream, spikes p99 processing latencies, and can lead to severe service timeouts.

#### The target architecture

Implement a non-blocking **Dynamic Stream Graph Reconfiguration** layer. The core event consumer continues ingest execution paths at full scale, while drift corrections are compiled into isolated topological sub-graphs that are **hot-swapped** into the running processing chain at runtime using structural proxy routers.

#### The proxy-router pattern

```
Before drift detection:
   [Source] → [TransformV1] → [Sink]
                  ↑
              All traffic

After drift detection (no pause; both transforms live simultaneously):
                  ┌─→ [TransformV1] ──┐
   [Source] → [Router]                 ├─→ [Sink]
                  └─→ [TransformV2] ──┘   (router gradually shifts traffic
                                          from V1 to V2 as V2 proves stable)

After shim validated:
   [Source] → [TransformV2] → [Sink]
                                          (V1 drained and removed once
                                          the router's V2-confidence ≥ 1.0)
```

The router uses a **canary deployment** pattern: V2 starts receiving a small fraction of traffic (10%), and the router increases V2's share as the drift metric on V2's output proves stable. V1 stays running until V2 reaches full traffic and the router validates V2's output matches V1's on the shadow fraction.

This pattern is the standard implementation in production self-healing stream processors and is grounded in Kramer-Magee (1990)'s **dynamic change management** framework, which proves that a reconfiguration is safe if and only if every operator's *quiescent state* is reached during the swap — exactly what the router-driven gradual shift achieves.

#### Mathematical contract (Kramer-Magee quiescence)

An operator `v` is **quiescent at swap-time T** if:

```
∀ t > T : v has no in-flight transactions
       ∧ v's outputs have been acknowledged by all downstream consumers
       ∧ v is not currently committed to participate in any future state action
```

The router enforces quiescence on V1 by draining its in-flight requests before terminating it, and on V2 by gating it behind successful canary validation. The reconfiguration is provably safe — no message is lost, no operation is double-applied, no operator deadlocks.

#### File targets

- `uasr/mapek_worker.py` — replace `self.pause()` + `self.resume()` with `self.deploy_shim_v2(shim_config)` that triggers the router pattern; new helper `_drain_v1(timeout_s)` enforces quiescence on the old transform
- New `uasr/shim_router.py` — `ShimRouter` class with `add_route(version, weight)`, `shift_traffic(from_v, to_v, ratio)`, `validate_canary(metric_fn) → bool`
- `uasr/recovery_loop.py` — recovery deployments now route through `ShimRouter` instead of mutating the in-process transform reference
- New eval-gate Layer (S18): simulate drift mid-stream; assert that `pause()` is never called, that the router's canary fraction climbs from 0.1 to 1.0 over the validation window, and that no upstream events are queued beyond the static buffer ceiling

#### Adoption sprint

**Sprint 18** (Causal-RL Self-Healing). The router pattern is the structural substrate for the off-policy DR evaluator — every candidate shim runs as a V2 candidate behind the router, the DR evaluator scores its canary output, and the router promotes the winning shim.

---

## Three Mathematical Guardrails

These are the formal algorithms that replace the current heuristic mechanisms. Each is independently testable and shippable; together they upgrade AURA's streaming substrate from "good engineering" to "mathematically deterministic fail-safe product."

### Guardrail 1 — ABS Alignment Function

Already specified in Evolution 1 above. Restating with full mathematical context:

For node `v ∈ V` in DAG `G = (V, E)`:

```
Align(v, c) where c ∈ Inflow(v) just emitted barrier b :
  if ∃c' ∈ Inflow(v) such that c' has not yet emitted b:
    enqueue c's incoming elements into buffer Q_c
    mark c as awaiting alignment for barrier b
  else:
    snapshot S_v ← serialize(v.state)
    forward b on all outflows Outflow(v) ⊆ E
    flush Q_{c'} for all c' that were buffered
    persist S_v asynchronously to durable storage
```

**Correctness guarantee (Carbone et al. 2015, Theorem 1):** the snapshots `{S_v : v ∈ V}` collectively form a consistent global snapshot in the Chandy-Lamport sense. Recovery from `{S_v}` produces output downstream that is byte-identical to a fresh execution with zero replay overlap.

### Guardrail 2 — Wasserstein + Azuma-Hoeffding Drift Detection

**The current heuristic** (`uasr/drift_detector.py`): IQR-based statistical drift on per-column distributions. False positives are common when network noise or expected statistical variance shifts the IQR boundaries; each false positive triggers `MAPEKWorker.pause()` and drives up operational latency.

**The replacement** (Bifet-Gavalda 2007 ADWIN + Azuma-Hoeffding bound): a sequential analysis model based on the **Wasserstein-1 distance** between baseline and current distributions, combined with a martingale process whose deviations are bounded by Azuma-Hoeffding. The detector fires only when drift is *mathematically proven* to be structural, ignoring random noise.

#### Mathematical formulation

Let `P_0` be the verified baseline data distribution snapshot and `Q_t` the active batch distribution at time `t`. Compute the Earth Mover's (Wasserstein-1) distance:

```
D_W(P_0, Q_t) = inf_{γ ∈ Γ(P_0, Q_t)} ∫∫ |x - y| dγ(x, y)
```

where `Γ(P_0, Q_t)` is the set of all joint distributions with marginals `P_0` and `Q_t`. For 1-D distributions (per-column drift detection), `D_W` reduces to the L1 distance between empirical CDFs — fast to compute.

Construct a **zero-mean martingale process**:

```
M_t = Σ_{i=1}^t ( D_W(P_0, Q_i) - E[D_W] )
```

where `E[D_W]` is the expected drift distance under the null hypothesis of no structural drift (estimated from a held-out reference period during system startup).

Enforce a strict safety ceiling `ε` using the **Azuma-Hoeffding inequality** under maximum risk tolerance `α`:

```
P( max_{1 ≤ t ≤ N} M_t ≥ ε ) ≤ exp( -ε² / (2 Σ_{i=1}^t c_i²) ) = α
```

Solving for `ε`:

```
ε = √( 2 ln(1/α) · Σ_{i=1}^t c_i² )
```

where `c_i` is the bounded range of the martingale increment at step `i` (computable from `D_W`'s max possible value on the column's domain).

#### Operational contract

- Fix `α` at deploy time (e.g., `α = 0.001` for false-positive rate ≤ 0.1%)
- On every new batch, update `M_t` and compare against `ε(t)`
- Fire drift alarm iff `M_t ≥ ε(t)` — provably bounded false-positive rate `≤ α`

**This guarantee is unbreakable in the asymptotic sense.** Random noise cannot trigger the alarm; only structural distribution shift can. The MAPE-K loop fires only on confirmed structural drift.

#### File targets

- `uasr/drift_detector.py` — replace IQR statistic with `WassersteinMartingaleDetector(alpha=0.001, reference_window=1000)`
- New `uasr/martingale.py` — pure-Python implementation of the bound + martingale update
- New eval-gate Layer (S18): inject random noise (no structural drift) over 10k batches; assert false-positive rate ≤ 0.5% (loose bound — true bound is α=0.1%). Then inject structural drift; assert detection within K batches.

#### Adoption sprint

**Sprint 18** (Causal-RL Self-Healing). The Wasserstein-Azuma-Hoeffding detector becomes the trigger; the causal-RL evaluator picks the recovery shim; the router pattern (Evolution 3) deploys it without pause.

---

### Guardrail 3 — PID Control for Backpressure

**The current heuristic** (`pipeline/streaming/backpressure.py:42::max_buffer_size`): a static `max_buffer_size=10_000` cliff with `DROP_TAIL` when exceeded. This introduces hard processing cliffs (sudden drops at the boundary), resource underutilization (queue runs near-empty most of the time), and oscillation under variable load (queue fills, drops fire, queue empties, repeat).

**The replacement** (Hellerstein-Diao 2004): a **Proportional-Integral-Derivative (PID)** feedback loop. The ingestion service continuously scales the polling interval based on downstream consumption rates, keeping memory usage perfectly optimized around a target capacity.

#### Mathematical formulation

Let `B(t)` be the current backpressure queue depth at time `t`, and `B_target` the optimal queue capacity (e.g., 70% of `max_buffer_size`):

```
B_target = 0.7 · max_buffer_size
e(t)     = B_target - B(t)
```

The PID controller computes the adjustment `u(t)` to the ingestion sleep window `Δ_tick`:

```
u(t) = K_p · e(t) + K_i · ∫_0^t e(τ) dτ + K_d · de(t)/dt
```

The three terms:

- **Proportional `K_p · e(t)`** — responds to current error magnitude
- **Integral `K_i · ∫ e(τ) dτ`** — accumulates past error to eliminate steady-state offset
- **Derivative `K_d · de(t)/dt`** — anticipates future error from current rate of change

The new sleep window: `Δ_tick' = clamp(Δ_tick - u(t), Δ_min, Δ_max)`.

#### Tuning

Default `K_p, K_i, K_d` derived via Ziegler-Nichols method on the system's open-loop step response. For typical AURA workloads (n=10000 buffer, n=1ms tick):

```
K_p = 0.5,  K_i = 0.1,  K_d = 0.05    (conservative defaults)
```

Operators override per-deployment via env: `AURA_BACKPRESSURE_KP`, `..._KI`, `..._KD`.

#### Operational contract

- The ingestion sleep window dynamically scales between `Δ_min` (high throughput, queue near-empty) and `Δ_max` (low throughput, queue at target)
- Queue depth `B(t)` converges to `B_target` over O(1/K_i) timescale (integral controller's settling time)
- Hard buffer-overflow events become rare; the controller adjusts ingestion rate before the queue saturates
- **Provable stability** (Hellerstein-Diao Theorem 6.2): for properly tuned `(K_p, K_i, K_d)`, the closed-loop system is BIBO stable — bounded ingestion produces bounded queue depth

#### File targets

- `pipeline/streaming/backpressure.py` — add `PIDBackpressureController(b_target, kp, ki, kd)`; the existing `DROP_TAIL` policy becomes a hard fallback when the controller diverges (e.g., divide-by-zero on degenerate workloads)
- `pipeline/streaming/streaming_engine.py` — call the controller every loop iteration to update the next-poll `Δ_tick`
- Update `shared/observability.py` to emit Prometheus gauges for `B(t)`, `e(t)`, `u(t)` so operators can tune `(K_p, K_i, K_d)` empirically
- New eval-gate Layer (S20): simulate variable-rate ingestion; assert queue depth stays within `[B_target ± 0.1·max_buffer_size]` for 90%+ of the time window

#### Adoption sprint

**Sprint 20**. The PID controller is the third deepening (alongside ABS and Dataflow watermarks) of the streaming substrate.

---

## Adoption Sequence

| Sprint | Plan-file pillar | Adds from this document |
|---|---|---|
| **S18** | Pillar 1 — Causal-RL Self-Healing | Evolution 3 (Dynamic Reconfiguration) + Guardrail 2 (Wasserstein-Azuma-Hoeffding drift detection) |
| **S20** | Pillar 4 — Scheduler v2: Distributed | Evolution 1 (ABS Checkpointing) + Evolution 2 (Dataflow Watermarks) + Guardrail 3 (PID Backpressure) |
| **S19, S21, S22, S23** | (no streaming impact) | Continue as planned (Merkle audit log, Service Factory v2, TMLE, E-value) |

Each evolution + guardrail ships with:

1. A specific file rewrite (named above)
2. One new eval-gate Layer that assertably tests the formal contract
3. A memory file documenting the non-obvious decisions
4. A CI-verified bundle commit
5. References back to this document via wiki-style links in the sprint memory

---

## Reading List (Annotated)

The six papers below are the canonical references for this evolution. Listed in approximate reading order — Chandy-Lamport first establishes the foundational distributed-snapshot result; Carbone et al. shows the production implementation in Apache Flink; Akidau et al. is the unifying framework that ties watermarks, windows, and triggers; Kephart-Chess + Kramer-Magee underpin the autonomic-reconfiguration story; Bifet-Gavalda and Hellerstein-Diao provide the math for the two formal guardrails.

### 1. Chandy & Lamport (1985) — Foundational Distributed Snapshots

> Chandy, K. M., & Lamport, L. (1985). *"Distributed Snapshots: Determining Global States of Distributed Systems."* ACM Transactions on Computer Systems (TOCS) 3(1):63-75.

**What it proves:** that a globally consistent snapshot of a distributed system can be obtained *without halting any process*. The "marker algorithm" injects special marker messages into communication channels; each process snapshots its local state when it first receives a marker and records subsequent incoming messages until it receives a marker on every channel.

**Why read it:** Evolution 1 (ABS) is the direct engineering descendant. Carbone et al. extend the marker algorithm with the alignment optimisation that makes it practical for production streaming.

**Key insight to internalise:** consistency is not the same as synchrony. A snapshot can be globally consistent without being instantaneous — the cut just has to satisfy the causality constraint.

### 2. Carbone, Fekete, Ewen, Haridi, Katsifodamos & Markl (2015) — Apache Flink ABS

> Carbone, P., et al. (2015). *"Lightweight Asynchronous Snapshots for Distributed Dataflows."* arXiv:1506.08603.

**What it ships:** the alignment-based ABS algorithm that backs Apache Flink's exactly-once-processing guarantee. Operators block individual input channels (not the whole operator) while waiting for barrier alignment, then snapshot asynchronously and forward the barrier downstream.

**Why read it:** Evolution 1's file targets (`state_manager.py`, `barrier.py`, `window_processor.py`) implement exactly this algorithm. The paper's Section 3 is the implementation blueprint.

**Key insight to internalise:** the alignment phase is cheap because the per-channel block is typically microseconds; the snapshot phase is expensive but happens asynchronously off the critical path. The combined latency overhead is sub-millisecond per checkpoint.

### 3. Akidau, Bradshaw, Chambers, Chernyak, et al. (2015) — The Dataflow Model

> Akidau, T., et al. (2015). *"The Dataflow Model: A Practical Approach to Balancing Correctness, Latency, and Cost in Massive-Scale, Unbounded, Out-of-Order Data Processing."* Proceedings of the VLDB Endowment 8(12):1792-1803.

**What it ships:** the unified watermark + window + trigger formalism that powers Google Cloud Dataflow, Apache Beam, and (with adaptations) Flink. The paper's contribution is showing how to decompose "stream processing" into four orthogonal concerns: *what* (window assignment), *where in event time* (window function), *when in processing time* (trigger), *how* (accumulation mode).

**Why read it:** Evolution 2's `WatermarkTracker`, `triggers.py`, `late_data.py` are direct implementations of the paper's primitives. Sections 2.2 (windowing) and 2.3 (triggers) are the implementation spec.

**Key insight to internalise:** event time and processing time are orthogonal; conflating them is the source of most stream-processing bugs. Watermarks track event-time progress; triggers fire on processing-time or watermark events; the two interact only through explicit configuration.

### 4. Kephart & Chess (2003) — Autonomic Computing

> Kephart, J. O., & Chess, D. M. (2003). *"The Vision of Autonomic Computing."* IEEE Computer 36(1):41-50.

**What it ships:** the MAPE-K (Monitor-Analyze-Plan-Execute-Knowledge) reference architecture for self-healing systems. This is the conceptual ancestor of AURA's `uasr.mapek_worker`.

**Why read it:** when refactoring the worker to support Evolution 3 (Dynamic Reconfiguration), the MAPE-K phases stay; only the Execute phase changes (router-based hot-swap instead of pause-deploy-resume). The paper's Section 3 catalogues failure modes the architecture must handle.

**Key insight to internalise:** self-healing is an architectural property, not a feature. The MAPE-K loop has to be designed in from the start; bolting it on later doesn't work because every component needs to expose Monitor and Execute hooks.

### 5. Kramer & Magee (1990) — Dynamic Change Management

> Kramer, J., & Magee, J. (1990). *"The Evolving Philosophers Problem: Dynamic Change Management."* IEEE Transactions on Software Engineering 16(11):1293-1306.

**What it proves:** the formal conditions under which a running distributed system can be reconfigured safely. The paper's core contribution is the **quiescence** criterion (defined above in Evolution 3) — an operator can be safely removed iff it reaches the quiescent state.

**Why read it:** Evolution 3's router-based hot-swap implements the canonical Kramer-Magee algorithm. Section 4 specifies the quiescence protocol that `_drain_v1()` must implement.

**Key insight to internalise:** dynamic reconfiguration is solved theory but tricky engineering. The router pattern works because it enforces quiescence by gradual traffic shift; ad-hoc reconfiguration (mutating in-process state) cannot prove quiescence and so cannot prove safety.

### 6a. Bifet & Gavalda (2007) — ADWIN Adaptive Windowing

> Bifet, A., & Gavalda, R. (2007). *"Learning from Time-Changing Data Streams with Adaptive Windowing."* SIAM International Conference on Data Mining.

**What it ships:** ADWIN — Adaptive Windowing — an online algorithm that maintains a sliding window of variable length and detects distribution change with formal guarantees on false-positive rate. ADWIN's bounds are based on Hoeffding's inequality, the same family of concentration bounds Guardrail 2 uses.

**Why read it:** Guardrail 2's Wasserstein-Azuma-Hoeffding detector is in spirit ADWIN applied to Wasserstein-1 distances. The paper's Section 3 proves the false-positive-rate guarantee that we restate above.

**Key insight to internalise:** all concentration-bound-based drift detectors have the same shape: track a statistic over a window, bound the deviation under the null hypothesis, fire when the observed deviation exceeds the bound. The choice of statistic (IQR / KS distance / Wasserstein) trades off computational cost against power against specific drift shapes.

### 6b. Hellerstein, Diao, Parekh & Tilbury (2004) — Feedback Control of Computing Systems

> Hellerstein, J. L., Diao, Y., Parekh, S., & Tilbury, D. M. (2004). *Feedback Control of Computing Systems*. John Wiley & Sons. ISBN 978-0-471-26637-2.

**What it ships:** the definitive textbook on applying classical PID + state-space control to software systems. Chapters 6 (PID design) and 9 (queueing-system control) are direct templates for Guardrail 3.

**Why read it:** Guardrail 3's `PIDBackpressureController` and the `(K_p, K_i, K_d)` tuning approach come directly from this text. The Ziegler-Nichols tuning method (Chapter 6.5) is what the file targets specify.

**Key insight to internalise:** the integral term `K_i` is what eliminates steady-state offset; without it, the controller will leave the queue persistently above or below `B_target`. Tuning `K_i` is the bulk of the work; `K_p` and `K_d` are usually less sensitive.

---

## Structural Summary for Architecture Reviews

When walking into a technical review session with principal systems architects, frame the progression as:

```
[Current Layout: Micro-Batching]                  [Target: Distributed Fabric]
─────────────────────────────────────────────────────────────────────────────
Kafka → local Parquet → DuckDB (Txn)              NATS / Redpanda → ABS Checkpointing
Wall-clock checkpoint intervals                    In-stream barrier markers (Carbone 2015)
Coarse checkpoints freeze ingest                   Async snapshot, zero ingest pause
Local watermarks per operator                      Composite watermarks across DAG (Akidau 2015)
Static buffer + DROP_TAIL cliff                    PID controller targets B_target (Hellerstein 2004)
IQR drift detection (false positives)              Wasserstein-Azuma-Hoeffding (bounded α)
MAPEKWorker.pause() halts ingest                   Router-based canary hot-swap (Kramer 1990)
                                                   Quiescence-proven safe reconfiguration
```

The transition is principled, not opportunistic: each evolution is grounded in a published theorem; each guardrail comes with a formal false-positive or stability bound. AURA stops competing with chat-wrapper agent platforms and starts competing with Apache Flink + Kafka Streams on the distributed-systems frontier — while keeping the audit-engine, MAPE-K, and counterfactual-RL primitives that differentiate it from those frameworks.

---

## Open Questions for Architecture Review

Items the team should debate before S18 / S20 implementation begins:

1. **Barrier injection cadence.** Default 30s gives ~30s recovery worst-case (one barrier-interval). High-volume deployments may want 5-10s; ultra-low-latency may want 60s+. What's the right per-deployment knob structure?

2. **Wasserstein computation cost.** 1-D Wasserstein-1 is O(n log n) via sorting; multivariate Wasserstein-p is much more expensive. Do we use 1-D per-column (cheap, ignores cross-column correlation) or multivariate (expensive, captures correlation)? Recommendation: 1-D per column with an explicit `correlation_drift_detector` as a separate primitive for cross-column shifts.

3. **PID divergence handling.** If `(K_p, K_i, K_d)` are mis-tuned the controller can oscillate or diverge. Fallback to static `DROP_TAIL` when divergence detected (definition?) — or fail closed (refuse to accept ingestion at all)?

4. **Router-based canary fraction curve.** Linear 10% → 100% over N batches? Exponential ramp? Based on canary's empirical drift score? Recommendation: empirical — the router watches the candidate shim's output drift score and promotes monotonically as drift decreases.

5. **Quiescence timeout.** Kramer-Magee proves safety but not liveness — what if V1 never reaches quiescence? Hard timeout after which we force-terminate V1 and accept the data-loss window?

6. **Cross-region barrier propagation.** Sprint 20's distributed-scheduler vision implies barriers may need to cross region boundaries. Is the per-region barrier the leader's responsibility, or is there a global barrier coordinator? Recommendation: per-region barriers, with the multi-region recovery handled at the application layer (replay from per-region snapshots).

7. **Sprint 18 + Sprint 20 sequencing.** S18 ships dynamic reconfiguration before S20 ships ABS. This means S18's hot-swap router runs on top of the current wall-clock checkpoint mechanism; the router itself is safe, but recovery after a mid-swap node failure is limited to the last wall-clock checkpoint until S20 lands. Acceptable for the S18 → S20 window?

---

## Document Lifecycle

- **Authored:** 2026-05-16 as part of the enterprise-pillar reframing
- **Owners:** AURA architecture team; updates land via PR with reviewers from the streaming + UASR subsystems
- **Sprint references:** S18 and S20 sprint memory files (`project_aura_sprint{18,20}_*.md`) link back here for the math
- **Update cadence:** revise after each sprint that touches `pipeline/streaming/` or `uasr/` — append a "What changed this sprint" section
