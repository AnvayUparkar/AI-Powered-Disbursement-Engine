"""
Integration tests for bounding box formation fix.

Tests the complete pipeline from OCR element creation through
confidence evaluation to ensure layout-passthrough detection
works correctly in realistic scenarios.
"""

import pytest
from unittest.mock import MagicMock
from idp.models.ocr import OCRElement, OCRResult
from idp.services.ocr.confidence import OCRConfidenceEvaluator


class TestBboxFormationIntegration:
    """Integration tests for realistic OCR processing scenarios."""

    def test_loan_application_form_scenario(self):
        """Test realistic loan application form with mixed element types."""
        evaluator = OCRConfidenceEvaluator(threshold=0.70)
        
        # Simulate realistic loan form elements as would come from Docling + RapidOCR
        elements = [
            # Valid OCR text elements
            OCRElement(
                id="1",
                text="Loan Application Form",
                bbox=[50, 20, 200, 40],
                confidence=0.95,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            # HDB logo - layout passthrough with no actual text
            OCRElement(
                id="2", 
                text="Picture",  # Generic Docling label
                bbox=[20, 20, 80, 60],  # Logo region
                confidence=1.0,  # Docling gives high confidence to layout regions
                page_number=1,
                metadata={"layout_label": "Picture"}
            ),
            
            # Checkbox row - wide layout container with nested tokens
            OCRElement(
                id="3",
                text="Text Block",  # Generic Docling container label
                bbox=[100, 100, 500, 120],  # Wide container
                confidence=1.0,
                page_number=1,
                metadata={"layout_label": "Text Block"}
            ),
            
            # Individual checkbox options (would be detected by RapidOCR)
            OCRElement(
                id="4",
                text="Business",
                bbox=[110, 102, 160, 118],
                confidence=0.92,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            OCRElement(
                id="5",
                text="Self employed",
                bbox=[170, 102, 250, 118],
                confidence=0.89,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            OCRElement(
                id="6",
                text="Professional",
                bbox=[260, 102, 340, 118],
                confidence=0.91,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            # Regular form field
            OCRElement(
                id="7",
                text="Applicant Name",
                bbox=[100, 150, 200, 170],
                confidence=0.94,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            # Degenerate bbox case (parsing error)
            OCRElement(
                id="8",
                text="Corrupted Entry",
                bbox=[0, 0, 0, 0],
                confidence=0.85,
                page_number=1,
                metadata={}
            )
        ]
        
        # Set source attributes (simulate what would come from parsers)
        elements[0].source = "rapidocr"
        elements[1].source = "docling"  # Logo from layout detector
        elements[2].source = "docling"  # Container from layout detector  
        elements[3].source = "rapidocr"
        elements[4].source = "rapidocr"
        elements[5].source = "rapidocr"
        elements[6].source = "rapidocr"
        elements[7].source = "unknown"
        
        # Create OCRResult
        result = OCRResult(page_number=1, elements=elements)
        
        # Evaluate each element
        evaluated_elements = []
        for element in elements:
            evaluated = evaluator.evaluate_element(element)
            evaluated_elements.append(evaluated)
        
        # Test expectations
        
        # Element 1: Clean title - should pass through unchanged
        assert evaluated_elements[0].needs_vlm is False
        assert "layout_passthrough" not in evaluated_elements[0].metadata
        assert evaluated_elements[0].confidence >= 0.90
        
        # Element 2: Logo - should be flagged as passthrough (Picture label + generic text)
        assert evaluated_elements[1].needs_vlm is True
        assert evaluated_elements[1].metadata.get("layout_passthrough") is True
        
        # Element 3: Wide container - should be flagged as passthrough (Text Block label)
        assert evaluated_elements[2].needs_vlm is True
        assert evaluated_elements[2].metadata.get("layout_passthrough") is True
        
        # Elements 4,5,6: Valid checkbox text - should not be flagged
        for i in [3, 4, 5]:
            assert evaluated_elements[i].needs_vlm is False
            assert "layout_passthrough" not in evaluated_elements[i].metadata
        
        # Element 7: Regular form field - should not be flagged
        assert evaluated_elements[6].needs_vlm is False
        assert "layout_passthrough" not in evaluated_elements[6].metadata
        
        # Element 8: Degenerate bbox - should be penalized in confidence
        assert evaluated_elements[7].confidence < 0.70  # Below threshold due to bbox penalty

    def test_bbox_reconstruction_realistic_scenario(self):
        """Test bbox reconstruction with realistic form layout."""
        evaluator = OCRConfidenceEvaluator(threshold=0.70)
        
        # Container element that needs reconstruction
        container = OCRElement(
            id="container",
            text="Form Field",
            bbox=[50, 200, 400, 230],  # Wide container
            confidence=1.0,
            page_number=1,
            metadata={"layout_label": "Form Field"}
        )
        container.source = "docling"
        
        # Child elements detected by OCR within the container
        children = [
            OCRElement(
                id="child1",
                text="Employment:",
                bbox=[60, 205, 130, 225],
                confidence=0.93,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            OCRElement(
                id="child2", 
                text="Salaried",
                bbox=[140, 205, 190, 225],
                confidence=0.91,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            ),
            
            OCRElement(
                id="child3",
                text="Business Owner",
                bbox=[200, 205, 300, 225],
                confidence=0.89,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            )
        ]
        
        for child in children:
            child.source = "rapidocr"
        
        all_elements = [container] + children
        
        # Test that container is detected as passthrough
        assert evaluator.is_layout_passthrough(container) is True
        
        # Test reconstruction
        reconstructed_bbox = evaluator.reconstruct_bbox_from_row_neighbors(
            container, all_elements
        )
        
        # Should return union of children: [60, 205, 300, 225]
        assert reconstructed_bbox is not None
        assert reconstructed_bbox[0] == 60    # min x from child1
        assert reconstructed_bbox[1] == 205   # min y from children
        assert reconstructed_bbox[2] == 300   # max x from child3
        assert reconstructed_bbox[3] == 225   # max y from children
        
        # Metadata should reflect reconstruction
        assert container.metadata["bbox_source"] == "reconstructed_from_neighbors"
        assert container.metadata["bbox_reconstruction_token_count"] == 3

    def test_logo_region_no_reconstruction(self):
        """Test that genuine logo/image regions don't get reconstructed."""
        evaluator = OCRConfidenceEvaluator(threshold=0.70)
        
        # HDB logo element
        logo = OCRElement(
            id="logo",
            text="Picture",
            bbox=[20, 20, 100, 80],
            confidence=1.0,
            page_number=1,
            metadata={"layout_label": "Picture"}
        )
        logo.source = "docling"
        
        # Other elements on page (but not overlapping with logo)
        other_elements = [
            OCRElement(
                id="title",
                text="Housing Development Board",
                bbox=[120, 30, 300, 50],
                confidence=0.94,
                page_number=1,
                metadata={"ocr_engine": "rapidocr"}
            )
        ]
        other_elements[0].source = "rapidocr"
        
        all_elements = [logo] + other_elements
        
        # Should be detected as passthrough
        assert evaluator.is_layout_passthrough(logo) is True
        
        # Reconstruction should keep original bbox (no overlapping elements)
        reconstructed = evaluator.reconstruct_bbox_from_row_neighbors(
            logo, all_elements
        )
        
        assert reconstructed == [20, 20, 100, 80]  # Original bbox preserved
        assert logo.metadata["bbox_source"] == "layout_fallback_no_text"
        assert logo.metadata["bbox_reconstruction_token_count"] == 0

    def test_confidence_score_integration(self):
        """Test that confidence scoring integrates properly with passthrough detection."""
        evaluator = OCRConfidenceEvaluator(threshold=0.75)
        
        test_cases = [
            # High confidence valid text - should remain high, not flagged
            {
                "element": OCRElement(
                    id="1", text="Applicant Name", bbox=[10, 10, 100, 30],
                    confidence=0.95, page_number=1, metadata={"ocr_engine": "rapidocr"}
                ),
                "expected_needs_vlm": False,
                "expected_min_confidence": 0.90
            },
            
            # Layout passthrough - should be flagged regardless of high confidence
            {
                "element": OCRElement(
                    id="2", text="Text Block", bbox=[10, 50, 200, 70],
                    confidence=1.0, page_number=1, metadata={"layout_label": "Text Block"}
                ),
                "expected_needs_vlm": True,
                "expected_min_confidence": 0.90  # Original confidence maintained
            },
            
            # Low confidence text - should be flagged for different reason
            {
                "element": OCRElement(
                    id="3", text="Garbled~Text#123", bbox=[10, 90, 100, 110],
                    confidence=0.60, page_number=1, metadata={"ocr_engine": "rapidocr"}
                ),
                "expected_needs_vlm": True,
                "expected_min_confidence": 0.0  # May be penalized further
            },
            
            # Degenerate bbox - should be heavily penalized
            {
                "element": OCRElement(
                    id="4", text="Valid Text Content", bbox=[0, 0, 0, 0],
                    confidence=0.90, page_number=1, metadata={}
                ),
                "expected_needs_vlm": False,  # Only flagged due to low final confidence
                "expected_min_confidence": 0.0  # Will be below threshold due to bbox penalty
            }
        ]
        
        # Set source for elements that need it
        test_cases[0]["element"].source = "rapidocr"
        test_cases[1]["element"].source = "docling"
        test_cases[2]["element"].source = "rapidocr" 
        test_cases[3]["element"].source = "rapidocr"
        
        for i, case in enumerate(test_cases):
            element = case["element"]
            evaluated = evaluator.evaluate_element(element)
            
            assert evaluated.needs_vlm == case["expected_needs_vlm"], \
                f"Case {i+1}: Expected needs_vlm={case['expected_needs_vlm']}, got {evaluated.needs_vlm}"
            
            if case["expected_min_confidence"] > 0:
                assert evaluated.confidence >= case["expected_min_confidence"], \
                    f"Case {i+1}: Expected confidence>={case['expected_min_confidence']}, got {evaluated.confidence}"

    def test_error_handling_robustness(self):
        """Test that the system handles malformed data gracefully."""
        evaluator = OCRConfidenceEvaluator(threshold=0.70)
        
        # Test malformed elements don't crash the system
        malformed_elements = [
            # Missing attributes
            MagicMock(text="test", bbox=None, metadata=None),
            
            # Invalid bbox formats  
            MagicMock(text="test", bbox="invalid", metadata={}),
            MagicMock(text="test", bbox=[1, 2], metadata={}),
            MagicMock(text="test", bbox=[float('nan'), 0, 10, 10], metadata={}),
            
            # Missing text
            MagicMock(text=None, bbox=[10, 10, 50, 30], metadata={}),
        ]
        
        for element in malformed_elements:
            # Should not crash
            try:
                is_passthrough = evaluator.is_layout_passthrough(element)
                assert isinstance(is_passthrough, bool)
                
                # Reconstruction should also not crash
                result = evaluator.reconstruct_bbox_from_row_neighbors(element, [])
                # Should return None or original bbox, not crash
                
            except Exception as e:
                pytest.fail(f"System crashed on malformed element: {e}")