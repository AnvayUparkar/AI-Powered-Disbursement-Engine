"""Image normalization utilities — converts TIF/TIFF images to standard PNG for OCR ingestion."""
import logging
import tempfile
from pathlib import Path
from PIL import Image

logger = logging.getLogger("disbursement_pipeline.image_normalizer")


def ensure_png_for_idp(file_path: Path | str) -> Path:
    """Converts .tif/.tiff images to standard RGB .png to ensure safe IDP OCR ingestion.
    
    Returns original path unchanged for non-TIFF documents (PDF, PNG, JPG, XML).
    """
    path = Path(file_path)
    if not path.exists():
        return path

    if path.suffix.lower() not in (".tif", ".tiff"):
        return path

    try:
        # Create a temp directory or target path for converted PNG
        tmp_dir = Path(tempfile.gettempdir()) / "idp_converted_images"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        converted_path = tmp_dir / f"{path.stem}.png"

        with Image.open(path) as img:
            rgb_img = img.convert("RGB")
            rgb_img.save(converted_path, format="PNG")

        logger.info("Converted TIFF image %s -> %s for IDP processing", path.name, converted_path.name)
        return converted_path
    except Exception as exc:
        logger.warning("Failed converting TIFF %s to PNG: %s. Using original file.", path, exc)
        return path
