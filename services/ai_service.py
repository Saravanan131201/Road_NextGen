import cv2
import numpy as np
import os
from datetime import datetime


# ─── Class definitions ────────────────────────────────────────────────────────
DAMAGE_LABELS = {
    0: "alligator_cracking",
    1: "lateral_cracking",
    2: "longitudinal_cracking",
    3: "pothole"
}

DAMAGE_DISPLAY = {
    "alligator_cracking":    "Alligator Cracking",
    "lateral_cracking":      "Lateral Cracking",
    "longitudinal_cracking": "Longitudinal Cracking",
    "pothole":               "Pothole"
}

DAMAGE_TEAM_RECOMMENDATION = {
    "alligator_cracking":    "gamma",
    "lateral_cracking":      "beta",
    "longitudinal_cracking": "beta",
    "pothole":               "delta"
}

BOX_COLORS_BGR = {
    "alligator_cracking":    (59,  68,  239),
    "lateral_cracking":      (22, 115, 249),
    "longitudinal_cracking": (36, 191, 251),
    "pothole":               (148, 103, 189)
}

BOX_COLORS_HEX = {
    "alligator_cracking":    "#ef4444",
    "lateral_cracking":      "#f97316",
    "longitudinal_cracking": "#fbbf24",
    "pothole":               "#8b5cf6"
}

SEVERITY_COLORS_BGR = {
    "low":      (34,  197,  94),
    "medium":   (251, 191,  36),
    "high":     (249, 115,  22),
    "critical": (239,  68,  68)
}

SEVERITY_COLORS_HEX = {
    "low":      "#22c55e",
    "medium":   "#fbbf24",
    "high":     "#f97316",
    "critical": "#ef4444"
}

SEVERITY_TEAM_MAP = {
    "low":      "alpha",
    "medium":   "beta",
    "high":     "gamma",
    "critical": "delta"
}

_SEV_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}


# ─── Per-detection severity (single box) ─────────────────────────────────────
def compute_severity_single(confidence: float,
                             area_ratio: float,
                             damage_type: str) -> str:
    """
    Severity for a single bounding box.
    Rule 5: Large pothole (area_ratio > 0.15) with any confidence → critical.
    """
    # Rule 5 — single huge pothole regardless of confidence
    if damage_type == "pothole" and area_ratio > 0.15:
        return "critical"

    weight = 1.2 if damage_type == "pothole" else 1.0
    score  = ((confidence * 0.65) + (min(area_ratio, 1.0) * 0.35)) * weight

    if score > 0.80:
        return "critical"
    elif score > 0.58:
        return "high"
    elif score > 0.38:
        return "medium"
    return "low"


# ─── Aggregate severity across all detections ─────────────────────────────────
def aggregate_severity(detections: list) -> str:
    """
    Apply group-level severity rules AFTER per-box scores are computed.

    Rule 5:  Single pothole with area_ratio > 0.15  → critical
    Rule 6:  More than one pothole detected          → critical
    Rule 7a: 3 non-pothole classes detected          → high
    Rule 7b: More than 3 non-pothole classes         → critical
    Otherwise: worst individual severity wins.
    """
    if not detections:
        return "low"

    potholes     = [d for d in detections if d["damage_type"] == "pothole"]
    non_potholes = [d for d in detections if d["damage_type"] != "pothole"]

    # Rule 6 — multiple potholes
    if len(potholes) > 1:
        return "critical"

    # Rule 5 — single large pothole (already set per-box but enforce here too)
    if len(potholes) == 1 and potholes[0].get("area_ratio", 0) > 0.15:
        return "critical"

    # Rule 7 — non-pothole class count
    non_pothole_types = len(set(d["damage_type"] for d in non_potholes))
    if non_pothole_types > 3:
        return "critical"
    if non_pothole_types == 3:
        return "high"

    # Default — worst individual
    return max(
        (d["severity"] for d in detections),
        key=lambda s: _SEV_RANK.get(s, 0)
    )


def recommend_team(severity: str) -> str:
    return SEVERITY_TEAM_MAP.get(severity, "beta")


# ─── Lazy model loader ────────────────────────────────────────────────────────
_model = None

def load_model():
    global _model
    if _model is not None:
        return _model
    try:
        from ultralytics import YOLO
        from flask import current_app
        model_path = current_app.config.get(
            "YOLO_MODEL_PATH", "models/weights/road_damage_yolo11s_v2.pt"
        )
        if not os.path.exists(model_path):
            print(f"[AI] Model not found at {model_path} — mock mode")
            return None
        _model = YOLO(model_path)
        print(f"[AI] ✅ Model loaded: {model_path}")
        return _model
    except Exception as e:
        print(f"[AI] Model load error: {e}")
        try:
            import torch, functools
            orig = torch.load
            @functools.wraps(orig)
            def _patched(*a, **kw):
                kw["weights_only"] = False
                return orig(*a, **kw)
            torch.load = _patched
            from ultralytics import YOLO
            from flask import current_app
            _model = YOLO(current_app.config.get("YOLO_MODEL_PATH", ""))
            torch.load = orig
            print("[AI] ✅ Model loaded via fallback patch.")
            return _model
        except Exception as e2:
            print(f"[AI] Fallback failed: {e2} — mock mode")
            return None


# ─── Drawing ──────────────────────────────────────────────────────────────────
def _draw_box(img, x1, y1, x2, y2,
              label: str, color_bgr: tuple,
              conf: float, severity: str):
    cv2.rectangle(img, (x1, y1), (x2, y2), color_bgr, 3)
    text = f"{label}  {conf:.0%}"
    font, fscale, fthick, pad = cv2.FONT_HERSHEY_SIMPLEX, 0.52, 2, 5
    (tw, th), bl = cv2.getTextSize(text, font, fscale, fthick)
    ty = max(y1 - 4, th + bl + pad * 2)
    cv2.rectangle(img, (x1, ty - th - bl - pad*2), (x1 + tw + pad*2, ty), color_bgr, -1)
    cv2.putText(img, text, (x1+pad, ty-bl-pad), font, fscale, (255,255,255), fthick, cv2.LINE_AA)
    sev_c  = SEVERITY_COLORS_BGR.get(severity, (34,197,94))
    sev_t  = severity.upper()
    (sw, sh), _ = cv2.getTextSize(sev_t, font, 0.42, 1)
    cv2.rectangle(img, (x2-sw-pad*2, y2), (x2, y2+sh+pad*2), sev_c, -1)
    cv2.putText(img, sev_t, (x2-sw-pad, y2+sh+pad), font, 0.42, (255,255,255), 1, cv2.LINE_AA)



def _draw_legend(img: np.ndarray, damage_types: list):
    if not damage_types:
        return
    unique = list(dict.fromkeys(damage_types))
    h, w   = img.shape[:2]
    font, fscale, pad, row_h = cv2.FONT_HERSHEY_SIMPLEX, 0.42, 6, 20
    box_w  = 170
    box_h  = pad + row_h * len(unique) + pad
    x0, y0 = w - box_w - 10, 10
    cv2.rectangle(img, (x0, y0), (x0+box_w, y0+box_h), (20,20,20), -1)
    cv2.rectangle(img, (x0, y0), (x0+box_w, y0+box_h), (80,80,80), 1)
    for i, dt in enumerate(unique):
        color = BOX_COLORS_BGR.get(dt, (128,128,128))
        ry    = y0 + pad + i * row_h
        cv2.rectangle(img, (x0+6, ry+2), (x0+20, ry+14), color, -1)
        lbl = DAMAGE_DISPLAY.get(dt, dt.replace("_"," ").title())
        cv2.putText(img, lbl, (x0+26, ry+13), font, fscale, (230,230,230), 1, cv2.LINE_AA)


def _mock_detections(img: np.ndarray) -> list:
    h, w = img.shape[:2]
    mocks = [
        {"damage_type":"pothole","display_name":"Pothole","confidence":0.82,
         "bbox":[int(w*.10),int(h*.20),int(w*.42),int(h*.60)],"area_ratio":0.18},
        {"damage_type":"alligator_cracking","display_name":"Alligator Cracking",
         "confidence":0.71,"bbox":[int(w*.50),int(h*.30),int(w*.85),int(h*.70)],
         "area_ratio":0.095},
    ]
    for d in mocks:
        x1,y1,x2,y2 = d["bbox"]
        sev = compute_severity_single(d["confidence"], d["area_ratio"], d["damage_type"])
        d["severity"]     = sev
        d["color_hex"]    = BOX_COLORS_HEX[d["damage_type"]]
        d["severity_hex"] = SEVERITY_COLORS_HEX[sev]
        _draw_box(img, x1,y1,x2,y2, d["display_name"],
                  BOX_COLORS_BGR[d["damage_type"]], d["confidence"], sev)
    return mocks


# ─── Main inference ───────────────────────────────────────────────────────────
def run_inference(image_bytes: bytes) -> dict:
    nparr = np.frombuffer(image_bytes, np.uint8)
    img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return {"error": "Invalid or corrupt image file.", "detections": []}

    h, w     = img.shape[:2]
    img_area = max(h * w, 1)
    annotated  = img.copy()
    detections = []
    model = load_model()

    if model is not None:
        try:
            results = model(annotated, conf=0.25, iou=0.45, verbose=False)
            for result in results:
                if result.boxes is None:
                    continue
                for box in result.boxes:
                    x1,y1,x2,y2 = map(int, box.xyxy[0].tolist())
                    conf   = float(box.conf[0])
                    cls_id = int(box.cls[0])
                    damage_type  = DAMAGE_LABELS.get(cls_id, "pothole")
                    display_name = DAMAGE_DISPLAY.get(damage_type, damage_type)
                    box_area     = max((x2-x1)*(y2-y1), 0)
                    area_ratio   = box_area / img_area
                    severity     = compute_severity_single(conf, area_ratio, damage_type)
                    color_bgr    = BOX_COLORS_BGR.get(damage_type, (128,128,128))
                    _draw_box(annotated, x1,y1,x2,y2, display_name, color_bgr, conf, severity)
                    detections.append({
                        "damage_type":  damage_type,
                        "display_name": display_name,
                        "confidence":   round(conf, 3),
                        "severity":     severity,
                        "bbox":         [x1,y1,x2,y2],
                        "area_ratio":   round(area_ratio, 5),
                        "color_hex":    BOX_COLORS_HEX.get(damage_type, "#ef4444"),
                        "severity_hex": SEVERITY_COLORS_HEX.get(severity, "#22c55e")
                    })
        except Exception as e:
            print(f"[AI] Inference error: {e}")
            detections = _mock_detections(annotated)
    else:
        detections = _mock_detections(annotated)

    if detections:
        detections.sort(key=lambda d: d["confidence"], reverse=True)

    # Apply group-level severity rules (6,7)
    overall_sev = aggregate_severity(detections)

    # Re-stamp per-box severity if group forces higher
    for d in detections:
        if _SEV_RANK.get(overall_sev,0) > _SEV_RANK.get(d["severity"],0):
            d["severity"]     = overall_sev
            d["severity_hex"] = SEVERITY_COLORS_HEX[overall_sev]

    detected_types     = list(dict.fromkeys(d["damage_type"] for d in detections))
    display_names_list = [DAMAGE_DISPLAY.get(dt,dt) for dt in detected_types]

    _draw_legend(annotated, detected_types)

    primary      = detections[0] if detections else {}
    primary_type = primary.get("damage_type", "unknown")
    primary_sev  = primary.get("severity", "low")
    rec_team     = recommend_team(overall_sev)

    _, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 92])

    return {
        "detections":            detections,
        "damage_types":          detected_types,
        "display_names":         display_names_list,
        "primary_damage_type":   primary_type,
        "primary_display_name":  DAMAGE_DISPLAY.get(primary_type, primary_type),
        "primary_confidence":    round(primary.get("confidence", 0.0), 3),
        "primary_severity":      primary_sev,
        "overall_severity":      overall_sev,
        "recommended_team":      rec_team,
        "total_detections":      len(detections),
        "annotated_image_bytes": buf.tobytes(),
        "processed_at":          datetime.utcnow().isoformat(),
        "model_classes":         list(DAMAGE_LABELS.values())
    }