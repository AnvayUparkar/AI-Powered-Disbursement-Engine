from typing import List, Tuple, Dict, Any, Optional
import io
import math


def transform_bbox_to_corners(bbox: Dict[str, float]) -> List[List[float]]:
    """
    Transform bounding box from {l, t, r, b} dict format to corner coordinates.
    Order: top-left, top-right, bottom-right, bottom-left.
    (Adapted from PipesHub bbox.py)
    """
    left, top, right, bottom = bbox['l'], bbox['t'], bbox['r'], bbox['b']
    return [
        [left, top],
        [right, top],
        [right, bottom],
        [left, bottom]
    ]


def normalize_corner_coordinates(
    corners: List[List[float]],
    page_width: float,
    page_height: float
) -> List[List[float]]:
    """
    Normalize corner coordinates to [0, 1] range using page dimensions.
    (Adapted from PipesHub bbox.py)
    """
    if page_width <= 0 or page_height <= 0:
        return corners

    normalized = []
    for corner in corners:
        x, y = corner[0], corner[1]
        norm_x = max(0.0, min(1.0, x / page_width))
        norm_y = max(0.0, min(1.0, y / page_height))
        normalized.append([norm_x, norm_y])
    return normalized


def normalize_bbox(bbox: List[float], page_width: float, page_height: float) -> List[float]:
    """
    Normalize bbox [l, t, r, b] to [0.0, 1.0] relative coordinates.
    """
    if page_width <= 0 or page_height <= 0 or len(bbox) < 4:
        return bbox

    l, t, r, b = bbox[0], bbox[1], bbox[2], bbox[3]
    return [
        round(max(0.0, min(1.0, l / page_width)), 4),
        round(max(0.0, min(1.0, t / page_height)), 4),
        round(max(0.0, min(1.0, r / page_width)), 4),
        round(max(0.0, min(1.0, b / page_height)), 4)
    ]


def crop_image_region(
    image_bytes: bytes,
    bbox: List[float],
    page_width: float,
    page_height: float,
    padding_pct: float = 0.05
) -> Optional[bytes]:
    """
    Crop a micro region from an image given a bbox [l, t, r, b] for targeted VLM input.
    """
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        img_w, img_h = img.size

        l, t, r, b = bbox[0], bbox[1], bbox[2], bbox[3]

        # Convert normalized coordinates if l,t,r,b are <= 1.0
        if r <= 1.0 and b <= 1.0 and page_width > 1.0:
            l, t, r, b = l * page_width, t * page_height, r * page_width, b * page_height

        # Apply padding
        width = r - l
        height = b - t
        pad_x = width * padding_pct
        pad_y = height * padding_pct

        crop_l = max(0, int(l - pad_x))
        crop_t = max(0, int(t - pad_y))
        crop_r = min(img_w, int(r + pad_x))
        crop_b = min(img_h, int(b + pad_y))

        if crop_r <= crop_l or crop_b <= crop_t:
            return None

        cropped = img.crop((crop_l, crop_t, crop_r, crop_b))
        out_buf = io.BytesIO()
        cropped.save(out_buf, format="PNG")
        return out_buf.getvalue()
    except Exception:
        return None
