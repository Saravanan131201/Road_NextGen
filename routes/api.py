from flask import Blueprint, request, jsonify, session, current_app
from utils.decorators import login_required
from models.report import Report
from models.user import User
from services.ai_service import run_inference, DAMAGE_DISPLAY, SEVERITY_TEAM_MAP
from services.cloudinary_service import upload_image, upload_road_image, upload_annotated_image
from services.notification_service import (
    get_user_notifications, mark_read, mark_all_read, create_notification
)
from utils.helpers import serialize_list, serialize_doc
from datetime import datetime

api_bp = Blueprint("api", __name__)


# ── Cloudinary signature ──────────────────────────────────────────────────────
@api_bp.route("/cloudinary/signature", methods=["POST"])
@login_required
def cloudinary_signature():
    from services.cloudinary_service import generate_upload_signature
    return jsonify(generate_upload_signature(folder="road_reports"))


# ── AI Detection (multi-class, all boxes) ─────────────────────────────────────
@api_bp.route("/ai/detect", methods=["POST"])
@login_required
def ai_detect():
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    file        = request.files["image"]
    image_bytes = file.read()
    if not image_bytes:
        return jsonify({"error": "Empty image file"}), 400

    # Upload original (signed)
    original_url       = ""
    original_public_id = ""
    original_upload = upload_road_image(image_bytes, subfolder="originals")
    if original_upload:
        original_url       = original_upload["url"]
        original_public_id = original_upload["public_id"]
    else:
        current_app.logger.warning("[API] Cloudinary original upload skipped")

    # Run YOLO inference
    results = run_inference(image_bytes)
    if "error" in results:
        return jsonify({"error": results["error"]}), 500

    # Upload annotated with AI watermark (signed)
    annotated_url        = ""
    annotated_public_id  = ""
    annotated_bytes = results.pop("annotated_image_bytes", None)
    if annotated_bytes:
        ann = upload_annotated_image(annotated_bytes)
        if ann:
            annotated_url       = ann["url"]
            annotated_public_id = ann["public_id"]

    return jsonify({
        "success":              True,
        "original_url":         original_url,
        "original_public_id":   original_public_id,
        "annotated_url":        annotated_url,
        "annotated_public_id":  annotated_public_id,
        # Multi-class fields
        "detections":           results["detections"],
        "damage_types":         results["damage_types"],
        "display_names":        results["display_names"],
        # Primary (highest confidence)
        "primary_damage_type":  results["primary_damage_type"],
        "primary_display_name": results["primary_display_name"],
        "primary_confidence":   results["primary_confidence"],
        "primary_severity":     results["primary_severity"],
        # Aggregated
        "overall_severity":     results["overall_severity"],
        "recommended_team":     results["recommended_team"],
        "total_detections":     results["total_detections"],
        "model_classes":        results["model_classes"],
        "processed_at":         results["processed_at"]
    })


# ── Submit report (multi-class) ───────────────────────────────────────────────
@api_bp.route("/reports", methods=["POST"])
@login_required
def submit_report():
    data = request.get_json()

    # damage_types is always a list (multi-class AI or manual single)
    damage_types = data.get("damage_types", [])
    if isinstance(damage_types, str):
        damage_types = [damage_types]   # backward-compat

    # display_names derived server-side if frontend didn't send them
    display_names = data.get("display_names", [])
    if not display_names:
        display_names = [
            DAMAGE_DISPLAY.get(dt, dt.replace("_", " ").title())
            for dt in damage_types
        ]

    report_data = {
        "reporter_id":     session["user_id"],
        "report_type":     data.get("report_type", "manual"),
        # No singular damage_type stored anymore
        "damage_types":    damage_types,
        "display_names":   display_names,
        "severity":        data.get("severity", "medium"),
        "description":     data.get("description", ""),
        "coordinates":     data.get("coordinates", [0, 0]),
        "address":         data.get("address", ""),
        "city":            data.get("city", ""),
        "original_image":           data.get("original_url", ""),
        "original_public_id":       data.get("original_public_id", ""),
        "annotated_image":          data.get("annotated_url", ""),
        "annotated_public_id":      data.get("annotated_public_id", ""),
        "ai_results":      data.get("ai_results", {}),
    }

    report = Report.create(report_data)

    # Notify admins — use display_names for readable labels
    sev = report_data["severity"]
    admins, _ = User.get_all(role="admin")
    for admin in admins:
        create_notification(
            admin["_id"],
            "New Report Submitted",
            f"{sev.upper()} severity — {', '.join(display_names)}",
            "warning" if sev in ["high", "critical"] else "info",
            {"report_id": str(report["_id"])}
        )

    if sev == "critical":
        from app import socketio
        damage_display_str = ', '.join(display_names)
        socketio.emit("critical_alert", {
            "report_id":    str(report["_id"]),
            "damage_types": damage_display_str,
            "severity":     sev,
            "location":     report_data.get("address", "Unknown")
        })

    return jsonify({"success": True, "report_id": str(report["_id"])})


# ── Nearby reports ────────────────────────────────────────────────────────────
@api_bp.route("/reports/nearby", methods=["GET"])
@login_required
def nearby_reports():
    lat    = float(request.args.get("lat", 0))
    lng    = float(request.args.get("lng", 0))
    radius = float(request.args.get("radius", 5))
    reports = Report.get_nearby(lat, lng, radius)
    return jsonify({"reports": serialize_list(reports)})


# ── All reports for map (includes damage_types list) ─────────────────────────
@api_bp.route("/reports/all", methods=["GET"])
@login_required
def all_reports_geo():
    reports, _ = Report.get_all(
        {"status": {"$nin": ["rejected", "completed"]}}, per_page=500
    )
    return jsonify({"reports": serialize_list(reports)})


# ── Notifications ─────────────────────────────────────────────────────────────
@api_bp.route("/notifications", methods=["GET"])
@login_required
def get_notifications():
    notifs  = get_user_notifications(session["user_id"])
    unread  = sum(1 for n in notifs if not n.get("is_read"))
    return jsonify({
        "notifications": serialize_list(notifs),
        "unread":        unread
    })


@api_bp.route("/notifications/<notif_id>/read", methods=["POST"])
@login_required
def read_notification(notif_id):
    mark_read(notif_id)
    return jsonify({"success": True})


@api_bp.route("/notifications/read-all", methods=["POST"])
@login_required
def read_all_notifications():
    mark_all_read(session["user_id"])
    return jsonify({"success": True})


# ── User settings ─────────────────────────────────────────────────────────────
@api_bp.route("/user/settings", methods=["POST"])
@login_required
def update_settings():
    data = request.get_json()
    User.update(session["user_id"], {"settings": data})
    return jsonify({"success": True})


# ── Socket events ─────────────────────────────────────────────────────────────
def register_socket_events(socketio):
    from flask_socketio import join_room

    @socketio.on("connect")
    def on_connect():
        print("[Socket] Client connected")

    @socketio.on("disconnect")
    def on_disconnect():
        print("[Socket] Client disconnected")

    @socketio.on("join_room")
    def on_join(data):
        room = data.get("room", "general")
        join_room(room)
        print(f"[Socket] Joined room: {room}")

    # Client sends {"user_id": "<their id>"} right after connecting
    # so the server places them in their personal room for targeted notifications
    @socketio.on("join_user_room")
    def on_join_user_room(data):
        user_id = data.get("user_id", "")
        if user_id:
            join_room(str(user_id))
            print(f"[Socket] User {user_id} joined personal room")

    @socketio.on("ping_dashboard")
    def on_ping(_):
        socketio.emit("pong_dashboard", {"timestamp": datetime.utcnow().isoformat()})