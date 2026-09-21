#!/usr/bin/env bash
# Load the image tarballs and push them to the cluster's internal registry, keeping the repository
# paths the chart expects:  <REGISTRY>/dgcl-engine-backend:<tag>, <REGISTRY>/dgcl-engine-frontend:<tag>,
# <REGISTRY>/bitnami/redis:<tag>.   Then install with --set global.imageRegistry=<REGISTRY>.
#
#   REGISTRY=registry.internal.example.com  scripts/load-and-push-images.sh
#   REGISTRY=localhost:5001 TLS_VERIFY=false scripts/load-and-push-images.sh     # plain-http registry
set -euo pipefail
: "${REGISTRY:?set REGISTRY, e.g. registry.internal.example.com}"
CLI="${CONTAINER_CLI:-$(command -v podman || command -v docker || true)}"
[ -n "$CLI" ] || { echo "ERROR: need podman or docker" >&2; exit 1; }
cd "$(dirname "$0")/.."
PUSH_FLAGS=""; [ "$(basename "$CLI")" = "podman" ] && [ "${TLS_VERIFY:-true}" = "false" ] && PUSH_FLAGS="--tls-verify=false"

for tar in images/*.tar; do
  echo "== loading $tar"
  # `load` prints e.g. "Loaded image: localhost/dgcl-engine-backend:v1.0.0"
  loaded="$("$CLI" load -i "$tar" | sed -n 's/^Loaded image[s]*: //p' | tail -1)"
  [ -n "$loaded" ] || { echo "ERROR: could not determine image name loaded from $tar" >&2; exit 1; }
  path="${loaded#localhost/}"; path="${path#docker.io/}"
  arch="$("$CLI" image inspect "$loaded" --format '{{.Architecture}}')"
  [ "$arch" = "amd64" ] || { echo "ERROR: $loaded is $arch, expected amd64" >&2; exit 1; }
  echo "   -> $REGISTRY/$path"
  "$CLI" tag "$loaded" "$REGISTRY/$path"
  # shellcheck disable=SC2086
  "$CLI" push $PUSH_FLAGS "$REGISTRY/$path"
done
echo "All images pushed to $REGISTRY. Install with:  --set global.imageRegistry=$REGISTRY"
