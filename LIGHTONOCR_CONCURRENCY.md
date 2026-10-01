# LightOnOCR Latency & Concurrency

Why a scanned document could take up to 30 minutes for ~40 pages even though LiteLLM
itself reported ~16s per page, and what changed to fix it.

## Root causes

1. **No HTTP connection reuse.** `LightOnOCREngine.process_page` opened a brand new
   `httpx.Client` (fresh DNS + TCP + TLS handshake) for every single page. LiteLLM's own
   reported latency only starts once it receives an already-connected request, so this
   overhead was invisible on the LiteLLM side but fully counted on the application side.
2. **Fully sequential pages.** Every page of a document was OCR'd one at a time
   (`for page in pages: await ...`), so total document time scaled linearly with page
   count × per-page latency. A 40-page document paid the full per-page cost 40 times in a
   row with zero overlap.

Neither cause is about LightOnOCR/vLLM being slow — both are about how this app called it.

## What changed

### 1. Persistent HTTP client (`idp/services/ocr/lightonocr_engine.py`)

`LightOnOCREngine` now lazily builds one `httpx.Client` per process and reuses it for
every page/document, instead of a `with httpx.Client(...)` per call. Connections are
pooled and reused, so only the first request pays handshake cost. `httpx.Client` is
safe for concurrent use across threads, which matters because page calls run on
`asyncio.to_thread` workers.

### 2. Page-level concurrent fan-out (`DocumentProcessor._run_lightonocr_pages`)

Pages of one document are now sent `MAX_PAGE_WORKERS` at a time via `asyncio.gather`
instead of one at a time. Results are still returned in page order — `asyncio.gather`
preserves input order regardless of which call finishes first.

### 3. Tier-wide concurrency cap (`idp/services/ocr/lightonocr_concurrency.py`)

idp can run multiple replicas, and each replica multiple worker processes. A plain
in-process `asyncio.Semaphore` would only cap concurrency *within one process*, so the
effective load on the shared GPU/vLLM backend would multiply by however many
replicas/workers happen to be running — not a real cap.

`LightOnOCRConcurrencyLimiter` uses a Redis sorted set as a distributed counting
semaphore instead: every in-flight call registers itself with a timestamp, and only the
oldest `LIGHTONOCR_MAX_CONCURRENT_CALLS` entries are allowed to proceed. A crashed
holder's entry is reaped by age (not by relying on a clean release), so a dead pod can
never permanently occupy a slot. If Redis is unavailable, it falls back to a local
`asyncio.Semaphore` (same degradation pattern already used by `DocumentProcessor`'s
singleflight lock) rather than blocking OCR entirely.

This is flow control, not a correctness lock: if no slot frees up within 45s, a call
proceeds anyway rather than hanging forever.

## Why concurrency is the lever, not client-side "batching"

LightOnOCR is served through vLLM behind LiteLLM's OpenAI-compatible
`/chat/completions` endpoint. There is no API to submit "a batch of pages" in one call.
vLLM performs its own continuous/dynamic batching **server-side** across whatever
concurrent requests it currently has in flight — the only lever this app has is how many
requests it sends at once. Raising client-side concurrency is what lets vLLM's scheduler
actually batch work on the GPU; sending pages one at a time (the old behavior) meant
vLLM never had more than one request to batch.

Docling (the CPU OCR path) is different: `converter.convert()` already processes an
entire multi-page PDF in one call internally, so there is no per-page loop to
parallelize on our side. Its real scaling lever is more replicas/processes, not
intra-process concurrency — a single process's native PDF backend is not thread-safe
for concurrent `convert()` calls (`docling_convert_lock()`), and each profile is
already configured with a fixed `AcceleratorOptions.num_threads` (see
`config/docling_profiles.py`).

## Config knobs

| Setting | Where | Meaning |
|---|---|---|
| `MAX_PAGE_WORKERS` | `idp/core/config.py` | How many pages of **one document** are sent to LightOnOCR at once. Bounds how many page images one document holds in memory concurrently. |
| `LIGHTONOCR_MAX_CONCURRENT_CALLS` | `idp/core/config.py` | Tier-wide cap on concurrent LightOnOCR calls across **every** idp pod/process, enforced via Redis. This is the real GPU-facing limit. |

`LIGHTONOCR_MAX_CONCURRENT_CALLS` should be sized against the vLLM server's own
configured concurrency (e.g. `max_num_seqs` or equivalent) — ask whoever operates that
server, don't guess. Tune it up gradually while watching vLLM/LiteLLM's own reported
latency; if it climbs as you raise the cap, you've found the server's real ceiling.

## A note on cluster topology

This app can run several idp replicas, each with several worker processes, so the
effective client-side concurrency hitting the GPU is `LIGHTONOCR_MAX_CONCURRENT_CALLS`
× however many replicas/processes are actually deployed — but that multiplier is
**not** reliably reflected by this repo's local Helm values files (e.g.
`deploy/helm/dgcl-engine/values-airgap.yaml`), which may not match what's actually
running on the live Kubernetes cluster. Check the real live values with `kubectl`
(replica count, HPA max, `--workers` argument) rather than trusting the committed
values file, if you need to reason about actual total concurrency against the GPU.

## Testing

- `tests/idp/unit/test_lightonocr_concurrency.py` — the distributed limiter: local
  fallback enforces the cap, Redis-backed acquire/release, cap respected under real
  concurrent contention, a slot is released even if the holder raises, Redis errors
  fail open, and a stuck acquire gives up and proceeds after its timeout instead of
  hanging.
- `tests/idp/unit/test_lightonocr_page_fanout.py` — `DocumentProcessor._run_lightonocr_pages`:
  results stay page-ordered under concurrency, `MAX_PAGE_WORKERS` actually bounds
  in-flight pages, empty input, a page whose call raises becomes a failed result without
  stopping the rest, and a page returned with `extraction_failed=True` is counted
  correctly.
- `tests/idp/unit/test_lightonocr_routing.py` — unchanged behavior of the engine/adapter
  themselves (39 pre-existing tests), including with the new persistent `httpx.Client`.
