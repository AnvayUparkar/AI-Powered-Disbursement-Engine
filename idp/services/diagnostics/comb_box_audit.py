"""
Audit trail and observability for comb-box field reconstruction.

Tracks detection events, validation results, and reconstruction metrics for
production monitoring and debugging.
"""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from idp.core.logging import logger


@dataclass
class CombBoxAuditRecord:
    """
    Audit record for a single comb-box reconstruction event.
    
    Attributes:
        document_id: Document being processed
        page_number: Page where reconstruction occurred
        field_name: Field name if known (e.g., "application_no")
        merged_text: Final reconstructed text
        constituent_tokens: Original text tokens that were merged
        uniformity_score: Quality metric (0.0-1.0)
        detection_confidence: Confidence from detector
        validation_result: Result from FieldValidator ("valid", "invalid", "not_validated")
        bbox: Bounding box of merged token
        timestamp: ISO timestamp of event
        metadata: Additional context
    """
    document_id: str
    page_number: int
    field_name: Optional[str]
    merged_text: str
    constituent_tokens: List[str]
    uniformity_score: float
    detection_confidence: float
    validation_result: str
    bbox: List[float]
    timestamp: str
    metadata: dict


class CombBoxAuditor:
    """
    Production audit system for tracking comb-box reconstruction events.
    
    Logs all reconstruction attempts, validation results, and generates
    summary reports for monitoring and debugging.
    """
    
    def __init__(self, output_dir: Optional[Path] = None):
        """
        Initialize auditor.
        
        Args:
            output_dir: Directory for audit logs (default: poc_data/audit/comb_box)
        """
        if output_dir is None:
            output_dir = Path("poc_data/audit/comb_box")
        
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events: List[CombBoxAuditRecord] = []
    
    def log_reconstruction(
        self,
        document_id: str,
        page_number: int,
        merged_text: str,
        constituent_tokens: List[str],
        uniformity_score: float,
        detection_confidence: float,
        bbox: List[float],
        field_name: Optional[str] = None,
        validation_result: str = "not_validated",
        metadata: Optional[dict] = None
    ):
        """
        Log a comb-box reconstruction event.
        
        Args:
            document_id: Document ID
            page_number: Page number
            merged_text: Reconstructed text
            constituent_tokens: Original tokens
            uniformity_score: Uniformity metric
            detection_confidence: Detection confidence
            bbox: Bounding box
            field_name: Field name if known
            validation_result: Validation status
            metadata: Additional context
        """
        record = CombBoxAuditRecord(
            document_id=document_id,
            page_number=page_number,
            field_name=field_name,
            merged_text=merged_text,
            constituent_tokens=constituent_tokens,
            uniformity_score=uniformity_score,
            detection_confidence=detection_confidence,
            validation_result=validation_result,
            bbox=bbox,
            timestamp=datetime.utcnow().isoformat(),
            metadata=metadata or {}
        )
        self.events.append(record)
    
    def generate_report(self, doc_id: str) -> Dict:
        """
        Generate summary audit report for a document.
        
        Args:
            doc_id: Document ID
            
        Returns:
            Summary dict with metrics and event details
        """
        doc_events = [e for e in self.events if e.document_id == doc_id]
        
        if not doc_events:
            return {
                "document_id": doc_id,
                "total_reconstructions": 0,
                "pages_with_reconstructions": 0,
                "events": []
            }
        
        # Calculate metrics
        pages = set(e.page_number for e in doc_events)
        
        # Group by field name
        fields_reconstructed = {}
        for event in doc_events:
            if event.field_name:
                if event.field_name not in fields_reconstructed:
                    fields_reconstructed[event.field_name] = []
                fields_reconstructed[event.field_name].append(event.merged_text)
        
        # Validation stats
        validation_counts = {
            "valid": sum(1 for e in doc_events if e.validation_result == "valid"),
            "invalid": sum(1 for e in doc_events if e.validation_result == "invalid"),
            "not_validated": sum(1 for e in doc_events if e.validation_result == "not_validated")
        }
        
        # Quality metrics
        avg_uniformity = (
            sum(e.uniformity_score for e in doc_events) / len(doc_events)
            if doc_events else 0.0
        )
        avg_confidence = (
            sum(e.detection_confidence for e in doc_events) / len(doc_events)
            if doc_events else 0.0
        )
        
        return {
            "document_id": doc_id,
            "total_reconstructions": len(doc_events),
            "pages_with_reconstructions": len(pages),
            "fields_reconstructed": fields_reconstructed,
            "validation_counts": validation_counts,
            "validation_rate": (
                validation_counts["valid"] / len(doc_events)
                if doc_events else 0.0
            ),
            "average_uniformity_score": round(avg_uniformity, 3),
            "average_detection_confidence": round(avg_confidence, 3),
            "events": [asdict(e) for e in doc_events]
        }
    
    def save_audit_log(self, doc_id: str):
        """
        Persist audit log to JSON file.
        
        Args:
            doc_id: Document ID
        """
        report = self.generate_report(doc_id)
        output_file = self.output_dir / f"{doc_id}_comb_box_audit.json"
        
        try:
            with open(output_file, "w") as f:
                json.dump(report, f, indent=2)
            logger.info(f"[{doc_id}] Comb-box audit log saved: {output_file}")
        except Exception as e:
            logger.warning(f"[{doc_id}] Failed to save comb-box audit log: {e}")
    
    def get_global_stats(self) -> Dict:
        """
        Get statistics across all documents processed.
        
        Returns:
            Global metrics dict
        """
        if not self.events:
            return {
                "total_events": 0,
                "total_documents": 0,
                "total_pages": 0
            }
        
        documents = set(e.document_id for e in self.events)
        pages = set((e.document_id, e.page_number) for e in self.events)
        
        return {
            "total_events": len(self.events),
            "total_documents": len(documents),
            "total_pages": len(pages),
            "average_reconstructions_per_document": round(
                len(self.events) / len(documents), 1
            ) if documents else 0.0,
            "average_uniformity_score": round(
                sum(e.uniformity_score for e in self.events) / len(self.events), 3
            ),
            "average_detection_confidence": round(
                sum(e.detection_confidence for e in self.events) / len(self.events), 3
            )
        }
    
    def reset(self):
        """Clear all accumulated events."""
        self.events = []


# Global auditor instance (optional singleton pattern)
_GLOBAL_AUDITOR: Optional[CombBoxAuditor] = None


def get_global_auditor() -> CombBoxAuditor:
    """Get or create global auditor instance."""
    global _GLOBAL_AUDITOR
    if _GLOBAL_AUDITOR is None:
        _GLOBAL_AUDITOR = CombBoxAuditor()
    return _GLOBAL_AUDITOR
