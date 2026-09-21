#!/usr/bin/env bash
# Verify the bundle BEFORE loading anything: checksums, presence of every image the chart pulls, image
# architecture, offline chart render. Run from anywhere; it locates the bundle root itself.
set -euo pipefail
cd "$(dirname "$0")/.."

SUM="shasum -a 256 -c"; command -v sha256sum >/dev/null && SUM="sha256sum -c"
echo "== 1/3 checksums"
# An explicit `if`: `cmd && echo ok` does NOT stop a `set -e` script when cmd fails, which would let a
# corrupted bundle through with "BUNDLE OK".
if ! $SUM SHA256SUMS >/dev/null 2>&1; then
  echo "ERROR: checksum mismatch - the bundle is corrupted or incomplete. Re-transfer it." >&2
  $SUM SHA256SUMS 2>&1 | grep -v ': OK$' >&2 || true
  exit 1
fi
echo "all files match SHA256SUMS"

# Names each tarball carries (from its manifest.json; nothing is loaded).
image_tags() { tar -xOf "$1" manifest.json | tr ',' '\n' | sed -n 's/.*"\(localhost\/[^"]*\)".*/\1/p'; }
image_arch() {
  local cfg; cfg="$(tar -xOf "$1" manifest.json | tr ',' '\n' | sed -n 's/.*"Config":"\([^"]*\)".*/\1/p' | head -1)"
  tar -xOf "$1" "$cfg" | tr ',' '\n' | sed -n 's/.*"architecture":"\([^"]*\)".*/\1/p' | head -1
}

echo "== 2/3 every image the chart pulls is in images/"
CHART="$(ls charts/dgcl-engine-*.tgz)"
if command -v helm >/dev/null 2>&1; then
  missing=0
  helm template dgcl "$CHART" --set global.imageRegistry=REGISTRY.INVALID >/dev/null   # renders offline
  while IFS= read -r ref; do
    found=0
    for t in images/*.tar; do image_tags "$t" | grep -qx "localhost/$ref" && found=1 && break; done
    if [ "$found" = 1 ]; then echo "  ok      $ref"; else echo "  MISSING $ref" >&2; missing=1; fi
  done < <(helm template dgcl "$CHART" --set global.imageRegistry=REGISTRY.INVALID \
            | sed -n 's/^ *image: *"\{0,1\}\([^" ]*\)"\{0,1\} *$/\1/p' | sed 's|^REGISTRY.INVALID/||' | sort -u)
  [ "$missing" = 0 ] || exit 1
else
  echo "  (helm not installed on this host; skipped - the load script still checks each image)"
fi

echo "== 3/3 image architecture"
for t in images/*.tar; do
  arch="$(image_arch "$t")"
  printf "  %-46s %s\n" "$(basename "$t")" "$arch"
  [ "$arch" = "amd64" ] || { echo "ERROR: $t is not amd64" >&2; exit 1; }
done
echo "BUNDLE OK"
