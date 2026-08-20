from functools import wraps
from flask import session, redirect, url_for, flash, jsonify, request


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.is_json or request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            flash("Please log in to continue.", "warning")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "user_id" not in session:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                flash("Please log in to continue.", "warning")
                return redirect(url_for("auth.login"))
            user_role = session.get("role", "citizen")
            if user_role not in roles:
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Access forbidden"}), 403
                flash("You don't have permission to access this page.", "danger")
                return redirect(url_for("auth.login"))
            return f(*args, **kwargs)
        return decorated
    return decorator


def admin_required(f):
    return role_required("admin")(f)


def crew_required(f):
    return role_required("crew")(f)


def citizen_required(f):
    return role_required("citizen")(f)