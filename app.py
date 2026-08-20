from flask import Flask, render_template, session, redirect, url_for
from flask_cors import CORS
from config import Config
import cloudinary

from pymongo import ASCENDING

from extensions import mongo, socketio, oauth, mail

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Extensions
    import certifi
    app.config["MONGO_URI"] = app.config.get("MONGO_URI")
    mongo.init_app(app, tlsCAFile=certifi.where()) 

    with app.app_context():
        mongo.db.otp_tokens.create_index(
            [("expires", ASCENDING)],
            expireAfterSeconds=0,
            background=True
        )

    mail.init_app(app)

    CORS(app)
    socketio.init_app(app, cors_allowed_origins="*", async_mode="eventlet")
    oauth.init_app(app)

    # Cloudinary
    cloudinary.config(
        cloud_name=app.config["CLOUDINARY_CLOUD_NAME"],
        api_key=app.config["CLOUDINARY_API_KEY"],
        api_secret=app.config["CLOUDINARY_API_SECRET"],
        secure=True
    )

    # Register Google OAuth (Citizen only)
    oauth.register(
        name="google",
        client_id=app.config["GOOGLE_CLIENT_ID"],
        client_secret=app.config["GOOGLE_CLIENT_SECRET"],
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={
            "scope": "openid email profile",
            "prompt": "select_account"
        }
    )

    # Register blueprints
    from routes.auth import auth_bp
    from routes.citizen import citizen_bp
    from routes.admin import admin_bp
    from routes.crew import crew_bp
    from routes.api import api_bp

    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(citizen_bp, url_prefix="/citizen")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(crew_bp, url_prefix="/crew")
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.route('/')
    def index():
        user = None
        if 'user_id' in session:
            user = {
                'name': session.get('name', ''),
                'email': session.get('email', ''),
                'role': session.get('role', ''),          # 'citizen' | 'admin' | 'crew'
                'avatar': session.get('avatar', ''),       # URL or empty
            }
        return render_template('index.html', user=user, active_tab=session.get('role','citizen'))


    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    from routes.api import register_socket_events
    register_socket_events(socketio)

    return app


if __name__ == "__main__":
    import os
    app = create_app()
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, debug=False, port=port)