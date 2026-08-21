import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")
    MONGO_URI = os.getenv("MONGO_URI")

    # Google OAuth (Citizen only)
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

    # SMTP mail OTP login
    PROMAILER_API_KEY  = os.getenv("PROMAILER_API_KEY")
    PROMAILER_SMTP_ID  = os.getenv("PROMAILER_SMTP_ID")
    MAIL_FROM_ADDRESS  = os.getenv("MAIL_FROM_ADDRESS")
    MAIL_SENDER_NAME   = os.getenv("MAIL_SENDER_NAME", "Road NextGen")

    OTP_EXPIRY_SECONDS  = int(os.getenv("OTP_EXPIRY_SECONDS", 600))
    OTP_MAX_TRIES       = int(os.getenv("OTP_MAX_TRIES", 5))



    # Cloudinary
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_API_KEY = os.getenv("CLOUDINARY_API_KEY")
    CLOUDINARY_API_SECRET = os.getenv("CLOUDINARY_API_SECRET")

    # YOLO
    YOLO_MODEL_PATH = os.getenv("YOLO_MODEL_PATH")

    # Session security
    SESSION_COOKIE_SECURE = os.getenv("FLASK_ENV") == "production"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"