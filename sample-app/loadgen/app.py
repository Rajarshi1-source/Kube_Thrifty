#!/usr/bin/env python3
"""
Synthetic workload generator for the KubeThrifty demo cluster.

One image, five profiles. Each profile reproduces one of the §3 demand shapes, because the shape --
not the average -- is what the product reasons about:

  steady        flat load. The easy case; a percentile sizer gets this right too.
  bursty        long quiet periods punctuated by short, tall spikes. THE case that matters: the
                spike lands above P95, so a percentile-based sizer discards it and ships a memory
                request below real demand. Sizing off the observed peak keeps it alive.
  cpu_heavy     sustained CPU near its limit, so CFS throttling is visible in
                container_cpu_cfs_throttled_periods_total. Compressible: it gets slow, not dead.
  memory_heavy  allocates, holds, and releases in a sawtooth like a GC'd runtime. RSS lags real
                demand, which is why MEM_HEADROOM_FOR_GC exists.
  nearly_idle   almost nothing. Enormous waste percentage, negligible absolute saving -- which is
                exactly why savings are reported as a node delta and not as summed millicores.

These workloads are DELIBERATELY over-provisioned in the Helm chart (5.5 CPU / 6.5 GiB requested
against a ~1.7 CPU / 2.6 GiB real peak). That gap is the demo. Do not "fix" the requests.

Exposes /healthz, /readyz and a tiny /metrics for eyeballing what the generator thinks it is doing.
The real measurements come from cAdvisor and the cgroup-truth collector, never from this process.
"""
from __future__ import annotations

import http.server
import json
import math
import os
import random
import socketserver
import threading
import time

PROFILE = os.environ.get("WORKLOAD_PROFILE", "steady")
SERVICE = os.environ.get("SERVICE_NAME", "unknown")
PORT = int(os.environ.get("PORT", "8080"))

# Target *actual* consumption. Deliberately far below the declared requests.
CPU_TARGET_MILLICORES = int(os.environ.get("CPU_TARGET_MILLICORES", "100"))
MEM_TARGET_MIB = int(os.environ.get("MEM_TARGET_MIB", "128"))

# Spike geometry for the bursty profile. PEAK_MULTIPLIER is the whole point: with a 60s spike every
# 900s, the spike occupies ~7% of samples, so it sits just above P95 and a percentile sizer drops it.
SPIKE_PERIOD_S = int(os.environ.get("SPIKE_PERIOD_S", "900"))
SPIKE_DURATION_S = int(os.environ.get("SPIKE_DURATION_S", "60"))
PEAK_MULTIPLIER = float(os.environ.get("PEAK_MULTIPLIER", "3.0"))

MIB = 1024 * 1024
_state: dict[str, float] = {"cpu_millicores": 0.0, "mem_mib": 0.0, "spiking": 0.0}
_ballast: list[bytearray] = []
_lock = threading.Lock()


def _burn_cpu(millicores: float, slice_s: float = 0.1) -> None:
    """Busy-spin for `millicores/1000` of each `slice_s` window, then sleep the remainder.

    Duty-cycling like this produces a stable, predictable utilisation that shows up cleanly in
    container_cpu_usage_seconds_total, instead of the sawtooth a naive tight loop would give.
    """
    duty = max(0.0, min(1.0, millicores / 1000.0))
    busy_until = time.perf_counter() + slice_s * duty
    x = 0.0
    while time.perf_counter() < busy_until:
        for _ in range(2000):
            x += math.sqrt(random.random() + 1.0)
    idle = slice_s * (1.0 - duty)
    if idle > 0:
        time.sleep(idle)


def _hold_memory(target_mib: float) -> None:
    """Resize the ballast so RSS tracks `target_mib`. Touch pages so they are really resident."""
    target_chunks = max(0, int(target_mib))
    with _lock:
        while len(_ballast) < target_chunks:
            chunk = bytearray(MIB)
            # Touch every page: without this the kernel may never fault them in and RSS stays low,
            # which would make the demo lie about memory demand.
            for off in range(0, MIB, 4096):
                chunk[off] = 1
            _ballast.append(chunk)
        while len(_ballast) > target_chunks:
            _ballast.pop()
        _state["mem_mib"] = float(len(_ballast))


def _profile_targets(t: float) -> tuple[float, float, bool]:
    """Return (cpu_millicores, mem_mib, spiking) for wall-clock second `t`."""
    if PROFILE == "steady":
        # Mild sinusoid so the forecaster has something to model, but no tall tail.
        wobble = 1.0 + 0.12 * math.sin(t / 300.0)
        return CPU_TARGET_MILLICORES * wobble, MEM_TARGET_MIB, False

    if PROFILE == "bursty":
        in_spike = (t % SPIKE_PERIOD_S) < SPIKE_DURATION_S
        if in_spike:
            return (CPU_TARGET_MILLICORES * PEAK_MULTIPLIER,
                    MEM_TARGET_MIB * PEAK_MULTIPLIER, True)
        return CPU_TARGET_MILLICORES * 0.45, MEM_TARGET_MIB * 0.6, False

    if PROFILE == "cpu_heavy":
        # Sustained and high, with enough variance to be realistic. Memory stays flat and small:
        # this workload should be cut on memory and NOT cut on CPU.
        wobble = 1.0 + 0.20 * math.sin(t / 120.0)
        return CPU_TARGET_MILLICORES * wobble, MEM_TARGET_MIB, False

    if PROFILE == "memory_heavy":
        # Sawtooth: climb to the target, drop to ~65%, climb again. Mimics a GC'd heap, where
        # instantaneous RSS understates the demand the allocator actually needs headroom for.
        phase = (t % 600.0) / 600.0
        occupancy = 0.65 + 0.35 * phase
        return CPU_TARGET_MILLICORES, MEM_TARGET_MIB * occupancy, False

    if PROFILE == "nearly_idle":
        # A cron-ish twitch every 5 minutes, otherwise asleep.
        twitch = (t % 300.0) < 5.0
        return (CPU_TARGET_MILLICORES * (4.0 if twitch else 0.35),
                MEM_TARGET_MIB, False)

    return CPU_TARGET_MILLICORES, MEM_TARGET_MIB, False


def _worker() -> None:
    start = time.time()
    _hold_memory(MEM_TARGET_MIB)
    while True:
        t = time.time() - start
        cpu, mem, spiking = _profile_targets(t)
        _state["cpu_millicores"] = cpu
        _state["spiking"] = 1.0 if spiking else 0.0
        if abs(mem - _state["mem_mib"]) >= 1.0:
            _hold_memory(mem)
        _burn_cpu(cpu)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802  (stdlib-mandated name)
        if self.path in ("/healthz", "/readyz"):
            self._send(200, b"ok\n", "text/plain")
        elif self.path == "/metrics":
            body = (
                "# HELP loadgen_target_cpu_millicores What the generator is aiming to consume.\n"
                "# TYPE loadgen_target_cpu_millicores gauge\n"
                f'loadgen_target_cpu_millicores{{service="{SERVICE}",profile="{PROFILE}"}} '
                f'{_state["cpu_millicores"]:.1f}\n'
                "# HELP loadgen_target_memory_mib Resident ballast the generator is holding.\n"
                "# TYPE loadgen_target_memory_mib gauge\n"
                f'loadgen_target_memory_mib{{service="{SERVICE}",profile="{PROFILE}"}} '
                f'{_state["mem_mib"]:.1f}\n'
                "# HELP loadgen_spiking 1 while the bursty profile is inside a spike.\n"
                "# TYPE loadgen_spiking gauge\n"
                f'loadgen_spiking{{service="{SERVICE}",profile="{PROFILE}"}} '
                f'{_state["spiking"]:.0f}\n'
            ).encode()
            self._send(200, body, "text/plain; version=0.0.4")
        else:
            self._send(200, json.dumps({"service": SERVICE, "profile": PROFILE}).encode() + b"\n",
                       "application/json")

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """Silence per-request logging: it would dwarf the signal in `kubectl logs`."""


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main() -> None:
    print(f"loadgen: service={SERVICE} profile={PROFILE} "
          f"cpu_target={CPU_TARGET_MILLICORES}m mem_target={MEM_TARGET_MIB}Mi", flush=True)
    threading.Thread(target=_worker, daemon=True).start()
    Server(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
