# Ruggedization — linus-pai

Reliability & fault-tolerance standard for this repository. Every component must
meet this bar before it is considered production-ready.

**Repo snapshot:** Python ~8710 LOC, Shell/Infra ~882 LOC.
**Primary stack:** Python.
**Status:** **STANDARD DEFINED** — apply the checklist below. The reference implementations of every item live in `PhysAIOS/v2` (Python) and `PhysAI-cpp/v2` (C++); port the pattern to this repo's stack.

## The mantra (non-negotiable)

> **Handle all errors at the source. Recover within each class / method / call
> whenever possible. Do not rethrow (or propagate) when there is an alternative.**

An exception must never escape a function that could have handled it locally.
Prefer an error *return* (sentinel / `Result` / `Option` / error payload / bounded
safe fallback) over raising. Validate inputs at the entry of each method and
degrade gracefully in place. Callers should not be burdened with recovery.

## 24x7x365x1000 fault-tolerance principles

The system must run continuously and survive any single fault. Engineer for:

1. **No unhandled exception can take the process down.** Every entry point and
   every worker/loop/thread/task has a last-resort backstop; every *call* below
   it already recovers in place so the backstop effectively never fires.
2. **Self-healing supervision.** Long-running work runs under a supervisor with a
   **heartbeat**, a **watchdog**, **per-cycle exception recovery with bounded
   (exponential) backoff**, and **auto-restart** of a dead worker. Transient
   faults self-heal; a hard hang flips the service to a **degraded/not-ready**
   state so an orchestrator (systemd/k8s) can restart it.
3. **Liveness / readiness / metrics probes.** Expose `/health` (process up),
   `/ready` (heartbeat fresh & not degraded; non-200 when degraded), and
   `/metrics` (cycles, errors, restarts, uptime, heartbeat age). Probes are
   lock-free so they answer even mid-stall.
4. **Graceful degradation.** On bad input or numerical/ill-conditioned failure,
   fall back to a **bounded safe value** (brake/stop/last-known-good), never
   propagate `NaN`/`Inf`/garbage. Validate finiteness at layer boundaries.
5. **Bounded resources for infinite uptime.** No unbounded queues/caches/logs;
   reference-counted/GC'd buffers; ring buffers; 64-bit (or arbitrary-precision)
   monotonic counters; **monotonic clocks** (never wall-clock for elapsed time).
6. **Last-known-good output.** Readers always get the last valid result even if
   the current cycle failed.
7. **Idempotent, crash-only recovery.** Restart-in-place must be safe and
   deterministic; persist only what is needed to resume.
8. **Timeouts & retries with backoff + jitter** on every external call; circuit
   breakers around dependencies; never an unbounded wait.
9. **Fault-injection tests.** Unit tests that feed `NaN`/`Inf`/empty/malformed/
   singular/timeout inputs and assert bounded, finite, no-throw behaviour; plus
   supervisor tests (throwing worker self-heals, stall -> degraded -> recover).

## Python ruggedization checklist

- [ ] **Don't `raise` where an alternative exists.** Return an error payload /
      `None` / a `Result` dataclass / a bounded safe fallback; validate at method
      entry and recover in place.
- [ ] **No bare exception escaping a thread/task/loop.** Wrap workers in
      `try/except Exception` with logging + bounded backoff + continue; `asyncio`
      tasks supervised + restarted on failure (`add_done_callback`).
- [ ] **Numerical guards**: `np.isfinite` at boundaries; `safe_inverse`
      (regularized) instead of `np.linalg.inv`; `ridge_lstsq` instead of `lstsq`;
      drop corrupt samples; never propagate `NaN`.
- [ ] **Bounded resources**: `collections.deque(maxlen=...)`, capped caches; close
      DB/file/socket handles (context managers); monotonic clock (`time.monotonic`).
- [ ] **Self-healing supervisor** (threaded or async) with heartbeat + watchdog +
      `/health` `/ready` `/metrics`; graceful shutdown on SIGINT/SIGTERM.
- [ ] **Timeouts + retries with backoff** on every network/subprocess call.
- [ ] **`unittest`/`pytest` fault injection** (NaN/Inf/singular/malformed) + a
      supervisor self-heal/stall test.

**Reference implementation:** `PhysAIOS/v2/physaios_v2` — `reliability.py`,
`supervisor.py`, hardened `safety/cognitive/telemetry/simulator/license`, the
`/health` `/ready` `/metrics` probes in `app.py`, and `tests/test_reliability.py`.

## How to verify

A change is "ruggedized" only when:
1. no recoverable error path raises/propagates that could be handled locally;
2. every long-running unit is supervised (heartbeat + watchdog + backoff + restart);
3. malformed / `NaN` / `Inf` / empty / timeout inputs degrade gracefully (proven
   by fault-injection tests, not by inspection);
4. the service answers `/health` `/ready` `/metrics` and shuts down cleanly on a
   signal;
5. resources stay bounded over an indefinite run (no leaks, monotonic clocks,
   wide/overflow-safe counters).

---
*Generated as part of the org-wide ruggedization rollout. Reference
implementations: [`PhysAIOS/v2`](../PhysAIOS/v2) (Python) and
[`PhysAI-cpp/v2`](../PhysAI-cpp/v2) (C++).*
