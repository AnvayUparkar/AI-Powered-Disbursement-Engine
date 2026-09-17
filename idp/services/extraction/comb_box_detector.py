"""
Production-grade comb-box field detection for segmented character sequences.

Detects and reconstructs fields where each character appears in a separate box,
common in Indian government forms (Aadhaar, Application Forms, PAN cards).

Architecture:
1. Cluster elements by horizontal row (y-coordinate alignment)
2. Detect uniform spacing/sizing patterns (comb-box signature)
3. Merge sequences into single tokens with metadata
4. Preserve original elements for VLM context
"""

import uuid
import statistics
from typing import List, Dict, Tuple, Optional,Any
from idp.models.layout import LayoutElement
from idp.models.merged_token import MergedToken
from idp.core.logging import logger
from idp.services.diagnostics.comb_box_audit import get_global_auditor


class CombBoxDetector:
    """
    Spatial clustering engine for detecting and reconstructing comb-box fields.
    
    Detects sequences of single characters with uniform spacing/sizing and
    merges them into cohesive tokens for field extraction.
    """

    # Shared candidate definition — MUST match what upstream ingestion
    # (idp/services/output/serializer.py) uses to decide which elements are
    # allowed to bypass noise/table filtering before this detector ever sees
    # them. Multi-character OCR runs up to 4 characters (e.g. 'AR', 'AJA', 'ATAK')
    # are accepted as comb-box candidates, while standard prose words (>= 5 chars)
    # are excluded.
    DEFAULT_MAX_CHAR_LENGTH = 4

    _ALLOWED_SYMBOLS = frozenset("@.-/,_#")

    @classmethod
    def is_candidate_text(cls, text: Optional[str], max_char_length: int = DEFAULT_MAX_CHAR_LENGTH) -> bool:
        """
        True if `text` is short/simple enough to plausibly be one cell of a
        comb-box field (a single handwritten or printed character/short code,
        including common symbols like @, ., -, /, ,, #).
        """
        if not text:
            return False
        stripped = text.strip()
        if not stripped or len(stripped) > max_char_length:
            return False
        return all(c.isalnum() or c in cls._ALLOWED_SYMBOLS for c in stripped)

    def __init__(self,
                 y_tolerance: float = 0.065,
                 spacing_uniformity_threshold: float = 0.45,
                 size_uniformity_threshold: float = 0.50,
                 min_sequence_length: int = 2,
                 max_char_length: int = DEFAULT_MAX_CHAR_LENGTH):
        """
        Initialize comb-box detector with configuration.

        NOTE on thresholds: `size_uniformity_threshold` and
        `spacing_uniformity_threshold` were widened from their original
        0.25 / 0.30 values. The originals were only ever validated against
        synthetic fixtures where every character shared an identical 10px
        bbox (tests/test_comb_box_detector.py) — real OCR on handwritten
        capital letters has genuine per-glyph width variance (e.g. "I" vs
        "M"/"W" inside the same printed cell pitch), which pushed the
        width coefficient-of-variation past 0.25 and silently dropped real
        comb-box sequences (0 merged tokens, 0 bounding box) even when the
        printed cell grid itself was perfectly uniform. 0.40 / 0.35 were
        chosen to tolerate that ink-width variance while still rejecting
        clearly non-uniform layouts (see test_reject_non_uniform_spacing).
        """
        self.y_tolerance = y_tolerance
        self.spacing_uniformity_threshold = spacing_uniformity_threshold
        self.size_uniformity_threshold = size_uniformity_threshold
        self.min_sequence_length = min_sequence_length
        self.max_char_length = max_char_length
    
    def detect_and_merge_comb_boxes(
        self,
        elements: List[LayoutElement],
        page_number: int,
        doc_id: str = "DOC"
    ) -> List[MergedToken]:
        """
        Main entry point: detect and reconstruct comb-box sequences.
        
        Args:
            elements: Layout elements from parser
            page_number: Page number being processed
            doc_id: Document ID for logging
            
        Returns:
            List of merged tokens representing reconstructed comb-box fields
        """
        if not elements:
            return []
        
        # Filter to small text elements (potential comb-box chars)
        candidate_elements = [
            elem for elem in elements
            if elem.page_number == page_number
            and self.is_candidate_text(elem.text, self.max_char_length)
        ]
        
        if len(candidate_elements) < self.min_sequence_length:
            return []
        
        # Cluster by horizontal row
        rows = self._cluster_by_row(candidate_elements)
        
        # Detect and merge comb-box sequences in each row
        merged_tokens = []
        for row_idx, row_elements in enumerate(rows):
            if len(row_elements) < self.min_sequence_length:
                continue
            
            # Sort left-to-right
            row_elements.sort(key=lambda e: e.bbox[0])
            
            # Find comb-box sequences
            sequences = self._find_comb_box_sequences(row_elements)
            
            # Merge each sequence
            for seq in sequences:
                merged = self._merge_sequence(seq, page_number, doc_id)
                if merged:
                    merged_tokens.append(merged)

        logger.info(
            f"[{doc_id}] Page {page_number}: Detected {len(merged_tokens)} comb-box sequences "
            f"from {len(candidate_elements)} candidate elements"
        )

        # Observability: this is the exact signal that would have caught the
        # "candidates exist but nothing merges" failure mode in production
        # (e.g. uniformity thresholds rejecting real handwriting variance,
        # or a row split by y_tolerance) before a human ever spotted a
        # missing bounding box.
        if merged_tokens:
            auditor = get_global_auditor()
            for mt in merged_tokens:
                auditor.log_reconstruction(
                    document_id=doc_id,
                    page_number=page_number,
                    merged_text=mt.text,
                    constituent_tokens=mt.metadata.get("constituent_texts", []),
                    uniformity_score=mt.uniformity_score,
                    detection_confidence=mt.confidence,
                    bbox=mt.bbox,
                )
        elif len(candidate_elements) >= self.min_sequence_length:
            logger.warning(
                f"[{doc_id}] Page {page_number}: {len(candidate_elements)} comb-box candidate "
                f"characters found but 0 sequences merged — check row clustering (y_tolerance) "
                f"and uniformity thresholds against this page's actual OCR geometry."
            )

        return merged_tokens
    
    def _cluster_by_row(
        self,
        elements: List[LayoutElement]
    ) -> List[List[LayoutElement]]:
        """
        Group elements into horizontal rows based on vertical overlap and central baseline.
        Prevents cascading drift that chains distinct form lines together.

        Args:
            elements: Elements to cluster

        Returns:
            List of rows, each containing horizontally-aligned elements
        """
        if not elements:
            return []

        # Sort primarily by vertical center, secondarily by left X
        sorted_elements = sorted(
            elements,
            key=lambda e: ((e.bbox[1] + e.bbox[3]) / 2.0, e.bbox[0])
        )

        rows: List[Dict[str, Any]] = []

        for elem in sorted_elements:
            ey0, ey1 = elem.bbox[1], elem.bbox[3]
            eh = max(0.0001, ey1 - ey0)
            ecy = (ey0 + ey1) / 2.0

            best_row = None
            best_overlap = 0.0

            for r in rows:
                # Vertical overlap between element and row band
                inter_top = max(ey0, r["top"])
                inter_bottom = min(ey1, r["bottom"])
                inter_h = max(0.0, inter_bottom - inter_top)
                overlap_ratio = inter_h / eh

                cy_dist = abs(ecy - r["cy"])
                max_allowed_dist = max(eh, r["h"]) * 0.65

                if overlap_ratio >= 0.35 or cy_dist <= max_allowed_dist:
                    if overlap_ratio > best_overlap or (best_row is None and cy_dist <= max_allowed_dist):
                        best_row = r
                        best_overlap = overlap_ratio

            if best_row is not None:
                best_row["elements"].append(elem)
                best_row["top"] = min(best_row["top"], ey0)
                best_row["bottom"] = max(best_row["bottom"], ey1)
                all_cys = [(e.bbox[1] + e.bbox[3]) / 2.0 for e in best_row["elements"]]
                all_hs = [e.bbox[3] - e.bbox[1] for e in best_row["elements"]]
                best_row["cy"] = sum(all_cys) / len(all_cys)
                best_row["h"] = sum(all_hs) / len(all_hs)
            else:
                rows.append({
                    "elements": [elem],
                    "top": ey0,
                    "bottom": ey1,
                    "cy": ecy,
                    "h": eh,
                })

        rows.sort(key=lambda r: r["cy"])
        return [r["elements"] for r in rows]

    
    def _find_comb_box_sequences(
        self,
        row_elements: List[LayoutElement]
    ) -> List[List[LayoutElement]]:
        """
        Identify contiguous sequences with uniform spacing/sizing.
        
        Scans left-to-right, building sequences that meet uniformity criteria.
        Breaks sequence when uniformity drops or gap is too large.
        
        Args:
            row_elements: Elements in a single row (already sorted left-to-right)
            
        Returns:
            List of sequences, each a list of elements
        """
        if len(row_elements) < self.min_sequence_length:
            return []

        # Step 1: Pre-split row into sub-runs separated by whole-field jumps (large gaps between words or fields)
        char_lens = [max(1, len(e.text.strip()) if e.text else 1) for e in row_elements]
        norm_widths = [max(0.001, abs(e.bbox[2] - e.bbox[0]) / c_len) for e, c_len in zip(row_elements, char_lens)]
        norm_heights = [max(0.001, abs(e.bbox[3] - e.bbox[1])) for e in row_elements]
        row_med_w = statistics.median(norm_widths) if norm_widths else 0.01
        row_med_h = statistics.median(norm_heights) if norm_heights else 0.02

        sub_runs = []
        current_sub = [row_elements[0]]
        for i in range(1, len(row_elements)):
            prev_e = row_elements[i - 1]
            curr_e = row_elements[i]
            gap = curr_e.bbox[0] - prev_e.bbox[2]

            # In normalized coordinates (<= 1.5) vs pixel coordinates (> 1.5)
            # Scaled by median character width and height across the row.
            # Allow empty comb-box spacer cells (up to ~2.5x pitch) within a multi-word field,
            # while cleanly separating true whole-field/multi-column jumps (>= 3.5x width/height)
            gap_threshold = (
                max(row_med_w * 4.0, row_med_h * 2.0, 0.065)
                if prev_e.bbox[0] <= 1.5
                else max(row_med_w * 4.0, row_med_h * 2.0, 42.0)
            )

            if gap > gap_threshold:
                if len(current_sub) >= self.min_sequence_length:
                    sub_runs.append(current_sub)
                current_sub = [curr_e]
            else:
                current_sub.append(curr_e)

        if len(current_sub) >= self.min_sequence_length:
            sub_runs.append(current_sub)

        # Step 2: Extract uniform sequences within each sub-run
        sequences = []
        for run in sub_runs:
            current_seq = [run[0]]
            for i in range(1, len(run)):
                test_seq = current_seq + [run[i]]
                is_uniform, _ = self._is_comb_box_sequence(test_seq)
                if is_uniform:
                    current_seq = test_seq
                else:
                    if len(current_seq) >= self.min_sequence_length:
                        sequences.append(current_seq)
                    current_seq = [run[i]]

            if len(current_seq) >= self.min_sequence_length:
                sequences.append(current_seq)

        return sequences
    
    def _is_comb_box_sequence(
        self,
        elements: List[LayoutElement]
    ) -> Tuple[bool, float]:
        """
        Detect uniform spacing/sizing signature of comb-box rendering.
        
        Checks:
        1. Spacing uniformity: per-character gaps/pitches between boxes are consistent
        2. Size uniformity: per-character box widths or center pitches are consistent
        3. Aspect ratio: per-character boxes are roughly character-like
        
        Args:
            elements: Candidate sequence (already sorted left-to-right)
            
        Returns:
            (is_uniform, uniformity_score)
        """
        if len(elements) < 2:
            return False, 0.0

        # --- Geometry gate ------------------------------------------------
        char_lens = [max(1, len(e.text.strip()) if e.text else 1) for e in elements]
        widths = [(e.bbox[2] - e.bbox[0]) / c_len for e, c_len in zip(elements, char_lens)]
        heights = [e.bbox[3] - e.bbox[1] for e in elements]
        med_w = statistics.median(widths)
        med_h = statistics.median(heights)
        if med_w <= 0 or med_h <= 0:
            return False, 0.0

        origin_advances = [
            (elements[i + 1].bbox[0] - elements[i].bbox[0]) / ((char_lens[i] + char_lens[i + 1]) / 2.0)
            for i in range(len(elements) - 1)
        ]
        centre_ys = [(e.bbox[1] + e.bbox[3]) / 2.0 for e in elements]

        # 1a. Every cell must sit clearly to the right of the previous one.
        min_allowed_advance = min(med_w * 0.25, med_h * 0.15)
        if min(origin_advances) < min_allowed_advance:
            return False, 0.0

        # 1b. Baseline alignment
        centre_y_steps = [abs(centre_ys[i + 1] - centre_ys[i]) for i in range(len(centre_ys) - 1)]
        if max(centre_y_steps) > med_h * 0.6:
            return False, 0.0
        if max(centre_ys) - min(centre_ys) > med_h * 1.5:
            return False, 0.0

        # 2. No single step may be a whole-field jump (scale against height if width is narrow)
        max_allowed_advance = max(med_w * 6.5, med_h * 3.0)
        if max(origin_advances) > max_allowed_advance:
            return False, 0.0

        # Check size / pitch uniformity (per-character normalized, accounting for empty cell steps)
        x_centers = [(e.bbox[0] + e.bbox[2]) / 2.0 for e in elements]
        raw_pitches = [
            (x_centers[i + 1] - x_centers[i]) / ((char_lens[i] + char_lens[i + 1]) / 2.0)
            for i in range(len(elements) - 1)
        ]
        med_pitch = statistics.median(raw_pitches) if raw_pitches else max(med_w, 0.001)

        pitches = []
        for p in raw_pitches:
            if med_pitch > 0:
                steps = max(1, round(p / med_pitch))
                # If step is within 1 to 3 grid cell steps, normalize by step count
                if steps <= 3:
                    pitches.append(p / steps)
                else:
                    pitches.append(p)
            else:
                pitches.append(p)

        if len(pitches) >= 2:
            mean_pitch = statistics.mean(pitches)
            std_pitch = statistics.stdev(pitches) if len(pitches) > 1 else 0.0
            pitch_cv = std_pitch / (mean_pitch + 1e-4) if mean_pitch > 0 else 0.0
        else:
            pitch_cv = 0.0

        # Calculate normalized per-character gaps between consecutive elements
        gaps = []
        for i in range(len(elements) - 1):
            raw_gap = (elements[i + 1].bbox[0] - elements[i].bbox[2]) / ((char_lens[i] + char_lens[i + 1]) / 2.0)
            if med_pitch > 0:
                steps = max(1, round(raw_pitches[i] / med_pitch))
                effective_gap = raw_gap - (steps - 1) * med_pitch if (1 < steps <= 3) else raw_gap
            else:
                effective_gap = raw_gap
            gaps.append(effective_gap)

        # Check spacing uniformity
        if len(gaps) >= 2:
            abs_gaps = [abs(g) for g in gaps]
            mean_gap = statistics.mean(abs_gaps)
            std_gap = statistics.stdev(abs_gaps) if len(abs_gaps) > 1 else 0.0
            gap_cv = std_gap / (mean_gap + 1e-4)
        else:
            gap_cv = 0.0

        if len(widths) >= 2:
            mean_width = statistics.mean(widths)
            if mean_width <= 0:
                return False, 0.0
            std_width = statistics.stdev(widths) if len(widths) > 1 else 0.0
            width_cv = std_width / mean_width if mean_width > 0 else 1.0
        else:
            width_cv = 0.0
        
        # Check aspect ratio (allow narrow digital glyphs like '1', 'I', 'l')
        mean_height = statistics.mean(heights)
        mean_width = statistics.mean(widths)
        aspect_ratio = mean_width / mean_height if mean_height > 0 else 0.0
        
        aspect_ok = 0.10 <= aspect_ratio <= 3.0
        
        # Pass if spacing is uniform (via gap CV or center-to-center pitch CV) AND size/pitch is uniform
        spacing_ok = (gap_cv <= self.spacing_uniformity_threshold) or (pitch_cv <= self.spacing_uniformity_threshold)
        size_ok = (width_cv <= self.size_uniformity_threshold) or (pitch_cv <= self.spacing_uniformity_threshold)
        
        is_uniform = spacing_ok and size_ok and aspect_ok
        
        # Calculate overall uniformity score
        effective_cv = min(width_cv, pitch_cv)
        uniformity_score = (
            (1.0 - min(gap_cv, 1.0)) * 0.4 +
            (1.0 - min(effective_cv, 1.0)) * 0.4 +
            (1.0 if aspect_ok else 0.0) * 0.2
        )
        
        return is_uniform, uniformity_score
    
    def _merge_sequence(
        self,
        elements: List[LayoutElement],
        page_number: int,
        doc_id: str
    ) -> Optional[MergedToken]:
        """
        Merge a sequence of elements into a single token.
        
        Concatenates text (inserting space if an empty comb-cell spacer was present),
        merges bounding boxes, averages confidence.
        
        Args:
            elements: Sequence to merge (already validated as uniform)
            page_number: Page number
            doc_id: Document ID for logging
            
        Returns:
            MergedToken or None if merge fails
        """
        if not elements:
            return None
        
        try:
            # Concatenate text (inserting space if an empty comb-cell spacer was present)
            char_lens = [max(1, len(e.text.strip()) if e.text else 1) for e in elements]
            x_centers = [(e.bbox[0] + e.bbox[2]) / 2.0 for e in elements]
            raw_pitches = [
                (x_centers[i + 1] - x_centers[i]) / ((char_lens[i] + char_lens[i + 1]) / 2.0)
                for i in range(len(elements) - 1)
            ]
            med_pitch = statistics.median(raw_pitches) if raw_pitches else 0.0

            parts = []
            for i, elem in enumerate(elements):
                if i > 0 and med_pitch > 0:
                    gap = elements[i].bbox[0] - elements[i - 1].bbox[2]
                    pitch = x_centers[i] - x_centers[i - 1]
                    # If step represents 1 or more empty comb boxes between words
                    if pitch >= 1.55 * med_pitch or gap >= 0.85 * med_pitch:
                        parts.append(" ")
                parts.append(elem.text.strip())
            merged_text = "".join(parts)
            
            # Merge bounding boxes (encompassing bbox)
            min_x = min(elem.bbox[0] for elem in elements)
            min_y = min(elem.bbox[1] for elem in elements)
            max_x = max(elem.bbox[2] for elem in elements)
            max_y = max(elem.bbox[3] for elem in elements)
            merged_bbox = [min_x, min_y, max_x, max_y]

            
            # Average confidence
            avg_confidence = statistics.mean(elem.confidence for elem in elements)
            
            # Calculate uniformity metrics
            is_uniform, uniformity_score = self._is_comb_box_sequence(elements)
            
            # Calculate spacing/size standard deviations
            gaps = [
                elements[i + 1].bbox[0] - elements[i].bbox[2]
                for i in range(len(elements) - 1)
            ]
            widths = [elem.bbox[2] - elem.bbox[0] for elem in elements]
            
            spacing_std = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
            size_std = statistics.stdev(widths) if len(widths) > 1 else 0.0
            
            # Create merged token
            merged_token = MergedToken(
                id=f"merged-{page_number}-{uuid.uuid4().hex[:8]}",
                text=merged_text,
                bbox=merged_bbox,
                page_number=page_number,
                confidence=avg_confidence,
                source_type="comb_box_reconstruction",
                constituent_element_ids=[elem.id for elem in elements],
                merge_method="spatial_clustering",
                uniformity_score=uniformity_score,
                spacing_std_dev=spacing_std,
                size_std_dev=size_std,
                metadata={
                    "num_constituents": len(elements),
                    "constituent_texts": [elem.text for elem in elements]
                }
            )
            
            return merged_token
            
        except Exception as e:
            logger.warning(
                f"[{doc_id}] Failed to merge sequence of {len(elements)} elements: {e}"
            )
            return None