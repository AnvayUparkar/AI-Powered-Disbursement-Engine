# Deploying DGCL Engine to a Kubernetes cluster via the jump server

Plain (non-AWS) Kubernetes cluster, airgapped, reached only via a jump
server. This chart deploys the current codebase as-is: FastAPI app
(`:8000`), IDP service (`:8001`), one Celery worker pool, the React
dashboard, and a self-hosted Redis (Bitnami subchart) as the Celery
broker/result-backend. No cloud APIs are assumed anywhere — no IRSA, no
EBS/gp3 StorageClass, no ALB. Object storage (S3) is deliberately not
configured for this pass; see §3's note. It does **not** implement the
4-way ingestion/reasoning split proposed in `SCALING_PLAN.md` — that needs
new code (separate queues/entrypoints) and should be a separate change once
Phase 1-3 of that plan land.

```
                         ┌──────────────────────────────┐
   Jump server  ── SSH ──▶ tunnel to the cluster's       │
   (bastion)             │  API server; kubectl/helm     │
                         │  run here                     │
                         └──────────────┬────────────────┘
                                        │
                              ┌─────────▼─────────┐   nginx Ingress
                              │  Kubernetes cluster│◀──Controller
                              │                    │   (TLS terminated)
                              │  ┌───────────────┐ │
                              │  │ frontend (2x) │ │
                              │  └──────┬────────┘ │
                              │         │ /api     │
                              │  ┌──────▼────────┐ │
                              │  │  api (2x, HPA)│ │──┐
                              │  └──────┬────────┘ │  │
                              │   ┌─────┴─────┐    │  │
                              │   ▼           ▼    │  │
                              │ ┌─────┐   ┌────────┐│  │  shared RWX PVC
                              │ │idp  │   │worker  ││──┤  (poc_data — §3a)
                              │ │(HPA)│   │(KEDA/  ││  │  mounted by all three
                              │ │     │   │ HPA)   ││──┘
                              │ └──┬──┘   └───┬────┘│
                              │    └────┬─────┘     │
                              │      ┌──▼──┐        │
                              │      │Redis│        │
                              │      │(no  │        │
                              │      │ PVC)│        │
                              │      └─────┘        │
                              └──────────────────────┘
                                              │
                                              ▼
                                       LiteLLM gateway
                                     (egress, if permitted —
                                      confirm the firewall rule)
```

## 0. Prerequisites on the cluster (one-time, ask the platform team)

- An nginx Ingress Controller installed (`ingress.className: nginx` is this
  chart's default — confirm it matches what's actually running).
- (Optional but recommended) [KEDA](https://keda.sh) installed, if you want
  the worker to scale on Redis queue depth instead of CPU — see §5.
- An internal container registry reachable from both the cluster and a
  build machine with internet access (see §2).
- Outbound firewall rule for the LLM provider's API host(s), if the cluster
  has any egress at all — confirm with the platform team whether this
  airgapped cluster permits any egress, or whether calls to the LiteLLM
  gateway (`tsguat1.hdbfs.com:560`) need to go through an internal proxy instead.
- **A StorageClass that supports ReadWriteMany (RWX)** — required for the
  shared document volume in §3a. This is the one prerequisite most likely to
  need a real conversation with the platform team rather than a quick
  confirmation: most default StorageClasses (block storage) are
  ReadWriteOnce only. Ask specifically "do you have an NFS/CephFS-backed
  StorageClass" — a "yes we have storage" answer isn't enough on its own.

## 1. Jump server → cluster API access (private endpoint via SSH tunnel)

If the cluster's API server is private-only, open a local port-forward
through the bastion before running any `kubectl`/`helm` command. Two common
shapes — use whichever matches how the jump server is wired, and how the
platform team distributes cluster credentials (a kubeconfig file is the
usual artifact on a non-managed cluster — there's no `aws eks
update-kubeconfig` equivalent without AWS):

**A. Jump server IS inside the cluster's network and can reach the API server directly**
```bash
ssh -i ~/.ssh/jump-server.pem <user>@<jump-server-ip>
# on the jump server — kubeconfig provided by the platform team:
export KUBECONFIG=/path/to/provided-kubeconfig.yaml
kubectl get nodes   # should resolve the API server directly
```

**B. You tunnel from your laptop, through the jump server, to the API server**
```bash
# Forward local :6443 to the cluster's API server via the bastion.
ssh -i ~/.ssh/jump-server.pem -N -L 6443:<cluster-api-server-host>:6443 <user>@<jump-server-ip>

# In another terminal: point kubeconfig at localhost, keeping the real
# server's TLS SNI/cert name so verification still works.
export KUBECONFIG=/path/to/provided-kubeconfig.yaml
kubectl config set-cluster <cluster-name-in-kubeconfig> --server=https://127.0.0.1:6443
```
Confirm with `kubectl get nodes` before touching Helm. If TLS verification
fails on option B, the issue is almost always a stale `--server` override —
re-fetch the original kubeconfig from the platform team and re-apply the
`set-cluster` line rather than disabling verification.

Once `kubectl get nodes` works, `helm` uses the same kubeconfig automatically.

## 2. Build and push images

This deployment is airgapped, so images are built where there's internet
access, transferred as tars, and loaded into the internal registry the
platform team runs. (If a future environment for this chart does have a
connected registry — Harbor, Nexus, a plain Docker registry — the same
`docker build`/`docker push` pair from the build step below works directly
against it; skip the save/transfer/load steps in that case.)

**On a machine WITH internet access** (a build box, laptop, or CI runner — not the jump server), run the
bundle builder from the repo root. It builds every image for **linux/amd64**, packages the chart, and writes
`../airgap-bundle/` with checksums, a manifest and the load/verify scripts:

```bash
deploy/airgap/build-bundle.sh            # podman or docker, auto-detected; ~1h under emulation on Apple Silicon
cd .. && zip -r -X dgcl-engine-airgap-bundle.zip airgap-bundle && shasum -a 256 dgcl-engine-airgap-bundle.zip
```

The list of images is taken from the chart itself (`helm template`), so the bundle cannot miss one the chart
pulls. Transfer the zip across the airgap through your org's approved process, then on the jump server /
a host that can reach the internal registry, follow `airgap-bundle/INSTALL.md`:

```bash
unzip dgcl-engine-airgap-bundle.zip && cd airgap-bundle
scripts/verify-bundle.sh                                   # checksums, all images present, all amd64
REGISTRY=<internal-registry> scripts/load-and-push-images.sh
```

Things that have bitten this deployment before, all handled by the script:
- **Architecture.** Images must be `linux/amd64`. `docker build` on an Apple-Silicon machine silently builds
  arm64 unless `--platform linux/amd64` is given (the script always does).
- **Redis.** Upstream Bitnami no longer publishes versioned tags — `bitnami/redis:8.10.2` does **not** exist;
  only `:latest` does (which currently *is* 8.10.2). The script pulls `:latest`, requires its version label to
  equal the tag pinned in `values.yaml`, and retags it, so the chart's tag and the pushed image always agree.
  Its digest is recorded in `MANIFEST.txt`.
- **Registry paths.** Paths are preserved (`<registry>/dgcl-engine-backend`, `<registry>/bitnami/redis`) so
  `global.imageRegistry` alone resolves every image.

<details><summary>Manual equivalent (only if you cannot use the script)</summary>

```bash
export TAG=v1.0.0 PLATFORM=linux/amd64
docker build --platform $PLATFORM -f docker/Dockerfile.backend  -t localhost/dgcl-engine-backend:$TAG  .
docker build --platform $PLATFORM -f docker/Dockerfile.frontend -t localhost/dgcl-engine-frontend:$TAG .
docker pull  --platform $PLATFORM bitnami/redis:latest        # must report version 8.10.2:
docker image inspect bitnami/redis:latest --format '{{index .Config.Labels "org.opencontainers.image.version"}}'
docker tag bitnami/redis:latest localhost/bitnami/redis:8.10.2
for i in dgcl-engine-backend:$TAG dgcl-engine-frontend:$TAG bitnami/redis:8.10.2; do
  docker save -o "$(echo $i | tr '/:' '-_')_amd64.tar" localhost/$i; done
# on the registry side: docker load -i each tar, then docker tag localhost/<name> $REGISTRY/<name> && docker push
```
</details>

### The chart itself also needs to cross the airgap

`helm dependency update` (already run once — the fetched `.tgz` lives in
`charts/redis-28.2.1.tgz`) reached out to `charts.bitnami.com`, which the
airgapped side cannot do. Do not re-run `helm dependency update` on the
airgapped side — it has nothing to reach.

**On the machine WITH internet access**, package the chart into a single
tarball (bundles `charts/redis-28.2.1.tgz` automatically — confirmed by
inspecting the packaged output):
```bash
cd deploy/helm
helm package dgcl-engine   # -> dgcl-engine-<chart-version>.tgz, e.g. dgcl-engine-0.1.0.tgz
```
Transfer that one `.tgz` across the airgap alongside the image tars from
above. **On the jump server**, install straight from it — no need to unpack:
```bash
helm upgrade --install dgcl-engine ./dgcl-engine-0.1.0.tgz \
  -f values-poc.yaml \
  --namespace dgcl --create-namespace \
  ...   # same --set flags as §5
```
(§5 below shows `helm upgrade --install dgcl-engine .` assuming you're
running from an unpacked chart directory — swap `.` for the `.tgz` path if
you transferred it packaged instead. Either form works identically.)

If the internal registry requires authentication to pull from (rather than
being reachable anonymously inside the cluster network), create a
`kubernetes.io/dockerconfigjson` Secret out of band and reference it via
`imagePullSecrets` in your values override — see the comment on that field
in `values.yaml`.

## 3. Object storage — deliberately not configured

This deployment does not talk to S3 or any S3-compatible store. The app's
own storage layer already handles that gracefully:
`idp/services/storage/s3.py`'s boto3 client falls back to a local filesystem
mock (under `TEMP_DIR/s3_mock/`) whenever it can't initialize real S3
access — logging a warning, not failing. `pipeline/storage.py` (the
LangGraph pipeline's own artifact tiers — `poc_data/s3_raw`, `s3_extracted`,
etc.) is local filesystem already, unconditionally; "S3" there is a naming
convention from the original POC, not a real AWS dependency. No IAM role,
IRSA, or credentials are needed for either path as configured. Revisit this
section (and re-add `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` to the
Secret in §4, plus the `AWS_REGION`/`S3_BUCKET`/prefix keys in `values.yaml`'s
`config` block) if real object storage is introduced later.

## 3a. Shared document storage (api/worker/idp)

`api`, `worker`, and `idp` are three separate pods with no shared filesystem
by default. That's fine for `idp`'s own direct-upload endpoint (write and
process happen in one request, one pod) and for the `poc_data` fixtures
already baked into every image — but the normal case pipeline (`app` →
`worker` → `idp` over HTTP, passing a file *path*, not the file's bytes)
fails once a document is written at runtime by `api`/`worker`, because
`idp` — a different pod — has no way to see it.

The fix: a single ReadWriteMany PVC (`sharedStorage` in `values.yaml`),
mounted at `/srv/app/poc_data` on all three Deployments — same disk, same
files, regardless of which pod wrote them. **Requires an RWX-capable
StorageClass** (see §0) — confirm this with the platform team before
enabling it; a ReadWriteOnce StorageClass will fail to bind once a second
pod tries to mount it.

Each user's data lives under `poc_data/tenants/<tenant_id>/` on this volume
(one isolated folder per signup — see §4a). **There is nothing to seed:** the
image ships no `poc_data` (`.dockerignore` excludes it) and every account
starts empty. The `prepare-shared-storage` initContainer on each of the three
Deployments only creates `/shared/tenants` and proves the volume is writable
by the app's uid (10001), so a permissions problem shows up as a clear
`Init:Error` with a message instead of a 500 on the first upload. If it fails
on NFS, fix the export's ownership; `podSecurityContext.fsGroup` covers CSI
block/CephFS drivers but plain NFS ignores it.

Set the StorageClass name before installing:
```bash
--set sharedStorage.storageClassName=<platform-team-provided-rwx-class>
```
Turn it off entirely (back to the earlier ephemeral-per-pod behavior, and
the file-visibility gap above) with `--set sharedStorage.enabled=false` if
you're only testing via `idp`'s direct-upload endpoint and don't need it yet.

## 4. Bootstrap secrets (kept out of Helm values / release history on purpose)

```bash
kubectl create namespace dgcl

# Redis auth (referenced by redis.auth.existingSecret in values.yaml)
kubectl -n dgcl create secret generic dgcl-engine-redis-auth \
  --from-literal=redis-password="$(openssl rand -hex 24)"

# Application secrets (referenced by secrets.existingSecret in values.yaml)
kubectl -n dgcl create secret generic dgcl-engine-secrets \
  --from-literal=LLM_API_KEY="<litellm-virtual-key>" \
  --from-literal=VLM_API_KEY="<litellm-virtual-key>" \
  --from-literal=INTERNAL_API_TOKEN="$(openssl rand -hex 32)"
  # no AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY — see §3, object storage is not used here
```

Use `-hex`, not `-base64`, for the Redis password: the pods splice it into
`redis://:<password>@host:6379/0`, and base64 output can contain `/`, `+` and
`=`, which corrupt that URL (roughly four passwords in ten contain a `/`).

`INTERNAL_API_TOKEN` is the shared secret api, idp and worker use to call each
other (worker/pipeline → idp). It must be the same in all three, which is
automatic because they read the same Secret.

## 4a. Accounts, isolation and login

The app has built-in signup/login: each signup creates its own isolated
workspace (`poc_data/tenants/<tenant_id>/`) and nobody can see another
account's data. Users are stored in a SQLite file.

- **The api runs as exactly one replica** (`api.replicaCount: 1`; the chart
  refuses to render with more). SQLite supports a single writer, and the
  in-memory caches are per process. Scaling the API out first needs the auth
  store moved to a shared database — a code change.
- **The auth database has its own small ReadWriteOnce volume** (`auth.persistence`
  in `values.yaml`), mounted only on the api pod, *not* on the RWX shared
  volume: SQLite needs real file locking, which NFS/CephFS don't reliably give.
  The api Deployment therefore uses the `Recreate` strategy (a short outage per
  upgrade, because an RWO volume can't attach to two pods at once). The PVC is
  annotated `helm.sh/resource-policy: keep` — losing it loses every account.
  Back it up.
- **`SESSION_COOKIE_SECURE` is `"true"` by default** and needs HTTPS at the
  ingress. On a plain-http POC set it to `"false"` (`values-poc.yaml` does), or
  the browser will drop the login cookie and login will appear to do nothing.
- **Signup is open**: anyone who can reach the ingress can create an account
  and consume LLM credits and disk. Keep the ingress on an internal network or
  behind the bank's SSO/VPN, and add an invite code before wider exposure.
- There is **no password recovery** and no email; an operator resets a
  password by editing the SQLite file.

## 5. Install / upgrade

```bash
cd deploy/helm/dgcl-engine
# helm dependency update .   # do NOT run this on the airgapped side — see §2b;
                              # only re-run it on a connected machine if you
                              # change the vendored chart version

export INTERNAL_REGISTRY=<internal-registry>
export TAG=v1.0.0

helm upgrade --install dgcl-engine . \
  --namespace dgcl --create-namespace \
  --set global.imageRegistry=$INTERNAL_REGISTRY \
  --set image.backend.tag=$TAG \
  --set image.frontend.tag=$TAG \
  --set ingress.host=dgcl.internal.example.com \
  --set ingress.tls.secretName=dgcl-engine-tls \
  --set sharedStorage.storageClassName=<platform-team-provided-rwx-class> \
  --wait --timeout 10m
```

Keep environment-specific overrides (`ingress.host`, resource sizes,
replica counts) in a checked-in `values-prod.yaml` rather than typing
`--set` every time; pass it with `-f values-prod.yaml`.

If you installed KEDA (§0) and want the worker to scale on Redis queue depth
instead of CPU — the approach `SCALING_PLAN.md` §8 recommends, because a
worker blocked on an LLM call looks idle on CPU exactly when the queue is
growing — add:
```bash
  --set worker.autoscaling.enabled=false \
  --set worker.keda.enabled=true
```

## 5a. GPU acceleration for `idp` (optional)

Only the `idp` deployment benefits from a GPU — Docling's layout model and
TableFormer are torch and run on CUDA directly; RapidOCR only engages CUDA
through the `onnxruntime-gpu` package that `docker/Dockerfile.backend-gpu`
installs in place of plain `onnxruntime`. See
`idp/services/docling/accelerator.py` for how the device is resolved.
`api`/`worker`/`frontend` have nothing to gain here and stay on the plain
CPU image regardless of this section.

**Prerequisites** (ask the platform team if these aren't already there):
1. A GPU-equipped node group in the cluster.
2. The [NVIDIA device plugin](https://github.com/NVIDIA/k8s-device-plugin)
   DaemonSet installed, so `nvidia.com/gpu` is a schedulable resource:
   ```bash
   kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/main/nvidia-device-plugin.yml
   ```
3. `idp.gpu.nodeSelector` in `values.yaml` matched to whatever label your
   GPU node group actually carries (`nvidia.com/gpu.present: "true"` is the
   default guess, not guaranteed to match your node group's labels).

**Enable it** (the GPU image also needs to cross the airgap the same way as
the others in §2 — `docker save`/transfer/`docker load`/tag/push the
`dgcl-engine-backend-gpu` image before running this):
```bash
helm upgrade --install dgcl-engine . \
  --namespace dgcl \
  --set global.imageRegistry=$INTERNAL_REGISTRY \
  --set image.backendGpu.tag=$TAG \
  --set idp.gpu.enabled=true \
  --reuse-values --wait --timeout 15m
```

This swaps the `idp` image, adds `nvidia.com/gpu: 1` to its requests/limits,
applies the GPU node selector/toleration, and sets `IDP_ACCELERATOR_DEVICE=cuda`
— all only on the `idp` Deployment (see `templates/idp-deployment.yaml`).

One GPU per pod, one process per GPU: `idp.args` keeps `--workers 1` so a
single uvicorn process owns the device — scale by adding `idp` replicas (and
GPU nodes 1:1), not by adding workers inside a pod. `SCALING_PLAN.md` §1.6's
CUDA numbers are **extrapolated, not measured** — benchmark one real GPU
pod against your own documents before resizing `idp.replicaCount` /
`idp.autoscaling.maxReplicas` around it.

## 6. Verify

```bash
kubectl -n dgcl get pods -w
kubectl -n dgcl logs deploy/dgcl-engine-api -f
kubectl -n dgcl get ingress dgcl-engine
kubectl -n dgcl exec deploy/dgcl-engine-worker -- celery -A pipeline.celery_app inspect active
```

## 7. Rollback

```bash
helm -n dgcl history dgcl-engine
helm -n dgcl rollback dgcl-engine <revision>
```

## Notes / deliberate omissions

- **No Postgres.** The app currently persists case state to local JSON
  (`poc_data/`), not a database — this chart doesn't add one. If you adopt
  `SCALING_PLAN.md` §3.1's `case_state` table, add a database and a
  `DATABASE_URL` secret key at that point.
- **CPU-based HPA is used for `api` and `idp`** (legitimately CPU-bound) but
  **left off by default for `worker`** (LLM-call-bound — see §8 of
  `SCALING_PLAN.md`) in favor of the optional KEDA Redis-length trigger.
- **NetworkPolicy** templates assume a CNI that enforces them (Calico and
  Cilium do; confirm this cluster's CNI actually does before relying on
  them) — otherwise they're silently no-ops.
- **Document upload visibility across `api`/`worker`/`idp` is fixed by
  `sharedStorage` (§3a)** — a ReadWriteMany PVC mounted at
  `/srv/app/poc_data` on all three, so a file written by any of them is
  immediately visible to the others regardless of which pod handles which
  request. This requires an RWX-capable StorageClass, which is not
  guaranteed to exist on every cluster (see §0) — if `sharedStorage.enabled`
  is turned off, the gap comes back: uploads only work through `idp`'s own
  direct-upload endpoint (write and process in one request, one pod), and
  the normal case pipeline (`app` → `worker` → `idp` over HTTP, passing a
  file path rather than the file's bytes) will fail on any document written
  at runtime by `api`/`worker`.
- **Model weights are baked into the image, not fetched at runtime.**
  `docker/Dockerfile.backend` runs `scripts/download_models.py` at *build*
  time (needs internet on the build machine only) and sets
  `DOCLING_ARTIFACTS_PATH`, `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE`, so
  pods make no outbound calls. Before go-live, prove it: start the image on a
  network-isolated node/namespace (no egress), upload a scanned PDF and confirm
  OCR text comes back with no `HF`/download errors in the idp log. The `idp`
  pod's `--workers 1` and ~2Gi memory limit should be re-checked with the real
  weights loaded.
- **The frontend is built once and is environment-independent.** It calls the
  same-origin `/api` path; do **not** set `VITE_API_BASE_URL` for the
  container build unless the API is on a different origin (then include the
  `/api` prefix, e.g. `https://api.example.com/api`).
