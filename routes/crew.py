from flask import Blueprint, render_template, session, request, jsonify, redirect, url_for
from utils.decorators import crew_required
from models.task import Task
from models.report import Report
from models.user import User
from models.team import Worker, TEAMS, WORKER_STATUS, TeamHead
from services.cloudinary_service import upload_image, delete_image, upload_worker_photo, upload_repair_photo
from services.notification_service import get_user_notifications, create_notification, upsert_notification

from services.ai_service import DAMAGE_DISPLAY

from utils.helpers import serialize_list, serialize_doc, time_ago, _fmt_dt
from bson import ObjectId
from app import socketio
from datetime import datetime


crew_bp = Blueprint("crew", __name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_current_head():
    return User.find_by_id(session["user_id"])


def _assert_team_head():
    user = _get_current_head()
    if not user or not user.get("is_team_head"):
        return None, None
    return user, user.get("team", "")


# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

@crew_bp.route("/dashboard")
@crew_required
def dashboard():
    user, team_key = _assert_team_head()
    if not user:
        return redirect(url_for("auth.login"))

    COMPLETED_PAGE_SIZE = 10

    raw_tasks = Task.find_by_team_head(str(user["_id"]))
    workers   = Worker.find_by_team(team_key)
    team_cfg  = TEAMS.get(team_key, {})

    enriched = []
    for task in raw_tasks:
        report = Report.find_by_id(task.get("report_id", ""))
        assigned_workers = []
        for wid in task.get("assigned_workers", []):
            w = Worker.find_by_id(wid)
            if w:
                assigned_workers.append(serialize_doc(w))
        enriched.append({
            "task":    serialize_doc(task),
            "report":  serialize_doc(report),
            "workers": assigned_workers
        })

    active    = [t for t in enriched if t["task"].get("status") != "completed"]
    completed = [t for t in enriched if t["task"].get("status") == "completed"]

    # Serialize workers and force all _id fields to plain strings
    serialized_workers = []
    for w in workers:
        sw = serialize_doc(w)
        sw["_id"] = str(sw.get("_id", ""))
        serialized_workers.append(sw)

    return render_template(
        "crew/dashboard.html",
        user=serialize_doc(user),
        active_tasks=active,
        completed_tasks=completed,
        completed_count=len(completed),
        completed_page_size=COMPLETED_PAGE_SIZE,
        workers=serialized_workers,
        team_cfg=team_cfg,
        team_key=team_key,
        time_ago=time_ago
    )
# ══════════════════════════════════════════════════════════════════════════════
#  WORKER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@crew_bp.route("/workers")
@crew_required
def workers():
    user, team_key = _assert_team_head()
    if not user:
        return redirect(url_for("auth.login"))

    workers_list = Worker.find_by_team(team_key)
    team_cfg     = TEAMS.get(team_key, {})
    return render_template(
        "crew/workers.html",
        user=serialize_doc(user),
        workers=serialize_list(workers_list),
        team_cfg=team_cfg,
        team_key=team_key,
        count=len(workers_list),
        worker_status=WORKER_STATUS,
        time_ago=time_ago
    )


@crew_bp.route("/api/workers", methods=["GET"])
@crew_required
def list_workers():
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403
    status_filter = request.args.get("status", "")
    workers = Worker.find_by_team(team_key, status=status_filter or None)
    team_cfg = TEAMS.get(team_key, {})
    return jsonify({
        "workers":   serialize_list(workers),
        "count":     len(workers),
        "max":       team_cfg.get("max_workers", 5),
        "team":      team_key,
        "team_name": team_cfg.get("name", "")
    })


@crew_bp.route("/api/workers", methods=["POST"])
@crew_required
def add_worker():
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    team_cfg   = TEAMS.get(team_key, {})
    max_cap    = team_cfg.get("max_workers", 5)
    curr_count = Worker.count_by_team(team_key, status="available")

    if curr_count >= max_cap:
        return jsonify({
            "error": f"{team_cfg['name']} is at full capacity ({max_cap} workers)."
        }), 400

    name    = request.form.get("name", "").strip()
    phone   = request.form.get("phone", "").strip()
    address = request.form.get("address", "").strip()
    status  = request.form.get("status", "available")

    if not name or not phone:
        return jsonify({"error": "Name and phone are required."}), 400
    if status not in WORKER_STATUS:
        return jsonify({"error": "Invalid status."}), 400

    photo_url = ""
    if "photo" in request.files and request.files["photo"].filename:
        f      = request.files["photo"]
        result = upload_worker_photo(f.read(), team_key, name)
        if result:
            photo_url = result["url"]

    worker = Worker.create({
        "team":         team_key,
        "team_head_id": str(user["_id"]),
        "name":         name,
        "phone":        phone,
        "address":      address,
        "photo_url":    photo_url,
        "status":       status,
    })
    return jsonify({"success": True, "worker": serialize_doc(worker)})


@crew_bp.route("/api/workers/<worker_id>", methods=["PUT"])
@crew_required
def update_worker(worker_id):
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    worker = Worker.find_by_id(worker_id)
    if not worker or worker.get("team") != team_key:
        return jsonify({"error": "Worker not found."}), 404

    name   = request.form.get("name",    worker["name"]).strip()
    status = request.form.get("status",  worker.get("status", "available"))

    update_data = {
        "name":    name,
        "phone":   request.form.get("phone",   worker["phone"]).strip(),
        "address": request.form.get("address", worker.get("address", "")).strip(),
        "status":  status,
    }

    remove_photo = request.form.get("remove_photo", "0") == "1"
    has_new_photo = "photo" in request.files and request.files["photo"].filename

    if has_new_photo:
        # Delete old photo from Cloudinary first
        old_public_id = worker.get("photo_public_id")
        if old_public_id:
            delete_image(old_public_id)

        f      = request.files["photo"]
        result = upload_worker_photo(f.read(), team_key, name)
        if result:
            update_data["photo_url"]       = result["url"]
            update_data["photo_public_id"] = result["public_id"]

    elif remove_photo:
        # User explicitly removed photo without uploading a new one
        old_public_id = worker.get("photo_public_id")
        if old_public_id:
            delete_image(old_public_id)
        update_data["photo_url"]       = ""
        update_data["photo_public_id"] = ""

    Worker.update(worker_id, update_data)
    return jsonify({"success": True})



@crew_bp.route("/api/workers/<worker_id>", methods=["DELETE"])
@crew_required
def delete_worker(worker_id):
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    worker = Worker.find_by_id(worker_id)
    if not worker or worker.get("team") != team_key:
        return jsonify({"error": "Worker not found."}), 404

    Worker.update(worker_id, {"status": "inactive"})
    return jsonify({"success": True})


# ══════════════════════════════════════════════════════════════════════════════
#  EXPORTS
# ══════════════════════════════════════════════════════════════════════════════

@crew_bp.route("/export/team-history/csv")
@crew_required
def export_team_history_csv():
    from flask import Response
    from extensions import mongo
    import csv, io
    from services.ai_service import DAMAGE_DISPLAY

    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    team_cfg = TEAMS.get(team_key, {})
    now      = datetime.utcnow()

    # All tasks for this team head
    tasks = list(mongo.db.tasks.find({"team_head_id": str(user["_id"])}).sort("created_at", -1))

    total      = len(tasks)
    on_time    = 0
    late       = 0
    overdue    = 0
    in_progress = 0

    for t in tasks:
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = t.get("completed_at")
        if t_status == "completed":
            if due and completed and completed <= due:
                on_time += 1
            else:
                late += 1
        elif t_status in ("in_progress", "assigned"):
            in_progress += 1
        else:
            if due and now > due:
                overdue += 1

    rate = round((on_time / total * 100), 1) if total else 0

    # if total == 0:
    #     stars = "N/A"
    # else:
    #     pct = (on_time / total) * 100
    #     if pct <= 20:   stars = "1/5 ★"
    #     elif pct <= 40: stars = "2/5 ★★"
    #     elif pct <= 60: stars = "3/5 ★★★"
    #     elif pct <= 80: stars = "4/5 ★★★★"
    #     else:            stars = "5/5 ★★★★★"

    output = io.StringIO()
    writer = csv.writer(output)

    # ── Summary block ──
    writer.writerow(["=== TEAM TASK HISTORY REPORT ==="])
    writer.writerow(["Generated At",    now.strftime("%Y-%m-%d %H:%M:%S UTC")])
    writer.writerow([])
    writer.writerow(["Team Name",       team_cfg.get("name", team_key)])
    writer.writerow(["Team Head",       user.get("name", "")])
    writer.writerow(["Email",           user.get("email", "")])
    writer.writerow(["Phone",           user.get("phone", "")])
    writer.writerow(["Handles Severity", team_cfg.get("severity", "")])
    writer.writerow([])
    writer.writerow(["=== PERFORMANCE SUMMARY ==="])
    writer.writerow(["Total Tasks",            total])
    writer.writerow(["Completed On Time",       on_time])
    writer.writerow(["Completed Late",          late])
    writer.writerow(["Overdue / Not Completed", overdue])
    writer.writerow(["In Progress",             in_progress])
    writer.writerow(["On-Time Completion Rate", f"{rate}%"])
    writer.writerow([])

    # ── Worker roster ──
    writer.writerow(["=== TEAM WORKERS ==="])
    writer.writerow(["Name", "Phone", "Address", "Status"])
    workers_list = Worker.find_by_team(team_key)
    for w in workers_list:
        writer.writerow([
            w.get("name", ""),
            w.get("phone", ""),
            w.get("address", ""),
            w.get("status", "").replace("_", " ").title(),
        ])
    writer.writerow([])

    # ── Task detail rows ──
    writer.writerow(["=== TASK DETAILS ==="])
    writer.writerow([
        "#", "Task ID", "Report ID", "Priority", "Task Status",
        "Assigned At", "Due Date", "Started At", "Completed At",
        "Completed On Time?", "Days Variance",
        "Workers", "Worker Count",
        "Address", "Damage Types", "Severity", "Notes"
    ])

    for i, t in enumerate(tasks, 1):
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = t.get("completed_at")
        started   = t.get("started_at")

        if t_status == "completed":
            on_time_flag = "Yes" if (due and completed and completed <= due) else "No"
        else:
            on_time_flag = "—"

        days_variance = ""
        if due and completed and t_status == "completed":
            delta = (completed - due).days
            days_variance = f"+{delta}" if delta > 0 else str(delta)

        worker_ids   = t.get("assigned_workers", [])
        worker_names = []
        for wid in worker_ids:
            w = Worker.find_by_id(wid)
            if w:
                worker_names.append(w.get("name", ""))
        workers_str  = "; ".join(worker_names)

        report_id  = t.get("report_id", "")
        address    = damage_str = severity = ""
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
            t["created_at"].strftime("%Y-%m-%d %H:%M") if t.get("created_at") else "",
            due.strftime("%Y-%m-%d")             if due       else "No due date",
            started.strftime("%Y-%m-%d %H:%M")   if started   else "",
            completed.strftime("%Y-%m-%d %H:%M") if completed else "",
            on_time_flag,
            days_variance,
            workers_str,
            len(worker_ids),
            address,
            damage_str,
            severity,
            t.get("notes", ""),
        ])

    output.seek(0)
    fname = f"{team_key}_team_history.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )


@crew_bp.route("/export/worker/<worker_id>/csv")
@crew_required
def export_worker_history_csv(worker_id):
    from flask import Response
    from extensions import mongo
    import csv, io
    from services.ai_service import DAMAGE_DISPLAY

    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    # Security: worker must belong to this team head's team
    worker = Worker.find_by_id(worker_id)
    if not worker or worker.get("team") != team_key:
        return "Worker not found", 404

    now   = _fmt_dt(datetime.utcnow())
    tasks = list(mongo.db.tasks.find({"assigned_workers": worker_id}).sort("created_at", -1))

    total       = len(tasks)
    on_time     = 0
    late        = 0
    overdue     = 0
    in_progress = 0

    for t in tasks:
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = _fmt_dt(t.get("completed_at"))
        if t_status == "completed":
            if due and completed and completed <= due:
                on_time += 1
            else:
                late += 1
        elif t_status in ("in_progress", "assigned"):
            in_progress += 1
        else:
            if due and now > due:
                overdue += 1

    rate = round((on_time / total * 100), 1) if total else 0

    # if total == 0:
    #     stars = "N/A"
    # else:
    #     pct = (on_time / total) * 100
    #     if pct <= 20:   stars = "1/5 ★"
    #     elif pct <= 40: stars = "2/5 ★★"
    #     elif pct <= 60: stars = "3/5 ★★★"
    #     elif pct <= 80: stars = "4/5 ★★★★"
    #     else:            stars = "5/5 ★★★★★"

    team_cfg = TEAMS.get(team_key, {})

    output = io.StringIO()
    writer = csv.writer(output)

    # ── Worker summary ──
    writer.writerow(["=== WORKER TASK HISTORY REPORT ==="])
    writer.writerow(["Generated At", now.strftime("%Y-%m-%d %H:%M:%S UTC")])
    writer.writerow([])
    writer.writerow(["Worker Name",  worker.get("name", "")])
    writer.writerow(["Phone",        worker.get("phone", "")])
    writer.writerow(["Address",      worker.get("address", "")])
    writer.writerow(["Status",       worker.get("status", "").replace("_", " ").title()])
    writer.writerow(["Team",         team_cfg.get("name", team_key)])
    writer.writerow(["Team Head",    user.get("name", "")])
    writer.writerow([])
    writer.writerow(["=== PERFORMANCE SUMMARY ==="])
    writer.writerow(["Total Tasks Assigned",    total])
    writer.writerow(["Completed On Time",        on_time])
    writer.writerow(["Completed Late",           late])
    writer.writerow(["Overdue / Not Completed",  overdue])
    writer.writerow(["In Progress",              in_progress])
    writer.writerow(["On-Time Completion Rate",  f"{rate}%"])
    writer.writerow([])

    # ── Task detail rows ──
    writer.writerow(["=== TASK DETAILS ==="])
    writer.writerow([
        "#", "Task ID", "Report ID", "Priority", "Task Status",
        "Assigned At", "Due Date", "Started At", "Completed At",
        "Completed On Time?", "Days Variance",
        "All Workers on Task", "Address", "Damage Types", "Severity", "Notes"
    ])

    for i, t in enumerate(tasks, 1):
        t_status  = t.get("status", "")
        due       = t.get("due_date")
        completed = _fmt_dt(t.get("completed_at"))
        started   = _fmt_dt(t.get("started_at"))

        if t_status == "completed":
            on_time_flag = "Yes" if (due and completed and completed <= due) else "No"
        else:
            on_time_flag = "—"

        days_variance = ""
        if due and completed and t_status == "completed":
            delta = (completed - due).days
            days_variance = f"+{delta}" if delta > 0 else str(delta)

        # All workers on the task (not just this worker)
        all_wids   = t.get("assigned_workers", [])
        all_w_names = []
        for wid in all_wids:
            w = Worker.find_by_id(wid)
            if w:
                all_w_names.append(w.get("name", ""))
        all_workers_str = "; ".join(all_w_names)

        report_id  = t.get("report_id", "")
        address    = damage_str = severity = ""
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
            t["created_at"].strftime("%Y-%m-%d %H:%M") if t.get("created_at") else "",
            due.strftime("%Y-%m-%d")             if due       else "No due date",
            started   if started   else "",
            completed.strftime("%Y-%m-%d %H:%M") if completed else "",
            on_time_flag,
            days_variance,
            all_workers_str,
            address,
            damage_str,
            severity,
            t.get("notes", ""),
        ])

    output.seek(0)
    safe_name = worker.get("name", worker_id).replace(" ", "_")
    fname     = f"worker_{safe_name}_history.csv"
    return Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

# ══════════════════════════════════════════════════════════════════════════════
#  TASK MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

@crew_bp.route("/task/<task_id>")
@crew_required
def task_detail(task_id):
    user, team_key = _assert_team_head()
    if not user:
        return redirect(url_for("auth.login"))

    task = Task.find_by_id(task_id)
    if not task or task.get("team_head_id") != str(user["_id"]):
        return "Task not found", 404

    report   = Report.find_by_id(task.get("report_id", ""))
    workers  = Worker.find_by_team(team_key)
    team_cfg = TEAMS.get(team_key, {})

    assigned_workers = []
    for wid in task.get("assigned_workers", []):
        w = Worker.find_by_id(wid)
        if w:
            assigned_workers.append(serialize_doc(w))

    return render_template(
        "crew/task_detail.html",
        user=serialize_doc(user),
        task=serialize_doc(task),
        report=serialize_doc(report),
        available_workers=serialize_list(workers),
        assigned_workers=assigned_workers,
        team_cfg=team_cfg,
        team_key=team_key,
        fmt_created_at=_fmt_dt(task.get("created_at")),
        fmt_started_at=_fmt_dt(task.get("started_at")),
        fmt_completed_at=_fmt_dt(task.get("completed_at")),
    )

@crew_bp.route("/api/task/<task_id>/assign-workers", methods=["POST"])
@crew_required
def assign_workers(task_id):
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    task = Task.find_by_id(task_id)
    if not task or task.get("team_head_id") != str(user["_id"]):
        return jsonify({"error": "Task not found."}), 404

    data       = request.get_json()
    worker_ids = data.get("worker_ids", [])
    team_cfg   = TEAMS.get(team_key, {})
    max_cap    = team_cfg.get("max_workers", 5)

    if not worker_ids:
        return jsonify({"error": "Select at least one worker."}), 400
    if len(worker_ids) > max_cap:
        return jsonify({
            "error": f"Maximum {max_cap} workers allowed for {team_cfg.get('name','your team')}."
        }), 400

    for wid in worker_ids:
        w = Worker.find_by_id(wid)
        if not w or w.get("team") != team_key:
            return jsonify({"error": "Worker does not belong to your team."}), 400

    Task.update_workers(task_id, worker_ids)

    # ── Resolve worker names for notifications ────────────────────────────────
    worker_docs  = [Worker.find_by_id(wid) for wid in worker_ids]
    worker_names = [w["name"] for w in worker_docs if w]
    count        = len(worker_names)
    names_str    = ", ".join(worker_names)

    # Fetch report for damage type label
    report      = Report.find_by_id(task.get("report_id", ""))
    damage_label = ""
    location_str = ""
    if report:
        display_names = report.get("display_names", [])
        if display_names:
            damage_label = ", ".join(display_names)
        else:
            damage_label = ", ".join(
                DAMAGE_DISPLAY.get(dt, dt.replace("_", " ").title())
                for dt in report.get("damage_types", [])
            )
        if report.get("location"):
            location_str = report["location"].get("address", "")


    # ── Notify citizen ────────────────────────────────────────────────────────
    reporter_id = report.get("reporter_id") if report else None
    reporter_id = report.get("reporter_id") if report else None
    if reporter_id:
        upsert_notification(
            user_id=reporter_id,
            ref_key=f"workers_assigned_{task.get('report_id')}",
            title=f"🔧 {count} Worker{'s' if count > 1 else ''} Assigned to Your Report",
            message=(
                f"{count} member{'s' if count > 1 else ''} from "
                f"{team_cfg.get('name', 'the repair team')} "
                f"{'are' if count > 1 else 'is'} assigned to your {damage_label} report"
                + (f" at {location_str}" if location_str else "") + "."
            ),
            notif_type="info",
            data={"report_id": task.get("report_id"), "task_id": task_id}
        )

    # ── Notify all admins ─────────────────────────────────────────────────────
    admins, _ = User.get_all(role="admin")
    for admin in admins:
        upsert_notification(
            user_id=str(admin["_id"]),
            ref_key=f"workers_assigned_{task_id}_{str(admin['_id'])}",
            title=f"👷 {count} Worker{'s' if count > 1 else ''} Assigned — {damage_label}",
            message=(
                f"{team_cfg.get('name', 'Team')} assigned {count} member{'s' if count > 1 else ''} "
                f"({names_str}) to the {damage_label} task"
                + (f" at {location_str}" if location_str else "") + "."
            ),
            notif_type="info",
            data={"report_id": task.get("report_id"), "task_id": task_id}
        )

    # ── Broadcast via socket so admin dashboard updates live ─────────────────
    socketio.emit("task_updated", {
        "task_id":      task_id,
        "status":       task.get("status"),
        "worker_count": count,
        "worker_names": names_str,
    })

    return jsonify({"success": True, "assigned_count": count})


@crew_bp.route("/api/task/<task_id>/status", methods=["POST"])
@crew_required
def update_task_status(task_id):
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    data       = request.get_json()
    new_status = data.get("status")
    if new_status not in ["in_progress", "completed"]:
        return jsonify({"error": "Invalid status"}), 400

    task = Task.find_by_id(task_id)
    if not task or task.get("team_head_id") != str(user["_id"]):
        return jsonify({"error": "Task not found."}), 404

    # existing completed block is already there above ...

    if new_status == "in_progress":
        report   = Report.find_by_id(task.get("report_id", ""))
        team_cfg = TEAMS.get(team_key, {})
        now_str  =_fmt_dt(datetime.utcnow)

        damage_label = ""
        location_str = ""
        if report:
            display_names = report.get("display_names", [])
            if display_names:
                damage_label = ", ".join(display_names)
            else:
                damage_label = ", ".join(
                    DAMAGE_DISPLAY.get(dt, dt.replace("_", " ").title())
                    for dt in report.get("damage_types", [])
                )
            if report.get("location"):
                location_str = report["location"].get("address", "")

        # Notify citizen
        reporter_id = report.get("reporter_id") if report else None
        if reporter_id:
            create_notification(
                user_id=reporter_id,
                title="🚧 Repair Work Has Started on Your Report",
                message=(
                    f"The {team_cfg.get('name', 'repair team')} has begun working on your "
                    f"{damage_label} report"
                    + (f" at {location_str}" if location_str else "")
                    + f". Work started at {now_str}."
                ),
                notif_type="info",
                data={"report_id": task.get("report_id"), "task_id": task_id}
            )

        # Notify all admins
        admins, _ = User.get_all(role="admin")
        for admin in admins:
            create_notification(
                user_id=str(admin["_id"]),
                title=f"🚧 Repair Started — {damage_label}",
                message=(
                    f"{team_cfg.get('name', 'Team')} started repair work on the "
                    f"{damage_label} task"
                    + (f" at {location_str}" if location_str else "")
                    + f" at {now_str}."
                ),
                notif_type="info",
                data={"report_id": task.get("report_id"), "task_id": task_id}
            )

    if new_status == "completed":
        # ── Block completion until both repair photos are uploaded ────────────
        missing = []
        if not task.get("before_image"):
            missing.append("before-repair photo")
        if not task.get("after_image"):
            missing.append("after-repair photo")
        if missing:
            return jsonify({
                "error": f"Please upload the {' and '.join(missing)} before marking complete."
            }), 400

        # ── Completion notifications ────────────────────────────
        report   = Report.find_by_id(task.get("report_id", ""))
        team_cfg = TEAMS.get(team_key, {})

        damage_display = {
            "pothole": "Pothole", "alligator_cracking": "Alligator Cracking",
            "lateral_cracking": "Lateral Cracking",
            "longitudinal_cracking": "Longitudinal Cracking", "other": "Other"
        }
        damage_type  = ""
        location_str = ""
        if report:
            raw_dt      = report.get("damage_type", "")
            damage_type = damage_display.get(raw_dt, raw_dt.replace("_", " ").title())
            if report.get("location"):
                location_str = report["location"].get("address", "")

        # ── Notify citizen ────────────────────────────────────────────────────
        reporter_id = report.get("reporter_id") if report else None
        if reporter_id:
            create_notification(
                user_id=reporter_id,
                title="✅ Your Road Repair is Complete!",
                message=(
                    f"The {damage_type} repair"
                    + (f" at {location_str}" if location_str else "")
                    + f" has been successfully completed by "
                    f"{team_cfg.get('name', 'the repair team')}."
                ),
                notif_type="success",
                data={"report_id": task.get("report_id"), "task_id": task_id}
            )

        # ── Notify all admins ─────────────────────────────────────────────────
        admins, _ = User.get_all(role="admin")
        for admin in admins:
            create_notification(
                user_id=str(admin["_id"]),
                title=f"✅ Task Completed — {damage_type}",
                message=(
                    f"{team_cfg.get('name', 'Team')} has completed the {damage_type} repair"
                    + (f" at {location_str}" if location_str else "")
                    + "."
                ),
                notif_type="success",
                data={"report_id": task.get("report_id"), "task_id": task_id}
            )

    Task.update_status(task_id, new_status)
    if task.get("report_id"):
        Report.update_status(task["report_id"], new_status)

    socketio.emit("task_updated", {"task_id": task_id, "status": new_status})
    return jsonify({"success": True})


@crew_bp.route("/api/task/<task_id>/upload-image", methods=["POST"])
@crew_required
def upload_repair_image(task_id):
    user, team_key = _assert_team_head()
    if not user:
        return jsonify({"error": "Unauthorized"}), 403

    img_type = request.form.get("type", "before")
    if "image" not in request.files:
        return jsonify({"error": "No image provided"}), 400

    task = Task.find_by_id(task_id)
    if not task or task.get("team_head_id") != str(user["_id"]):
        return jsonify({"error": "Task not found."}), 404

    # Photos only allowed once work has started
    if task.get("status") not in ["in_progress", "completed"]:
        return jsonify({"error": "Start the repair work before uploading photos."}), 400

    f      = request.files["image"]
    result = upload_repair_photo(f.read(), task_id, img_type)
    if not result:
        return jsonify({"error": "Upload failed"}), 500

    field = f"{img_type}_image"
 
    Task.update(task_id, {
        field: result["url"],
        f"{img_type}_image_public_id": result.get("public_id", "")
    })

    if task.get("report_id"):
        repair_field     = "images.repair_before" if img_type == "before" else "images.repair_after"
        repair_pid_field = "images.repair_before_public_id" if img_type == "before" else "images.repair_after_public_id"
        Report.update(task["report_id"], {
            repair_field:     result["url"],
            repair_pid_field: result.get("public_id", "")
        })

    return jsonify({"success": True, "url": result["url"]})


# ══════════════════════════════════════════════════════════════════════════════
#  HISTORY & NOTIFICATIONS
# ══════════════════════════════════════════════════════════════════════════════

@crew_bp.route("/history")
@crew_required
def history():
    user, team_key = _assert_team_head()
    if not user:
        return redirect(url_for("auth.login"))

    raw   = Task.find_by_team_head(str(user["_id"]), status="completed")
    tasks = []
    for task in raw:
        report = Report.find_by_id(task.get("report_id", ""))
        tasks.append({
            "task":   serialize_doc(task),
            "report": serialize_doc(report)
        })
    return render_template(
        "crew/history.html",
        tasks=tasks,
        user=serialize_doc(user),
        team_key=team_key,
        team_cfg=TEAMS.get(team_key, {}),
        time_ago=time_ago
    )


@crew_bp.route("/notifications")
@crew_required
def notifications():
    user = _get_current_head()
    _, team_key = _assert_team_head()
    notifs = get_user_notifications(session["user_id"])
    return render_template(
        "crew/notifications.html",
        notifications=serialize_list(notifs),
        user=serialize_doc(user),
        team_key=team_key,
        team_cfg=TEAMS.get(team_key, {}),
        fmt_dt = _fmt_dt
    )