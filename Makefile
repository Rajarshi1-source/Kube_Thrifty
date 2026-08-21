# KubeThrifty. Every version here is pinned; `policy.yml` fails CI on an unpinned image anywhere.
#
# On Windows run these from WSL2 (or use the individual commands directly) -- the cluster-facing
# targets need Linux nodes with cgroup v2 and PSI. See docs/adr/0001-psi-evidence-tier-on-wsl2.md.

SHELL := /bin/bash
.DEFAULT_GOAL := help

CLUSTER              := kubethrifty
KIND_NODE_IMAGE      := kindest/node:v1.36.1
# 88.5.3 is the current ~88.x patch. Note 88.2.1 does NOT exist -- the series skips it, and the OCI
# registry reports a missing tag as `FetchReference ... not found`, which reads like a registry
# outage rather than a bad version. Verify with `helm search repo --versions` before bumping.
KPS_CHART_VERSION    := 88.5.3
KPS_REPO             := https://prometheus-community.github.io/helm-charts
KEDA_CHART_VERSION   := 2.20.0
PY                   := python3

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------------- phase 0
.PHONY: preflight
preflight: ## Verify cgroup v2 + PSI on the host (the evidence-tier decision)
	@echo "cgroup fs: $$(stat -fc %T /sys/fs/cgroup)   (must be cgroup2fs)"
	@test "$$(stat -fc %T /sys/fs/cgroup)" = "cgroup2fs" || { echo "FAIL: cgroup v2 required"; exit 1; }
	@echo "PSI:"; cat /proc/pressure/cpu || { echo "FAIL: kernel PSI unavailable -- add psi=1"; exit 1; }
	@test -e /sys/fs/cgroup/memory.pressure && echo "cgroup-level PSI: present" || echo "cgroup-level PSI: ABSENT (partial evidence tier)"

# ---------------------------------------------------------------------------------- phase 1
.PHONY: install
install: ## Install pinned Python deps from the lockfile
	cd analyser && $(PY) -m pip install -r requirements.lock

.PHONY: lint
lint: ## ruff
	cd analyser && ruff check .

.PHONY: evals
evals: ## Run all four CI gates (any failure is a build break)
	cd analyser && $(PY) -m evals.recommendation_eval
	cd analyser && $(PY) -m evals.sizing_eval
	cd analyser && $(PY) -m evals.forecast_eval
	cd analyser && $(PY) -m evals.detective_eval

.PHONY: unit
unit: ## Unit tests (sizing, signals, pricing, cache/lock protocol)
	cd analyser && $(PY) -m pytest tests -q

.PHONY: test
test: lint unit evals ## Lint + unit tests + all gates

# ---------------------------------------------------------------------------------- phase 2
.PHONY: cluster-up
cluster-up: ## Create the kind 1.36 cluster (3 nodes)
	kind create cluster --config k8s/kind/cluster.yaml --image $(KIND_NODE_IMAGE) --wait 120s
	kubectl cluster-info --context kind-$(CLUSTER)

.PHONY: cluster-down
cluster-down: ## Delete the kind cluster
	kind delete cluster --name $(CLUSTER)

.PHONY: monitoring
monitoring: ## Install the pinned kube-prometheus-stack with the cAdvisor scrape
	helm repo add prometheus-community $(KPS_REPO) 2>/dev/null || true
	helm repo update prometheus-community
	helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
		--version $(KPS_CHART_VERSION) \
		--namespace monitoring --create-namespace \
		-f k8s/prometheus/kube-prometheus-stack-values.yaml \
		--wait --timeout 15m

.PHONY: loadgen-image
loadgen-image: ## Build the workload generator and load it into kind
	docker build -t kubethrifty-loadgen:dev sample-app/loadgen
	kind load docker-image kubethrifty-loadgen:dev --name $(CLUSTER)

.PHONY: sample-app
sample-app: loadgen-image ## Deploy the five over-provisioned demo services
	helm upgrade --install sample-app charts/sample-app --wait --timeout 5m

.PHONY: verify-metrics
verify-metrics: ## Phase 2 exit criterion: every required metric family populated
	kubectl -n monitoring port-forward svc/monitoring-prometheus 9090:9090 & \
		sleep 5; $(PY) scripts/verify_metrics.py; kill %1

.PHONY: demo-up
demo-up: cluster-up monitoring sample-app ## Full demo cluster from nothing

# ---------------------------------------------------------------------------------- local stack
.PHONY: stack-up
stack-up: ## TimescaleDB + Valkey + Prometheus + Grafana (docker compose)
	docker compose up -d timescaledb valkey prometheus grafana

.PHONY: stack-down
stack-down:
	docker compose down

# ---------------------------------------------------------------------------------- phase 4
# These two are integration checks against the REAL pinned images. The unit tests prove our protocol
# is self-consistent; only these prove the servers agree with our reading of it -- that the Toolkit
# functions exist, that the CHECK constraints bite, and that Valkey enforces SET NX as assumed.
.PHONY: verify-schema
verify-schema: ## Assert migrations, Toolkit round trip, and the safety CHECK constraints
	docker compose exec -T timescaledb psql -U kubethrifty -d kubethrifty -v ON_ERROR_STOP=1 \
		< scripts/verify_schema.sql

.PHONY: verify-valkey
verify-valkey: ## Assert the run lock, cache-aside, consumer group, reclaim and DLQ
	$(PY) scripts/verify_valkey.py

.PHONY: verify-data
verify-data: verify-schema verify-valkey ## Phase 4 exit criterion

# ---------------------------------------------------------------------------------- policy
.PHONY: policy
policy: ## Run the CI policy guards locally (pinning, blast radius, null-vs-zero)
	$(PY) scripts/check_policy.py

# ---------------------------------------------------------------------------------- phase 6-8
.PHONY: collector-image
collector-image: ## Build the cgroup-truth collector and load it into kind
	docker build -f collector/Dockerfile -t kubethrifty-cgroup-collector:dev .
	kind load docker-image kubethrifty-cgroup-collector:dev --name $(CLUSTER)

.PHONY: platform
platform: collector-image ## Install the KubeThrifty chart (collector + recording rules)
	helm upgrade --install kubethrifty charts/kubethrifty \
		--namespace kubethrifty --create-namespace \
		--set cgroupCollector.image.repository=kubethrifty-cgroup-collector \
		--set cgroupCollector.image.tag=dev \
		--set cgroupCollector.image.pullPolicy=Never \
		--wait --timeout 5m

.PHONY: verify-evidence
verify-evidence: ## Phase 6 exit criterion: memory sized from the kernel high-water mark
	@echo "Recorded cgroup series (0 means the collector is blind or the join is broken):"
	@$(PY) - <<-'PY'
	import json, urllib.request, urllib.parse
	q = 'count(kubethrifty:cgroup_memory_peak_bytes:container)'
	url = 'http://localhost:9091/api/v1/query?' + urllib.parse.urlencode({'query': q})
	r = json.load(urllib.request.urlopen(url, timeout=10))
	result = r['data']['result']
	print('  series:', result[0]['value'][1] if result else 0)
	PY

# ---------------------------------------------------------------------------------- self-referential
.PHONY: demo-self
demo-self: ## KubeThrifty right-sizes its OWN pods -- the self-referential demo
	cd analyser && $(PY) -m src.main --window 1h --namespace kubethrifty

.PHONY: demo-rehearsal
demo-rehearsal: ## Narrated Resize Rehearsal walkthrough (simulated, no cluster needed)
	$(PY) scripts/demo_rehearsal.py

.PHONY: demo-record
demo-record: ## The same walkthrough, paced for screen recording
	$(PY) scripts/demo_rehearsal.py --slow

# ---------------------------------------------------------------------------------- detective
.PHONY: reliability
reliability: ## Reliability diagram behind the ECE number (shows over- vs under-confidence)
	cd analyser && $(PY) -m evals.reliability

.PHONY: freeze-corpus
freeze-corpus: ## Re-anchor the determinism digest. READ Brier/ECE FIRST and say why in the commit.
	cd analyser && $(PY) -m evals.reliability
	@echo ""
	@echo "Above is the calibration you are about to freeze. Ctrl-C now unless you have read it."
	@sleep 5
	cd analyser && $(PY) -m evals.corpus.generate --freeze

.PHONY: analyse
analyse: ## Run one analysis pass against the local stack
	cd analyser && $(PY) -m src.main --window 7d
