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


class CombBoxDetector:
    """
    Spatial clustering engine for detecting and reconstructing comb-box fields.
    
    Detects sequences of single characters with uniform spacing/sizing and
    merges them into cohesive tokens for field extraction.
    """
    
    def __init__(self,
                 y_tolerance: float = 0.045,
                 spacing_uniformity_threshold: float = 0.30,
                 size_uniformity_threshold: float = 0.25,
                 min_sequence_length: int = 4,
                 max_char_length: int = 3):
        """
        Initialize comb-box detector with configuration.
        
        Args:
            y_tolerance: Max vertical deviation for row clustering (normalized)
            spacing_uniformity_threshold: Max StdDev/Mean for gap uniformity
            size_uniformity_threshold: Max StdDev/Mean for width uniformity
            min_sequence_length: Minimum consecutive chars for comb-box
            max_char_length: Maximum characters per element (1-3 typical)
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
            and elem.text
            and len(elem.text.strip()) <= self.max_char_length
            and elem.text.strip().isalnum()  # Only alphanumeric
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
        
        for elem in sorted_elements[1:]:
            elem_y = elem.bbox[1]
            
            # Check if within tolerance of current row
            if abs(elem_y - current_y) <= self.y_tolerance:
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
        
        # Calculate gaps between consecutive elements
        gaps = []
        for i in range(len(elements) - 1):
            gap = elements[i + 1].bbox[0] - elements[i].bbox[2]
            gaps.append(gap)
        
        # Calculate element widths
        widths = [elem.bbox[2] - elem.bbox[0] for elem in elements]
        
        # Calculate heights for aspect ratio check
        heights = [elem.bbox[3] - elem.bbox[1] for elem in elements]
        
        # Check spacing uniformity
        if len(gaps) >= 2:
            mean_gap = statistics.mean(gaps)
            if mean_gap <= 0:
                return False, 0.0
            std_gap = statistics.stdev(gaps) if len(gaps) > 1 else 0.0
            gap_cv = std_gap / mean_gap if mean_gap > 0 else 1.0
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
