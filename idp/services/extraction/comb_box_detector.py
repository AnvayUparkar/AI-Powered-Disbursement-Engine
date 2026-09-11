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
from typing import List, Dict, Tuple, Optional
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
    # them. Keeping this as a single static method avoids the two call sites
    # drifting apart, which is what originally let comb-box characters get
    # deleted upstream before merging was ever attempted.
    DEFAULT_MAX_CHAR_LENGTH = 3

    @staticmethod
    def is_candidate_text(text: Optional[str], max_char_length: int = DEFAULT_MAX_CHAR_LENGTH) -> bool:
        """
        True if `text` is short/simple enough to plausibly be one cell of a
        comb-box field (a single handwritten or printed character/short code).
        """
        if not text:
            return False
        stripped = text.strip()
        return bool(stripped) and len(stripped) <= max_char_length and stripped.isalnum()

    def __init__(self,
                 y_tolerance: float = 0.045,
                 spacing_uniformity_threshold: float = 0.35,
                 size_uniformity_threshold: float = 0.40,
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
        Group elements into horizontal rows based on y-coordinate alignment.
        
        Uses y_tolerance to handle slight vertical misalignment from OCR.
        
        Args:
            elements: Elements to cluster
            
        Returns:
            List of rows, each containing horizontally-aligned elements
        """
        if not elements:
            return []
        
        # Sort by vertical position (top to bottom)
        sorted_elements = sorted(elements, key=lambda e: e.bbox[1])
        
        rows = []
        current_row = [sorted_elements[0]]
        current_y = sorted_elements[0].bbox[1]
        
        # Adaptive y_tolerance based on coordinate space (normalized <= 1.5 vs pixels > 1.5)
        sample_y = sorted_elements[0].bbox[1]
        effective_tolerance = self.y_tolerance if sample_y <= 1.5 else max(15.0, self.y_tolerance * 500.0)

        for elem in sorted_elements[1:]:
            elem_y = elem.bbox[1]
            
            # Check if within tolerance of current row
            if abs(elem_y - current_y) <= effective_tolerance:
                current_row.append(elem)
            else:
                # Start new row
                rows.append(current_row)
                current_row = [elem]
                current_y = elem_y
        
        # Add last row
        if current_row:
            rows.append(current_row)
        
        return rows
    
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
        
        sequences = []
        current_seq = [row_elements[0]]
        
        for i in range(1, len(row_elements)):
            # Try adding next element to current sequence
            test_seq = current_seq + [row_elements[i]]
            
            # Check if still uniform
            is_uniform, _ = self._is_comb_box_sequence(test_seq)
            
            if is_uniform:
                current_seq = test_seq
            else:
                # Break sequence - save current if long enough
                if len(current_seq) >= self.min_sequence_length:
                    sequences.append(current_seq)
                # Start new sequence
                current_seq = [row_elements[i]]
        
        # Add last sequence
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
        1. Spacing uniformity: gaps between boxes are consistent
        2. Size uniformity: box widths are consistent
        3. Aspect ratio: boxes are roughly square (not words)
        
        Args:
            elements: Candidate sequence (already sorted left-to-right)
            
        Returns:
            (is_uniform, uniformity_score)
        """
        if len(elements) < 2:
            return False, 0.0

        # --- Geometry gate ------------------------------------------------
        # A genuine comb-box field is a run of cells that advance strictly
        # left-to-right at a roughly constant pitch along ONE shared text
        # baseline. Reject sequences that don't look like that:
        #   1. VERTICAL STACKS -- a column of single characters (a numbered
        #      list "1"/"2"/"3", stacked initials) shares one x position, so
        #      the abs()-based spacing check further down mistakes it for a
        #      perfectly uniform comb row and merges it into a tall, narrow
        #      bogus token.
        #   2. FAR-APART TOKENS ON ONE LINE -- e.g. a label and a value
        #      ("STD" ........ "PAN") with nothing between them, merged into
        #      a single page-spanning token.
        # Every tolerance below is scaled by the MEDIAN cell width/height of
        # the whole run, never an individual element's own size: a real comb
        # row legitimately mixes a thin "1"/"I" with a wide "M"/"0" inside an
        # identical printed cell pitch, and scaling by the thin glyph's width
        # (as an earlier version did) wrongly broke the field so it stopped
        # being detected at all.
        widths = [e.bbox[2] - e.bbox[0] for e in elements]
        heights = [e.bbox[3] - e.bbox[1] for e in elements]
        med_w = statistics.median(widths)
        med_h = statistics.median(heights)
        if med_w <= 0 or med_h <= 0:
            return False, 0.0

        origin_advances = [
            elements[i + 1].bbox[0] - elements[i].bbox[0]
            for i in range(len(elements) - 1)
        ]
        centre_ys = [(e.bbox[1] + e.bbox[3]) / 2.0 for e in elements]

        # 1a. Every cell must sit clearly to the right of the previous one.
        #     A vertical stack advances by ~0 (shared column); even a tightly
        #     kerned comb row still advances by most of a glyph width.
        if min(origin_advances) < med_w * 0.25:
            return False, 0.0

        # 1b. All cell centres must lie on one baseline. A stacked row is
        #     offset by a whole line (>= med_h); page-scan skew stays well
        #     inside this band.
        if max(centre_ys) - min(centre_ys) > med_h * 0.6:
            return False, 0.0

        # 2.  No single step may be a whole-field jump instead of a cell pitch
        #     (a label-to-value gap). Real comb pitch stays within a few
        #     median cell widths.
        if max(origin_advances) > med_w * 5.0:
            return False, 0.0

        # Calculate gaps between consecutive elements
        gaps = []
        for i in range(len(elements) - 1):
            gap = elements[i + 1].bbox[0] - elements[i].bbox[2]
            gaps.append(gap)

        # `widths` / `heights` were already computed for the geometry gate above.

        # Check spacing uniformity
        if len(gaps) >= 2:
            abs_gaps = [abs(g) for g in gaps]
            mean_gap = statistics.mean(abs_gaps)
            std_gap = statistics.stdev(abs_gaps) if len(abs_gaps) > 1 else 0.0
            gap_cv = std_gap / (mean_gap + 1e-4)
        else:
            gap_cv = 0.0
        
        # Check size uniformity
        if len(widths) >= 2:
            mean_width = statistics.mean(widths)
            if mean_width <= 0:
                return False, 0.0
            std_width = statistics.stdev(widths) if len(widths) > 1 else 0.0
            width_cv = std_width / mean_width if mean_width > 0 else 1.0
        else:
            width_cv = 0.0
        
        # Check aspect ratio (comb boxes are roughly square, not wide)
        mean_height = statistics.mean(heights)
        mean_width = statistics.mean(widths)
        aspect_ratio = mean_width / mean_height if mean_height > 0 else 0.0
        
        # Comb boxes have aspect ratio ~0.5 to 2.0 (square-ish)
        aspect_ok = 0.3 <= aspect_ratio <= 2.5
        
        # Pass if both spacing and size are uniform
        spacing_ok = gap_cv <= self.spacing_uniformity_threshold
        size_ok = width_cv <= self.size_uniformity_threshold
        
        is_uniform = spacing_ok and size_ok and aspect_ok
        
        # Calculate overall uniformity score
        uniformity_score = (
            (1.0 - min(gap_cv, 1.0)) * 0.4 +  # 40% weight on spacing
            (1.0 - min(width_cv, 1.0)) * 0.4 +  # 40% weight on size
            (1.0 if aspect_ok else 0.0) * 0.2  # 20% weight on aspect
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
        
        Concatenates text, merges bounding boxes, averages confidence.
        
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
            # Concatenate text
            merged_text = "".join(elem.text.strip() for elem in elements)
            
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