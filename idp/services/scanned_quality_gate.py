"""
Scanned Document Quality Gate and In-Flight Selective Remediation Engine.

Provides fast, deterministic (< 15ms CPU) quality scoring and targeted 
remediations for scanned document pages (PDF raster pages or standalone image files).
"""

from typing import Dict, Any, List, Tuple, Optional
from pydantic import BaseModel, Field
import cv2
import numpy as np


class ScannedQualityReport(BaseModel):
    """Quality metrics scorecard and applied remediation report for a scanned page."""
    page_number: int = 1
    effective_dpi: int = 300
    sharpness: float = 0.0
    contrast: float = 0.0
    skew_angle_deg: float = 0.0
    dark_ratio: float = 0.0
    quality_grade: str = "CLEAN"  # 'CLEAN', 'DEFECTIVE', 'DEGRADED'
    applied_remedies: List[str] = Field(default_factory=list)


class ScannedDocQualityGate:
    """
    Production-grade quality inspector and selective enhancement router for scanned documents.
    Avoids blanket 3x upscaling, binarization, and filtering on clean scans while surgically
    remedying degraded pages.
    """

    def __init__(
        self,
        min_dpi: int = 180,
        min_sharpness: float = 120.0,
        min_contrast: float = 38.0,
        max_skew_deg: float = 0.5,
    ) -> None:
        self.min_dpi = min_dpi
        self.min_sharpness = min_sharpness
        self.min_contrast = min_contrast
        self.max_skew_deg = max_skew_deg

    def calculate_effective_dpi(self, page: Any) -> int:
        """
        Computes true raster image resolution relative to physical page bounds for a PyMuPDF Page.
        """
        try:
            images = page.get_images(full=True)
            if not images:
                return 72

            page_rect = page.rect
            dpis = []
            for img_info in images:
                xref = img_info[0]
                img = page.parent.extract_image(xref)
                dpi_x = img["width"] / (page_rect.width / 72.0)
                dpi_y = img["height"] / (page_rect.height / 72.0)
                dpis.append(min(dpi_x, dpi_y))

            return int(np.median(dpis)) if dpis else 72
        except Exception:
            return 72

    def analyze_scanned_image(
        self,
        bgr_image: np.ndarray,
        effective_dpi: int = 300,
        page_number: int = 1
    ) -> ScannedQualityReport:
        """
        Runs fast no-reference image quality heuristics (< 15ms on CPU).
        """
        gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY) if len(bgr_image.shape) == 3 else bgr_image

        # 1. Sharpness (Variance of Laplacian)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

        # 2. Contrast & Dynamic Range
        contrast = float(gray.std())
        dark_pixels_ratio = float(np.mean(gray < 25))

        # 3. Skew Detection via Hough Lines
        skew_angle = self._detect_skew_angle(gray)

        # Classify remedies needed
        remedies_needed: List[str] = []
        if effective_dpi < self.min_dpi:
            remedies_needed.append("SUPER_RESOLUTION")
        if abs(skew_angle) > self.max_skew_deg:
            remedies_needed.append("DESKEW")
        if dark_pixels_ratio > 0.08 or (dark_pixels_ratio > 0.04 and contrast < self.min_contrast):
            remedies_needed.append("SHADOW_REMOVAL")
        elif contrast < self.min_contrast:
            remedies_needed.append("CONTRAST_STRETCH")
        if sharpness < self.min_sharpness and "SUPER_RESOLUTION" not in remedies_needed:
            remedies_needed.append("UNSHARP_MASK")

        if not remedies_needed:
            grade = "CLEAN"
        elif "SUPER_RESOLUTION" in remedies_needed or (sharpness < 70.0 and contrast < 25.0):
            grade = "DEGRADED"
        else:
            grade = "DEFECTIVE"

        return ScannedQualityReport(
            page_number=page_number,
            effective_dpi=effective_dpi,
            sharpness=round(sharpness, 2),
            contrast=round(contrast, 2),
            skew_angle_deg=round(skew_angle, 2),
            dark_ratio=round(dark_pixels_ratio, 4),
            quality_grade=grade,
            applied_remedies=remedies_needed,
        )

    def _detect_skew_angle(self, gray: np.ndarray) -> float:
        """
        Determines document orientation angle using edge detection and Hough line transform.
        """
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)
        if lines is None:
            return 0.0

        angles: List[float] = []
        for line in lines:
            coords = line.ravel()
            if len(coords) < 4:
                continue
            x1, y1, x2, y2 = coords[:4]
            angle = float(np.degrees(np.arctan2(y2 - y1, x2 - x1)))
            if abs(angle) < 45.0:  # Only consider near-horizontal text lines
                angles.append(angle)

        return float(np.median(angles)) if angles else 0.0

    def process_scanned_page(
        self,
        bgr_image: np.ndarray,
        effective_dpi: int = 300,
        page_number: int = 1
    ) -> Tuple[np.ndarray, ScannedQualityReport]:
        """
        In-Flight Page Remediation:
        Clean scans bypass all destructive operations (0 memory copy, 0 filtering).
        Defective pages receive surgical remedies.
        """
        report = self.analyze_scanned_image(bgr_image, effective_dpi, page_number)

        # FAST PATH: Clean page passes through untouched
        if report.quality_grade == "CLEAN":
            return bgr_image, report

        # SELECTIVE PATH: Apply targeted remedies
        processed = bgr_image.copy()

        # 1. Deskew
        if "DESKEW" in report.applied_remedies:
            processed = self._deskew_image(processed, report.skew_angle_deg)

        # 2. Shadow Removal / Illumination Normalization
        if "SHADOW_REMOVAL" in report.applied_remedies:
            processed = self._normalize_background(processed)
        elif "CONTRAST_STRETCH" in report.applied_remedies:
            processed = self._apply_clahe(processed)

        # 3. Super-Resolution / Lanczos Upscaling for Low DPI
        if "SUPER_RESOLUTION" in report.applied_remedies:
            scale = 300.0 / max(effective_dpi, 72)
            processed = cv2.resize(processed, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)

        # 4. Selective Edge Sharpening for Minor Blur
        if "UNSHARP_MASK" in report.applied_remedies:
            processed = self._unsharp_mask(processed)

        return processed, report

    def _deskew_image(self, img: np.ndarray, angle: float) -> np.ndarray:
        h, w = img.shape[:2]
        center = (w // 2, h // 2)
        rot_mat = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(img, rot_mat, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

    def _normalize_background(self, img: np.ndarray) -> np.ndarray:
        """
        Removes localized shadows and uneven lighting via morphological background division,
        preserving anti-aliased font strokes.
        """
        if len(img.shape) == 3:
            # Process luminance in LAB color space to preserve color consistency
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            dilated = cv2.morphologyEx(l, cv2.MORPH_DILATE, np.ones((7, 7), np.uint8))
            bg_smooth = cv2.medianBlur(dilated, 21)
            diff = 255 - cv2.absdiff(l, bg_smooth)
            norm_l = cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)
            merged = cv2.merge((norm_l, a, b))
            return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
        else:
            dilated = cv2.morphologyEx(img, cv2.MORPH_DILATE, np.ones((7, 7), np.uint8))
            bg_smooth = cv2.medianBlur(dilated, 21)
            diff = 255 - cv2.absdiff(img, bg_smooth)
            return cv2.normalize(diff, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX, dtype=cv2.CV_8UC1)

    def _apply_clahe(self, img: np.ndarray) -> np.ndarray:
        if len(img.shape) == 3:
            lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            l_clahe = clahe.apply(l)
            merged = cv2.merge((l_clahe, a, b))
            return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        return clahe.apply(img)

    def _unsharp_mask(self, img: np.ndarray, sigma: float = 1.0, strength: float = 1.5) -> np.ndarray:
        blurred = cv2.GaussianBlur(img, (0, 0), sigma)
        sharpened = cv2.addWeighted(img, 1.0 + strength, blurred, -strength, 0)
        return np.clip(sharpened, 0, 255).astype(np.uint8)
