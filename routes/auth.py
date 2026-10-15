from flask import (Blueprint, render_template, request, session,
                   redirect, url_for, flash, current_app)
from extensions import mongo, oauth
from models.user import User
from utils.helpers import serialize_doc
from flask_mail import Message
from datetime import datetime, timedelta
from bson import ObjectId
import bcrypt, random, string

auth_bp = Blueprint("auth", __name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────

SESSION_OTP_DAYS   = 1
SESSION_OAUTH_DAYS = 30

def _set_session(user, permanent_days: int = SESSION_OAUTH_DAYS):
    session.permanent = True
    current_app.permanent_session_lifetime = timedelta(days=permanent_days)
    session["user_id"]      = str(user["_id"])
    session["role"]         = user.get("role", "citizen")
    session["name"]         = user.get("name", "")
    session["avatar"]       = user.get("avatar", "")
    session["email"]        = user.get("email", "")
    session["session_days"] = permanent_days


def _redirect_by_role(role):
    if role == "admin":
        return redirect(url_for("admin.dashboard"))
    if role == "crew":
        return redirect(url_for("crew.dashboard"))
    return redirect(url_for("citizen.dashboard"))


def _generate_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))


def _send_otp_email(to_email: str, otp: str):
    import requests

    expiry_mins = current_app.config.get("OTP_EXPIRY_SECONDS", 600) // 60
    mailer_url  = current_app.config["MAILER_SERVICE_URL"]   # your Vercel URL
    api_secret  = current_app.config["MAILER_API_SECRET"]    # shared secret

    response = requests.post(
        f"{mailer_url}/send-otp",
        headers={
            "X-API-Secret": api_secret,
            "Content-Type": "application/json"
        },
        json={
            "to_email":    to_email,
            "otp":         otp,
            "expiry_mins": expiry_mins
        },
        timeout=30
    )

    if response.status_code != 200:
        raise Exception(
            f"Mailer service error {response.status_code}: {response.text}"
        )

def _send_welcome_email(to_email: str, name: str):
    import requests as _req
    try:
        mailer_url = current_app.config["MAILER_SERVICE_URL"]
        api_secret = current_app.config["MAILER_API_SECRET"]
        _req.post(
            f"{mailer_url}/send-welcome",
            headers={"X-API-Secret": api_secret, "Content-Type": "application/json"},
            json={"to_email": to_email, "name": name},
            timeout=30
        )

        print("[Welcome Mail] sent successfully")
        
    except Exception as e:
        print(f"[Welcome Mail] failed: {e}")


# routes/auth.py  —  _upsert_citizen()

def _upsert_citizen(email: str, name: str = None, google_id: str = None,
                    avatar: str = None, auth_provider: str = "otp"):
    user = mongo.db.users.find_one({"email": email})

    if user:
        updates = {"last_login": datetime.utcnow()}

        if google_id and not user.get("google_id"):
            updates["google_id"] = google_id

        if avatar and not user.get("avatar"):
            updates["avatar"] = avatar

        existing = user.get("auth_provider", [])
        if isinstance(existing, str):          
            existing = [existing]
        if auth_provider not in existing:
            existing.append(auth_provider)
        updates["auth_provider"] = existing   

        mongo.db.users.update_one({"_id": user["_id"]}, {"$set": updates})
        return mongo.db.users.find_one({"_id": user["_id"]})

    # New user
    payload = {
        "email":         email,
        "name":          name or email.split("@")[0],
        "avatar":        avatar or "",
        "auth_provider": [auth_provider],      
        "role":          "citizen",
        "is_verified":   True,
        "is_active":     True,
        "created_at":    datetime.utcnow(),
        "last_login":    datetime.utcnow(),
    }
    if google_id:
        payload["google_id"] = google_id
    result = mongo.db.users.insert_one(payload) 
    return mongo.db.users.find_one({"_id": result.inserted_id})


# ─── Login page ───────────────────────────────────────────────────────────────

@auth_bp.route("/login")
def login():
    if "user_id" in session:
        return _redirect_by_role(session.get("role", "citizen"))
    # Which tab to open (citizen | admin | crew)
    tab = request.args.get("tab", "citizen")
    error = request.args.get("error", "")
    return render_template("index.html", active_tab=tab, error=error)



# ══════════════════════════════════════════════════════════════════════════════
#  CITIZEN AUTH
# ══════════════════════════════════════════════════════════════════════════════

# ── OTP: Step 1 — request code ────────────────────────────────────────────────

@auth_bp.route("/citizen/send-otp", methods=["POST"])
def citizen_send_otp():
    email = request.form.get("email", "").strip().lower()
    name  = request.form.get("name", "").strip()

    if not email.endswith("@gmail.com"):
        flash("Only @gmail.com addresses are accepted.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    # Check if this is a new user and name wasn't supplied
    existing = mongo.db.users.find_one({"email": email})
    if not existing and not name:
        # Re-render with name field visible
        flash("Please enter your name to create your account.", "info")
        return redirect(url_for("auth.login", tab="citizen", show_name="1",
                                prefill_email=email))

    otp = _generate_otp()
    expires = datetime.utcnow() + timedelta(seconds=current_app.config.get("OTP_EXPIRY_SECONDS", 600))

    mongo.db.otp_tokens.replace_one(
        {"email": email},
        {
            "email":   email,
            "code":    otp,
            "expires": expires,        
            "tries":   0,
            "name":    name if not existing else existing.get("name", ""),
        },
        upsert=True
    )

    try:
        _send_otp_email(email, otp)
    except Exception as e:
        current_app.logger.error(f"[OTP Mail] {e}")
        mongo.db.otp_tokens.delete_one({"email": email})
        flash("Failed to send OTP. Check the email and try again.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    flash(f"A 6-digit code has been sent to {email}.", "success")
    return redirect(url_for("auth.login", tab="citizen", step="otp",
                            otp_email=email))


# ── OTP: Step 2 — verify code ─────────────────────────────────────────────────

@auth_bp.route("/citizen/verify-otp", methods=["POST"])
def citizen_verify_otp():
    entered = request.form.get("otp", "").strip()
    email   = request.form.get("otp_email", "").strip().lower()

    if not email:
        flash("Session lost. Please start again.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    record = mongo.db.otp_tokens.find_one({"email": email})

    if not record:
        flash("No pending code for this email. Please request a new one.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    # Expiry
    if datetime.utcnow() > record["expires"]:
        mongo.db.otp_tokens.delete_one({"email": email})
        flash("Your code has expired. Please request a new one.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    # Max attempts
    if record["tries"] >= current_app.config.get("OTP_MAX_TRIES", 5):
        mongo.db.otp_tokens.delete_one({"email": email})
        flash("Too many incorrect attempts. Please request a new code.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    # Wrong code
    if entered != record["code"]:
        mongo.db.otp_tokens.update_one(
            {"email": email}, {"$inc": {"tries": 1}}
        )
        remaining = current_app.config.get("OTP_MAX_TRIES", 5) - (record["tries"] + 1)
        flash(f"Incorrect code. {remaining} attempt(s) remaining.", "danger")
        return redirect(url_for("auth.login", tab="citizen", step="otp",
                                otp_email=email))

    # ── Correct — clean up token ──────────────────────────────────────────────
    name = record.get("name", "")
    mongo.db.otp_tokens.delete_one({"email": email})

    is_new = not mongo.db.users.find_one({"email": email})
    user   = _upsert_citizen(email=email, name=name, auth_provider="otp")
    _set_session(user, SESSION_OTP_DAYS)
    if is_new:
        _send_welcome_email(email, user.get("name", name))
    flash(f"Welcome, {user.get('name','').split()[0]}!", "success")
    return redirect(url_for("citizen.dashboard"))


# ── OTP: Resend ───────────────────────────────────────────────────────────────

@auth_bp.route("/citizen/resend-otp", methods=["POST"])
def citizen_resend_otp():
    email = request.form.get("otp_email", "").strip().lower()
    if not email:
        flash("Session lost. Please start again.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    record = mongo.db.otp_tokens.find_one({"email": email})
    if not record:
        flash("No pending request for this email. Please start again.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))

    otp     = _generate_otp()
    expires = datetime.utcnow() + timedelta(seconds=current_app.config.get("OTP_EXPIRY_SECONDS", 600))

    mongo.db.otp_tokens.update_one(
        {"email": email},
        {"$set": {"code": otp, "expires": expires, "tries": 0}}
    )

    try:
        _send_otp_email(email, otp)
        flash(f"A new code has been sent to {email}.", "success")
    except Exception as e:
        current_app.logger.error(f"[OTP Resend] {e}")
        flash("Failed to resend. Please try again.", "danger")

    return redirect(url_for("auth.login", tab="citizen", step="otp",
                            otp_email=email))


# ── Google OAuth ──────────────────────────────────────────────────────────────

@auth_bp.route("/google/login")
def google_login():
    redirect_uri = url_for("auth.google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@auth_bp.route("/google/callback")
def google_callback():
    try:
        token     = oauth.google.authorize_access_token()
        user_info = token.get("userinfo")

        if not user_info:
            flash("Failed to retrieve account information from Google.", "danger")
            return redirect(url_for("auth.login", tab="citizen"))

        email     = user_info.get("email", "")
        google_id = user_info.get("sub")
        name      = user_info.get("name", email.split("@")[0])
        avatar    = user_info.get("picture", "")

        if not email.endswith("@gmail.com"):
            flash("Only @gmail.com accounts are accepted for citizen login.", "danger")
            return redirect(url_for("auth.login", tab="citizen"))

        is_new = not mongo.db.users.find_one({"email": email})
        user   = _upsert_citizen(
            email=email, name=name,
            google_id=google_id, avatar=avatar,
            auth_provider="google"
        )

        if user.get("role") != "citizen":
            flash("This login method is for citizens only.", "danger")
            return redirect(url_for("auth.login", tab="citizen"))

        _set_session(user, SESSION_OAUTH_DAYS)
        if is_new:
            _send_welcome_email(email, name)
            
        flash(f"Welcome back, {name.split()[0]}!", "success")
        return redirect(url_for("citizen.dashboard"))

    except Exception as e:
        current_app.logger.error(f"[Google OAuth] {e}")
        flash("Google authentication failed. Please try again.", "danger")
        return redirect(url_for("auth.login", tab="citizen"))



# ══════════════════════════════════════════════════════════════════════════════
#  STAFF AUTH  (Admin + Crew — email & password)
# ══════════════════════════════════════════════════════════════════════════════

@auth_bp.route("/staff/login", methods=["POST"])
def staff_login():
    """
    Single endpoint for both Admin and Crew password login.
    The 'role' field in the form tells us which portal they want.
    """
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "").strip()
    role     = request.form.get("role", "")   # "admin" | "crew"

    if role not in ("admin", "crew"):
        flash("Invalid portal selection.", "danger")
        return redirect(url_for("auth.login", tab=role or "admin"))

    if not email or not password:
        flash("Email and password are required.", "danger")
        return redirect(url_for("auth.login", tab=role))

    user = User.find_by_email(email)

    if not user:
        flash("No account found with that email address.", "danger")
        return redirect(url_for("auth.login", tab=role))

    if user.get("role") != role:
        flash(f"This account does not have {role} access.", "danger")
        return redirect(url_for("auth.login", tab=role))

    if not user.get("is_active", True):
        flash("Your account has been deactivated. Contact the administrator.", "danger")
        return redirect(url_for("auth.login", tab=role))

    # Verify password
    stored_hash = user.get("password_hash", "")
    if not stored_hash or not bcrypt.checkpw(password.encode(), stored_hash.encode()):
        flash("Incorrect password.", "danger")
        return redirect(url_for("auth.login", tab=role))

    User.update(str(user["_id"]), {"last_login": datetime.utcnow()})
    _set_session(user, SESSION_OAUTH_DAYS)

    flash(f"Welcome, {user.get('name','').split()[0] or email}!", "success")
    return _redirect_by_role(role)


# ─── Logout ───────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
def logout():
    role = session.get("role", "citizen")
    session.clear()
    tab = "citizen" if role == "citizen" else role
    flash("You have been logged out successfully.", "info")
    return render_template("index.html", active_tab=tab)


# ─── Admin seed (first-run helper) ───────────────────────────────────────────

@auth_bp.route("/setup/create-admin", methods=["GET", "POST"])
def create_admin():
    """
    One-time setup endpoint to create the first admin account.
    Disable this route in production after initial setup.
    """
    if mongo.db.users.count_documents({"role": "admin"}) > 0:
        flash("An admin account already exists.", "info")
        return redirect(url_for("auth.login", tab="admin"))

    if request.method == "POST":
        name     = request.form.get("name", "").strip()
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not name or not email or not password or len(password) < 8:
            flash("All fields are required and password must be ≥ 8 characters.", "danger")
            return render_template("auth/setup.html")

        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        User.create({
            "name": name,
            "email": email,
            "password_hash": hashed,
            "auth_provider": "password",
            "role": "admin",
            "is_verified": True,
            "municipality": "City Corporation"
        })
        flash("Admin account created! You can now log in.", "success")
        return redirect(url_for("auth.login", tab="admin"))

    return render_template("auth/setup.html")