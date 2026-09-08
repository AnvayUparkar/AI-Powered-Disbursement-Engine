"""
Data model for reconstructed comb-box tokens.

Represents character sequences that have been spatially merged from individual
OCR elements (e.g., "A" "P" "P" "L" → "APPL").
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class MergedToken:
    """
    Represents a token reconstructed from spatially-aligned single characters.
    
    Used for comb-box fields where each character appears in a separate box
    (common in Indian government forms: Aadhaar, Application Forms, PAN cards).
    
    Attributes:
        id: Unique identifier for this merged token
        text: Concatenated text from constituent elements
        bbox: Encompassing bounding box [x1, y1, x2, y2]
        page_number: Page this token appears on
        confidence: Average confidence from constituent elements
        source_type: Always "comb_box_reconstruction"
        constituent_element_ids: Original element IDs that were merged
        merge_method: Algorithm used ("spatial_clustering", "uniform_spacing")
        uniformity_score: Metric for spacing/sizing consistency (0.0-1.0)
        spacing_std_dev: Standard deviation of gaps between chars
        size_std_dev: Standard deviation of character widths
    """
    
    id: str
    text: str
    bbox: List[float]
    page_number: int
    confidence: float
    source_type: str = "comb_box_reconstruction"
    constituent_element_ids: List[str] = field(default_factory=list)
    merge_method: str = "spatial_clustering"
    uniformity_score: float = 0.0
    spacing_std_dev: float = 0.0
    size_std_dev: float = 0.0
    metadata: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "text": self.text,
            "bbox": self.bbox,
            "page_number": self.page_number,
            "confidence": self.confidence,
            "source_type": self.source_type,
            "constituent_element_ids": self.constituent_element_ids,
            "merge_method": self.merge_method,
            "uniformity_score": self.uniformity_score,
            "spacing_std_dev": self.spacing_std_dev,
            "size_std_dev": self.size_std_dev,
            "metadata": self.metadata
        }
