from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for
from utils.decorators import login_required
from models.report import Report
from models.user import User
from models.task import Task as TaskModel
from services.notification_service import get_user_notifications
from utils.helpers import serialize_list, serialize_doc, time_ago, _fmt_dt, get_greeting_hour
from extensions import mongo

citizen_bp = Blueprint("citizen", __name__)


@citizen_bp.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    user    = User.find_by_id(user_id)
    reports, total = Report.get_all({"reporter_id": user_id}, per_page=5)
    notifications  = get_user_notifications(user_id, limit=5)

    _, pending_count     = Report.get_all({"reporter_id": user_id, "status": "pending"}, per_page=1)
    _, in_progress_count = Report.get_all({"reporter_id": user_id, "status": "in_progress"}, per_page=1)
    _, completed_count   = Report.get_all({"reporter_id": user_id, "status": "completed"}, per_page=1)


    by_status = {}
    for r in reports:
        s = r.get("status", "pending")
        by_status[s] = by_status.get(s, 0) + 1

    stats = {
        "total_reports": total,
        "pending":       pending_count,
        "in_progress":   in_progress_count,
        "completed":     completed_count,
    }
    
    return render_template(
        "citizen/dashboard.html",
        user=serialize_doc(user),
        reports=serialize_list(reports),
        stats=stats,
        time_ago=time_ago,
        now_hour=get_greeting_hour(),
        notifications=serialize_list(notifications)
    )


@citizen_bp.route("/report")
@login_required
def report():
    return render_template("citizen/report.html")


@citizen_bp.route("/my-reports")
@login_required
def my_reports():
    user_id        = session["user_id"]
    page           = int(request.args.get("page", 1))
    status_filter  = request.args.get("status", "")
    search         = request.args.get("search", "").strip()
    query          = {"reporter_id": user_id}

    if status_filter:
        if status_filter == "assigned":
            query["status"] = {"$in": ["assigned", "in_progress"]}
        else:
            query["status"] = status_filter

    if search:
        or_clauses = [
            {"damage_types":      {"$regex": search, "$options": "i"}},
            {"location.address": {"$regex": search, "$options": "i"}},
        ]
        try:
            from bson import ObjectId
            or_clauses.append({"_id": ObjectId(search)})
        except Exception:
            pass
        query["$or"] = or_clauses

    reports, total = Report.get_all(query, page=page, per_page=20)
    serialized = serialize_list(reports)

    for r in serialized:
        r["_fmt_created_at"]   = _fmt_dt(r.get("created_at"))
        r["_worker_count"]     = 0
        r["_fmt_completed_at"] = None
        r["_fmt_due_date"]     = None
        r["_fmt_started_at"]   = None
        r["_fmt_assigned_at"]  = None

        if r.get("status") in ["assigned", "in_progress", "completed"]:
            t = TaskModel.find_by_report_id(r["_id"])
            if t:
                r["_worker_count"]     = len(t.get("assigned_workers", []))
                r["_fmt_due_date"]     = _fmt_dt(t.get("due_date"))
                r["_fmt_completed_at"] = _fmt_dt(t.get("completed_at"))
                r["_fmt_started_at"]   = _fmt_dt(t.get("started_at"))
                r["_fmt_assigned_at"]  = _fmt_dt(t.get("created_at"))

    return render_template(
        "citizen/my_reports.html",
        reports=serialized,
        total=total, page=page,
        current_status=status_filter,
        current_search=search,
        time_ago=time_ago,
        fmt_dt=_fmt_dt
    )

@citizen_bp.route("/my-reports/<report_id>/export/csv")
@login_required
def export_citizen_report_csv(report_id):
    import csv, io
    from flask import Response
    from bson import ObjectId
    from models.team import TEAMS, Worker

    user_id = session["user_id"]

    try:
        r = mongo.db.reports.find_one({"_id": ObjectId(report_id), "reporter_id": user_id})
    except Exception:
        return "Invalid report ID", 400
    if not r:
        return "Report not found", 404

    output = io.StringIO()
    writer = csv.writer(output)

    # ── Report section ──
    writer.writerow(["=== REPORT DETAILS ==="])
    writer.writerow(["Field", "Value"])

    loc        = r.get("location", {})
    coords     = loc.get("coordinates", [None, None])
    images     = r.get("images", {})
    created_at = r.get("created_at", "")
    if created_at:
        created_at = created_at.strftime("%Y-%m-%d %H:%M:%S")

    damage_types = ", ".join(
        r.get("display_names") or r.get("damage_types") or [r.get("damage_type", "")]
    )

    ai_breakdown = ""
    if r.get("ai_results") and r["ai_results"].get("detections"):
        from collections import Counter
        DAMAGE_DISPLAY = {
            "pothole": "Pothole", "alligator_cracking": "Alligator Cracking",
            "lateral_cracking": "Lateral Cracking",
            "longitudinal_cracking": "Longitudinal Cracking", "other": "Other"
        }
        counts       = Counter(d.get("damage_type", "unknown") for d in r["ai_results"]["detections"])
        ai_breakdown = "; ".join(
            f"{DAMAGE_DISPLAY.get(k, k.replace('_',' ').title())}({v})"
            for k, v in counts.items()
        )

    writer.writerow(["Report ID",       str(r["_id"])])
    writer.writerow(["Report Type",     r.get("report_type", "")])
    writer.writerow(["Damage Types",    damage_types])
    writer.writerow(["AI Breakdown",    ai_breakdown])
    writer.writerow(["Severity",        r.get("severity", "")])
    writer.writerow(["Status",          r.get("status", "")])
    writer.writerow(["Address",         loc.get("address", "")])
    writer.writerow(["Latitude",        coords[1] if len(coords) > 1 else ""])
    writer.writerow(["Longitude",       coords[0] if len(coords) > 0 else ""])
    writer.writerow(["Description",     r.get("description", "")])
    writer.writerow(["Created At",      _fmt_dt(created_at)])
    writer.writerow(["Original Image",  images.get("original", "")])
    writer.writerow(["Annotated Image", images.get("annotated", "")])
    writer.writerow(["Repair Before",   images.get("repair_before", "")])
    writer.writerow(["Repair After",    images.get("repair_after", "")])

    # ── Task section ──
    writer.writerow([])
    writer.writerow(["=== TASK DETAILS ==="])

    task = TaskModel.find_by_report_id(report_id)
    if not task:
        writer.writerow(["No task assigned yet."])
    else:
        assigned_team = TEAMS.get(task.get("assigned_team", ""), {}).get("name", task.get("assigned_team", ""))
        started_at    = _fmt_dt(task["started_at"])  if task.get("started_at")   else ""
        completed_at  = _fmt_dt(task["completed_at"]) if task.get("completed_at") else ""
        assigned_at   = _fmt_dt(task["created_at"]) if task.get("created_at")   else ""

        writer.writerow(["Task ID",       str(task["_id"])])
        writer.writerow(["Assigned Team", assigned_team])
        writer.writerow(["Priority",      task.get("priority", "")])
        writer.writerow(["Due Date",      task.get("due_date", "") or ""])
        writer.writerow(["Task Status",   task.get("status", "")])
        writer.writerow(["Assigned At",   assigned_at])
        writer.writerow(["Started At",    started_at])
        writer.writerow(["Completed At",  completed_at])
        writer.writerow(["Notes",         task.get("notes", "")])

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
            worker_count = len(worker_ids)
            writer.writerow(["Workers Assigned", worker_count])


    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=report_{report_id}.csv"}
    )


@citizen_bp.route("/hazard-map")
@login_required
def hazard_map():
    return render_template("citizen/hazard_map.html")


@citizen_bp.route("/route-scanner")
@login_required
def route_scanner():
    return render_template("citizen/route_scanner.html")


@citizen_bp.route("/notifications")
@login_required
def notifications():
    user_id = session["user_id"]
    notifs  = get_user_notifications(user_id)
    return render_template(
        "citizen/notifications.html",
        notifications=serialize_list(notifs),
        fmt_dt = _fmt_dt
    )


@citizen_bp.route("/profile")
@login_required
def profile():
    user           = User.find_by_id(session["user_id"])
    _, total       = Report.get_all({"reporter_id": session["user_id"]})
    return render_template(
        "citizen/profile.html",
        user=serialize_doc(user),
        total_reports=total,
        fmt_dt=_fmt_dt
    )