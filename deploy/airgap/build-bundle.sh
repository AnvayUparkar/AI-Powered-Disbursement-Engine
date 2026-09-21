#!/usr/bin/env bash
# Build the airgap bundle: linux/amd64 images, the packaged Helm chart, checksums and a manifest.
# Run on a machine WITH internet. Output: $OUT (default ../airgap-bundle).
#
#   deploy/airgap/build-bundle.sh
#   CONTAINER_CLI=docker OUT=/tmp/bundle deploy/airgap/build-bundle.sh
#   INCLUDE_GPU=true deploy/airgap/build-bundle.sh      # also the ~10GB CUDA idp image (idp.gpu.enabled=true)
#   CHART_ONLY=true  deploy/airgap/build-bundle.sh      # keep the existing images/, refresh chart+scripts+docs+sums
#                                                       # (only valid when no Dockerfile/app change; version bump the chart)
#
# The list of images to ship is NOT hard-coded: it is whatever the chart renders, so the bundle can never
# silently miss an image the chart pulls.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${OUT:-$ROOT/../airgap-bundle}"
CLI="${CONTAINER_CLI:-$(command -v podman || command -v docker || true)}"
PLATFORM="linux/amd64"
INCLUDE_GPU="${INCLUDE_GPU:-false}"
CHART_ONLY="${CHART_ONLY:-false}"
CHART_DIR="$ROOT/deploy/helm/dgcl-engine"
MARK="REGISTRY.INVALID"   # placeholder registry, stripped again below
[ -n "$CLI" ] || { echo "ERROR: need podman or docker on PATH (or set CONTAINER_CLI)" >&2; exit 1; }
command -v helm >/dev/null || { echo "ERROR: helm is required" >&2; exit 1; }

CHART_VERSION="$(awk '/^version:/{print $2}' "$CHART_DIR/Chart.yaml")"
GPU_SET=""; [ "$INCLUDE_GPU" = "true" ] && GPU_SET="idp.gpu.enabled=true"
# Every image the chart will pull, with the registry prefix removed (e.g. dgcl-engine-backend:v1.0.0).
REQUIRED=()
while IFS= read -r line; do REQUIRED+=("$line"); done < <(helm template dgcl "$CHART_DIR" \
  --set "global.imageRegistry=$MARK" --set "${GPU_SET:-global.unused=x}" \
  | sed -n 's/^ *image: *"\{0,1\}\([^" ]*\)"\{0,1\} *$/\1/p' | sed "s|^$MARK/||" | sort -u)
[ "${#REQUIRED[@]}" -ge 3 ] || { echo "ERROR: chart renders only ${#REQUIRED[@]} images: ${REQUIRED[*]:-none}" >&2; exit 1; }
echo "== chart $CHART_VERSION needs: ${REQUIRED[*]}"

PREV_REDIS_LINE=""
[ -f "$OUT/MANIFEST.txt" ] && PREV_REDIS_LINE="$(grep '^redis source:' "$OUT/MANIFEST.txt" || true)"
if [ "$CHART_ONLY" = "true" ]; then
  [ -d "$OUT/images" ] && ls "$OUT"/images/*.tar >/dev/null 2>&1 || { echo "ERROR: CHART_ONLY needs an existing $OUT/images" >&2; exit 1; }
  rm -rf "$OUT/charts" "$OUT/scripts"; mkdir -p "$OUT/charts" "$OUT/scripts"
  find "$OUT" -maxdepth 1 -type f ! -name MANIFEST.txt -delete   # stale docs/values/checksums from the last build
else
  rm -rf "$OUT"; mkdir -p "$OUT/images" "$OUT/charts" "$OUT/scripts"
fi

dockerfile_for() {
  case "$1" in
    dgcl-engine-backend:*)      echo Dockerfile.backend ;;
    dgcl-engine-backend-gpu:*)  echo Dockerfile.backend-gpu ;;
    dgcl-engine-frontend:*)     echo Dockerfile.frontend ;;
    *) return 1 ;;
  esac
}
tarname() { echo "$1" | sed 's|/|-|g; s|:|_|' ; }   # bitnami/redis:8.10.2 -> bitnami-redis_8.10.2

for ref in "${REQUIRED[@]}"; do
  local_ref="localhost/$ref"
  if [ "$CHART_ONLY" = "true" ]; then
    ls "$OUT/images/$(tarname "$ref")_amd64.tar" >/dev/null || { echo "ERROR: existing bundle lacks the image for $ref" >&2; exit 1; }
    continue
  fi
  if df_name="$(dockerfile_for "$ref")"; then
    echo "== building $ref ($PLATFORM)"
    "$CLI" build --platform "$PLATFORM" -f "$ROOT/docker/$df_name" -t "$local_ref" "$ROOT"
  elif [ "${ref%%:*}" = "bitnami/redis" ]; then
    # Upstream Bitnami now publishes only :latest. Pull it, require its version label to equal the tag the
    # chart pins, then retag. Fails loudly if upstream moves on.
    echo "== pulling redis ($PLATFORM)"
    "$CLI" pull --platform "$PLATFORM" docker.io/bitnami/redis:latest
    ver="$("$CLI" image inspect docker.io/bitnami/redis:latest --format '{{index .Config.Labels "org.opencontainers.image.version"}}')"
    [ "$ver" = "${ref#*:}" ] || { echo "ERROR: upstream redis is $ver but chart pins ${ref#*:}; update redis.image.tag" >&2; exit 1; }
    "$CLI" tag docker.io/bitnami/redis:latest "$local_ref"
  else
    echo "ERROR: don't know how to build or fetch '$ref'" >&2; exit 1
  fi
  arch="$("$CLI" image inspect "$local_ref" --format '{{.Architecture}}')"
  [ "$arch" = "amd64" ] || { echo "ERROR: $local_ref is $arch, expected amd64" >&2; exit 1; }
  "$CLI" save --format docker-archive -o "$OUT/images/$(tarname "$ref")_amd64.tar" "$local_ref"
done

echo "== packaging chart"
helm package "$CHART_DIR" --destination "$OUT/charts" >/dev/null
helm template dgcl "$OUT/charts/dgcl-engine-$CHART_VERSION.tgz" >/dev/null   # must render offline from the .tgz

cp "$ROOT"/deploy/airgap/{load-and-push-images.sh,verify-bundle.sh} "$OUT/scripts/"
cp "$ROOT/deploy/airgap/INSTALL.md" "$OUT/"
cp "$CHART_DIR/values.yaml" "$OUT/values-airgap.yaml"   # the FULL values file, for the team to edit
chmod +x "$OUT"/scripts/*.sh

# Image name and id are read from the tarballs themselves (a docker-archive's config file is named after the
# image id), so the manifest always describes exactly what is in images/, however the tars got there.
{
  echo "dgcl-engine airgap bundle   built $(date -u +%Y-%m-%dT%H:%M:%SZ)   platform $PLATFORM   chart $CHART_VERSION"
  echo; echo "images (name as loaded -> image id):"
  for t in "$OUT"/images/*.tar; do
    cfg="$(tar -xOf "$t" manifest.json | tr ',' '\n' | sed -n 's/.*"Config":"\([^"]*\)".*/\1/p' | head -1)"
    tag="$(tar -xOf "$t" manifest.json | tr ',' '\n' | sed -n 's/.*"\(localhost\/[^"]*\)".*/\1/p' | head -1)"
    printf "  %-52s %s\n" "$tag" "${cfg%.json}"
  done
  echo
  if [ "$CHART_ONLY" != "true" ]; then
    echo "redis source: docker.io/bitnami/redis:latest digest $("$CLI" image inspect docker.io/bitnami/redis:latest --format '{{.Digest}}') (upstream ships only :latest; retagged to the version it reports)"
  else
    echo "${PREV_REDIS_LINE:-redis source: see the build that produced images/bitnami-redis_*.tar}"
  fi
} > "$OUT/MANIFEST.txt"

SHA="shasum -a 256"; command -v sha256sum >/dev/null && SHA="sha256sum"
# Every shipped file except SHA256SUMS itself.
( cd "$OUT" && find . -type f ! -name SHA256SUMS | sed 's|^\./||' | sort | xargs $SHA > SHA256SUMS && $SHA -c SHA256SUMS >/dev/null )
echo "== done: $OUT"; ls -la "$OUT" "$OUT/images"
