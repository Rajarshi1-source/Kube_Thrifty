# Platform Prerequisites & Migration Reference

Load this when standing up a cluster, debugging missing PSI or cgroup data, migrating Helm 3 → 4, or
writing the README prerequisites section. These are the things that make features silently return nothing
rather than fail loudly, which is why they belong in a reference you actually read.

## Contents
1. Node prerequisites and how to verify each one
2. kind cluster config for the demo
3. kube-prometheus-stack values that PSI needs
4. Helm 3 → 4 migration checklist
5. The cgroup-truth DaemonSet manifest
6. Pinned-versions checklist for the repo
7. The two scaling decisions to document

---

## 1. Node prerequisites — verify, don't assume

| Requirement | Why | Verify |
|---|---|---|
| **cgroup v2** | Mandatory from Kubernetes 1.35; the sizing basis (`memory.peak`) and PSI both live there | `stat -fc %T /sys/fs/cgroup` → `cgroup2fs` |
| **Kernel ≥ 4.20 with `CONFIG_PSI=y`** | PSI is a kernel feature | `zgrep CONFIG_PSI /proc/config.gz`, or `cat /proc/pressure/cpu` |
| **PSI enabled at boot** | Some distros compile it in but disable it | if `/proc/pressure/*` is missing, add `psi=1` to the kernel cmdline |
| **containerd 2.x** | PSI plumbing is not in the 1.7 line | `containerd --version` on the node |
| **Linux nodes** | No PSI on Windows nodes | mixed clusters are fine; the collector has `nodeSelector: kubernetes.io/os: linux` |
| **kubelet cadvisor scrape enabled** | PSI is exposed on `/metrics/cadvisor` | the check below |

```bash
NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl get --raw "/api/v1/nodes/$NODE/proxy/metrics/cadvisor" \
  | grep -E 'container_pressure_(cpu|memory)_(stalled|waiting)_seconds_total' | head

# in-place resize available?
kubectl version --short           # server >= 1.35, client >= 1.32 for --subresource
kubectl explain pod.spec.containers.resizePolicy
```

If PSI is empty: recommendations must ship as `evidence: partial` and the README must say which nodes lack
it. Never let an empty metric read as "no pressure" — that is the failure mode that turns a safety feature
into a rubber stamp.

---

## 2. kind config for the demo

```yaml
# k8s/kind/cluster.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: kubethrifty
nodes:
  - role: control-plane
    image: kindest/node:v1.36.0          # pin the patch you tested; never :latest
  - role: worker
    image: kindest/node:v1.36.0
  - role: worker
    image: kindest/node:v1.36.0
featureGates:
  # InPlacePodVerticalScaling and KubeletPSI are GA on 1.36 — do NOT set them explicitly,
  # it emits "setting GA feature gate" warnings. Set only what is still beta and needed:
  InPlacePodLevelResourcesVerticalScaling: true
kubeadmConfigPatches:
  - |
    kind: KubeletConfiguration
    serverTLSBootstrap: true
```

Three workers, because bin-packing and consolidation demos need somewhere to consolidate *to*, and the
rehearsal preflight needs workloads with ≥2 replicas spread across nodes.

---

## 3. kube-prometheus-stack values that matter

```yaml
# k8s/prometheus/kube-prometheus-stack-values.yaml
kubelet:
  enabled: true
  serviceMonitor:
    cAdvisor: true                 # <- PSI + throttling + oom_events arrive through here
    probes: true
    resource: true
    honorLabels: true              # keep pod/container labels from the kubelet
prometheus:
  prometheusSpec:
    scrapeInterval: 15s
    retention: 14d                 # the analyser's window is 7d; keep headroom for backtests
    resources:                     # eat your own dog food — pinned, modest, right-sized later
      requests: {cpu: 200m, memory: 1Gi}
grafana:
  image: {tag: "13.0.x"}           # pin the patch you tested
  defaultDashboardsEnabled: true
  sidecar:
    dashboards: {enabled: true, label: grafana_dashboard}
```

Install with the chart version pinned, from the OCI artefact:

```bash
helm install kps oci://ghcr.io/prometheus-community/charts/kube-prometheus-stack \
  --version <PINNED_CHART_VERSION> -n monitoring --create-namespace \
  -f k8s/prometheus/kube-prometheus-stack-values.yaml
```

---

## 4. Helm 3 → 4 migration checklist

Helm 3 stopped receiving bug fixes on **8 July 2026** and security support ends **10 Feb 2027**. Helm 4.0
GA'd 12 Nov 2025; pin **4.2.x**.

- [ ] **CI pins Helm 4** (`azure/setup-helm` with an explicit `version:`). Both binaries can coexist
      locally during migration.
- [ ] **`--wait` needs the `watch` verb** on every resource kind in the chart — Helm 4 uses kstatus, and a
      missing `watch` permission fails the operation *before* anything applies. Add it to your CI service
      account's role.
- [ ] **Server-side apply is the default for new installs.** Expect explicit field-manager conflict errors
      where Helm 3 silently overwrote — that is the behaviour you want, especially if Argo CD or another
      controller touches the same objects. Note: SSA applies to new installs; releases migrated from Helm 3
      keep client-side apply until reinstalled.
- [ ] **`--post-renderer <executable>` is gone.** Package post-renderers as plugins with a `plugin.yaml`
      (`runtime: subprocess` for identical Helm 3 behaviour).
- [ ] **Chart `apiVersion: v2` works unchanged.** Chart format v3 is experimental and disabled by default —
      do not use it in a portfolio project.
- [ ] **Optional but cheap credibility:** install charts by **OCI digest**
      (`oci://…/chart@sha256:…`) so a mutable tag cannot change what deploys.
- [ ] Re-run `helm template` in CI and diff against the previous revision's output before merging the
      migration PR.

---

## 5. The cgroup-truth DaemonSet

```yaml
# charts/kubethrifty/templates/daemonset-cgroup-truth.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: kubethrifty-cgroup-truth
  labels: {app.kubernetes.io/name: kubethrifty, component: cgroup-truth}
spec:
  selector:
    matchLabels: {app.kubernetes.io/name: kubethrifty, component: cgroup-truth}
  template:
    metadata:
      labels: {app.kubernetes.io/name: kubethrifty, component: cgroup-truth}
    spec:
      automountServiceAccountToken: false      # it needs no API access at all
      nodeSelector: {kubernetes.io/os: linux}
      tolerations: [{operator: Exists}]        # observe every node, including tainted ones
      containers:
        - name: collector
          image: ghcr.io/OWNER/kubethrifty-cgroup-truth:1.0.0     # never :latest
          args: ["--listen=:9847", "--interval=30s"]
          ports: [{name: metrics, containerPort: 9847}]
          env: [{name: CGROUP_ROOT, value: /host/sys/fs/cgroup}]
          securityContext:
            runAsNonRoot: true
            runAsUser: 65534
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: {drop: ["ALL"]}
            seccompProfile: {type: RuntimeDefault}
          resources:
            requests: {cpu: 10m, memory: 32Mi}
            limits: {cpu: 100m, memory: 64Mi}
          volumeMounts:
            - {name: cgroup, mountPath: /host/sys/fs/cgroup, readOnly: true}
      volumes:
        - name: cgroup
          hostPath: {path: /sys/fs/cgroup, type: Directory}
```

Pair it with a `PodMonitor` on port `metrics`. The security review answer: read-only mount, non-root, no
capabilities, no service-account token, no host network, 10m/32Mi.

---

## 6. Pinned-versions checklist for the repo

Run this mentally before every demo, and literally in CI with a grep:

- [ ] `docker-compose.yml` — no `:latest` (this is where Rev 2 slipped: Prometheus and Grafana were
      `latest` in a document whose headline rule is "never latest")
- [ ] chart `values.yaml` image tags — explicit
- [ ] `Chart.yaml` `dependencies` — explicit versions
- [ ] GitHub Actions — pinned by major (or SHA for third-party actions)
- [ ] `kindest/node` — explicit patch
- [ ] `requirements.txt` / `package.json` — exact versions plus a lockfile
- [ ] `config/instances.json` — carries an `as_of` date so ₹ figures are reproducible

```bash
# CI guard — one line, catches the embarrassing case
! grep -rnE ':latest|image: *[a-z0-9./-]+ *$' --include='*.yml' --include='*.yaml' . \
  || { echo "unpinned image found"; exit 1; }
```

---

## 7. The two scaling decisions to document

Interviewers ask for *a* scaling decision. Have two, both with the trade-off stated:

1. **The analyser scales to zero, the dashboard does not.** The analyser is a batch worker driven by a
   6-hourly enqueue, so KEDA runs it 0→N→0 on Redis-Stream lag; scale-to-zero is itself a cost win and it
   makes the tool practise what it preaches. The dashboard holds 2 replicas behind an HPA because a cold
   start on a portfolio demo link is worse than the cost of one idle pod.
2. **Rehearsals are serialised per workload and capped per cluster (default 3), and only the top N
   candidates per run get one.** A rehearsal costs ~35 minutes of wall clock and touches production, so
   candidates are ranked by `projected_node_savings × confidence`; everything else ships as `modelled`
   evidence. That is a deliberate throughput-versus-assurance trade: three changes with proof beat thirty
   with predictions, because the three get merged.
