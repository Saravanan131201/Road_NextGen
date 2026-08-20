from app import mongo
from datetime import datetime
from bson import ObjectId

# ─── Team Configuration ───────────────────────────────────────────────────────
TEAMS = {
    "alpha": {"name": "Alpha Team",  "max_workers": 5,  "color": "#3b82f6", "severity": "low"},
    "beta":  {"name": "Beta Team",   "max_workers": 6,  "color": "#f59e0b", "severity": "medium"},
    "gamma": {"name": "Gamma Team",  "max_workers": 8,  "color": "#f97316", "severity": "high"},
    "delta": {"name": "Delta Team",  "max_workers": 10, "color": "#ef4444", "severity": "critical"},
}

WORKER_STATUS = ["available", "on_leave", "inactive"]


class TeamHead:
    """Crew user with is_team_head=True — the only crew member with a login."""

    @staticmethod
    def create(data: dict) -> dict:
        from models.user import User
        import bcrypt
        hashed = bcrypt.hashpw(data["password"].encode(), bcrypt.gensalt()).decode()
        return User.create({
            "name":          data["name"],
            "email":         data["email"].lower(),
            "phone":         data.get("phone", ""),
            "password_hash": hashed,
            "auth_provider": "password",
            "role":          "crew",
            "is_team_head":  True,
            "team":          data["team"].lower(),
            "is_verified":   True,
        })

    @staticmethod
    def find_by_team(team: str):
        return mongo.db.users.find_one({
            "role":         "crew",
            "is_team_head": True,
            "team":         team.lower(),
            "is_active":    True
        })

    @staticmethod
    def get_all() -> list:
        return list(mongo.db.users.find(
            {"role": "crew", "is_team_head": True}
        ).sort("team", 1))


class Worker:
    """Daily-wage worker record — NO login account."""

    @staticmethod
    def create(data: dict) -> dict:
        worker = {
            "team":         data["team"].lower(),
            "team_head_id": data["team_head_id"],
            "name":         data["name"],
            "phone":        data.get("phone", ""),
            "address":      data.get("address", ""),
            "aadhaar":      data.get("aadhaar", ""),
            "photo_url":    data.get("photo_url", ""),
            "status":       data.get("status", "available"),
            "created_at":   datetime.utcnow(),
            "updated_at":   datetime.utcnow(),
        }
        result = mongo.db.workers.insert_one(worker)
        worker["_id"] = result.inserted_id
        return worker

    @staticmethod
    def find_by_team(team: str, status: str = None) -> list:
        query = {"team": team.lower()}
        if status:
            query["status"] = status
        return list(mongo.db.workers.find(query).sort("name", 1))

    @staticmethod
    def find_by_id(worker_id: str):
        try:
            return mongo.db.workers.find_one({"_id": ObjectId(worker_id)})
        except Exception:
            return None

    @staticmethod
    def update(worker_id: str, data: dict):
        data["updated_at"] = datetime.utcnow()
        mongo.db.workers.update_one(
            {"_id": ObjectId(worker_id)},
            {"$set": data}
        )

    @staticmethod
    def delete(worker_id: str):
        mongo.db.workers.delete_one({"_id": ObjectId(worker_id)})

    @staticmethod
    def count_by_team(team: str) -> int:
        return mongo.db.workers.count_documents({"team": team.lower()})

    @staticmethod
    def get_all_with_teams() -> list:
        return list(mongo.db.workers.find({}).sort([("team", 1), ("name", 1)]))