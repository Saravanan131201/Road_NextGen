from app import mongo
from datetime import datetime
from bson import ObjectId
import bcrypt


class User:
    ROLES = ["citizen", "admin", "crew"]

    @staticmethod
    def create(data: dict) -> dict:
        user = {
            "name":          data.get("name", ""),
            "email":         data.get("email", "").lower(),
            "phone":         data.get("phone", ""),
            "password_hash": data.get("password_hash", ""),
            "auth_provider": data.get("auth_provider", "google"),
            "google_id":     data.get("google_id", ""),
            "avatar":        data.get("avatar", ""),
            "role":          data.get("role", "citizen"),
            "is_team_head":  data.get("is_team_head", False),
            "team":          data.get("team", ""),          # alpha/beta/gamma/delta
            "is_active":     True,
            "is_verified":   data.get("is_verified", False),
            "municipality":  data.get("municipality", ""),
            "created_at":    datetime.utcnow(),
            "last_login":    datetime.utcnow(),
            "settings": {
                "notifications": True,
                "dark_mode":     False,
                "language":      "en"
            }
        }
        result = mongo.db.users.insert_one(user)
        user["_id"] = result.inserted_id
        return user

    @staticmethod
    def find_by_email(email: str):
        return mongo.db.users.find_one({"email": email.lower()})

    @staticmethod
    def find_by_google_id(google_id: str):
        return mongo.db.users.find_one({"google_id": google_id})

    @staticmethod
    def find_by_phone(phone: str):
        return mongo.db.users.find_one({"phone": phone})

    @staticmethod
    def find_by_id(user_id: str):
        try:
            return mongo.db.users.find_one({"_id": ObjectId(user_id)})
        except Exception:
            return None

    @staticmethod
    def update(user_id: str, data: dict):
        mongo.db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": data}
        )

    @staticmethod
    def set_password(user_id: str, plain_password: str):
        hashed = bcrypt.hashpw(plain_password.encode(), bcrypt.gensalt()).decode()
        mongo.db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"password_hash": hashed}}
        )

    @staticmethod
    def check_password(user: dict, plain_password: str) -> bool:
        stored = user.get("password_hash", "")
        if not stored:
            return False
        return bcrypt.checkpw(plain_password.encode(), stored.encode())

    @staticmethod
    def get_all(role: str = None, page: int = 1, per_page: int = 20,
                search: str = "", team: str = "", is_team_head: bool = None,
                is_active: bool = None):
        query = {}
        if role:
            query["role"] = role
        if team:
            query["team"] = team
        if is_team_head is not None:
            query["is_team_head"] = is_team_head
        if is_active is not None:
            query["is_active"] = is_active
        if search:
            query["$or"] = [
                {"name":  {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}},
            ]
        skip  = (page - 1) * per_page
        users = list(mongo.db.users.find(query).sort("created_at", -1).skip(skip).limit(per_page))
        total = mongo.db.users.count_documents(query)
        return users, total

    @staticmethod
    def count_by_role() -> dict:
        pipeline = [{"$group": {"_id": "$role", "count": {"$sum": 1}}}]
        result   = {r["_id"]: r["count"] for r in mongo.db.users.aggregate(pipeline)}
        return result