import cloudinary
import cloudinary.uploader
import cloudinary.utils
import hashlib
import time
import cv2
import numpy as np
from flask import current_app


def _ok() -> bool:
    cfg = cloudinary.config()
    if not cfg.cloud_name or cfg.cloud_name in ("", "your-cloud-name"):
        current_app.logger.error("[Cloudinary] Not configured.")
        return False
    return True


def generate_upload_signature(folder: str = "road_reports",
                               public_id: str = None) -> dict:
    timestamp  = int(time.time())
    params     = {"timestamp": timestamp, "folder": folder}
    if public_id:
        params["public_id"] = public_id
    param_str  = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    api_secret = current_app.config["CLOUDINARY_API_SECRET"]
    signature  = hashlib.sha256(f"{param_str}{api_secret}".encode()).hexdigest()
    return {
        "signature":  signature,
        "timestamp":  timestamp,
        "api_key":    current_app.config["CLOUDINARY_API_KEY"],
        "cloud_name": current_app.config["CLOUDINARY_CLOUD_NAME"],
        "folder":     folder
    }


def _signed_upload(image_bytes: bytes,
                   folder: str,
                   public_id: str = None,
                   resource_type: str = "image") -> dict | None:
    """
    Core signed upload used by ALL upload helpers.
    Generates signature server-side, uploads via signed API call.
    Stores and returns public_id for MongoDB.
    """
    if not _ok():
        return None
    try:
        timestamp  = int(time.time())
        params     = {"folder": folder, "timestamp": timestamp, "overwrite": True}
        if public_id:
            params["public_id"] = public_id

        # Build signature from sorted param string (exclude resource_type)
        sign_params = {k: v for k, v in params.items() if k not in ("resource_type",)}
        param_str   = "&".join(f"{k}={v}" for k, v in sorted(sign_params.items()))
        api_secret  = current_app.config["CLOUDINARY_API_SECRET"]
        signature   = hashlib.sha256(f"{param_str}{api_secret}".encode()).hexdigest()

        result = cloudinary.uploader.upload(
            image_bytes,
            signature=signature,
            api_key=current_app.config["CLOUDINARY_API_KEY"],
            resource_type=resource_type,
            **params
        )
        return {
            "url":       result.get("secure_url"),
            "public_id": result.get("public_id"),
            "width":     result.get("width"),
            "height":    result.get("height"),
            "format":    result.get("format"),
            "bytes":     result.get("bytes"),
        }
    except cloudinary.exceptions.Error as e:
        current_app.logger.error(f"[Cloudinary] Upload error: {e}")
        return None
    except Exception as e:
        current_app.logger.error(f"[Cloudinary] Unexpected error: {e}")
        return None


# ── Watermark helpers ─────────────────────────────────────────────────────────

def _apply_watermark_ai(image_bytes: bytes) -> bytes:
    """Add 'Road NextGen | YOLO v11 s' watermark at bottom-left for AI/annotated images."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return image_bytes
    h, w  = img.shape[:2]
    text  = "Road NextGen | YOLO v11 s"
    font, fscale, fthick = cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2
    (tw, th), bl = cv2.getTextSize(text, font, fscale, fthick)
    x = 10
    y = h - 10
    # Shadow
    cv2.putText(img, text, (x+1, y+1), font, fscale, (0, 0, 0), fthick + 1, cv2.LINE_AA)
    # Text
    cv2.putText(img, text, (x, y), font, fscale, (220, 220, 220), fthick, cv2.LINE_AA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes()


def _apply_watermark_brand(image_bytes: bytes) -> bytes:
    """Add 'Road NextGen' watermark at bottom-left for admin/worker photos."""
    nparr = np.frombuffer(image_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return image_bytes
    h, w  = img.shape[:2]
    text  = "Road NextGen"
    font, fscale, fthick = cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2
    x = 10
    y = h - 10
    # Shadow
    cv2.putText(img, text, (x+1, y+1), font, fscale, (0, 0, 0), fthick + 1, cv2.LINE_AA)
    # Text
    cv2.putText(img, text, (x, y), font, fscale, (220, 220, 220), fthick, cv2.LINE_AA)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return buf.tobytes()


# ── Public upload functions (all use _signed_upload) ─────────────────────────

def upload_image(image_bytes: bytes,
                 folder: str = "road_reports",
                 public_id: str = None,
                 resource_type: str = "image") -> dict | None:
    """Generic signed upload — used for road originals and repair photos."""
    return _signed_upload(image_bytes, folder, public_id, resource_type)


def upload_road_image(image_bytes: bytes, subfolder: str = "originals") -> dict | None:
    """Upload original road image (no watermark — raw evidence)."""
    return _signed_upload(image_bytes, folder=f"road_reports/{subfolder}")


def upload_annotated_image(image_bytes: bytes, report_id: str = "") -> dict | None:
    """Upload AI-annotated image with 'Road NextGen | YOLO v11 s' watermark."""
    watermarked = _apply_watermark_ai(image_bytes)
    pid = f"annotated_{report_id}" if report_id else None
    return _signed_upload(watermarked, folder="road_reports/annotated", public_id=pid)


def upload_admin_photo(image_bytes: bytes, name: str = "") -> dict | None:
    """Upload admin profile photo with 'Road NextGen' watermark."""
    watermarked = _apply_watermark_brand(image_bytes)
    ts   = int(time.time())
    slug = name.lower().replace(" ", "_")[:20] if name else "admin"
    pid  = f"admin_{slug}_{ts}"
    return _signed_upload(watermarked, folder="admins/photos", public_id=pid)


def upload_crew_head_photo(image_bytes: bytes,
                           team_key: str,
                           name: str = "") -> dict | None:
    """Upload crew head profile photo with 'Road NextGen' watermark."""
    watermarked = _apply_watermark_brand(image_bytes)
    ts   = int(time.time())
    slug = name.lower().replace(" ", "_")[:20] if name else "head"
    pid  = f"{team_key}_head_{slug}_{ts}"
    return _signed_upload(watermarked, folder=f"crew/heads/{team_key}", public_id=pid)


def upload_worker_photo(image_bytes: bytes,
                        team_key: str,
                        worker_name: str = "") -> dict | None:
    """Upload daily-wage worker photo with 'Road NextGen' watermark."""
    watermarked = _apply_watermark_brand(image_bytes)
    ts   = int(time.time())
    slug = worker_name.lower().replace(" ", "_")[:20] if worker_name else "worker"
    pid  = f"{team_key}_{slug}_{ts}"
    return _signed_upload(watermarked, folder=f"workers/photos/{team_key}", public_id=pid)


def upload_repair_photo(image_bytes: bytes,
                        task_id: str,
                        photo_type: str = "before") -> dict | None:
    """Upload before/after repair photo (no watermark — field documentation)."""
    pid = f"repair_{task_id}_{photo_type}"
    return _signed_upload(image_bytes, folder="repairs", public_id=pid)


def delete_image(public_id: str) -> bool:
    if not _ok():
        return False
    try:
        cloudinary.uploader.destroy(public_id, invalidate=True)
        return True
    except Exception as e:
        current_app.logger.error(f"[Cloudinary] Delete error: {e}")
        return False


def test_connection() -> bool:
    try:
        import cloudinary.api
        cloudinary.api.ping()
        print("✅ Cloudinary connection OK")
        return True
    except Exception as e:
        print(f"❌ Cloudinary error: {e}")
        return False