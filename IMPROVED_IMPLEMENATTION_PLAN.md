# Hybrid OCR Routing — Implementation Plan

**Scope:** Node 2 IDP (`idp/`) and the field layer consumed by Node 3 (`pipeline/`)
**Branch baseline:** `Anvay`
**Supersedes:** the routing sections of `LIGHTONOCR_IMPLEMENTATION_PLAN.md` (document-level `is_scanned_pdf` → LightOnOCR full page)

---

## 0. Executive summary

Today every page of a document takes one of two paths, decided by a **document-level average** (`< 50 chars/page` in `DocumentPreprocessor._inspect_pdf`):

```
digital → Docling (text layer + RapidOCR)
scanned → LightOnOCR-2-1B on the whole page → (VLM fallback that never fires)
```

Target architecture routes **per page**, then **per region**, and decides acceptance **per field**:

```
                       ┌────────────────────────────────────────────┐
PDF / image ──► Page classifier (PyMuPDF signals, no ML)            │
                       │                                            │
     ┌─────────────────┼───────────────┬─────────────────────┐      │
  DIGITAL        SCANNED_OCR_LAYER   SCANNED              HYBRID    │
     │            (ignore layer)        │                    │      │
  Docling text        └──────────┬──────┴────────────────────┘      │
  layer (today)                  ▼                                  │
     │               Deskew/orient (keep) → Layout detection        │
     │                          (Docling layout model)              │
     │                                   │                          │
     │                     Region classifier (per block/line)       │
     │               printed|handwritten  ×  latin|devanagari|mixed │
     │                        comb-box grid? (existing detector)    │
     │                                   │                          │
     │        ┌──────────────┬───────────┴─────┬──────────────┐     │
     │   PRINTED_LATIN  PRINTED_INDIC     HANDWRITTEN     COMB_BOX  │
     │   PP-OCRv5/v6    Indic engine      crop VLM /      per-cell  │
     │   (RapidOCR)     (cloud or PP-     LightOnOCR      crops →   │
     │                  OCR devanagari)   on the crop     VLM/HTR   │
     │        └──────────────┴────────┬────────┴──────────────┘     │
     ▼                                ▼                             │
  LayoutElements ◄──────── merged OCRElements (bbox + real conf) ───┘
                                      │
                         Field extraction (KV + LLM, today)
                                      │
            Field consensus: engine agreement + validators + LOS signal
                                      │
                 ACCEPT  |  VLM re-read  |  HUMAN_REVIEW (existing)
                                      │
                 Provenance + disagreement telemetry → retraining set
```

Delivery is split into 8 phases. Phase 1 is a hotfix that can ship in days and removes the silent failures; the rest is additive and feature-flagged.

| Phase | Deliverable | Est. effort | Flag |
|---|---|---|---|
| 1 | LightOnOCR hotfix (real confidence, no truncation, correct input) | 2–3 d | existing `LIGHTONOCR_ENABLED` |
| 2 | Evaluation harness v2 (gates everything else) | 3–4 d | — |
| 3 | Per-page classifier + per-page routing | 3–4 d | `PAGE_ROUTING_ENABLED` |
| 4 | Region detection + printed/handwritten + script classification | 6–8 d | `REGION_ROUTING_ENABLED` |
| 5 | Engine adapters behind one interface + region router | 6–8 d | `OCR_ENGINE_*` |
| 6 | Field consensus + validators + decision policy | 5–6 d | `FIELD_CONSENSUS_ENABLED` |
| 7 | Telemetry, provenance, active-learning export | 3–4 d | `OCR_TELEMETRY_ENABLED` |
| 8 | Shadow mode → canary → default-on rollout | 2–3 wks calendar | all |

---

## 1. Current-state findings (why this plan exists)

| # | Finding | Location | Impact |
|---|---|---|---|
| F1 | `confidence = 0.95` hard-coded; `_compute_quality_score` returns ≈0.95 for any text ≥25 chars vs threshold 0.40 | `idp/services/ocr/lightonocr_engine.py` | VLM fallback never fires; truncation/loops accepted silently |
| F2 | `max_new_tokens=1024` | same | Dense printed pages (agreements, KFS, statements) truncated |
| F3 | Text prompt `"Extract the text from this document."` sent | same | LightOnOCR-2 is trained image-only, no text prefix |
| F4 | Page fed after 2–3× raster + adaptive binarization | `document_processor.py` → `_get_page_images(docling_input_path)` | Model trained at 200 DPI, longest edge ≤ 1540 px, RGB |
| F5 | One full-page bbox per page | `lightonocr_adapter.py` | Field location, spatial fusion, comb-box logic get no geometry |
| F6 | Scanned/digital decided by document **average** | `document_preprocessor.py::_inspect_pdf` | Mixed PDFs mis-routed; searchable-scan PDFs with invisible garbage OCR layer treated as digital |
| F7 | Images (`.jpg/.png/.tif`) always `is_scanned=True` → whole page to LightOnOCR | `document_preprocessor.py` | Printed Devanagari (Aadhaar) goes to a European-focused model |
| F8 | `OCRElement.source` is a closed `Literal` | `idp/models/ocr.py` | Any new engine name raises a Pydantic validation error |
| F9 | Benchmark covers 2 PDFs; digital doc CER 0.406, mean bbox containment 0.117 | `tests/idp/ocr_bench_report.json` | Too small to choose engines; bbox quality already weak |

---

## 2. Design principles

1. **Deterministic before learned.** Page type comes from PDF objects, not a model.
2. **Route the smallest unit that has a single character.** Page → region → line/cell.
3. **Never trust a model's self-reported confidence alone.** Acceptance = agreement × validity.
4. **Every element carries geometry and provenance** (`engine`, `engine_version`, `route`, `region_class`).
5. **Engines are replaceable** behind one interface; the choice of cloud vs self-hosted is config, not code.
6. **LOS data is a signal, never a correction source.** OCR must not be "fixed" to match LOS — that hides fraud and defeats the verification engine's purpose.
7. **Additive and flagged.** With all new flags off, output must be byte-identical to today (regression test enforces this).
8. **PII by default.** Aadhaar/PAN crops never leave the VPC unless the engine is explicitly approved; logs are masked (`idp/utils/masking.py`).

---

## 3. Target module layout

```
idp/
  services/
    classification/                  # NEW
      __init__.py
      page_classifier.py             # Phase 3
      region_classifier.py           # Phase 4 (printed/handwritten)
      script_classifier.py           # Phase 4 (image-level script ID, wraps script_detector)
      models.py                      # PageKind, RegionClass, RegionRoute
    routing/                         # NEW
      __init__.py
      region_router.py               # Phase 5: RegionClass -> engine chain
      page_router.py                 # Phase 3: PageKind -> pipeline branch
    engines/                         # NEW (thin adapters)
      __init__.py
      base.py                        # OCREngine protocol, EngineResult
      ppocr_engine.py                # RapidOCR / PP-OCRv5-v6 (already a dependency)
      indic_engine.py                # Azure DI | Google DocAI | PP-OCR devanagari
      lightonocr_crop_engine.py      # wraps fixed LightOnOCREngine, crop mode
      vlm_engine.py                  # wraps existing VLMClient
      textract_engine.py             # optional (printed/handwritten flag source)
    consensus/                       # NEW
      __init__.py
      field_candidates.py            # Phase 6
      agreement.py
      validators.py                  # PAN, Aadhaar (Verhoeff), IFSC, pincode, date, mobile, amount
      decision.py                    # ACCEPT | VLM | HUMAN_REVIEW
    telemetry/                       # NEW
      ocr_events.py                  # Phase 7
      crop_export.py
    ocr/lightonocr_engine.py         # Phase 1 fixes (modified)
    ocr/lightonocr_adapter.py        # Phase 1 fixes (modified)
    document_preprocessor.py         # Phase 3 (per-page kinds)
    document_processor.py            # Phase 3/5 (branching)
  models/
    ocr.py                           # F8: widen `source`, add provenance fields
    classification.py                # NEW pydantic models
tests/
  idp/bench/                         # Phase 2 harness v2
  idp/unit/test_page_classifier.py
  idp/unit/test_region_classifier.py
  idp/unit/test_region_router.py
  idp/unit/test_validators.py
  idp/unit/test_consensus_decision.py
  idp/integration/test_hybrid_routing_e2e.py
  idp/integration/test_flags_off_regression.py
```

---

## 4. Phase 1 — LightOnOCR hotfix (ship first)

Goal: stop silent failures on the current path before any re-architecture.

### 4.1 Changes in `lightonocr_engine.py`

1. **Input normalization** (new helper `_prepare_image`):
   - Source image = deskewed/oriented page from `scan_preprocessor` **before** CLAHE/binarization. Add a `return_stage="deskewed"` option to `preprocess_scanned_document()` so it can emit the geometric-only result alongside the binarized one.
   - Convert to RGB; downscale so longest edge ≤ 1540 px (`Image.thumbnail`, LANCZOS). Do not upscale.
2. **Prompt:** image-only user turn; delete the text item.
3. **Generation:** `max_new_tokens=settings.LIGHTONOCR_MAX_NEW_TOKENS` (default 4096), `do_sample=True, temperature=0.2, top_p=0.9` (LightOn's published settings) with a `LIGHTONOCR_DETERMINISTIC` flag that switches to greedy for reproducible audits. `output_scores=True, return_dict_in_generate=True`.
4. **Real confidence:** token probabilities via `model.compute_transition_scores(..., normalize_logits=True)`.
   - `conf_mean` = mean token prob; `conf_p05` = 5th percentile over a sliding 8-token window mean. Report `confidence = conf_p05` (a single bad span should lower the page score).
5. **Hard-fail conditions** (return result with `hard_fail_reason`, adapter marks `extraction_failed=True`):
   - `truncated`: generated tokens ≥ `max_new_tokens`.
   - `repetition`: any 6-gram repeated ≥ 5 times, or compression ratio (`len(zlib.compress(text)) / len(text)`) < 0.15.
   - `empty`: < 5 non-whitespace chars on a page whose ink ratio > 2 %.
6. **Replace `_compute_quality_score`** with:
   ```
   quality = conf_p05 × (1 − garble_ratio) × coverage_factor
   coverage_factor = min(1, chars_out / expected_chars(ink_ratio, page_area))
   ```
   where `expected_chars` is a linear fit from the benchmark set (Phase 2); until then use `0.6 × ink_pixels / avg_glyph_area`. `garble_ratio` from existing `OCRConfidenceEvaluator.is_garbled_text` over lines.

### 4.2 Changes in `lightonocr_adapter.py`

- Split output into lines; if the model returns markdown tables, keep the table text as one element per row.
- Until Phase 5 provides geometry, **align lines to boxes** by running PP-OCR detection only (`RapidOCR(det=True, rec=False)`) on the same image and matching lines by vertical order + fuzzy text overlap (Needleman-Wunsch on line sequences). Unmatched lines keep a full-page bbox with `metadata.bbox_estimated=True`.

### 4.3 Config additions (`idp/core/config.py`)

```python
LIGHTONOCR_MAX_NEW_TOKENS: int = 4096
LIGHTONOCR_MAX_EDGE_PX: int = 1540
LIGHTONOCR_DETERMINISTIC: bool = False
LIGHTONOCR_QUALITY_THRESHOLD: float = 0.55   # recalibrate in Phase 2
LIGHTONOCR_REPETITION_NGRAM: int = 6
LIGHTONOCR_REPETITION_MAX: int = 5
```

### 4.4 Tests

- `test_lightonocr_routing.py`: mock `generate` to return (a) 4096 tokens → `truncated`, (b) looped text → `repetition`, (c) low-prob tokens → quality below threshold → VLM route taken.
- Assert no text prompt in the constructed conversation.
- Assert the image passed to the processor has `max(size) ≤ 1540` and mode `RGB`.

**Exit criteria:** on the existing fixtures, zero pages accepted with `truncated`/`repetition`; VLM fallback count > 0 on the handwritten fixture.

---

## 5. Phase 2 — Evaluation harness v2 (gates every later phase)

No engine or threshold decision is made without this.

### 5.1 Dataset

`tests/fixtures/ocr_bench_v2/` (git-LFS or S3-synced; **masked or synthetic PII only in git**):

| Doc type | Pages | Must include |
|---|---|---|
| Application form | 30 | printed labels + handwritten values, comb boxes, stamps |
| Aadhaar (front/back) | 20 | bilingual Devanagari/English, masked & unmasked, photocopies |
| PAN | 15 | old/new layouts, phone photos |
| KFS / sanction letter | 15 | dense printed, tables |
| Loan agreement | 15 | long dense pages (truncation stress), signatures |
| Bank statement | 20 | tables, small fonts, searchable-scan PDFs with OCR layers |
| Address proof | 10 | utility bills, mixed quality |

Ground truth JSON per page:
```json
{
  "page": 1,
  "page_kind": "hybrid",
  "regions": [
    {"bbox": [l,t,r,b], "class": "handwritten", "script": "latin", "text": "RAMESH KUMAR"}
  ],
  "fields": {"PAN": "ABCPK1234F", "Pincode": "400076"}
}
```

Label with a lightweight tool (Label Studio) using pre-annotations from PP-OCR to cut effort.

### 5.2 Metrics (per doc type, per region class, per engine)

- CER / WER (normalized: NFC, whitespace collapse, Devanagari nukta normalization).
- **Field exact-match accuracy** (primary business metric) and field recall.
- Bbox IoU@0.5 and containment (reuse existing helpers).
- Page-classifier and region-classifier confusion matrices.
- Latency p50/p95 per page and cost per 1k pages.
- **Consensus calibration** (Phase 6): accuracy of ACCEPTed fields (must be ≥ 99.5 %), review rate.

### 5.3 Runner

`python -m tests.idp.bench.run --engines ppocr,lightonocr,azure_di,gdocai --doc-types all --out reports/bench_<date>.json`
Engine adapters (Phase 5) are reused, so the harness also runs ad-hoc engines side-by-side. Add a CI job that runs a 20-page smoke subset and fails on regression > 1 pp field accuracy.

**Exit criteria:** baseline report committed for (today's pipeline, Phase-1 pipeline, each candidate engine per region class).

---

## 6. Phase 3 — Per-page classification and routing

### 6.1 Models (`idp/models/classification.py`)

```python
class PageKind(str, Enum):
    DIGITAL = "digital"                      # trustworthy visible text layer
    SCANNED = "scanned"                      # raster only
    SCANNED_OCR_LAYER = "scanned_ocr_layer"  # raster + invisible OCR text (do not trust)
    HYBRID = "hybrid"                        # vector text + embedded raster content

class PageClassification(BaseModel):
    page_number: int
    kind: PageKind
    image_coverage: float
    visible_chars: int
    hidden_chars: int
    text_layer_garble_ratio: float
    reasons: list[str]
```

### 6.2 Classifier (`page_classifier.py`)

Signals from PyMuPDF (`import pymupdf`; `get_texttrace()` `type == 3` = invisible render mode, verified):

```python
def classify_page(page) -> PageClassification:
    area = abs(page.rect)
    img_rects = [r for x in page.get_images(full=True) for r in page.get_image_rects(x[0])]
    img_cov = min(1.0, sum(abs(r & page.rect) for r in img_rects) / area)
    spans = page.get_texttrace()
    visible = sum(len(s["chars"]) for s in spans if s["type"] != 3 and s["opacity"] > 0)
    hidden  = sum(len(s["chars"]) for s in spans if s["type"] == 3 or s["opacity"] == 0)
    garble  = garble_ratio(page.get_text())          # reuse OCRConfidenceEvaluator
    ...
```

Decision table (thresholds live in config, calibrated on the Phase 2 set):

| Condition | Kind |
|---|---|
| `visible ≥ 200` and `img_cov < 0.5` and `garble < 0.2` | DIGITAL |
| `hidden > visible` and `img_cov ≥ 0.6` | SCANNED_OCR_LAYER |
| `img_cov ≥ 0.6` and `visible < 50` | SCANNED |
| `visible ≥ 50` and a single image ≥ 10 % of page area | HYBRID |
| `visible ≥ 200` but `garble ≥ 0.2` (broken font encoding / CID without ToUnicode) | SCANNED (re-OCR the render) |
| else | HYBRID |

Extra checks:
- Fonts named `GlyphLessFont` (Tesseract/ABBYY OCR layers) → strong SCANNED_OCR_LAYER signal.
- Image inputs (`.jpg/.png/.tif`) → SCANNED directly.
- Signed loan agreements already have a pyHanko fast path — leave that path untouched.

### 6.3 Preprocessor changes

`PreprocessedDocument` gains `page_kinds: list[PageClassification]`. Keep `is_scanned_pdf` as a **derived** property (`any(kind != DIGITAL)`) so existing callers and tests keep working.

### 6.4 Processor changes (`document_processor.py`)

Replace the single `if prep_doc.is_scanned_pdf and settings.LIGHTONOCR_ENABLED` branch with a page loop:

```
digital_pages   = [p for p in kinds if p.kind == DIGITAL]
raster_pages    = [p for p in kinds if p.kind in {SCANNED, SCANNED_OCR_LAYER}]
hybrid_pages    = [p for p in kinds if p.kind == HYBRID]

Docling(text-layer mode)  ← digital_pages + text portion of hybrid_pages
Raster pipeline (Phase 4) ← raster_pages + raster regions of hybrid_pages
```

Implementation notes:
- Docling supports `page_range`; otherwise split pages to a temp PDF with PyMuPDF `insert_pdf(from_page, to_page)`. Page numbers must be remapped back to original indices before serialization.
- For SCANNED_OCR_LAYER pages, render to image and **strip the text layer** before Docling sees it (Docling would otherwise use it). Keep the hidden text as `metadata.legacy_ocr_layer` — it becomes a third, low-weight vote in consensus.
- Run page preprocessing (deskew/orient) only on raster pages.
- Merge results in original page order before `DocumentSerializer.build_unified_document`.

### 6.5 Tests

- Synthetic PDFs generated in the test (PyMuPDF): pure text; pure image; image + `render_mode=3` text; text + embedded photo; 10 pages with pages 3 & 7 scanned.
- Assert per-page routes and that output page numbers are preserved.
- Flags-off regression test: `PAGE_ROUTING_ENABLED=False` → identical `ParsedDocument` JSON on existing fixtures.

**Exit criteria:** page-kind accuracy ≥ 99 % on bench v2; mixed-PDF fixtures route correctly.

---

## 7. Phase 4 — Region detection and classification

### 7.1 Region detection

Reuse Docling's layout model (already loaded) on raster pages in **layout-only** mode (OCR disabled) to get blocks: text, table, key_value, picture, etc. Then:
- Run PP-OCR **detection only** inside each text/KV block to get line boxes.
- Table blocks: keep TableFormer structure; cells become regions.
- Comb-box grids: existing `CombGridDetector` / `CombBoxDetector` produce cell regions; tag them `COMB_BOX` and keep their grid id for reassembly.
- Picture blocks (photo on Aadhaar/PAN): skip OCR, keep for face-embedding path.

Output unit = **line or cell region** with bbox in page pixel coordinates (and point coordinates for serialization).

### 7.2 Printed vs handwritten classifier (`region_classifier.py`)

Backends behind one interface; choose by config (`REGION_CLASSIFIER_BACKEND`):

```python
class RegionClassifier(Protocol):
    def classify(self, page_img: np.ndarray, regions: list[Region]) -> list[RegionLabel]: ...
```

| Backend | How | When to use |
|---|---|---|
| `crop_cnn` (default, self-hosted) | MobileNetV3-small or ViT-tiny, 2 classes (+`mixed`), input 48×384 grayscale line crop, ONNX on CPU | Default; data stays in VPC; ~1–2 ms/crop |
| `textract` | Use `TextType` of WORD blocks, aggregate per region by majority of area | If Textract is already approved; gives labels for bootstrapping training data |
| `azure_di` | Use `styles[].isHandwritten` spans | If Azure DI is chosen as the Indic engine anyway |
| `heuristic` | Stroke-width variance, baseline straightness, connected-component regularity | Cold-start fallback only |

Training the `crop_cnn`:
1. Bootstrap labels: run Textract or Azure DI once over an **approved, masked** sample of ~2–5k pages → auto-labeled line crops. Spot-check 5 %.
2. Add public data: IAM / IAM-OnDB lines (handwritten Latin), synthetic printed lines rendered from your form templates with the actual fonts, plus Devanagari printed lines rendered from Noto Sans Devanagari.
3. Augment: JPEG artifacts, blur, photocopy noise, skew ±5°, stamp overlays.
4. Train/val/test split **by document**, not by crop.
5. Export ONNX; store in `models/region_classifier/<version>/` with a model card (data, metrics, date).
6. Target: ≥ 98 % accuracy, handwritten recall ≥ 99 % (missing handwriting is the costly error — it would go to the printed engine).

Label `mixed` when the region contains a printed label and handwritten value in one line (common on forms): split at the largest horizontal gap between PP-OCR word boxes and classify halves.

### 7.3 Script classification (`script_classifier.py`)

`script_detector.py` works on text, so script must be decided before choosing the recognizer. Two-step approach:

1. **Doc-type prior** from `config/doc_types.py`: Aadhaar, address proof → expect `mixed`; KFS, agreements, statements → `latin`.
2. **Cheap first pass:** run PP-OCR recognition with the Latin model on the line; if the doc-type prior allows Devanagari and (mean char confidence < 0.6 or `is_garbled_text`), run the Devanagari recognizer and pass both outputs to `ScriptDetector.detect_script`. Keep the one with higher confidence and consistent script.
3. Optional later: a tiny image script-ID head on the same `crop_cnn` backbone (multi-task: handwriting × script) once enough labeled crops exist from telemetry.

### 7.4 RegionClass

```python
class RegionClass(str, Enum):
    PRINTED_LATIN = "printed_latin"
    PRINTED_INDIC = "printed_indic"
    PRINTED_MIXED = "printed_mixed"
    HANDWRITTEN_LATIN = "handwritten_latin"
    HANDWRITTEN_INDIC = "handwritten_indic"
    COMB_BOX = "comb_box"
    TABLE_CELL = "table_cell"      # further split by content class
    NON_TEXT = "non_text"
```

### 7.5 Tests

- Unit: classifier backends return one label per region; `mixed` split logic on a synthetic label+value line.
- Bench: region-class confusion matrix on bench v2.

**Exit criteria:** region classifier meets targets above; region detection recall ≥ 98 % vs ground-truth regions.

---

## 8. Phase 5 — Engine adapters and region router

### 8.1 Engine interface (`engines/base.py`)

```python
class EngineResult(BaseModel):
    text: str
    bbox: list[float]
    confidence: float                 # engine-native, normalized 0..1
    char_confidences: list[float] | None = None
    engine: str                       # "ppocr_v5_en", "azure_di_read", ...
    engine_version: str
    latency_ms: float
    hard_fail_reason: str | None = None

class OCREngine(Protocol):
    name: str
    supports: set[RegionClass]
    data_residency: Literal["local", "cloud_in", "cloud_other"]
    async def recognize(self, image: np.ndarray, regions: list[Region]) -> list[EngineResult]: ...
```

All engines batch regions per page. Cloud engines send **the page once** and map returned words back to regions by IoU (cheaper and more accurate than one call per crop).

### 8.2 Engines

| Engine | Implementation | Regions |
|---|---|---|
| `ppocr` | Existing `rapidocr-onnxruntime` with PP-OCRv5/v6 rec models (`OCR_MODEL`), det already done | PRINTED_LATIN, TABLE_CELL (printed) |
| `ppocr_devanagari` | RapidOCR with the Devanagari rec model (the `DEVANAGARI_OCR_ENABLED` profile already exists) | PRINTED_INDIC (self-hosted baseline) |
| `azure_di` / `gdocai` | REST clients with retry, timeout, circuit breaker; region deployed in India | PRINTED_INDIC, PRINTED_MIXED, optionally HANDWRITTEN_* |
| `lightonocr_crop` | Phase-1-fixed engine, called on a **crop padded 8 px**, upscaled so text height ≈ 32–48 px, longest edge ≤ 1540 | HANDWRITTEN_LATIN, second opinion on PRINTED_LATIN |
| `vlm` | Existing `VLMClient.analyze_region` with field-aware prompt (field name + expected format) | Escalation for any class, COMB_BOX |
| `textract` | Optional; `DetectDocumentText` in `ap-south-1` | HANDWRITTEN_LATIN, classifier bootstrap |

Choice between `azure_di`, `gdocai` and `ppocr_devanagari` for PRINTED_INDIC is **made from the Phase 2 bench**, not upfront. Cloud engines require a compliance sign-off (Aadhaar images are sensitive under UIDAI rules; confirm masking/residency obligations with your compliance team) before enabling in production.

### 8.3 Region router (`routing/region_router.py`)

Config-driven chains (primary, secondary for agreement, escalation):

```python
REGION_ROUTES = {
  "printed_latin":     {"primary": "ppocr",           "secondary": "lightonocr_crop", "escalate": "vlm"},
  "printed_indic":     {"primary": "<bench winner>",  "secondary": "ppocr_devanagari","escalate": "vlm"},
  "printed_mixed":     {"primary": "<bench winner>",  "secondary": "ppocr",           "escalate": "vlm"},
  "handwritten_latin": {"primary": "lightonocr_crop", "secondary": "vlm",             "escalate": "human"},
  "handwritten_indic": {"primary": "vlm",             "secondary": None,              "escalate": "human"},
  "comb_box":          {"primary": "per_cell",        "secondary": "vlm_strip",       "escalate": "human"},
}
```

Rules:
- **Secondary engines run only on field-bearing regions** (regions whose bbox overlaps a KV value or known field anchor), not on every line — keeps cost and latency bounded. Boilerplate paragraphs get primary only.
- **Comb boxes:** OCR each cell individually with a single-char recognizer constraint (digits for Aadhaar/pincode/mobile, `[A-Z0-9]` for PAN), then also read the full strip with the VLM; the two readings are the agreement pair. Reassemble with the existing `_recover_comb_grids` logic.
- Hard-failed or timed-out engines fall through to the next in chain; the failure is recorded in provenance.
- Concurrency: per-page `asyncio.gather` with `MAX_PAGE_WORKERS`; cloud engine semaphore `OCR_CLOUD_MAX_CONCURRENCY`.

### 8.4 Model changes (`idp/models/ocr.py`)

```python
source: str = "docling_ocr"   # widen from Literal (F8); validate against a registry instead
engine_version: str | None = None
route: str | None = None                # e.g. "hybrid/printed_latin/primary"
region_class: str | None = None
alternates: list[dict] = Field(default_factory=list)   # other engines' readings
```

Keep a `KNOWN_SOURCES` set and a validator that logs (not raises) unknown sources, so older serialized docs still load.

### 8.5 Wiring

`document_processor.py`: raster/hybrid pages → `RegionPipeline.run(page_img, page_kind)` → `list[OCRElement]` → converted to `LayoutElement`s via the same path Docling output uses today, so `DocumentSerializer`, `SpatialCellFusion`, `KeyValueExtractor` and comb-box logic keep working unchanged.

Retire the full-page LightOnOCR branch once Phase 5 is default-on (keep code behind `LIGHTONOCR_FULLPAGE_LEGACY` for one release).

### 8.6 Tests

- Router unit tests with fake engines: correct chain per class; fallthrough on hard fail; secondary only on field regions.
- Contract test per engine adapter using recorded responses (no network in CI).
- E2E on bench smoke set with local engines only.

**Exit criteria:** field exact-match on bench v2 ≥ baseline + agreed uplift (set after Phase 2; suggest ≥ +10 pp on handwritten/Indic fields, no regression on digital); p95 latency per page within budget.

---

## 9. Phase 6 — Field consensus, validators, decision policy

Today fields come from `KeyValueExtractor` (spatial KV) and `llm_extract_fields` (LLM over raw text). Consensus sits **after** field extraction and **before** Node 3 checks.

### 9.1 Field candidates (`consensus/field_candidates.py`)

For each extracted field value, collect every reading that overlaps its source bbox:

```python
class FieldCandidate(BaseModel):
    field: str
    value: str
    normalized: str
    engine: str
    confidence: float
    bbox: list[float]
    page: int

class FieldEvidence(BaseModel):
    field: str
    candidates: list[FieldCandidate]   # primary, secondary, VLM, legacy OCR layer, LLM-extracted
    chosen: FieldCandidate | None
    agreement: float
    validator: ValidationOutcome
    los_signal: Literal["match", "mismatch", "absent"]
    decision: Literal["ACCEPT", "VLM", "HUMAN_REVIEW"]
    reasons: list[str]
```

The LLM extractor output is itself a candidate: if the LLM "normalized" a value that no OCR engine produced, that is flagged (`llm_unsupported_value`) — this catches hallucinated fields.

### 9.2 Agreement (`consensus/agreement.py`)

- Normalize per field type (uppercase, strip spaces/punct for IDs; parse dates to ISO; parse amounts to integers).
- Confusable-aware comparison using existing `extraction/confusable_chars.py` (`O/0`, `I/1`, `S/5`, `B/8`) **only in positions where the validator's grammar fixes the type** (e.g. PAN chars 6–9 must be digits). Never apply confusable substitution freely.
- `agreement = 1 − normalized_levenshtein(a, b)`; exact after normalization = 1.0.

### 9.3 Validators (`consensus/validators.py`)

Extend (don't duplicate) `extraction/field_validator.py`; put pure functions here and call them from `FieldValidator`.

| Field | Rule |
|---|---|
| PAN | `^[A-Z]{3}[PCHFATBLJG][A-Z][0-9]{4}[A-Z]$`; for individuals (`P`), 5th char should equal first letter of surname → soft check |
| Aadhaar (full) | 12 digits, first digit not 0/1, **Verhoeff checksum** |
| Aadhaar (masked) | `^[Xx*]{4}\s?[Xx*]{4}\s?\d{4}$`; last 4 compared with other docs/LOS |
| IFSC | `^[A-Z]{4}0[A-Z0-9]{6}$` |
| Pincode | `^[1-9][0-9]{5}$`; optional: first digit consistent with State |
| Mobile | `^[6-9][0-9]{9}$` after stripping `+91`/`0` |
| Date | parseable, plausible range (DOB: age 18–80 at application date; doc dates ≤ today) |
| Amount | numeric parses; if "amount in words" present on page, words→number must equal figure |
| Email | RFC-lite regex |
| Account number | 9–18 digits |

Verhoeff (verified implementation):

```python
_D = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],
      [4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],
      [8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]]
_P = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],
      [9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]]

def verhoeff_ok(num: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(num)):
        c = _D[c][_P[i % 8][int(ch)]]
    return c == 0
```

Validators return `VALID | INVALID | NOT_APPLICABLE`, plus `repairable_candidates` — e.g. if a PAN fails only because char 6 is `O`, and `0` makes it valid **and** another engine read `0`, that repaired value is a candidate (never auto-applied without a second engine's support).

### 9.4 LOS signal

Compare to LOS (`pipeline/engines/comparison.py` helpers) and record `match / mismatch / absent`. **LOS never changes the chosen value.** It only affects the decision:
- LOS match can lift a borderline field to ACCEPT only if at least one engine read that exact value.
- LOS mismatch with strong OCR agreement + valid format → ACCEPT the OCR value; the mismatch is a finding for Node 3, not an OCR error.

### 9.5 Decision policy (`consensus/decision.py`)

Evaluated top to bottom:

| # | Condition | Decision |
|---|---|---|
| 1 | Validator `INVALID` for all candidates | VLM (field-aware prompt) → if still invalid, HUMAN_REVIEW |
| 2 | Primary & secondary agree (≥ 0.99 after normalization) and validator `VALID`/`N/A` | ACCEPT |
| 3 | Single engine only, validator `VALID`, engine conf ≥ `τ_single(field)` | ACCEPT |
| 4 | Disagreement, exactly one candidate `VALID` | VLM tie-break; ACCEPT if VLM matches the valid candidate |
| 5 | Disagreement, both valid (e.g. two plausible pincodes) | HUMAN_REVIEW |
| 6 | `llm_unsupported_value` | HUMAN_REVIEW |
| 7 | Critical field (PAN, Aadhaar, account no., loan amount, name) with any unresolved conflict | HUMAN_REVIEW |
| 8 | Otherwise | VLM → re-evaluate once → HUMAN_REVIEW |

`τ_single` per field is calibrated on bench v2 so that ACCEPT precision ≥ 99.5 %. Config: `CONSENSUS_THRESHOLDS: dict[str, float]`, `CRITICAL_FIELDS: set[str]`.

### 9.6 Integration

- `pipeline/nodes/idp_scan.py` / `llm_structure.py`: attach `FieldEvidence` per field under `_field_evidence`; set existing `needs_review` flag from `decision == HUMAN_REVIEW` so the current review queue (`app/routers/reviews.py`, `HumanReviewPage.tsx`) picks it up unchanged.
- Review UI: show candidates, engines, crop image, and reasons (frontend change: extend `CheckpointDrawer.tsx` / review page to render `_field_evidence`).
- Reviewer's final value is stored as a label → feeds Phase 7 training export.

### 9.7 Tests

- Validators: table-driven tests including known-valid/invalid Verhoeff numbers (generate test numbers; never use real Aadhaar numbers in fixtures).
- Decision table: one test per rule row.
- Property test: LOS value is never written into `chosen` unless an engine produced it.

**Exit criteria:** ACCEPT precision ≥ 99.5 % on bench v2; human-review rate reported per doc type and agreed with ops.

---

## 10. Phase 7 — Telemetry, provenance, active learning

### 10.1 Per-element / per-field provenance

Every `OCRElement` and `FieldEvidence` carries `engine`, `engine_version`, `route`, `region_class`, `page_kind`, `latency_ms`. Stored in the parsed-document JSON (already uploaded to `PARSED_DOCUMENT_PREFIX`).

### 10.2 Events (`telemetry/ocr_events.py`)

Structured log events (JSON, PII-masked via `utils/masking.py`):

- `ocr.page_classified` — kind, signals
- `ocr.region_routed` — class, engine chain
- `ocr.engine_result` — engine, conf, latency, hard_fail_reason
- `ocr.field_decision` — field, decision, agreement, validator, los_signal, engines_disagreeing
- `ocr.review_resolved` — field, which engine (if any) was right

Extend `ProcessingMetrics` with counters: pages by kind, regions by class, engine calls/failures, consensus decisions, cloud cost estimate.

### 10.3 Dashboards

Reuse `dashboard_reports.py` endpoints; add:
- Disagreement rate by (doc type × field × engine pair) — the key "where does each engine fail" view.
- Review rate and review-resolved-by-engine (which engine the human agreed with).
- Page-kind and region-class distribution drift week over week.

### 10.4 Active-learning export (`telemetry/crop_export.py`)

- On `HUMAN_REVIEW` resolution and on engine disagreement, save the region crop + all readings + human label to an **encrypted, access-controlled** S3 prefix with retention policy. Aadhaar crops: store masked unless compliance approves otherwise.
- Monthly job builds training sets for: region classifier (printed/handwritten/script), optional PP-OCR fine-tune on your fonts, and threshold recalibration.
- Retrain → re-run bench v2 → promote model version only if metrics improve.

**Exit criteria:** dashboard live; first monthly export produced.

---

## 11. Phase 8 — Rollout

1. **Shadow mode** (1–2 weeks): new pipeline runs in parallel via Celery on a copy of each document; production still uses old output. Compare field-level outputs and review decisions; investigate every disagreement class with > 1 % frequency.
2. **Canary:** enable per doc type in increasing risk order — KFS/sanction letter (printed, Latin) → bank statements → application form → PAN → Aadhaar.
3. **Default-on** after two weeks of canary at target metrics. Keep legacy flags for one release, then delete the full-page LightOnOCR path.

Rollback = flip flags; no data migration needed because new fields are additive.

---

## 12. Configuration summary (`idp/core/config.py`)

```python
# Phase 3
PAGE_ROUTING_ENABLED: bool = False
PAGE_DIGITAL_MIN_VISIBLE_CHARS: int = 200
PAGE_SCANNED_MIN_IMAGE_COVERAGE: float = 0.6
PAGE_TEXT_LAYER_MAX_GARBLE: float = 0.2

# Phase 4
REGION_ROUTING_ENABLED: bool = False
REGION_CLASSIFIER_BACKEND: str = "crop_cnn"   # crop_cnn | textract | azure_di | heuristic
REGION_CLASSIFIER_MODEL_PATH: str = "models/region_classifier/v1/model.onnx"

# Phase 5
OCR_ENGINE_PRINTED_LATIN: str = "ppocr"
OCR_ENGINE_PRINTED_INDIC: str = "ppocr_devanagari"   # switch after bench
OCR_ENGINE_HANDWRITTEN: str = "lightonocr_crop"
OCR_SECONDARY_ON_FIELDS_ONLY: bool = True
OCR_CLOUD_MAX_CONCURRENCY: int = 4
AZURE_DI_ENDPOINT: str | None = None
AZURE_DI_KEY: str | None = None
GDOCAI_PROCESSOR: str | None = None
LIGHTONOCR_FULLPAGE_LEGACY: bool = False

# Phase 6
FIELD_CONSENSUS_ENABLED: bool = False
CONSENSUS_AGREEMENT_MIN: float = 0.99
CONSENSUS_THRESHOLDS: dict = {}          # per-field τ_single, from calibration
CRITICAL_FIELDS: set = {"PAN", "Aadhaar Card No", "Account Number", "Loan Amount", "Applicant Name"}

# Phase 7
OCR_TELEMETRY_ENABLED: bool = True
CROP_EXPORT_ENABLED: bool = False
CROP_EXPORT_PREFIX: str = "ocr-training/"
```

Also fix `AWS_REGION` default for any Textract/S3 use in India (`ap-south-1`) via `.env`.

---

## 13. Dependencies

- Already present: `docling`, `rapidocr-onnxruntime`, `onnxruntime-gpu`, `PyMuPDF`, `opencv-python-headless`, `transformers`, `torch`, `boto3`.
- New (optional, per engine chosen): `azure-ai-documentintelligence`, `google-cloud-documentai`.
- Training only (separate `requirements-train.txt`): `timm`, `albumentations`, `onnx`.
- Fetch Devanagari recognition model files for RapidOCR and pin their checksums.

---

## 14. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Latency grows from multi-engine reads | Secondary only on field regions; batch per page; cache by page hash (Redis lock infra exists) |
| Cloud cost | Cloud only for PRINTED_INDIC/MIXED regions; per-page single call; cost counter in metrics |
| Compliance (Aadhaar/PAN leaving VPC) | Engine `data_residency` attribute; policy check refuses non-approved engines for sensitive doc types; self-hosted Devanagari baseline always available |
| Region classifier misses handwriting | Optimize for handwritten recall; low-margin predictions route to handwritten chain (costlier, safer) |
| Page-number remapping bugs in split-PDF processing | Dedicated test with 10-page mixed PDF; assert every element's page number |
| Consensus over-routes to review | Calibrate `τ_single` per field; monitor review rate; ops agreement on target |
| LOS bias corrupting extraction | Property test: chosen value always originates from an engine reading |
| Breaking existing consumers | Additive schema, widened `source`, flags-off byte-identical regression test |

---

## 15. Definition of done

- [ ] Phase 1 shipped; no truncated/looped LightOnOCR output accepted.
- [ ] Bench v2 with ≥ 125 labeled pages across 7 doc types; baseline and per-engine reports committed.
- [ ] Per-page routing live; page-kind accuracy ≥ 99 %.
- [ ] Region classifier: accuracy ≥ 98 %, handwritten recall ≥ 99 %.
- [ ] Engine choice for PRINTED_INDIC decided from bench data and approved by compliance.
- [ ] Field consensus: ACCEPT precision ≥ 99.5 % on bench v2.
- [ ] Every field in output has provenance and a decision reason.
- [ ] Dashboard shows disagreement by doc type × field × engine.
- [ ] Shadow → canary → default-on completed; legacy full-page LightOnOCR path removed.