# DGCL Engine — airgap install

Everything here is **linux/amd64**. Nothing needs internet: the model weights are inside the backend image.

```
images/   dgcl-engine-backend, dgcl-engine-frontend, bitnami-redis   (docker-archive tarballs)
charts/   dgcl-engine-<version>.tgz                                   (Redis subchart included)
scripts/  verify-bundle.sh, load-and-push-images.sh
SHA256SUMS  MANIFEST.txt  values-airgap.yaml   (the chart's full values file - edit this one)
```

## 1. Verify the bundle (do this first)
```bash
scripts/verify-bundle.sh          # checksums, every image the chart pulls is present, all amd64
```

## 2. Load the images and push them to the internal registry
```bash
REGISTRY=<internal-registry-host[:port]> scripts/load-and-push-images.sh
# plain-http registry with podman:  TLS_VERIFY=false REGISTRY=host:5000 scripts/load-and-push-images.sh
```
Paths are preserved, so the chart's `global.imageRegistry` resolves to
`<REGISTRY>/dgcl-engine-backend`, `<REGISTRY>/dgcl-engine-frontend` and `<REGISTRY>/bitnami/redis`.

## 3. Create the secrets (once; kept out of Helm values on purpose)
```bash
kubectl create namespace dgcl
kubectl -n dgcl create secret generic dgcl-engine-redis-auth \
  --from-literal=redis-password="$(openssl rand -hex 24)"      # hex, NOT base64: it is spliced into a URL
kubectl -n dgcl create secret generic dgcl-engine-secrets \
  --from-literal=LLM_API_KEY="<litellm-virtual-key>" \
  --from-literal=VLM_API_KEY="<litellm-virtual-key>" \
  --from-literal=INTERNAL_API_TOKEN="$(openssl rand -hex 32)"
# the TLS secret named in ingress.tls.secretName must also exist
```

## 4. Install
```bash
# Edit values-airgap.yaml in place. Read the banner at the top, then search for ">>> FILL IN".
helm upgrade --install dgcl charts/dgcl-engine-*.tgz -n dgcl -f values-airgap.yaml
```
`values-airgap.yaml` is the complete values file with every option explained in comments. Leave the settings the
banner lists under "LEAVE ALONE" as they are.

## 5. Check
```bash
kubectl -n dgcl get pods                    # api, idp, worker, frontend, redis-master all Running
kubectl -n dgcl logs deploy/dgcl-api        # no errors
# open https://<host> -> "Create account" -> sign up -> the empty case list appears
```

## Things to know
- **The API runs as exactly one replica.** Accounts live in SQLite on a small ReadWriteOnce volume; the chart
  refuses to render with more. The volume survives `helm uninstall` (back it up: it holds every account).
- **Storage:** the shared volume must be ReadWriteMany and writable by uid/gid 10001 (or honour `fsGroup`).
  A `prepare-shared-storage` init container fails with a clear message if it is not.
- **Redis:** upstream Bitnami now publishes only `:latest`; the bundle's image is that build retagged `8.10.2`
  (see MANIFEST.txt for the digest). The Redis subchart's NetworkPolicy is disabled; the chart ships its own.
- **No GPU image** is in this bundle (`idp.gpu.enabled` defaults to false). Build it separately with
  `INCLUDE_GPU=true deploy/airgap/build-bundle.sh` on a machine with ~25GB free if needed.
- **Prove offline behaviour before go-live:** run an upload of a scanned PDF on a node with no egress and
  confirm OCR text comes back and the idp log shows no download attempts.
