from typing import Tuple, Dict, Any, Optional
import io
import re
from idp.core.logging import logger


class OCRImagePreprocessor:
    """Preprocesses images for optimal OCR recognition (deskew, contrast, blur correction, denoising, binarization)."""

    def __init__(
        self,
        blur_threshold: float = 100.0,
        contrast_std_threshold: float = 35.0,
        contrast_dynamic_range_threshold: float = 80.0
    ):
        self.blur_threshold = blur_threshold
        self.contrast_std_threshold = contrast_std_threshold
        self.contrast_dynamic_range_threshold = contrast_dynamic_range_threshold

    def assess_quality(self, image_bytes: bytes) -> Dict[str, Any]:
        """
        Lightweight assessment of image quality metrics (blur variance, contrast std_dev, dynamic range)
        without performing any heavy transformations.
        """
        try:
            import cv2
            import numpy as np

            nparr = np.frombuffer(image_bytes, np.uint8)
            gray = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
            if gray is None:
                return {
                    "blur_score": 0.0,
                    "contrast_std": 0.0,
                    "dynamic_range": 0.0,
                    "is_sharp": False,
                    "is_well_contrasted": False,
                    "needs_preprocessing": True
                }

            blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            contrast_std = float(np.std(gray))
            p5, p95 = np.percentile(gray, (5, 95))
            dynamic_range = float(p95 - p5)

            is_sharp = blur_score >= self.blur_threshold
            is_well_contrasted = (
                contrast_std >= self.contrast_std_threshold
                and dynamic_range >= self.contrast_dynamic_range_threshold
            )

            return {
                "blur_score": blur_score,
                "contrast_std": contrast_std,
                "dynamic_range": dynamic_range,
                "is_sharp": is_sharp,
                "is_well_contrasted": is_well_contrasted,
                "needs_preprocessing": not (is_sharp and is_well_contrasted)
            }
        except Exception as e:
            logger.debug(f"Quality assessment error: {e}")
            return {
                "blur_score": 0.0,
                "contrast_std": 0.0,
                "dynamic_range": 0.0,
                "is_sharp": False,
                "is_well_contrasted": False,
                "needs_preprocessing": True
            }

    def preprocess_image(
        self,
        image_bytes: bytes,
        doc_id: str = "DOC",
        skip_preprocessing: bool = False
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Evaluate image characteristics and apply conditional preprocessing:
        1. Grayscale conversion
        2. Coarse orientation correction (0/90/180/270)
        3. Fine-angle deskew (Hough lines with minAreaRect contour fallback)
        4. Blur detection and unsharp masking sharpening
        5. Contrast enhancement (CLAHE) on low-contrast/faded scans
        6. Denoising with fastNlMeansDenoising
        7. Adaptive binarization on low-contrast/faded pages
        """
        metadata: Dict[str, Any] = {
            "rotation_applied": False,
            "rotation_angle": 0.0,
            "contrast_enhanced": False,
            "grayscale_converted": False,
            "blur_corrected": False,
            "denoised": False,
            "binarized": False,
            "orientation_corrected": False,
            "skipped": skip_preprocessing
        }

        if skip_preprocessing:
            return image_bytes, metadata

        try:
            import cv2
            import numpy as np

            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return image_bytes, metadata

            # 1. Grayscale conversion
            try:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                metadata["grayscale_converted"] = True
            except Exception as e:
                logger.debug(f"[{doc_id}] Grayscale conversion exception: {e}")
                gray = img

            # 2. Coarse orientation correction (0/90/180/270) before fine-angle deskew
            try:
                coarse_angle = self._detect_coarse_orientation(gray)
                if coarse_angle in (90, 180, 270):
                    if coarse_angle == 90:
                        rotate_code = cv2.ROTATE_90_CLOCKWISE
                    elif coarse_angle == 180:
                        rotate_code = cv2.ROTATE_180
                    else:
                        rotate_code = cv2.ROTATE_90_COUNTERCLOCKWISE

                    gray = cv2.rotate(gray, rotate_code)
                    if len(img.shape) == 3:
                        img = cv2.rotate(img, rotate_code)
                    metadata["orientation_corrected"] = True
                    metadata["rotation_applied"] = True
                    metadata["rotation_angle"] += float(coarse_angle)
                    logger.info(f"[{doc_id}] Applied coarse orientation correction of {coarse_angle} degrees")
            except Exception as e:
                logger.debug(f"[{doc_id}] Coarse orientation detection skipped/failed: {e}")

            # 3. Fine-angle deskew detection using Hough Lines with minAreaRect contour fallback
            try:
                angle = self._detect_skew_angle(gray)
                if abs(angle) > 1.0 and abs(angle) < 45.0:
                    gray = self._rotate_image(gray, angle)
                    if len(img.shape) == 3:
                        img = self._rotate_image(img, angle)
                    metadata["rotation_applied"] = True
                    metadata["rotation_angle"] += float(angle)
                    logger.info(f"[{doc_id}] Applied fine deskew rotation of {angle:.2f} degrees")
            except Exception as e:
                logger.debug(f"[{doc_id}] Fine deskew failed: {e}")

            # 4. Blur detection and unsharp masking sharpening
            try:
                blur_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
                if blur_var < self.blur_threshold:
                    # Apply unsharp masking: sharpen by adding weighted difference of blurred image
                    gaussian = cv2.GaussianBlur(gray, (0, 0), sigmaX=3.0)
                    gray = cv2.addWeighted(gray, 1.5, gaussian, -0.5, 0)
                    metadata["blur_corrected"] = True
                    logger.info(f"[{doc_id}] Applied blur correction (Laplacian variance={blur_var:.2f} < {self.blur_threshold})")
            except Exception as e:
                logger.debug(f"[{doc_id}] Blur correction failed: {e}")

            # 5. Contrast enhancement (CLAHE) checking std_dev and histogram dynamic range
            low_contrast_detected = False
            try:
                std_dev = float(np.std(gray))
                p5, p95 = np.percentile(gray, (5, 95))
                dynamic_range = float(p95 - p5)

                if std_dev < self.contrast_std_threshold or dynamic_range < self.contrast_dynamic_range_threshold:
                    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                    gray = clahe.apply(gray)
                    metadata["contrast_enhanced"] = True
                    low_contrast_detected = True
                    logger.info(
                        f"[{doc_id}] Applied CLAHE contrast enhancement (std_dev={std_dev:.2f}, dynamic_range={dynamic_range:.2f})"
                    )
            except Exception as e:
                logger.debug(f"[{doc_id}] Contrast enhancement failed: {e}")

            # 6. Denoising with fastNlMeansDenoising before binarization
            try:
                if metadata["contrast_enhanced"] or metadata["blur_corrected"]:
                    gray = cv2.fastNlMeansDenoising(gray, None, h=10, templateWindowSize=7, searchWindowSize=21)
                    metadata["denoised"] = True
            except Exception as e:
                logger.debug(f"[{doc_id}] Denoising failed: {e}")

            # 7. Adaptive binarization on low-contrast/faded pages
            try:
                if low_contrast_detected or metadata["contrast_enhanced"]:
                    gray = cv2.adaptiveThreshold(
                        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 13, 3
                    )
                    metadata["binarized"] = True
                    logger.info(f"[{doc_id}] Applied adaptive binarization on low-contrast scan")
            except Exception as e:
                logger.debug(f"[{doc_id}] Adaptive binarization failed: {e}")

            # Encode back to PNG bytes
            if len(gray.shape) == 2:
                final_img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
            else:
                final_img = gray

            _, encoded_img = cv2.imencode(".png", final_img)
            return encoded_img.tobytes(), metadata

        except Exception as e:
            logger.warning(f"[{doc_id}] Preprocessing OpenCV fallback triggered: {e}")
            return image_bytes, metadata

    @staticmethod
    def _detect_coarse_orientation(gray_img) -> int:
        """Attempts coarse 0/90/180/270 orientation detection via pytesseract OSD."""
        try:
            import pytesseract
            from PIL import Image

            pil_img = Image.fromarray(gray_img)
            osd = pytesseract.image_to_osd(pil_img)
            match = re.search(r"Rotate:\s*(\d+)", osd)
            if match:
                angle = int(match.group(1))
                if angle in (90, 180, 270):
                    return angle
        except Exception:
            pass
        return 0

    @staticmethod
    def _detect_skew_angle(gray_img) -> float:
        """
        Detect skew angle using Hough lines with fallback to minAreaRect on contours.
        """
        try:
            import cv2
            import numpy as np

            edges = cv2.Canny(gray_img, 50, 150, apertureSize=3)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)
            if lines is not None:
                angles = []
                for line in lines:
                    x1, y1, x2, y2 = line[0]
                    angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                    if -45 < angle < 45:
                        angles.append(angle)
                if angles:
                    return float(np.median(angles))

            # Fallback: minAreaRect on thresholded contours
            _, thresh = cv2.threshold(gray_img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            contours, _ = cv2.findContours(thresh, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            contour_angles = []
            for c in contours:
                if cv2.contourArea(c) > 50:
                    rect = cv2.minAreaRect(c)
                    angle = rect[-1]
                    if angle < -45:
                        angle = 90.0 + angle
                    elif angle > 45:
                        angle = angle - 90.0
                    if abs(angle) > 0.5 and abs(angle) < 45.0:
                        contour_angles.append(angle)

            if contour_angles:
                return float(np.median(contour_angles))
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _rotate_image(image, angle: float):
        import cv2
        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated

