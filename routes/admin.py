from flask import Blueprint, render_template, session, request, jsonify, Response, redirect, url_for
from utils.decorators import admin_required
from models.report import Report
from models.user import User
from models.task import Task
from models.team import TeamHead, Worker, TEAMS, WORKER_STATUS
from services.notification_service import create_notification, get_user_notifications
from services.cloudinary_service import upload_admin_photo, upload_crew_head_photo, delete_image

from services.ai_service import SEVERITY_TEAM_MAP, DAMAGE_DISPLAY
from utils.helpers import serialize_list, serialize_doc, time_ago, _fmt_dt
from datetime import datetime
from bson import ObjectId
import bcrypt, csv, io, base64, re

from extensions import mongo

admin_bp = Blueprint("admin", __name__)


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/dashboard")
@admin_required
def dashboard():
    stats      = Report.get_stats()
    recent, _  = Report.get_all(per_page=10)
    team_heads = TeamHead.get_all()
    now_hour   = datetime.utcnow().hour
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        reports=serialize_list(recent),
        team_heads=serialize_list(team_heads),
        teams=TEAMS,
        now_hour=now_hour,
        time_ago=time_ago
    )


# ══════════════════════════════════════════════════════════════════════════════
#  REPORTS
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/reports")
@admin_required
def reports():
    page     = int(request.args.get("page", 1))
    status   = request.args.get("status", "")
    severity = request.args.get("severity", "")
    search   = request.args.get("search", "").strip()
    mode     = request.args.get("mode", "")   # 'ai' or 'manual'

    query = {}
    if status:
        query["status"] = status
    if severity:
        query["severity"] = severity
    if mode in ("ai", "manual"):
        query["report_type"] = mode
    if search:
        or_clauses = [
            {"damage_types":      {"$regex": search, "$options": "i"}},
            {"location.address": {"$regex": search, "$options": "i"}},
        ]
        try:
            or_clauses.append({"_id": ObjectId(search)})
        except Exception:
            pass  # not a valid ObjectId, skip it
        query["$or"] = or_clauses

    reps, total = Report.get_all(query, page=page, per_page=15)
    team_heads  = TeamHead.get_all()

    for r in reps:
        r["_worker_count"] = 0
        r["_worker_names"] = []
        task_for_report = Task.find_by_report_id(str(r["_id"]))
        if task_for_report:
            wids = task_for_report.get("assigned_workers", [])
            r["_worker_count"] = len(wids)
            r["_worker_names"] = [
                {"name": w["name"], "photo_url": w.get("photo_url", "")}
                for wid in wids
                for w in [Worker.find_by_id(wid)] if w
            ]

    return render_template(
        "admin/reports.html",
        reports=serialize_list(reps),
        team_heads=serialize_list(team_heads),
        teams=TEAMS,
        total=total, page=page,
        time_ago=time_ago,
        severity_team_map=SEVERITY_TEAM_MAP,
        damage_display=DAMAGE_DISPLAY,
        current_search=search,
        current_status=status,
        current_severity=severity,
        current_mode=mode,
    )


@admin_bp.route("/reports/export/csv")
@admin_required
def export_reports_csv():
    # Build query same way as reports() route
    status   = request.args.get("status", "")
    severity = request.args.get("severity", "")
    search   = request.args.get("search", "").strip()
    mode     = request.args.get("mode", "")

    query = {}
    if status:
        query["status"] = status
    if severity:
        query["severity"] = severity
    if mode in ("ai", "manual"):
        query["report_type"] = mode
    if search:
            or_clauses = [
                {"damage_types":     {"$regex": search, "$options": "i"}},
                {"display_names":    {"$regex": search, "$options": "i"}},
                {"location.address": {"$regex": search, "$options": "i"}},
            ]
            try:
                or_clauses.append({"_id": ObjectId(search)})
            except Exception:
                pass
            query["$or"] = or_clauses

    # Fetch ALL matching reports (no pagination)
    reports = list(mongo.db.reports.find(query).sort("created_at", -1))

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "Report ID", "Report Type", "Damage Types", "Severity", "Status",
        "Address", "Latitude", "Longitude",
        "Reporter ID", "Created At",
        "Task ID", "Assigned Team", "Team Head Name", "Team Head Email", "Team Head Phone",
        "Workers", "Worker Count", "Priority", "Due Date",
        "Task Status", "Started At", "Completed At", "Notes",
        "AI Detections Count", "Original Image", "Annotated Image"
    ])

    for r in reports:
        report_id  = str(r["_id"])
        loc        = r.get("location", {})
        coords     = loc.get("coordinates", [None, None])
        created_at = r.get("created_at", "")
        if created_at:
            created_at = _fmt_dt(created_at)

        # Damage types
    
        damage_label = ", ".join(
            r.get("display_names") or
            [DAMAGE_DISPLAY.get(dt, dt.replace("_"," ").title()) for dt in r.get("damage_types", [])]
        ) or "—"
        # AI detection count
        ai_detections = ""
        if r.get("ai_results") and r["ai_results"].get("detections"):
            ai_detections = len(r["ai_results"]["detections"])

        # Images
        images       = r.get("images", {})
        original_img = images.get("original", "")
        annotated_img= images.get("annotated", "")

        # Task info
        task = Task.find_by_report_id(report_id)
        task_id = assigned_team = team_head_name = team_head_email = team_head_phone = ""
        workers_str = worker_count = priority = due_date = ""
        task_status = started_at = completed_at = notes = ""

        if task:
            task_id      = str(task["_id"])
            assigned_team= TEAMS.get(task.get("assigned_team", ""), {}).get("name", task.get("assigned_team", ""))
            priority     = task.get("priority", "")
            due_date     = task.get("due_date", "") or ""
            task_status  = task.get("status", "")
            notes        = task.get("notes", "")

            if task.get("started_at"):
                started_at = _fmt_dt(task["started_at"])
            if task.get("completed_at"):
                completed_at = _fmt_dt(task["completed_at"])

            # Team head
            if task.get("team_head_id"):
                th = mongo.db.users.find_one({"_id": ObjectId(task["team_head_id"])})
                if th:
                    team_head_name  = th.get("name", "")
                    team_head_email = th.get("email", "")
                    team_head_phone = th.get("phone", "")

            # Workers
            worker_ids  = task.get("assigned_workers", [])
            worker_count= len(worker_ids)
            worker_names= []
            for wid in worker_ids:
                w = Worker.find_by_id(wid)
                if w:
                    worker_names.append(f"{w['name']} ({w.get('phone', '')})")
            workers_str = "; ".join(worker_names)

        writer.writerow([
            report_id,
            r.get("report_type", ""),
            damage_label,
            r.get("severity", ""),
            r.get("status", ""),
            loc.get("address", ""),
            coords[1] if len(coords) > 1 else "",  # lat
            coords[0] if len(coords) > 0 else "",  # lng
            r.get("reporter_id", ""),
            created_at,
            task_id,
            assigned_team,
            team_head_name,
            team_head_email,
            team_head_phone,
            workers_str,
            worker_count,
            priority,
            due_date,
            task_status,
            started_at,
            completed_at,
            notes,
            ai_detections,
            original_img,
            annotated_img,
        ])

    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=RoadNextGen_reports.csv"}
    )


@admin_bp.route("/reports/<report_id>/export/csv")
@admin_required
def export_single_report_csv(report_id):
    try:
        r = mongo.db.reports.find_one({"_id": ObjectId(report_id)})
    except Exception:
        return "Invalid report ID", 400
    if not r:
        return "Report not found", 404

    output = io.StringIO()
    writer = csv.writer(output)

    # ── Report section ──
    writer.writerow(["=== REPORT DETAILS ==="])
    writer.writerow(["Field", "Value"])

    loc    = r.get("location", {})
    coords = loc.get("coordinates", [None, None])
    images = r.get("images", {})

    damage_types = ", ".join(r.get("display_names") or r.get("damage_types") or [r.get("damage_type", "")])

    # AI detection breakdown
    ai_breakdown = ""
    if r.get("ai_results") and r["ai_results"].get("detections"):
        from collections import Counter
        counts  = Counter(d.get("damage_type", "unknown") for d in r["ai_results"]["detections"])
        ai_breakdown = "; ".join(f"{DAMAGE_DISPLAY.get(k, k.replace('_',' ').title())}({v})" for k, v in counts.items())

    created_at = r.get("created_at", "")
    if created_at:
        created_at = _fmt_dt(created_at)

    writer.writerow(["Report ID",    str(r["_id"])])
    writer.writerow(["Report Type",  r.get("report_type", "")])
    writer.writerow(["Damage Types", damage_types])
    writer.writerow(["AI Breakdown", ai_breakdown])
    writer.writerow(["Severity",     r.get("severity", "")])
    writer.writerow(["Status",       r.get("status", "")])
    writer.writerow(["Address",      loc.get("address", "")])
    writer.writerow(["Latitude",     coords[1] if len(coords) > 1 else ""])
    writer.writerow(["Longitude",    coords[0] if len(coords) > 0 else ""])
    writer.writerow(["Reporter ID",  r.get("reporter_id", "")])
    writer.writerow(["Description",  r.get("description", "")])
    writer.writerow(["Created At",   created_at])
    writer.writerow(["Original Image",   images.get("original", "")])
    writer.writerow(["Annotated Image",  images.get("annotated", "")])
    writer.writerow(["Repair Before",    images.get("repair_before", "")])
    writer.writerow(["Repair After",     images.get("repair_after", "")])

    # ── Task section ──
    writer.writerow([])
    writer.writerow(["=== TASK DETAILS ==="])

    task = Task.find_by_report_id(report_id)
    if not task:
        writer.writerow(["No task assigned yet."])
    else:
        assigned_team = TEAMS.get(task.get("assigned_team", ""), {}).get("name", task.get("assigned_team", ""))
        priority      = task.get("priority", "")
        due_date      = task.get("due_date", "") or ""
        task_status   = task.get("status", "")
        notes         = task.get("notes", "")

        started_at   = _fmt_dt(task["started_at"]) if task.get("started_at")   else ""
        completed_at = _fmt_dt(task["completed_at"]) if task.get("completed_at") else ""
        created_at_t = _fmt_dt(task["created_at"]) if task.get("created_at")   else ""

        writer.writerow(["Task ID",       str(task["_id"])])
        writer.writerow(["Assigned Team", assigned_team])
        writer.writerow(["Priority",      priority])
        writer.writerow(["Due Date",      due_date])
        writer.writerow(["Task Status",   task_status])
        writer.writerow(["Assigned At",   created_at_t])
        writer.writerow(["Started At",    started_at])
        writer.writerow(["Completed At",  completed_at])
        writer.writerow(["Notes",         notes])

        # ── Team Head section ──
        writer.writerow([])
        writer.writerow(["=== TEAM HEAD ==="])
        if task.get("team_head_id"):
            th = mongo.db.users.find_one({"_id": ObjectId(task["team_head_id"])})
            if th:
                writer.writerow(["Name",  th.get("name", "")])
                writer.writerow(["Email", th.get("email", "")])
                writer.writerow(["Phone", th.get("phone", "")])
                writer.writerow(["Team",  assigned_team])
            else:
                writer.writerow(["Team head record not found."])
        else:
            writer.writerow(["No team head assigned."])

        # ── Workers section ──
        writer.writerow([])
        writer.writerow(["=== ASSIGNED WORKERS ==="])
        worker_ids = task.get("assigned_workers", [])
        if not worker_ids:
            writer.writerow(["No workers assigned."])
        else:
            writer.writerow(["#", "Name", "Phone", "Address", "Status"])
            for i, wid in enumerate(worker_ids, 1):
                w = Worker.find_by_id(wid)
                if w:
                    writer.writerow([
                        i,
                        w.get("name", ""),
                        w.get("phone", ""),
                        w.get("address", ""),
                        w.get("status", ""),
                    ])

    output.seek(0)
    fname = f"report_{report_id}.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

@admin_bp.route("/api/reports/<report_id>/verify", methods=["POST"])
@admin_required
def verify_report(report_id):
    Report.update_status(report_id, "verified")

    # Notify citizen
    report = mongo.db.reports.find_one({"_id": ObjectId(report_id)})
    if report and report.get("reporter_id"):
        create_notification(
            report["reporter_id"],
            "✅ Your Report Has Been Verified",
            "Your road damage report has been reviewed and verified by our admin team. "
            "A repair crew will be assigned shortly.",
            "success",
            {"report_id": report_id}
        )

    return jsonify({"success": True})


@admin_bp.route("/api/reports/<report_id>/reject", methods=["POST"])
@admin_required
def reject_report(report_id):
    data        = request.get_json()
    reason      = data.get("reason", "No reason provided")
    reporter_id = data.get("reporter_id", "")
    notify      = data.get("notify_reporter", False)

    Report.update_status(report_id, "rejected", {"rejection_reason": reason})

    if notify and reporter_id:
        create_notification(
            reporter_id,
            "Your Report Was Rejected",
            f"Reason: {reason}",
            "critical",
            {"report_id": report_id}
        )

    return jsonify({"success": True})


@admin_bp.route("/api/reports/<report_id>/assign", methods=["POST"])
@admin_required
def assign_report(report_id):
    data       = request.get_json()
    team_key   = data.get("team", "").lower()
    priority   = data.get("priority", "medium")
    due_date_s = data.get("due_date", "")
    notes      = data.get("notes", "")

    if team_key not in TEAMS:
        return jsonify({"error": "Invalid team."}), 400

    team_head = TeamHead.find_by_team(team_key)
    if not team_head:
        return jsonify({
            "error": f"No team head found for {TEAMS[team_key]['name']}. "
                     f"Please create a team head first."
        }), 404

    due_date = None
    if due_date_s:
        try:
            due_date = datetime.strptime(due_date_s, "%Y-%m-%d")
        except ValueError:
            pass

    task = Task.create({
        "report_id":     report_id,
        "assigned_team": team_key,
        "team_head_id":  str(team_head["_id"]),
        "assigned_by":   session["user_id"],
        "priority":      priority,
        "due_date":      due_date,
        "notes":         notes,
    })

    Report.update_status(report_id, "assigned", {
        "assigned_crew_id": str(team_head["_id"]),
        "assigned_at":      datetime.utcnow()
    })

    #Notify Team Head
    create_notification(
        team_head["_id"],
        "New Task Assigned",
        f"Your team has been assigned a repair task — Priority: {priority.upper()}",
        "warning",
        {"task_id": str(task["_id"]), "report_id": report_id}
    )

    # Notify citizen
    report = mongo.db.reports.find_one({"_id": ObjectId(report_id)})
    if report and report.get("reporter_id"):
        due_date_str = ""
        if due_date:
            due_date_str = f" The expected completion date is {due_date.strftime('%d %b %Y')}."

        damage_label = ", ".join(
            report.get("display_names") or
            [DAMAGE_DISPLAY.get(dt, dt.replace("_", " ").title())
             for dt in report.get("damage_types", [])]
        ) or "road damage"

        location_str = ""
        if report.get("location"):
            location_str = report["location"].get("address", "")

        create_notification(
            user_id=report["reporter_id"],
            title=f"🔧 {TEAMS[team_key]['name']} - Assigned to Your Report",
            message=(
            f"The {TEAMS[team_key]['name']} has been assigned to fix your "
            f"{damage_label} report"
            + (f" at {location_str}" if location_str else "")
            + f". Priority: {priority.upper()}."
            + due_date_str
            ),
            notif_type="info",
            data={"task_id": str(task["_id"]), "report_id": report_id}
        )


    from app import socketio
    socketio.emit("task_assigned", {
        "team":      TEAMS[team_key]["name"],
        "task_id":   str(task["_id"]),
        "report_id": report_id,
        "priority":  priority
    })

    return jsonify({"success": True, "task_id": str(task["_id"])})


@admin_bp.route("/api/reports/<report_id>/severity", methods=["POST"])
@admin_required
def update_severity(report_id):
    data        = request.get_json()
    new_sev     = data.get("severity")
    reason      = data.get("reason", "")
    notify      = data.get("notify_reporter", False)
    reporter_id = data.get("reporter_id", "")

    if not new_sev:
        return jsonify({"error": "Severity required"}), 400

    Report.update(report_id, {"severity": new_sev})

    if notify and reporter_id:
        create_notification(
            reporter_id,
            "Report Severity Updated",
            f"An admin has adjusted your report severity to {new_sev.upper()}."
            + (f" Reason: {reason}" if reason else ""),
            "warning" if new_sev in ["high", "critical"] else "info",
            {"report_id": report_id}
        )

    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════════
#  ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/analytics")
@admin_required
def analytics():
    severity_dist = Report.get_severity_distribution()
    damage_dist   = Report.get_damage_type_distribution()
    monthly_trend = Report.get_monthly_trend()
    stats         = Report.get_stats()
    return render_template(
        "admin/analytics.html",
        severity_dist=severity_dist,
        damage_dist=damage_dist,
        monthly_trend=monthly_trend,
        stats=stats,
        damage_display=DAMAGE_DISPLAY
    )


# ══════════════════════════════════════════════════════════════════════════════
#  CREW MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/crew")
@admin_required
def crew_management():
    team_heads  = TeamHead.get_all()
    all_workers = Worker.get_all_with_teams()
    team_counts = {k: Worker.count_by_team(k) for k in TEAMS}

    # ── Per-team performance stats for the cards ──
    now        = datetime.utcnow()
    team_stats = {}
    for team_key in TEAMS:
        tasks = list(mongo.db.tasks.find({"assigned_team": team_key}))
        total      = len(tasks)
        on_time    = 0
        late       = 0
        overdue    = 0

        for t in tasks:
            t_status  = t.get("status", "")
            due       = t.get("due_date")
            completed = t.get("completed_at")
            if t_status == "completed":
                if due and completed and completed <= due:
                    on_time += 1
                else:
                    late += 1
            else:
                if due and now > due:
                    overdue += 1

        rate = round((on_time / total * 100), 1) if total else 0

        # Star rating
        if total == 0:
            stars = 0
        else:
            pct = (on_time / total) * 100
            if pct <= 20:   stars = 1
            elif pct <= 40: stars = 2
            elif pct <= 60: stars = 3
            elif pct <= 80: stars = 4
            else:            stars = 5

        team_stats[team_key] = {
            "total":   total,
            "on_time": on_time,
            "late":    late,
            "overdue": overdue,
            "rate":    rate,
            "stars":   stars,
        }

    return render_template(
        "admin/crew_management.html",
        team_heads=serialize_list(team_heads),
        all_workers=serialize_list(all_workers),
        teams=TEAMS,
        team_counts=team_counts,
        team_stats=team_stats,
    )


@admin_bp.route("/api/team-heads", methods=["POST"])
def api_create_team_head():
    data      = request.get_json()
    name      = (data.get("name") or "").strip()
    email     = (data.get("email") or "").strip().lower()
    phone     = (data.get("phone") or "").strip()
    team      = (data.get("team") or "").strip()
    password  = data.get("password", "")
    photo_b64 = data.get("photo_b64", "")           # NEW

    if not all([name, email, phone, team, password]):
        return jsonify({"success": False, "error": "All fields are required."})
    if len(password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters."})
    if not photo_b64:                                # NEW — mandatory
        return jsonify({"success": False, "error": "Profile photo is required."})
    if team not in TEAMS:
        return jsonify({"success": False, "error": "Invalid team selected."})
    if User.find_by_email(email):
        return jsonify({"success": False, "error": "An account with this email already exists."})

    # Upload photo to Cloudinary
    try:
        b64_data  = re.sub(r"^data:image/\w+;base64,", "", photo_b64)
        img_bytes = base64.b64decode(b64_data)
        upload_result = upload_crew_head_photo(img_bytes, team, name)
        avatar_url = upload_result["url"] if upload_result else ""
    except Exception:
        return jsonify({"success": False, "error": "Photo upload failed. Please try again."})

    import bcrypt
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User.create({
        "name":          name,
        "email":         email,
        "phone":         phone,
        "password_hash": pw_hash,
        "auth_provider": "email",
        "role":          "crew",
        "team":          team,
        "is_team_head":  True,
        "is_verified":   True,
        "avatar":        avatar_url,                 # NEW — store Cloudinary URL
    })
    return jsonify({"success": True, "user_id": str(user["_id"])})


@admin_bp.route("/api/team-heads/<head_id>", methods=["PUT"])
@admin_required
def update_team_head(head_id):
    data      = request.get_json()
    name      = (data.get("name") or "").strip()
    email     = (data.get("email") or "").strip().lower()
    phone     = (data.get("phone") or "").strip()
    photo_b64 = data.get("photo_b64", "")

    if not all([name, email, phone]):
        return jsonify({"success": False, "error": "Name, email and phone are required."})

    user = User.find_by_id(head_id)
    if not user:
        return jsonify({"success": False, "error": "Team head not found."})

    # Check email uniqueness (exclude current user)
    existing = User.find_by_email(email)
    if existing and str(existing["_id"]) != head_id:
        return jsonify({"success": False, "error": "This email is already used by another account."})

    updates = {"name": name, "email": email, "phone": phone}

    # Handle photo update
    if photo_b64:
        # Delete old photo from Cloudinary if exists
        old_public_id = user.get("avatar_public_id", "")
        if old_public_id:
            delete_image(old_public_id)

        try:
            b64_data  = re.sub(r"^data:image/\w+;base64,", "", photo_b64)
            img_bytes = base64.b64decode(b64_data)
            upload_result = upload_crew_head_photo(img_bytes, user.get("team", ""), name)
            if not upload_result:
                return jsonify({"success": False, "error": "Photo upload failed. Please try again."})
            updates["avatar"]           = upload_result["url"]
            updates["avatar_public_id"] = upload_result["public_id"]
        except Exception:
            return jsonify({"success": False, "error": "Invalid image data."})

    User.update(head_id, updates)
    return jsonify({"success": True})


@admin_bp.route("/api/team-heads/<head_id>", methods=["DELETE"])
@admin_required
def delete_team_head(head_id):
    data   = request.get_json(silent=True) or {}
    reason = (data.get("reason") or "").strip()
    note   = (data.get("note") or "").strip()

    User.update(head_id, {
        "is_active":             False,
        "deactivation_reason":   reason,
        "deactivation_note":     note,
        "deactivated_at":        datetime.utcnow(),
    })
    return jsonify({"success": True})


@admin_bp.route("/api/team-heads/<head_id>/reset-password", methods=["POST"])
@admin_required
def reset_team_head_password(head_id):
    data   = request.get_json()
    new_pw = data.get("password", "")
    if len(new_pw) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    User.set_password(head_id, new_pw)
    return jsonify({"success": True})


@admin_bp.route("/api/teams/<team>/workers", methods=["GET"])
@admin_required
def get_team_workers(team):
    if team not in TEAMS:
        return jsonify({"error": "Invalid team"}), 400
    workers = Worker.find_by_team(team)
    return jsonify({
        "workers":   serialize_list(workers),
        "count":     len(workers),
        "max":       TEAMS[team]["max_workers"],
        "team_name": TEAMS[team]["name"]
    })

@admin_bp.route("/api/teams/<team>/export/csv")
@admin_required
def export_team_task_history(team):
    if team not in TEAMS:
        return jsonify({"error": "Invalid team"}), 400

    team_cfg = TEAMS[team]
    now      = _fmt_dt(datetime.utcnow())

    # Fetch all tasks assigned to this team
    tasks = list(mongo.db.tasks.find({"assigned_team": team}).sort("created_at", -1))

    total_tasks       = len(tasks)
    completed_on_time = 0
    completed_late    = 0
    not_completed     = 0
    in_progress       = 0

    for t in tasks:
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = _fmt_dt(t.get("completed_at"))
        if t_status == "completed":
            if due and completed and completed <= due:
                completed_on_time += 1
            else:
                completed_late += 1
        elif t_status in ("in_progress", "assigned"):
            in_progress += 1
        else:
            if due and now > due:
                not_completed += 1

    completion_rate = round((completed_on_time / total_tasks * 100), 1) if total_tasks else 0

    # Star rating: based on on-time completion rate
    # 0-20% → 1★, 21-40% → 2★, 41-60% → 3★, 61-80% → 4★, 81-100% → 5★
    # if total_tasks == 0:
    #     star_rating = "N/A"
    # else:
    #     pct = (completed_on_time / total_tasks) * 100
    #     if pct <= 20:
    #         star_rating = "1/5"
    #     elif pct <= 40:
    #         star_rating = "2/5"
    #     elif pct <= 60:
    #         star_rating = "3/5"
    #     elif pct <= 80:
    #         star_rating = "4/5"
    #     else:
    #         star_rating = "5/5"

    output = io.StringIO()
    writer = csv.writer(output)

    # ── Team Summary ──
    writer.writerow(["=== TEAM TASK HISTORY REPORT ==="])
    writer.writerow(["Generated At", now])
    writer.writerow([])
    writer.writerow(["Team Name",          team_cfg["name"]])
    writer.writerow(["Handles Severity",   team_cfg["severity"]])
    writer.writerow(["Max Workers",        team_cfg["max_workers"]])
    writer.writerow([])
    writer.writerow(["=== PERFORMANCE SUMMARY ==="])
    writer.writerow(["Total Tasks",             total_tasks])
    writer.writerow(["Completed On Time",        completed_on_time])
    writer.writerow(["Completed Late",           completed_late])
    writer.writerow(["Overdue / Not Completed",  not_completed])
    writer.writerow(["In Progress",              in_progress])
    writer.writerow(["On-Time Completion Rate",  f"{completion_rate}%"])
    writer.writerow([])

    # ── Team Head ──
    writer.writerow(["=== TEAM HEAD ==="])
    th = mongo.db.users.find_one({"role": "crew", "team": team, "is_active": True})
    if th:
        writer.writerow(["Name",  th.get("name", "")])
        writer.writerow(["Email", th.get("email", "")])
        writer.writerow(["Phone", th.get("phone", "")])
    else:
        writer.writerow(["No active team head assigned."])
    writer.writerow([])

    # ── Task Detail Rows ──
    writer.writerow(["=== TASK DETAILS ==="])
    writer.writerow([
        "#", "Task ID", "Report ID", "Priority", "Task Status",
        "Assigned At", "Due Date", "Started At", "Completed At",
        "Completed On Time?", "Days Variance",
        "Workers", "Worker Count",
        "Address", "Damage Types", "Severity",
        "Notes"
    ])

    for i, t in enumerate(tasks, 1):
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = _fmt_dt(t.get("completed_at"))
        started   = _fmt_dt( t.get("started_at"))

        # On-time flag
        if t_status == "completed":
            on_time = "Yes" if (due and completed and completed <= due) else "No"
        else:
            on_time = "—"

        # Days variance (negative = early, positive = late)
        days_variance = ""
        if due and completed and t_status == "completed":
            delta = (completed - due).days
            days_variance = f"+{delta}" if delta > 0 else str(delta)

        # Workers
        worker_ids = t.get("assigned_workers", [])
        worker_names = []
        for wid in worker_ids:
            w = Worker.find_by_id(wid)
            if w:
                worker_names.append(w.get("name", ""))
        workers_str  = "; ".join(worker_names)
        worker_count = len(worker_ids)

        # Report info
        report_id   = t.get("report_id", "")
        address     = ""
        damage_str  = ""
        severity    = ""
        if report_id:
            try:
                rep = mongo.db.reports.find_one({"_id": ObjectId(report_id)})
                if rep:
                    loc        = rep.get("location", {})
                    address    = loc.get("address", "")
                    severity   = rep.get("severity", "")
                    damage_str = ", ".join(
                        rep.get("display_names") or
                        [DAMAGE_DISPLAY.get(d, d.replace("_", " ").title())
                         for d in rep.get("damage_types", [])]
                    )
            except Exception:
                pass

        writer.writerow([
            i,
            str(t["_id"]),
            report_id,
            t.get("priority", ""),
            t_status,
            _fmt_dt(t["created_at"]) if t.get("created_at") else "",
            due.strftime("%Y-%m-%d")       if due       else "No due date",
            started  if started   else "",
            completed if completed else "",
            on_time,
            days_variance,
            workers_str,
            worker_count,
            address,
            damage_str,
            severity,
            t.get("notes", ""),
        ])

    output.seek(0)
    fname = f"{team}_task_history.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

# ══════════════════════════════════════════════════════════════════════════════
#  USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/users")
@admin_required
def users():
    page      = int(request.args.get("page", 1))
    role      = request.args.get("role", "")
    team      = request.args.get("team", "")
    status    = request.args.get("status", "")
    search    = request.args.get("search", "").strip()
    per_page  = 20

    role_counts = User.count_by_role()
    from app import mongo
    role_counts["workers"] = mongo.db.workers.count_documents({})

    # ── Workers tab ────────────────────────────────────────────────────────
    if role == "workers":
        worker_query = {}
        if team:
            worker_query["team"] = team.lower()
        if status in ("available", "on_leave", "inactive"):
            worker_query["status"] = status

        workers_raw = list(
            mongo.db.workers.find(worker_query).sort([("team", 1), ("name", 1)])
        )
        team_heads = TeamHead.get_all()
        return render_template(
            "admin/users.html",
            users=[],
            workers=serialize_list(workers_raw),
            team_heads=serialize_list(team_heads),
            total=len(workers_raw),
            page=1,
            per_page=per_page,
            role_counts=role_counts,
            teams=TEAMS,
            current_role=role,
            current_team=team,
            current_status=status,
            current_search=search,
        )

    # ── All other role tabs ────────────────────────────────────────────────
    is_active = None
    if status == "active":
        is_active = True
    elif status == "inactive":
        is_active = False

    users_list, total = User.get_all(
        role=role or None,
        page=page,
        per_page=per_page,
        search=search,
        team=team,
        is_active=is_active
    )

    return render_template(
        "admin/users.html",
        users=serialize_list(users_list),
        workers=[],
        team_heads=[],
        total=total,
        page=page,
        per_page=per_page,
        role_counts=role_counts,
        teams=TEAMS,
        current_role=role,
        current_team=team,
        current_status=status,
        current_search=search
    )


@admin_bp.route("/api/users/<user_id>/role", methods=["POST"])
@admin_required
def update_user_role(user_id):
    data     = request.get_json()
    new_role = data.get("role")
    if new_role not in User.ROLES:
        return jsonify({"error": "Invalid role"}), 400
    User.update(user_id, {"role": new_role})
    return jsonify({"success": True})


@admin_bp.route("/api/users/<user_id>/toggle-status", methods=["POST"])
@admin_required
def toggle_user_status(user_id):
    user = User.find_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    new_status = not user.get("is_active", True)
    User.update(user_id, {"is_active": new_status})
    return jsonify({"success": True, "is_active": new_status})


@admin_bp.route("/api/users/create-admin", methods=["POST"])
def api_create_admin():
    data     = request.get_json()
    name     = (data.get("name") or "").strip()
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password", "")
    photo_b64 = data.get("photo_b64", "")           # NEW

    if not name or not email or not password:
        return jsonify({"success": False, "error": "Name, email and password are required."})
    if len(password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters."})
    if not photo_b64:                                # NEW — mandatory
        return jsonify({"success": False, "error": "Profile photo is required."})
    if User.find_by_email(email):
        return jsonify({"success": False, "error": "An account with this email already exists."})

    # Upload photo to Cloudinary
    try:
        # strip data-URL prefix if present
        b64_data = re.sub(r"^data:image/\w+;base64,", "", photo_b64)
        img_bytes = base64.b64decode(b64_data)
        upload_result = upload_admin_photo(img_bytes, name)
        avatar_url = upload_result["url"] if upload_result else ""
    except Exception:
        return jsonify({"success": False, "error": "Photo upload failed. Please try again."})

    import bcrypt
    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user = User.create({
        "name":          name,
        "email":         email,
        "password_hash": pw_hash,
        "auth_provider": "email",
        "role":          "admin",
        "is_verified":   True,
        "avatar":        avatar_url,                 # NEW — store Cloudinary URL
    })
    return jsonify({"success": True, "user_id": str(user["_id"])})


@admin_bp.route("/api/update-avatar", methods=["POST"])
def api_update_avatar():
    user_id   = session.get("user_id")
    data      = request.get_json()
    photo_b64 = data.get("photo_b64", "")

    if not photo_b64:
        return jsonify({"success": False, "error": "No photo provided."})

    # Fetch current user to get existing avatar public_id
    current_user = User.find_by_id(user_id)
    if not current_user:
        return jsonify({"success": False, "error": "User not found."})

    # Delete old avatar from Cloudinary if it exists
    old_public_id = current_user.get("avatar_public_id", "")
    if old_public_id:
        delete_image(old_public_id)

    # Upload new photo
    try:
        b64_data  = re.sub(r"^data:image/\w+;base64,", "", photo_b64)
        img_bytes = base64.b64decode(b64_data)
        result    = upload_admin_photo(img_bytes, current_user.get("name", ""))
        if not result:
            return jsonify({"success": False, "error": "Upload failed. Try again."})
    except Exception:
        return jsonify({"success": False, "error": "Invalid image data."})

    # Update MongoDB — store both url and public_id for future deletion
    User.update(user_id, {
        "avatar":           result["url"],
        "avatar_public_id": result["public_id"],
    })

    return jsonify({"success": True, "avatar_url": result["url"]})



# ══════════════════════════════════════════════════════════════════════════════
#  WORKERS
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/workers")
@admin_required
def workers():
    from app import mongo

    team   = request.args.get("team", "")
    status = request.args.get("status", "")
    search = request.args.get("search", "").strip()

    worker_query = {}
    if team:
        worker_query["team"] = team.lower()
    if status in ("available", "on_leave", "inactive"):
        worker_query["status"] = status
    if search:
        worker_query["name"] = {"$regex": search, "$options": "i"}

    workers_raw = list(
        mongo.db.workers.find(worker_query).sort([("team", 1), ("name", 1)])
    )
    team_heads = TeamHead.get_all()

    # Build team head lookup by team key for quick access
    head_by_team = {th["team"]: th for th in team_heads}

    # Attach work history stats + per-task detail for each worker
    for w in workers_raw:
        wid   = str(w["_id"])
        tasks = list(mongo.db.tasks.find({"assigned_workers": wid}))

        sites_attended    = len(tasks)
        completed_on_time = 0
        completed_late    = 0
        not_completed     = 0
        now = datetime.utcnow()

        tasks_detail = []

        for t in tasks:
            t_status  = t.get("status", "")
            due       = t.get("due_date")
            completed = t.get("completed_at")

            if t_status == "completed":
                if due and completed and completed <= due:
                    completed_on_time += 1
                else:
                    completed_late += 1
            else:
                if due and now > due:
                    not_completed += 1

            # Fetch the linked report for site address / photos / coordinates
            report = None
            report_id = t.get("report_id")
            if report_id:
                try:
                    report = mongo.db.reports.find_one({"_id": ObjectId(report_id)})
                except Exception:
                    pass

            lat = lng = site_address = photo_before = photo_after = ""
            if report:
                loc = report.get("location", {})
                lat = loc.get("lat") or loc.get("latitude", "")
                lng = loc.get("lng") or loc.get("longitude", "")
                site_address  = report.get("address", "") or report.get("location_description", "")
                photo_before  = report.get("image_url", "") or report.get("photo_url", "")
                # after-photo stored on task or report depending on your schema
                photo_after   = t.get("after_photo_url", "") or report.get("after_photo_url", "")

            tasks_detail.append({
                "task_id":        str(t["_id"]),
                "address":        site_address,
                "latitude":       str(lat),
                "longitude":      str(lng),
                "photo_before":   photo_before,
                "photo_after":    photo_after,
                "assigned_date":  t["created_at"].strftime("%Y-%m-%d") if t.get("created_at") else "",
                "due_date":       due.strftime("%Y-%m-%d") if due else "",
                "completed_date": completed.strftime("%Y-%m-%d") if completed else "",
                "priority":       t.get("priority", ""),
                "task_status":    t_status,
                "notes":          t.get("notes", ""),
            })

        w["_history"] = {
            "sites_attended":    sites_attended,
            "completed_on_time": completed_on_time,
            "completed_late":    completed_late,
            "not_completed":     not_completed,
            "_tasks_detail":     tasks_detail,
        }

    return render_template(
        "admin/workers.html",
        workers=serialize_list(workers_raw),
        team_heads=serialize_list(team_heads),
        teams=TEAMS,
        current_team=team,
        current_status=status,
        current_search=search,
        total=len(workers_raw),
    )


@admin_bp.route("/workers/<worker_id>/export/csv")
@admin_required
def export_worker_csv(worker_id):
    from app import mongo

    try:
        w = mongo.db.workers.find_one({"_id": ObjectId(worker_id)})
    except Exception:
        return "Invalid worker ID", 400
    if not w:
        return "Worker not found", 404

    team_heads = TeamHead.get_all()
    head = next((th for th in team_heads if th.get("team") == w.get("team")), None)
    head_name = head["name"] if head else "—"
    team_cfg  = TEAMS.get(w.get("team", ""), {})
    team_name = team_cfg.get("name", w.get("team", "—"))

    tasks = list(mongo.db.tasks.find({"assigned_workers": worker_id}))
    now   = datetime.utcnow()

    sites_attended    = len(tasks)
    completed_on_time = 0
    completed_late    = 0
    not_completed     = 0

    tasks_detail = []
    for t in tasks:
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = _fmt_dt(t.get("completed_at"))

        if t_status == "completed":
            if due and completed and completed <= due:
                completed_on_time += 1
            else:
                completed_late += 1
        else:
            if due and now > due:
                not_completed += 1

        report = None
        report_id = t.get("report_id")
        if report_id:
            try:
                report = mongo.db.reports.find_one({"_id": ObjectId(report_id)})
            except Exception:
                pass

        lat = lng = site_address = photo_before = photo_after = ""
        if report:
            loc          = report.get("location", {})
            coords       = loc.get("coordinates", [])
            lat          = coords[1] if len(coords) > 1 else ""
            lng          = coords[0] if len(coords) > 0 else ""
            site_address = loc.get("address", "")
            images       = report.get("images", {})
            photo_before = images.get("original", "")
            photo_after  = t.get("after_photo_url", "") or images.get("repair_after", "")

        maps_link = ""
        if lat and lng:
            maps_link = f"https://www.google.com/maps?q={lat},{lng}"
        elif site_address:
            maps_link = f"https://www.google.com/maps/search/{site_address}"

        is_completed     = t_status == "completed"
        completed_date_s = completed.strftime("%Y-%m-%d") if completed else ""
        due_date_s       = due.strftime("%Y-%m-%d") if due else ""

        on_time_flag = "No"
        if is_completed and completed_date_s and due_date_s:
            on_time_flag = "Yes" if completed_date_s <= due_date_s else "No"

        late_flag = "No"
        if is_completed and completed_date_s and due_date_s:
            late_flag = "Yes" if completed_date_s > due_date_s else "No"

        not_done_flag = "No"
        if not is_completed and due and now > due:
            not_done_flag = "Yes"

        tasks_detail.append({
            "task_id":        str(t["_id"]),
            "address":        site_address,
            "maps_link":      maps_link,
            "photo_before":   photo_before,
            "photo_after":    photo_after,
            "assigned_date":  t["created_at"].strftime("%Y-%m-%d") if t.get("created_at") else "",
            "due_date":       due_date_s,
            "completed_date": completed_date_s,
            "on_time":        on_time_flag,
            "late":           late_flag,
            "not_completed":  not_done_flag,
            "priority":       t.get("priority", ""),
            "task_status":    t_status,
            "notes":          t.get("notes", ""),
        })

    pct = round((completed_on_time / sites_attended * 100)) if sites_attended else 0
    added_on = w.get("created_at", "")
    if added_on:
        added_on = added_on[:10] if isinstance(added_on, str) else added_on.strftime("%Y-%m-%d")

    output = io.StringIO()
    writer = csv.writer(output)

    # ── Section 1: Worker Profile ──
    writer.writerow(["WORKER PROFILE"])
    writer.writerow(["Name", "Address", "Phone Number", "Team", "Team Head",
                     "Added On", "Status", "On-Time Completion Rate"])
    writer.writerow([
        w.get("name", ""),
        w.get("address", ""),
        w.get("phone", ""),
        team_name,
        head_name,
        added_on,
        w.get("status", ""),
        f"{pct}%",
    ])

    writer.writerow([])
    writer.writerow(["WORK HISTORY SUMMARY"])
    writer.writerow(["Sites Attended", "Completed On Time", "Completed After Due", "Not Completed"])
    writer.writerow([sites_attended, completed_on_time, completed_late, not_completed])

    writer.writerow([])
    writer.writerow(["TASK HISTORY"])
    writer.writerow([
        "Task ID", "Site Address", "Google Maps Link",
        "Site Photo (Before)", "Site Photo (After)",
        "Assigned Date", "Due Date", "Completed Date",
        "Completed On Time", "Completed After Due", "Not Completed",
        "Priority", "Task Status", "Notes",
    ])

    if not tasks_detail:
        writer.writerow(["No task assignments found for this worker."])
    else:
        for t in tasks_detail:
            writer.writerow([
                t["task_id"], t["address"], t["maps_link"],
                t["photo_before"], t["photo_after"],
                _fmt_dt(t["assigned_date"]), t["due_date"], _fmt_dt(t["completed_date"]),
                t["on_time"], t["late"], t["not_completed"],
                t["priority"], t["task_status"], t["notes"],
            ])

    output.seek(0)
    fname = (w.get("name", "worker") or "worker").replace(" ", "_") + "_history.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

# Notifications

@admin_bp.route("/notifications")
@admin_required
def notifications():
    user_id = session["user_id"]
    notifs  = get_user_notifications(user_id)
    return render_template(
        "admin/notifications.html",
        notifications=serialize_list(notifs),
        fmt_dt = _fmt_dt
    )



# ══════════════════════════════════════════════════════════════════════════════
#  MAP / SETTINGS
# ══════════════════════════════════════════════════════════════════════════════

@admin_bp.route("/map")
@admin_required
def map_view():
    return render_template("admin/map.html")


@admin_bp.route("/settings")
@admin_required
def settings():
    user = User.find_by_id(session["user_id"])
    return render_template("admin/settings.html", user=serialize_doc(user))


@admin_bp.route("/api/change-password", methods=["POST"])
@admin_required
def change_password():
    data   = request.get_json()
    curr   = data.get("current_password", "")
    new_pw = data.get("new_password", "")
    if not curr or len(new_pw) < 8:
        return jsonify({"error": "Invalid input."}), 400
    user = User.find_by_id(session["user_id"])
    if not user or not bcrypt.checkpw(
        curr.encode(), user.get("password_hash", "").encode()
    ):
        return jsonify({"error": "Current password is incorrect."}), 403
    User.set_password(session["user_id"], new_pw)
    return jsonify({"success": True})