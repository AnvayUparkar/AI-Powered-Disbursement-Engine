# Production Scaling Plan — DGCL Disbursement Engine

**Status:** proposal for review. Nothing here is implemented yet.
**Context:** two polled APIs (DMS + LOS), 30s cadence, auto-run the 12-point DGCL
checklist per case, dump results to S3.

---

## 0. The short answer

**Do not decompose this into microservices.** Split it into **four deployables divided by
scaling axis**, which is a different and much smaller change.

Microservice decomposition splits by *domain noun* (a "document service", a "scorecard
service", a "KYC service"). That buys team autonomy and independent release cycles. You have
one codebase, one team, and a pipeline whose stages are inherently sequential per case — so
you would pay the distributed-systems tax (network hops, partial failure, distributed
tracing, schema versioning across 8 repos) and get almost nothing back.

What you actually have is **one workload with four wildly different resource profiles**
sharing a single process. That is the real problem, and it is worth fixing:

| Work | Bound by | Today |
|---|---|---|
| Docling/OCR/TableFormer | CPU + GPU + ~400 MB resident models | in the Celery worker |
| LLM / VLM calls | external API latency + rate limits | in the same worker |
| Checkers / scorecard | trivial CPU, pure Python | in the same worker |
| DMS/LOS polling | network IO, needs a scheduler | not built yet |

The pathology: **a 45-second LLM timeout currently occupies a worker slot that is holding
400 MB of ONNX models resident.** You cannot scale OCR throughput without also scaling LLM
concurrency, and vice versa. That single coupling is the main thing to break.

---

## 1. Measured baseline

Everything in this section was measured on this machine (Apple Silicon, MPS) against the
PDFs in `poc_data/idp_temp/`. **These are local dev numbers, not production numbers** — see
§9 for what still has to be measured.

### 1.1 Document processing cost

```
doc                                    pages  parse_s  s/page  elements  tables
MANUAL CIBIL REPORT APP.pdf                4    29.17    7.29       368      10
ESIGN_AUDIT_TRAIL_DOCUMENT.pdf             2     7.87    3.94       187       1
KFSReport..pdf                             7    25.95    3.71        33      12
ONLINE BUSINESS.pdf                        5     9.05    1.81       126       2

TOTAL 72.1s / 18 pages / 4 docs  ->  4.00 s/page, 18.0 s/doc
```

**4 s/page is the number that governs everything below.** Note the 4x spread (1.81 → 7.29
s/page): cost tracks element density and table count, not page count alone. Capacity
planning must use pages, not documents, and ideally weight by table presence.

This aggregate is superseded for planning purposes by the stage-level profile in §1.5 —
use that instead, because the stages do not scale the same way on different hardware.

### 1.2 Model load cost

```
RSS after imports          :    35 MB
converter #1 cold build    :  4.00 s  -> RSS 379 MB  (+344 MB)
converter #1 cached lookup :  0.0000 s
converter #2 (new profile) :  0.08 s  -> RSS 379 MB  (+0 MB)
```

Two consequences, one of which contradicts an assumption worth flagging:

- A worker process costs **~400 MB resident** before it does any work, and **4 s** of cold
  start. Process-per-task is therefore off the table; workers must be long-lived.
- **A second profile is free** (0.08 s, 0 MB). Docling shares loaded model instances across
  `DocumentConverter` instances. I had assumed 6 profiles meant 6x model memory — it does
  not. Per-profile tuning is cheap; use it freely.

### 1.3 Throughput ceiling, single worker

```
cases/hour = 3600 / (pages_per_case x 4.0s / parallelism)
```

At an assumed 25 pages/case:

| Parallel IDP slots | Cases/hour | Sustained cases/30s poll |
|---|---|---|
| 1  | 36   | 0.3 |
| 4  | 144  | 1.2 |
| 16 | 576  | 4.8 |
| 64 | 2304 | 19.2 |

Your 30s poll implies a target. If you expect **N cases per poll**, you need roughly
`N x 25 x 4.0 / 30` parallel IDP slots — about **3.3 x N**. Ten cases per poll needs ~33
slots, i.e. 8-9 machines at 4 slots each. **Get a real pages-per-case and cases-per-poll
number before sizing anything** — this table is the model, not the answer.

### 1.4 Known bottlenecks in the current code

| # | Issue | Evidence | Impact |
|---|---|---|---|
| B1 | LLM and OCR share a worker slot | `celery_app.py` runs the whole graph in one task | Cannot scale the two independently |
| B2 | `worker_pool="threads"` | `pipeline/celery_app.py:24`, commented "Windows compatibility" | GIL contention on Python-side work; ONNX/torch release the GIL, the rest does not |
| B3 | VLM loop is strictly serial with a 250 ms floor | `document_processor.py:161` `await asyncio.sleep(0.25)` | 40 flagged elements = 10 s of pure sleep |
| B4 | No idempotency on the poll | not built | A 30 s poll will re-enqueue in-flight cases |
| B5 | Local-filesystem artifacts | `poc_data/` tree | Not shared across workers; blocks horizontal scale |
| B6 | Whole-graph retry granularity | `run_pipeline_task` | A failure in `push_results` re-runs all the OCR |
| B7 | No per-stage timing in logs | `ProcessingMetrics` exists but is per-document, not emitted as metrics | Cannot find the real bottleneck in prod |
| B8 | OCR pinned to CPU on macOS | Docling's RapidOCR path handles CUDA/DirectML only | Linux + CUDA changes the economics; re-measure there |
| B9 | LLM timeout is 45-60s | `pipeline/engines/llm_field_extractor.py:258`, `:352` | A single slow provider response blows any sub-minute case SLA on its own |

### 1.5 Where the time actually goes (stage profile)

Measured with `settings.debug.profile_pipeline_timings`, aggregated over 13 pages of the
same fixtures. **This is the number to plan with**, not the 4 s/page aggregate:

```
ocr                  2.24 s/page    74%    <- runs on CPU, not the GPU
table_structure      1.11 s/page    37%    <- MPS
layout               0.38 s/page    13%    <- MPS
page_parse / assemble / reading_order   0.03 s/page   1%
-----------------------------------------------------
pipeline_total       3.04 s/page           wall clock; stages overlap across pages
```

Percentages exceed 100 because Docling's threaded pipeline overlaps stages across pages —
`pipeline_total` is the wall clock, the rest are occupancy.

Two conclusions:

- **OCR is three quarters of the cost and is the one stage not on the GPU.** That is where
  essentially all the headroom is (B8). Everything else is already accelerated.
- **2.24 s/page on OCR is suspiciously high for digital PDFs.** With `mode=DEFAULT` Docling
  only OCRs regions lacking a text layer, so either these fixtures are scans,
  `force_full_page_ocr=True` is in play, or layout clusters are not matching the text layer.
  **If production is mostly digital PDFs with text layers, OCR should largely be skipped and
  the real figure is nearer 1 s/page.** That single question is a 3x swing in fleet size and
  is the highest-value thing to determine — see §9.8.

### 1.6 Latency budget — 10 documents x 6 pages per case

For a latency target the governing number is **the slowest single document**, not the page
total, because N documents run on N slots concurrently.

| | today (Mac, OCR on CPU) | Linux + CUDA (ESTIMATED) |
|---|---|---|
| slowest 6-page document | 18.2 s | ~9 s |
| + LLM extraction (parallel across docs) | 3-5 s | 3-5 s |
| + queue hops | 1-2 s | 1-2 s |
| + assembly (checkers, scorecard, S3) | 2-3 s | 2-3 s |
| **case wall clock** | **25-28 s** | **15-19 s** |

**A 30-40s per-case SLA is achievable**, with three conditions:

1. **10 IDP slots per in-flight case.** This is the real cost of the target. One case at a
   time needs ~10-12 slots; ten cases per 30s poll needs **~120 slots** (~30 GPU nodes at 4
   slots each). Latency and throughput are separate budgets — sizing for one does not give
   you the other.
2. **The tail sets the SLA, not the mean.** At the measured 7.29 s/page worst case, a single
   6-page document is **44 seconds on its own** and blows the target before anything else
   runs. Capacity must be planned against p99 per document type.
3. **The LLM needs an aggressive timeout.** 45-60s today (B9) against a 35s SLA is
   incoherent. Drop to ~8s with one retry, and consider hedged requests so a provider p99
   does not become a case p99.

**The CUDA column is extrapolation, not measurement.** It assumes ONNX Runtime's CUDA
execution provider gives 3-5x on the OCR stage. Verify before committing to an SLA.

---

## 2. Target architecture

Four deployables. Everything else stays one codebase, one repo, one deploy pipeline.

```
                    ┌──────────────────────────────────────────┐
   DMS API ────────▶│ (1) INGESTION SERVICE                    │
   LOS API ────────▶│  poll 30s, join on case_id, dedupe,      │
                    │  lease, enqueue                          │
                    └────────────────┬─────────────────────────┘
                                     │ Redis: q.idp (per-document)
                    ┌────────────────▼─────────────────────────┐
                    │ (2) IDP WORKERS        [CPU/GPU, ~400MB]  │
                    │  Docling + OCR + TableFormer             │
                    │  scales on PAGES/sec                      │
                    └────────────────┬─────────────────────────┘
                                     │ Redis: q.reason (per-document)
                    ┌────────────────▼─────────────────────────┐
                    │ (3) REASONING WORKERS  [IO-bound, ~50MB] │
                    │  LLM sanitise/classify, VLM fallback     │
                    │  scales on API RATE LIMIT                 │
                    └────────────────┬─────────────────────────┘
                                     │ Redis: q.assemble (per-case)
                    ┌────────────────▼─────────────────────────┐
                    │ (4) ASSEMBLY + API     [cheap]            │
                    │  checkers, 12-point scorecard, S3 push,  │
                    │  FastAPI for the review UI                │
                    └──────────────────────────────────────────┘

   Shared: Postgres (case state, idempotency, audit) | S3 (artifacts) | Redis (queues)
```

### Why exactly these four

Each boundary exists because the two sides **scale on a different signal** and **fail
differently**:

1. **Ingestion** — singleton-ish, network-bound, needs a leader. Cannot be scaled by adding
   replicas without distributed locking (see §3.2). Separate because it is the only stateful
   scheduler.
2. **IDP** — the expensive tier. 400 MB + GPU. Scales on pages/sec. Separate so you buy
   exactly as much of the expensive thing as you need.
3. **Reasoning** — 50 MB, does nothing but wait on HTTP. Scales to hundreds of concurrent
   slots on one small box. Separate so LLM latency never blocks a model slot. **This is the
   single highest-value split.**
4. **Assembly + API** — milliseconds of pure Python. Colocated with the API because the
   review UI needs the same data and neither is a bottleneck.

### What stays shared

One repo, one container image, four entrypoints. `config/`, `idp/`, `pipeline/` and the
models stay shared libraries. The services differ only in which queue they consume. This
keeps schema changes atomic and avoids the versioning tax that kills real microservices.

---

## 3. Ingestion design (the 30s poll)

### 3.1 The join

A case is runnable only when **both** DMS documents and LOS data exist. Poll both, persist
what you see, and let the DB decide readiness rather than holding it in memory:

```sql
CREATE TABLE case_state (
  case_id            TEXT PRIMARY KEY,
  los_seen_at        TIMESTAMPTZ,
  los_checksum       TEXT,
  dms_seen_at        TIMESTAMPTZ,
  dms_doc_count      INT,
  dms_manifest_hash  TEXT,
  status             TEXT NOT NULL,   -- discovered|ready|leased|running|done|failed|quarantined
  lease_owner        TEXT,
  lease_expires_at   TIMESTAMPTZ,
  attempt_count      INT NOT NULL DEFAULT 0,
  input_fingerprint  TEXT,            -- hash(los_checksum + dms_manifest_hash)
  last_error         TEXT,
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON case_state (status, lease_expires_at);
```

`status='ready'` becomes a simple query, not a coordination problem.

### 3.2 Idempotency — the part that bites at 30s

A 30-second poll against a pipeline that takes minutes **will** re-enqueue work in flight.
Three defences, all needed:

1. **Input fingerprint.** `input_fingerprint = sha256(los_checksum + dms_manifest_hash)`.
   Never run the same fingerprint twice. This also gives you free re-run semantics: if DMS
   adds a document, the hash changes and a re-run is correct rather than wasteful.
2. **Lease, not lock.** Claim with `UPDATE ... WHERE status='ready' AND (lease_expires_at IS
   NULL OR lease_expires_at < now()) RETURNING case_id` — atomic, and self-healing if a
   worker dies mid-case. Renew the lease in a heartbeat; expiry returns the case to the pool.
3. **Celery task id = fingerprint.** `apply_async(task_id=f"case:{case_id}:{fingerprint}")`
   makes duplicate submission a no-op at the broker.

### 3.3 Polling discipline

- **Watermark, don't full-scan.** Track `last_modified_cursor` per API; request only deltas.
  A full listing every 30s will not survive production volume.
- **Jitter the schedule** (`30s ± 5s`) so replicas don't stampede.
- **Decouple poll from enqueue.** The poller only writes `case_state`. A separate dispatcher
  drains `status='ready'` at a rate the cluster can absorb — this is your backpressure valve.
- **Circuit-break the upstreams.** If DMS 5xxs, stop hammering it; exponential backoff with
  a cap, and emit a metric rather than filling the log.

---

## 4. Batch processing design

### 4.1 Unit of work: the document, not the case

Today the unit is the case, which means one 40-page bank statement blocks everything behind
it and a failure in `push_results` re-runs every OCR (B6). Invert it:

```
case fan-out ──▶ N document tasks (parallel, independently retryable)
                        │
                        ▼  barrier: all documents done
              case assembly task (checkers -> scorecard -> S3)
```

Celery `chord` expresses this directly. Benefits: per-document retry, natural parallelism
across the fleet, and a slow document delays only its own case's barrier.

### 4.2 Queue topology

| Queue | Consumer | Concurrency | Rationale |
|---|---|---|---|
| `q.idp` | IDP workers | `prefetch=1`, `concurrency = cores` | Long, CPU-bound, uneven. Prefetch >1 causes head-of-line blocking |
| `q.reason` | Reasoning workers | `concurrency = 50-200` | Pure IO wait |
| `q.assemble` | Assembly workers | `concurrency = cores x 2` | Milliseconds |
| `q.idp.slow` | IDP workers (separate pool) | small | Quarantine lane, §4.4 |

**`prefetch_multiplier=1` on `q.idp` is not optional.** Celery's default of 4 will hand one
worker four 40-page statements while its neighbour idles.

### 4.3 Smart batching — where it actually helps

"Batching" is only a win where there is a fixed per-call overhead to amortise. Be selective:

| Candidate | Batch? | Why |
|---|---|---|
| Docling page inference | **Already batched internally** | Docling batches pages; leave it |
| LLM field extraction | **Yes — highest value** | One HTTP round trip per document today. Batch 5-10 documents per request, or move to a provider batch API. Cuts calls ~10x |
| VLM region correction | **Yes** | Currently one call per region, serial, +250 ms sleep (B3). Batch all flagged regions of a page into one multi-image call |
| S3 writes | **Yes** | Batch per case, one multipart upload, not one PUT per artifact |
| Checkers | **No** | Microseconds; batching adds latency for nothing |

Batching trades latency for throughput. Since this is an automated pipeline with no human
waiting, that trade is nearly free — but cap the batch wait (e.g. "10 documents or 2
seconds, whichever first") so a quiet period doesn't strand work.

### 4.4 Failure handling

- **Classify errors.** Transient (5xx, timeout, rate limit) → retry with exponential backoff
  + jitter. Permanent (corrupt PDF, unsupported MIME) → fail fast to quarantine. Retrying a
  corrupt file 5 times is 5x wasted GPU.
- **Poison-pill quarantine.** After `attempt_count >= 3`, move to `q.idp.slow` with a longer
  timeout; after that, `status='quarantined'` and alert. Never let one document loop forever.
- **Partial results are valid.** If 9 of 10 documents succeed, still produce the scorecard
  with the tenth marked unavailable. The 12-point checklist already models
  `INDETERMINATE` — use it rather than failing the case.
- **Idempotent writes.** Every S3 key derives from `(case_id, fingerprint, artifact)` so a
  retry overwrites rather than duplicates.

### 4.5 Caching

- **Content-addressed document cache.** Key on `sha256(file_bytes) + profile_fingerprint`.
  The same PAN card re-submitted across cases is parsed once. Given 18 s/doc, this is likely
  the highest-ROI single optimisation if document reuse is common — **measure the duplicate
  rate first.**
- **Keep the converter warm.** `prewarm_docling_converters()` exists; call it in the worker
  `worker_ready` signal so the 4 s cold start is paid at boot, not on the first case.

---

## 5. Smart logging and observability

### 5.1 Structured, correlated, sampled

Every log line JSON, every line carrying the correlation triple:

```json
{"ts":"...","level":"INFO","svc":"idp-worker","event":"stage.complete",
 "case_id":"LOAN_001","doc_id":"LOAN_001:pan","run_id":"01HX...","fingerprint":"a3f...",
 "stage":"docling_parse","duration_ms":18042,"pages":4,"elements":368,"tables":10,
 "device":"mps","profile":"character_box_forms"}
```

`run_id` (ULID, one per case execution) is what makes a re-run distinguishable from the
original — essential once retries exist.

**Sample aggressively.** This codebase already emits per-element decision logs
(`docling_region_decision`, one line per OCR element). At 368 elements/document that is
thousands of lines per case. Keep them at DEBUG, sample at 1%, and emit the *aggregate* at
INFO: `{"event":"region_decisions","skipped_in_table":41,"retained_uncaptured":3,...}`.

### 5.2 Log the decisions, not the narration

The useful logs in this system are the ones that explain *why an answer came out the way it
did* — the codebase already has good instincts here. Keep and standardise:

- `RETAINED_UNCAPTURED_TABLE_TEXT`, `SKIPPED_INSIDE_DOCLING_TABLE` — why an element is or
  isn't in the output
- table dropped by the fill-ratio gate — currently `logger.debug`; **promote to WARNING with
  the ratio**, a silently vanishing table is a data-loss event
- `location_status` distribution per document (`resolved` / `unresolved` / `not_extracted` /
  `not_locatable` / `no_ocr_text`) — this is your extraction-quality signal
- every LLM call: model, prompt tokens, completion tokens, latency, retry count, **cost**

Delete the narration ("Beginning processing...", "Executing...") — it is noise that hides
the above.

### 5.3 Metrics (Prometheus)

RED per stage plus the queue signals that actually predict trouble:

```
dgcl_stage_duration_seconds{stage,doc_type,profile}     histogram
dgcl_stage_failures_total{stage,error_class}            counter
dgcl_queue_depth{queue}                                 gauge   <- the #1 alert
dgcl_queue_oldest_age_seconds{queue}                    gauge   <- better than depth
dgcl_case_end_to_end_seconds                            histogram
dgcl_llm_tokens_total{model,direction}                  counter
dgcl_llm_cost_usd_total{model}                          counter
dgcl_pages_processed_total                              counter
dgcl_extraction_quality{status}                         counter  <- location_status buckets
dgcl_checkpoint_verdict_total{checkpoint,verdict}       counter  <- the 12 DGCL points
```

**`queue_oldest_age_seconds` is the alert to build first.** Depth tells you a number; age
tells you whether you are falling behind. Alert when it exceeds your SLA.

`dgcl_checkpoint_verdict_total` is the business metric: a sudden spike in
`INDETERMINATE` on checkpoint 4 means KYC extraction broke, and you will see it in minutes
rather than via a reviewer complaint.

**Cardinality discipline:** never put `case_id`, `doc_id` or `run_id` in a metric label.
They belong in logs and traces. `doc_type` (12 values) and `profile` (6) are safe.

### 5.4 Tracing

OpenTelemetry, one trace per case, spans per stage, propagated through Celery headers. With
a fan-out/barrier topology, a trace waterfall is the only practical way to see whether a
case was slow because of one document or because of queue wait. Sample 1-5% plus 100% of
errors.

### 5.5 Data quality as a first-class signal

Distinct from system health, and the thing that will actually hurt you silently:

- extraction completeness per doc type (how many of the 22 template fields are non-null)
- `location_status` distribution, trended
- OCR confidence distribution (now that real per-model scores exist — layout vs OCR)
- checkpoint verdict mix vs a rolling baseline
- **drift alert**: if today's `not_extracted` rate for `application_form` is 2σ above the
  trailing 7-day mean, something upstream changed. This catches a DMS format change on day
  one instead of week three.

---

## 6. Performance work, ranked

Ordered by expected impact per unit of effort. Items 1-4 are worth doing before any
architectural change.

| # | Change | Effort | Expected gain |
|---|---|---|---|
| 1 | Split reasoning off the IDP worker (B1) | M | Unblocks independent scaling; the enabling change for everything else |
| 2 | **Get OCR onto the GPU — Linux + CUDA** (B8) | M | Targets **74% of per-page cost** (§1.5). The single largest lever — but **re-measure, do not assume** |
| 3 | `prefetch_multiplier=1` + prewarm on `worker_ready` | XS | Removes head-of-line blocking and 4 s cold start |
| 4 | Content-addressed document cache (§4.5) | S | Up to 18 s/doc eliminated on repeats — **measure duplicate rate first** |
| 5 | Batch VLM regions per page, drop the 250 ms sleep (B3) | S | Seconds per document with flagged regions |
| 6 | Cut the LLM timeout to ~8s + retry (B9) | XS | Required for any sub-minute SLA; today one slow response blows it |
| 7 | Document-level fan-out with chord (§4.1) | M | Per-document retry; removes whole-case re-runs |
| 8 | `worker_pool="prefork"` for IDP (B2) | S | Real parallelism; costs ~400 MB per child, budget for it |
| 9 | Batch LLM extraction across documents (§4.3) | M | ~10x fewer API calls, proportional cost cut |
| 10 | Tune `images_scale` per profile | S | Feeds both OCR and TableFormer; cost scales ~quadratically. 3.0 → 2.0 is ~2x faster where accuracy allows |

Ranking changed after the §1.5 stage profile: OCR is 74% of per-page cost and is the only
stage still on CPU, which moves CUDA from #6 to #2. **Its payoff is conditional on §9.7** —
if production documents carry native text layers, OCR is largely skipped and the win shrinks.

Note on #10: per §1.2 extra profiles cost nothing (0.08 s, 0 MB), so tune `images_scale` per
document type rather than globally.

---

## 7. Migration path

Each phase is independently shippable and leaves the system working.

**Phase 1 — Instrument (1 week).** Structured logging, correlation IDs, the metrics in §5.3,
stage timers. Change no behaviour. *You cannot optimise what you cannot see, and every
number in §1 is a dev-machine number.*

**Phase 2 — Durable state (1 week).** `case_state` in Postgres, fingerprints, leases. Move
artifacts from `poc_data/` to real S3 (B5). Still one worker type.

**Phase 3 — Ingestion service (1 week).** Poller + dispatcher against the two APIs, writing
`case_state`. Feature-flag it alongside the existing manual trigger.

**Phase 4 — Split the workers (2 weeks).** Separate `q.idp` and `q.reason`; document-level
fan-out with a chord barrier. This is the big one — do it after Phase 1 proves where the
time goes.

**Phase 5 — Optimise (ongoing).** Items 2, 3, 6, 8, 9 from §6, each validated against the
Phase 1 metrics.

Phases 1 and 2 are worth doing **regardless** of whether you ever split the services.

---

## 8. What I would not do

- **Do not split into 8+ microservices.** One team, one codebase, sequential stages. The
  coordination cost exceeds the benefit.
- **Do not put the models in a shared inference server yet.** Tempting, but it adds a network
  hop to a 4 s/page operation for maybe 10% memory saving. Revisit only if you run many small
  workers.
- **Do not use Kubernetes HPA on CPU%.** A worker blocked on an LLM call shows low CPU and
  will be scaled down exactly when the queue is growing. Scale on `queue_oldest_age_seconds`.
- **Do not cache LLM responses on raw text.** OCR output is non-deterministic across versions;
  you would serve stale extractions after a model upgrade. Cache on
  `sha256(file_bytes) + profile + model_version` instead.
- **Do not add a message bus (Kafka/SQS) yet.** Redis + Celery is already in the repo and
  handles this volume. Revisit past ~10k cases/hour or if you need replay.

---

## 9. Open questions — answer before sizing

The §1.3 table is a model, not a prediction. These change the answer by an order of magnitude:

1. **Cases per poll, peak and sustained?** Drives the entire fleet size.
2. **Pages per case, p50 and p99?** Cost is per page, and the 4x spread in §1.1 means the tail
   dominates.
3. **Duplicate document rate across cases?** Decides whether §4.5 caching is the top win or a
   rounding error.
4. **Target end-to-end SLA?** "Within the hour" and "within 60 seconds" are different systems.
5. **LLM provider rate limit and budget?** Caps reasoning concurrency and may make §4.3
   batching mandatory rather than optional.
6. **Linux + CUDA in production?** Every number in §1 was measured on Apple Silicon with OCR
   pinned to CPU (B8). CUDA changes the economics and must be re-measured.
7. **What fraction of production documents carry a native PDF text layer?** Per §1.5 this is
   a ~3x swing in fleet size: a text layer means OCR is largely skipped, no text layer means
   2.24 s/page of CPU work on every page.
8. **Are DMS documents immutable once written?** If they can change after a case runs, §3.2's
   fingerprint becomes the re-run trigger — and that needs an explicit policy.

---

## 10. Immediate next steps

1. Answer §9.1, §9.2, §9.3 — from the real DMS/LOS, not estimates.
2. Ship Phase 1 instrumentation and run the current pipeline against a realistic batch.
3. Re-measure §1 on the target production hardware.
4. Revisit this document with real numbers; the architecture in §2 should survive, the sizing
   in §1.3 will not.
