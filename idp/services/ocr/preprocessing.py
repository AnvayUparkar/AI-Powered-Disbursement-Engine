from typing import Tuple, Dict, Any, Optional
import io
from idp.core.logging import logger


class OCRImagePreprocessor:
    """Preprocesses images for optimal PP-OCRv6 recognition (deskew, contrast, noise reduction)."""

    def preprocess_image(
        self,
        image_bytes: bytes,
        doc_id: str = "DOC",
        skip_preprocessing: bool = False
    ) -> Tuple[bytes, Dict[str, Any]]:
        """
        Evaluate image characteristics and apply conditional preprocessing.

        Returns:
            Tuple[processed_image_bytes, preprocessing_metadata]
        """
        metadata = {
            "rotation_applied": False,
            "rotation_angle": 0.0,
            "contrast_enhanced": False,
            "grayscale_converted": False,
            "skipped": skip_preprocessing
        }

        if skip_preprocessing:
            return image_bytes, metadata

        try:
            import cv2
            import numpy as np

            # Convert bytes to numpy array
            nparr = np.frombuffer(image_bytes, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if img is None:
                return image_bytes, metadata

            h, w, _ = img.shape

            # 1. Grayscale & Contrast enhancement if low contrast detected
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            std_dev = np.std(gray)

            if std_dev < 35.0:  # Low contrast scan
                # Apply CLAHE (Contrast Limited Adaptive Histogram Equalization)
                clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
                gray = clahe.apply(gray)
                img = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                metadata["contrast_enhanced"] = True

            # 2. Deskew detection using Hough Lines / MinAreaRect
            angle = self._detect_skew_angle(gray)
            if abs(angle) > 1.0 and abs(angle) < 45.0:
                img = self._rotate_image(img, angle)
                metadata["rotation_applied"] = True
                metadata["rotation_angle"] = float(angle)
                logger.info(f"[{doc_id}] Applied deskew rotation of {angle:.2f} degrees")

            # Encode back to PNG bytes
            _, encoded_img = cv2.imencode(".png", img)
            return encoded_img.tobytes(), metadata

        except Exception as e:
            logger.warning(f"[{doc_id}] Preprocessing OpenCV fallback triggered: {e}")
            return image_bytes, metadata

    @staticmethod
    def _detect_skew_angle(gray_img) -> float:
        try:
            import cv2
            import numpy as np
            edges = cv2.Canny(gray_img, 50, 150, apertureSize=3)
            lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=100, minLineLength=100, maxLineGap=10)
            if lines is None:
                return 0.0

            angles = []
            for line in lines:
                x1, y1, x2, y2 = line[0]
                angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
                if -45 < angle < 45:
                    angles.append(angle)

            if angles:
                median_angle = float(np.median(angles))
                return median_angle
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _rotate_image(image, angle: float):
        import cv2
        import numpy as np
        h, w = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return rotated
