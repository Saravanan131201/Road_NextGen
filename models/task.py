from app import mongo
from datetime import datetime
from bson import ObjectId


class Task:
    STATUS_FLOW = ["assigned", "in_progress", "completed"]

    @staticmethod
    def create(data: dict) -> dict:
        task = {
            "report_id":        data.get("report_id"),
            "assigned_team":    data.get("assigned_team"),
            "team_head_id":     data.get("team_head_id"),
            "assigned_workers": data.get("assigned_workers", []),
            "assigned_by":      data.get("assigned_by"),
            "priority":         data.get("priority", "medium"),
            "due_date":         data.get("due_date"),
            "status":           "assigned",
            "notes":            data.get("notes", ""),
            "before_image":     "",
            "after_image":      "",
            "started_at":       None,
            "completed_at":     None,
            "created_at":       datetime.utcnow(),
            "updated_at":       datetime.utcnow(),
        }
        result = mongo.db.tasks.insert_one(task)
        task["_id"] = result.inserted_id
        return task

    @staticmethod
    def find_by_team_head(team_head_id: str, status: str = None) -> list:
        """Primary query for crew dashboard — by team head ID."""
        query = {"team_head_id": str(team_head_id)}
        if status:
            query["status"] = status
        return list(mongo.db.tasks.find(query).sort("created_at", -1))

    @staticmethod
    def find_by_team(team: str, status: str = None) -> list:
        query = {"assigned_team": team.lower()}
        if status:
            query["status"] = status
        return list(mongo.db.tasks.find(query).sort("created_at", -1))

    @staticmethod
    def find_by_id(task_id: str):
        try:
            return mongo.db.tasks.find_one({"_id": ObjectId(task_id)})
        except Exception:
            return None

    @staticmethod
    def find_by_report_id(report_id: str):
        return mongo.db.tasks.find_one({"report_id": report_id})

    @staticmethod
    def update(task_id: str, data: dict):
        data["updated_at"] = datetime.utcnow()
        mongo.db.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": data}
        )

    @staticmethod
    def update_status(task_id: str, status: str, extra: dict = None):
        update = {"status": status, "updated_at": datetime.utcnow()}
        if status == "in_progress":
            update["started_at"] = datetime.utcnow()
        elif status == "completed":
            update["completed_at"] = datetime.utcnow()
        if extra:
            update.update(extra)
        mongo.db.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": update}
        )

    @staticmethod
    def update_workers(task_id: str, worker_ids: list):
        mongo.db.tasks.update_one(
            {"_id": ObjectId(task_id)},
            {"$set": {
                "assigned_workers": worker_ids,
                "updated_at": datetime.utcnow()
            }}
        )

    @staticmethod
    def get_all_admin(filters: dict = None, page: int = 1, per_page: int = 20):
        query = filters or {}
        skip  = (page - 1) * per_page
        tasks = list(mongo.db.tasks.find(query).sort("created_at", -1).skip(skip).limit(per_page))
        total = mongo.db.tasks.count_documents(query)
        return tasks, total