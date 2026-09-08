"""Field Location Resolver — Maps extracted field values back to physical OCR tokens and bounding boxes."""
import difflib
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from idp.models.extraction import CandidateMatch, FieldLocation, OCRTokenDebug

logger = logging.getLogger("disbursement_pipeline.field_location_resolver")


def _clean_text(s: Any) -> str:
    """Standardizes string by stripping and collapsing whitespace."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def _clean_alphanumeric(s: Any) -> str:
    """Extracts only alphanumeric lowercase characters."""
    return re.sub(r"[^a-zA-Z0-9]", "", _clean_text(s)).lower()


def _clean_numeric(s: Any) -> str:
    """Extracts numeric digits and decimal point only."""
    text = _clean_text(s)
    # Remove currency symbols and formatting commas
    cleaned = re.sub(r"[₹$€£,]", "", text)
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if match:
        val = match.group(1)
        # Drop trailing .0 or .00 for integer values
        if val.endswith(".0"):
            val = val[:-2]
        elif val.endswith(".00"):
            val = val[:-3]
        return val
    return ""


def _normalize_date(s: Any) -> str:
    """Extracts numeric date representation (DDMMYYYY or YYYYMMDD)."""
    text = _clean_text(s)
    return re.sub(r"[\/\-\.\s]", "", text)


def _ensure_normalized_bbox(
    bbox: List[float],
    page_w: float = 0.0,
    page_h: float = 0.0
) -> Tuple[List[float], List[float]]:
    """
    Returns (normalized_bbox [0..1], pixel_bbox).
    Handles both already-normalized and pixel/point coordinates.
    """
    if not bbox or len(bbox) < 4:
        return [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]

    x1, y1, x2, y2 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    # Ensure correct min/max ordering
    l = min(x1, x2)
    t = min(y1, y2)
    r = max(x1, x2)
    b = max(y1, y2)

    # Check if already normalized (coordinates <= 1.05)
    if r <= 1.05 and b <= 1.05:
        norm = [max(0.0, min(1.0, l)), max(0.0, min(1.0, t)), max(0.0, min(1.0, r)), max(0.0, min(1.0, b))]
        pw = page_w if page_w > 0 else 1000.0
        ph = page_h if page_h > 0 else 1000.0
        pix = [round(norm[0] * pw, 1), round(norm[1] * ph, 1), round(norm[2] * pw, 1), round(norm[3] * ph, 1)]
        return norm, pix

    # Pixel/point coordinates: normalize using page dimensions
    pw = page_w if page_w > 0 else max(r, 595.0)
    ph = page_h if page_h > 0 else max(b, 842.0)

    norm_l = max(0.0, min(1.0, l / pw))
    norm_t = max(0.0, min(1.0, t / ph))
    norm_r = max(0.0, min(1.0, r / pw))
    norm_b = max(0.0, min(1.0, b / ph))

    return [round(norm_l, 4), round(norm_t, 4), round(norm_r, 4), round(norm_b, 4)], [round(l, 1), round(t, 1), round(r, 1), round(b, 1)]


def _merge_bboxes(boxes: List[List[float]]) -> List[float]:
    """Combines a list of [x1, y1, x2, y2] into a single bounding box."""
    if not boxes:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


class FieldLocationResolver:
    """
    Production-grade spatial mapping layer that resolves extracted fields
    to physical OCR tokens and bounding boxes without LLM coordinate hallucination.
    """

    def __init__(self, fuzzy_threshold: float = 0.82):
        self.fuzzy_threshold = fuzzy_threshold

    def resolve_field_locations(
        self,
        extracted_fields: Dict[str, Any],
        ocr_elements: List[Dict[str, Any]],
        table_cells: Optional[List[Dict[str, Any]]] = None,
        page_dimensions: Optional[List[Dict[str, float]]] = None,
        debug_mode: bool = True
    ) -> Dict[str, FieldLocation]:
        """
        Resolves bounding box locations for all extracted fields.

        Args:
            extracted_fields: Dict mapping field names to extracted values.
            ocr_elements: List of raw OCR/Layout elements with text, bbox, page_number, confidence.
            table_cells: Optional list of table cells with text, bbox, page_number.
            page_dimensions: Optional list of dicts with width/height per page.
            debug_mode: Whether to record candidate evaluations for debugging.

        Returns:
            Dict mapping field_name to FieldLocation.
        """
        page_dims_map: Dict[int, Tuple[float, float]] = {}
        if page_dimensions:
            for idx, dims in enumerate(page_dimensions):
                page_dims_map[idx + 1] = (float(dims.get("width", 0)), float(dims.get("height", 0)))

        # Pre-process all tokens with normalized coordinates
        tokens_by_page: Dict[int, List[Dict[str, Any]]] = {}
        for elem in (ocr_elements or []):
            txt = _clean_text(elem.get("text", ""))
            if not txt:
                continue
            pno = int(elem.get("page_number", 1))
            raw_bbox = elem.get("bbox", [])
            pw, ph = page_dims_map.get(pno, (0.0, 0.0))
            norm_bbox, pix_bbox = _ensure_normalized_bbox(raw_bbox, pw, ph)

            token_item = {
                "id": elem.get("id") or f"tok-{len(tokens_by_page.get(pno, []))}",
                "text": txt,
                "clean_text": txt.lower(),
                "clean_alnum": _clean_alphanumeric(txt),
                "clean_num": _clean_numeric(txt),
                "bbox": norm_bbox,
                "bbox_pixels": pix_bbox,
                "page": pno,
                "confidence": float(elem.get("confidence", 1.0)),
                "source": elem.get("source") or "docling_ocr",
            }
            tokens_by_page.setdefault(pno, []).append(token_item)

        # Include table cells as valid searchable text units
        for cell in (table_cells or []):
            txt = _clean_text(cell.get("text", ""))
            if not txt:
                continue
            pno = int(cell.get("page_number", 1))
            raw_bbox = cell.get("bbox", [])
            pw, ph = page_dims_map.get(pno, (0.0, 0.0))
            norm_bbox, pix_bbox = _ensure_normalized_bbox(raw_bbox, pw, ph)

            token_item = {
                "id": cell.get("id") or f"tbl-cell-{len(tokens_by_page.get(pno, []))}",
                "text": txt,
                "clean_text": txt.lower(),
                "clean_alnum": _clean_alphanumeric(txt),
                "clean_num": _clean_numeric(txt),
                "bbox": norm_bbox,
                "bbox_pixels": pix_bbox,
                "page": pno,
                "confidence": float(cell.get("confidence", 1.0)),
                "source": "table_cell",
            }
            tokens_by_page.setdefault(pno, []).append(token_item)

        # Sort tokens per page by top-to-bottom, left-to-right reading order
        for pno in tokens_by_page:
            tokens_by_page[pno].sort(key=lambda t: (t["bbox"][1], t["bbox"][0]))

        results: Dict[str, FieldLocation] = {}

        for field_name, value in extracted_fields.items():
            if field_name.startswith("_") or value is None:
                continue
            if isinstance(value, bool):
                # Presence/boolean flags don't have a single literal bounding box
                continue

            val_str = _clean_text(value)
            if not val_str:
                continue

            location = self._resolve_single_field(
                field_name=field_name,
                value=value,
                val_str=val_str,
                tokens_by_page=tokens_by_page,
                debug_mode=debug_mode
            )
            results[field_name] = location

            if location.location_status == "resolved":
                logger.debug(
                    "FIELD_LOCATION_RESOLVED field=%s value=%s page=%d bbox=%s conf=%.2f match_conf=%.2f",
                    field_name,
                    val_str,
                    location.page,
                    location.bbox,
                    location.confidence,
                    location.match_confidence,
                )
            else:
                logger.debug(
                    "FIELD_LOCATION_UNRESOLVED field=%s value=%s reason=%s",
                    field_name,
                    val_str,
                    location.reason,
                )

        return results

    def _resolve_single_field(
        self,
        field_name: str,
        value: Any,
        val_str: str,
        tokens_by_page: Dict[int, List[Dict[str, Any]]],
        debug_mode: bool
    ) -> FieldLocation:
        """Finds best matching OCR token sequence for a single field value."""
        target_lower = val_str.lower()
        target_alnum = _clean_alphanumeric(val_str)
        target_num = _clean_numeric(val_str)
        target_date = _normalize_date(val_str) if any(c in val_str for c in "/-.") else ""

        candidates: List[CandidateMatch] = []

        # NEW: Priority matching on comb-box merged tokens
        for pno, tokens in tokens_by_page.items():
            for tok in tokens:
                # Check if this is a comb-box reconstructed token
                if tok.get("source") == "comb_box_merged":
                    t_lower = _clean_text(tok["text"]).lower()
                    t_alnum = _clean_alphanumeric(tok["text"])
                    t_num = _clean_numeric(tok["text"])
                    
                    # Exact match on merged token (high confidence)
                    if target_lower == t_lower:
                        candidates.append(CandidateMatch(
                            text=tok["text"],
                            page=pno,
                            bbox=tok["bbox"],
                            score=0.96,  # Very high score for comb-box exact match
                            exactness=0.96,
                            ocr_confidence=tok.get("confidence", 0.9),
                            match_strategy="comb_box_exact",
                            source=tok.get("source", "comb_box_merged")
                        ))
                    
                    # Alphanumeric match on merged token
                    elif target_alnum and target_alnum == t_alnum:
                        candidates.append(CandidateMatch(
                            text=tok["text"],
                            page=pno,
                            bbox=tok["bbox"],
                            score=0.94,
                            exactness=0.94,
                            ocr_confidence=tok.get("confidence", 0.9),
                            match_strategy="comb_box_alphanumeric",
                            source=tok.get("source", "comb_box_merged")
                        ))
                    
                    # Numeric match on merged token
                    elif target_num and t_num and target_num == t_num:
                        candidates.append(CandidateMatch(
                            text=tok["text"],
                            page=pno,
                            bbox=tok["bbox"],
                            score=0.92,
                            exactness=0.92,
                            ocr_confidence=tok.get("confidence", 0.9),
                            match_strategy="comb_box_numeric",
                            source=tok.get("source", "comb_box_merged")
                        ))

        # Existing matching strategies (lower priority)
        for pno, tokens in tokens_by_page.items():
            # 1. Single-token exact / normalized search
            for tok in tokens:
                t_lower = tok["clean_text"]
                t_alnum = tok["clean_alnum"]
                t_num = tok["clean_num"]

                # Exact match
                if target_lower == t_lower:
                    candidates.append(CandidateMatch(
                        text=tok["text"],
                        page=pno,
                        bbox=tok["bbox"],
                        score=1.0,
                        exactness=1.0,
                        ocr_confidence=tok["confidence"],
                        match_strategy="exact_match",
                        source=tok.get("source", "docling"),
                        constituent_tokens=[tok["text"]]
                    ))
                    continue

                # Alphanumeric match (ignoring whitespace and punctuation)
                if target_alnum and target_alnum == t_alnum:
                    candidates.append(CandidateMatch(
                        text=tok["text"],
                        page=pno,
                        bbox=tok["bbox"],
                        score=0.98,
                        exactness=0.98,
                        ocr_confidence=tok["confidence"],
                        match_strategy="alphanumeric_match",
                        source=tok.get("source", "docling"),
                        constituent_tokens=[tok["text"]]
                    ))
                    continue

                # Numeric match (e.g. 94111 vs 94,111 or 94,111.00)
                if target_num and t_num and target_num == t_num:
                    candidates.append(CandidateMatch(
                        text=tok["text"],
                        page=pno,
                        bbox=tok["bbox"],
                        score=0.95,
                        exactness=0.95,
                        ocr_confidence=tok["confidence"],
                        match_strategy="numeric_match",
                        source=tok.get("source", "docling"),
                        constituent_tokens=[tok["text"]]
                    ))
                    continue

                # Date match (e.g. 13/07/1991 vs 13-07-1991)
                if target_date and _normalize_date(tok["text"]) == target_date:
                    candidates.append(CandidateMatch(
                        text=tok["text"],
                        page=pno,
                        bbox=tok["bbox"],
                        score=0.95,
                        exactness=0.95,
                        ocr_confidence=tok["confidence"],
                        match_strategy="date_match",
                        source=tok.get("source", "docling"),
                        constituent_tokens=[tok["text"]]
                    ))
                    continue

                # Substring match within single token (e.g. key-value line: "PAN: ABCDE1234F")
                if target_alnum and len(target_alnum) >= 5 and target_alnum in t_alnum:
                    ratio = len(target_alnum) / max(len(t_alnum), 1)
                    score = 0.85 + (0.10 * ratio)
                    candidates.append(CandidateMatch(
                        text=tok["text"],
                        page=pno,
                        bbox=tok["bbox"],
                        score=score,
                        exactness=round(score, 2),
                        ocr_confidence=tok["confidence"],
                        match_strategy="token_containment",
                        source=tok.get("source", "docling"),
                        constituent_tokens=[tok["text"]]
                    ))

            # 2. Multi-token sliding window search (sequences of 2 to 7 tokens)
            num_tokens = len(tokens)
            for window_size in range(2, min(8, num_tokens + 1)):
                for i in range(num_tokens - window_size + 1):
                    window = tokens[i : i + window_size]

                    # Check geometric continuity: tokens must be on roughly the same line / vertical band
                    y_diffs = [abs(window[w]["bbox"][1] - window[0]["bbox"][1]) for w in range(1, window_size)]
                    if any(dy > 0.035 for dy in y_diffs):
                        # Not on the same line, check if adjacent lines for address / multi-line name
                        line_gap = max(window[w]["bbox"][1] - window[w - 1]["bbox"][3] for w in range(1, window_size))
                        if line_gap > 0.06:
                            continue

                    joined_text = " ".join(t["text"] for t in window)
                    joined_lower = joined_text.lower()
                    joined_alnum = _clean_alphanumeric(joined_text)
                    joined_num = _clean_numeric(joined_text)

                    merged_box = _merge_bboxes([t["bbox"] for t in window])
                    avg_conf = sum(t["confidence"] for t in window) / window_size

                    # Exact multi-token match
                    if target_lower == joined_lower:
                        candidates.append(CandidateMatch(
                            text=joined_text,
                            page=pno,
                            bbox=merged_box,
                            score=0.97,
                            exactness=0.97,
                            ocr_confidence=avg_conf,
                            match_strategy="multi_token_exact",
                            constituent_tokens=[t["text"] for t in window]
                        ))
                        continue

                    # Alphanumeric multi-token match
                    if target_alnum and target_alnum == joined_alnum:
                        candidates.append(CandidateMatch(
                            text=joined_text,
                            page=pno,
                            bbox=merged_box,
                            score=0.95,
                            exactness=0.95,
                            ocr_confidence=avg_conf,
                            match_strategy="multi_token_alnum",
                            constituent_tokens=[t["text"] for t in window]
                        ))
                        continue

                    # Numeric multi-token match (e.g. "Rs." + "5,00,000")
                    if target_num and joined_num and target_num == joined_num:
                        candidates.append(CandidateMatch(
                            text=joined_text,
                            page=pno,
                            bbox=merged_box,
                            score=0.93,
                            exactness=0.93,
                            ocr_confidence=avg_conf,
                            match_strategy="multi_token_numeric",
                            constituent_tokens=[t["text"] for t in window]
                        ))
                        continue

                    # Multi-token containment (e.g. full address matching address phrase)
                    if target_alnum and len(target_alnum) >= 8 and (target_alnum in joined_alnum or joined_alnum in target_alnum):
                        overlap = min(len(target_alnum), len(joined_alnum)) / max(len(target_alnum), len(joined_alnum))
                        if overlap >= 0.70:
                            score = 0.82 + (0.12 * overlap)
                            candidates.append(CandidateMatch(
                                text=joined_text,
                                page=pno,
                                bbox=merged_box,
                                score=round(score, 3),
                                exactness=round(score, 3),
                                ocr_confidence=avg_conf,
                                match_strategy="multi_token_containment",
                                constituent_tokens=[t["text"] for t in window]
                            ))

            # 3. Fuzzy fallback search on single tokens and small windows (only if string has >= 4 chars)
            if len(target_lower) >= 4:
                for tok in tokens:
                    ratio = difflib.SequenceMatcher(None, target_lower, tok["clean_text"]).ratio()
                    if ratio >= self.fuzzy_threshold:
                        candidates.append(CandidateMatch(
                            text=tok["text"],
                            page=pno,
                            bbox=tok["bbox"],
                            score=round(ratio * 0.90, 3),
                            exactness=round(ratio, 3),
                            ocr_confidence=tok["confidence"],
                            match_strategy="fuzzy_match",
                            constituent_tokens=[tok["text"]]
                        ))

        # 4. Key-Anchor Proximity Fallback Search (if direct text matching finds no candidates)
        if not candidates:
            field_words = field_name.replace("_", " ").lower().split()
            for pno, tokens in tokens_by_page.items():
                for tok_idx, tok in enumerate(tokens):
                    tok_txt = tok["clean_text"]
                    if any(fw in tok_txt for fw in field_words if len(fw) >= 3):
                        key_box = tok["bbox"]
                        # Search for adjacent right or below token on same page
                        best_neighbor = None
                        min_dist = 999.0
                        for neighbor in tokens:
                            if neighbor["id"] == tok["id"]:
                                continue
                            n_box = neighbor["bbox"]
                            # Right neighbor (same row y-band) or bottom neighbor (vertical alignment)
                            is_right = (n_box[0] >= key_box[0]) and abs(n_box[1] - key_box[1]) <= 0.04
                            is_below = (n_box[1] >= key_box[3]) and (n_box[1] - key_box[3]) <= 0.08 and abs(n_box[0] - key_box[0]) <= 0.20
                            if is_right or is_below:
                                dist = ((n_box[0] - key_box[2]) ** 2 + (n_box[1] - key_box[1]) ** 2) ** 0.5
                                if dist < min_dist:
                                    min_dist = dist
                                    best_neighbor = neighbor

                        if best_neighbor:
                            candidates.append(CandidateMatch(
                                text=best_neighbor["text"],
                                page=pno,
                                bbox=best_neighbor["bbox"],
                                score=0.82,
                                exactness=0.82,
                                ocr_confidence=best_neighbor["confidence"],
                                match_strategy="key_anchor_proximity",
                                constituent_tokens=[best_neighbor["text"]]
                            ))

        if not candidates:
            return FieldLocation(
                field_name=field_name,
                value=value,
                location_status="unresolved",
                reason="No sufficiently confident OCR match found",
                candidates=[]
            )

        # Candidate Scoring & Selection:
        # Prioritize exactness, OCR confidence, same-line consistency, and Page 1 preference for headers
        def _rank_candidate(c: CandidateMatch) -> float:
            score = c.score * 0.7 + c.ocr_confidence * 0.2
            # Slight page bonus if on page 1
            if c.page == 1:
                score += 0.05
            # Penalty for fuzzy matches
            if c.match_strategy == "fuzzy_match":
                score -= 0.08
            return score

        candidates.sort(key=_rank_candidate, reverse=True)
        best = candidates[0]

        return FieldLocation(
            field_name=field_name,
            value=value,
            page=best.page,
            bbox=best.bbox,
            matched_text=best.text,
            confidence=round(best.ocr_confidence, 3),
            match_confidence=round(best.score, 3),
            location_status="resolved",
            source=getattr(best, "source", "docling_ocr") or "docling_ocr",
            match_strategy=best.match_strategy,
            candidates=candidates[:5] if debug_mode else []
        )

    def extract_debug_tokens(
        self,
        ocr_elements: List[Dict[str, Any]],
        page_dimensions: Optional[List[Dict[str, float]]] = None
    ) -> List[OCRTokenDebug]:
        """Converts raw OCR elements into standardized OCRTokenDebug items for the debug view."""
        page_dims_map: Dict[int, Tuple[float, float]] = {}
        if page_dimensions:
            for idx, dims in enumerate(page_dimensions):
                page_dims_map[idx + 1] = (float(dims.get("width", 0)), float(dims.get("height", 0)))

        tokens: List[OCRTokenDebug] = []
        for idx, elem in enumerate(ocr_elements or []):
            txt = _clean_text(elem.get("text", ""))
            if not txt:
                continue
            pno = int(elem.get("page_number", 1))
            raw_bbox = elem.get("bbox", [])
            pw, ph = page_dims_map.get(pno, (0.0, 0.0))
            norm_bbox, pix_bbox = _ensure_normalized_bbox(raw_bbox, pw, ph)

            tokens.append(OCRTokenDebug(
                id=elem.get("id") or f"ocr-tok-{pno}-{idx + 1}",
                text=txt,
                page=pno,
                bbox=norm_bbox,
                bbox_pixels=pix_bbox,
                confidence=round(float(elem.get("confidence", 1.0)), 3),
                source=elem.get("source", "ocr"),
                line_number=elem.get("line_number")
            ))
        return tokens

    # Aliases for convenience
    resolve_fields = resolve_field_locations
    get_debug_tokens = extract_debug_tokens
