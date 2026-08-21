# ADR 0001 — PSI availability on the WSL2 host, and the resulting evidence tier

- **Status:** accepted
- **Date:** 2026-08-21
- **Phase:** 0 (toolchain + platform prerequisites)

## Context

KubeThrifty's headline claim is that it sizes from *observed suffering*, not just usage. The
evidence layer (§23) leans on two Linux-only facilities:

- **cgroup v2** for `memory.peak` — the sizing basis for memory, because a percentile discards the
  top 5% of samples, which are exactly the ones that OOMKill a container.
- **Kernel PSI** (`/proc/pressure/*`, `cpu.pressure`, `memory.pressure`) for stall time, which is
  what distinguishes "ran hot and was fine" from "ran hot and stalled".

The demo host is Windows, so every cluster-facing feature runs against Linux nodes inside
Docker Desktop / WSL2. PSI is frequently absent from vendor kernels, and it is compiled out or
runtime-disabled often enough that the plan required this to be **settled in Phase 0 rather than
discovered in Phase 6**. If PSI were unavailable, §23 would degrade to throttle + OOM + restarts
and every recommendation would ship `evidence: partial`.

## Decision

**PSI is available. The project runs on the full evidence tier.**

Verified on the WSL2 distribution that backs Docker Desktop:

| Check | Requirement | Observed |
| --- | --- | --- |
| Kernel | ≥ 4.20 for PSI | `6.18.33.1-microsoft-standard-WSL2` |
| `stat -fc %T /sys/fs/cgroup` | `cgroup2fs` | `cgroup2fs` |
| `/proc/pressure/` | exists | `cpu io memory` |
| `/proc/pressure/cpu` | returns `some` + `full` | `some avg10=1.33 avg60=0.28 avg300=0.06 total=221439` |
| `CONFIG_PSI` | `y` | `CONFIG_PSI=y` |
| `CONFIG_PSI_DEFAULT_DISABLED` | unset, or `psi=1` boot arg | **not set** |
| `/sys/fs/cgroup/memory.pressure` | exists (cgroup-level PSI) | present |

`CONFIG_PSI_DEFAULT_DISABLED` being unset is the load-bearing detail: PSI is compiled in *and*
enabled by default, so no `psi=1` needs to be added to `.wslconfig` `kernelCommandLine`. Both the
system-wide and the per-cgroup interfaces are live, which is what the cgroup-truth DaemonSet reads.

### Re-verified on the actual cluster nodes

The checks above were run on the WSL2 host. A kind node is a *container*, so the properties had to be
confirmed again inside one — a host with PSI does not guarantee a node container that exposes it.
Confirmed on `kubethrifty-worker` in the running 3-node cluster:

| Check | Observed |
| --- | --- |
| Kubernetes | `v1.36.1` on all 3 nodes, all `Ready` |
| Container runtime | `containerd://2.3.1` (the 2.x line; 1.7 does not expose PSI) |
| `stat -fc %T /sys/fs/cgroup` | `cgroup2fs` |
| `/proc/pressure/cpu` | `some avg10=0.00 avg60=0.16 avg300=0.30 total=8960918` |
| `/sys/fs/cgroup/memory.pressure` | present, reporting `some` and `full` |

The decision therefore holds at the layer that matters: the DaemonSet will find a real cgroup tree
with live PSI on the nodes it is scheduled onto.

## Consequences

- `size_memory` uses cgroup `memory.peak` as its sizing basis, and recommendations carry
  `evidence_tier` of `rehearsed` or `modelled` — **not** `partial` — once the collector is deployed.
- Rehearsal verdicts can use the `PSI_FULL_CEILING = 0.05` trip condition, so a rehearsal can fail
  for stalling even when nothing was throttled and nothing was OOMKilled. Without PSI those
  verdicts would have been materially weaker.
- The `partial` tier remains implemented and reachable. It is the correct output whenever
  `container_pressure_*` is empty for a workload, and per the null-safety rule an empty PSI series
  must downgrade the tier — it must never be read as `0` / "no pressure".
- This finding is host-specific. Any deployment onto a cluster whose nodes lack PSI or run
  containerd 1.7 falls back to `partial`; the collector exports a scrape-health metric precisely so
  a silent collector cannot masquerade as a healthy one.

## Toolchain recorded alongside this decision

| Tool | Mandate | Installed |
| --- | --- | --- |
| Python | 3.14 | 3.14.6 |
| Node.js | 24 LTS | v24.12.0 |
| kubectl | ≥ 1.32 (for `--subresource resize`) | v1.36.1 |
| kind | current | v0.32.0 |
| Helm | 4.2.x | v4.2.4 |
| Docker Desktop | WSL2 backend | 4.87.0 |

`numpy==2.4.4` and `pandas==3.0.2` both resolved as `cp314` wheels on Python 3.14, so the exact
pins in `requirements.txt` hold without a source build.

### Host changes required to get here

Two environment problems had to be fixed before any of this could be verified, both recorded because
they will recur on any similar Windows host:

1. **Docker's disk image was relocated from `C:` to `D:\DockerData`.** `C:` had 0.3 GB free and the
   TimescaleDB pull exhausted it, which took the Docker Linux engine down with an opaque
   `500 Internal Server Error` on `/_ping` — a disk-space failure wearing an API error's clothes.
   The effective setting is `CustomWslDistroDir` in `%APPDATA%\Docker\settings-store.json`;
   `DataFolder` alone is recorded but not acted upon, and the existing distro must be unregistered
   so Docker recreates it at the new path.

2. **The compose host port for PostgreSQL moved to 5433.** A native `postgresql-x64-17` service
   already owned 5432, and Docker's bind failure (`An attempt was made to access a socket in a way
   forbidden by its access permissions`) does not name the conflict. All compose host ports are now
   overridable via `KT_DB_PORT`, `KT_REDIS_PORT`, `KT_PROMETHEUS_PORT`, `KT_GRAFANA_PORT` and
   `KT_DASHBOARD_PORT`; container-side ports stay canonical.

### One config error worth remembering

The first `kind create` attempt failed with `could not find a log line that matches "Reached target
.*Multi-User System.*"`. The cause was an `extraMounts` entry in `k8s/kind/cluster.yaml` mounting the
host's `/sys/fs/cgroup` read-only over the node container's own cgroup filesystem, which prevents
systemd from booting inside the node. A kind node needs its own writable cgroup tree; the
cgroup-truth DaemonSet reaches the same data through a pod `hostPath` volume, where the "host" is the
node container. The mount was removed and is documented in the config so it does not come back.
