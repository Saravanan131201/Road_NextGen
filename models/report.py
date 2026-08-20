from app import mongo
from datetime import datetime
from bson import ObjectId

# Canonical display name mapping (single source of truth)
DAMAGE_DISPLAY = {
    "alligator_cracking":    "Alligator Cracking",
    "lateral_cracking":      "Lateral Cracking",
    "longitudinal_cracking": "Longitudinal Cracking",
    "pothole":               "Pothole",
    "other":                 "Other",
}


class Report:
    DAMAGE_TYPES = [
        "alligator_cracking", "lateral_cracking",
        "longitudinal_cracking", "pothole", "other"
    ]
    SEVERITY_LEVELS = ["low", "medium", "high", "critical"]
    STATUS_FLOW = [
        "pending", "verified", "assigned",
        "in_progress", "completed", "rejected"
    ]

    @staticmethod
    def create(data: dict) -> dict:
        # Always normalise to a list — even single-detection AI reports
        damage_types = data.get("damage_types", [])
        if not damage_types:
            # fall back: wrap the legacy singular field if present
            legacy = data.get("damage_type", "")
            if legacy:
                damage_types = [legacy]

        # Derive display_names from damage_types if not provided
        display_names = data.get("display_names", [])
        if not display_names:
            display_names = [
                DAMAGE_DISPLAY.get(dt, dt.replace("_", " ").title())
                for dt in damage_types
            ]

        report = {
            "reporter_id":    data.get("reporter_id"),
            "report_type":    data.get("report_type", "manual"),
            # ── Removed singular damage_type field ──
            "damage_types":   damage_types,       # always a list
            "display_names":  display_names,       # always a list
            "severity":       data.get("severity", "medium"),
            "description":    data.get("description", ""),
            "location": {
                "type":        "Point",
                "coordinates": data.get("coordinates", [0, 0]),
                "address":     data.get("address", ""),
                "city":        data.get("city", ""),
            },
            "images": {
                "original":           data.get("original_image", ""),
                "original_public_id": data.get("original_public_id", ""),
                "annotated":          data.get("annotated_image", ""),
                "annotated_public_id": data.get("annotated_public_id", ""),
                "repair_before":          "",
                "repair_before_public_id": "",
                "repair_after":           "",
                "repair_after_public_id":  ""
            },
            "ai_results":       data.get("ai_results", {}),
            "assigned_crew_id": None,
            "assigned_at":      None,
            "completed_at":     None,
            "admin_notes":      "",
            "status":           "pending",
            "is_alert_sent":    False,
            "created_at":       datetime.utcnow(),
            "updated_at":       datetime.utcnow(),
        }
        result = mongo.db.reports.insert_one(report)
        report["_id"] = result.inserted_id
        return report

    @staticmethod
    def find_by_id(report_id: str):
        try:
            return mongo.db.reports.find_one({"_id": ObjectId(report_id)})
        except Exception:
            return None

    @staticmethod
    def update(report_id: str, fields: dict):
        fields["updated_at"] = datetime.utcnow()
        mongo.db.reports.update_one(
            {"_id": ObjectId(report_id)},
            {"$set": fields}
        )

    @staticmethod
    def update_status(report_id: str, status: str, extra: dict = None):
        update = {"status": status, "updated_at": datetime.utcnow()}
        if extra:
            update.update(extra)
        mongo.db.reports.update_one(
            {"_id": ObjectId(report_id)},
            {"$set": update}
        )

    @staticmethod
    def get_nearby(lat: float, lng: float,
                   radius_km: float = 5, limit: int = 50) -> list:
        return list(mongo.db.reports.find({
            "location": {
                "$near": {
                    "$geometry": {
                        "type":        "Point",
                        "coordinates": [lng, lat]
                    },
                    "$maxDistance": radius_km * 1000
                }
            },
            "status": {"$nin": ["rejected", "completed"]}
        }).limit(limit))

    @staticmethod
    def get_all(filters: dict = None,
                page: int = 1, per_page: int = 20):
        query = filters or {}
        skip  = (page - 1) * per_page
        docs  = list(
            mongo.db.reports.find(query)
            .sort("created_at", -1)
            .skip(skip)
            .limit(per_page)
        )
        total = mongo.db.reports.count_documents(query)
        return docs, total

    @staticmethod
    def get_stats() -> dict:
        pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        by_status = {r["_id"]: r["count"]
                     for r in mongo.db.reports.aggregate(pipeline)}
        return {
            "total":     mongo.db.reports.count_documents({}),
            "critical":  mongo.db.reports.count_documents({"severity": "critical"}),
            "by_status": by_status
        }

    @staticmethod
    def get_severity_distribution() -> list:
        return list(mongo.db.reports.aggregate([
            {"$group": {"_id": "$severity", "count": {"$sum": 1}}}
        ]))

    @staticmethod
    def get_damage_type_distribution() -> list:
        # Unwind damage_types array — works even when array has one element
        return list(mongo.db.reports.aggregate([
            {"$unwind": {"path": "$damage_types", "preserveNullAndEmptyArrays": True}},
            {"$group": {
                "_id":   {"$ifNull": ["$damage_types", "unknown"]},
                "count": {"$sum": 1}
            }},
            {"$sort": {"count": -1}}
        ]))

    @staticmethod
    def get_monthly_trend(months: int = 6) -> list:
        from datetime import timedelta
        cutoff = datetime.utcnow() - timedelta(days=months * 30)
        return list(mongo.db.reports.aggregate([
            {"$match": {"created_at": {"$gte": cutoff}}},
            {"$group": {
                "_id": {
                    "year":  {"$year":  "$created_at"},
                    "month": {"$month": "$created_at"}
                },
                "count": {"$sum": 1}
            }},
            {"$sort": {"_id.year": 1, "_id.month": 1}}
        ]))